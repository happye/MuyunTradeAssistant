"""baostock 季频财务接口 → 研究证据层窄适配（plan/fusion 影子批后续，DATA_COVERAGE「财务三表」接线）。

探查锚定（2026-09-26，tests/data_sources/probe_fin_pubdate.py + 贵州茅台 2023Q4/2024Q1 实测）：
- 五接口（query_profit/balance/cash_flow/operation/growth_data）均带 pubDate 官方公布日
  （2023Q4→2024-04-03；2024Q1→2024-04-27）——PIT 判据成立
- **数值口径实测**：货币字段为元（netProfit≈775 亿量级 ✓）；比例字段为**小数比值**
  （roeAvg 0.3618 = 36.18%——baostock 文档标 % 但实测非百分数，unit 一律登记"倍"，
  不做二次换算）；缺失值是空串 ""，映射 None（绝不补 0 假装存在）
- 全部为**年初累计口径**（Q4 累计=年报；Q1 累计=单季）——period_kind="cumulative"
  显式登记；单季值须由相邻累计差分得出（下游关注点，本模块不做差分不猜口径）
- **重述限制（诚实登记）**：接口只返回最新一版（同期重复查询一致），后发重述与首发
  不可区分——VALIDATION §2「重述版本可区分」部分满足（首次公布日可得、重述不可分）；
  严格 PIT 回放消费财务证据须接受该限制（E2/E4 研究用途已登记）

网络纪律：baostock 读取走 _call_with_timeout 30s 硬超时（防冻结，同 data_feeder 口径）；
本模块只服务 **live 证据抓取**——回测路径不调用（财务证据经 research_snapshot 严格
PIT 闸门进快照，回放从快照读而非实时拉）。
"""

import logging
from concurrent.futures import TimeoutError as _FuturesTimeout

from src.data.akshare_client import _call_with_timeout, _ensure_baostock_login

logger = logging.getLogger(__name__)

FINANCIAL_SOURCE_VERSION = "baostock_financial_v1"

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
            value = None
            if raw != "":
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None  # 畸形值按缺失处理，不猜
            out.append({
                "metric": field,
                "value": value,
                "unit": unit,
                "period_kind": "cumulative",  # baostock 季频=年初累计（实测口径）
                "period_end": stat,
                "published_at": pub,
                "source_uri": f"baostock.{fn_name}(code={bs_code},year={year},quarter={quarter})",
                "source_version": FINANCIAL_SOURCE_VERSION,
            })
    return out
