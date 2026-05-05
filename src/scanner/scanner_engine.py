"""Scanner引擎 - 全市场技术面初筛 + 深度分析调度

工作流程：
1. 加载 scan_rules.yaml（YAML声明式规则）
2. 通过 MarketCache 获取全市场行情数据
3. 执行全局排除（ST/停牌/北交所等）
4. 执行行业板块过滤（可选）
5. 执行技术面初筛（ScannerFilter，基于行情数据）
6. 产出候选股列表（ScanCandidate[]）
7. 对用户选择的候选股逐只走 Orchestrator.analyze() 六层深度分析

两步走模式：
- Step 1: quick_scan() 快速初筛 → 展示候选池
- Step 2: deep_analyze() 用户选择后逐只深度分析
"""

import os
import logging
from typing import Optional, Callable

import yaml
import pandas as pd

from src.scanner.market_cache import MarketCache
from src.scanner.scanner_filter import ScannerFilter
from src.data.models import ScanCandidate
from src.core.orchestrator import Orchestrator
from src.data.akshare_client import AKShareClient

logger = logging.getLogger(__name__)


class ScannerEngine:
    """Scanner引擎 — 全市场技术面初筛 → 候选股票池 → 逐只深度分析

    设计约束：
    - 初筛只用行情数据，不逐只拉K线
    - 候选股走Orchestrator深度分析时才逐只拉K线
    - 全市场数据缓存，避免重复请求
    - 两步走模式：初筛展示→用户选择→深度分析
    """

    def __init__(
        self,
        rules_path: str = "./src/scanner/scan_rules.yaml",
        skills_dir: str = "./src/skills",
        enabled_skills: list = None,
        signal_weights: dict = None,
        skill_types: dict = None,
        ai_config: dict = None,
        cache_ttl: int = 300,
    ):
        """初始化Scanner引擎

        Args:
            rules_path: 扫描规则YAML路径
            skills_dir: 技能目录
            enabled_skills: 启用的技能列表
            signal_weights: 信号权重
            skill_types: 技能类型映射
            ai_config: AI调节层配置
            cache_ttl: 行情缓存TTL(秒)
        """
        self.market_cache = MarketCache(ttl_seconds=cache_ttl)
        self.rules = self._load_rules(rules_path)
        self._rules_path = rules_path

        # 深度分析用的Orchestrator（延迟初始化，只在deep_analyze时创建）
        self._orchestrator = None
        self._orchestrator_args = {
            "skills_dir": skills_dir,
            "enabled_skills": enabled_skills,
            "signal_weights": signal_weights,
            "skill_types": skill_types,
            "ai_config": ai_config,
        }

    def _get_orchestrator(self) -> Orchestrator:
        """延迟初始化Orchestrator（避免不必要的import开销）"""
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(**self._orchestrator_args)
        return self._orchestrator

    def quick_scan(
        self,
        rule_name: str = "default",
        industry_filter: list[str] = None,
        exclude_codes: set[str] = None,
    ) -> tuple[list[ScanCandidate], dict]:
        """快速扫描（仅初筛，不深度分析）

        用缓存的全市场行情数据执行过滤，秒级返回。

        Args:
            rule_name: 使用的规则集名称
            industry_filter: 行业过滤白名单
            exclude_codes: 排除的股票代码集合

        Returns:
            (candidates, scan_info)
            candidates: ScanCandidate列表
            scan_info: 扫描元数据{rule_name, total_stocks, filtered, elapsed, cache_status}
        """
        import time
        start_time = time.time()

        # 获取规则
        rule = self.rules.get("rules", {}).get(rule_name)
        if not rule:
            logger.error(f"ScannerEngine: 规则 '{rule_name}' 不存在")
            return [], {"error": f"规则 '{rule_name}' 不存在"}

        # 获取全市场行情
        df = self.market_cache.get_all_stocks()
        if df.empty:
            return [], {"error": "全市场行情数据获取失败"}

        total_count = len(df)

        # 全局排除
        global_excludes = self.rules.get("global_exclude", [])
        df = ScannerFilter.apply_global_exclude(df, global_excludes)
        after_exclude = len(df)

        # 行业过滤（可选）
        if industry_filter:
            df = self._apply_industry_filter(df, industry_filter)
            after_industry = len(df)
        else:
            after_industry = after_exclude

        # 排除已持仓股票
        if exclude_codes:
            if "代码" in df.columns:
                df = df[~df["代码"].astype(str).isin(exclude_codes)]

        # 应用规则过滤器
        filters = rule.get("filters", [])
        df = ScannerFilter.apply(df, filters)

        # 排序
        sort_by = rule.get("sort_by", "change_pct")
        sort_desc = rule.get("sort_desc", True)
        col_name = ScannerFilter.FIELD_MAP.get(sort_by)
        if col_name and col_name in df.columns:
            df = df.sort_values(
                by=col_name,
                ascending=not sort_desc,
                na_position="last"
            )

        # 限制候选数量
        max_candidates = rule.get("max_candidates", 30)
        if len(df) > max_candidates:
            df = df.head(max_candidates)

        # 转换为 ScanCandidate 列表
        candidates = self._df_to_candidates(df, rule_name)

        elapsed = time.time() - start_time
        scan_info = {
            "rule_name": rule_name,
            "rule_display_name": rule.get("name", rule_name),
            "total_stocks": total_count,
            "after_exclude": after_exclude,
            "after_industry": after_industry,
            "filtered": len(df),
            "candidates_count": len(candidates),
            "elapsed_seconds": round(elapsed, 2),
            "cache_status": self.market_cache.get_cache_status(),
        }

        logger.info(
            f"ScannerEngine: 初筛完成 "
            f"规则={rule.get('name', rule_name)}, "
            f"{total_count}只→{len(candidates)}只, "
            f"{elapsed:.1f}秒"
        )

        return candidates, scan_info

    def deep_analyze(
        self,
        stock_codes: list[str],
        ai_enabled: bool = True,
        ai_debug: bool = False,
        progress_callback: Optional[Callable] = None,
    ) -> list[dict]:
        """对选定的候选股逐只执行深度分析（走Orchestrator六层）

        Args:
            stock_codes: 要深度分析的股票代码列表
            ai_enabled: 是否启用AI调节层
            ai_debug: 是否开启AI调试模式
            progress_callback: 进度回调 callback(step, current, total, message)

        Returns:
            分析结果列表，每项为 dict:
            {
                "stock_code": str,
                "stock_name": str,
                "success": bool,
                "decision_result": DecisionResult,
                "strategy_decision": StrategyDecision,
                "execution_eval": ExecutionEvaluation,
                "ai_result": Optional[AIModifierResult],
                "error": Optional[str],
            }
        """
        from src.data.portfolio import PortfolioManager

        results = []
        total = len(stock_codes)
        orchestrator = self._get_orchestrator()
        pm = PortfolioManager()

        for i, code in enumerate(stock_codes):
            if progress_callback:
                progress_callback("deep_analyze", i + 1, total, f"分析 {code}")

            result = {"stock_code": code, "success": False}

            try:
                # 获取完整技术指标数据
                stock_data = AKShareClient.calculate_indicators(code)
                if not stock_data:
                    # 降级：尝试仅获取行情
                    quote = AKShareClient.get_realtime_quote(code)
                    if quote:
                        result["stock_name"] = quote.get("stock_name", code)
                        result["error"] = "无法获取技术指标（K线数据缺失）"
                    else:
                        result["error"] = "无法获取行情数据"
                    results.append(result)
                    continue

                result["stock_name"] = stock_data.stock_name or code

                # 获取持仓状态（如果有）
                current_ratio = 0.0
                strategy_state = None
                positions = pm.list_positions()
                for pos in positions:
                    if pos.stock_code == code:
                        current_ratio = pos.current_ratio
                        strategy_state = pm.to_strategy_state(code)
                        break

                # 调用Orchestrator六层分析
                decision_result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(
                    data=stock_data,
                    current_position_ratio=current_ratio,
                    strategy_state=strategy_state,
                    ai_enabled=ai_enabled,
                )

                result.update({
                    "success": True,
                    "decision_result": decision_result,
                    "strategy_decision": strategy_decision,
                    "execution_eval": execution_eval,
                    "ai_result": ai_result,
                })

            except Exception as e:
                result["error"] = str(e)
                logger.warning(f"ScannerEngine: 深度分析 {code} 失败: {e}")

            results.append(result)

        success_count = sum(1 for r in results if r["success"])
        logger.info(
            f"ScannerEngine: 深度分析完成 "
            f"{success_count}/{total}只成功"
        )

        return results

    def get_available_rules(self) -> list[dict]:
        """获取可用的规则集列表

        Returns:
            [{"name": str, "display_name": str, "description": str}, ...]
        """
        rules_cfg = self.rules.get("rules", {})
        result = []
        for key, rule in rules_cfg.items():
            result.append({
                "name": key,
                "display_name": rule.get("name", key),
                "description": rule.get("description", ""),
            })
        return result

    def get_industry_list(self) -> list[dict]:
        """获取行业板块列表（含涨跌幅）

        Returns:
            [{"name": str, "change_pct": float}, ...]
        """
        df = self.market_cache.get_industry_boards()
        if df.empty:
            return []

        result = []
        # 查找列名
        name_col = None
        change_col = None
        for col in df.columns:
            if "板块" in col or "名称" in col:
                name_col = col
            if "涨跌幅" in col:
                change_col = col

        if name_col:
            for _, row in df.iterrows():
                item = {"name": str(row[name_col])}
                if change_col:
                    try:
                        item["change_pct"] = float(row[change_col])
                    except (ValueError, TypeError):
                        item["change_pct"] = 0.0
                result.append(item)

        return result

    def _load_rules(self, rules_path: str) -> dict:
        """加载扫描规则YAML

        Args:
            rules_path: YAML文件路径

        Returns:
            规则字典
        """
        if not os.path.exists(rules_path):
            logger.warning(f"ScannerEngine: 规则文件不存在 {rules_path}，使用空规则")
            return {"rules": {}}

        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                rules = yaml.safe_load(f)
            logger.info(f"ScannerEngine: 规则加载成功 ({len(rules.get('rules', {}))}个规则集)")
            return rules
        except Exception as e:
            logger.error(f"ScannerEngine: 规则加载失败: {e}")
            return {"rules": {}}

    def _apply_industry_filter(
        self, df: pd.DataFrame, industries: list[str]
    ) -> pd.DataFrame:
        """行业板块过滤

        通过行业板块的成分股列表过滤DataFrame。
        只保留属于指定行业的股票。

        Args:
            df: 全市场行情DataFrame
            industries: 行业名称列表

        Returns:
            过滤后的DataFrame
        """
        if not industries:
            return df

        # 收集所有指定行业的成分股代码
        all_codes = set()
        for industry_name in industries:
            codes = self.market_cache.get_stocks_by_industry(industry_name)
            all_codes.update(codes)

        if not all_codes:
            logger.warning(f"ScannerEngine: 行业过滤无结果，行业: {industries}")
            return df

        # 在DataFrame中过滤
        if "代码" in df.columns:
            result = df[df["代码"].astype(str).isin(all_codes)]
            logger.info(
                f"ScannerEngine: 行业过滤 "
                f"{industries} → {len(all_codes)}只成分股 → 匹配{len(result)}只"
            )
            return result

        return df

    def _df_to_candidates(
        self, df: pd.DataFrame, rule_name: str
    ) -> list[ScanCandidate]:
        """将DataFrame行转换为ScanCandidate列表

        Args:
            df: 过滤后的行情DataFrame
            rule_name: 匹配的规则名

        Returns:
            ScanCandidate列表
        """
        candidates = []
        for _, row in df.iterrows():
            candidate = ScanCandidate(
                stock_code=str(row.get("代码", "")),
                stock_name=str(row.get("名称", "")),
                price=self._safe_float(row.get("最新价")),
                change_pct=self._safe_float(row.get("涨跌幅")),
                turnover_rate=self._safe_float(row.get("换手率")),
                volume_ratio=self._safe_float(row.get("量比")),
                amplitude=self._safe_float(row.get("振幅")),
                amount=self._safe_float(row.get("成交额")),
                pe_ratio=self._safe_float(row.get("市盈率-动态")),
                pb_ratio=self._safe_float(row.get("市净率")),
                total_mv=self._safe_float(row.get("总市值")),
                matched_rules=[rule_name],
            )
            candidates.append(candidate)

        return candidates

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """安全转换为float，失败返回None"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
