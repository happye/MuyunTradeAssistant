"""影子差异捕获与报告（plan/fusion 影子阶段前置，ROLLOUT §1/§4）。

职责：在**不改主结论、不回写持仓**的前提下，对每次持仓分析额外用周期决策表
（fusion_mid_v1 / fusion_long_v1，F5 evaluate_horizon）出对照包，差异落账
~/.muyun/shadow_diff.jsonl，供 `shadow` 命令按原因分组观察——DESIGN 原文：
「shadow 运行新策略但不改用户行动卡主结论、不回写实际持仓；差异报告标记原因，
不能只比较总收益」。

映射规则版本 SHADOW_DERIVATION_VERSION（改动映射必须 bump 并在报告留版本）。
信息集标签 = rule_bz_proxy（VALIDATION §3：影子对照属规则代理信息集，非 AI 前瞻）。

facts 映射规则（v1，显式登记；改动必须 bump 版本）：
- hard_exit_triggered  ← sell_path ∈ {fundamental_alert, top_signal}
  （证据支撑的强制退出通道：ST/业绩预亏硬退出、实控人减持/换手见顶 force-exit——
  PlanGuard 不可压；被压制/被降级场景行1 仍触发，"已验证硬退出不被官僚流程掩盖"）
- technical_exit_triggered ← sell_path ∈ {trend_exit, take_profit_trim, stop_loss_exit,
  stop_loss_trim, time_stop, weak_sell}（预设技术退出，MID 行5）
- entry_condition_met  ← 终态 position_action ∈ {OPEN, ADD}
- research_status      ← packet.research_status 透传（数据缺口/分歧如实进入决策表）
- thesis_status ← **v3**：经 research.assess_thesis 唯一入口评估（已激活计划的事实
  需带可解析证据引用才 VALID；无引用文本/空白 → UNESTABLISHED，评分/技术信号仍不
  冒充逻辑）
- budget_available     ← None（组合预算未接，组合信息未知不给精确目标）
- confirmed_ratio      ← 持仓事实 pos.current_ratio
- 不把 legacy mode（气宗/剑宗）映射成 horizon——每个持仓同时评估 MID 与 LONG
  两个包（TASKS F5 硬约束："不用旧 mode 代替 horizon"）

模拟计划语义：shadow 派生的 HorizonPlan.plan_id 带 shadow_ 前缀、accepted_at 取当前
时点（模拟激活以越过行0 激活门——否则全部 REVIEW 无观察价值），但每条记录带
shadow_disclosure 字段且 reason 首条固定标注「shadow 模拟计划（非用户确认，仅对照
观察）」。模拟计划不落盘到用户计划、不影响任何主结论，关闭开关即零写入。

v3（R0 资格止血，2026-09-26）：
- thesis 状态不再影子自写「facts 非空 → VALID」——统一经 research.assess_thesis
  唯一入口（无证据引用的文本事实 → UNESTABLISHED，A03）
- 来源**逐周期**标注 mid_plan_source / long_plan_source、mid_thesis_status /
  long_thesis_status（同股仅 MID 接受时 LONG 显式 simulated——A05 整行聚合修复）；
  v2 的整行 plan_source / thesis_status 保留为兼容聚合口径（任一周期），报告与
  原因分类已改用分周期字段，R3 收口时移除聚合字段
- 新差异原因 facts_unverified：已激活计划的事实无可解析证据引用（R0 资格门落表）

v4（J0b 评估唯一真值，2026-09-27）：
- thesis 状态不再由影子从 facts+refs 重判——计划带 assessment_id 时经
  AssessmentStore 加载并核对（security/horizon/snapshot/method/时效）后消费
  **同一 status**；找不到/错配/过期 → REVIEW_REQUIRED（明确待复核）
- 旧计划（无 assessment_id）→ UNESTABLISHED 降级：facts+refs 简化判断退役，
  不再可能被非空引用救成 VALID（N2 根因封堵）；计划文字保留、资料不销毁

v7（L0 观察合同，2026-10-01；R11 V1/V2 修复——版本升级为独立新协议，旧 v6 不追认）：
- **同一资格函数** `_binding_eligibility` 决定字段缺口、eligible/diagnostic 与
  报告分母——缺任一必要字段（accepted_ref/assessment/snapshot/account_version/
  policy_version/method/规则版本/证据截止/行情时点/两臂完整）或时点在未来 →
  降级 diagnostic，不为凑有效样本伪造时点或版本
- 行情时点（quote_cutoff）来自实际行情（StockData.quote_as_of：抓取时刻/最近
  K 线日期）——不可得时如实缺省并阻塞资格；证据截止（evidence_cutoff）来自
  **被消费评估**的 evaluated_as_of（不再用捕获墙钟冒充）；decision_rule_version
  来自真实规则版本常量（policy_id@DECISION_TABLE_VERSION，不再复制 policy_id）
- 两臂输出完整：每绑定 arms={legacy, fusion}×{action,target,target_state,
  blockers,execution}——补 legacy 目标权重与分别的执行约束/阻塞
- **输出指纹消费完整绑定语义**（两臂目标/阻塞/时点/版本全参与）——目标权重或
  阻塞变化必须留痕（V2）；仅捕获时点变化 → 真实重复折叠；WRITE 成功才回执 saved
- 报告分母按协议版本分列：v7_* 只数当期协议；旧记录（v6 及更早）单列 legacy_
  records 只诊断不追认（v6_* 按记录原样计数仅供过渡观察）
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# L0：v7 完整观察合同——统一资格函数（行情/证据时点、策略/规则/方法版本、两臂
# 完整进资格）+ 完整语义去重指纹（两臂目标/阻塞变化不吞）。旧 v5/v5_k0c/v6 记录
# 原样保留只诊断、不进当期分母、不追认（R11：v6 缺行情时点计有效是记录合同缺陷，
# 修正协议单独升版，不能事后追认）。
SHADOW_DERIVATION_VERSION = "shadow_v7"

SHADOW_STORE_PATH = Path.home() / ".muyun" / "shadow_diff.jsonl"

# 行1：已验证硬退出（证据支撑的强制退出通道——PlanGuard 不可压）
_HARD_EXIT_PATHS = {"fundamental_alert", "top_signal"}
# 行5：预设技术退出（MID）；flat_sell 是空仓标签不入表
_TECHNICAL_EXIT_PATHS = {"trend_exit", "take_profit_trim", "stop_loss_exit",
                         "stop_loss_trim", "time_stop", "weak_sell"}

# 差异原因标签（机器可分组；报告按此聚合——DESIGN：差异报告标记原因）
REASON_HARD_EXIT = "hard_exit_divergence"      # fusion 要求退出而 legacy 未退出
REASON_THESIS = "thesis_unestablished"         # fusion 无投资逻辑记录 → REVIEW/WAIT
REASON_RESEARCH = "research_gap"               # 证据缺口/分歧 → 冻结新增风险
REASON_ACTION_FLIP = "action_flip"             # 动作族翻转（非硬退出/研究差异所致）
REASON_PLAN_DRAFT = "plan_draft_not_activated"  # 用户计划未激活 → 只产出复核（行0 激活门）
REASON_FACTS_UNVERIFIED = "facts_unverified"   # 已激活计划的事实无可解析证据引用（R0 资格门）
REASON_AGREE = "agree"                         # 动作族一致


class ShadowDiffRecord(BaseModel):
    """一条影子对照记录（追加式 JSONL，不 prune）。"""

    security_id: str
    as_of: str = Field(description="捕获时点（带时区 ISO）")
    source: str = Field(default="", description="触发来源 l/la/chat")
    legacy_action: str = Field(description="legacy 终态 position_action")
    legacy_desired: str = Field(description="legacy 终态 desired_action（packet 口径）")
    fusion_mid_action: str = Field(description="fusion_mid_v1 决策表动作")
    fusion_long_action: str = Field(description="fusion_long_v1 决策表动作")
    fusion_mid_reason: str = Field(default="", description="MID 决策表行 reason（首条）")
    fusion_long_reason: str = Field(default="", description="LONG 决策表行 reason（首条）")
    sell_path: Optional[str] = None
    hard_exit: bool = False
    technical_exit: bool = False
    research_status: str = ""
    mid_plan_source: str = Field(default="simulated",
                                 description="MID 周期来源：simulated / user_plan_draft / user_plan_accepted")
    long_plan_source: str = Field(default="simulated",
                                  description="LONG 周期来源：simulated / user_plan_draft / user_plan_accepted")
    mid_thesis_status: str = Field(default="UNESTABLISHED", description="MID 周期逻辑状态（评估唯一真值）")
    long_thesis_status: str = Field(default="UNESTABLISHED", description="LONG 周期逻辑状态（评估唯一真值）")
    # J5 收口：v2 兼容聚合字段 thesis_status/plan_source 已删除——分周期字段
    # （mid/long_plan_source、mid/long_thesis_status）自 v3 起是唯一口径；
    # 旧 JSONL 记录的聚合键由报告读取适配（render 侧 fallback 保留）。
    # ── J4 有效观察字段（DELIVERY_PLAN：一条有效观察必须包含的绑定信息；
    # 旧记录/模拟/草稿/真实接受计划分桶——plan_source × observation_kind）──
    observation_kind: str = Field(
        default="diagnostic",
        description="none=无计划 / diagnostic=模拟或草稿（只诊断不进有效比较） / "
                    "effective=真实接受计划+评估可解析+账户版本齐——才进有效分母")
    mid_active_plan_revision: int = Field(default=0, description="MID 接受计划 revision（未接受=0）")
    long_active_plan_revision: int = Field(default=0, description="LONG 接受计划 revision（未接受=0）")
    mid_assessment_id: str = Field(default="", description="MID 评估唯一真值引用（错配/缺失=空——不冒充）")
    long_assessment_id: str = Field(default="", description="LONG 评估唯一真值引用")
    snapshot_id: str = Field(default="", description="计划所依据的证据快照 id（接受计划时）")
    account_version: str = Field(default="", description="账户账本内容版本（缺失=不进有效比较——J4 合同）")
    policy_version: str = Field(default="", description="计划生成方版本（J4 合同 policy 字段——去重拼接）")
    execution_status: str = Field(default="", description="legacy 执行层 effective_action（J4 合同「执行状态」）")
    execution_blocked: bool = Field(default=False, description="执行被阻（跌停/停牌等——J4 合同「阻塞」）")
    blocking_gates: list[str] = Field(default_factory=list, description="捕获时点融合模式未达的发布门")
    input_fingerprint: str = Field(default="", description="同输入指纹（计划/账户/评估/快照——同输入重复不是独立观察）")
    output_fingerprint: str = Field(
        default="", description="同输出指纹（K0c/A4：双方动作/理由/执行状态/thesis——同输入不同输出必须留痕）")
    append_status: str = Field(
        default="", description="落盘回执（K0c-3：saved=已写入 / deduped=重复去重——向调用方/诊断可见）")
    # ── K1 shadow_v6 → v7：MID/LONG 分别登记的完整绑定（分别判资格、分别计分母）──
    mid_binding: Optional[dict] = Field(
        default=None,
        description="MID 绑定 {horizon, plan_id, plan_revision, content_hash, "
                    "accepted_ref, active_ref, assessment_id, thesis_status, "
                    "snapshot_id, policy_id, decision_rule_version, method_version, "
                    "policy_version, arms{legacy,fusion}×{action,target,target_state,"
                    "blockers,execution}, evidence_cutoff, quote_cutoff, eligible, "
                    "drop_reasons}——None=无计划")
    long_binding: Optional[dict] = Field(
        default=None,
        description="LONG 绑定（结构同 mid_binding）——None=无计划")
    mid_effective: Optional[bool] = Field(
        default=None, description="MID 周期有效观察资格（独立判定——不搭另一周期的车）")
    long_effective: Optional[bool] = Field(
        default=None, description="LONG 周期有效观察资格（独立判定）")
    v6_drop_reasons: list[str] = Field(
        default_factory=list,
        description="v6 资格阻塞原因（机器可读：no_plan_long/no_accepted_ref_mid/"
                    "no_assessment_mid/no_account_version 等）——字段缺失按 diagnostic")
    delta_reasons: list[str] = Field(default_factory=list, description="差异原因标签（报告按此聚合）")
    derivation_version: str = SHADOW_DERIVATION_VERSION
    shadow_disclosure: str = "shadow 模拟计划（非用户确认，仅对照观察）"


def _load_capture_switch(config: Optional[dict]) -> Optional["FusionModeResolution"]:
    """读融合模式并判定是否捕获（J5 审查 P1-2：config=None 时 load_config 一次，
    开关判定与 requested/effective/blocking_gates **同源解析**——生产调用点不传
    config 时审计字段不再失真）。

    返回 FusionModeResolution；关闭（legacy_only）/读取失败 → None（不捕获）。"""
    if config is None:
        try:
            from src.cli.main import load_config
            config = load_config()
        except Exception as e:  # 配置读取失败按关闭处理（不捕获数据），并如实留痕
            logger.warning(f"影子捕获开关读取失败，本次不捕获（不影响分析）: {e}")
            return None
    res = resolve_fusion_mode_full(config)
    if res.parse_note:
        logger.info(f"fusion.mode={res.effective_mode}（{res.parse_note}）")
    if res.effective_mode == "legacy_only":
        return None
    return res


def _derive_facts(strategy_decision, packet) -> dict:
    """末端 legacy 结果 + 终态包 → HorizonFacts 字段（映射规则见模块 docstring v1）。"""
    sell_path = getattr(strategy_decision, "sell_path", None)
    pos_action = getattr(strategy_decision, "position_action", None)
    pos_action_val = getattr(pos_action, "value", pos_action)
    return {
        "hard_exit_triggered": sell_path in _HARD_EXIT_PATHS,
        "technical_exit_triggered": sell_path in _TECHNICAL_EXIT_PATHS,
        "entry_condition_met": pos_action_val in ("OPEN", "ADD"),
        "research_status": getattr(packet, "research_status", None),
        "thesis_status": None,  # v1 恒 UNESTABLISHED（不拿评分冒充逻辑）
        "budget_available": None,  # 组合预算未接——组合信息未知不给精确目标
    }


def _derive_shadow_plan(horizon, security_id: str, as_of: datetime,
                        trade_plan=None):
    """派生模拟 HorizonPlan（不落用户计划；模拟激活语义见模块 docstring）。"""
    from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID, HorizonPlan
    intent = "未登记投资逻辑（影子对照占位——等待 PlanV2 逻辑记录）"
    legacy_mode = None
    if trade_plan is not None:
        legacy_mode = getattr(trade_plan, "mode", None)
        tp_desc = getattr(trade_plan, "description", None) or ""
        if tp_desc:
            intent = f"由 legacy TradePlan 概述派生: {str(tp_desc)[:60]}"
    return HorizonPlan(
        plan_id=f"shadow_{security_id}_{str(horizon.value).lower()}",
        accepted_at=as_of.isoformat(timespec="seconds"),  # 模拟激活（越过行0；披露字段见下）
        horizon=horizon,
        policy_id=POLICY_ID_MID if horizon.value == "MID" else POLICY_ID_LONG,
        intent=intent,
        required_evidence_refs=[],
        invalidate_if=[],
        legacy_mode=legacy_mode,  # 仅对照字段，不参与 horizon 裁决
    )


def _load_verified_assessment(plan, horizon_value: str, security_id: str,
                              assessment_store=None):
    """按 J0b 口径加载并核对评估（J5 审查 P2-2：与 thesis 消费共用同一核对——
    security 非空且一致 / horizon / snapshot / method_version / 不在未来）。
    核对通过返回 ThesisAssessment；否则 None（调用方按各自语义降级：
    thesis→REVIEW_REQUIRED，有效观察→不登记引用）。"""
    aid = str(getattr(plan, "assessment_id", "") or "").strip()
    if not aid or ":" in aid or not aid.startswith("asm_"):
        return None  # 旧格式合成串（R3 草稿）不是评估库 id
    if assessment_store is None:
        from src.data.research_store import AssessmentStore
        assessment_store = AssessmentStore()
    asm = assessment_store.load(aid)
    if asm is None:
        return None
    if not str(asm.security_id or "").strip() or \
            (security_id and asm.security_id != security_id):
        return None  # 实体缺失/错配（空实体不冒充任意证券）
    if str(asm.horizon) != str(horizon_value):
        return None  # 周期错配
    plan_snap = str(getattr(plan, "snapshot_id", "") or "")
    asm_snap = str(asm.snapshot_id or "").strip()
    if not plan_snap or not asm_snap:
        # K0c/A2：计划与评估 snapshot **两侧非空且相等**才可消费——空值不作通配
        #（旧实现任一侧为空即跳过比较，让缺快照评估冒充消费资格）
        return None
    if plan_snap != asm_snap:
        return None  # 快照错配
    from src.core.research_service import ASSERTION_METHOD_VERSION
    if str(asm.method_version or "").strip() != ASSERTION_METHOD_VERSION:
        return None  # 方法版本不符——旧评估不冒充当前语义
    if asm.evaluated_as_of is not None and asm.evaluated_as_of > datetime.now().astimezone():
        return None  # 评估时点在未来——不可消费
    return asm


# ── L0/shadow_v7：统一资格函数与两臂目标状态 ──────────────────────────────

_ARM_FIELDS = ("action", "target", "target_state", "blockers", "execution")
_NO_POSITION_TARGET_ACTIONS = ("WAIT", "REVIEW")  # 不隐含数值目标仓位的动作族


def _target_state(target, action: str) -> str:
    """两臂目标状态（合同三值）：有数值=KNOWN；无数值且动作族不隐含目标=
    NOT_APPLICABLE（WAIT/REVIEW）；其余（HOLD/OPEN/ADD/REDUCE/EXIT 未给数值）
    =UNKNOWN——不拿 0 或墙钟凑 KNOWN。"""
    if target is not None:
        return "KNOWN"
    return "NOT_APPLICABLE" if action in _NO_POSITION_TARGET_ACTIONS else "UNKNOWN"


def _parse_cutoff(raw) -> Optional[datetime]:
    """时点解析：带时区 ISO → aware datetime；纯日期（YYYY-MM-DD）→ 当日（日精度，
    按交易所日期语义比较）；其余/不可解析 → None（资格函数按缺口处理）。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return dt


def _cutoff_in_future(raw, as_of: datetime) -> bool:
    """时点是否晚于捕获时点（未来时点不可比较——合同降级项）。
    纯日期按日期比较（日精度不假造时刻）；带时区按时刻比较；naive 带时刻串
    按日期比较（同日不误判未来——保守方向：宁漏判不冤判）。"""
    dt = _parse_cutoff(raw)
    if dt is None:
        return False
    if dt.tzinfo is None:
        return dt.date() > as_of.date()
    return dt > as_of


def _binding_eligibility(binding: Optional[dict], *, as_of: datetime) -> tuple[bool, list[str]]:
    """统一资格函数（L0 合同核心）：**一个函数**决定字段缺口、eligible/diagnostic
    与报告分母——绑定构造、记录判定、报告消费共用同一判据，不再各查各的子集
    （R11 V1 根因：eligible 只查 accepted/ref/aid/account_version 四项）。

    必要字段缺失 / 时点不可解析或在未来 / 版本缺失 / 两臂不完整 → (False, drops)。
    缺字段如实缺省先 diagnostic——不为凑有效样本伪造时点或版本。"""
    if not binding:
        return False, ["no_binding"]
    h = str(binding.get("horizon", "")).lower()
    drops: list[str] = []
    # 必要字段 → 缺失原因码（沿用 v6 既有约定：no_accepted_ref_X/no_assessment_X/
    # no_snapshot_X/no_account_version；L0 新增版本与时点码）
    for key, code in (
        ("accepted_ref", f"no_accepted_ref_{h}"),
        ("assessment_id", f"no_assessment_{h}"),
        ("snapshot_id", f"no_snapshot_{h}"),
        ("account_version", "no_account_version"),
        ("policy_version", f"no_policy_version_{h}"),
        ("method_version", f"no_method_version_{h}"),
        ("decision_rule_version", f"no_decision_rule_version_{h}"),
        ("evidence_cutoff", f"no_evidence_cutoff_{h}"),
        ("quote_cutoff", f"no_quote_cutoff_{h}"),
    ):
        if not binding.get(key):
            drops.append(code)
    # 时点可解析性与未来检查（证据截止来自被消费评估、行情时点来自实际行情——
    # 都不得晚于捕获时点；不可解析视同缺失）
    for key in ("evidence_cutoff", "quote_cutoff"):
        raw = binding.get(key)
        if not raw:
            continue  # 缺失已记
        if _parse_cutoff(raw) is None:
            drops.append(f"{key}_unparsable_{h}")
        elif _cutoff_in_future(raw, as_of):
            drops.append(f"{key}_future_{h}")
    arms = binding.get("arms") or {}
    for arm in ("legacy", "fusion"):
        a = arms.get(arm) or {}
        if not all(k in a for k in _ARM_FIELDS):
            drops.append(f"{arm}_arm_incomplete_{h}")
    # 去重保序
    seen: set[str] = set()
    ordered = [d for d in drops if not (d in seen or seen.add(d))]
    return (not ordered), ordered


def _thesis_status_for(plan, horizon_value: str, security_id: str,
                       assessment_store=None):
    """单周期逻辑状态（J0b v4：评估唯一真值——影子消费同一 Assessment，不再重判）。

    - plan 带 assessment_id → 核对（security/horizon/snapshot/method/时效）后消费
      **同一 status**；找不到、错配或过期 → REVIEW_REQUIRED（明确待复核），
      **绝不调用旧 facts+refs 简化判断救成 VALID**（N2 根因）。
      核对收紧（J0 审查 P2）：评估 security_id 为空、method_version 与当前
      ASSERTION_METHOD_VERSION 不符 → 一律待复核（空实体/旧方法不冒充当前语义）
    - 旧计划降级（J0 审查 P2 语义对齐）：assessment_id 缺失**或为旧格式**
      （`{horizon}:{snapshot_id}`——R3 旧草稿合成串，不是评估库 id）→ UNESTABLISHED
      （计划文字保留、资料不销毁；补一次正式评估即恢复真判断路径）
    - 无计划 → UNESTABLISHED（与原行为一致）"""
    from src.core.decision_contract import ThesisStatus
    if plan is None:
        return ThesisStatus.UNESTABLISHED
    aid = str(getattr(plan, "assessment_id", "") or "").strip()
    if not aid or ":" in aid or not aid.startswith("asm_"):
        # 无评估引用（或 R3 旧合成串格式——非评估库 id）→ 旧计划降级
        return ThesisStatus.UNESTABLISHED
    asm = _load_verified_assessment(plan, horizon_value, security_id, assessment_store)
    if asm is None:
        return ThesisStatus.REVIEW_REQUIRED  # 找不到/错配/过期——明确待复核（不救成 VALID）
    return asm.status


# 计划来源 → 人话（披露串与报告共用）
_SRC_CN = {
    "simulated": "模拟计划（非用户确认，仅对照观察）",
    "user_plan_draft": "草稿计划未激活——只产出复核",
    "user_plan_accepted": "用户已确认计划——真判断",
}


def _disclosure_for(mid_src: str, long_src: str) -> str:
    """披露串：两周期同源沿用原口径；混合来源逐周期明示（R0 验收2）。"""
    if mid_src == long_src:
        if mid_src == "simulated":
            return "shadow 模拟计划（非用户确认，仅对照观察）"
        return f"对照评估（{_SRC_CN[mid_src]}）"
    return f"分周期对照——MID={_SRC_CN.get(mid_src, mid_src)}；LONG={_SRC_CN.get(long_src, long_src)}"


def _classify_reasons(facts: dict, mid_packet, long_packet,
                      legacy_desired: str,
                      mid_plan_source: str = "simulated",
                      long_plan_source: str = "simulated") -> list[str]:
    """差异原因分类（优先级：硬退出 > 研究缺口 > 周期级逻辑/计划状态 > 动作翻转 > 一致）。

    v3 归因（R0 资格止血——逐周期感知 plan_source 与 thesis 状态）：
    - simulated：REVIEW/WAIT → thesis_unestablished（模拟无计划，如实）
    - user_plan_draft：REVIEW/WAIT → plan_draft_not_activated（行0 激活门，等用户确认）
    - user_plan_accepted：thesis UNESTABLISHED → facts_unverified（事实未挂证据引用，
      R0 资格门；否则不打逻辑类标签——已登记逻辑，REVIEW 多来自研究缺口，另计）
    """
    from src.core.decision_contract import ThesisStatus
    reasons: list[str] = []
    legacy_exit_family = legacy_desired in ("EXIT", "REDUCE")
    mid_exit_family = mid_packet.desired_action.value in ("EXIT", "REDUCE")
    long_exit_family = long_packet.desired_action.value in ("EXIT", "REDUCE")

    if facts["hard_exit_triggered"] and not legacy_exit_family:
        reasons.append(REASON_HARD_EXIT)
    research_status = getattr(facts["research_status"], "value", facts["research_status"])
    if research_status in ("INCOMPLETE", "CONFLICTED"):
        reasons.append(REASON_RESEARCH)
    for pkt, src in ((mid_packet, mid_plan_source), (long_packet, long_plan_source)):
        if pkt.desired_action.value not in ("REVIEW", "WAIT"):
            continue
        if src == "simulated":
            if REASON_THESIS not in reasons:
                reasons.append(REASON_THESIS)
        elif src == "user_plan_draft":
            if REASON_PLAN_DRAFT not in reasons:
                reasons.append(REASON_PLAN_DRAFT)
        elif (src == "user_plan_accepted"
              and pkt.thesis_status is ThesisStatus.UNESTABLISHED
              and REASON_FACTS_UNVERIFIED not in reasons):
            reasons.append(REASON_FACTS_UNVERIFIED)
    agreed = (legacy_exit_family == mid_exit_family == long_exit_family) or \
             (mid_packet.desired_action.value == legacy_desired and
              long_packet.desired_action.value == legacy_desired)
    if not reasons and agreed:
        reasons.append(REASON_AGREE)
    elif not reasons:
        reasons.append(REASON_ACTION_FLIP)
    return reasons


def capture_shadow(decision_result, strategy_decision, execution_eval, pos,
                   *, packet=None, source: str = "", config: Optional[dict] = None,
                   store_path: Optional[Path] = None,
                   plans_store=None, assessment_store=None,
                   account_version: str = "", quote_as_of: str = "") -> Optional[ShadowDiffRecord]:
    """一次持仓分析的影子对照捕获（纯读 + 追加一条 JSONL；异常如实告警不吞）。

    plans_store: HorizonPlanStore 注入（测试密闭用；None=读真实 ~/.muyun/horizon_plans.json）。
    assessment_store: AssessmentStore 注入（J0b v4——None=默认 ~/.muyun/research/assessments）。
    account_version: 账户账本内容版本（J4 有效观察合同——空=尝试读默认账本；
    无账户版本 → 观察只记 diagnostic，不进有效比较分母）。
    quote_as_of: 行情时点（v7 合同——实际行情抓取时刻或最近 K 线日期，来自
    StockData.quote_as_of；空=如实缺省 → 缺行情时点阻塞资格，只记 diagnostic）。"""
    if pos is None:
        return None
    mode_res = _load_capture_switch(config)
    if mode_res is None:
        return None

    if packet is None:
        from src.core.analysis_service import build_decision_packet
        packet = build_decision_packet(
            decision_result, strategy_decision, execution_eval,
            confirmed_ratio=getattr(pos, "current_ratio", None), source=source or "shadow")

    facts = _derive_facts(strategy_decision, packet)
    from src.core.decision_contract import (
        Horizon,
        ResearchStatus,
        ThesisStatus,
    )
    from src.core.decision_policy import HorizonFacts, evaluate_horizon

    as_of = datetime.now().astimezone()
    security_id = packet.security_id

    # v3：用户 plan2 计划优先（每股每周期一份——mid/long 可各立），来源逐周期标注。
    # 已激活计划走真决策表；草稿按行0 只复核；facts 只作用于其所在周期。
    # K0c-5：来源与有效观察按**精确接受引用**判定（is_accepted_version：
    # plan_id+revision+content_hash）——不再仅依 accepted_at 非空（计划被改后
    # accepted_at 仍在会冒充接受版本）。
    user_plans: dict = {}
    try:
        store = plans_store
        if store is None:
            from src.data.horizon_plans import HorizonPlanStore
            store = HorizonPlanStore()
        for h in ("MID", "LONG"):
            user_plans[h] = store.get(security_id, h)

        def _source_of(up) -> str:
            if up is None:
                return "simulated"
            try:
                if store.is_accepted_version(up):
                    return "user_plan_accepted"
            except Exception:
                pass
            return "user_plan_draft"
    except Exception as e:
        logger.debug(f"PlanV2 读取失败（按无计划处理）: {e}")
        user_plans = {"MID": None, "LONG": None}

        def _source_of(up) -> str:
            return "simulated" if up is None else "user_plan_draft"
    mid_src = _source_of(user_plans.get("MID"))
    long_src = _source_of(user_plans.get("LONG"))

    # J4 有效观察绑定信息：接受计划的 revision/评估引用/快照；账户版本缺失 →
    # 只记 diagnostic（不进有效比较分母——「缺账户…保留诊断但不进有效比较」）
    if not account_version:
        try:
            from src.data.account_service import DEFAULT_LEDGER_PATH, AccountService
            if DEFAULT_LEDGER_PATH.exists():
                account_version = AccountService(DEFAULT_LEDGER_PATH).account_version()
        except Exception as e:
            logger.debug(f"账户版本读取失败（观察按 diagnostic 记）: {e}")
    mid_rev = long_rev = 0
    mid_aid = long_aid = ""
    policy_versions: list[str] = []
    snap_ids: set[str] = set()
    for h, up in user_plans.items():
        # K0c-5：有效观察的登记以精确接受引用为前提（accepted_ref 三元组一致）
        if up is None or not _source_of(up) == "user_plan_accepted":
            continue
        pv = str(getattr(up, "policy_version", "") or "")
        if pv and pv not in policy_versions:
            policy_versions.append(pv)
        # 与 _thesis_status_for 同一核对（J5 审查 P2-2 口径对称）：
        # 核对通过才登记评估引用为「可解析」——否则 observation 降级
        asm = _load_verified_assessment(up, h, security_id, assessment_store)
        resolved = str(getattr(up, "assessment_id", "") or "") if asm is not None else ""
        if h == "MID":
            mid_rev, mid_aid = int(getattr(up, "revision", 0) or 0), resolved
        else:
            long_rev, long_aid = int(getattr(up, "revision", 0) or 0), resolved
        if getattr(up, "snapshot_id", ""):
            snap_ids.add(str(up.snapshot_id))
    has_any_plan = mid_src != "simulated" or long_src != "simulated"

    hf_kwargs = dict(
        research_status=facts["research_status"] or ResearchStatus.INCOMPLETE,
        hard_exit_triggered=facts["hard_exit_triggered"],
        hard_exit_detail=f"sell_path={getattr(strategy_decision, 'sell_path', None)}",
        technical_exit_triggered=facts["technical_exit_triggered"],
        entry_condition_met=facts["entry_condition_met"],
        budget_available=None,
    )

    horizon_packets: list = []
    confirmed_ratio = getattr(pos, "current_ratio", None)
    per_horizon_thesis: dict[str, str] = {}
    for horizon in (Horizon.MID, Horizon.LONG):
        up = user_plans.get(horizon.value)
        hf = dict(hf_kwargs)
        # 事实只作用于用户计划所在的周期——不把一份计划的逻辑事实泄漏到另一周期；
        # 状态消费评估唯一真值（J0b v4：assessment 引用——缺失/错配→待复核，不重判）
        thesis_status = _thesis_status_for(up, horizon.value, security_id,
                                           assessment_store=assessment_store)
        per_horizon_thesis[horizon.value] = thesis_status.value
        hf["thesis_status"] = thesis_status
        plan = up if up is not None else _derive_shadow_plan(
            horizon, security_id, as_of, trade_plan=getattr(pos, "trade_plan", None))
        horizon_packets.append(
            evaluate_horizon(plan, HorizonFacts(**hf), security_id,
                             confirmed_ratio=confirmed_ratio))
    mid_packet, long_packet = horizon_packets[0], horizon_packets[1]

    legacy_desired = packet.desired_action.value
    delta_reasons = _classify_reasons(facts, mid_packet, long_packet, legacy_desired,
                                      mid_plan_source=mid_src, long_plan_source=long_src)
    legacy_pos_action = getattr(strategy_decision, "position_action", None)
    legacy_pos_action_val = getattr(legacy_pos_action, "value", legacy_pos_action) or ""

    # ── shadow_v7：MID/LONG 分别登记完整绑定（统一资格函数判资格、分别计分母）──
    def _build_binding(h, up, pkt):
        """单周期绑定组（v7 合同）：plan/accepted_ref/assessment/policy/method/
        规则版本/两臂（action/target/target_state/blockers/execution）/时点。
        eligible 与 drop_reasons 由统一资格函数 `_binding_eligibility` 判定——
        字段缺失、时点在未来、版本缺失一律降级 diagnostic，不冒充合格。

        字段真实来源（L0，R11 V1）：
        - quote_cutoff ← 实际行情（调用方注入 StockData.quote_as_of）——不可得如实
          None + 阻塞资格，不拿捕获墙钟凑数
        - evidence_cutoff ← 被消费评估的 evaluated_as_of（核对通过的 asm）——
          不再用捕获墙钟 as_of 冒充证据截止
        - decision_rule_version ← 真实规则版本常量（policy_id@DECISION_TABLE_VERSION）"""
        if up is None:
            return None, [f"no_plan_{h.lower()}"]
        accepted = _source_of(up) == "user_plan_accepted"
        ref = None
        if accepted:
            try:
                ref = store.get_accepted_ref(security_id, h)
            except Exception:
                ref = None  # K1 审查 P2-1：接受态但绑定缺失——不得以空充资格
        try:
            active_ref = store.get_active_ref(security_id)
        except Exception:
            active_ref = None
        asm = _load_verified_assessment(up, h, security_id, assessment_store)
        aid = str(getattr(up, "assessment_id", "") or "") if asm is not None else ""
        quote_cutoff = str(quote_as_of or "").strip() or None
        evidence_cutoff = None
        if asm is not None and asm.evaluated_as_of is not None:
            evidence_cutoff = asm.evaluated_as_of.isoformat(timespec="seconds")
        from src.core.decision_policy import DECISION_TABLE_VERSION
        policy_id = str(getattr(pkt, "policy_id", "") or "")
        legacy_target = getattr(strategy_decision, "position_ratio", None)
        legacy_blockers: list[str] = []
        if bool(getattr(execution_eval, "blocked", False)):
            legacy_blockers.append(str(getattr(execution_eval, "block_reason", "") or "执行被阻"))
        binding = {
            "horizon": h,
            "plan_id": getattr(up, "plan_id", ""),
            "plan_revision": int(getattr(up, "revision", 0) or 0),
            "content_hash": up.content_hash(),
            "accepted_ref": ref,
            "active_ref": active_ref,  # K1 审查 P1-1：主意图引用（README 冻结候选清单）
            "assessment_id": aid,
            "thesis_status": per_horizon_thesis.get(h, ""),
            "snapshot_id": str(getattr(up, "snapshot_id", "") or ""),
            "account_version": str(account_version or ""),
            "policy_id": policy_id,
            "decision_rule_version": f"{policy_id}@{DECISION_TABLE_VERSION}",
            "method_version": str(getattr(asm, "method_version", "") or "") if asm else "",
            "policy_version": str(getattr(up, "policy_version", "") or ""),
            # 两臂完整输出（R11 V1：补 legacy 目标权重与分别的执行约束/阻塞）
            "arms": {
                "legacy": {
                    "action": legacy_desired,
                    "target": legacy_target,
                    "target_state": _target_state(legacy_target, legacy_desired),
                    "blockers": legacy_blockers,
                    "execution": str(getattr(getattr(execution_eval, "effective_action",
                                                     None), "value", "") or ""),
                },
                "fusion": {
                    "action": pkt.desired_action.value,
                    "target": getattr(pkt, "target_weight", None),
                    "target_state": _target_state(getattr(pkt, "target_weight", None),
                                                  pkt.desired_action.value),
                    "blockers": [str(getattr(b, "detail", b)) for b in (pkt.blockers or [])],
                    "execution": str(getattr(getattr(pkt, "execution_status", None),
                                             "value", "") or ""),
                },
            },
            "evidence_cutoff": evidence_cutoff,
            "quote_cutoff": quote_cutoff,
        }
        eligible, drops = _binding_eligibility(binding, as_of=as_of)
        binding["eligible"] = eligible
        binding["drop_reasons"] = drops
        return binding, drops

    mid_binding, mid_drops = _build_binding("MID", user_plans.get("MID"), mid_packet)
    long_binding, long_drops = _build_binding("LONG", user_plans.get("LONG"), long_packet)
    mid_effective = bool(mid_binding and mid_binding["eligible"])
    long_effective = bool(long_binding and long_binding["eligible"])
    v6_drop_reasons = mid_drops + long_drops
    # 行级 observation_kind 也走同一资格结果（J4 语义：任一周期合格才算行级有效）
    observation_kind = ("effective" if (mid_effective or long_effective)
                        else ("diagnostic" if has_any_plan else "none"))

    record = ShadowDiffRecord(
        security_id=security_id,
        as_of=as_of.isoformat(timespec="seconds"),
        source=source,
        legacy_action=str(legacy_pos_action_val),
        legacy_desired=legacy_desired,
        fusion_mid_action=mid_packet.desired_action.value,
        fusion_long_action=long_packet.desired_action.value,
        fusion_mid_reason=(mid_packet.reason_codes[0] if mid_packet.reason_codes else ""),
        fusion_long_reason=(long_packet.reason_codes[0] if long_packet.reason_codes else ""),
        sell_path=getattr(strategy_decision, "sell_path", None),
        hard_exit=facts["hard_exit_triggered"],
        technical_exit=facts["technical_exit_triggered"],
        research_status=getattr(facts["research_status"], "value", "") if facts["research_status"] else "",
        mid_plan_source=mid_src,
        long_plan_source=long_src,
        mid_thesis_status=per_horizon_thesis["MID"],
        long_thesis_status=per_horizon_thesis["LONG"],
        observation_kind=observation_kind,
        mid_active_plan_revision=mid_rev,
        long_active_plan_revision=long_rev,
        mid_assessment_id=mid_aid,
        long_assessment_id=long_aid,
        snapshot_id=(";".join(sorted(snap_ids)) if snap_ids else ""),
        account_version=account_version,
        policy_version=";".join(policy_versions),
        execution_status=str(getattr(getattr(execution_eval, "effective_action", None),
                                     "value", "") or ""),
        execution_blocked=bool(getattr(execution_eval, "blocked", False)),
        blocking_gates=list(mode_res.blocking_gates),
        mid_binding=mid_binding, long_binding=long_binding,
        mid_effective=mid_effective, long_effective=long_effective,
        v6_drop_reasons=v6_drop_reasons,
        input_fingerprint=_input_fingerprint(security_id, user_plans, account_version,
                                             snap_ids=snap_ids,
                                             policy_versions=policy_versions,
                                             assessment_ids={"MID": mid_aid,
                                                             "LONG": long_aid}),
        shadow_disclosure=_disclosure_for(mid_src, long_src),
        delta_reasons=delta_reasons,
    )
    store = Path(store_path) if store_path else SHADOW_STORE_PATH
    _append_record(store, record)
    if record.append_status == "deduped":
        logger.info(f"影子记录重复触发去重（{security_id}，输入/输出指纹均同）——"
                    "不重复计观察")
    return record


def _input_fingerprint(security_id: str, user_plans: dict, account_version: str,
                       snap_ids: Optional[set] = None,
                       policy_versions: Optional[list] = None,
                       assessment_ids: Optional[dict] = None) -> str:
    """同输入指纹（K0c/A3 扩展业务字段）：证券+账户版本+两周期计划内容 hash+
    证据快照 id+计划策略版本+评估引用——同输入重复不是独立观察。"""
    import hashlib as _h
    parts = [str(security_id), str(account_version or "")]
    for h in ("MID", "LONG"):
        up = user_plans.get(h)
        parts.append(up.content_hash() if up is not None else "-")
    parts.append(";".join(sorted(snap_ids or set())))
    parts.append(";".join(policy_versions or []))
    parts.append(";".join(f"{k}:{v}" for k, v in sorted((assessment_ids or {}).items())))
    return _h.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# 输出指纹排除项（v7/V2：其余全部语义字段参与——两臂绑定/目标/阻塞/时点/版本/
# 资格结果一个不少；排除的仅是非语义字段）
_OUTPUT_FP_EXCLUDED = frozenset({
    "as_of",               # 捕获墙钟——时点单列，不是观察语义（V1）
    "input_fingerprint",   # 输入侧指纹独立比较（K0c/A3 分工）
    "output_fingerprint",  # 自身
    "append_status",       # 落盘回执
    "source",              # 触发入口（l/la/chat）——触发方式不是决策语义
    "shadow_disclosure",   # 渲染串（由来源派生）
})


def _output_fingerprint(record: ShadowDiffRecord) -> str:
    """同输出指纹（v7/V2 重写）：对记录做**完整语义投影**后规范序列化 hash——
    旧实现手工列举字段子集，漏掉绑定目标权重/阻塞（R11 V2：目标变化被去重吞掉）。
    现在除捕获墙钟与回执/入口等非语义字段外全部参与：绑定两臂的目标、阻塞、
    时点、版本变化都改变指纹——同输入不同输出必须留痕；仅捕获时点变化 → 指纹
    不变（真实重复才可折叠）。含 derivation_version：跨协议版本永不互判重复。"""
    import hashlib as _h
    d = record.model_dump(mode="json")
    for k in _OUTPUT_FP_EXCLUDED:
        d.pop(k, None)
    canon = json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
    return _h.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _append_record(store: Path, record: ShadowDiffRecord) -> bool:
    """追加一条记录；K0c/A3/A4 去重规则：**只折叠同一完整输入且同一输出的重复触发**——
    - 同输入+同输出：同分钟幂等、同日跨分钟亦幂等（同输入重复不是独立观察——J4 继承）
    - 同分钟但输入或输出任一变化 → 留痕（计划/账户版本变更、决策结果翻转不丢——A3/A4）
    - 旧记录无输出指纹（v5 时代）→ 无法认定同输出 → 保守留痕（不吞新观察）
    返回是否实际写入；record.append_status 向调用方回执（saved/deduped）。"""
    store.parent.mkdir(parents=True, exist_ok=True)
    minute_key = (record.security_id, record.as_of[:16])
    day = record.as_of[:10]
    record.output_fingerprint = record.output_fingerprint or _output_fingerprint(record)
    if store.exists():
        with store.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行隔离（G14：不因坏行丢弃既有记录）
                if old.get("security_id") != record.security_id:
                    continue
                same_fp = (record.input_fingerprint and
                           old.get("input_fingerprint") == record.input_fingerprint)
                same_out = (record.output_fingerprint and
                            old.get("output_fingerprint") == record.output_fingerprint)
                same_minute = (str(old.get("as_of", ""))[:16]) == minute_key[1]
                if same_minute and same_fp and same_out:
                    record.append_status = "deduped"
                    return False
                if not same_minute and same_fp and same_out \
                        and str(old.get("as_of", ""))[:10] == day:
                    record.append_status = "deduped"
                    return False
    with store.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    # L0：写盘成功才回执 saved（WRITE 失败回执不算观察——异常向上抛，调用方
    # 如实告警；不出现「回了执但没落盘」的假观察）
    record.append_status = "saved"
    return True


def build_shadow_report(store_path: Optional[Path] = None, days: int = 7) -> dict:
    """影子差异聚合（近 N 天）——分原因计数与差异率，不比较总收益（DESIGN 硬要求）。"""
    store = Path(store_path) if store_path else SHADOW_STORE_PATH
    if not store.exists():
        return {"total": 0, "stocks": 0, "since": None, "by_reason": {},
                "records": [], "store": str(store)}
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    by_reason: dict[str, int] = {}
    records = []
    stocks: set[str] = set()
    with store.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("as_of", "")) < cutoff:
                continue
            records.append(r)
            stocks.add(r.get("security_id", ""))
            for tag in r.get("delta_reasons", []) or []:
                by_reason[tag] = by_reason.get(tag, 0) + 1
    total = len(records)
    non_agree = sum(1 for r in records if REASON_AGREE not in (r.get("delta_reasons") or []))
    # J4：行级有效观察单独计数（真实接受计划+评估可解析+账户版本齐才进有效分母；
    # 0 有效样本 = 尚无证据——不宣称稳定性）。v7 起只数**当期协议**记录——
    # 旧协议记录的 effective 标签产生于旧合同（缺行情时点仍计有效），不追认。
    current_version = SHADOW_DERIVATION_VERSION
    cur_records = [r for r in records if r.get("derivation_version") == current_version]
    effective_n = sum(1 for r in cur_records if r.get("observation_kind") == "effective")
    # ── shadow_v7：当期协议分别计分母 + 旧协议记录只诊断 ──
    cur_mid_effective = sum(1 for r in cur_records if r.get("mid_effective") is True)
    cur_long_effective = sum(1 for r in cur_records if r.get("long_effective") is True)
    cur_diagnostic = sum(1 for r in cur_records
                         if r.get("mid_effective") is not True
                         and r.get("long_effective") is not True)
    cur_drop_counts: dict = {}
    for r in cur_records:
        # K1 审查 P2-3：按记录去重（同一阻塞在 MID/LONG 双 binding 各记一次，
        # 计数按记录不按周期双倍）
        for dr in sorted(set(r.get("v6_drop_reasons") or [])):
            cur_drop_counts[dr] = cur_drop_counts.get(dr, 0) + 1
    legacy_records = total - len(cur_records)
    # 旧 v6 协议记录：按记录原样计数（仅供过渡观察——只诊断不追认，
    # 不进当期分母；现行资格一律以 cur_* 为准）
    v6_records = [r for r in records if r.get("derivation_version") == "shadow_v6"]
    v6_mid_effective = sum(1 for r in v6_records if r.get("mid_effective") is True)
    v6_long_effective = sum(1 for r in v6_records if r.get("long_effective") is True)
    v6_diagnostic = sum(1 for r in v6_records
                        if r.get("mid_effective") is not True
                        and r.get("long_effective") is not True)
    return {
        "total": total,
        "stocks": len(stocks),
        "since": cutoff,
        "by_reason": by_reason,
        "diff_rate": (non_agree / total) if total else 0.0,
        "effective_observations": effective_n,
        "protocol_version": current_version,
        "v7_mid_effective": cur_mid_effective,
        "v7_long_effective": cur_long_effective,
        "v7_diagnostic": cur_diagnostic,
        "v7_drop_counts": cur_drop_counts,
        "legacy_records": legacy_records,
        "v6_mid_effective": v6_mid_effective,
        "v6_long_effective": v6_long_effective,
        "v6_diagnostic": v6_diagnostic,
        "records": records[-30:],
        "store": str(store),
    }


_REASON_CN = {
    REASON_HARD_EXIT: "硬退出分歧（fusion 要求退出而 legacy 未退出）",
    REASON_THESIS: "fusion 无投资逻辑记录 → REVIEW/WAIT",
    REASON_PLAN_DRAFT: "计划已立但未激活 → 只产出复核（plan2 accept 激活）",
    REASON_FACTS_UNVERIFIED: "已激活计划的事实未挂证据引用——按「逻辑未立」处理（R0 资格门）",
    REASON_RESEARCH: "证据缺口/分歧 → 冻结新增风险",
    REASON_ACTION_FLIP: "动作族翻转",
    REASON_AGREE: "动作一致",
}


_ACTION_CN = {
    "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓", "EXIT": "清仓退出",
    "HOLD": "继续持有", "WAIT": "等条件", "REVIEW": "需人工复核",
}


def _action_cn(action: str) -> str:
    """动作英文枚举 → 人话（ISS-106：用户走查——报告不能全是英文）。"""
    return _ACTION_CN.get(action, action)


def render_shadow_report(report: dict) -> str:
    """报告渲染（rich markup，供 console.print；定位说明前置——ISS-106）。"""
    if not report["total"]:
        return (
            "[bold cyan]🔍 影子对照报告[/bold cyan]\n"
            "  [dim]这是干什么的：你分析持仓时，系统会用一套还在试验的新决策思路把这只股"
            "也评一遍，把两边的差异记下来——你的分析结论一个字都不会变。攒几周后看新思路"
            "和旧思路常在哪里意见不一致，用真实使用数据决定它能不能转正。[/dim]\n"
            f"  近期无影子记录（捕获开关 fusion.shadow_capture，账本 {report['store']}）\n"
            "  持仓股经 l/la/chat 分析时自动累积对比，无需手动触发"
        )
    lines = ["[bold cyan]🔍 影子对照报告[/bold cyan]",
             "  [dim]shadow 模拟计划（非用户确认，仅对照观察）——不改变任何主结论[/dim]"]
    # 来源**逐周期**计数（R0 验收2：仅 MID 接受时 LONG 计入模拟，不再整行聚合；
    # 旧 v2 记录无分周期字段时回退整行 plan_source）
    def _src(r: dict, horizon: str) -> str:
        v = r.get(f"{horizon}_plan_source")
        return v if v in _SRC_CN else (r.get("plan_source") or "simulated")
    records = report["records"]
    cnt = {h: {"user_plan_accepted": 0, "user_plan_draft": 0, "simulated": 0}
           for h in ("mid", "long")}
    for r in records:
        for h in ("mid", "long"):
            cnt[h][_src(r, h)] += 1
    src_parts = []
    if cnt["mid"]["user_plan_accepted"] or cnt["long"]["user_plan_accepted"]:
        src_parts.append(f"真计划对照 MID {cnt['mid']['user_plan_accepted']} 条｜"
                         f"LONG {cnt['long']['user_plan_accepted']} 条"
                         "（你的 plan2 已激活——真判断）")
    if cnt["mid"]["user_plan_draft"] or cnt["long"]["user_plan_draft"]:
        src_parts.append(f"草稿计划 MID {cnt['mid']['user_plan_draft']} 条｜"
                         f"LONG {cnt['long']['user_plan_draft']} 条"
                         "（plan2 accept 后出真判断）")
    if cnt["mid"]["simulated"] or cnt["long"]["simulated"]:
        src_parts.append(f"模拟计划 MID {cnt['mid']['simulated']} 条｜"
                         f"LONG {cnt['long']['simulated']} 条")
    if src_parts:
        lines.append("  记录来源: " + "｜".join(src_parts))
    lines.append(f"  近 7 天捕获 {report['total']} 条（{report['stocks']} 只持仓）"
                 f"｜差异率 {report['diff_rate']:.0%}（非一致占比）")
    # shadow_v7：分周期有效/诊断计数 + 具体缺口（L0 验收：UI 显示具体缺口和
    # 两周期数——旧版本记录只诊断不追认）
    cur_mid = report.get("v7_mid_effective", 0)
    cur_long = report.get("v7_long_effective", 0)
    cur_diag = report.get("v7_diagnostic", 0)
    legacy_n = report.get("legacy_records", 0)
    protocol = report.get("protocol_version", SHADOW_DERIVATION_VERSION)
    lines.append(f"  📐 {protocol} 协议：有效 MID {cur_mid}｜LONG {cur_long}（分别计分母）"
                 f"｜诊断 {cur_diag}｜旧版本记录 {legacy_n} 条（只诊断不追认）")
    if report.get("v7_drop_counts"):
        top_drops = sorted(report["v7_drop_counts"].items(), key=lambda kv: -kv[1])[:3]
        drop_cn = {"no_plan": "无计划", "no_accepted_ref": "未接受",
                   "no_assessment": "缺评估", "no_account_version": "缺账户版本",
                   "no_snapshot": "缺证据快照", "no_policy_version": "缺策略版本",
                   "no_method_version": "缺方法版本", "no_decision_rule_version": "缺规则版本",
                   "no_evidence_cutoff": "缺证据截止", "no_quote_cutoff": "缺行情时点",
                   "evidence_cutoff_future": "证据截止在未来",
                   "quote_cutoff_future": "行情时点在未来",
                   "evidence_cutoff_unparsable": "证据截止不可解析",
                   "quote_cutoff_unparsable": "行情时点不可解析",
                   "legacy_arm_incomplete": "两臂字段不全(legacy)",
                   "fusion_arm_incomplete": "两臂字段不全(fusion)",
                   "no_binding": "无绑定"}
        drops_txt = "、".join(f"{drop_cn.get(k.split('_mid')[0].split('_long')[0], k)}"
                              f" {n}" for k, n in top_drops)
        lines.append(f"  [dim]缺口原因（近 7 天）：{drops_txt}[/dim]")
    eff = report.get("effective_observations", 0)
    if eff:
        lines.append(f"  ✅ 有效观察 {eff} 条（真实接受计划+评估可解析+账户版本齐——进有效比较）")
    else:
        lines.append("  有效观察 0 条——尚无证据（有效样本=真实接受计划+评估可解析+账户版本齐；"
                     "模拟/草稿只作诊断，不宣称稳定性）")
    reason_labels = {"hard_exit_divergence": _REASON_CN[REASON_HARD_EXIT],
                     "thesis_unestablished": _REASON_CN[REASON_THESIS],
                     "plan_draft_not_activated": _REASON_CN[REASON_PLAN_DRAFT],
                     "facts_unverified": _REASON_CN[REASON_FACTS_UNVERIFIED],
                     "research_gap": _REASON_CN[REASON_RESEARCH],
                     "action_flip": _REASON_CN[REASON_ACTION_FLIP],
                     "agree": _REASON_CN[REASON_AGREE]}
    for tag, label in reason_labels.items():
        n = report["by_reason"].get(tag, 0)
        if n:
            lines.append(f"  · {label}: {n}")
    lines.append("  最近记录（旧方法 → 新思路中期/长期 ｜ 差异原因）:")
    for r in report["records"][-8:]:
        tags = "、".join(_REASON_CN.get(t, t) for t in (r.get("delta_reasons") or []))
        lines.append(
            f"   {r['as_of'][:16]} {r['security_id']} "
            f"{_action_cn(r['legacy_desired'])} → "
            f"{_action_cn(r['fusion_mid_action'])}/{_action_cn(r['fusion_long_action'])}"
            f"  ｜ {tags}")
    lines.append(f"  [dim]账本: {report['store']}（追加式）｜ 映射版本 {SHADOW_DERIVATION_VERSION}[/dim]")
    return "\n".join(lines)


# ──────────────── R9/J4：fusion.mode 单一解析器（requested/effective 拆分）────────────────

FUSION_MODES = ("legacy_only", "capture_only", "shadow", "opt_in", "default")

# 发布门（J4：requested 不等于 effective——未过门一律按 capture_only 执行）
_MODE_BLOCKING_GATES = (
    "J0–J3 场景与真实入口验收",
    "配对影子有效观察证据（J4 观察协议）",
    "独立验收（J5）",
)


class FusionModeResolution(BaseModel):
    """融合模式解析结果（J4：所有消费者只据 effective 执行）。"""
    model_config = ConfigDict(extra="allow")

    requested_mode: str = Field(description="配置声明的意愿（含迁移缺省）——不是执行依据")
    effective_mode: str = Field(description="实际执行模式——发布门未过一律 capture_only")
    blocking_gates: list[str] = Field(default_factory=list, description="requested≠effective 时未达的门")
    parse_note: str = Field(default="", description="解析期注记（旧开关迁移/未知回退/缺省）")


def resolve_fusion_mode_full(config: Optional[dict]) -> FusionModeResolution:
    """解析融合模式（单一入口；迁移旧开关 fusion.shadow_capture，不并存两套语义）。

    - legacy_only：融合层完全不运行（requested=effective）
    - capture_only：影子捕获采集数据（当前缺省）
    - shadow：捕获 + 影子决策表对照输出（与 capture_only 同实现——显式命名阶段）
    - opt_in / default：**意愿登记不等于生效**——发布门未过（J4 观察证据/J5 独立
      验收），effective 一律 capture_only，blocking_gates 明示
    """
    fusion = (config or {}).get("fusion") or {}
    mode = fusion.get("mode")
    requested = ""
    parse_note = ""
    if mode:
        mode = str(mode).strip().lower()
        if mode in FUSION_MODES:
            requested = mode
        else:
            logger.warning(f"未知 fusion.mode={mode!r}——回退 capture_only（合法值: {'/'.join(FUSION_MODES)}）")
            requested = "capture_only"
            parse_note = "未知 mode 回退"
    elif "shadow_capture" in fusion:
        requested = "capture_only" if fusion.get("shadow_capture") else "legacy_only"
        parse_note = "经旧开关 fusion.shadow_capture 迁移"
    else:
        requested = "capture_only"
        parse_note = "缺省（捕获语义不改主结论）"
    if requested in ("opt_in", "default"):
        return FusionModeResolution(requested_mode=requested, effective_mode="capture_only",
                                    blocking_gates=list(_MODE_BLOCKING_GATES),
                                    parse_note=parse_note)
    return FusionModeResolution(requested_mode=requested, effective_mode=requested,
                                blocking_gates=[], parse_note=parse_note)


def resolve_fusion_mode(config: Optional[dict]) -> tuple[str, str]:
    """兼容适配（旧调用点/测试）：返回 (effective_mode, note)——**消费者只据 effective**。

    requested/effective/blocking_gates 全量解析见 resolve_fusion_mode_full。"""
    res = resolve_fusion_mode_full(config)
    if res.effective_mode != res.requested_mode:
        return res.effective_mode, (f"requested={res.requested_mode} 未达发布门"
                                    "（意愿登记不等于生效）——按 capture_only 执行")
    return res.effective_mode, res.parse_note
