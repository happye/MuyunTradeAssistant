"""F3 研究证据快照与时点资格回归测试（plan/fusion TASKS.md F3 验收条款）

锁死语义（DESIGN ADR-F05）：
1. 未来公告和后发重述不进入旧快照（strict PIT 闸门：available_at 缺失/晚于 as_of 必拒）
2. 元/万元及累计/单季口径可验证（显式字段 + 显式换算，未知单位拒猜）
3. 删必需字段后资格变 INCOMPLETE（缺失显式命名，不落 0/50）
4. RAG 方法文本不当公司事实（qualify 默认排除 source_kind=rag）
5. 同快照重放稳定（snapshot_id 内容 hash，入序无关）
6. 回测路径零实时网络（模块 import 与纯函数路径无网络依赖——源码守卫）

纯内存测试，无网络。跑法：pytest tests/core/test_research_snapshot.py -q
"""
import ast
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.decision_contract import FactStatus, ResearchStatus
from src.data.research_snapshot import (
    EvidenceRecord,
    EvidenceSnapshot,
    RequiredEvidence,
    announcement_record,
    financial_record,
    forecast_record,
    market_bar_record,
    normalize_amount,
    rag_method_record,
    user_asserted_record,
)

TZ = timezone.utc


def _dt(y, m, d, hh=8):
    return datetime(y, m, d, hh, 0, tzinfo=TZ)


AS_OF = _dt(2026, 9, 25)


def _rec(metric="revenue", value=100.0, unit="万元", *, available=None, published=None,
         kind=None, period="2026-06-30", quality=FactStatus.OBSERVED,
         source="financial", revision=""):
    return EvidenceRecord(
        source_kind=source, security_id="600519", metric_or_claim=metric,
        value=value, unit=unit, period_kind=kind, period_end=period,
        published_at=published, available_at=available,
        quality_status=quality, revision_id=revision,
    )


# ── 1. 未来公告和后发重述不进入旧快照 ─────────────────────

def test_future_announcement_excluded_from_old_snapshot():
    """as_of 之前不可得的公告（未来公布）不得进入旧快照。"""
    future_ann = announcement_record(
        "600519", {"title": "重大合同", "date": "2026-10-01", "source": "巨潮资讯"},
        official_date=True)
    snap = EvidenceSnapshot.build("600519", AS_OF, [future_ann])
    assert snap.records == [], "未来公告必须被拒"
    assert snap.dropped_pit == 1 and snap.dropped_ids, "被拒证据如实记录"


def test_untrustworthy_publish_time_excluded():
    """无可信公布时间（东财新闻式）→ available_at=None → 不进严格快照（live 视图可见）。"""
    news = announcement_record(
        "600519", {"title": "传闻", "source": "东方财富"}, official_date=False)
    assert news.pit_confident is False
    snap = EvidenceSnapshot.build("600519", AS_OF, [news])
    assert snap.records == []
    # live 诊断视图（strict=False）可见
    live_snap = EvidenceSnapshot.build("600519", AS_OF, [news], strict=False)
    assert len(live_snap.records) == 1


def test_late_restatement_does_not_enter_old_snapshot():
    """后发重述：rev1 在 T1 公布（营收 100），rev2 在 T3 重述（120）——
    T2 快照只见 rev1，T4 快照才见 rev2；两版都是可用历史，各进各的时点。"""
    rev1 = _rec(value=100.0, available=_dt(2026, 8, 1), revision="r1")
    rev2 = _rec(value=120.0, available=_dt(2026, 9, 20), revision="r2")
    snap_t2 = EvidenceSnapshot.build("600519", _dt(2026, 9, 10), [rev1, rev2])
    assert [r.revision_id for r in snap_t2.records] == ["r1"], "T2 不得看到 T3 才公布的重述"
    assert snap_t2.latest("revenue").value == 100.0
    snap_t4 = EvidenceSnapshot.build("600519", AS_OF, [rev1, rev2])
    assert snap_t4.latest("revenue").value == 120.0, "T4 快照用重述版（当时可得）"
    assert len(snap_t4.revisions_of("revenue")) == 2, "重述版本可审计"


def test_market_bar_pit_natural():
    """行情 bar 天然 PIT：available_at=bar 收盘日当日尾——旧 as_of 不可见当日 bar。"""
    bar = market_bar_record("600519", {"date": "2026-09-25", "close": 1500.0})
    snap_before = EvidenceSnapshot.build("600519", _dt(2026, 9, 24), [bar])
    assert snap_before.records == [], "9-25 的 bar 对 9-24 的决策是未来数据"
    snap_after = EvidenceSnapshot.build("600519", _dt(2026, 9, 26), [bar])
    assert snap_after.records and snap_after.records[0].value == 1500.0, "bar 收盘次日才对次日决策可见"


# ── 2. 元/万元与累计/单季口径可验证 ───────────────────────

def test_unit_conversion_explicit():
    assert normalize_amount(500.0, "万元") == pytest.approx(5_000_000.0)
    assert normalize_amount(2.0, "亿元") == pytest.approx(200_000_000.0)
    assert normalize_amount(5_000_000.0, "元", target="万元") == pytest.approx(500.0)
    with pytest.raises(ValueError, match="单位"):
        normalize_amount(1.0, "万美元")  # 未登记单位拒猜


def test_period_kind_must_be_explicit_for_financial():
    """财务证据必须显式给累计/单季口径——缺口径拒绝构造（G15）。"""
    with pytest.raises(ValueError, match="period_kind"):
        financial_record("600519", {"metric": "revenue", "value": 100.0,
                                    "period_end": "2026-06-30"})
    rec = financial_record("600519", {"metric": "revenue", "value": 100.0, "unit": "亿元",
                                      "period_kind": "single", "period_end": "2026-06-30",
                                      "published_at": "2026-08-01"})
    assert rec.period_kind == "single" and rec.unit == "亿元"


def test_qualify_respects_period_kind():
    """口径不混用：要求单季时累计证据不顶数。"""
    cum = _rec(kind="cumulative", value=300.0, available=_dt(2026, 8, 1))
    snap = EvidenceSnapshot.build("600519", AS_OF, [cum])
    req_single = [RequiredEvidence(metric="revenue", period_kind="single")]
    status, problems = snap.qualify(req_single)
    assert status is ResearchStatus.INCOMPLETE
    assert any("单季" in p for p in problems)
    status2, _ = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status2 is ResearchStatus.COMPLETE, "不限口径时累计证据可用"


# ── 3. 删必需字段后资格变 INCOMPLETE ─────────────────────

def test_missing_required_evidence_incomplete_named():
    req = [RequiredEvidence(metric="revenue", max_age_days=90),
           RequiredEvidence(metric="operating_cash_flow", max_age_days=400)]
    full = [_rec(metric="revenue", available=_dt(2026, 8, 1)),
            _rec(metric="operating_cash_flow", available=_dt(2026, 5, 1))]
    snap_ok = EvidenceSnapshot.build("600519", AS_OF, full)
    status, problems = snap_ok.qualify(req)
    assert status is ResearchStatus.COMPLETE and problems == []

    # 删掉现金流证据 → INCOMPLETE 且点名缺失项（不是 0/50 兜底）
    snap_missing = EvidenceSnapshot.build("600519", AS_OF, [full[0]])
    status2, problems2 = snap_missing.qualify(req)
    assert status2 is ResearchStatus.INCOMPLETE
    assert any("operating_cash_flow" in p for p in problems2)


def test_stale_evidence_flagged():
    """时效判定：available_at 距 as_of 超上限 → 过期显式命名（STALE 语义）。"""
    old = _rec(metric="revenue", available=_dt(2026, 1, 1))  # 距今 ~9 个月
    snap = EvidenceSnapshot.build("600519", AS_OF, [old])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue", max_age_days=90)])
    assert status is ResearchStatus.INCOMPLETE
    assert any("过期" in p for p in problems)


def test_conflicting_evidence_reported():
    """F3 审查 P1-2 语义重写：真冲突 = 同事实同可用时点的跨源不一致观测。

    重述（available 晚者替代早者）与多报告期历史**不是**冲突（DESIGN §5.1
    "历史回放选择当时可得版本" + ADR-F06"更正公告是新版本"）。"""
    # 真冲突：同报告期、同一可用时点、两个来源、不同值
    src_a = _rec(value=100.0, available=_dt(2026, 8, 1), source="financial", revision="r1")
    src_b = _rec(value=999.0, available=_dt(2026, 8, 1), source="user_asserted", revision="r2")
    snap = EvidenceSnapshot.build("600519", AS_OF, [src_a, src_b])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.CONFLICTED
    assert any("冲突" in p for p in problems)


def test_restatement_pair_is_not_conflict():
    """F3 审查 P1-2 回归锁：后发重述对（同报告期、不同 available 时点）qualify
    不判冲突——晚者替代早者，资格 COMPLETE 且取重述值。"""
    rev1 = _rec(value=100.0, available=_dt(2026, 8, 1), revision="r1")
    rev2 = _rec(value=120.0, available=_dt(2026, 9, 20), revision="r2")
    snap = EvidenceSnapshot.build("600519", AS_OF, [rev1, rev2])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.COMPLETE, f"重述对不是冲突: {problems}"
    assert snap.latest("revenue").value == 120.0


def test_multi_period_history_is_not_conflict():
    """F3 审查 P1-2 回归锁：不同报告期（Q1/Q2 历史）是不同事实——永不互判冲突
    （DESIGN §5.2 长视野 12 季度包必然多期并存）。"""
    q1 = _rec(value=80.0, period="2026-03-31", available=_dt(2026, 4, 20))
    q2 = _rec(value=180.0, period="2026-06-30", available=_dt(2026, 7, 20))
    snap = EvidenceSnapshot.build("600519", AS_OF, [q1, q2])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue", max_age_days=365)])
    assert status is ResearchStatus.COMPLETE, f"多期历史不是冲突: {problems}"


def test_same_value_different_units_not_conflict():
    """F3 审查 P1-2 回归锁：同值不同单位（100万元 vs 1000000元）归一后相等——不冲突。"""
    a = _rec(value=100.0, unit="万元", available=_dt(2026, 8, 1))
    b = _rec(value=1_000_000.0, unit="元", available=_dt(2026, 8, 1))
    snap = EvidenceSnapshot.build("600519", AS_OF, [a, b])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.COMPLETE, f"单位归一后同值不是冲突: {problems}"


def test_live_view_with_untimed_records_does_not_crash():
    """F3 审查 P1-1 回归锁：strict=False live 视图 + 多条无可信时点记录（东财新闻
    常态）——latest/revisions_of/qualify 全部不崩（None 排序键安全）。"""
    news1 = announcement_record("600519", {"title": "传闻1", "source": "东方财富"}, official_date=False)
    news2 = announcement_record("600519", {"title": "传闻2", "source": "东方财富"}, official_date=False)
    live = EvidenceSnapshot.build("600519", AS_OF, [news1, news2], strict=False)
    assert live.latest("announcement") is not None  # 不抛 TypeError
    assert len(live.revisions_of("announcement")) == 2
    status, problems = live.qualify([RequiredEvidence(metric="announcement")])
    assert status is ResearchStatus.INCOMPLETE
    assert any("无可信时点" in p for p in problems), "无时点证据显式报告不进严格判定"


def test_missing_quality_status_excluded():
    """quality=MISSING/NOT_APPLICABLE 的记录不满足要求（占位不算数）。"""
    placeholder = _rec(value=None, quality=FactStatus.MISSING, available=_dt(2026, 8, 1))
    snap = EvidenceSnapshot.build("600519", AS_OF, [placeholder])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.INCOMPLETE


# ── 4. RAG 方法文本不当公司事实 ──────────────────────────

def test_rag_text_not_company_fact():
    """只有 RAG 检索块时，公司事实要求 INCOMPLETE——方法文本不能当营收/事实。"""
    rag = rag_method_record("投资策略（持续更新）/笨总教学.md", "营收增速大于20%为佳", available_at=_dt(2026, 9, 1))
    snap = EvidenceSnapshot.build("600519", AS_OF, [rag])
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.INCOMPLETE, "RAG 块不得满足公司事实要求"
    # 显式允许 rag 的方法论要求可以命中（方法依据用途）
    status2, _ = snap.qualify([RequiredEvidence(metric="method_reference",
                                                allow_source_kinds=["rag"])])
    assert status2 is ResearchStatus.COMPLETE


def test_user_asserted_is_usable_but_marked():
    """人工证据通道：USER_ASSERTED 可满足要求但状态可辨（不冒充 OBSERVED）。"""
    seg = user_asserted_record("600519", "segment_revenue", 45.0, unit="%",
                               asserted_by="user", note="2025年报分部数据，来源巨潮年报P12",
                               period_end="2025-12-31", available_at=_dt(2026, 9, 1))
    assert seg.quality_status is FactStatus.USER_ASSERTED
    snap = EvidenceSnapshot.build("600519", AS_OF, [seg])
    status, _ = snap.qualify([RequiredEvidence(metric="segment_revenue")])
    assert status is ResearchStatus.COMPLETE


# ── 5. 同快照重放稳定 ────────────────────────────────────

def test_snapshot_id_stable_across_replay_and_order():
    r1 = _rec(metric="revenue", available=_dt(2026, 8, 1))
    r2 = market_bar_record("600519", {"date": "2026-09-01", "close": 100.0})
    s1 = EvidenceSnapshot.build("600519", AS_OF, [r1, r2])
    # 重建：新 record 对象（不同 evidence_id/fetched_at）+ 不同入序 → 同 snapshot_id
    r1b = _rec(metric="revenue", available=_dt(2026, 8, 1))
    r2b = market_bar_record("600519", {"date": "2026-09-01", "close": 100.0})
    s2 = EvidenceSnapshot.build("600519", AS_OF, [r2b, r1b])
    assert s1.snapshot_id == s2.snapshot_id != "", "同内容快照重放稳定（入序/实例无关）"
    # 内容变化 → id 变化
    s3 = EvidenceSnapshot.build("600519", AS_OF, [r1b])
    assert s3.snapshot_id != s1.snapshot_id


# ── 5b. R0 资格止血：latest-only 财务证据不入严格历史快照（A01）──

def _fin_rec(metric="netProfit", value=999.0, *, revision="", fetched=None):
    """latest-only 财务证据（financial_record 默认 fetched_at=现在，即今天抓的）。"""
    rec = financial_record("600519", {
        "metric": metric, "value": value, "unit": "CNY",
        "period_kind": "cumulative", "period_end": "2023-12-31",
        "published_at": "2024-04-01", "source_uri": "probe://latest-only",
        "source_version": "baostock_financial_v1", "revision_id": revision,
    })
    if fetched is not None:
        return rec.model_copy(update={"fetched_at": fetched})
    return rec


_HISTORY_AS_OF = _dt(2024, 5, 1)


def test_latest_only_financial_excluded_from_strict_history():
    """探针 P4 反例回归：今天抓的 latest-only 财务（旧 pubDate、无 revision）
    不能进历史 strict 快照——「版本不可追溯」≠「当时已发布该版本」（A01 止血）。"""
    rec = _fin_rec()
    assert rec.fetched_at is not None and rec.fetched_at > _HISTORY_AS_OF
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [rec], strict=True)
    assert snap.records == [] and snap.dropped_pit == 1
    assert snap.drop_reasons.get("latest_only_unverifiable") == ["netProfit"]
    assert rec.evidence_id in snap.dropped_ids


def test_latest_only_exclusion_surfaces_specific_qualify_problem():
    """R0 验收3：用户看到具体「版本不可追溯」，不是笼统「缺失必需证据」。"""
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [_fin_rec()], strict=True)
    status, problems = snap.qualify([RequiredEvidence(metric="netProfit")])
    assert status is ResearchStatus.INCOMPLETE
    assert any("版本不可追溯" in p and "netProfit" in p for p in problems)


def test_versioned_financial_with_revision_enters_strict():
    """带 revision（版本可区分）不触 latest-only 门——R1 版本分级的临时合法路径。"""
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF,
                                  [_fin_rec(revision="r20240401")], strict=True)
    assert len(snap.records) == 1


def test_financial_fetched_before_asof_enters_strict():
    """当时抓取（fetched_at ≤ as_of）正常入快照——门只拦「晚抓的 latest-only」。"""
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF,
                                  [_fin_rec(fetched=_dt(2024, 4, 5))], strict=True)
    assert len(snap.records) == 1


def test_market_bars_unaffected_by_latest_only_gate():
    """行情天然 PIT（bar 自带日期）——晚抓不触发 latest-only 门（防矫枉过正）。"""
    bar = market_bar_record("600519", {"date": "2024-04-10", "close": 1500.0})
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [bar], strict=True)
    assert len(snap.records) == 1


# ── 6. 回测路径零实时网络（源码守卫）─────────────────────

def test_module_import_is_network_free():
    """F3 审查 P2-3 加固：递归 AST——网络类 import 只许出现在两个 live helper
    函数体内，其余任何作用域出现即违规（回测路径零实时网络）。"""
    src = Path(__file__).resolve().parents[2] / "src" / "data" / "research_snapshot.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    banned = ("akshare", "baostock", "requests", "urllib", "httpx", "efinance")
    allowed_functions = {"fetch_announcement_evidence_live", "fetch_forecast_evidence_live"}

    # 父节点映射：判断某 import 节点是否处于允许函数子树内
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""])
        if not any(any(b in (n or "") for b in banned) for n in names):
            continue
        cur, inside_allowed = node, False
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.FunctionDef) and cur.name in allowed_functions:
                inside_allowed = True
                break
        assert inside_allowed, \
            f"网络依赖 import 出现在 live helper 之外（回测路径零实时网络）: {names}"


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError, match="时区"):
        _rec(available=datetime(2026, 8, 1))  # naive
    with pytest.raises(ValueError, match="时区"):
        EvidenceSnapshot.build("600519", datetime(2026, 9, 25), [])
