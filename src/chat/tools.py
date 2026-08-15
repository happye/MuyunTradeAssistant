"""Chat Agent工具函数 — 映射到底层引擎 (v0.8.1)

设计原则：
- 绕过CLI层（CLI层用Rich Console打印，无返回值）
- 直接调底层引擎（Orchestrator/ScannerEngine/PortfolioManager/NewsClient/RAGService）
- 返回纯文本字符串（供AI读取和用户查看）
- 所有异常在工具层捕获并返回友好错误信息

v0.8.1 新增：search_knowledge 工具（RAG策略知识检索）
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 工具失败标记：所有失败返回（内部异常/坏参数/数据源失败）统一以此开头，
# agent 据此识别失败的工具调用——失败轮不占工具轮次上限，允许AI重试。
TOOL_ERROR_MARK = "[工具失败] "

# 模块级引擎实例（由ChatAgent启动时通过init_engines()初始化）
_orchestrator = None
_scanner_engine = None
_portfolio_manager = None
_rag_service = None  # v0.8.1: RAG服务实例


def init_engines(config: dict):
    """初始化所有底层引擎（ChatAgent启动时调用一次）

    Args:
        config: settings.yaml完整配置
    """
    global _orchestrator, _scanner_engine, _portfolio_manager, _rag_service

    from src.core.orchestrator import Orchestrator
    from src.scanner.scanner_engine import ScannerEngine
    from src.data.portfolio import PortfolioManager

    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    enabled_skills = config.get("skills", {}).get("enabled", None)
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    ai_config = config.get("ai", None)
    event_config = config.get("event", None)
    scanner_cfg = config.get("scanner", {})

    # v0.8.1: 初始化RAG服务（先于Orchestrator，以便传递给子组件）
    rag_config = config.get("rag", {})
    if rag_config.get("enabled", False):
        try:
            from src.rag.service import RAGService
            _rag_service = RAGService(rag_config)
            _rag_service.initialize()
            if _rag_service.is_available():
                logger.info(f"RAG服务已启用: {_rag_service.doc_count}个文档块")
            else:
                logger.warning("RAG服务初始化失败，search_knowledge工具不可用")
                _rag_service = None
        except Exception as e:
            logger.warning(f"RAG服务初始化异常: {e}，search_knowledge工具不可用")
            _rag_service = None

    # 审查修复 H2：补 entry_exit_config + pyramid_config（原漏传 -> chat 买卖点计算器=None，
    # 突破/Chandelier/止盈全不计算，chat 分析永不产生买卖点，与 CLI 不一致）
    from src.cli.main import load_pyramid_config
    _orchestrator = Orchestrator(
        skills_dir, enabled_skills, weights, skill_types,
        ai_config=ai_config, event_config=event_config,
        entry_exit_config=config.get("entry_exit"),
        pyramid_config=load_pyramid_config(config),
        rag_service=_rag_service,  # v0.8.1: 传递RAG服务给子组件
    )

    _scanner_engine = ScannerEngine(
        rules_path=scanner_cfg.get("rules_path", "./src/scanner/scan_rules.yaml"),
        skills_dir=skills_dir,
        enabled_skills=enabled_skills,
        signal_weights=weights,
        skill_types=skill_types,
        ai_config=ai_config,
        cache_ttl=scanner_cfg.get("cache_ttl", 300),
    )

    _portfolio_manager = PortfolioManager()

    logger.info("Chat Agent工具引擎初始化完成")


def search_stocks_by_sector(keyword: str) -> str:
    """按行业/板块搜索股票"""
    if not _scanner_engine:
        return TOOL_ERROR_MARK + "扫描引擎未初始化"

    try:
        industries = _scanner_engine.get_industry_list(keyword=keyword)
        if not industries:
            return f"未找到包含'{keyword}'的行业板块"

        from src.chat.formatter import format_industry_list
        return format_industry_list(industries, keyword)
    except Exception as e:
        logger.error(f"search_stocks_by_sector失败: {e}")
        return TOOL_ERROR_MARK + f"搜索行业板块时出错: {e}"


def _get_stock_data_with_timeout(stock_code: str, timeout: int = 30):
    """带超时的 get_stock_data，防 akshare/baostock 挂起卡死 chat。
    复用 CLI main.py 的 daemon 线程 + t.join(timeout) 模式。超时返回 None 走降级。"""
    import threading
    from src.data.akshare_client import AKShareClient
    result_container = [None]
    error_container = [None]

    def _fetch():
        try:
            result_container[0] = AKShareClient.calculate_indicators(stock_code)
        except Exception as e:
            error_container[0] = e

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning(f"chat: 获取 {stock_code} 数据超时({timeout}s)，降级实时行情")
        return None
    if error_container[0]:
        raise error_container[0]
    return result_container[0]


def analyze_stock(stock_code: str) -> str:
    """对单只股票进行深度分析"""
    if not _orchestrator:
        return TOOL_ERROR_MARK + "编排器未初始化"

    try:
        from src.data.akshare_client import AKShareClient
        # get_stock_data 是 AKShareClient.calculate_indicators 的别名
        get_stock_data = AKShareClient.calculate_indicators

        # 获取数据
        stock_data = _get_stock_data_with_timeout(stock_code)

        if not stock_data:
            # 降级：尝试仅获取实时行情
            quote = AKShareClient.get_realtime_quote(stock_code)
            if quote:
                from src.chat.formatter import format_basic_quote
                from src.data.models import StockData
                stock_data = StockData(
                    stock_code=quote.get("stock_code", stock_code),
                    stock_name=quote.get("stock_name", stock_code),
                    price=quote.get("price", 0),
                    change_pct=quote.get("change_pct"),
                    volume=quote.get("volume"),
                )
                return format_basic_quote(stock_data) + "\n\n[技术指标不可用，无法进行深度分析]"

            return TOOL_ERROR_MARK + f"无法获取 {stock_code} 的数据，请检查股票代码是否正确"

        # 获取持仓状态
        pm = _portfolio_manager
        current_ratio = 0.0
        strategy_state = None
        pos = None
        if pm:
            positions = pm.list_positions()
            for p in positions:
                if p.stock_code == stock_code:
                    pos = p
                    current_ratio = p.current_ratio
                    strategy_state = pm.to_strategy_state(stock_code)
                    break

        has_position = pos is not None and pos.current_ratio > 0
        # 执行7层分析（审查修复 H1：补齐 has_position/entry_price/high_since_entry/trade_plan，
        # 与 CLI l 一致；原漏传 -> 即使持仓 has_position=False，fundamental_alert/top_signal/
        # force_exit/PlanGuard 全失效，chat 与 CLI 对同一持仓股给不同决策）
        decision_result, strategy_decision, execution_eval, ai_result = _orchestrator.analyze(
            stock_data,
            current_position_ratio=current_ratio,
            strategy_state=strategy_state,
            ai_enabled=True,
            has_position=has_position,
            entry_price=pos.entry_price if pos else None,
            high_since_entry=pos.high_since_entry if pos else None,
            trade_plan=pos.trade_plan if pos else None,  # PlanGuard 守卫
        )

        from src.chat.formatter import format_analysis_result
        result = format_analysis_result(
            stock_data, decision_result, strategy_decision,
            execution_eval, ai_result
        )

        # 审查修复H1：回写策略状态(inertia/cooldown/high_since_entry等)到portfolio.yaml，
        # 与CLI一致；原chat只读不写->持仓股策略状态冻结，chat与CLI随时间分歧。
        # 仅持仓股回写(非持仓回写会创建虚假持仓记录)
        if has_position and pos and _portfolio_manager:
            try:
                _stock_name = stock_data.stock_name or stock_code
                _portfolio_manager.update_from_strategy_decision(
                    stock_code, _stock_name, strategy_decision, stock_data
                )
            except Exception as e:
                logger.warning(f"chat回写策略状态失败({stock_code}): {e}")

        return result

    except Exception as e:
        logger.error(f"analyze_stock失败: {e}")
        return TOOL_ERROR_MARK + f"分析 {stock_code} 时出错: {e}"


def scan_market(rule_name: str = "healthy_pullback", query: Optional[str] = None) -> str:
    """全市场扫描"""
    if not _scanner_engine:
        return TOOL_ERROR_MARK + "扫描引擎未初始化"

    try:
        from src.data.portfolio import PortfolioManager

        # 排除已持仓
        exclude_codes = set()
        pm = _portfolio_manager or PortfolioManager()
        try:
            positions = pm.list_positions()
            exclude_codes = {pos.stock_code for pos in positions}
        except Exception:
            pass

        # 初筛
        candidates, scan_info = _scanner_engine.quick_scan(
            rule_name=rule_name,
            market_query=query,
            exclude_codes=exclude_codes,
        )

        if "error" in scan_info:
            return TOOL_ERROR_MARK + f"扫描失败: {scan_info['error']}"

        if not candidates:
            return (
                f"未找到符合条件的股票\n"
                f"扫描信息: 全市场{scan_info.get('total_stocks', '?')}只 → "
                f"规则'{scan_info.get('rule_display_name', rule_name)}'过滤后0只"
            )

        from src.chat.formatter import format_scan_result
        return format_scan_result(candidates, scan_info)

    except Exception as e:
        logger.error(f"scan_market失败: {e}")
        return TOOL_ERROR_MARK + f"市场扫描时出错: {e}"


def get_portfolio() -> str:
    """获取持仓列表"""
    try:
        from src.data.portfolio import PortfolioManager

        pm = _portfolio_manager or PortfolioManager()
        positions = pm.list_positions()

        if not positions:
            return "当前无持仓记录"

        from src.chat.formatter import format_portfolio
        return format_portfolio(positions)

    except Exception as e:
        logger.error(f"get_portfolio失败: {e}")
        return TOOL_ERROR_MARK + f"获取持仓时出错: {e}"


def get_news(stock_code: str, max_count: int = 5) -> str:
    """获取股票新闻"""
    try:
        from src.data.news_client import NewsClient

        news_data = NewsClient.gather_news_for_analysis(stock_code, max_count)
        stock_news = news_data.get("stock_news", [])
        macro_news = news_data.get("macro_news", [])

        if not stock_news and not macro_news:
            return f"未获取到 {stock_code} 的相关新闻"

        from src.chat.formatter import format_news
        return format_news(stock_code, stock_news, macro_news)

    except Exception as e:
        logger.error(f"get_news失败: {e}")
        return TOOL_ERROR_MARK + f"获取新闻时出错: {e}"


# 工具名 → 函数 的映射
TOOL_REGISTRY = {
    "search_stocks_by_sector": search_stocks_by_sector,
    "analyze_stock": analyze_stock,
    "scan_market": scan_market,
    "get_portfolio": get_portfolio,
    "get_news": get_news,
    "search_knowledge": lambda query="": search_knowledge(query),  # v0.8.1
}


def search_knowledge(query: str) -> str:
    """搜索策略知识库（v0.8.1 RAG）

    根据查询文本检索相关策略知识，返回格式化的上下文。
    用于回答用户关于交易策略、止损止盈方法、心理偏差等问题。

    Args:
        query: 查询文本，如"止损怎么设"、"套牢了怎么办"

    Returns:
        策略知识检索结果（纯文本）
    """
    if not _rag_service or not _rag_service.is_available():
        return TOOL_ERROR_MARK + "策略知识库未启用或初始化失败"

    if not query.strip():
        return TOOL_ERROR_MARK + "请提供查询内容，如'止损怎么设'、'突破买入注意事项'"

    try:
        context = _rag_service.get_context(
            query, target="chat", top_k=5, max_length=3000
        )

        if not context:
            return f"未找到与'{query}'相关的策略知识"

        result = _rag_service.retrieve(query, top_k=5)
        header = f"找到 {len(result.documents)} 条相关策略知识：\n\n"
        return header + context

    except Exception as e:
        logger.error(f"search_knowledge失败: {e}")
        return TOOL_ERROR_MARK + f"搜索策略知识时出错: {e}"
