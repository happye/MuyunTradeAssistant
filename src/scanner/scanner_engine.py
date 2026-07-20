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
import json
import logging
from difflib import SequenceMatcher
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
        entry_exit_config: dict = None,
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
            entry_exit_config: 买卖点计算器配置
        """
        self.market_cache = MarketCache(ttl_seconds=cache_ttl)
        self.rules = self._load_rules(rules_path)
        self._rules_path = rules_path
        self._ai_config = ai_config or {}
        self._match_client = None
        self._match_model = ""
        self._match_provider = ""
        self._init_match_client()

        # 深度分析用的Orchestrator（延迟初始化，只在deep_analyze时创建）
        self._orchestrator = None
        self._orchestrator_args = {
            "skills_dir": skills_dir,
            "enabled_skills": enabled_skills,
            "signal_weights": signal_weights,
            "skill_types": skill_types,
            "ai_config": ai_config,
            "entry_exit_config": entry_exit_config,
        }

    def _get_orchestrator(self) -> Orchestrator:
        """延迟初始化Orchestrator（避免不必要的import开销）"""
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(**self._orchestrator_args)
        return self._orchestrator

    def _init_match_client(self):
        """初始化用于板块/概念纠偏的AI客户端。"""
        if not self._ai_config or not self._ai_config.get("enabled", True):
            return

        provider = self._ai_config.get("provider", "deepseek")
        provider_cfg = self._ai_config.get(provider, {})
        api_key = provider_cfg.get("api_key") or os.environ.get(f"{provider.upper()}_API_KEY", "")
        base_url = provider_cfg.get("base_url", "")
        model = provider_cfg.get("model", "")

        if not api_key or not model:
            return

        try:
            from openai import OpenAI

            self._match_client = OpenAI(api_key=api_key, base_url=base_url)
            self._match_model = model
            self._match_provider = provider
        except Exception as exc:
            logger.debug(f"ScannerEngine: 初始化主题匹配AI失败: {exc}")

    def quick_scan(
        self,
        rule_name: str = "healthy_pullback",
        market_query: str = None,
        exclude_codes: set[str] = None,
    ) -> tuple[list[ScanCandidate], dict]:
        """快速扫描（仅初筛，不深度分析）

        用缓存的全市场行情数据执行过滤，秒级返回。
        rule_name 支持模糊匹配（如"缩量"→shrink_pullback, "动量"→strong_momentum）。

        Args:
            rule_name: 使用的规则集名称（支持模糊匹配）
            market_query: 统一主题词，自动匹配相关行业/概念并按并集过滤
            exclude_codes: 排除的股票代码集合

        Returns:
            (candidates, scan_info)
            candidates: ScanCandidate列表
            scan_info: 扫描元数据{rule_name, total_stocks, filtered, elapsed, cache_status}
        """
        import time
        start_time = time.time()

        # 模糊匹配规则名
        resolved = self.resolve_rule_name(rule_name)
        if not resolved:
            available = [r["name"] for r in self.get_available_rules()]
            display_names = [r["display_name"] for r in self.get_available_rules()]
            logger.error(f"ScannerEngine: 规则 '{rule_name}' 不存在")
            return [], {
                "error": f"规则 '{rule_name}' 不存在",
                "available_keys": available,
                "available_names": display_names,
            }

        # 获取规则
        rule = self.rules.get("rules", {}).get(resolved)
        # （resolved已验证存在，此处无需再检查）

        # 获取全市场行情
        df = self.market_cache.get_all_stocks()
        if df.empty:
            return [], {"error": "全市场行情数据获取失败"}

        total_count = len(df)

        # 全局排除
        global_excludes = self.rules.get("global_exclude", [])
        df = ScannerFilter.apply_global_exclude(df, global_excludes)
        after_exclude = len(df)

        theme_info = None
        if market_query:
            theme_info = self.resolve_market_theme(market_query)
            if not theme_info["industries"] and not theme_info["concepts"]:
                logger.warning(
                    f"ScannerEngine: 主题词 '{market_query}' 未匹配到行业/概念，回退为全市场规则扫描"
                )
                theme_info["fallback_unfiltered"] = True
                after_theme = after_exclude
                after_industry = after_exclude
                after_concept = after_exclude
            else:
                df = self._apply_market_theme_filter(
                    df,
                    theme_info["industries"],
                    theme_info["concepts"],
                )
                after_theme = len(df)
                after_industry = after_theme
                after_concept = after_theme
        else:
            after_industry = after_exclude
            after_theme = after_exclude
            after_concept = after_industry

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
        else:
            # 审查修复 M-G：sort_by 列缺失（如新浪/efinance 无 change_60d）静默跳过排序，
            # 且下面 head() 先截断再 _enrich_trend_data 填 60d -> "按60d降序取前30"实际是任意30。
            # 警告让用户知道候选非按 sort_by 排序的前列。彻底修需富集前置（enrich all，昂贵，留后续）。
            logger.warning(
                f"ScannerEngine: sort_by '{sort_by}'(列'{col_name}')在数据源不存在，排序跳过--"
                f"候选为过滤后未排序集，head({rule.get('max_candidates', 30)})取前N非按 {sort_by} 排序"
            )

        # 限制候选数量
        max_candidates = rule.get("max_candidates", 30)
        if len(df) > max_candidates:
            df = df.head(max_candidates)

        # 转换为 ScanCandidate 列表
        candidates = self._df_to_candidates(df, resolved)

        # v0.8.4: enrich with 60d change
        candidates = self._enrich_trend_data(candidates)

        elapsed = time.time() - start_time
        scan_info = {
            "rule_name": resolved,
            "rule_input": rule_name,
            "rule_display_name": rule.get("name", resolved),
            "total_stocks": total_count,
            "after_exclude": after_exclude,
            "after_theme": after_theme,
            "after_industry": after_industry,
            "after_concept": after_concept,
            "filtered": len(df),
            "candidates_count": len(candidates),
            "elapsed_seconds": round(elapsed, 2),
            "cache_status": self.market_cache.get_cache_status(),
        }
        if theme_info:
            scan_info.update({
                "market_query": market_query,
                "matched_industries": theme_info["industries"],
                "matched_concepts": theme_info["concepts"],
                "match_source": theme_info["source"],
                "fallback_unfiltered": theme_info.get("fallback_unfiltered", False),
            })

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
        candidates: list[ScanCandidate] = None,
        ai_enabled: bool = True,
        ai_debug: bool = False,
        progress_callback: Optional[Callable] = None,
        ranking_config: Optional[dict] = None,
    ) -> list[dict]:
        """对选定的候选股逐只执行深度分析（走Orchestrator七层）

        Args:
            stock_codes: 要深度分析的股票代码列表
            candidates: 初筛候选股列表（提供流动性/波动性数据，用于排名）
            ai_enabled: 是否启用AI调节层
            ai_debug: 是否开启AI调试模式
            progress_callback: 进度回调 callback(step, current, total, message)
            ranking_config: 排名层配置（None时使用默认配置）

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
                "candidate": Optional[ScanCandidate],
                "ranking": Optional[RankingResult],  # 排名结果
                "error": Optional[str],
            }
        """
        from src.data.portfolio import PortfolioManager

        # 构建 candidates 索引 {stock_code: ScanCandidate}
        candidate_map = {}
        if candidates:
            candidate_map = {c.stock_code: c for c in candidates}

        results = []
        total = len(stock_codes)
        orchestrator = self._get_orchestrator()
        pm = PortfolioManager()

        for i, code in enumerate(stock_codes):
            if progress_callback:
                progress_callback("deep_analyze", i + 1, total, f"分析 {code}")

            result = {"stock_code": code, "success": False}

            # 附加候选股数据（用于排名层的流动性/波动性评分）
            result["candidate"] = candidate_map.get(code)

            # 防御性校验：跳过北交所代码（初筛应已排除，此处为兜底）
            if code.startswith("920") or code.startswith("8"):
                result["error"] = "北交所股票不支持深度分析"
                result["stock_name"] = code
                logger.info(f"ScannerEngine: 跳过北交所股票 {code}")
                results.append(result)
                continue

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

                # 名称修复：Baostock日K线不返回股票名称，优先用候选股的中文名
                name = stock_data.stock_name or code
                if (name == code or not name.strip()) and code in candidate_map:
                    name = candidate_map[code].stock_name or name
                result["stock_name"] = name

                # 获取持仓状态（如果有）
                current_ratio = 0.0
                strategy_state = None
                has_position = False
                entry_price = None
                positions = pm.list_positions()
                for pos in positions:
                    if pos.stock_code == code:
                        current_ratio = pos.current_ratio
                        strategy_state = pm.to_strategy_state(code)
                        has_position = pos.current_ratio > 0
                        entry_price = pos.entry_price
                        break

                # 调用Orchestrator七层分析（含买卖点 Layer 3.75）
                decision_result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(
                    data=stock_data,
                    current_position_ratio=current_ratio,
                    strategy_state=strategy_state,
                    ai_enabled=ai_enabled,
                    has_position=has_position,
                    entry_price=entry_price,
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

        # 排名层：对深度分析结果按四维评分排序
        if ranking_config is None or ranking_config.get("enabled", True):
            from src.core.ranking_layer import RankingLayer
            ranking_layer = RankingLayer(ranking_config)
            results = ranking_layer.rank(results, ai_enabled=ai_enabled)

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

    def resolve_rule_name(self, user_input: str) -> Optional[str]:
        """模糊匹配规则名

        支持以下匹配方式（按优先级）：
        1. 精确匹配 key（如 "healthy_pullback", "shrink_pullback"）
        2. 精确匹配中文显示名（如 "放量突破", "缩量回调"）
        3. 包含匹配 key（如 "shrink" → "shrink_pullback"）
        4. 包含匹配中文显示名（如 "缩量" → "缩量回调", "动量" → "强势动量"）

        Args:
            user_input: 用户输入的规则名

        Returns:
            匹配到的规则key，无匹配返回None
        """
        if not user_input:
            return "healthy_pullback"

        rules_cfg = self.rules.get("rules", {})

        # 1. 精确匹配key
        if user_input in rules_cfg:
            return user_input

        # 2. 精确匹配中文显示名
        for key, rule in rules_cfg.items():
            if rule.get("name", "") == user_input:
                return key

        # 3. 包含匹配key
        for key in rules_cfg:
            if user_input in key:
                return key

        # 4. 包含匹配中文显示名
        for key, rule in rules_cfg.items():
            display_name = rule.get("name", "")
            if user_input in display_name:
                return key

        return None

    def get_industry_list(self, keyword: str = None) -> list[dict]:
        """获取行业板块列表（含涨跌幅）

        Args:
            keyword: 可选关键词过滤（模糊匹配板块名称）

        Returns:
            [{"name": str, "change_pct": float, "up_count": int, "down_count": int, "lead_stock": str}, ...]
        """
        df = self.market_cache.get_industry_boards()
        if df.empty:
            return []

        # 查找列名（精确匹配优先）
        name_col = None
        change_col = None
        up_col = None
        down_col = None
        lead_col = None
        for col in df.columns:
            if col == "板块名称":
                name_col = col
            if col == "涨跌幅":
                change_col = col
            if col == "上涨家数":
                up_col = col
            if col == "下跌家数":
                down_col = col
            if col == "领涨股票":
                lead_col = col
        # 兜底模糊匹配
        if name_col is None:
            for col in df.columns:
                if "名称" in col:
                    name_col = col
                    break
                if "板块" in col and "代码" not in col:
                    name_col = col
                    break

        if not name_col:
            return []

        # 关键词过滤
        if keyword:
            mask = df[name_col].astype(str).str.contains(keyword, case=False, na=False)
            df = df[mask]

        result = []
        for _, row in df.iterrows():
            item = {"name": str(row[name_col])}
            if change_col:
                try:
                    item["change_pct"] = float(row[change_col])
                except (ValueError, TypeError):
                    item["change_pct"] = 0.0
            if up_col:
                try:
                    item["up_count"] = int(row[up_col])
                except (ValueError, TypeError):
                    item["up_count"] = 0
            if down_col:
                try:
                    item["down_count"] = int(row[down_col])
                except (ValueError, TypeError):
                    item["down_count"] = 0
            if lead_col:
                item["lead_stock"] = str(row[lead_col])
            result.append(item)

        return result

    def get_concept_list(self, keyword: str = None) -> list[dict]:
        """获取概念板块列表（含涨跌幅）"""
        df = self.market_cache.get_concept_boards()
        if df.empty:
            return []

        name_col = None
        change_col = None
        lead_col = None
        for col in df.columns:
            if col == "板块名称":
                name_col = col
            if col == "涨跌幅":
                change_col = col
            if col == "领涨股":
                lead_col = col
        if name_col is None:
            for col in df.columns:
                if "名称" in col:
                    name_col = col
                    break
                if "板块" in col and "代码" not in col:
                    name_col = col
                    break

        if not name_col:
            return []

        if keyword:
            mask = df[name_col].astype(str).str.contains(keyword, case=False, na=False)
            df = df[mask]

        result = []
        for _, row in df.iterrows():
            item = {"name": str(row[name_col])}
            if change_col:
                try:
                    item["change_pct"] = float(row[change_col])
                except (ValueError, TypeError):
                    item["change_pct"] = 0.0
            if lead_col:
                item["lead_stock"] = str(row[lead_col])
            result.append(item)
        return result

    def resolve_market_theme(self, query: str) -> dict:
        """将统一主题词解析为相关行业/概念。

        优先本地模糊匹配；若AI可用，则用AI在候选池里做一次纠偏。
        """
        terms = self._split_market_query(query)
        if not terms:
            return {"query": "", "industries": [], "concepts": [], "source": "none"}

        industries = self.get_industry_list()
        concepts = self.get_concept_list()
        board_entries = [
            *[{"name": item["name"], "type": "industry"} for item in industries],
            *[{"name": item["name"], "type": "concept"} for item in concepts],
        ]

        selected = []
        source = "local"
        for term in terms:
            local_matches = self._find_board_candidates(term, board_entries)
            term_matches = local_matches
            ai_matches = self._resolve_market_theme_with_ai(term, board_entries, local_matches)
            if ai_matches:
                term_matches = ai_matches
                source = "ai"
            if not term_matches:
                term_matches = self._resolve_market_theme_direct(term)
                if term_matches:
                    source = "direct"
            selected.extend(term_matches)

        result = {"query": ",".join(terms), "industries": [], "concepts": [], "source": source}
        seen_industries = set()
        seen_concepts = set()
        for item in selected:
            if item["type"] == "industry" and item["name"] not in seen_industries:
                seen_industries.add(item["name"])
                result["industries"].append(item["name"])
            elif item["type"] == "concept" and item["name"] not in seen_concepts:
                seen_concepts.add(item["name"])
                result["concepts"].append(item["name"])
        return result

    def _resolve_market_theme_direct(self, query: str) -> list[dict]:
        """当板块列表不可用时，直接探测行业/概念成分股接口。"""
        query = (query or "").strip()
        if not query:
            return []

        selected = []
        try:
            industry_codes = self.market_cache.get_stocks_by_industry(query)
            if industry_codes:
                selected.append({"name": query, "type": "industry"})
        except Exception as exc:
            logger.debug(f"ScannerEngine: 直接探测行业失败 {query}: {exc}")

        try:
            concept_codes = self.market_cache.get_stocks_by_concept(query)
            if concept_codes:
                selected.append({"name": query, "type": "concept"})
        except Exception as exc:
            logger.debug(f"ScannerEngine: 直接探测概念失败 {query}: {exc}")

        if selected:
            logger.info(f"ScannerEngine: 直接探测主题词 {query} -> {selected}")
        return selected

    @staticmethod
    def _split_market_query(query: str) -> list[str]:
        """按英文逗号拆分多个主题词。"""
        raw = (query or "").strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    def _find_board_candidates(self, query: str, board_entries: list[dict], limit: int = 12) -> list[dict]:
        """按本地规则选出最相关的行业/概念候选。"""
        query = query.strip()
        if not query:
            return []

        scored = []
        query_lower = query.lower()
        for item in board_entries:
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            score = 0.0
            name_lower = name.lower()
            if name == query:
                score = 100.0
            elif name_lower == query_lower:
                score = 95.0
            elif query_lower in name_lower:
                score = 80.0 + min(len(query_lower), 10)
            else:
                similarity = SequenceMatcher(None, query_lower, name_lower).ratio()
                if similarity >= 0.35:
                    score = similarity * 100

            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
        deduped = []
        seen = set()
        for _, item in scored:
            key = (item["type"], item["name"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def _resolve_market_theme_with_ai(
        self,
        query: str,
        board_entries: list[dict],
        local_matches: list[dict],
    ) -> list[dict]:
        """使用AI从候选行业/概念中纠偏匹配名称。"""
        if not self._match_client or not board_entries:
            return []

        candidate_pool = local_matches or board_entries[:120]
        if not candidate_pool:
            return []

        prompt_lines = [f"{item['type']}: {item['name']}" for item in candidate_pool[:120]]
        system_prompt = (
            "你是A股行业和概念板块名称纠偏助手。"
            "用户会给一个主题词，你需要从候选名单里选出最相关的行业或概念名称。"
            "只能从候选名单里选择，输出严格 JSON："
            '{"industries": ["行业名"], "concepts": ["概念名"]}'
        )
        user_prompt = (
            f"用户主题词: {query}\n"
            "候选名单:\n"
            + "\n".join(prompt_lines)
            + "\n请返回最相关的行业和概念，可为空数组。"
        )

        try:
            response = self._match_client.chat.completions.create(
                model=self._match_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                **({"extra_body": {"thinking": {"type": "disabled"}}} if str(self._match_model).startswith("deepseek") else {}),
            )
            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
            industries = set(parsed.get("industries", []) or [])
            concepts = set(parsed.get("concepts", []) or [])
            selected = []
            for item in candidate_pool:
                if item["type"] == "industry" and item["name"] in industries:
                    selected.append(item)
                if item["type"] == "concept" and item["name"] in concepts:
                    selected.append(item)
            return selected
        except Exception as exc:
            logger.debug(f"ScannerEngine: AI主题纠偏失败，回退本地匹配: {exc}")
            return []

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

    def _apply_market_theme_filter(
        self,
        df: pd.DataFrame,
        industries: list[str],
        concepts: list[str],
    ) -> pd.DataFrame:
        """统一主题过滤：行业和概念按并集筛选。"""
        if not industries and not concepts:
            return df

        all_codes = set()
        resolved_industries = self._resolve_industry_names(industries) if industries else []
        resolved_concepts = self._resolve_concept_names(concepts) if concepts else []

        for industry_name in resolved_industries:
            all_codes.update(self.market_cache.get_stocks_by_industry(industry_name))
        for concept_name in resolved_concepts:
            all_codes.update(self.market_cache.get_stocks_by_concept(concept_name))

        if not all_codes:
            logger.warning(
                f"ScannerEngine: 统一主题过滤无结果，行业={industries}，概念={concepts}"
            )
            return df

        if "代码" in df.columns:
            result = df[df["代码"].astype(str).isin(all_codes)]
            logger.info(
                "ScannerEngine: 统一主题过滤 "
                f"行业={resolved_industries}, 概念={resolved_concepts} -> {len(all_codes)}只成分股 -> 匹配{len(result)}只"
            )
            return result

        return df

    def _resolve_industry_names(self, industries: list[str]) -> list[str]:
        """将行业过滤词解析为准确行业名，支持模糊匹配。"""
        industry_list = self.get_industry_list()
        return self._resolve_board_names(industries, industry_list, "行业")

    def _resolve_concept_names(self, concepts: list[str]) -> list[str]:
        """将概念过滤词解析为准确概念名，支持模糊匹配。"""
        concept_list = self.get_concept_list()
        return self._resolve_board_names(concepts, concept_list, "概念")

    @staticmethod
    def _resolve_board_names(inputs: list[str], board_list: list[dict], board_type: str) -> list[str]:
        """将用户输入解析为板块精确名称。

        匹配顺序：
        1. 精确匹配
        2. 忽略大小写精确匹配
        3. 包含匹配（关键词命中多个板块时取并集）

        如果板块列表为空，或某个输入没有匹配项，则保留原始输入，避免因缓存/接口异常导致误伤。
        """
        if not inputs:
            return []
        if not board_list:
            return inputs

        names = [str(item.get("name", "")).strip() for item in board_list if item.get("name")]
        resolved = []

        for raw_input in inputs:
            query = str(raw_input).strip()
            if not query:
                continue

            exact = [name for name in names if name == query]
            if exact:
                resolved.extend(exact)
                continue

            query_lower = query.lower()
            exact_casefold = [name for name in names if name.lower() == query_lower]
            if exact_casefold:
                resolved.extend(exact_casefold)
                continue

            fuzzy = [name for name in names if query_lower in name.lower()]
            if fuzzy:
                logger.info(f"ScannerEngine: {board_type}过滤模糊匹配 {query} -> {fuzzy}")
                resolved.extend(fuzzy)
                continue

            resolved.append(query)

        deduped = []
        seen = set()
        for name in resolved:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped

    def _enrich_trend_data(self, candidates: list) -> list:
        """Enrich candidates with 60-day price change via Baostock (v0.8.4).

        Only runs when candidates <= 50. Fails silently on error.
        """
        if len(candidates) > 50 or len(candidates) == 0:
            return candidates

        try:
            from src.data.akshare_client import _ensure_baostock_login
            import baostock as bs
            from datetime import datetime, timedelta
            import pandas as pd

            if not _ensure_baostock_login():
                return candidates

            end_d = datetime.now().strftime("%Y-%m-%d")
            start_d = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
            updated = 0

            for cand in candidates:
                try:
                    code = cand.stock_code
                    prefix = "sh" if code.startswith(("6", "9")) else "sz"
                    bs_code = f"{prefix}.{code}"

                    rs = bs.query_history_k_data_plus(
                        bs_code, "date,close",
                        start_date=start_d, end_date=end_d,
                        frequency="d", adjustflag="2"
                    )
                    if rs.error_code != "0":
                        continue

                    rows = []
                    while (rs.error_code == "0") and rs.next():
                        rows.append(rs.get_row_data())

                    if len(rows) < 20:
                        continue

                    df = pd.DataFrame(rows, columns=["date", "close"])
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    df = df.dropna(subset=["close"])

                    if len(df) < 45:
                        continue

                    latest = df["close"].iloc[-1]
                    ago_idx = max(0, len(df) - 60)
                    ago = df["close"].iloc[ago_idx]

                    if ago > 0:
                        cand.change_60d = round((latest - ago) / ago * 100, 2)
                        updated += 1
                except Exception:
                    continue

            logger.info(f"60d change enriched: {updated}/{len(candidates)}")
        except Exception as e:
            logger.debug(f"60d enrichment failed: {e}")

        return candidates

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
