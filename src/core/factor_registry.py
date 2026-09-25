"""因子登记表（plan/fusion F4，DESIGN §5.3）：首批 8 因子的定义与缺失政策。

纪律（DESIGN §5.3 原文约束，登记即冻结）：
- 每个新因子登记 factor_id/version/horizon/definition/inputs/unit/missing_policy/
  sector_scope/availability_rule/economic_hypothesis/experiment_id——字段不齐不许登记
- 首批因子保留原始值和解释，**不求一个覆盖所有用途的总分**；旧六维按原口径显示不动
- 表中门槛由训练窗实验选择并冻结，未完成验证前仅供研究（economic_hypothesis 是假设
  不是结论）
- 上游证据可得性以 plan/fusion/DATA_COVERAGE.md 矩阵为准：availability_rule 引用
  DATA_COVERAGE 的类别，可得性未验证的因子标 needs_data_probe=True——在探查通过前
  只登记不计算（不填通用数字强行纳入）

本模块只做**登记与校验**（纯数据+校验函数，零计算、零网络）；因子计算在数据接线
验证后按 experiment 立项引入。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

FACTOR_REGISTRY_VERSION = "f4.v1"


class FactorSpec(BaseModel):
    """一条因子登记（字段集=DESIGN §5.3 登记模板，缺一不可）。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09

    factor_id: str = Field(description="因子唯一ID（如 earnings_quality_v1）")
    version: str = Field(description="因子版本（定义/缺失政策变更必须 bump）")
    horizon: Literal["MID", "LONG", "BOTH"] = Field(description="适用周期（与气宗/剑宗 legacy_mode 正交）")
    definition: str = Field(description="计算/提取契约（人话+公式要素；可审计）")
    inputs: list[str] = Field(description="输入证据（引用 DATA_COVERAGE 类别或 user_asserted 通道）")
    unit: str = Field(description="单位（元/万元/%/倍/比值/None）")
    missing_policy: str = Field(description="缺失政策：显式说明缺失时如何表现（UNKNOWN/INCOMPLETE），禁止兜底 0/50")
    sector_scope: str = Field(description="适用行业范围（如 非金融/全行业；金融不硬套 ROIC/FCF）")
    availability_rule: str = Field(description="可得性规则（引用 DATA_COVERAGE 类别 + published/available 判据）")
    economic_hypothesis: str = Field(description="经济假设（为什么该因子应有效——研究假设非结论）")
    experiment_id: str = Field(default="", description="关联实验ID（E 矩阵编号；未立项为空=仅供研究）")
    needs_data_probe: bool = Field(default=False, description="上游可得性未验证（DATA_COVERAGE『待现场探查』）——探查通过前只登记不计算")
    sector_growth_guard: Optional[str] = Field(default=None, description="分母/口径防护（如『增速分母≤0 时不用普通百分比结论』）")


# ── 首批 8 因子（DESIGN §5.3 表格逐条登记；门槛未验证前仅供研究）──

FACTOR_SPECS: list[FactorSpec] = [
    FactorSpec(
        factor_id="business_exposure_v1",
        version="1",
        horizon="BOTH",
        definition="相关业务营收/总营收；同报告期同口径；另存利润贡献（如可得）。"
                   "分部数字缺失时为 UNKNOWN，禁止从主营文字猜测占比",
        inputs=["financial.segment_revenue（待现场探查）", "user_asserted（年报分部人工证据）"],
        unit="比值(0-1)",
        missing_policy="分部数字缺失 → value=UNKNOWN + research INCOMPLETE；不从主营介绍文字猜",
        sector_scope="全行业",
        availability_rule="DATA_COVERAGE「财务三表」或 user_asserted 通道；period_end/published 可信",
        economic_hypothesis="产业受益相关性由真实业务暴露决定——蹭概念标的暴露度低，产业逻辑不成立",
        experiment_id="",
        needs_data_probe=True,
    ),
    FactorSpec(
        factor_id="demand_change_v1",
        version="1",
        horizon="MID",
        definition="同口径需求量同比及较上期变化；价格、库存各自存原值不盲加——"
                   "价格上涨需结合上下游身份（上游利好可能是下游成本压力），库存变化需排季节性",
        inputs=["user_asserted（行业量价库存数据，需用户给来源）",
                "industry_data 文本报告层（live-only 非结构化，不可作 PIT 证据）"],
        unit="同比%(各分量分存)",
        missing_policy="任一分量缺失 → 该分量 UNKNOWN；三者不合成单一分数",
        sector_scope="全行业（上下游身份必填才能定方向）",
        availability_rule="DATA_COVERAGE「行业数据」行（live-only 文本层，无结构化序列——"
                          "结构化需求量数据待现场探查接线）",
        economic_hypothesis="行业经营方向（量价库存）驱动中期盈利预期——方向须由产业链位置定",
        experiment_id="",
        needs_data_probe=True,
    ),
    FactorSpec(
        factor_id="earnings_quality_v1",
        version="1",
        horizon="LONG",
        definition="TTM经营现金流/TTM净利润；另报利润为负、非经常损益占比、应收增速",
        inputs=["financial.cashflow_ttm（待现场探查）", "financial.netprofit_ttm（待现场探查）"],
        unit="比值",
        missing_policy="分母≤0 不用比值排名，改专门风险解释（sector_growth_guard）；分项缺失各自 UNKNOWN",
        sector_scope="非金融",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，接线待做；TTM 需至少4个季度披露，公布日可信）",
        economic_hypothesis="利润有现金流支撑的公司盈利质量更高——长期持有的核心筛选之一",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过（pubDate 实证）；接线批前仍只登记
        sector_growth_guard="净利润≤0 时改输出『利润为负+现金流方向』的风险解释，不做比值",
    ),
    FactorSpec(
        factor_id="capital_return_v1",
        version="1",
        horizon="LONG",
        definition="适用行业 NOPAT/平均投入资本（ROIC），或经核对的 ROE；保留计算构成（分母口径必须可审计）",
        inputs=["financial（baostock 季频 pubDate 已探查可 PIT，2026-09-26）", "user_asserted"],
        unit="%（或比值）",
        missing_policy="投入资本或 NOPAT 分项缺失 → UNKNOWN；不把高杠杆 ROE 当质量",
        sector_scope="非金融（金融与负投入资本不硬套 ROIC）",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，接线待做）",
        economic_hypothesis="持续资本回报率高于资本成本的企业创造长期价值",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过（pubDate 实证）；接线批前仍只登记
        sector_growth_guard="投入资本≤0 时输出 UNKNOWN 并注明口径异常",
    ),
    FactorSpec(
        factor_id="balance_risk_v1",
        version="1",
        horizon="LONG",
        definition="净债务、现金/短债、利息保障、到期分布——各自独立输出，不合成单一分",
        inputs=["financial.balance（baostock query_balance_data pubDate 已探查可 PIT，2026-09-26）"],
        unit="各分量原生单位",
        missing_policy="缺到期分布数据不认定无偿债压力——该分量 UNKNOWN 并注明",
        sector_scope="非金融工业企业口径（金融行业规则未适配，禁套）",
        availability_rule="DATA_COVERAGE「财务三表」",
        economic_hypothesis="资产负债结构约束是硬风险/复核触发源，独立于盈利质量",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过；接线批前仍只登记
    ),
    FactorSpec(
        factor_id="valuation_range_v1",
        version="1",
        horizon="LONG",
        definition="正常化每股盈利×可比倍数区间（同行业同时点样本），或经审核的现金流情景；"
                   "记录盈利定义、倍数参照、净债务/股数调整；亏损企业不用普通 PE；"
                   "价值区间不是确定目标价",
        inputs=["financial（baostock 季频 pubDate 已探查可 PIT，2026-09-26）", "market.close（天然PIT）", "user_asserted（可比样本）"],
        unit="元/股（区间）",
        missing_policy="盈利为负/可比样本不足 → NOT_APPLICABLE + 保留研究（不强填区间）",
        sector_scope="先支持盈利稳定的普通非金融企业；高增长亏损/金融未适配显式标 NOT_APPLICABLE",
        availability_rule="财务三表（探查通过，接线待做）+ 同日行情样本；倍数参照集必须同时点",
        economic_hypothesis="价格位置只是风险维度；真实估值区间决定『好公司≠现在适合买』",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过；接线批前仍只登记
        sector_growth_guard="正常化盈利≤0 时不用普通 PE，转专门研究",
    ),
    FactorSpec(
        factor_id="relative_trend_v1",
        version="1",
        horizon="MID",
        definition="过去60交易日个股收益 − 行业指数收益（窗长是首个实验候选，非已验证最优）；"
                   "扣除当日未收盘信息（bar 收盘日尾才可用）",
        inputs=["market.daily（天然PIT）", "market.industry_index（akshare index_hist_sw 申万日线，2026-09-26 探查可用，接线待做）"],
        unit="%",
        missing_policy="行业指数缺失 → UNKNOWN（不用全市场指数冒充行业）；停牌日剔除",
        sector_scope="全行业",
        availability_rule="DATA_COVERAGE「行业指数」行（探查通过，接线待做）——个股日线天然 PIT，"
                          "行业指数接线前因子整体不可算",
        economic_hypothesis="相对行业强弱是中期趋势纪律的排序输入（Weinstein 体系的量化投影）",
        experiment_id="E4",
        needs_data_probe=False,  # 2026-09-26 探查通过（申万指数日线可得）；接线批前仍只登记
    ),
    FactorSpec(
        factor_id="trading_capacity_v1",
        version="1",
        horizon="BOTH",
        definition="拟交易金额/过去20完整交易日日均成交额，附停牌、价差与限价状态",
        inputs=["market.amount（天然PIT，data_feeder 已接线）",
                "market.停牌状态（DATA_COVERAGE：未接线——分量暂记 UNKNOWN）"],
        unit="倍（+状态标记）",
        missing_policy="成交额未知不能默认正常——UNKNOWN + 不发精确指令；元/万元显式换算；"
                       "停牌状态未接线期间该分量记 UNKNOWN（不影响比值主计算）",
        sector_scope="全行业",
        availability_rule="DATA_COVERAGE「日线行情」——天然 PIT",
        economic_hypothesis="执行容量约束决定建议是否可实现（组合预算的输入之一）",
        experiment_id="E4",
        needs_data_probe=False,
    ),
]


def get_spec(factor_id: str) -> Optional[FactorSpec]:
    for s in FACTOR_SPECS:
        if s.factor_id == factor_id:
            return s
    return None


def validate_registry() -> list[str]:
    """登记表自检：字段不齐/ID 重复/版本缺失即报错清单（登记即冻结的机器化）。

    注意（F4 审查 P2）：inputs 的引用校验是**子串级**——拦得住完全未引用，拦不住
    "引用了矩阵中不存在的类别"（幻影引用）；类别存在性需与 DATA_COVERAGE.md 逐行
    人工对照（本批已对照修正：行业数据/行业指数/停牌状态三行已入矩阵）。"""
    problems = []
    seen = set()
    required_fields = ("definition", "inputs", "unit", "missing_policy",
                       "sector_scope", "availability_rule", "economic_hypothesis")
    for s in FACTOR_SPECS:
        if s.factor_id in seen:
            problems.append(f"因子ID重复: {s.factor_id}")
        seen.add(s.factor_id)
        for f in required_fields:
            if not getattr(s, f):
                problems.append(f"{s.factor_id} 缺登记字段: {f}")
        if not s.version:
            problems.append(f"{s.factor_id} 缺版本")
        for inp in s.inputs:
            if "user_asserted" not in inp and "DATA_COVERAGE" not in inp and "market" not in inp \
                    and "industry" not in inp and "financial" not in inp and "待现场探查" not in inp:
                problems.append(f"{s.factor_id} 输入未引用 DATA_COVERAGE 类别: {inp}")
    return problems
