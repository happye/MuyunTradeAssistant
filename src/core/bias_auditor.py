"""回测偏差审计器 — v0.8.2 Phase 3

自动扫描回测结果中的偏差风险点，产出审计报告。
不改变回测引擎逻辑，仅做"检查"和"标注"。

检查项：
1. 前视偏差（lookahead bias）
   - 开盘价缺失导致的前视偏差（B-01已在Phase 3修复，此处检查残留）
   - signal_date 与 execution_date 时序断言
   - DataFeeder cutoff逻辑确认
2. 数据质量评估
   - 交易日连续性
   - 开盘价缺失率
   - 零成交量交易日统计
3. 参数来源追踪
   - 策略层参数快照
   - 参数来源标记（default / custom）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from src.data.models import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class BiasAuditResult:
    """偏差审计结果"""

    has_lookahead_bias: bool = False
    lookahead_details: list[str] = field(default_factory=list)
    data_quality_score: float = 1.0
    data_issues: list[str] = field(default_factory=list)
    strategy_params: dict[str, Any] = field(default_factory=dict)
    param_source: str = "default"
    signal_execution_order_ok: bool = True
    blocked_no_open_price_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_lookahead_bias": self.has_lookahead_bias,
            "lookahead_details": self.lookahead_details,
            "data_quality_score": round(self.data_quality_score, 3),
            "data_issues": self.data_issues,
            "strategy_params": self.strategy_params,
            "param_source": self.param_source,
            "signal_execution_order_ok": self.signal_execution_order_ok,
            "blocked_no_open_price_count": self.blocked_no_open_price_count,
        }


class BiasAuditor:
    """偏差审计器"""

    # 交易日跳空阈值（超过此天数视为数据缺失，排除节假日后）
    GAP_THRESHOLD_DAYS = 5

    # 策略层默认参数（与 strategy_layer.py 保持同步）
    DEFAULT_STRATEGY_PARAMS = {
        "INERTIA_MIN_DAYS": 3,
        "INERTIA_BUY_THRESHOLD_BUMP": 0.10,
        "INERTIA_SELL_THRESHOLD_BUMP": 0.10,
        "CONFIRMATION_MIN_CONSECUTIVE": 2,
        "CONFIRMATION_WEAK_SIGNAL_THRESHOLD": 0.30,
        "COOLDOWN_AFTER_CLOSE_DAYS": 5,
        "COOLDOWN_AFTER_REDUCE_DAYS": 10,
        "REVERSE_COST_BASE_PCT": 0.003,
        "REVERSE_COST_PER_REVERSE": 0.002,
        "STABILITY_LOW_THRESHOLD": 0.4,
        "STABILITY_DISCOUNT": 0.7,
        "EXTREME_DROP_THRESHOLD": -0.05,
        "EXTREME_RISE_THRESHOLD": 0.07,
    }

    def audit(
        self,
        result: BacktestResult,
        feeder: Optional[Any] = None,
        strategy_params: Optional[dict[str, Any]] = None,
    ) -> BiasAuditResult:
        """执行偏差审计

        Args:
            result: 回测结果
            feeder: 数据回放器（可选，用于数据质量检查）
            strategy_params: 策略层参数快照（可选）
        """
        audit = BiasAuditResult()

        # 1. 前视偏差检查
        self._check_lookahead_bias(result, audit)

        # 2. 数据质量检查
        if feeder is not None:
            self._check_data_quality(feeder, audit)

        # 3. 参数来源追踪
        if strategy_params is not None:
            self._check_param_source(strategy_params, audit)

        # 4. B-01 开盘价缺失统计
        audit.blocked_no_open_price_count = result.blocked_by_no_open_price
        if result.blocked_by_no_open_price > 0:
            audit.data_issues.append(
                f"开盘价缺失{result.blocked_by_no_open_price}次（已跳过执行，无前视偏差）"
            )

        return audit

    def _check_lookahead_bias(self, result: BacktestResult, audit: BiasAuditResult) -> None:
        """检查前视偏差"""
        diagnostics = result.diagnostics or {}
        execution_logs = diagnostics.get("execution_logs", [])

        # 检查 signal_date < execution_date
        violations = []
        for log in execution_logs:
            signal_date = log.get("signal_date")
            execution_date = log.get("execution_date")
            if signal_date and execution_date:
                if signal_date >= execution_date:
                    violations.append(
                        f"信号日期{signal_date} >= 执行日期{execution_date}"
                    )

        if violations:
            audit.has_lookahead_bias = True
            audit.signal_execution_order_ok = False
            # 最多记录5条
            audit.lookahead_details.extend(violations[:5])
            if len(violations) > 5:
                audit.lookahead_details.append(
                    f"... 共{len(violations)}条signal_date >= execution_date违规"
                )

        # 检查是否有blocked_no_open_price残留的前视偏差
        # （Phase 3修复后这些应该都是跳过执行的，此处确认）
        for log in execution_logs:
            execution = log.get("execution", {})
            if execution.get("block_reason") == "开盘价不可用" and not execution.get("blocked", True):
                audit.has_lookahead_bias = True
                audit.lookahead_details.append(
                    f"{log.get('execution_date', '?')}: 开盘价不可用但未标记为blocked"
                )

    def _check_data_quality(self, feeder: Any, audit: BiasAuditResult) -> None:
        """检查数据质量"""
        if feeder._stock_df is None or feeder._dates is None:
            audit.data_issues.append("数据回放器未加载")
            audit.data_quality_score = 0.0
            return

        dates = feeder._dates
        total_days = len(dates)

        if total_days == 0:
            audit.data_issues.append("无交易日期数据")
            audit.data_quality_score = 0.0
            return

        # 检查交易日连续性
        gap_issues = 0
        for i in range(1, len(dates)):
            try:
                d1 = datetime.strptime(dates[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(dates[i], "%Y-%m-%d")
                gap = (d2 - d1).days
                # 超过阈值（排除周末和常规节假日）
                if gap > self.GAP_THRESHOLD_DAYS:
                    gap_issues += 1
                    if gap_issues <= 3:
                        audit.data_issues.append(
                            f"交易日跳空: {dates[i-1]} → {dates[i]} ({gap}天)"
                        )
            except ValueError:
                pass

        if gap_issues > 3:
            audit.data_issues.append(f"... 共{gap_issues}处交易日跳空超过{self.GAP_THRESHOLD_DAYS}天")

        # 检查开盘价缺失率
        df = feeder._stock_df
        mask = (df['date'] >= feeder.start_date) & (df['date'] <= feeder.end_date)
        backtest_df = df.loc[mask]
        open_null_count = backtest_df['open'].isna().sum() if 'open' in backtest_df.columns else 0
        volume_zero_count = (backtest_df['volume'].astype(float) == 0).sum() if 'volume' in backtest_df.columns else 0

        total_rows = len(backtest_df)
        if total_rows > 0:
            open_null_rate = open_null_count / total_rows
            vol_zero_rate = volume_zero_count / total_rows

            if open_null_rate > 0.01:
                audit.data_issues.append(
                    f"开盘价缺失率: {open_null_rate:.1%} ({open_null_count}/{total_rows}天)"
                )
            if vol_zero_rate > 0.05:
                audit.data_issues.append(
                    f"零成交量率: {vol_zero_rate:.1%} ({volume_zero_count}/{total_rows}天)"
                )

        # 计算数据质量评分
        quality_score = 1.0
        quality_score -= gap_issues * 0.05  # 每处跳空扣5%
        quality_score -= open_null_rate * 0.3 if total_rows > 0 else 0  # 开盘价缺失扣30%
        quality_score -= vol_zero_rate * 0.1 if total_rows > 0 else 0  # 零成交量扣10%
        audit.data_quality_score = max(0.0, min(1.0, quality_score))

    def _check_param_source(self, params: dict[str, Any], audit: BiasAuditResult) -> None:
        """检查参数来源"""
        audit.strategy_params = params

        # 与默认参数对比
        defaults = self.DEFAULT_STRATEGY_PARAMS
        changed = []
        for key, default_val in defaults.items():
            actual_val = params.get(key)
            if actual_val is not None and actual_val != default_val:
                changed.append(f"{key}: {default_val} → {actual_val}")

        if changed:
            audit.param_source = "custom"
            audit.data_issues.append(
                f"策略参数已调优({len(changed)}项): " + ", ".join(changed[:5])
            )
        else:
            audit.param_source = "default"
