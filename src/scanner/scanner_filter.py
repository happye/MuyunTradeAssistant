"""声明式扫描过滤器 - Scanner初筛

与Skill Engine的CONDITION_REGISTRY独立：
- Skill Engine: 作用于StockData（含技术指标），52个条件
- ScannerFilter: 作用于DataFrame行情行（仅实时行情字段），field/op/value三元组

原因：Scanner初筛只有行情数据，没有K线和技术指标。
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class ScannerFilter:
    """声明式扫描过滤器

    解析 scan_rules.yaml 中的 filters 配置（field/op/value 三元组），
    对 DataFrame 行情数据执行批量过滤。

    用法：
        filtered_df = ScannerFilter.apply(df, filters)
        errors = ScannerFilter.validate_filters(filters)
    """

    # Scanner字段名 → AKShare DataFrame列名 映射
    # 基于 ak.stock_zh_a_spot_em() 实际返回的列名（2026-05-05验证）
    FIELD_MAP: dict[str, str] = {
        "change_pct": "涨跌幅",       # 涨跌幅(%)
        "turnover_rate": "换手率",     # 换手率(%)
        "volume_ratio": "量比",        # 量比
        "amplitude": "振幅",           # 振幅(%)
        "price": "最新价",             # 最新价
        "volume": "成交量",            # 成交量
        "amount": "成交额",            # 成交额
        "pe_ratio": "市盈率-动态",     # 市盈率
        "pb_ratio": "市净率",          # 市净率
        "total_mv": "总市值",          # 总市值
        "circ_mv": "流通市值",         # 流通市值
        "change_speed": "涨速",        # 涨速
        "change_5min": "5分钟涨跌",    # 5分钟涨跌
        "change_60d": "60日涨跌幅",    # 60日涨跌幅
        "change_ytd": "年初至今涨跌幅", # 年初至今涨跌幅
        # 特殊字段（不在DataFrame列中，需特殊处理）
        "code": "代码",               # 股票代码
        "name": "名称",               # 股票名称
    }

    # 支持的操作符
    VALID_OPS = frozenset({
        "gt", "gte", "lt", "lte", "eq", "neq",
        "between", "in", "not_in", "not_like", "like",
    })

    @classmethod
    def apply(cls, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        """对DataFrame应用一组过滤器

        Args:
            df: 全市场行情DataFrame
            filters: 过滤器列表，每项格式: {field, op, value}

        Returns:
            过滤后的DataFrame（所有filter取交集AND逻辑）
        """
        if not filters:
            return df

        result = df.copy()
        initial_count = len(result)

        for f in filters:
            result = cls._apply_single(result, f)
            if result.empty:
                logger.info(f"ScannerFilter: 过滤器 {f} 将结果清空，提前终止")
                break

        final_count = len(result)
        logger.info(
            f"ScannerFilter: {initial_count}只 → {final_count}只 "
            f"({len(filters)}个过滤器)"
        )
        return result

    @classmethod
    def _apply_single(cls, df: pd.DataFrame, f: dict) -> pd.DataFrame:
        """应用单个过滤器

        Args:
            df: 待过滤的DataFrame
            f: 过滤器配置 {field, op, value}

        Returns:
            过滤后的DataFrame
        """
        field = f.get("field", "")
        op = f.get("op", "")
        value = f.get("value")

        # 字段名映射
        col_name = cls.FIELD_MAP.get(field)
        if not col_name:
            logger.warning(f"ScannerFilter: 未知字段 '{field}'，跳过")
            return df

        # 检查列是否存在
        if col_name not in df.columns:
            logger.warning(f"ScannerFilter: DataFrame中无列 '{col_name}'(字段:{field})，跳过")
            return df

        # 确保数值列为float类型（AKShare有时返回object类型）
        series = pd.to_numeric(df[col_name], errors="coerce")

        try:
            if op == "gt":
                return df[series > value]
            elif op == "gte":
                return df[series >= value]
            elif op == "lt":
                return df[series < value]
            elif op == "lte":
                return df[series <= value]
            elif op == "eq":
                return df[series == value]
            elif op == "neq":
                return df[series != value]
            elif op == "between":
                # value = [min, max]
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    return df[(series >= value[0]) & (series <= value[1])]
                else:
                    logger.warning(f"ScannerFilter: between操作需要[min, max]，收到 {value}")
                    return df
            elif op == "in":
                if isinstance(value, (list, tuple)):
                    return df[series.isin(value)]
                else:
                    return df[series == value]
            elif op == "not_in":
                if isinstance(value, (list, tuple)):
                    return df[~series.isin(value)]
                else:
                    return df[series != value]
            elif op == "like":
                # 字符串模糊匹配（用于name字段）
                return df[df[col_name].astype(str).str.contains(str(value), na=False)]
            elif op == "not_like":
                # 字符串排除匹配
                return df[~df[col_name].astype(str).str.contains(str(value), na=False)]
            else:
                logger.warning(f"ScannerFilter: 未知操作符 '{op}'，跳过")
                return df

        except Exception as e:
            logger.warning(f"ScannerFilter: 过滤器执行失败 {f}: {e}")
            return df

    @classmethod
    def validate_filters(cls, filters: list[dict]) -> list[str]:
        """校验过滤器配置合法性

        Args:
            filters: 过滤器列表

        Returns:
            错误信息列表（空列表表示全部合法）
        """
        errors = []

        for i, f in enumerate(filters):
            # 检查必要字段
            if "field" not in f:
                errors.append(f"过滤器#{i}: 缺少field字段")
                continue
            if "op" not in f:
                errors.append(f"过滤器#{i}: 缺少op字段")
                continue

            field = f["field"]
            op = f["op"]

            # 检查字段名
            if field not in cls.FIELD_MAP:
                errors.append(
                    f"过滤器#{i}: 未知字段 '{field}'，"
                    f"可用字段: {list(cls.FIELD_MAP.keys())}"
                )

            # 检查操作符
            if op not in cls.VALID_OPS:
                errors.append(
                    f"过滤器#{i}: 未知操作符 '{op}'，"
                    f"可用操作符: {sorted(cls.VALID_OPS)}"
                )

            # 检查between的value格式
            if op == "between":
                value = f.get("value")
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    errors.append(
                        f"过滤器#{i}: between操作需要value为[min, max]列表"
                    )

        return errors

    @classmethod
    def apply_global_exclude(cls, df: pd.DataFrame, excludes: list[dict]) -> pd.DataFrame:
        """应用全局排除规则

        语义：排除满足条件的行（即"不匹配的保留"）。
        与普通 filter 的区别：
        - filter: 保留满足条件的行（正向过滤）
        - exclude: 排除满足条件的行（反向过滤）

        支持的操作符（语义为"排除满足此条件的行"）：
        - contains: 字段包含指定字符串 → 排除
        - starts_with: 字段以指定字符串开头 → 排除
        - eq: 字段等于指定值 → 排除
        - lt/lte/gt/gte: 数值比较 → 排除
        - is_nan: 字段为NaN → 排除

        Args:
            df: 全市场行情DataFrame
            excludes: 排除规则列表

        Returns:
            排除后的DataFrame
        """
        if not excludes:
            # 默认排除规则（即使YAML中未配置也执行）
            result = df.copy()
            if "名称" in result.columns:
                result = result[~result["名称"].astype(str).str.contains("ST", na=False)]
            if "成交量" in result.columns:
                vol = pd.to_numeric(result["成交量"], errors="coerce")
                result = result[vol > 0]
            return result

        result = df.copy()
        for exc in excludes:
            field = exc.get("field", "")
            op = exc.get("op", "")
            value = exc.get("value")

            col_name = cls.FIELD_MAP.get(field)
            if not col_name or col_name not in result.columns:
                continue

            if op == "contains":
                # 排除：字段包含指定字符串
                mask = result[col_name].astype(str).str.contains(str(value), na=False)
                result = result[~mask]
            elif op == "starts_with":
                # 排除：字段以指定字符串开头
                mask = result[col_name].astype(str).str.startswith(str(value), na=False)
                result = result[~mask]
            elif op == "eq":
                # 排除：字段等于指定值
                series = pd.to_numeric(result[col_name], errors="coerce")
                result = result[series != value]
            elif op in ("lt", "lte", "gt", "gte"):
                # 排除：数值比较（如 volume lte 0 → 排除成交量<=0的）
                series = pd.to_numeric(result[col_name], errors="coerce")
                if op == "lt":
                    result = result[~(series < value)]
                elif op == "lte":
                    result = result[~(series <= value)]
                elif op == "gt":
                    result = result[~(series > value)]
                elif op == "gte":
                    result = result[~(series >= value)]
            elif op == "is_nan":
                # 排除：字段为NaN
                series = pd.to_numeric(result[col_name], errors="coerce")
                result = result[~series.isna()]
            else:
                logger.warning(f"ScannerFilter: 全局排除不支持操作符 '{op}'，跳过")

        return result
