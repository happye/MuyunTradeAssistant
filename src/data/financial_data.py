"""baostock 季频财务接口 → 研究证据层窄适配（plan/fusion 影子批后续 + iteration2 R1）。

探查锚定（2026-09-26，tests/data_sources/probe_fin_pubdate.py + 贵州茅台 2023Q4/2024Q1 实测）：
- 五接口（query_profit/balance/cash_flow/operation/growth_data）均带 pubDate 官方公布日
  （2023Q4→2024-04-03；2024Q1→2024-04-27）——PIT 判据成立
- **数值口径实测**：货币字段为元（netProfit≈775 亿量级 ✓）；比例字段为**小数比值**
  （roeAvg 0.3618 = 36.18%——baostock 文档标 % 但实测非百分数，unit 一律登记"倍"，
  不做二次换算）；缺失值是空串 ""，映射 None（绝不补 0 假装存在）
- 全部为**年初累计口径**（Q4 累计=年报；Q1 累计=单季）——period_kind="cumulative"
  显式登记；单季值须由相邻累计差分得出（research_snapshot.derive_single_quarter_from_cumulative）
- **重述限制（诚实登记）**：接口只返回最新一版（同期重复查询一致），后发重述与首发
  不可区分——knowledge_basis="LATEST_WITH_PUBLICATION_DATE"（R1 定级：带公布日的
  最新版不进 strict 历史快照，DATA_TRUST §1；原始历史文档走 announcement/档案路径）

R1 字段语义登记表（FIELD_DEFINITIONS，DATA_TRUST §3）：
- 每字段登记 value_kind（FLOW/STOCK/RATIO/GROWTH/PER_SHARE_TTM）/period_basis/
  underlying_period_basis/unit/metric_definition_version——派生规则按登记分派，
  不按 metric 名猜；旧 period_kind=cumulative 不自动推断为新字段
- 登记依据 = 字段名语义 + 茅台实测锚定；verification_status="name_semantics_pending_original"
  ——**待原始资料逐项复核**（R1 验收6：probe_fin_semantics.py 显式网络入口，失败登记
  而非 fixture 假称实测）
- 消费边界（审查 P1-4 措辞校准）：SUSPECT 隔离已在 research_snapshot.qualify 生效；
  「未核实字段不用于长期资格/风险预算/排名优选」的**消费侧限制尚未接线**（属
  R3/R4/R8 服务层与因子层）——当前语义状态只随证据记录落账，不隐含任何下游已拦截

网络纪律：baostock 读取走 _call_with_timeout 30s 硬超时（防冻结，同 data_feeder 口径）；
本模块只服务 **live 证据抓取**——回测路径不调用（财务证据经 research_snapshot 严格
PIT 闸门进快照，回放从快照读而非实时拉）。
"""

import logging
from concurrent.futures import TimeoutError as _FuturesTimeout
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.data.akshare_client import _call_with_timeout, _ensure_baostock_login

logger = logging.getLogger(__name__)

FINANCIAL_SOURCE_VERSION = "baostock_financial_v1"
FIELD_REGISTRY_VERSION = "baostock_field_registry_v1"

# (接口名, [(字段, 单位), ...])——单位实测锚定见模块 docstring；新增字段先实测再登记
_INTERFACE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "query_profit_data": [
        ("roeAvg", "倍"), ("npMargin", "倍"), ("gpMargin", "倍"),
        ("netProfit", "元"), ("epsTTM", "元"), ("MBRevenue", "元"),
        ("totalShare", "股"), ("liqaShare", "股"),
    ],
    "query_balance_data": [
        ("currentRatio", "倍"), ("quickRatio", "倍"), ("cashRatio", "倍"),
        ("YOYLiability", "倍"), ("liabilityToAsset", "倍"), ("assetToEquity", "倍"),
    ],
    "query_cash_flow_data": [
        ("CAToAsset", "倍"), ("NCAToAsset", "倍"), ("tangibleAssetToAsset", "倍"),
        ("ebitToInterest", "倍"), ("CFOToOR", "倍"), ("CFOToNP", "倍"), ("CFOToGr", "倍"),
    ],
    "query_operation_data": [
        ("NRTurnRatio", "次"), ("NRTurnDays", "天"), ("INVTurnRatio", "次"),
        ("INVTurnDays", "天"), ("CATurnRatio", "次"), ("AssetTurnRatio", "次"),
    ],
    "query_growth_data": [
        ("YOYEquity", "倍"), ("YOYAsset", "倍"), ("YOYNI", "倍"),
        ("YOYEPSBasic", "倍"), ("YOYPNI", "倍"),
    ],
}


class FinancialFieldDefinition(BaseModel):
    """单字段经济语义登记（DATA_TRUST §3：value_kind/period_basis 驱动派生规则）。"""

    metric: str
    interface: str
    value_kind: Literal["FLOW", "STOCK", "RATIO", "GROWTH", "PER_SHARE_TTM"]
    period_basis: Literal["YTD", "POINT_IN_TIME", "TTM", "COMPARATIVE"]
    underlying_period_basis: Optional[Literal[
        "YTD", "POINT_IN_TIME", "TTM"]] = None
    unit: str
    definition_note: str = ""
    # 登记依据 = 字段名语义 + 茅台实测锚定；逐项对照原始财报后才翻 verified
    verification_status: Literal["name_semantics_pending_original",
                                 "verified_against_original"] = "name_semantics_pending_original"


# 字段语义登记表（R1，DATA_TRUST §3——不依据单一茅台样本推定全部口径：
# value_kind/period_basis 按字段名经济含义登记，逐项待原始资料复核）。
# 注意：未登记字段不产生证据（_INTERFACE_FIELDS 与本表不一致时 get_financial_quarterly 告警跳过）
FIELD_DEFINITIONS: dict[str, FinancialFieldDefinition] = {
    # ── query_profit_data（利润表：期间流量为主）──
    "roeAvg": FinancialFieldDefinition(metric="roeAvg", interface="query_profit_data",
                                       value_kind="RATIO", period_basis="YTD",
                                       underlying_period_basis="YTD", unit="倍",
                                       definition_note="平均净资产收益率；分子分母均为期间口径"),
    "npMargin": FinancialFieldDefinition(metric="npMargin", interface="query_profit_data",
                                         value_kind="RATIO", period_basis="YTD",
                                         underlying_period_basis="YTD", unit="倍",
                                         definition_note="销售净利率（期间流量比）"),
    "gpMargin": FinancialFieldDefinition(metric="gpMargin", interface="query_profit_data",
                                         value_kind="RATIO", period_basis="YTD",
                                         underlying_period_basis="YTD", unit="倍",
                                         definition_note="销售毛利率（期间流量比）"),
    "netProfit": FinancialFieldDefinition(metric="netProfit", interface="query_profit_data",
                                          value_kind="FLOW", period_basis="YTD", unit="元",
                                          definition_note="净利润（年初累计）；合并范围/归属口径待原始年报核对"),
    "epsTTM": FinancialFieldDefinition(metric="epsTTM", interface="query_profit_data",
                                       value_kind="PER_SHARE_TTM", period_basis="TTM",
                                       unit="元",
                                       definition_note="滚动12月每股收益——禁止当 YTD 利润差分（DATA_TRUST §3）"),
    "MBRevenue": FinancialFieldDefinition(metric="MBRevenue", interface="query_profit_data",
                                          value_kind="FLOW", period_basis="YTD", unit="元",
                                          definition_note="主营业务收入（年初累计）"),
    "totalShare": FinancialFieldDefinition(metric="totalShare", interface="query_profit_data",
                                           value_kind="STOCK", period_basis="POINT_IN_TIME",
                                           unit="股", definition_note="总股本（期末存量）——禁止当累计量差分"),
    "liqaShare": FinancialFieldDefinition(metric="liqaShare", interface="query_profit_data",
                                          value_kind="STOCK", period_basis="POINT_IN_TIME",
                                          unit="股", definition_note="流通股（期末存量）"),
    # ── query_balance_data（资产负债表：期末存量比；YOY 为比较期）──
    "currentRatio": FinancialFieldDefinition(metric="currentRatio", interface="query_balance_data",
                                             value_kind="RATIO", period_basis="POINT_IN_TIME",
                                             underlying_period_basis="POINT_IN_TIME", unit="倍",
                                             definition_note="流动比率（期末存量比）——禁止相邻期差分出『单季比率』"),
    "quickRatio": FinancialFieldDefinition(metric="quickRatio", interface="query_balance_data",
                                           value_kind="RATIO", period_basis="POINT_IN_TIME",
                                           underlying_period_basis="POINT_IN_TIME", unit="倍",
                                           definition_note="速动比率（期末存量比）"),
    "cashRatio": FinancialFieldDefinition(metric="cashRatio", interface="query_balance_data",
                                          value_kind="RATIO", period_basis="POINT_IN_TIME",
                                          underlying_period_basis="POINT_IN_TIME", unit="倍",
                                          definition_note="现金比率（期末存量比）"),
    "YOYLiability": FinancialFieldDefinition(metric="YOYLiability", interface="query_balance_data",
                                             value_kind="GROWTH", period_basis="COMPARATIVE",
                                             unit="倍",
                                             definition_note="负债同比增长率——合法跨零，不参与量级筛查"),
    "liabilityToAsset": FinancialFieldDefinition(metric="liabilityToAsset",
                                                 interface="query_balance_data",
                                                 value_kind="RATIO", period_basis="POINT_IN_TIME",
                                                 underlying_period_basis="POINT_IN_TIME",
                                                 unit="倍",
                                                 definition_note="资产负债率=负债总额/资产总额（期末存量比）"
                                                 "——ISS-114 涉事字段：跨期量级漂移只隔离待核，不自动×100"),
    "assetToEquity": FinancialFieldDefinition(metric="assetToEquity", interface="query_balance_data",
                                              value_kind="RATIO", period_basis="POINT_IN_TIME",
                                              underlying_period_basis="POINT_IN_TIME", unit="倍",
                                              definition_note="权益乘数（期末存量比）"),
    # ── query_cash_flow_data（期末存量比 + 期间流量比）──
    "CAToAsset": FinancialFieldDefinition(metric="CAToAsset", interface="query_cash_flow_data",
                                          value_kind="RATIO", period_basis="POINT_IN_TIME",
                                          underlying_period_basis="POINT_IN_TIME", unit="倍",
                                          definition_note="流动资产/总资产（期末存量比）"),
    "NCAToAsset": FinancialFieldDefinition(metric="NCAToAsset", interface="query_cash_flow_data",
                                           value_kind="RATIO", period_basis="POINT_IN_TIME",
                                           underlying_period_basis="POINT_IN_TIME", unit="倍",
                                           definition_note="非流动资产/总资产（期末存量比）"),
    "tangibleAssetToAsset": FinancialFieldDefinition(metric="tangibleAssetToAsset",
                                                     interface="query_cash_flow_data",
                                                     value_kind="RATIO",
                                                     period_basis="POINT_IN_TIME",
                                                     underlying_period_basis="POINT_IN_TIME",
                                                     unit="倍",
                                                     definition_note="有形资产/总资产（期末存量比）"),
    "ebitToInterest": FinancialFieldDefinition(metric="ebitToInterest",
                                               interface="query_cash_flow_data",
                                               value_kind="RATIO", period_basis="YTD",
                                               underlying_period_basis="YTD", unit="倍",
                                               definition_note="利息保障倍数（分子为期间流量）"),
    "CFOToOR": FinancialFieldDefinition(metric="CFOToOR", interface="query_cash_flow_data",
                                        value_kind="RATIO", period_basis="YTD",
                                        underlying_period_basis="YTD", unit="倍",
                                        definition_note="经营现金流/营业收入（期间流量比）"),
    "CFOToNP": FinancialFieldDefinition(metric="CFOToNP", interface="query_cash_flow_data",
                                        value_kind="RATIO", period_basis="YTD",
                                        underlying_period_basis="YTD", unit="倍",
                                        definition_note="经营现金流/净利润（期间流量比）——分母接近零/负值时语义失真"),
    "CFOToGr": FinancialFieldDefinition(metric="CFOToGr", interface="query_cash_flow_data",
                                        value_kind="RATIO", period_basis="YTD",
                                        underlying_period_basis="YTD", unit="倍",
                                        definition_note="经营现金流/营业总收入（期间流量比）"),
    # ── query_operation_data（周转：分子为期间流量、分母为期均存量）──
    "NRTurnRatio": FinancialFieldDefinition(metric="NRTurnRatio", interface="query_operation_data",
                                            value_kind="RATIO", period_basis="YTD",
                                            underlying_period_basis="YTD", unit="次",
                                            definition_note="应收账款周转率=营业收入/平均应收（混合口径，非纯存量比）"),
    "NRTurnDays": FinancialFieldDefinition(metric="NRTurnDays", interface="query_operation_data",
                                           value_kind="RATIO", period_basis="YTD",
                                           underlying_period_basis="YTD", unit="天",
                                           definition_note="应收账款周转天数=360/周转率"),
    "INVTurnRatio": FinancialFieldDefinition(metric="INVTurnRatio", interface="query_operation_data",
                                             value_kind="RATIO", period_basis="YTD",
                                             underlying_period_basis="YTD", unit="次",
                                             definition_note="存货周转率"),
    "INVTurnDays": FinancialFieldDefinition(metric="INVTurnDays", interface="query_operation_data",
                                            value_kind="RATIO", period_basis="YTD",
                                            underlying_period_basis="YTD", unit="天",
                                            definition_note="存货周转天数"),
    "CATurnRatio": FinancialFieldDefinition(metric="CATurnRatio", interface="query_operation_data",
                                            value_kind="RATIO", period_basis="YTD",
                                            underlying_period_basis="YTD", unit="次",
                                            definition_note="流动资产周转率"),
    "AssetTurnRatio": FinancialFieldDefinition(metric="AssetTurnRatio",
                                               interface="query_operation_data",
                                               value_kind="RATIO", period_basis="YTD",
                                               underlying_period_basis="YTD", unit="次",
                                               definition_note="总资产周转率"),
    # ── query_growth_data（同比增长率：合法跨零）──
    "YOYEquity": FinancialFieldDefinition(metric="YOYEquity", interface="query_growth_data",
                                          value_kind="GROWTH", period_basis="COMPARATIVE",
                                          unit="倍", definition_note="净资产同比增长率"),
    "YOYAsset": FinancialFieldDefinition(metric="YOYAsset", interface="query_growth_data",
                                         value_kind="GROWTH", period_basis="COMPARATIVE",
                                         unit="倍", definition_note="总资产同比增长率"),
    "YOYNI": FinancialFieldDefinition(metric="YOYNI", interface="query_growth_data",
                                      value_kind="GROWTH", period_basis="COMPARATIVE",
                                      unit="倍", definition_note="净利润同比增长率（低基数/扭亏语义失真待标记）"),
    "YOYEPSBasic": FinancialFieldDefinition(metric="YOYEPSBasic", interface="query_growth_data",
                                            value_kind="GROWTH", period_basis="COMPARATIVE",
                                            unit="倍", definition_note="基本每股收益同比增长率"),
    "YOYPNI": FinancialFieldDefinition(metric="YOYPNI", interface="query_growth_data",
                                       value_kind="GROWTH", period_basis="COMPARATIVE",
                                       unit="倍", definition_note="扣非净利润同比增长率"),
}


def field_definition(metric: str) -> Optional[FinancialFieldDefinition]:
    """查字段登记；未登记返回 None（调用方决定跳过或告警）。"""
    return FIELD_DEFINITIONS.get(metric)


class UnitDriftMapping(BaseModel):
    """供应商单位漂移的版本化映射（J1 合同，DATA_DECISION §2：有适用范围、有证据、幂等）。

    合同要点（修复 N4）：
    - mapping_id 稳定标识——幂等判定与审计引用
    - canonical = raw × factor 只从原值算一次；相同 mapping 重复应用无变化
    - observed_scope = **已核定证券**（双样本不推全市场）；effective_period_range =
      [首个漂移报告期, 最后一个已观测报告期]——**不是无限报告期下界**（边界后新
      报告期=供应商恢复/再变更窗口，须重新核定）
    - verification_level：cross_checked_pending_original（跨供应商核定待原件）/
      verified_against_original（发行人原件核对）；adapter 版本不是供应商数据版本
    """

    mapping_id: str = Field(description="稳定映射 id（幂等判定/审计引用）")
    metric: str
    source_version: str
    source_schema: str = Field(default="", description="供应商响应 schema/adapter 标签（非供应商协议版本）")
    observed_scope: list[str] = Field(default_factory=list, description="已核定证券列表（空=不允许应用）")
    effective_period_range: tuple[str, str] = Field(description="[首个漂移报告期, 最后已观测报告期]（含边界，YYYY-MM-DD）")
    factor: float
    operation: Literal["multiply"] = "multiply"
    description: str
    evidence: list[str] = Field(default_factory=list, description="核验材料（探针日志/交叉核对记录）")
    evidence_hashes: list[str] = Field(default_factory=list, description="证据内容 sha256（可复现查询）")
    corrected_definition_version: str
    verification_level: Literal["cross_checked_pending_original",
                                "verified_against_original"] = "cross_checked_pending_original"
    registered_at: str


def _hash_evidence(evidence: list[str]) -> list[str]:
    """证据条目内容 hash（J1：query 和证据 hash 可复现）。"""
    import hashlib as _hl
    return [_hl.sha256(str(e).encode("utf-8")).hexdigest() for e in evidence]


# ISS-114 版本化单位漂移映射（2026-09-27 探针实证，tests/data_sources/probe_fin_semantics.py）：
# baostock liabilityToAsset 自 2024-06-30 报告期起对已核定样本统一 ÷100（万科A/贵州茅台
# 双样本一致，与东财资产负债表绝对值按定义重算交叉核对——2024-03-31 及以前逐期吻合
# （差 <1e-4）、2024-06-30 起全部偏差 ×100）。适用范围 = 该字段×该源×**已核定证券**
# ×已观测报告期区间；其余字段/证券/期间不动（J1 范围门）；待逐份发行人原始财报确认
# 前不称 VERIFIED（verification_level 如实登记）。映射修订 = 追加新条目，不覆盖本条。
UNIT_DRIFT_MAPPINGS: list[UnitDriftMapping] = [
    UnitDriftMapping(
        mapping_id="umd_iss114_liability_v1",
        metric="liabilityToAsset",
        source_version=FINANCIAL_SOURCE_VERSION,
        source_schema="baostock query_balance_data（本项目 adapter 标签 baostock_financial_v1，"
                      "非供应商数据协议版本）",
        observed_scope=["000002", "600519"],
        effective_period_range=("2024-06-30", "2024-12-31"),
        factor=100.0,
        description="baostock 该字段自 2024-06-30 报告期起对已核定样本单位由「小数比值」变为"
                    "「百分数/100」——canonical=raw×100；适用范围仅此字段此源此证券范围此观测区间"
                    "（ISS-114；双样本不推全市场）",
        evidence=[
            "plan/fusion/iteration2/evidence/2026-09-27_ISS114探针摘录.md（入库证据："
            "关键数字+留样样例+复现口令）",
            "tests/artifacts/probe_fin_semantics.log（2026-09-27 探针全日志，本地保留）"
            "：双样本逐期交叉核定",
            "东财资产负债表绝对值按定义重算（负债/资产，同报告期同合并口径）——"
            "2024-03-31 及以前与 baostock 原值差 <1e-4，2024-06-30 起恒差 ×100",
        ],
        evidence_hashes=_hash_evidence([
            "plan/fusion/iteration2/evidence/2026-09-27_ISS114探针摘录.md",
            "tests/artifacts/probe_fin_semantics.log",
            "东财重算交叉核对记录（2024Q1 前差<1e-4；2024Q2 起恒差×100）",
        ]),
        corrected_definition_version="baostock_field_registry_v1+iss114_driftfix",
        verification_level="cross_checked_pending_original",
        registered_at="2026-09-27",
    ),
]


def _mapping_scope_state(m: UnitDriftMapping, fin: dict) -> str:
    """fin 相对映射适用范围的状态（J1 范围门）：
    - "apply"：period ∈ 观测区间 且 security 已核定 → 应用
    - "pre_boundary"：period 早于漂移边界（已核定正常）→ 不动不加噪声
    - "out_of_scope"：边界后新报告期（供应商恢复/再变更窗口）或证券未核定 → 隔离
    """
    period = str(fin.get("period_end") or "")
    if not period:
        return "pre_boundary"  # 无报告期无从判定——维持旧行为（不应用不注记）
    if period < m.effective_period_range[0]:
        return "pre_boundary"
    sec = str(fin.get("security_id") or "")
    if not sec or sec not in m.observed_scope:
        return "out_of_scope"
    if period > m.effective_period_range[1]:
        return "out_of_scope"  # 超出已观测区间——供应商可能恢复/再变更，须重新核定
    return "apply"


def apply_unit_drift_mapping(fin: dict) -> dict:
    """对命中适用范围的 fin dict 应用版本化映射（J1 合同，修复 N4）：

    - **幂等**：已带同 mapping_id 标记、或 raw_value 在且 value==raw×factor
      （标记丢失兜底）→ 原样返回——重复应用不再 ×100，raw 不变
    - **范围门**：仅 已核定证券 × 已观测报告期区间 内应用；边界前已核定正常值
      原样返回不加噪声；范围外（证券未核定/边界后新报告期）→ **不自动换算**，
      置 semantic_status="SUSPECT" 隔离待人工（不按所有未来报告期无限乘）
    - **冲突**：多个映射同时命中 → SUSPECT，不任选其一静默换算
    - **透明**：value=canonical；raw_value/raw_unit 保供应商原貌；semantic_note 带
      mapping_id 与证据指针——不是静默改数。已 canonical 但丢失 raw 的记录不做
      猜测还原（DATA_DECISION §2.1）
    """
    matches = [m for m in UNIT_DRIFT_MAPPINGS
               if fin.get("metric") == m.metric
               and fin.get("source_version") == m.source_version
               and fin.get("value") is not None]
    if not matches:
        return fin
    # 幂等：已应用（标记或 raw 比对兜底）→ 原样返回
    for m in matches:
        if fin.get("unit_drift_mapping_id") == m.mapping_id:
            return fin
        raw_v, v = fin.get("raw_value"), fin.get("value")
        if (isinstance(raw_v, (int, float)) and not isinstance(raw_v, bool)
                and isinstance(v, (int, float)) and not isinstance(v, bool)
                and raw_v != 0 and abs(v - raw_v * m.factor) <= 1e-9 * max(1.0, abs(v))):
            return fin
    # 范围门
    applicable = [m for m in matches if _mapping_scope_state(m, fin) == "apply"]
    if len(applicable) > 1:
        return {**fin,
                "semantic_status": "SUSPECT",
                "semantic_note": (f"单位漂移映射冲突（同时命中 "
                                  f"{sorted(m.mapping_id for m in applicable)}）"
                                  "——不任选换算，隔离待人工核对")}
    if not applicable:
        if any(_mapping_scope_state(m, fin) == "out_of_scope" for m in matches):
            return {**fin,
                    "semantic_status": "SUSPECT",
                    "semantic_note": ("单位漂移映射范围外（证券未核定或报告期超出已观测区间，"
                                      "供应商可能恢复/再变更口径）——不自动换算，隔离待人工核对")}
        return fin  # 边界前已核定正常值——原样返回，不加噪声
    m = applicable[0]
    return {**fin,
            "value": fin["value"] * m.factor,
            "raw_value": fin.get("value"),
            "raw_unit": fin.get("unit", "元"),
            "unit_drift_mapping_id": m.mapping_id,
            "semantic_note": (
                f"单位漂移映射已应用（{m.mapping_id}；{m.description}；"
                f"证据: {'; '.join(m.evidence)}）——raw_value 为供应商原值；"
                f"{m.verification_level}"),
            "metric_definition_version": m.corrected_definition_version}


def select_ended_quarters(as_of: datetime, n: int = 4) -> list[tuple[int, int]]:
    """选最近 n 个**已结束**的报告期（J1b：不含未结束当季——不浪费请求；
    「已结束且已披露/可获得」——披露证明仍以 pubDate 为准，无公布日不产证据）。
    返回 [(year, quarter)] 由新到旧。"""
    q = (as_of.month - 1) // 3 + 1  # 当季按未结束处理（即使季末当日，披露也不可得）
    out: list[tuple[int, int]] = []
    y, qq = as_of.year, q
    for _ in range(max(0, int(n))):
        qq -= 1
        if qq == 0:
            y, qq = y - 1, 4
        out.append((y, qq))
    return out


def register_mapping_affected(store, mapping: UnitDriftMapping,
                              affected_refs: Optional[list[dict]] = None) -> dict:
    """映射修订/启用时登记受影响派生清单（DATA_DECISION §2.6：旧原件/快照/实验
    不覆盖，已确认账户事实不撤销——只登记影响清单供各层追溯）。"""
    return store.append_invalidation(
        cause_kind="unit_drift",
        metric=mapping.metric,
        period_end=mapping.effective_period_range[0],
        description=(f"单位漂移映射 {mapping.mapping_id} 登记生效"
                     f"（范围={mapping.observed_scope}×"
                     f"{mapping.effective_period_range[0]}..{mapping.effective_period_range[1]}；"
                     f"{mapping.description}）"),
        affected_refs=list(affected_refs or []))


def _to_baostock_code(stock_code: str) -> str:
    """600519 → sh.600519（复用既有规范化，拒绝空/非法码）。"""
    from src.data.akshare_client import AKShareClient
    exchange, code = AKShareClient._normalize_stock_code(str(stock_code).strip())
    if not code or not code.isdigit() or len(code) != 6:
        raise ValueError(f"非法股票代码: {stock_code!r}")
    return f"{exchange}.{code}"


def _query_one(fn_name: str, bs_code: str, year: int, quarter: int,
               timeout: int) -> dict | None:
    """单接口查询 → {field: value}；超时/空返回返回 None 并告警（不抛冻结）。"""
    import baostock as bs

    def _read():
        rs = getattr(bs, fn_name)(code=bs_code, year=year, quarter=quarter)
        fields = list(rs.fields)
        if rs.error_code != "0":
            logger.warning(
                f"Baostock 财务季频接口返回错误 {fn_name} {bs_code} {year}Q{quarter}: "
                f"{rs.error_code} {rs.error_msg}")
            return None
        while rs.next():
            return dict(zip(fields, rs.get_row_data()))
        return None

    try:
        return _call_with_timeout(_read, timeout=timeout)
    except _FuturesTimeout:
        logger.warning(f"Baostock 财务季频读取超时 {fn_name} {bs_code} {year}Q{quarter}")
        return None


def get_financial_quarterly(stock_code: str, year: int, quarter: int, *,
                            timeout: int = 30) -> list[dict]:
    """五接口 → research_snapshot.financial_record 可直接消费的 fin dict 列表。

    返回逐字段证据 dict：{metric, value, unit, period_kind="cumulative",
    period_end(=statDate), published_at(=pubDate), source_uri, source_version}。
    单接口失败只缺该接口字段（告警留痕），不拖垮其余接口。
    """
    bs_code = _to_baostock_code(stock_code)
    if not _ensure_baostock_login():
        logger.warning(f"Baostock 登录失败，财务季频证据跳过 {bs_code} {year}Q{quarter}")
        return []
    out: list[dict] = []
    for fn_name, fields in _INTERFACE_FIELDS.items():
        try:
            row = _query_one(fn_name, bs_code, year, quarter, timeout)
        except Exception as e:
            # 最后防线：_query_one 之外的意外异常（登录失效等）不拖垮其余接口——
            # 告警留痕按缺失降级，不是静默吞（单接口失败只缺该接口字段）
            logger.warning(f"Baostock 财务季频接口异常 {fn_name} {bs_code} {year}Q{quarter}: {e}")
            continue
        if row is None:
            continue  # 超时/错误已告警；缺该接口全部字段
        pub = row.get("pubDate") or None
        stat = row.get("statDate") or None
        if not pub:
            # 无公布日 = available_at 判据缺失——该期字段全部不产证据（不进严格快照，
            # G15：缺判据不造假）；statDate 留痕便于人工核对
            logger.warning(
                f"Baostock 财务季频无公布日 {fn_name} {bs_code} {year}Q{quarter} "
                f"(statDate={stat})——该期不产生证据")
            continue
        for field, unit in fields:
            raw = row.get(field)
            if raw is None:
                # 字段名与登记表漂移（baostock 改版征兆）——显式告警，不产 None 值假证据
                logger.warning(
                    f"Baostock 财务季频字段缺失 {fn_name}.{field} {bs_code} {year}Q{quarter}"
                    "——登记表与接口实际字段不一致，请核对 _INTERFACE_FIELDS")
                continue
            definition = FIELD_DEFINITIONS.get(field)
            if definition is None:
                # 语义登记缺失（R1：value_kind/period_basis 未登记的字段不产证据——
                # 派生规则分派不了就拒绝，不猜口径）
                logger.warning(
                    f"Baostock 财务字段未登记语义 {fn_name}.{field} {bs_code} {year}Q{quarter}"
                    "——请补 FIELD_DEFINITIONS 后再启用")
                continue
            if definition.unit != unit:
                logger.warning(
                    f"Baostock 字段单位登记不一致 {fn_name}.{field}: 接口表 {unit!r} vs "
                    f"语义表 {definition.unit!r}——以语义表为准，请核对登记表")
                unit = definition.unit
            value = None
            if raw != "":
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None  # 畸形值按缺失处理，不猜
            out.append({
                "metric": field,
                "security_id": str(stock_code),  # J1 范围门判定需要（映射按证券核定）
                "value": value,
                "unit": unit,
                "period_kind": "cumulative",  # baostock 季频=年初累计（实测口径；兼容旧读取）
                "period_basis": definition.period_basis,  # R1 语义登记（旧 cumulative 不自动推断）
                "value_kind": definition.value_kind,
                "underlying_period_basis": definition.underlying_period_basis,
                "metric_definition_version": FIELD_REGISTRY_VERSION,
                "period_end": stat,
                "published_at": pub,
                "source_uri": f"baostock.{fn_name}(code={bs_code},year={year},quarter={quarter})",
                "source_version": FINANCIAL_SOURCE_VERSION,
            })
    return out


def capture_quarterly_evidence(stock_code: str, year: int, quarter: int, *,
                               store=None, timeout: int = 30,
                               return_raw: bool = False,
                               skip_archived: bool = False):
    """采集一季财务证据：原始响应留样 → fin dict → EvidenceRecord（R1）。

    - store（ResearchStore）给定：五接口原始响应逐字段归档（写一次幂等——同内容
      重复采集不覆盖原留样），metadata 带接口参数/查询时点/源版本/报告期/字段名
      （DATA_TRUST §2 链路1）；观测索引逐字段登记（含命中的 unit_drift_mapping_id）
    - store=None：不落盘（纯函数路径，回测安全）
    - skip_archived=True（J1b 零重复抓取）：该季证据已在观测索引归档时直接返回
      空列表不发请求——相同归档离线可读（store=None 时无判据，忽略此参数）
    - 失败恢复：单接口失败仅缺该接口字段（既有纪律）；幂等重跑安全
    - **语义筛查不在此处**：相邻期量级跳变（ISS-114）需要跨季度累积比较——
      调用方把多期记录累积后调 research_snapshot.screen_semantic_anomalies，
      再进快照/资格判定（probe_fin_semantics.py 与 R3 服务层按此接线）
    - return_raw=True 时返回 (records, raw_fin_dicts)：raw=供应商原貌 dict
      （factor_compute._get 消费 "metric" 键——EvidenceRecord 用 metric_or_claim，
      因子计算不得用 model_dump 喂【监督员 P1 形状错配修正】）
    """
    from src.data.research_snapshot import financial_record
    if store is not None and skip_archived and store.has_quarter_evidence(
            str(stock_code), year, quarter):
        logger.info(f"财务季频证据已归档，跳过重复抓取 {stock_code} {year}Q{quarter}")
        return ([], []) if return_raw else []
    fin_dicts = get_financial_quarterly(stock_code, year, quarter, timeout=timeout)
    raw_digests: list[str] = []
    if store is not None:
        query_meta = {
            "kind": "baostock_financial_quarterly",
            "stock_code": str(stock_code),
            "bs_code": _to_baostock_code(stock_code),
            "year": year, "quarter": quarter,
            "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_version": FINANCIAL_SOURCE_VERSION,
            "field_registry_version": FIELD_REGISTRY_VERSION,
        }
        # 留样=供应商原貌（映射前归档——原始响应不可篡改，DATA_TRUST §2）
        for d in fin_dicts:
            digest, _created = store.archive_raw(d, metadata={
                **query_meta, "interface": d["source_uri"], "metric": d["metric"],
                "period_end": d.get("period_end"), "published_at": d.get("published_at")})
            raw_digests.append(digest)
    mapped = [apply_unit_drift_mapping(d) for d in fin_dicts]
    if store is not None:
        # 观测索引登记映射后状态（含命中的 unit_drift_mapping_id/隔离标记——追溯可查）
        for d, digest in zip(mapped, raw_digests):
            store.append_observation({
                "kind": "financial_quarterly", "security_id": str(stock_code),
                "metric": d["metric"], "period_end": d.get("period_end"),
                "value": d.get("value"), "unit": d.get("unit"),
                "raw_sha256": digest,
                "source_version": FINANCIAL_SOURCE_VERSION,
                "semantic_status": d.get("semantic_status", ""),
                "unit_drift_mapping_id": d.get("unit_drift_mapping_id", ""),
            })
    records = [financial_record(str(stock_code), d) for d in mapped]
    if return_raw:
        return records, mapped  # raw=映射后 fin dict（factor_compute._get 消费 "metric" 键）
    return records
