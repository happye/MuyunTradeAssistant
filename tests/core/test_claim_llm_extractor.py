"""LLM claim 提取器测试（plan/fusion F6 落地，src/core/claim_llm_extractor.py）

锁死语义：
1. 引用强制归属：citation 恒=本次输入文档（source+内容 hash）——模型不能发明引用
2. 容错：围栏 JSON/前后噪声可解析；API 失败/非法 JSON/无 client → 空列表（不炸不硬凑）
3. 单位白名单外丢弃该条；relation 非法降 NEUTRAL；confidence 钳 0-1
4. 缓存继承基类（同输入不重复付费）；usage tokens 累计（评测费用口径）

纯内存测试（fake client，零网络零费用）。跑法：pytest tests/core/test_claim_llm_extractor.py -q
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import hashlib

import pytest

from src.core.claim_llm_extractor import LLMClaimExtractor, _parse_json_tolerant, _parse_aware_dt

_DOC = {"title": "订单公告", "content": "公司获 5 亿元订单", "date": "2026-09-01",
        "source": "巨潮公告", "security_id": "600519", "event_type": "order"}

_GOOD_JSON = json.dumps({"claims": [{
    "statement": "公司获得 5 亿元订单",
    "subject": "600519", "event_type": "order", "relation": "SUPPORTS",
    "occurred_at": "2026-08-30", "value": 5.0, "unit": "亿元", "confidence": 0.9,
}]}, ensure_ascii=False)


class _FakeClient:
    def __init__(self, content, raise_exc=None):
        self._content = content
        self._raise = raise_exc
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        self.last_kw = kw
        if self._raise:
            raise self._raise
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
                               usage=SimpleNamespace(total_tokens=321))


def _doc_hash(doc):
    return hashlib.sha256((str(doc.get("title") or "") + str(doc.get("content") or ""))
                          .encode("utf-8")).hexdigest()


def test_happy_path_claims_with_forced_citation():
    ext = LLMClaimExtractor(client=_FakeClient(_GOOD_JSON), model="test-model")
    claims = ext.extract(dict(_DOC))
    assert len(claims) == 1 and ext.calls == 1
    c = claims[0]
    assert c.statement == "公司获得 5 亿元订单"
    assert c.relation.value == "SUPPORTS" and c.value == 5.0 and c.unit == "亿元"
    assert c.citation_uri == "巨潮公告" and c.citation_hash == _doc_hash(_DOC)
    assert c.model_confidence == 0.9
    assert c.occurred_at is not None and c.occurred_at.tzinfo is not None
    assert ext.last_usage_tokens == 321


def test_fenced_json_and_noise_tolerated():
    ext = LLMClaimExtractor(client=_FakeClient("说明文字```json\n" + _GOOD_JSON + "\n```完毕"),
                            model="t")
    assert len(ext.extract(dict(_DOC))) == 1


def test_illegal_unit_claim_dropped():
    bad = json.dumps({"claims": [{"statement": "x", "unit": "手", "value": 5.0},
                                 {"statement": "合法条目", "relation": "NEUTRAL"}]},
                     ensure_ascii=False)
    ext = LLMClaimExtractor(client=_FakeClient(bad), model="t")
    claims = ext.extract(dict(_DOC))
    assert len(claims) == 1 and claims[0].statement == "合法条目"


def test_api_failure_returns_empty():
    ext = LLMClaimExtractor(client=_FakeClient("", raise_exc=RuntimeError("timeout")), model="t")
    assert ext.extract(dict(_DOC)) == []


def test_illegal_json_returns_empty():
    ext = LLMClaimExtractor(client=_FakeClient("我不能输出 JSON"), model="t")
    assert ext.extract(dict(_DOC)) == []


def test_no_client_returns_empty(monkeypatch):
    # 本机 DEEPSEEK_API_KEY 环境变量会兜底构造真实 client（审查 P1 实测发真实网络调用）
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert LLMClaimExtractor(client=None, model="t", config={"ai": {"provider": "deepseek",
                                                                    "deepseek": {"api_key": ""}}}
                             ).extract(dict(_DOC)) == []


def test_cache_inherited_same_input_no_recharge():
    client = _FakeClient(_GOOD_JSON)
    ext = LLMClaimExtractor(client=client, model="t")
    ext.extract(dict(_DOC))
    ext.extract(dict(_DOC))
    assert client.calls == 1 and ext.calls == 1  # 同输入不重复付费


def test_confidence_clamped():
    bad = json.dumps({"claims": [{"statement": "x", "confidence": 7.5}]}, ensure_ascii=False)
    ext = LLMClaimExtractor(client=_FakeClient(bad), model="t")
    assert ext.extract(dict(_DOC))[0].model_confidence == 1.0


def test_parse_helpers():
    assert _parse_json_tolerant("noise {\"a\": 1} tail") == {"a": 1}
    assert _parse_json_tolerant("no json") is None
    dt = _parse_aware_dt("2026-09-01")
    assert dt is not None and dt.tzinfo is not None  # naive 补东八区
    assert _parse_aware_dt("垃圾") is None
