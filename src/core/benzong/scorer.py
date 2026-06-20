"""笨总「超景气价值投机」量化评分体系（v0.8.6.1）

来源：用户提供的笨总 B 站教学知识库
- 投资策略（持续更新）/笨总教学 bilibili-笨笨的韭菜/选股逻辑-教学/超景气价值投机-个股评分（二）.md
- 投资策略（持续更新）/笨总教学 bilibili-笨笨的韭菜/笨总课件/笨总选股打分表.xlsx

设计原则：
1. **严格忠于原始打分表公式**，不擅自加权重 / 不擅自缩放 / 不擅自重定义维度
2. **大前提（教学 2 反复强调）**：必须先有"高景气行业 + 现象级拐点"判断，
   才能用这个表给具体个股打分。中免案例（行业景气=0）反证了这点。
3. 公式严格按 xlsx：总分 = (Σ 各项分 × 价值占比) × 流动性系数

模块组成：
- scorer.BenzhongScorer：评分核心 + 6 维 dataclass
- scorer.score_one(...) 顶层便利函数

评分公式（与 xlsx Sheet1 一致）：

| 维度 | 价值占比 | 范围 |
|------|--------|------|
| 行业景气度 | 0.20 | 0-100 |
| 业务纯度 | 0.40 | 0-100（权重最大） |
| 历史估值位置 | 0.25 | 0-100 |
| 细分行业龙头 | 0.15 | 0-100 |
| 市场辨识度 | 0.20 | 0-100 |
| 个股风险值 | -0.20 | 0-100（扣分项） |

流动性系数：
- 全市场单日成交额 ≤ 0.8 万亿 → ×0.8
- ≥ 1.5 万亿              → ×1.2
- 其他                     → ×1.0

注意：业务纯度+0.40 + 历史估值+0.25 + 细分龙头+0.15 + 辨识度+0.20 +
行业景气+0.20 = 1.20，再 + 风险-0.20 = 1.00，与 xlsx C 列定义一致。
"""

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional


# 维度价值占比（与 xlsx C 列严格一致）
WEIGHT_INDUSTRY_PROSPERITY = 0.20
WEIGHT_BUSINESS_PURITY = 0.40
WEIGHT_VALUATION_POSITION = 0.25
WEIGHT_INDUSTRY_LEADER = 0.15
WEIGHT_MARKET_RECOGNITION = 0.20
WEIGHT_RISK_DEDUCTION = -0.20

# 流动性系数门槛（xlsx B9 注释）
LIQUIDITY_LOW_THRESHOLD = 0.8   # 单位：万亿，全市场单日成交额
LIQUIDITY_HIGH_THRESHOLD = 1.5
LIQUIDITY_LOW_COEFF = 0.8
LIQUIDITY_HIGH_COEFF = 1.2
LIQUIDITY_NORMAL_COEFF = 1.0


def liquidity_coefficient(market_turnover_trillion: float) -> float:
    """根据全市场单日成交额（万亿）返回流动性系数。

    Args:
        market_turnover_trillion: 全市场单日成交额，单位万亿（如 1.2 表示 1.2 万亿）

    Returns:
        流动性系数 0.8 / 1.0 / 1.2

    >>> liquidity_coefficient(0.7)
    0.8
    >>> liquidity_coefficient(1.0)
    1.0
    >>> liquidity_coefficient(1.6)
    1.2
    """
    if market_turnover_trillion <= LIQUIDITY_LOW_THRESHOLD:
        return LIQUIDITY_LOW_COEFF
    if market_turnover_trillion >= LIQUIDITY_HIGH_THRESHOLD:
        return LIQUIDITY_HIGH_COEFF
    return LIQUIDITY_NORMAL_COEFF


@dataclass
class BenzhongScore:
    """笨总 6 维打分输入 + 计算结果"""

    # 6 维原始打分（0-100）
    industry_prosperity: float       # 行业景气度
    business_purity: float           # 业务纯度（最重要，权重 40%）
    valuation_position: float        # 历史估值位置
    industry_leader: float           # 细分行业龙头
    market_recognition: float        # 市场辨识度
    risk_deduction: float            # 个股风险值（扣分项，0=最好）

    # 流动性
    market_turnover_trillion: float  # 全市场单日成交额（万亿）

    # 元数据（可选，用于审计）
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    note: str = ""

    # 计算字段（自动填充）
    contributions: dict = field(default_factory=dict)
    raw_sum: float = 0.0
    liquidity_coeff: float = 1.0
    total_score: float = 0.0

    def __post_init__(self):
        """计算各项贡献 + 总分（与 xlsx 公式一致）"""
        self.contributions = {
            "industry_prosperity": self.industry_prosperity * WEIGHT_INDUSTRY_PROSPERITY,
            "business_purity": self.business_purity * WEIGHT_BUSINESS_PURITY,
            "valuation_position": self.valuation_position * WEIGHT_VALUATION_POSITION,
            "industry_leader": self.industry_leader * WEIGHT_INDUSTRY_LEADER,
            "market_recognition": self.market_recognition * WEIGHT_MARKET_RECOGNITION,
            "risk_deduction": self.risk_deduction * WEIGHT_RISK_DEDUCTION,
        }
        self.raw_sum = sum(self.contributions.values())
        self.liquidity_coeff = liquidity_coefficient(self.market_turnover_trillion)
        self.total_score = round(self.raw_sum * self.liquidity_coeff, 2)

    def grade(self) -> Literal["A", "B", "C", "D", "F"]:
        """评分等级（基于总分）。

        来源：教学 2 + 教学报告 1 案例分析。
        - A 级 90+：超优质标的
        - B 级 80-90：优秀，可买入（HND 83.2 / THS 84.6 都是 B 级买入）
        - C 级 60-80：可观察
        - D 级 40-60：勉强观望
        - F 级 < 40：放弃
        """
        if self.total_score >= 90:
            return "A"
        if self.total_score >= 80:
            return "B"
        if self.total_score >= 60:
            return "C"
        if self.total_score >= 40:
            return "D"
        return "F"

    def precondition_warning(self) -> Optional[str]:
        """大前提警告：行业景气度 == 0 时打分模型失效（教学 2 反面案例）"""
        if self.industry_prosperity == 0:
            return (
                "⚠ 大前提失效：行业景气度=0 时，本打分模型不能用！"
                "教学 2 反面案例（中免）证明：没有高景气行业判断，其他得分再高也无意义。"
                "请先确认是否真有现象级拐点 / 高景气行业。"
            )
        return None

    def to_dict(self) -> dict:
        """序列化（用于 portfolio.yaml 持久化或日志）"""
        d = asdict(self)
        d["grade"] = self.grade()
        d["warning"] = self.precondition_warning()
        return d


def score_one(
    industry_prosperity: float,
    business_purity: float,
    valuation_position: float,
    industry_leader: float,
    market_recognition: float,
    risk_deduction: float,
    market_turnover_trillion: float,
    *,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    note: str = "",
) -> BenzhongScore:
    """顶层便利函数：一次性打 6 维分 + 流动性 → 输出 BenzhongScore"""
    return BenzhongScore(
        industry_prosperity=industry_prosperity,
        business_purity=business_purity,
        valuation_position=valuation_position,
        industry_leader=industry_leader,
        market_recognition=market_recognition,
        risk_deduction=risk_deduction,
        market_turnover_trillion=market_turnover_trillion,
        stock_code=stock_code,
        stock_name=stock_name,
        note=note,
    )
