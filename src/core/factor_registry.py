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

FACTOR_REGISTRY_VERSION = "r4.v1"


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
        version="2",  # ISS-112：定义对齐 v1 实际口径（baostock CFOToNP 年初累计；TTM 差分留待），bump 版本留痕
        horizon="LONG",
        definition="经营现金流/净利润（baostock CFOToNP，年初累计口径）；v1 无 TTM 差分"
                   "（相邻累计差分留待）；利润为负走风险解释不比值",
        inputs=["financial.cash_flow CFOToNP（baostock，pubDate 可 PIT）",
                "financial.profit netProfit（baostock）"],
        unit="倍",
        missing_policy="分母≤0 不用比值排名，改专门风险解释（sector_growth_guard）；分项缺失各自 UNKNOWN",
        sector_scope="非金融",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，financial_data.py 已接；年初累计口径）",
        economic_hypothesis="利润有现金流支撑的公司盈利质量更高——长期持有的核心筛选之一",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过（pubDate 实证）；计算在 factor_compute.py
        sector_growth_guard="净利润≤0 时改输出『利润为负+现金流方向』的风险解释，不做比值",
    ),
    FactorSpec(
        factor_id="cash_conversion_v1",
        version="1",  # R4：真实能力ID（earnings_quality_v1 v2 的计算承接至此——ID=能力，代理不再冒名）
        horizon="LONG",
        definition="经营现金流/净利润（baostock CFOToNP，年初累计口径）；利润为负/接近零"
                   "走风险解释不比值；一次性损益占比不可得→该分量 UNKNOWN（解释资本开支/"
                   "营运资本/一次性项目属 R3 命题评估，非本因子）",
        inputs=["financial.cash_flow CFOToNP（baostock，pubDate 可 PIT）",
                "financial.profit netProfit（baostock）"],
        unit="倍",
        missing_policy="分母≤0 或 |分母| 接近零不用比值排名，改专门风险解释（sector_growth_guard）；分项缺失各自 UNKNOWN",
        sector_scope="非金融",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，financial_data.py 已接；年初累计口径）",
        economic_hypothesis="利润有现金流支撑的公司盈利质量更高——长期持有的核心筛选之一",
        experiment_id="",
        needs_data_probe=False,
        sector_growth_guard="净利润≤0 或接近零时改输出『利润为负/微利+现金流方向』的风险解释，不做比值",
    ),
    FactorSpec(
        factor_id="roe_observed_v1",
        version="1",  # R4：ROE 独立能力ID——不再承载 capital_return（ROIC）名义（A08）
        horizon="LONG",
        definition="ROE（baostock roeAvg，平均净资产收益率）——股东账面资本回报的**观察值**；"
                   "高杠杆会抬高 ROE，跨行业比较需结合 liabilityToAsset 分量",
        inputs=["financial.profit roeAvg（baostock，pubDate 可 PIT）"],
        unit="倍（小数比值）",
        missing_policy="ROE 缺失 → UNKNOWN；不把高杠杆 ROE 当质量",
        sector_scope="非金融（金融不硬套）",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，financial_data.py 已接）",
        economic_hypothesis="持续股东回报是长期价值来源线索（观察维度）",
        experiment_id="",
        needs_data_probe=False,
        sector_growth_guard="投入资本≤0 时输出 UNKNOWN 并注明口径异常",
    ),
    FactorSpec(
        factor_id="pe_ttm_v1",
        version="1",  # R4：trailing PE 独立能力ID——不冒 valuation_range（区间）名义（A08）
        horizon="LONG",
        definition="trailing PE = price / epsTTM（当前价格/已报告盈利倍数）——位置维度不是目标价；"
                   "亏损企业不用普通 PE",
        inputs=["financial.profit epsTTM（baostock，pubDate 可 PIT）", "market.close（不复权口径显式声明）"],
        unit="倍",
        missing_policy="盈利为负 → NOT_APPLICABLE + 保留研究；价格/盈利缺失 → UNKNOWN",
        sector_scope="盈利稳定的普通非金融企业可作位置参考；高增长亏损/金融显式 NOT_APPLICABLE",
        availability_rule="财务三表（探查通过）+ 同日行情（不复权 bar）",
        economic_hypothesis="当前价隐含的已报告盈利倍数是估值位置的粗代理",
        experiment_id="",
        needs_data_probe=False,
        sector_growth_guard="正常化盈利≤0 时不用普通 PE，转专门研究",
    ),
    FactorSpec(
        factor_id="relative_return_v2",
        version="2",  # R4：输入改带交易日期序列（同窗按日期对齐——停牌错位不再伪同窗）
        horizon="MID",
        definition="过去 N 个指数交易日个股收益 − 行业指数收益（按交易日期对齐：两端点必须"
                   "在指数交易日上有真实 bar，区间内停牌缺 bar 计数声明）；窗长是首个实验候选",
        inputs=["market.daily（不复权口径显式声明）",
                "market.industry_index（akshare index_hist_sw 申万日线，探查可用）"],
        unit="%",
        missing_policy="行业指数缺失 → UNKNOWN（不用全市场指数冒充行业）；端点缺 bar（停牌）→ UNKNOWN；"
                       "区间缺 bar 比例超阈 → UNKNOWN 并声明停牌",
        sector_scope="全行业",
        availability_rule="DATA_COVERAGE「行业指数」行——个股日线与指数日线均按日期对齐",
        economic_hypothesis="相对行业强弱是中期趋势纪律的排序输入（Weinstein 体系的量化投影）",
        experiment_id="E4",
        needs_data_probe=False,
    ),
    FactorSpec(
        factor_id="capital_return_v1",
        version="3",  # R4：回归 ROIC 本名——投入资本分项不可得，当前**不可计算**（ROE 观察见 roe_observed_v1，不得自动满足本能力，A08）
        horizon="LONG",
        definition="ROIC（投入资本回报：NOPAT/投入资本）——投入资本分项（有息负债/净资产"
                   "拆分）数据源不可得，**当前不可计算**；ROE 观察值是独立因子 roe_observed_v1，"
                   "不自动满足本能力（研究资格按能力ID匹配）",
        inputs=["financial.投入资本分项（数据源不可得——待 DATA_COVERAGE 增补）"],
        unit="倍（小数比值）",
        missing_policy="不可计算 → UNKNOWN（不填通用数字强行纳入）",
        sector_scope="非金融（金融不硬套）",
        availability_rule="待数据源增补（DATA_COVERAGE 登记前保持不可计算）",
        economic_hypothesis="持续资本回报率高于资本成本的企业创造长期价值（假设——未验证）",
        experiment_id="",
        needs_data_probe=True,  # R4：翻转回待探查（不可计算态如实登记）
        sector_growth_guard="投入资本≤0 时输出 UNKNOWN 并注明口径异常",
    ),
    FactorSpec(
        factor_id="balance_risk_v1",
        version="2",  # ISS-112：定义对齐 v1 实际分量（baostock balance 可得字段；净债务/利息保障不可得），bump 留痕
        horizon="LONG",
        definition="流动比率/速动比率/资产负债率（baostock balance 现成字段）——各自独立输出，"
                   "不合成单一分；净债务/利息保障/到期分布数据源不可得→该分量 UNKNOWN",
        inputs=["financial.balance currentRatio/quickRatio/liabilityToAsset（baostock，pubDate 可 PIT）"],
        unit="各分量原生单位（倍）",
        missing_policy="缺到期分布数据不认定无偿债压力——该分量 UNKNOWN 并注明",
        sector_scope="非金融工业企业口径（金融行业规则未适配，禁套）",
        availability_rule="DATA_COVERAGE「财务三表」（探查通过，financial_data.py 已接）",
        economic_hypothesis="资产负债结构约束是硬风险/复核触发源，独立于盈利质量",
        experiment_id="",
        needs_data_probe=False,  # 2026-09-26 探查通过；计算在 factor_compute.py
    ),
    FactorSpec(
        factor_id="valuation_range_v1",
        version="3",  # R4：回归区间本名——可比样本集未接线，当前**不可计算**（trailing PE 见 pe_ttm_v1，不得自动满足本能力，A08）
        horizon="LONG",
        definition="正常化盈利区间×适用倍数区间（同行业同时点可比样本）——可比样本集未接线，"
                   "**当前不可计算**；trailing PE 是独立因子 pe_ttm_v1（位置观察），"
                   "不自动满足估值区间能力（研究资格按能力ID匹配）",
        inputs=["financial.可比同业样本（未接线——待 DATA_COVERAGE 增补）", "market.close（不复权口径显式声明）"],
        unit="倍（区间）",
        missing_policy="不可计算 → UNKNOWN（亏损企业转专门研究；不编缺失估值输入）",
        sector_scope="先支持盈利稳定的普通非金融企业；高增长亏损/金融未适配显式 NOT_APPLICABLE",
        availability_rule="待可比样本集接线（DATA_COVERAGE 登记前保持不可计算）",
        economic_hypothesis="真实估值区间决定『好公司≠现在适合买』（假设——未验证）",
        experiment_id="",
        needs_data_probe=True,  # R4：翻转回待接线（不可计算态如实登记）
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
