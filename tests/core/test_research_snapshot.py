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
         source="financial", revision="", **extra):
    """合成证据记录：默认 AS_PUBLISHED_ARCHIVE（version_available_at=available）——
    「在 available 公布的原始版本」语义；版本资格相关测试显式覆盖该参数。"""
    return EvidenceRecord(
        source_kind=source, security_id="600519", metric_or_claim=metric,
        value=value, unit=unit, period_kind=kind, period_end=period,
        published_at=published, available_at=available,
        quality_status=quality, revision_id=revision,
        knowledge_basis=extra.pop("knowledge_basis",
                                  "AS_PUBLISHED_ARCHIVE" if available else "UNKNOWN"),
        version_available_at=extra.pop("version_available_at", available),
        first_seen_at=extra.pop("first_seen_at", None),
        timestamp_precision=extra.pop("timestamp_precision", "day" if available else "unknown"),
        **extra,
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
    """不复权 bar = 交易所当日原始发布（AS_PUBLISHED_ARCHIVE）：
    available_at=bar 收盘日当日尾——旧 as_of 不可见当日 bar。"""
    bar = market_bar_record("600519", {"date": "2026-09-25", "close": 1500.0}, adjust="none")
    snap_before = EvidenceSnapshot.build("600519", _dt(2026, 9, 24), [bar])
    assert snap_before.records == [], "9-25 的 bar 对 9-24 的决策是未来数据"
    snap_after = EvidenceSnapshot.build("600519", _dt(2026, 9, 26), [bar])
    assert snap_after.records and snap_after.records[0].value == 1500.0, "bar 收盘次日才对次日决策可见"


def test_forward_adjusted_bar_not_strict_eligible():
    """前复权序列值随后续公司行动变化（DATA_TRUST §5）——不进严格历史快照，live 可用。"""
    bar = market_bar_record("600519", {"date": "2024-04-10", "close": 1500.0}, adjust="forward")
    assert bar.knowledge_basis == "UNKNOWN"
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [bar], strict=True)
    assert snap.records == [] and snap.drop_reasons.get("knowledge_basis_unknown")
    live = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [bar], strict=False)
    assert len(live.records) == 1


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
    """只有 RAG 检索块时，公司事实要求 INCOMPLETE——方法文本不能当营收/事实。
    （live 视图：RAG 方法块无历史版本资格，本就不进 strict——资格语义在 qualify 层验证）"""
    rag = rag_method_record("投资策略（持续更新）/笨总教学.md", "营收增速大于20%为佳", available_at=_dt(2026, 9, 1))
    snap = EvidenceSnapshot.build("600519", AS_OF, [rag], strict=False)
    status, problems = snap.qualify([RequiredEvidence(metric="revenue")])
    assert status is ResearchStatus.INCOMPLETE, "RAG 块不得满足公司事实要求"
    # 显式允许 rag 的方法论要求可以命中（方法依据用途）
    status2, _ = snap.qualify([RequiredEvidence(metric="method_reference",
                                                allow_source_kinds=["rag"])])
    assert status2 is ResearchStatus.COMPLETE


def test_user_asserted_is_usable_but_marked():
    """人工证据通道：USER_ASSERTED 支持当前人工辅助研究（live 视图）；但仅凭自填
    过去日期不进严格历史效果样本（DATA_TRUST §1——knowledge_basis=UNKNOWN）。"""
    seg = user_asserted_record("600519", "segment_revenue", 45.0, unit="%",
                               asserted_by="user", note="2025年报分部数据，来源巨潮年报P12",
                               period_end="2025-12-31", available_at=_dt(2026, 9, 1))
    assert seg.quality_status is FactStatus.USER_ASSERTED
    assert seg.knowledge_basis == "UNKNOWN"
    live = EvidenceSnapshot.build("600519", AS_OF, [seg], strict=False)
    status, _ = live.qualify([RequiredEvidence(metric="segment_revenue")])
    assert status is ResearchStatus.COMPLETE
    strict_snap = EvidenceSnapshot.build("600519", AS_OF, [seg], strict=True)
    assert strict_snap.records == [], "自填历史时点不进严格历史快照"


# ── 5. 同快照重放稳定 ────────────────────────────────────

def test_snapshot_id_stable_across_replay_and_order():
    r1 = _rec(metric="revenue", available=_dt(2026, 8, 1))
    r2 = market_bar_record("600519", {"date": "2026-09-01", "close": 100.0}, adjust="none")
    s1 = EvidenceSnapshot.build("600519", AS_OF, [r1, r2])
    # 重建：新 record 对象（不同 evidence_id/fetched_at）+ 不同入序 → 同 snapshot_id
    r1b = _rec(metric="revenue", available=_dt(2026, 8, 1))
    r2b = market_bar_record("600519", {"date": "2026-09-01", "close": 100.0}, adjust="none")
    s2 = EvidenceSnapshot.build("600519", AS_OF, [r2b, r1b])
    assert s1.snapshot_id == s2.snapshot_id != "", "同内容快照重放稳定（入序/实例无关）"
    # 内容变化 → id 变化
    s3 = EvidenceSnapshot.build("600519", AS_OF, [r1b])
    assert s3.snapshot_id != s1.snapshot_id


# ── 5b. 历史版本资格（R0 止血 → R1 knowledge_basis 正式化，DATA_TRUST §1）──

def _fin_rec(metric="netProfit", value=999.0, *, revision="", fetched=None,
             knowledge_basis=None, version_available_at=None, first_seen_at=None):
    """latest-only 财务证据（financial_record 默认 knowledge_basis=LATEST_WITH_PUBLICATION_DATE）。"""
    rec = financial_record("600519", {
        "metric": metric, "value": value, "unit": "CNY",
        "period_kind": "cumulative", "period_end": "2023-12-31",
        "published_at": "2024-04-01", "source_uri": "probe://latest-only",
        "source_version": "baostock_financial_v1", "revision_id": revision,
    }, knowledge_basis=knowledge_basis, version_available_at=version_available_at,
        first_seen_at=first_seen_at)
    if fetched is not None:
        return rec.model_copy(update={"fetched_at": fetched})
    return rec


_HISTORY_AS_OF = _dt(2024, 5, 1)


def test_latest_only_financial_excluded_from_strict_history():
    """探针 P4 反例回归：今天抓的 latest-only 财务（旧 pubDate）不能进历史 strict
    快照——「版本不可追溯」≠「当时已发布该版本」（A01；R1 正式化为 knowledge_basis）。"""
    rec = _fin_rec()
    assert rec.knowledge_basis == "LATEST_WITH_PUBLICATION_DATE"
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


def test_original_archive_document_enters_strict_by_publication_time():
    """R1 验收1（后半）：今天下载的可验证原始历史文档按**真实公开日**进历史快照
    （AS_PUBLISHED_ARCHIVE + version_available_at——R0 临时门的正式替代）。"""
    rec = _fin_rec(knowledge_basis="AS_PUBLISHED_ARCHIVE",
                   version_available_at=_dt(2024, 4, 1))
    assert rec.fetched_at is not None and rec.fetched_at > _HISTORY_AS_OF  # 今天抓的
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [rec], strict=True)
    assert len(snap.records) == 1, "档案证据按 version_available_at 入选"


def test_archive_declaration_without_version_time_rejected():
    """档案声明不完整（缺 version_available_at）→ REJECTED——不能只凭 pubDate 自称档案。"""
    with pytest.raises(ValueError, match="version_available_at"):
        _fin_rec(knowledge_basis="AS_PUBLISHED_ARCHIVE")


def test_contemporaneous_capture_supports_only_after_first_seen():
    """CONTEMPORANEOUS_CAPTURE 按 first_seen_at 资格——只支持其后决策，不能回填。"""
    rec = _fin_rec(knowledge_basis="CONTEMPORANEOUS_CAPTURE",
                   first_seen_at=_dt(2024, 4, 5))
    snap_after = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [rec], strict=True)
    assert len(snap_after.records) == 1, "first_seen 之后可用"
    snap_before = EvidenceSnapshot.build("600519", _dt(2024, 4, 2), [rec], strict=True)
    assert snap_before.records == [], "first_seen 之前不可用（不能把今天积累的数据回填过去）"
    with pytest.raises(ValueError, match="first_seen_at"):
        _fin_rec(knowledge_basis="CONTEMPORANEOUS_CAPTURE")


def test_unknown_knowledge_basis_rejected_in_strict():
    """旧记录/未登记版本资格的证据 → UNKNOWN：不进 strict（不自动补绿），live 可见。"""
    rec = financial_record("600519", {
        "metric": "revenue", "value": 1.0, "unit": "元", "period_kind": "cumulative",
        "published_at": "2024-04-01",
    }, knowledge_basis="UNKNOWN")
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [rec], strict=True)
    assert snap.records == [] and snap.drop_reasons.get("knowledge_basis_unknown")
    live = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [rec], strict=False)
    assert len(live.records) == 1, "当前可得但无法证明历史版本——live 诊断可用"


def test_revision_id_alone_no_longer_grants_strict():
    """R0→R1 语义升级：revision_id 只是版本身份标签，**不构成**历史版本资格证明——
    要进 strict 须 AS_PUBLISHED_ARCHIVE+公开时点或当时捕获。"""
    snap = EvidenceSnapshot.build("600519", _HISTORY_AS_OF,
                                  [_fin_rec(revision="r20240401")], strict=True)
    assert snap.records == [], "带 revision 的 latest-only 仍是 latest-only"
    archived = _fin_rec(revision="r20240401", knowledge_basis="AS_PUBLISHED_ARCHIVE",
                        version_available_at=_dt(2024, 4, 1))
    snap2 = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [archived], strict=True)
    assert len(snap2.records) == 1


# ── 5c. R1 修订审计 / 语义筛查 / 派生守卫（DATA_TRUST §2/§3）──

def test_restatement_does_not_change_old_snapshot_hash():
    """R1 验收2：同期修订后旧快照内容 hash 不变；新快照能解释修改来源和时点。"""
    rev1 = _rec(value=100.0, available=_dt(2026, 8, 1), revision="r1")
    old = EvidenceSnapshot.build("600519", _dt(2026, 9, 10), [rev1])
    rev2 = _rec(value=120.0, available=_dt(2026, 9, 20), revision="r2")
    old_rebuilt = EvidenceSnapshot.build("600519", _dt(2026, 9, 10), [rev1, rev2])
    assert old_rebuilt.snapshot_id == old.snapshot_id, "旧快照不受后发修订影响"
    new = EvidenceSnapshot.build("600519", AS_OF, [rev1, rev2])
    assert new.snapshot_id != old.snapshot_id
    latest = new.latest("revenue")
    assert latest.revision_id == "r2" and latest.source_uri == rev2.source_uri, \
        "新快照带修订来源与时点（available_at=2026-09-20）"
    assert [r.revision_id for r in new.revisions_of("revenue")] == ["r1", "r2"]


def test_unit_anomaly_suspect_isolated_not_frozen():
    """R1 验收4（ISS-114）：同指标相邻期 >10x 跳变 → SUSPECT 隔离该指标；其余指标
    不受累；不自动换算/纠偏。"""
    r_ok = _rec(metric="netProfit", value=7.75e10, available=_dt(2024, 4, 3),
                period="2023-12-31")
    drifted_old = _rec(metric="liabilityToAsset", value=0.7322, available=_dt(2023, 10, 27),
                       period="2023-09-30", value_kind="RATIO",
                       period_basis="POINT_IN_TIME")
    drifted_new = _rec(metric="liabilityToAsset", value=0.0073, available=_dt(2024, 1, 30),
                       period="2023-12-31", value_kind="RATIO",
                       period_basis="POINT_IN_TIME")
    # 语义筛查在快照外先跑（与采集管线一致）：跳变方标 SUSPECT
    from src.data.research_snapshot import screen_semantic_anomalies
    screened = screen_semantic_anomalies([drifted_old, drifted_new])
    assert screened[0].semantic_status != "SUSPECT"
    assert screened[1].semantic_status == "SUSPECT", "跳变方（较新）标 SUSPECT"
    assert "不自动" in screened[1].semantic_note
    snap = EvidenceSnapshot.build("600519", AS_OF, [r_ok, screened[1]], strict=True)
    assert len(snap.records) == 1 and snap.records[0].metric_or_claim == "netProfit", \
        "SUSPECT 隔离——正常字段不受累（不冻结全产品）"
    status, problems = snap.qualify([
        RequiredEvidence(metric="netProfit"), RequiredEvidence(metric="liabilityToAsset")])
    assert status is ResearchStatus.INCOMPLETE
    assert any("证据可疑" in p and "liabilityToAsset" in p for p in problems), "隔离有具体人话"


def test_zero_and_growth_not_screened():
    """零值/缺失不做跳变分母（单独分支）；GROWTH 合法跨零不筛查；FLOW 登记不筛查。"""
    from src.data.research_snapshot import screen_semantic_anomalies
    zero_then_big = _rec(metric="currentRatio", value=0.0, available=_dt(2024, 1, 30),
                         period="2023-12-31")
    big = _rec(metric="currentRatio", value=4.6, available=_dt(2024, 4, 28),
               period="2024-03-31")
    assert all(r.semantic_status != "SUSPECT"
               for r in screen_semantic_anomalies([zero_then_big, big])), "零值分母跳过"
    growth_a = _rec(metric="YOYNI", value=-0.5, available=_dt(2023, 10, 27),
                    period="2023-09-30", source="financial")
    growth_b = _rec(metric="YOYNI", value=0.8, available=_dt(2024, 1, 30),
                    period="2023-12-31", source="financial")
    growth_a = growth_a.model_copy(update={"value_kind": "GROWTH"})
    growth_b = growth_b.model_copy(update={"value_kind": "GROWTH"})
    assert all(r.semantic_status != "SUSPECT"
               for r in screen_semantic_anomalies([growth_a, growth_b])), "GROWTH 跨零合法"


def test_ratio_cumulative_diff_rejected_and_ttm_not_mixed():
    """R1 验收3：比率/存量禁止累计差分；跨年差分拒绝；TTM 只由连续单季 FLOW 拼。"""
    from src.data.research_snapshot import (
        derive_single_quarter_from_cumulative,
        derive_ttm_from_single_quarters,
    )
    ratio_q1 = _rec(metric="liabilityToAsset", value=0.73, available=_dt(2024, 4, 27),
                    period="2024-03-31")
    ratio_q2 = _rec(metric="liabilityToAsset", value=0.71, available=_dt(2024, 8, 30),
                    period="2024-06-30")
    for r in (ratio_q1, ratio_q2):
        r = r.model_copy(update={"value_kind": "RATIO", "period_basis": "POINT_IN_TIME"})
    with pytest.raises(ValueError, match="FLOW"):
        derive_single_quarter_from_cumulative(
            ratio_q1.model_copy(update={"value_kind": "RATIO", "period_basis": "YTD"}),
            ratio_q2.model_copy(update={"value_kind": "RATIO", "period_basis": "YTD"}))
    flow_q1 = _rec(metric="netProfit", value=240.0, available=_dt(2024, 4, 27),
                   period="2024-03-31").model_copy(update={"value_kind": "FLOW",
                                                           "period_basis": "YTD"})
    flow_q4_prev_year = _rec(metric="netProfit", value=747.0, available=_dt(2023, 4, 7),
                             period="2022-12-31").model_copy(update={"value_kind": "FLOW",
                                                                     "period_basis": "YTD"})
    with pytest.raises(ValueError, match="相邻"):
        derive_single_quarter_from_cumulative(flow_q4_prev_year, flow_q1), "跨年差分拒绝"
    q2 = _rec(metric="netProfit", value=490.0, available=_dt(2024, 8, 30),
              period="2024-06-30").model_copy(update={"value_kind": "FLOW",
                                                      "period_basis": "YTD"})
    single = derive_single_quarter_from_cumulative(flow_q1, q2)
    assert single.value == pytest.approx(250.0) and single.period_basis == "SINGLE_QUARTER"
    assert single.period_kind == "single"
    # TTM：拿 YTD 拼接 → 拒；4 个连续单季 → 成功
    with pytest.raises(ValueError, match="单季 FLOW"):
        derive_ttm_from_single_quarters([flow_q1, q2, flow_q1, q2])
    singles = []
    vals = [("2023-03-31", 100.0, (2023, 4, 27)), ("2023-06-30", 110.0, (2023, 7, 27)),
            ("2023-09-30", 120.0, (2023, 10, 27)), ("2023-12-31", 130.0, (2024, 1, 27))]
    for pe, v, (ay, am, ad) in vals:
        base = _rec(metric="netProfit", value=v, available=_dt(ay, am, ad), period=pe)
        singles.append(base.model_copy(update={
            "value_kind": "FLOW", "period_basis": "SINGLE_QUARTER", "period_kind": "single"}))
    ttm = derive_ttm_from_single_quarters(singles)
    assert ttm.value == pytest.approx(460.0) and ttm.period_basis == "TTM"


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


# ── 5d. R1 审查修复回归（监督员 P1/P2 发现）─────────────────

def test_naive_new_time_fields_rejected_at_construction():
    """P1 回归：version_available_at/first_seen_at 同样拒绝 naive datetime
    （拦截点在构造期，不在快照构建深处崩 TypeError）。"""
    with pytest.raises(ValueError, match="时区"):
        _rec(available=_dt(2024, 4, 1), version_available_at=datetime(2024, 4, 1))
    with pytest.raises(ValueError, match="时区"):
        _rec(available=_dt(2024, 4, 1), first_seen_at=datetime(2024, 4, 1))


def test_archive_derivation_inherits_version_provenance():
    """P1 回归：档案累计差分出的单季**按继承的 version_available_at 进 strict**
    （「原始历史文档路径」经派生不断裂）。"""
    from src.data.research_snapshot import derive_single_quarter_from_cumulative
    q1 = _rec(metric="netProfit", value=240.0, available=_dt(2024, 4, 27),
              period="2024-03-31", value_kind="FLOW", period_basis="YTD")
    q2 = _rec(metric="netProfit", value=490.0, available=_dt(2024, 8, 30),
              period="2024-06-30", value_kind="FLOW", period_basis="YTD")
    single = derive_single_quarter_from_cumulative(q1, q2)
    assert single.version_available_at == _dt(2024, 8, 30), "继承=两输入公开时点较晚者"
    snap = EvidenceSnapshot.build("600519", _dt(2024, 9, 15), [single], strict=True)
    assert len(snap.records) == 1, "档案血统派生值入选 strict（资格不断裂）"


def test_derivation_lineage_guards():
    """P1 回归：SUSPECT 输入 / 跨主体 / 跨修订（首发×重述）派生全部拒绝。"""
    from src.data.research_snapshot import derive_single_quarter_from_cumulative
    q1 = _rec(metric="netProfit", value=240.0, available=_dt(2024, 4, 27),
              period="2024-03-31", value_kind="FLOW", period_basis="YTD")
    q2 = _rec(metric="netProfit", value=490.0, available=_dt(2024, 8, 30),
              period="2024-06-30", value_kind="FLOW", period_basis="YTD")
    suspect = q2.model_copy(update={"semantic_status": "SUSPECT", "semantic_note": "跳变"})
    with pytest.raises(ValueError, match="SUSPECT"):
        derive_single_quarter_from_cumulative(q1, suspect)
    other_co = q2.model_copy(update={"security_id": "000002"})
    with pytest.raises(ValueError, match="跨主体"):
        derive_single_quarter_from_cumulative(q1, other_co)
    restated = q2.model_copy(update={"revision_id": "r2-restated"})
    with pytest.raises(ValueError, match="修订"):
        derive_single_quarter_from_cumulative(q1, restated), "首发×重述相减被禁"
    mixed_kb = q2.model_copy(update={"knowledge_basis": "CONTEMPORANEOUS_CAPTURE",
                                     "first_seen_at": _dt(2024, 8, 30)})
    with pytest.raises(ValueError, match="血统"):
        derive_single_quarter_from_cumulative(q1, mixed_kb)


def test_ttm_bad_period_end_valueerror():
    """P2 回归：period_end 非法/缺失 → 显式 ValueError（不是 TypeError）。"""
    from src.data.research_snapshot import derive_ttm_from_single_quarters
    singles = []
    for pe, v, av in [("2023-03-31", 100.0, (2023, 4, 27)), ("2023-06-30", 110.0, (2023, 7, 27)),
                      ("2023-09-30", 120.0, (2023, 10, 27)), ("2023-12-31", 130.0, (2024, 1, 27))]:
        base = _rec(metric="netProfit", value=v, available=_dt(*av), period=pe)
        singles.append(base.model_copy(update={"value_kind": "FLOW",
                                               "period_basis": "SINGLE_QUARTER",
                                               "period_kind": "single"}))
    broken = [r.model_copy(update={"period_end": None}) if i == 2 else r
              for i, r in enumerate(singles)]
    with pytest.raises(ValueError, match="period_end"):
        derive_ttm_from_single_quarters(broken)


def test_real_business_mutation_isolated_then_resolved_by_human():
    """P2 补测（R1 验收4 第三类反例）：真实经营突变（重组致股本 10x）同样被筛查
    隔离（筛查不辨真伪——只标记）；人工核对后可解除隔离（VERIFIED+结论注记），
    其余字段全程不受累。"""
    from src.data.research_snapshot import screen_semantic_anomalies
    normal = _rec(metric="netProfit", value=7.7e10, available=_dt(2024, 4, 3),
                  period="2023-12-31")
    merger_old = _rec(metric="totalShare", value=1.256e9, available=_dt(2024, 4, 27),
                      period="2024-03-31", value_kind="STOCK", period_basis="POINT_IN_TIME")
    merger_new = _rec(metric="totalShare", value=2.512e10, available=_dt(2024, 8, 30),
                      period="2024-06-30", value_kind="STOCK", period_basis="POINT_IN_TIME")
    screened = screen_semantic_anomalies([normal, merger_old, merger_new])
    flagged = next(r for r in screened if r.metric_or_claim == "totalShare"
                   and r.period_end == "2024-06-30")
    assert flagged.semantic_status == "SUSPECT", "真实突变同样先隔离（不辨真伪）"
    assert normal.semantic_status != "SUSPECT", "其余字段不受累"
    # 人工核对确认真实重组 → 解除隔离（人工结论入注记，不自动发生）
    resolved = flagged.model_copy(update={
        "semantic_status": "VERIFIED",
        "semantic_note": "人工核对：2024-06 送股重组（原始财报核实）——解除隔离"})
    snap = EvidenceSnapshot.build("600519", AS_OF, [normal, resolved], strict=True)
    status, problems = snap.qualify([RequiredEvidence(metric="netProfit"),
                                     RequiredEvidence(metric="totalShare")])
    assert status is ResearchStatus.COMPLETE, f"人工解除后资格恢复: {problems}"


def test_drop_reason_codes_structural_classification():
    """P2 回归：拒收原因码按结构判定（非文本匹配）——每条路径落正确的 drop_reasons 键。"""
    # 档案缺 version_available_at（构造直接建，绕过 financial_record 校验）
    bad_archive = EvidenceRecord(source_kind="financial", security_id="600519",
                                 metric_or_claim="x", value=1.0, unit="元",
                                 available_at=_dt(2024, 4, 1),
                                 knowledge_basis="AS_PUBLISHED_ARCHIVE",
                                 version_available_at=None)
    s1 = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [bad_archive], strict=True)
    assert s1.drop_reasons == {"archive_missing_version_time": ["x"]}
    # 当时捕获缺 first_seen
    bad_capture = EvidenceRecord(source_kind="financial", security_id="600519",
                                 metric_or_claim="x", value=1.0, unit="元",
                                 available_at=_dt(2024, 4, 5),
                                 knowledge_basis="CONTEMPORANEOUS_CAPTURE",
                                 first_seen_at=None)
    s2 = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [bad_capture], strict=True)
    assert s2.drop_reasons == {"contemporaneous_missing_first_seen": ["x"]}
    # 档案血统但 available_at=None
    no_time = EvidenceRecord(source_kind="financial", security_id="600519",
                             metric_or_claim="x", value=1.0, unit="元",
                             available_at=None,
                             knowledge_basis="AS_PUBLISHED_ARCHIVE",
                             version_available_at=_dt(2024, 4, 1))
    s3 = EvidenceSnapshot.build("600519", _HISTORY_AS_OF, [no_time], strict=True)
    assert s3.drop_reasons == {"no_trustworthy_time": ["x"]}
