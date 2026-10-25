"""笨总批量评分封装测试（ISS-041 方向 A）

mock auto_score + _build_ai_client/_build_rag_service，验证：
1. 按总分降序排序
2. top_n 截断正确
3. 单只失败不阻塞批次（记入 failures）
4. AI client 只 build 一次（注入复用，不每只重建）
5. invalidate 一票否决股排最后
6. progress_cb 回调被调用
"""

import contextlib
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _make_result(total, grade="A", conf=0.9, invalidate=False, name="测试",
                 effective_grade=None, normalized=None):
    """构造 mock AutoScoredResult"""
    r = mock.MagicMock()
    r.score.total_score = total
    r.score.grade.return_value = grade
    r.score.effective_grade.return_value = effective_grade or grade
    r.score.normalized_score.return_value = normalized if normalized is not None else round(total / 1.44, 1)
    r.score.stock_name = name
    r.overall_confidence = conf
    r.warnings = []
    r.invalidate = invalidate
    r.dimensions_meta = {
        "industry_prosperity": {"score": total * 0.8},
        "business_purity": {"score": total * 0.9},
    }
    return r


def test_sort_descending():
    """1. 按总分降序"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client", return_value=(mock.MagicMock(), "deepseek-chat")), \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        scores = {"A": 110, "B": 90, "C": 130}
        m_auto.side_effect = lambda code, **kw: _make_result(scores[code], name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["A", "B", "C"], top_n=10)
        codes = [x["code"] for x in r["ranked"]]
        assert codes == ["C", "A", "B"], f"应按总分降序, 实际 {codes}"
        print(f"✓ 排序降序: {codes} (130/110/90)")


def test_top_n_truncation():
    """2. top_n 截断"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client", return_value=(mock.MagicMock(), "m")), \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        m_auto.side_effect = lambda code, **kw: _make_result(int(code), name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["10", "20", "30", "40", "50"], top_n=3)
        assert len(r["top_n"]) == 3, f"top_n 应为 3, 实际 {len(r['top_n'])}"
        assert [x["code"] for x in r["top_n"]] == ["50", "40", "30"]
        assert len(r["ranked"]) == 5  # ranked 仍是全部
        print(f"✓ top_n=3 截断: {[x['code'] for x in r['top_n']]}")


def test_failure_isolation():
    """3. 单只失败不阻塞批次"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client", return_value=(mock.MagicMock(), "m")), \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        def fake(code, **kw):
            if code == "BAD":
                raise RuntimeError("网络挂")
            return _make_result(100, name=code)
        m_auto.side_effect = fake
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["GOOD1", "BAD", "GOOD2"], top_n=10)
        assert r["failed"] == 1
        assert r["scored"] == 2
        assert r["failures"][0]["code"] == "BAD"
        assert len(r["ranked"]) == 2
        print(f"✓ 失败隔离: scored={r['scored']} failed={r['failed']} failures={r['failures']}")


def test_client_built_once():
    """4. AI client 只 build 一次（注入复用）"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client") as m_build, \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        m_build.return_value = (mock.MagicMock(), "deepseek-chat")
        m_auto.side_effect = lambda code, **kw: _make_result(100, name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        auto_score_batch(["A", "B", "C", "D", "E"], top_n=10)
        assert m_build.call_count == 1, f"client 应只 build 1 次, 实际 {m_build.call_count}"
        # 验证每次 auto_score 都注入了同一个 client（非 None）
        for call in m_auto.call_args_list:
            assert call.kwargs.get("ai_client") is not None, "应注入 client"
        print(f"✓ client build 1 次, 5 只全部注入复用")


def test_invalidate_ranked_last():
    """5. invalidate 一票否决股排最后"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client", return_value=(mock.MagicMock(), "m")), \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        m_auto.side_effect = lambda code, **kw: _make_result(
            50, name=code, invalidate=(code == "VETO"))
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["VETO", "NORMAL1", "NORMAL2"], top_n=10)
        codes = [x["code"] for x in r["ranked"]]
        assert codes[-1] == "VETO", f"否决股应排最后, 实际 {codes}"
        print(f"✓ 否决股排最后: {codes}")


def test_progress_cb():
    """6. progress_cb 回调被调用"""
    with mock.patch("src.core.benzong.batch_scorer.auto_score") as m_auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client", return_value=(mock.MagicMock(), "m")), \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2):
        m_auto.side_effect = lambda code, **kw: _make_result(100, name=code)
        calls = []
        from src.core.benzong.batch_scorer import auto_score_batch
        auto_score_batch(["A", "B"], top_n=10, progress_cb=lambda i, t, c, s: calls.append((i, c)))
        assert calls == [(1, "A"), (2, "B")], f"回调顺序错: {calls}"
        print(f"✓ progress_cb: {calls}")


# ── T05-H：结构化逐项结果 callback（ba 只据此记账）──────────

_DIMS = ("industry_prosperity", "business_purity", "valuation_position",
         "industry_leader", "market_recognition", "risk_deduction")


def _make_structured_result(total, confs=None, name="测试", invalidate=False):
    """构造带 6 维 confidence 的 mock 结果（confs 缺省全 0.8 有效）。"""
    confs = dict(confs or {})
    r = _make_result(total, name=name, invalidate=invalidate)
    r.dimensions_meta = {
        d: {"score": total * 0.8, "confidence": confs.get(d, 0.8)} for d in _DIMS
    }
    return r


@contextlib.contextmanager
def _patch_scorer_env(m_auto):
    with mock.patch("src.core.benzong.batch_scorer.auto_score", m_auto) as auto, \
         mock.patch("src.core.benzong.batch_scorer._build_ai_client",
                    return_value=(mock.MagicMock(), "m")) as build, \
         mock.patch("src.core.benzong.batch_scorer.get_market_turnover", return_value=1.2) as turnover:
        yield auto, build, turnover


def test_result_cb_structured_complete_and_ranking_preserved():
    """完整维度结果 → 只回调一次结构化成功；ranked/top_n 业务语义不变。"""
    records, progress = [], []
    with _patch_scorer_env(mock.MagicMock()) as (m_auto, m_build, m_turn):
        m_auto.side_effect = lambda code, **kw: _make_structured_result(
            {"A": 80, "B": 60, "C": 70}[code], name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["A", "B", "C"], top_n=2,
                             progress_cb=lambda i, t, c, s: progress.append(c),
                             result_cb=lambda rec: records.append(rec))
    assert [x["code"] for x in r["ranked"]] == ["A", "C", "B"]
    assert [x["code"] for x in r["top_n"]] == ["A", "C"]
    assert r["failures"] == [] and r["scored"] == 3
    assert progress == ["A", "B", "C"]
    assert [rec["code"] for rec in records] == ["A", "B", "C"]
    for rec in records:
        assert rec["ok"] is True and rec["status"] == "complete"
        assert rec["degraded_dims"] == []


def test_result_cb_degraded_when_partial_confidence_unrated_or_illegal():
    """部分维度 confidence 缺失/0/非法 → 降级（仍算已返回结果，不记失败）。"""
    bad = [
        {"risk_deduction": 0},
        {"market_recognition": None},
        {"industry_leader": "bad"},
        {"valuation_position": float("nan")},
        {"industry_leader": -0.2},
    ]
    records = []
    with _patch_scorer_env(mock.MagicMock()) as (m_auto, m_build, m_turn):
        def fake(code, **kw):
            idx = ["D0", "D1", "D2", "D3", "D4", "OK"].index(code)
            confs = bad[idx] if idx < len(bad) else {}
            return _make_structured_result(70, confs=confs, name=code)
        m_auto.side_effect = fake
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["D0", "D1", "D2", "D3", "D4", "OK"],
                             top_n=10, result_cb=lambda rec: records.append(rec))
    assert r["failed"] == 0 and r["scored"] == 6
    by_code = {rec["code"]: rec for rec in records}
    for code in ["D0", "D1", "D2", "D3", "D4"]:
        assert by_code[code]["ok"] is True
        assert by_code[code]["status"] == "degraded", f"{code} 应记降级"
        assert by_code[code]["degraded_dims"], "降级必须列出未评出维度"
    assert by_code["OK"]["status"] == "complete"


def test_result_cb_failure_record():
    """auto_score 抛异常 → 结构化失败记录；ranked 不含该股、failures 照旧。"""
    records = []
    with _patch_scorer_env(mock.MagicMock()) as (m_auto, m_build, m_turn):
        def fake(code, **kw):
            if code == "BAD":
                raise RuntimeError("网络挂")
            return _make_structured_result(60, name=code)
        m_auto.side_effect = fake
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["GOOD", "BAD"], top_n=10,
                             result_cb=lambda rec: records.append(rec))
    assert r["failed"] == 1 and [x["code"] for x in r["ranked"]] == ["GOOD"]
    bad = next(rec for rec in records if rec["code"] == "BAD")
    assert bad["ok"] is False and bad["status"] == "failed"
    assert "网络挂" in bad["error"]


def test_result_cb_exception_does_not_reclassify_accepted_score():
    """记账回调异常：已接受评分不得改判失败/双计；批次继续并告知记录不可靠。"""
    records = []
    progress = []

    def _cb(rec):
        records.append(rec["code"])
        if rec["code"] == "A":
            raise RuntimeError("账本坏了")

    with _patch_scorer_env(mock.MagicMock()) as (m_auto, m_build, m_turn):
        m_auto.side_effect = lambda code, **kw: _make_structured_result(70, name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["A", "B"], top_n=10,
                             progress_cb=lambda i, t, c, s: progress.append(c),
                             result_cb=_cb)
    assert [x["code"] for x in r["ranked"]] == ["A", "B"]
    assert r["failures"] == [] and r["failed"] == 0
    assert progress == ["A", "B"]
    assert records == ["A", "B"]
    assert r.get("result_cb_errors") == 1, "必须如实报告记账回调异常（记录不可靠）"


def test_progress_cb_exception_is_isolated():
    """展示回调异常不得把已评分结果算失败，也不中断批次。"""
    calls = []

    def _progress(i, total, code, status):
        calls.append(code)
        raise RuntimeError("rich 挂了")

    with _patch_scorer_env(mock.MagicMock()) as (m_auto, m_build, m_turn):
        m_auto.side_effect = lambda code, **kw: _make_structured_result(70, name=code)
        from src.core.benzong.batch_scorer import auto_score_batch
        r = auto_score_batch(["A", "B"], top_n=10, progress_cb=_progress)
    assert [x["code"] for x in r["ranked"]] == ["A", "B"]
    assert r["failures"] == [] and r["failed"] == 0 and r["scored"] == 2
    assert calls == ["A", "B"]


if __name__ == "__main__":
    print("\n=== 笨总批量评分封装测试 (ISS-041 方向 A) ===\n")
    test_sort_descending()
    test_top_n_truncation()
    test_failure_isolation()
    test_client_built_once()
    test_invalidate_ranked_last()
    test_progress_cb()
    print("\n=== 全部 6 项 PASS ===")
