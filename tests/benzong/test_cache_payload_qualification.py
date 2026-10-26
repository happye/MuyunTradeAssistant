"""T05-K 笨总评分缓存 payload 资格门（读/写共用）回归。

边界（本卡）：同股同日 + 当前 CACHE_VERSION 只是必要条件，不是充分条件。
score/confidence 必填且必须是有限 JSON 数值；已消费的可选字段
(invalidate/warnings/sources/reasoning/market_turnover) 类型受限；未知额外
metadata 保持兼容。坏 payload 当未命中，坏文件字节保留不动。

不碰真实 ~/.muyun：全部经 monkeypatch 的临时缓存目录；无网络 / 无产品 AI。
"""
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.benzong import cache

CODE = "T05K01"
DATE = "2026-10-27"
DIM = "industry_prosperity"
DIMS = [
    "industry_prosperity",
    "business_purity",
    "valuation_position",
    "industry_leader",
    "market_recognition",
    "risk_deduction",
]
NAN = float("nan")
INF = float("inf")


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    """把缓存根固定到 pytest 临时目录，绝不读写真实 ~/.muyun。"""
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    return tmp_path


def _raw_write(root, dim, text):
    path = root / f"{CODE}_{DATE}_{dim}.json"
    path.write_text(text, encoding="utf-8")
    return path


def _json_write(root, dim, payload, *, version=None):
    data = dict(payload)
    data["_cache_version"] = cache.CACHE_VERSION if version is None else version
    data.setdefault("_cached_at", "2026-10-27T09:00:00")
    return _raw_write(root, dim, json.dumps(data, ensure_ascii=False))


def _assert_read_warning(caplog, field, marker="RAW_PAYLOAD_MUST_NOT_LEAK"):
    assert "笨总缓存读取 payload 资格校验失败" in caplog.text
    assert field in caplog.text
    assert marker not in caplog.text, "新诊断不得回显任意 payload 内容"


def _assert_write_invalid_warning(caplog, field, marker="RAW_PAYLOAD_MUST_NOT_LEAK"):
    assert "笨总缓存写入 payload 资格校验失败" in caplog.text
    assert field in caplog.text
    assert marker not in caplog.text, "新诊断不得回显任意 payload 内容"


# ── 读端：合法/边界 payload 必须继续命中 ────────────────────────────

VALID_PAYLOADS = [
    pytest.param({"score": 0, "confidence": 1}, id="int-min-score-int-max-conf"),
    pytest.param({"score": 100.0, "confidence": 0.3}, id="float-max-score-weak-conf"),
    pytest.param({"score": 50.5, "confidence": 0.999}, id="mid-score"),
    pytest.param({"score": 60, "confidence": 0.5, "warnings": [], "sources": [],
                  "reasoning": ""}, id="empty-optionals"),
    pytest.param({"score": 60, "confidence": 0.5, "invalidate": None}, id="invalidate-none"),
    pytest.param({"score": 60, "confidence": 0.5, "invalidate": True}, id="invalidate-true"),
    pytest.param({"score": 60, "confidence": 0.5, "invalidate": False}, id="invalidate-false"),
    pytest.param({"score": 60, "confidence": 0.5, "market_turnover": None}, id="mt-none"),
    pytest.param({"score": 60, "confidence": 0.5, "market_turnover": 0}, id="mt-zero"),
    pytest.param({"score": 60, "confidence": 0.5, "market_turnover": 2.5}, id="mt-positive"),
    pytest.param({"score": 60, "confidence": 0.5, "warnings": ["w"],
                  "sources": ["s1", "s2"], "reasoning": "第一行\n第二行"},
                 id="full-optionals"),
    pytest.param({"score": 60, "confidence": 0.5,
                  "unknown_extra": {"nested": [1, 2]}, "data_freshness": "2026-10-27"},
                 id="unknown-extra-compatible"),
]


@pytest.mark.parametrize("payload", VALID_PAYLOADS)
def test_read_admits_qualified_payload(cache_root, payload):
    path = _json_write(cache_root, DIM, payload)
    before = path.read_bytes()
    data = cache.get(CODE, DATE, DIM)
    assert data is not None, f"合法 payload 被误拒: {payload}"
    assert data["score"] == payload["score"]
    assert data["confidence"] == payload["confidence"]
    assert data["_cache_version"] == cache.CACHE_VERSION
    assert path.read_bytes() == before, "读命中不得改写原文件"


@pytest.mark.parametrize("payload", [
    pytest.param({"score": 0, "confidence": 0.3}, id="score0-conf0.3"),
    pytest.param({"score": 100, "confidence": 0.3}, id="score100-conf0.3"),
    pytest.param({"score": 0, "confidence": 1}, id="score0-conf1"),
    pytest.param({"score": 100, "confidence": 1}, id="score100-conf1"),
])
def test_read_keeps_weak_positive_and_boundaries(cache_root, payload):
    """弱正置信度 0.3 与 0/100 边界分是合法缓存，不能被资格门误杀。"""
    _json_write(cache_root, DIM, payload)
    data = cache.get(CODE, DATE, DIM)
    assert data is not None
    assert data["confidence"] == payload["confidence"]
    assert data["score"] == payload["score"]


# ── 读端：坏 payload 必须当未命中且保留坏文件 ────────────────────────

INVALID_PAYLOADS = [
    pytest.param({"confidence": 0.5}, "score", id="score-missing"),
    pytest.param({"score": 50}, "confidence", id="confidence-missing"),
    pytest.param({"score": "50", "confidence": 0.5}, "score", id="score-str"),
    pytest.param({"score": True, "confidence": 0.5}, "score", id="score-bool-true"),
    pytest.param({"score": False, "confidence": 0.5}, "score", id="score-bool-false"),
    pytest.param({"score": None, "confidence": 0.5}, "score", id="score-none"),
    pytest.param({"score": [50], "confidence": 0.5}, "score", id="score-list"),
    pytest.param({"score": {"v": 50}, "confidence": 0.5}, "score", id="score-dict"),
    pytest.param({"score": -0.1, "confidence": 0.5}, "score", id="score-negative"),
    pytest.param({"score": 100.1, "confidence": 0.5}, "score", id="score-over-100"),
    pytest.param({"score": NAN, "confidence": 0.5}, "score", id="score-nan"),
    pytest.param({"score": INF, "confidence": 0.5}, "score", id="score-inf"),
    pytest.param({"score": -INF, "confidence": 0.5}, "score", id="score-negative-inf"),
    pytest.param({"score": 50, "confidence": 0}, "confidence", id="conf-zero"),
    pytest.param({"score": 50, "confidence": -0.1}, "confidence", id="conf-negative"),
    pytest.param({"score": 50, "confidence": 1.0001}, "confidence", id="conf-over-one"),
    pytest.param({"score": 50, "confidence": NAN}, "confidence", id="conf-nan"),
    pytest.param({"score": 50, "confidence": INF}, "confidence", id="conf-inf"),
    pytest.param({"score": 50, "confidence": "0.5"}, "confidence", id="conf-str"),
    pytest.param({"score": 50, "confidence": True}, "confidence", id="conf-bool"),
    pytest.param({"score": 50, "confidence": None}, "confidence", id="conf-none"),
    pytest.param({"score": 50, "confidence": 0.5, "warnings": None}, "warnings", id="warnings-none"),
    pytest.param({"score": 50, "confidence": 0.5, "warnings": "w"}, "warnings", id="warnings-str"),
    pytest.param({"score": 50, "confidence": 0.5, "warnings": {"w": 1}}, "warnings", id="warnings-dict"),
    pytest.param({"score": 50, "confidence": 0.5, "warnings": ["ok", 1]}, "warnings", id="warnings-mixed"),
    pytest.param({"score": 50, "confidence": 0.5, "sources": None}, "sources", id="sources-none"),
    pytest.param({"score": 50, "confidence": 0.5, "sources": "src"}, "sources", id="sources-str"),
    pytest.param({"score": 50, "confidence": 0.5, "sources": [{"x": 1}]}, "sources", id="sources-dict-item"),
    pytest.param({"score": 50, "confidence": 0.5, "reasoning": None}, "reasoning", id="reasoning-none"),
    pytest.param({"score": 50, "confidence": 0.5, "reasoning": 5}, "reasoning", id="reasoning-number"),
    pytest.param({"score": 50, "confidence": 0.5, "reasoning": []}, "reasoning", id="reasoning-list"),
    pytest.param({"score": 50, "confidence": 0.5, "reasoning": {}}, "reasoning", id="reasoning-dict"),
    pytest.param({"score": 50, "confidence": 0.5, "invalidate": "yes"}, "invalidate", id="invalidate-str"),
    pytest.param({"score": 50, "confidence": 0.5, "invalidate": 1}, "invalidate", id="invalidate-int"),
    pytest.param({"score": 50, "confidence": 0.5, "market_turnover": -0.1}, "market_turnover",
                 id="mt-negative"),
    pytest.param({"score": 50, "confidence": 0.5, "market_turnover": NAN}, "market_turnover",
                 id="mt-nan"),
    pytest.param({"score": 50, "confidence": 0.5, "market_turnover": INF}, "market_turnover",
                 id="mt-inf"),
    pytest.param({"score": 50, "confidence": 0.5, "market_turnover": True}, "market_turnover",
                 id="mt-bool"),
    pytest.param({"score": 50, "confidence": 0.5, "market_turnover": "1.0"}, "market_turnover",
                 id="mt-str"),
]


@pytest.mark.parametrize("payload,field", INVALID_PAYLOADS)
def test_read_rejects_invalid_payload_without_touching_bytes(cache_root, caplog, payload, field):
    path = _json_write(cache_root, DIM, dict(payload,
                                             leak_marker="RAW_PAYLOAD_MUST_NOT_LEAK"))
    before = path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.get(CODE, DATE, DIM) is None
    assert path.read_bytes() == before, "坏 payload 文件字节必须原样保留（不删除/不改写）"
    _assert_read_warning(caplog, field)


def test_read_rejects_malformed_top_level(cache_root):
    for text in ("[]", '"text"', "123", "null", '{"score": 50, "confidence": 0.5'):
        path = _raw_write(cache_root, DIM, text)
        before = path.read_bytes()
        assert cache.get(CODE, DATE, DIM) is None, f"顶层/JSON 坏形态必须未命中: {text!r}"
        assert path.read_bytes() == before


def test_read_checks_legacy_version_before_payload(cache_root, caplog):
    """版本门必须仍是第一道：旧版本即使 payload 坏也不进入新资格门/新告警。"""
    path = _json_write(cache_root, DIM, {"score": "bad", "confidence": 0},
                       version="v0.0.1-legacy")
    before = path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.get(CODE, DATE, DIM) is None
    assert "payload 资格校验失败" not in caplog.text
    assert path.read_bytes() == before


def test_cache_version_not_bumped_by_this_card():
    """本卡只加资格门，不改评分语义：CACHE_VERSION 必须保持。"""
    assert cache.CACHE_VERSION == "v0.8.29.1"


# ── 写端：先复制/冻结再校验，非法输入零触碰路径与目录 ─────────────

def test_set_writes_valid_payload_and_roundtrips(cache_root):
    payload = {"score": 50, "confidence": 0.5, "sources": ["a"], "reasoning": "r"}
    assert cache.set(CODE, DATE, DIM, payload) is True
    path = cache_root / f"{CODE}_{DATE}_{DIM}.json"
    assert path.exists()
    data = cache.get(CODE, DATE, DIM)
    assert data is not None
    assert data["score"] == 50 and data["confidence"] == 0.5
    assert data["_cache_version"] == cache.CACHE_VERSION
    assert "_cached_at" in data


@pytest.mark.parametrize("payload", [
    pytest.param({"score": 0, "confidence": 0.3}, id="score0-conf0.3"),
    pytest.param({"score": 100, "confidence": 1}, id="score100-conf1"),
])
def test_set_admits_weak_positive_and_boundaries(cache_root, payload):
    assert cache.set(CODE, DATE, DIM, payload) is True
    assert cache.get(CODE, DATE, DIM) is not None


@pytest.mark.parametrize("payload,field", INVALID_PAYLOADS)
def test_set_invalid_returns_false_before_path_and_preserves_old(
        cache_root, monkeypatch, caplog, payload, field):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6,
                                        "reasoning": "old-valid"})
    old_bytes = old.read_bytes()
    path_calls = []

    def spy_get_cache_dir():
        path_calls.append(1)
        return cache_root

    monkeypatch.setattr(cache, "get_cache_dir", spy_get_cache_dir)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM,
                         dict(payload, leak_marker="RAW_PAYLOAD_MUST_NOT_LEAK")) is False
    assert path_calls == [], "非法输入必须在 _cache_path/目录初始化之前返回 False"
    assert old.read_bytes() == old_bytes, "非法输入不得触碰既有合法缓存字节"
    assert cache.get(CODE, DATE, DIM)["score"] == 61, "失败持久化不得变成成功缓存"
    _assert_write_invalid_warning(caplog, field)


def test_set_invalid_does_not_create_cache_root(tmp_path, monkeypatch, caplog):
    never_root = tmp_path / "cache_root_must_not_be_created"
    calls = []

    def spy_get_cache_dir():
        calls.append(1)
        return never_root

    monkeypatch.setattr(cache, "get_cache_dir", spy_get_cache_dir)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": "bad", "confidence": 0.5}) is False
    assert calls == []
    assert not never_root.exists(), "非法写入不得初始化缓存目录"


@pytest.mark.parametrize("result", [
    pytest.param([1, 2, 3], id="list"),
    pytest.param("text", id="str"),
    pytest.param(None, id="none"),
    pytest.param(42, id="int"),
])
def test_set_non_mapping_returns_false_before_path(tmp_path, monkeypatch, result):
    calls = []
    monkeypatch.setattr(cache, "get_cache_dir",
                        lambda: calls.append(1) or (tmp_path / "never"))
    assert cache.set(CODE, DATE, DIM, result) is False
    assert calls == []


def test_set_does_not_mutate_caller_result(cache_root):
    payload = {"score": 50, "confidence": 0.5, "sources": ["a"], "reasoning": "r"}
    snapshot = json.loads(json.dumps(payload))
    assert cache.set(CODE, DATE, DIM, payload) is True
    assert payload == snapshot, "写端只能校验冻结副本，不得改写调用方原对象"


def test_set_serialization_failure_returns_false_and_preserves_old(cache_root, caplog):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6})
    old_bytes = old.read_bytes()
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5,
                                          "extra": {1, 2}}) is False
    assert old.read_bytes() == old_bytes, "序列化失败必须保旧（原子替换前失败）"
    assert cache.get(CODE, DATE, DIM)["score"] == 61


def test_set_replace_failure_returns_false_and_preserves_old(cache_root, monkeypatch):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6})
    old_bytes = old.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("replace boom")

    monkeypatch.setattr(cache.os, "replace", boom)
    assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5}) is False
    assert old.read_bytes() == old_bytes, "原子替换失败必须保旧"
    assert cache.get(CODE, DATE, DIM) is not None


def test_set_path_failure_returns_false(cache_root, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError("path boom")

    monkeypatch.setattr(cache, "_cache_path", boom)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5}) is False
    assert "笨总缓存写入失败" in caplog.text


# ── fix1 P1：写入 record 必须是独立的深层快照，而非浅拷贝 ─────────────

class _DeepcopyBoom:
    """模拟无法快照的调用方对象（异常正文含 canary，禁止外泄）。"""

    def __deepcopy__(self, memo):
        raise RuntimeError("secret_canary deepcopy boom")


@pytest.mark.parametrize("result", [
    pytest.param([("score", 50), ("confidence", 0.5)], id="list-of-pairs"),
    pytest.param((("score", 50), ("confidence", 0.5)), id="tuple-of-pairs"),
    pytest.param({"score": 50, "confidence": 0.5}.items(), id="dict-items"),
])
def test_set_requires_real_dict_not_convertible_pairs(tmp_path, monkeypatch, result):
    """严格原输入 dict：可被 dict() 转换的 pair 序列也不得绕过输入资格。"""
    calls = []
    monkeypatch.setattr(cache, "get_cache_dir",
                        lambda: calls.append(1) or (tmp_path / "never"))
    assert cache.set(CODE, DATE, DIM, result) is False
    assert calls == [], "非 dict 输入必须在算缓存路径之前返回 False"


def test_set_freezes_nested_actual_record_before_path_hook(cache_root, monkeypatch):
    """_cache_path 期间调用方突变 sources/warnings/nested extra，不得进入写盘记录。"""
    result = {
        "score": 50,
        "confidence": 0.5,
        "sources": ["orig"],
        "warnings": ["w"],
        "reasoning": "r",
        "extra": {"nested": ["keep"]},
    }
    hook_calls = []

    def hook(code, date, dim):
        # 模拟并行调用方/路径钩子在 set 尚未落盘时改写同一 list/dict
        result["sources"].append(7)
        result["warnings"].append("mutated")
        result["extra"]["nested"].append("mutated")
        hook_calls.append(True)
        return cache_root / f"{code}_{date}_{dim}.json"

    monkeypatch.setattr(cache, "_cache_path", hook)
    assert cache.set(CODE, DATE, DIM, result) is True
    assert hook_calls, "测试前提：路径钩子确实在写盘前被调用"
    path = cache_root / f"{CODE}_{DATE}_{DIM}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sources"] == ["orig"], "写盘必须是冻结快照，不含钩子后续 append"
    assert data["warnings"] == ["w"]
    assert data["extra"]["nested"] == ["keep"]
    assert data["score"] == 50 and data["confidence"] == 0.5
    assert 7 not in data["sources"]
    # 冻结只保护写盘记录，不承诺阻止调用方自己修改自己的对象
    assert result["sources"] == ["orig", 7]


def test_set_snapshot_failure_returns_false_preserves_old_and_hides_body(
        cache_root, caplog):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6})
    old_bytes = old.read_bytes()
    payload = {"score": 50, "confidence": 0.5, "extra": _DeepcopyBoom()}
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, payload) is False
    assert old.read_bytes() == old_bytes, "快照失败必须保留旧合法缓存字节"
    assert cache.get(CODE, DATE, DIM)["score"] == 61
    assert "笨总缓存写入失败" in caplog.text
    assert "RuntimeError" in caplog.text, "允许保留异常类型，便于排障"
    assert "secret_canary" not in caplog.text, "不得回显异常正文"


def test_set_replace_failure_hides_exception_body(cache_root, monkeypatch, caplog):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6})
    old_bytes = old.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("secret_canary replace boom")

    monkeypatch.setattr(cache.os, "replace", boom)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5}) is False
    assert old.read_bytes() == old_bytes
    assert "笨总缓存写入失败" in caplog.text
    assert "OSError" in caplog.text
    assert "secret_canary" not in caplog.text


def test_set_serialization_failure_hides_exception_body(cache_root, monkeypatch, caplog):
    old = _json_write(cache_root, DIM, {"score": 61, "confidence": 0.6})
    old_bytes = old.read_bytes()

    def boom(*args, **kwargs):
        raise TypeError("secret_canary serialization boom")

    monkeypatch.setattr(cache.json, "dump", boom)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5}) is False
    assert old.read_bytes() == old_bytes
    assert "笨总缓存写入失败" in caplog.text
    assert "TypeError" in caplog.text
    assert "secret_canary" not in caplog.text


def test_set_path_failure_hides_exception_body(cache_root, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError("secret_canary path boom")

    monkeypatch.setattr(cache, "_cache_path", boom)
    with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
        assert cache.set(CODE, DATE, DIM, {"score": 50, "confidence": 0.5}) is False
    assert "笨总缓存写入失败" in caplog.text
    assert "secret_canary" not in caplog.text
