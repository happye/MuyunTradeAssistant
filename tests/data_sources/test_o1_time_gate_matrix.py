"""O1 估值与影子时点资格一致矩阵（plan/fusion iteration8；R14 Y3 反例）

锁死语义（Y3 转绿合同；估值门 portfolio._qualified_weight 与影子门
shadow_diff._binding_eligibility 对同一输入必须同判）：
1. naive 带时刻按 Asia/Shanghai 实际时刻比较——当天未来 naive 不再漏判为合格
   （旧实现只比日期，当天 23:59:59 可过资格——R14 Y3 病根）。
2. aware 按实际时刻比较；等价偏移（UTC 表示）同判。
3. 纯日期按上海日期比较（日精度不假造盘中时点）。
4. 非法日历/非法时刻/尾部垃圾/缺失 → 不获资格（估值 None；影子 unparsable/缺失 drop）。
5. `as_of` 参数化：估值门可注入固定时钟（可重复测试、固定时钟避免午夜偶发），
   捕获 as_of 以 UTC 表示与等价上海时刻同判。

既有回归不回退：源日期/预取/120s 缓存/仅采集时间去重（test_n2_time_gate/
test_m1_quote_time 继续绿）；采集时间（fetched_at）仍只诊断不入资格门。

隔离纪律：无持久化写入、零网络零 AI。
跑法：pytest tests/data_sources/test_o1_time_gate_matrix.py -q
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.portfolio import _qualified_weight
from src.core.shadow_diff import _binding_eligibility

SH = ZoneInfo("Asia/Shanghai")


def _binding(*, evidence_cutoff, quote_cutoff):
    """时间字段之外的绑定全给齐（版本取当期真实常量）——drops 只反映时间门。"""
    from src.core.decision_policy import DECISION_TABLE_VERSION, POLICY_ID_MID
    from src.core.research_service import ASSERTION_METHOD_VERSION, RESEARCH_SERVICE_VERSION
    arms = {
        "action": "HOLD", "target": None, "target_state": "UNKNOWN",
        "blockers": [], "execution": "NOT_NEEDED",
    }
    return {
        "horizon": "MID",
        "accepted_ref": "ref_fixture",
        "assessment_id": "asm_fixture",
        "snapshot_id": "snap_fixture",
        "account_version": "v_fixture",
        "policy_id": POLICY_ID_MID,
        "policy_version": RESEARCH_SERVICE_VERSION,
        "method_version": ASSERTION_METHOD_VERSION,
        "decision_rule_version": f"{POLICY_ID_MID}@{DECISION_TABLE_VERSION}",
        "evidence_cutoff": evidence_cutoff,
        "quote_cutoff": quote_cutoff,
        "arms": {"legacy": dict(arms), "fusion": dict(arms)},
    }


def _shadow(evidence_cutoff, quote_cutoff, as_of):
    return _binding_eligibility(_binding(evidence_cutoff=evidence_cutoff,
                                         quote_cutoff=quote_cutoff), as_of=as_of)


def _valuation(quote_raw, nav_raw, as_of):
    return _qualified_weight(100, 10.0, quote_raw, 10000.0, nav_raw, as_of=as_of)


# ── 矩阵：估值/影子两侧对同一输入断言 ─────────────────────────────────

def test_same_day_future_naive_rejected_on_both_sides():
    """当天未来 naive（23:59:59）配 as_of 当天中午——估值拒绝；影子判未来。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    quote = "2026-10-02T23:59:59"
    w, reason = _valuation(quote, "2026-10-02", as_of)
    assert w is None, f"当天未来 naive 不得通过估值门: {w} {reason}"
    assert "未来" in reason
    effective, drops = _shadow("2026-10-01", quote, as_of)
    assert not effective, f"影子门必须拒绝当天未来 naive: {drops}"
    assert any("quote_cutoff_future" in d for d in drops), \
        f"drop 原因必须标记 quote_cutoff_future: {drops}"


def test_same_day_past_naive_qualifies_on_both_sides():
    """当天过去 naive（09:30 盘中）——两边都过资格（新浪本地墙钟正例）。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    quote = "2026-10-02T09:30:00"
    w, _ = _valuation(quote, "2026-10-02", as_of)
    assert w == pytest.approx(0.1), f"当天过去 naive 估值门必须可算: {w}"
    effective, drops = _shadow("2026-10-01", quote, as_of)
    assert effective, f"影子门不得误拒当天过去 naive: {drops}"


def test_equivalent_offset_aware_qualifies_on_both_sides():
    """aware UTC 表示（=上海 12:00）与 as_of 同刻——相等不算未来，两边都过。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    quote = "2026-10-02T04:00:00+00:00"  # = 上海 10-02 12:00
    w, _ = _valuation(quote, "2026-10-02", as_of)
    assert w == pytest.approx(0.1)
    effective, drops = _shadow("2026-10-01", quote, as_of)
    assert effective, f"等价偏移不得误判未来: {drops}"


def test_shanghai_midnight_boundary_consistent():
    """上海午夜边界：as_of=10-02 00:00，前一秒 naive 过、整点 naive 恰等不过。"""
    as_of = datetime(2026, 10, 2, 0, 0, tzinfo=SH)
    w, _ = _valuation("2026-10-01T23:59:59", "2026-10-01", as_of)
    assert w == pytest.approx(0.1), "午夜前一秒（上一交易日盘后）不得误判未来"
    effective, drops = _shadow("2026-10-01", "2026-10-01T23:59:59", as_of)
    assert effective, f"影子同一输入必须同样放行: {drops}"
    w2, reason2 = _valuation("2026-10-02T00:00:00", "2026-10-02", as_of)
    assert w2 == pytest.approx(0.1), "恰等于 as_of 的 naive 不算未来（> 严格比较）"
    eff2, _ = _shadow("2026-10-01", "2026-10-02T00:00:00", as_of)
    assert eff2, "影子同一输入必须同样放行（相等不算未来）"


def test_as_of_expressed_in_utc_matches_shanghai_instant():
    """捕获 as_of 以 UTC 表示（=上海 10-02 00:00）与上海表示同判（等价偏移）。"""
    quote = "2026-10-01T23:59:59"  # naive 上海 10-01 23:59:59
    as_of_utc = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)  # = 上海 10-02 00:00
    as_of_sh = datetime(2026, 10, 2, 0, 0, tzinfo=SH)
    w_utc, _ = _valuation(quote, "2026-10-01", as_of_utc)
    w_sh, _ = _valuation(quote, "2026-10-01", as_of_sh)
    assert w_utc == pytest.approx(w_sh) == pytest.approx(0.1), \
        "as_of 的 UTC/上海表示必须同判"
    eff_utc, _ = _shadow("2026-10-01", quote, as_of_utc)
    eff_sh, _ = _shadow("2026-10-01", quote, as_of_sh)
    assert eff_utc == eff_sh is True, "影子门对等价 as_of 表示必须同判"
    # 反向：同一 as_of 下，naive 当天未来在 UTC 表示下同样拒绝
    quote_future = "2026-10-02T00:00:01"  # 上海 00:00:01 > as_of 上海 00:00
    w_bad, _ = _valuation(quote_future, "2026-10-02", as_of_utc)
    assert w_bad is None, "UTC as_of 下当天未来 naive 同样必须拒绝"
    eff_bad, drops_bad = _shadow("2026-10-01", quote_future, as_of_utc)
    assert not eff_bad and any("quote_cutoff_future" in d for d in drops_bad)


def test_legal_day_precision_qualifies_on_both_sides():
    """合法纯日期（日精度）——按上海日期比较，两边都过。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    w, reason = _valuation("2026-10-01", "2026-10-01", as_of)
    assert w == pytest.approx(0.1) and "合格" in reason
    effective, drops = _shadow("2026-10-01", "2026-10-01", as_of)
    assert effective, f"合法纯日期不得被影子门拒绝: {drops}"


def test_future_date_rejected_on_both_sides():
    """未来日期——估值拒绝；影子对应字段判未来（evidence 与 quote 分别登记）。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    w, reason = _valuation("2026-10-03", "2026-10-03", as_of)
    assert w is None and "未来" in reason
    eff_q, drops_q = _shadow("2026-10-01", "2026-10-03", as_of)
    assert not eff_q and any("quote_cutoff_future" in d for d in drops_q)
    eff_e, drops_e = _shadow("2026-10-03", "2026-10-01", as_of)
    assert not eff_e and any("evidence_cutoff_future" in d for d in drops_e)


def test_missing_and_unparsable_never_qualify_on_both_sides():
    """缺失/非法日历/非法时刻/尾部垃圾——估值 None；影子 unparsable/缺失 drop。"""
    as_of = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    # 缺失
    w, _ = _valuation(None, "2026-10-01", as_of)
    assert w is None
    eff, drops = _shadow("2026-10-01", None, as_of)
    assert not eff and any("no_quote_cutoff" in d for d in drops)
    for bad in ("2026-02-30", "2026-10-01T99:99:99", "2026-10-01garbage", "not-a-date"):
        w, _ = _valuation(bad, "2026-10-01", as_of)
        assert w is None, f"{bad!r} 估值门必须拒绝"
        eff, drops = _shadow("2026-10-01", bad, as_of)
        assert not eff, f"{bad!r} 影子门必须拒绝: {drops}"
        assert any("quote_cutoff_unparsable" in d for d in drops), \
            f"{bad!r} 必须标记 unparsable（非 future——坏值不冒充时点）: {drops}"


def test_fixed_clock_no_wall_clock_dependency():
    """固定时钟：同一输入在 as_of 参数化下结果稳定（不随真实墙钟漂移）。"""
    quote = "2026-10-02T20:00:00"  # 当天 20:00 naive
    as_of_noon = datetime(2026, 10, 2, 12, 0, tzinfo=SH)
    as_of_night = datetime(2026, 10, 2, 23, 0, tzinfo=SH)
    w_noon, _ = _valuation(quote, "2026-10-02", as_of_noon)
    w_night, _ = _valuation(quote, "2026-10-02", as_of_night)
    assert w_noon is None, "as_of=中午时 20:00 是未来——拒绝"
    assert w_night == pytest.approx(0.1), "as_of=23 点时 20:00 是过去——可算"
    eff_noon, _ = _shadow("2026-10-01", quote, as_of_noon)
    eff_night, _ = _shadow("2026-10-01", quote, as_of_night)
    assert eff_noon is False and eff_night is True, "影子门随 as_of 同样翻转"


def test_as_of_positional_backward_compat():
    """既有位置参数调用不回退（无 as_of 时按当前上海墙钟——旧调用方零变化）。"""
    w, _ = _qualified_weight(100, 10.0, "2026-10-01", 10000.0, "2026-10-01")
    assert w == pytest.approx(0.1)
