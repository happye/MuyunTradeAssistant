"""Chat Agent工具函数 — 映射到底层引擎 (v0.8.0 Phase 5)

设计原则：
- 绕过CLI层（CLI层用Rich Console打印，无返回值）
- 直接调底层引擎（Orchestrator/ScannerEngine/PortfolioManager/NewsClient）
- 返回纯文本字符串（供AI读取和用户查看）
- 所有异常在工具层捕获并返回友好错误信息
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 模块级引擎实例（由ChatAgent启动时通过init_engines()初始化）
_orchestrator = None
_scanner_engine = None
_portfolio_manager = None


def init_engines(config: dict):
    """初始化所有底层引擎（ChatAgent启动时调用一次）

    Args:
        config: settings.yaml完整配置
    """
    global _orchestrator, _scanner_engine, _portfolio_manager

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

    _orchestrator = Orchestrator(
        skills_dir, enabled_skills, weights, skill_types,
        ai_config=ai_config, event_config=event_config
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
        return "错误：扫描引擎未初始化"

    try:
        industries = _scanner_engine.get_industry_list(keyword=keyword)
        if not industries:
            return f"未找到包含'{keyword}'的行业板块"

        from src.chat.formatter import format_industry_list
        return format_industry_list(industries, keyword)
    except Exception as e:
        logger.error(f"search_stocks_by_sector失败: {e}")
        return f"搜索行业板块时出错: {e}"


def analyze_stock(stock_code: str) -> str:
    """对单只股票进行深度分析"""
    if not _orchestrator:
        return "错误：编排器未初始化"

    try:
        from src.data.akshare_client import AKShareClient
        # get_stock_data 是 AKShareClient.calculate_indicators 的别名
        get_stock_data = AKShareClient.calculate_indicators

        # 获取数据
        stock_data = get_stock_data(stock_code)

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

            return f"无法获取 {stock_code} 的数据，请检查股票代码是否正确"

        # 获取持仓状态
        pm = _portfolio_manager
        current_ratio = 0.0
        strategy_state = None
        if pm:
            positions = pm.list_positions()
            for pos in positions:
                if pos.stock_code == stock_code:
                    current_ratio = pos.current_ratio
                    strategy_state = pm.to_strategy_state(stock_code)
                    break

        # 执行7层分析
        decision_result, strategy_decision, execution_eval, ai_result = _orchestrator.analyze(
            stock_data,
            current_position_ratio=current_ratio,
            strategy_state=strategy_state,
            ai_enabled=True,
        )

        from src.chat.formatter import format_analysis_result
        return format_analysis_result(
            stock_data, decision_result, strategy_decision,
            execution_eval, ai_result
        )

    except Exception as e:
        logger.error(f"analyze_stock失败: {e}")
        return f"分析 {stock_code} 时出错: {e}"


def scan_market(rule_name: str = "default", industry: Optional[str] = None) -> str:
    """全市场扫描"""
    if not _scanner_engine:
        return "错误：扫描引擎未初始化"

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
        industry_filter = [industry] if industry else None
        candidates, scan_info = _scanner_engine.quick_scan(
            rule_name=rule_name,
            industry_filter=industry_filter,
            exclude_codes=exclude_codes,
        )

        if "error" in scan_info:
            return f"扫描失败: {scan_info['error']}"

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
        return f"市场扫描时出错: {e}"


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
        return f"获取持仓时出错: {e}"


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
        return f"获取新闻时出错: {e}"


# 工具名 → 函数 的映射
TOOL_REGISTRY = {
    "search_stocks_by_sector": search_stocks_by_sector,
    "analyze_stock": analyze_stock,
    "scan_market": scan_market,
    "get_portfolio": get_portfolio,
    "get_news": get_news,
}
