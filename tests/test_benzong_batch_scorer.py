"""笨总批量评分封装测试（ISS-041 方向 A）

mock auto_score + _build_ai_client/_build_rag_service，验证：
1. 按总分降序排序
2. top_n 截断正确
3. 单只失败不阻塞批次（记入 failures）
4. AI client 只 build 一次（注入复用，不每只重建）
5. invalidate 一票否决股排最后
6. progress_cb 回调被调用
"""

import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


if __name__ == "__main__":
    print("\n=== 笨总批量评分封装测试 (ISS-041 方向 A) ===\n")
    test_sort_descending()
    test_top_n_truncation()
    test_failure_isolation()
    test_client_built_once()
    test_invalidate_ranked_last()
    test_progress_cb()
    print("\n=== 全部 6 项 PASS ===")
