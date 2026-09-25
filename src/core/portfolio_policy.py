"""组合预算统一求解（plan/fusion F7，DESIGN ADR-F07）：禁止逐股各自满额。

DESIGN ADR-F07 原文约束：
- 预算提案采用**确定性约束分配**，首版不做收益协方差优化
- add_budget = max(0, min(计划目标-w, 个股上限-w, 周期总预算-周期暴露, 行业/主题上限
  -组暴露, 扣费后可用现金/NAV, 允许交易额/NAV, 组合剩余压力损失预算/压力损失率))
  ——该式仅分配新增风险；如已有暴露越限，由减仓策略生成动作，**不能把负新增额度
  直接解释成已成交减仓**。
  ⚠️ 八项公式中的"个股损失预算/压力损失率-w"单股维度暂缺（需用户风险档的个股
  损失预算字段——F7 审查 P2-1 登记，随用户风险档持久化接线补齐）；
- 求解顺序：先识别必须退出/减仓的风险，再分配新增；**只有已确认卖出或情景中明确
  可实现的现金才能用于新增**（卖不出不得提前花卖出现钱——G09/G11）
- 按"风险紧迫度→计划内到期→合格新增"的稳定排序分配；同级使用明示因子排序与
  security_id 稳定打破平局，不能 BUY/SELL 混在一张强度排行榜
- 相关主题（同一产业链多个概念名）聚合风险，不因名称不同当分散
- 排列不变：批量输入排列变化不改变结果（G09）
- 比例以账户净资产为分母；压力损失率未知/非正数时不给此模型的精确仓位
- 增加一只风险更高或证据更差的候选不得自动增加总预算

纯函数模块：零网络、零 AI、不写持仓（产出 BudgetSolution 由调用方消费/展示）。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

BUDGET_SOLVER_VERSION = "f7.v1"


class HoldingWeight(BaseModel):
    """已确认持仓权重（事实，来自 PortfolioManager——F1 语义）。"""

    stock_code: str
    weight: float = Field(ge=0.0, le=1.0, description="占账户净资产比例 0-1")
    industries: list[str] = Field(default_factory=list, description="行业/主题标签（同链多标签在此聚合）")
    horizon: str = Field(default="LEGACY", description="LEGACY/MID/LONG")


class AddProposal(BaseModel):
    """一条新增提案（来自 DecisionPacket desired=OPEN/ADD，或候选池）。"""

    stock_code: str
    stock_name: str = ""
    target_weight: float = Field(ge=0.0, le=1.0, description="计划目标权重（账户净资产分母）")
    horizon: str = Field(default="MID")
    industries: list[str] = Field(default_factory=list)
    pressure_loss_rate: Optional[float] = Field(
        default=None, ge=0.0, description="该股压力情景损失率（如止损宽度+跳空裕度）；未知=不给精确额度")
    risk_urgency: int = Field(default=0, description="风险紧迫度（0 普通；越高越优先——如硬风险临近的补足）")
    plan_due: Optional[str] = Field(default=None, description="计划内到期（ISO 日期；越早越优先）")
    score: float = Field(default=0.0, allow_inf_nan=False,
                         description="同级排序明示因子（如研究质量分；不与 BUY/SELL 混排）")
    evidence_grade: int = Field(default=0, description="证据等级（越高越好；用于同级排序）")


class BudgetConstraints(BaseModel):
    """账户级约束（用户风险档——只读取用户明确设置的值；未设置项 None=不启用该约束）。

    extra="forbid"：约束名拼错（如 per_stock_caps）当场报错——静默接受=约束悄悄放松
    （F7 审查 P2-5）。"""

    model_config = ConfigDict(extra="forbid")

    per_stock_max: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="单股权重上限")
    horizon_budgets: dict = Field(default_factory=dict, description="{horizon: 权重上限}")
    industry_max: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="单行业/主题暴露上限")
    cash_nav: Optional[float] = Field(default=None, ge=0.0, description="扣费后可用现金/NAV")
    tradeable_nav: Optional[float] = Field(default=None, ge=0.0, description="允许交易金额/NAV（容量约束）")
    portfolio_loss_budget: Optional[float] = Field(default=None, ge=0.0, description="组合剩余压力损失预算（占NAV）")
    max_adds: Optional[int] = Field(default=None, description="本轮最多新增标的数（研究预算）")


class BudgetLine(BaseModel):
    """一条求解结果（新增额度；不是成交指令）。"""

    stock_code: str
    stock_name: str = ""
    current_weight: float
    add_weight: float = Field(ge=0.0)
    target_weight: float
    horizon: str
    trimmed_to: str = Field(default="", description="被哪个约束截断（人话：cash/per_stock_max/industry/loss_budget/horizon）")
    reason: str = ""

    @property
    def feasible(self) -> bool:
        return self.add_weight > 1e-9


class RejectedAdd(BaseModel):
    """被拒/搁置的新增提案（拒绝原因明确——G09）。"""

    stock_code: str
    stock_name: str = ""
    reason: str


class BudgetSolution(BaseModel):
    """组合预算求解结果（排列无关；同一输入重放一致）。"""

    solver_version: str = BUDGET_SOLVER_VERSION
    exits_first: list[str] = Field(default_factory=list, description="必须退出/减仓的风险单（先于新增识别）")
    sell_cash_available_nav: float = Field(default=0.0, description="可用于新增的已确认卖出现金/NAV（未确认卖出不计入）")
    adds: list[BudgetLine] = Field(default_factory=list)
    rejected: list[RejectedAdd] = Field(default_factory=list)
    committed_industry_exposure: dict = Field(default_factory=dict, description="求解后各行业/主题暴露（聚合口径）")
    fingerprint: str = ""

    def fingerprint_of(self) -> str:
        import hashlib
        import json as _json
        canon = {
            # exits_first 由调用方返回后填充（展示用）——不参与指纹（与 docstring 一致）
            "adds": sorted((a.model_dump(mode="json") for a in self.adds),
                           key=lambda d: d["stock_code"]),
            "rejected": sorted((r.model_dump(mode="json") for r in self.rejected),
                               key=lambda d: d["stock_code"]),
        }
        return hashlib.sha256(_json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def solve_budget(proposals: list[AddProposal], holdings: list[HoldingWeight],
                 constraints: BudgetConstraints,
                 *, sell_eligible_nav: float = 0.0) -> BudgetSolution:
    """组合预算求解（确定性约束分配；排列无关）。

    Args:
        proposals: 新增提案（OPEN/ADD）；**先由调用方完成退出/减仓识别**——本函数
            只做新增分配。exits_first 字段由调用方在返回后自行填充（不参与
            fingerprint——展示用途）
        holdings: 已确认持仓（事实层）
        constraints: 账户约束
        sell_eligible_nav: **已确认且可成交**的卖出释放现金/NAV——未确认/受阻卖出
            不得提前计入（G09/G11：卖不出不得提前花卖出现金）
    """
    sol = BudgetSolution()
    holdings_by_code = {h.stock_code: h for h in holdings}
    current = {h.stock_code: h.weight for h in holdings}
    horizon_exposure: dict[str, float] = {}
    industry_exposure: dict[str, float] = {}
    portfolio_pressure = 0.0
    for h in holdings:
        horizon_exposure[h.horizon] = horizon_exposure.get(h.horizon, 0.0) + h.weight
        for ind in h.industries:
            industry_exposure[ind] = industry_exposure.get(ind, 0.0) + h.weight

    cash_known = constraints.cash_nav is not None
    cash_nav = (constraints.cash_nav or 0.0) + max(0.0, sell_eligible_nav)         if cash_known else None
    # F7 审查 P1-2：现金未设置（None）≠ 0 现金——跳过现金约束（DESIGN：没有可用
    # 配置不假装知道承受能力），拒绝理由不会假称"可用现金不足"
    remaining_tradeable = (constraints.tradeable_nav
                           if constraints.tradeable_nav is not None else None)
    sol.sell_cash_available_nav = cash_nav if cash_known else 0.0

    # 稳定排序：风险紧迫度 desc → 计划内到期 asc → score desc → 证据等级 desc → code asc
    def _sort_key(p: AddProposal):
        return (-p.risk_urgency,
                p.plan_due or "9999-12-31",
                -p.score,
                -p.evidence_grade,
                p.stock_code,
                p.target_weight)  # F7-F9 审查：同股多提案（同键不同目标）排列不变
    ordered = sorted(proposals, key=_sort_key)

    adds_count = 0
    for p in ordered:
        cur = current.get(p.stock_code, 0.0)
        caps = []
        label = ""

        cap0 = p.target_weight - cur
        caps.append((cap0, "计划目标"))

        if constraints.per_stock_max is not None:
            c1 = constraints.per_stock_max - cur
            caps.append((c1, "单股上限"))

        hb = constraints.horizon_budgets.get(p.horizon)
        if hb is not None:
            c2 = hb - horizon_exposure.get(p.horizon, 0.0)
            caps.append((c2, f"{p.horizon} 周期总预算"))

        inds = p.industries or ["__none__"]
        if constraints.industry_max is not None:
            c3 = min(constraints.industry_max - industry_exposure.get(i, 0.0) for i in inds)
            caps.append((c3, "行业/主题上限"))

        if cash_known:
            caps.append((cash_nav, "可用现金"))

        if remaining_tradeable is not None:
            # F7 审查 P1-1：容量约束逐笔递减——N 只候选合计不超容量（原实现每只用
            # 全值，N 只可成交 N×容量）
            caps.append((remaining_tradeable, "可交易额"))

        if p.pressure_loss_rate is not None and p.pressure_loss_rate > 0:
            if constraints.portfolio_loss_budget is not None:
                # 单位换算：损失预算占NAV → 可承载权重 = 剩余预算 / 该股压力损失率
                remaining_loss = constraints.portfolio_loss_budget - portfolio_pressure
                c5 = max(0.0, remaining_loss) / p.pressure_loss_rate
                caps.append((c5, "组合剩余压力损失预算"))
            # 个股损失预算维度需要个股损失预算参数（用户档）；未设置则跳过该约束
        else:
            # 压力损失率未知 → 不给此模型的精确额度（DESIGN：不给精确仓位）
            caps.append((0.0, "压力损失率未知"))
            label = "压力损失率未知"

        budget = max(0.0, min(c for c, _ in caps))
        if budget <= 1e-9:
            # 全部为零时找首个限制性约束给原因（资金不足时只给可行计划——G09）
            binding = next((lbl for c, lbl in caps if c <= 1e-9), "约束")
            if label:
                binding = label
            sol.rejected.append(RejectedAdd(
                stock_code=p.stock_code, stock_name=p.stock_name,
                reason=f"无可新增额度（受限于：{binding}）"))
            continue

        if constraints.max_adds is not None and adds_count >= constraints.max_adds:
            sol.rejected.append(RejectedAdd(
                stock_code=p.stock_code, stock_name=p.stock_name,
                reason=f"本轮新增标的数已达研究预算上限（{constraints.max_adds}）"))
            continue

        line = BudgetLine(
            stock_code=p.stock_code, stock_name=p.stock_name,
            current_weight=cur, add_weight=round(budget, 10),
            target_weight=round(cur + budget, 10), horizon=p.horizon,
            trimmed_to="、".join(lbl for c, lbl in caps if abs(c - budget) <= 1e-12),
            reason="按稳定排序分配（紧迫度→到期→明示因子→code）",
        )
        sol.adds.append(line)
        adds_count += 1
        # 提交状态更新（后续提案看到已占用额度——聚合口径）
        current[p.stock_code] = cur + budget
        if cash_known:
            cash_nav = max(0.0, cash_nav - budget)
        if remaining_tradeable is not None:
            remaining_tradeable = max(0.0, remaining_tradeable - budget)
        horizon_exposure[p.horizon] = horizon_exposure.get(p.horizon, 0.0) + budget
        for i in inds:
            if i == "__none__":
                continue  # 内部哨兵不进公开的行业暴露输出（F7 审查 P2-4）
            industry_exposure[i] = industry_exposure.get(i, 0.0) + budget
        if p.pressure_loss_rate and p.pressure_loss_rate > 0 and constraints.portfolio_loss_budget is not None:
            portfolio_pressure += budget * p.pressure_loss_rate

    sol.committed_industry_exposure = {k: round(v, 10) for k, v in industry_exposure.items()}
    sol.fingerprint = sol.fingerprint_of()
    return sol
