"""Chat Agent工具函数 — 映射到底层引擎 (v0.8.1)

设计原则：
- 绕过CLI层（CLI层用Rich Console打印，无返回值）
- 直接调底层引擎（Orchestrator/ScannerEngine/PortfolioManager/NewsClient/RAGService）
- 返回纯文本字符串（供AI读取和用户查看）
- 所有异常在工具层捕获并返回友好错误信息

v0.8.1 新增：search_knowledge 工具（RAG策略知识检索）
v0.8.8 新增：run_command（REPL 全命令桥，复用 start.parse_input+run_cli）
            manage_portfolio（持仓文件写操作，结构化参数）
v0.8.9.3 新增：read_file/write_file/list_files（本地文件读写，沙箱限仓库内；
            写仅限 AI笔记/ 专属目录，读拒绝密钥文件——ISS-092）
"""

import builtins
import contextlib
import io
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 工具失败标记：所有失败返回（内部异常/坏参数/数据源失败）统一以此开头，
# agent 据此识别失败的工具调用——失败轮不占工具轮次上限，允许AI重试。
TOOL_ERROR_MARK = "[工具失败] "


def _normalize_code(code) -> str:
    """股票代码规范化（v0.8.7.8 裁决修复 H05）：
    去交易所前缀（sh./SZ.）+ 去空白 + 补零到 6 位。持仓匹配与
    portfolio._load 的键规范化（G04）配套，带前缀/空格输入不再被当非持仓。
    v0.8.9.5：补 "000001.SZ" 式后缀格式——原 split(".")[-1] 对后缀式会取到
    "SZ" 并 zfill 成 "0000SZ"（垃圾键）；改为取点号分隔段中的纯数字段。"""
    c = str(code or "").strip()
    if not c:
        return ""
    if "." in c:
        for part in c.split("."):
            part = part.strip()
            if part.isdigit():
                c = part
                break
    if len(c) > 6 and c[:2].lower() in ("sh", "sz", "bj") and c[2:].isdigit():
        c = c[2:]
    return c.zfill(6) if c.isdigit() else c

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

    # v0.8.8 RAG单例对齐：CLI 的 TradePlan 路径（cli/main.py _try_attach_trade_plan/
    # _generate_or_update_plan）调 get_rag_service() 懒加载单例；不把 chat 已建实例
    # 注册进去，run_command 执行 pos add/pos plan 时会在同一进程二次加载
    # torch 嵌入模型+FAISS 索引（内存翻倍）。
    if _rag_service is not None:
        try:
            import src.rag.service as _rag_svc_mod
            _rag_svc_mod._rag_service_singleton = _rag_service
        except Exception as e:
            logger.warning(f"RAG单例对齐失败（CLI路径可能二次加载）: {e}")

    # 审查修复 H2：补 entry_exit_config（原漏传 -> chat 买卖点计算器=None，
    # 突破/Chandelier/止盈全不计算，chat 分析永不产生买卖点，与 CLI 不一致）
    _orchestrator = Orchestrator(
        skills_dir, enabled_skills, weights, skill_types,
        ai_config=ai_config, event_config=event_config,
        entry_exit_config=config.get("entry_exit"),
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


def shutdown_engines():
    """释放Chat Agent占用的底层引擎资源（chat退出时调用）。

    退出chat后若不清理：RAG的torch嵌入模型+FAISS索引（内存大头）随模块级
    单例驻留整个start.py会话，重进chat还会二次加载（峰值叠加）；baostock
    连接不登出（CLI的scan结束时有logout，chat补齐）；httpx连接池无可靠析构，
    不显式close则socket挂到进程退出。
    """
    global _orchestrator, _scanner_engine, _portfolio_manager, _rag_service

    # 关闭编排器内的AI客户端连接池（httpx）
    try:
        _mod = getattr(_orchestrator, "ai_modifier", None) if _orchestrator else None
        _cl = getattr(_mod, "_client", None) if _mod else None
        if _cl is not None and hasattr(_cl, "close"):
            _cl.close()
    except Exception as e:
        logger.warning(f"chat清理: 关闭AI客户端失败: {e}")

    # baostock登出：仅当本次会话实际登录过才登出
    # （未登录时调bs.logout()会打印"you don't login."噪声，无意义）
    try:
        from src.data import akshare_client
        if getattr(akshare_client, "_bs_login_status", False):
            akshare_client._baostock_logout()
    except Exception as e:
        logger.warning(f"chat清理: baostock登出失败: {e}")

    # 置空模块级引擎引用（RAG的torch模型/FAISS索引随之失去引用，可被GC回收）
    _orchestrator = None
    _scanner_engine = None
    _portfolio_manager = None
    _rag_service = None

    # v0.8.8: RAG单例同步清空（init_engines 里注册到 rag.service 的引用一并放掉，
    # 否则 shutdown 后单例仍持有 torch 模型，chat 退出前的内存释放失效）
    try:
        import src.rag.service as _rag_svc_mod
        _rag_svc_mod._rag_service_singleton = None
    except Exception:
        pass

    # 触发GC，实际回收torch/faiss等C扩展分配的内存
    import gc
    gc.collect()
    logger.info("Chat Agent引擎资源已释放（RAG模型/引擎实例/AI连接/baostock）")


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


def _split_codes_arg(raw: str) -> tuple:
    """解析多代码参数（ISS-087）：英文逗号/中文逗号/顿号/分号/空格均可作分隔符。

    Returns:
        (codes, invalid)：codes 为 6 位代码（含 .SZ/.SS 后缀，去重保序）；
        invalid 为无法识别的片段（#N 序号引用不支持——请用 run_command('l #1')）。
    """
    codes: list = []
    invalid: list = []
    for tok in str(raw or "").replace("，", ",").replace("、", ",").replace("；", ";").replace(";", ",").split():
        for piece in tok.split(","):
            piece = piece.strip()
            if not piece or piece.startswith("-"):
                continue
            base = piece.split(".")[0] if "." in piece else piece
            if base.isdigit() and len(base) == 6:
                if piece not in codes:
                    codes.append(piece)
            else:
                invalid.append(piece)
    return codes, invalid


def analyze_stock(stock_code: str = "", confirm: bool = False) -> str:
    """对股票进行深度分析（支持单只或多只，多只用逗号/空格分隔）"""
    if not _orchestrator:
        return TOOL_ERROR_MARK + "编排器未初始化"

    # ISS-087 多代码：单只走完整报告路径；多只走批量管道（confirm 硬门护 AI 费用）
    codes, invalid = _split_codes_arg(stock_code)
    if not codes:
        return TOOL_ERROR_MARK + ("请提供股票代码，如'600519'；多只用逗号/空格分隔："
                                  "'600519,000001'（#N 序号引用请改用 run_command('l #1')）")
    skip_note = ""
    if invalid:
        skip_note = f"[!] 以下片段不是有效代码已跳过: {'、'.join(invalid)}\n"
    if len(codes) > 1:
        if not confirm:
            return (TOOL_ERROR_MARK
                    + f"多代码（{len(codes)}只：{'、'.join(codes)}）属于批量AI费用操作"
                      f"（每只约0.5~1分钟+多次AI调用）。请先向用户说明耗时与费用并征得明确同意，"
                      f"然后带 confirm=true 重新调用本工具；单只分析无需 confirm。")
        # 复用 REPL live_multi 管道：compact 卡片 + 单只失败跳过 + 当日已析记录
        result = run_command("l " + " ".join(codes), confirm=True)
        return skip_note + result if skip_note else result

    result = _analyze_stock_single(codes[0])
    return skip_note + result if skip_note else result


def _analyze_stock_single(stock_code: str) -> str:
    """单只深度分析（原 analyze_stock 主体，供多代码调度器复用）"""
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
                    # v0.8.7.8 E02 同类点：open/high/low 此前被丢弃（quote 已返回）
                    open=quote.get("open") or None,
                    high=quote.get("high") or None,
                    low=quote.get("low") or None,
                    change_pct=quote.get("change_pct"),
                    volume=quote.get("volume"),
                )
                return format_basic_quote(stock_data) + "\n\n[技术指标不可用，无法进行深度分析]"

            return TOOL_ERROR_MARK + f"无法获取 {stock_code} 的数据，请检查股票代码是否正确"

        # 获取持仓状态（v0.8.7.8 H05：双侧代码规范化，带前缀/空格输入也能匹配持仓）
        pm = _portfolio_manager
        current_ratio = 0.0
        strategy_state = None
        pos = None
        norm_code = _normalize_code(stock_code)
        if pm:
            positions = pm.list_positions()
            for p in positions:
                if _normalize_code(p.stock_code) == norm_code:
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

        # v0.8.12: 观察池——WATCH 语义无持仓自动入池（与 CLI l 同一钩子；失败不影响分析）
        try:
            from src.cli.main import _watch_pool_touch
            _watch_pool_touch(decision_result, strategy_decision, stock_data, pos)
        except Exception as e:
            logger.debug(f"chat 观察池入池失败(不影响分析): {e}")

        from src.chat.formatter import format_analysis_result
        result = format_analysis_result(
            stock_data, decision_result, strategy_decision,
            execution_eval, ai_result
        )

        # 审查修复H1：回写策略状态(inertia/cooldown/high_since_entry等)到portfolio.yaml，
        # 与CLI一致；原chat只读不写->持仓股策略状态冻结，chat与CLI随时间分歧。
        # 仅持仓股回写(非持仓回写会创建虚假持仓记录)
        if has_position and pos and _portfolio_manager:
            # ISS-078 监督审查 P2：用 pos 的实际存储键（H05 规范化后匹配到的）查询
            # 与回写——AI 传 sh600519 式带前缀代码时 raw code 查询会误判"已被外部
            # 删除"，update 还会按 raw code 创建重复键
            _key = pos.stock_code
            try:
                # ISS-078：回写前重读一次并确认持仓仍在——分析期间用户可能在外部
                # 编辑器改过 portfolio.yaml，陈旧 _data 整文件写回会静默回滚外部修改
                # （_save 已有 mtime 告警兜底，这里把窗口进一步收窄并防"外部已删仓、
                # 回写又把记录造回来"）
                _reload_portfolio_manager()
                if _portfolio_manager.get_position(_key) is None:
                    logger.info(f"chat回写跳过({_key}): 持仓已被外部删除")
                    return result
                _stock_name = stock_data.stock_name or _key
                _portfolio_manager.update_from_strategy_decision(
                    _key, _stock_name, strategy_decision, stock_data
                )
            except Exception as e:
                logger.warning(f"chat回写策略状态失败({_key}): {e}")

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
        except Exception as e:
            # ISS-078：排除持仓失败必须留痕（此前静默 fail-open，已持仓股混入候选无感知）
            logger.warning(f"持仓读取失败，本次扫描无法排除已持仓股: {e}")

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
        result = format_scan_result(candidates, scan_info)

        # v0.8.8: 扫描结果同步写 session_state（与 CLI scan market 同一状态文件），
        # 让 chat 内扫描后的 #N / l all / ba / pos add #N 跨命令接续可用
        try:
            from src.cli.session_state import save_last_scan
            items = [
                {
                    "code": c.stock_code, "name": c.stock_name,
                    "price": c.price, "change_pct": c.change_pct,
                    "turnover_rate": getattr(c, "turnover_rate", None),
                    "volume_ratio": getattr(c, "volume_ratio", None),
                }
                for c in candidates
            ]
            save_last_scan(
                items, f"chat: scan_market {scan_info.get('rule_display_name', rule_name)}")
        except Exception as e:
            logger.warning(f"chat扫描结果落盘失败（不影响主流程）: {e}")

        return result

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
    "analyze_industry": lambda industry="": analyze_industry(industry),      # ISS-061（定义在文件尾）
    "get_main_business": lambda stock_code="": get_main_business(stock_code),  # ISS-061（定义在文件尾）
    "save_chain_graph": lambda name="", graph_yaml="": save_chain_graph(name, graph_yaml),  # ISS-061 v4 图谱自举（定义在文件尾）
    "run_command": lambda command="", confirm=False: run_command(command, confirm),  # v0.8.8 命令桥（定义在文件尾）
    "manage_portfolio": lambda **kw: manage_portfolio(**kw),  # v0.8.8 持仓修改（定义在文件尾）
    "read_file": lambda path="": read_file(path),  # v0.8.9.3 本地文件读（沙箱，定义在文件尾）
    "write_file": lambda path="", content="": write_file(path, content),  # v0.8.9.3 写 AI笔记/（定义在文件尾）
    "list_files": lambda subdir="": list_files(subdir),  # v0.8.9.3 列目录（沙箱，定义在文件尾）
    "get_fear_index": lambda scope="", history="5,10,22,66": get_fear_index(scope, history),  # v0.8.10 恐慌指数（定义在文件尾）
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
        # 2026-09-06 审查修复：单次检索同时供计数与上下文（原实现 get_context 后
        # 又无条件 retrieve 一次只为拿条数，双倍编码+搜索；且旧计数不带层过滤，
        # 与上下文口径不一致）。layer="Chat" 与 get_context(target="chat") 同源。
        from src.rag.service import TARGET_LAYER_MAP
        from src.rag.context import build_chat_context

        result = _rag_service.retrieve(
            query, top_k=5, layer=TARGET_LAYER_MAP.get("chat")
        )
        if not result.documents:
            return f"未找到与'{query}'相关的策略知识"

        context = build_chat_context(result, max_length=3000)
        header = f"找到 {len(result.documents)} 条相关策略知识：\n\n"
        return header + context

    except Exception as e:
        logger.error(f"search_knowledge失败: {e}")
        return TOOL_ERROR_MARK + f"搜索策略知识时出错: {e}"


def analyze_industry(industry: str) -> str:
    """行业产业链深度分析（ISS-061）。

    组装"行业数据包"：产业链图谱(环节-公司-周期锚点+用户持仓定位)
    + 商品价格(期货/现货基差/仓单) + 需求端月度数据 + 宏观底色(PMI/PPI)。
    数据源为 2026-08-15 实测可用的 akshare 接口；单项失败降级标注[数据缺失]。
    """
    try:
        from src.data.industry_data import build_industry_report

        # 持仓交叉定位：图谱内公司与用户持仓比对
        positions = []
        try:
            pm = _portfolio_manager or PortfolioManager()
            positions = [
                {"stock_code": p.stock_code, "current_ratio": p.current_ratio}
                for p in pm.list_positions()
            ]
        except Exception:
            pass

        # scanner_engine：全行业通用引擎解析THS板块成分股用（图谱未命中时）
        return build_industry_report(industry, positions, scanner_engine=_scanner_engine)
    except Exception as e:
        logger.error(f"analyze_industry失败: {e}")
        return f"{TOOL_ERROR_MARK}行业分析时出错: {e}"


def get_main_business(stock_code: str) -> str:
    """个股主营构成（ISS-061）--个股在产业链中的环节定位证据。

    实现委托 src/data/industry_data.fetch_main_business（与全行业引擎的
    成分股主营抽样共用同一份逻辑）。此处只做超时守护+失败标记。
    """
    import threading
    from src.data.industry_data import fetch_main_business

    box = [None, None]

    def _f():
        try:
            box[0] = fetch_main_business(stock_code)
        except Exception as e:
            box[1] = f"{type(e).__name__}: {str(e)[:80]}"

    t = threading.Thread(target=_f, daemon=True)
    t.start()
    t.join(timeout=25)
    if t.is_alive():
        return f"{TOOL_ERROR_MARK}获取主营构成超时"
    if box[1]:
        return f"{TOOL_ERROR_MARK}获取主营构成失败: {box[1]}"
    return box[0]


def save_chain_graph(name: str, graph_yaml: str) -> str:
    """图谱自举（ISS-061 v4）：AI把梳理出的产业链结构沉淀到 configs/industry_chains_auto.yaml。

    校验 YAML 与 schema 后写入（同名覆盖），下次该行业分析直接走图谱路径。
    失败返回 TOOL_ERROR_MARK 开头（ISS-059 语义）。
    """
    import yaml as _yaml
    from src.data.industry_data import save_auto_chain

    if not name or not name.strip():
        return f"{TOOL_ERROR_MARK}链名不能为空"
    if not graph_yaml or len(graph_yaml) > 8000:
        return f"{TOOL_ERROR_MARK}图谱YAML为空或超长(>{len(graph_yaml or '')}字符)"
    try:
        graph = _yaml.safe_load(graph_yaml)
    except _yaml.YAMLError as e:
        return f"{TOOL_ERROR_MARK}YAML解析失败: {str(e)[:100]}"
    if not isinstance(graph, dict):
        return f"{TOOL_ERROR_MARK}YAML顶层必须是dict（含 sections/aliases 等键）"
    try:
        return save_auto_chain(name.strip(), graph)
    except Exception as e:
        logger.error(f"save_chain_graph失败: {e}")
        return f"{TOOL_ERROR_MARK}图谱沉淀失败: {e}"


# ══════════════════════════════════════════════════════════════
# v0.8.8 命令桥：run_command / manage_portfolio
#
# 设计（详见 docs/chat模块架构文档.md 命令桥章节）：
# - 单一真相源：复用 start.py 的 parse_input+run_cli，chat 与 REPL 走同一份
#   调度代码，行为平价由构造保证（REPL 新增命令 chat 自动可用）
# - 输出 Tee 捕获：命令输出实时回显终端（用户看进度）+ 缓冲留存喂 AI
# - 交互确认映射：REPL 的 y/N 交互 → confirm 参数；批量AI费用 mode 不带
#   confirm 会被硬门直接拒绝（不靠提示词软约束）
# ══════════════════════════════════════════════════════════════

_start_module = None  # 惰性加载的 start 模块（parse_input/run_cli 单一真相源）


class _TeeBuf(io.StringIO):
    """双写缓冲：命令输出实时回显终端 + 缓冲留存喂 AI。

    Rich Console.file 每次渲染动态解析 sys.stdout（cli/main.py 的 console
    未固定 file），redirect_stdout 后 Rich 表格与 print 全落本缓冲；非 tty
    时 Rich 自动无 ANSI。_echo 在构造时（redirect 之前）捕获真实 stdout。
    """

    def __init__(self):
        super().__init__()
        self._echo = sys.stdout

    def write(self, s):
        try:
            self._echo.write(s)
        except Exception:
            pass  # 回显失败不影响捕获
        return super().write(s)

    def flush(self):
        try:
            self._echo.flush()
        except Exception:
            pass


class _InputPatcher:
    """临时替换 builtins.input：把命令内的交互确认映射到 confirm 参数。

    语义（2026-09-02 对抗审查定稿）：
    - 提示含 "y/N"/"Y/n"（确认类）→ confirm ? "y" : "n"
    - 其他提示（如 scan market 选股菜单 "  > "）或空提示（Rich console.input
      内部调无参 input()，提示已由 console.print 打印过）→ "q"，一律非肯定
      应答，未知交互点安全跳过
    - 回显提示词+所选答案进 Tee：cli/main.py 的"是否采用此计划草稿"只存在于
      prompt 参数里，不回显则用户和 AI 都不知道这个问题出现过
    - __exit__ 恢复 builtins.input（管道模式 chat 与 REPL 共进程，泄漏会废掉
      REPL 主循环的 input）

    用法：with _InputPatcher(confirm, echo=buf): ...（__exit__ 恢复原 input）
    """

    _CONFIRM_RE = re.compile(r"[yY]/[nN]")

    def __init__(self, confirm: bool, echo=None):
        self.confirm = confirm
        self.echo = echo

    def __enter__(self):
        self._orig = builtins.input
        patcher = self

        def _input(prompt=""):
            p = prompt if isinstance(prompt, str) else ""
            if patcher._CONFIRM_RE.search(p):
                ans = "y" if patcher.confirm else "n"
            else:
                ans = "q"
            if patcher.echo is not None:
                try:
                    patcher.echo.write(f"{p}[chat自动应答: {ans}]\n")
                except Exception:
                    pass
            return ans

        builtins.input = _input
        return self

    def __exit__(self, *exc):
        builtins.input = self._orig
        return False


def _get_start_module():
    """惰性导入 start.py（REPL 入口），复用其 parse_input/run_cli。

    import start 依赖项目根在 sys.path；从非常规目录启动时兜底补路径。
    start.py 模块级副作用（chdir 项目根/代理清理/chcp）均幂等无害；main()
    有 __main__ 保护不会执行。tests/core/test_live_scan_all.py 已有先例。
    """
    global _start_module
    if _start_module is not None:
        return _start_module
    try:
        import start as _s
    except ImportError:
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        import start as _s
    _start_module = _s
    return _start_module


def _reload_portfolio_manager():
    """命令/持仓操作后无条件重读 portfolio.yaml。

    run_command/manage_portfolio 的写盘（pos plan、l/la/l all 的策略状态回写、
    pos add/rm）都发生在各自新建的 PortfolioManager 实例上；chat 的
    _portfolio_manager 若不重读，analyze_stock 随后的 update_from_strategy_decision
    会把旧快照整体写回，静默回滚刚做的修改（对抗审查点③：数据丢失向量，
    不能按"是否写操作"枚举，必须无条件做）。
    """
    global _portfolio_manager
    try:
        from src.data.portfolio import PortfolioManager
        _portfolio_manager = PortfolioManager()
    except Exception as e:
        logger.warning(f"chat重读持仓失败: {e}")


def _confirm_gate(mode: str, args: dict, confirm: bool) -> Optional[str]:
    """批量AI费用/写盘操作的 confirm 硬门：不带 confirm 直接拒绝（返回拒绝消息）。

    不靠系统提示词软约束——模型不听话也拦得住。与 REPL 的 y/N 确认语义对齐
    （l all/ba 会问"继续?y/N"，bz scan/plan all 等本身批量烧 AI）。
    """
    if confirm:
        return None
    _HINT = "该操作会批量调用 AI（耗时与费用）。请先在对话中征得用户明确同意，用户确认后带 confirm=true 重新调用"
    # ISS-078：补齐 la/lall（live_all，逐持仓 AI 深析）、scan（analyze_portfolio，
    # 逐持仓 ai_enabled=True）、events（事件层 AI 分类）——与 l all/ba 同属批量
    # AI 费用操作，此前漏门（AI 调 run_command("la") 可无确认烧 N 次 AI 费）
    if mode in ("live_scan_all", "benzong_all", "benzong_scan", "live_all", "scan", "events",
                # ISS-087：l/bz 多代码批量（live_multi/benzong_multi）与 la 同属批量 AI 费用
                "live_multi", "benzong_multi"):
        return f"{TOOL_ERROR_MARK}{_HINT}"
    if mode == "scan_market" and args.get("deep"):
        return f"{TOOL_ERROR_MARK}deep 深度分析会批量调用 AI。{_HINT}"
    if mode == "pos_plan" and (
            args.get("update")
            or str(args.get("stock_code", "")).lower() in ("all", "*", "全部")):
        return f"{TOOL_ERROR_MARK}批量生成/更新交易计划会逐持仓调用 AI。{_HINT}"
    if mode == "benzong" and args.get("refresh") and not args.get("check"):
        return f"{TOOL_ERROR_MARK}--refresh 跳过缓存重算会消耗 AI 费用。{_HINT}"
    return None


def _finalize_output(buf: _TeeBuf, note: str = "") -> str:
    """命令输出收尾：追加人话告警汇总（plain_errors）+ 空输出兜底 + 尾注。

    logging 的 StreamHandler 在进程启动时就绑定了真实 stderr，redirect_stderr
    换不掉已绑定句柄——plain_errors 的人话汇总是本次命令告警喂给 AI 的唯一通道
    （照抄 start.py 主循环收尾的 drain_new+render_summary 模式）。未命中映射表
    的原始 warning 仍只有终端用户可见，属已知局限。
    """
    out = buf.getvalue().strip()
    try:
        from src.cli.plain_errors import drain_new, render_summary
        summary = render_summary(drain_new())
        if summary:
            out = (out + "\n" + summary).strip() if out else summary
    except Exception:
        pass  # 汇总渲染失败不影响主流程
    if note:
        out = (out + "\n" + note).strip() if out else note
    return out if out else "（命令执行完成，无输出）"


def run_command(command: str, confirm: bool = False) -> str:
    """执行 REPL 原生命令（v0.8.8 命令桥）。输出实时回显终端并捕获给 AI。

    复用 start.parse_input + start.run_cli——与 REPL 完全同一份调度代码。
    """
    cmd = (command or "").strip()
    if not cmd:
        return TOOL_ERROR_MARK + "命令不能为空"
    parts = cmd.split()
    first = parts[0].lower()

    # 原始串预检（必须在 parse_input 之前）：chains rm 在解析阶段就直接执行
    # 删除（start.py chains 分支），等 parse 完再拦就晚了
    if first == "chains" and len(parts) >= 2 and parts[1].lower() in ("rm", "remove", "del"):
        return TOOL_ERROR_MARK + "chains rm 是无确认的破坏性删除（删自举图谱），请在 REPL 手动执行"
    # 会话级/交互式命令：chat 会递归子进程、noai/debug 是 REPL 进程级状态
    if first in ("chat", "q", "quit", "exit", "h", "help", "?", "noai", "debug"):
        return TOOL_ERROR_MARK + f"命令 '{first}' 属于会话级/交互式命令，在 chat 中不可用，请用户直接在 REPL 执行"
    # pos 写操作重定向到结构化工具（名称含空格等场景下字符串拼命令易碎）
    if (first == "pos" and len(parts) >= 2
            and parts[1].lower() in ("add", "a", "remove", "rm", "r", "del", "d", "overweight", "ow")):
        return TOOL_ERROR_MARK + "持仓增删/超配请使用 manage_portfolio 工具（结构化参数，confirm 硬门保护）"

    start = _get_start_module()
    buf = _TeeBuf()
    note = ""
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
                _InputPatcher(confirm, echo=buf):
            try:
                parsed = start.parse_input(cmd)
            except SystemExit:
                return TOOL_ERROR_MARK + "命令解析阶段异常退出"
            if parsed is None:
                # parse_input 已自行打印"无法识别/用法"提示（已捕获并回显）
                return _finalize_output(buf)
            mode, args = parsed

            # bz 交互式打分：_ask_score 循环吃满自动应答，出来的是垃圾分数
            if mode == "benzong" and (args.get("manual") or not args.get("meta")):
                return TOOL_ERROR_MARK + "bz --manual/空参是交互式打分，chat 中不可用；请用 bz <代码> 自动评分"

            gate = _confirm_gate(mode, args, confirm)
            if gate:
                return gate

            try:
                start.run_cli(mode, args)
            except SystemExit:
                # CLI 内部数据获取失败会 sys.exit(1)（如 l <代码>）；SystemExit 是
                # BaseException，agent 层 except Exception 掓不住，漏接会杀死整个 chat 会话
                note = "[命令异常退出] 数据源可能暂时不可用，可稍后重试"
            except KeyboardInterrupt:
                note = "[命令被用户中断]"
            except Exception as e:
                note = f"[命令错误] {type(e).__name__}: {e}"
    finally:
        # 无条件重读持仓：l/la/l all 会回写策略状态、pos plan 会写计划，
        # 陈旧 _portfolio_manager 会在下次 analyze_stock 回写时整体回滚
        _reload_portfolio_manager()
    return _finalize_output(buf, note)


def manage_portfolio(action: str = "", stock_code: str = "", stock_name: str = "",
                     price: Optional[float] = None, ratio: Optional[float] = None,
                     basis: str = "", confirm: bool = False) -> str:
    """修改本地持仓文件 portfolio.yaml（v0.8.8，写操作带 confirm 硬门）。

    add/remove/plan/overweight 复用 CLI manage_positions（含 TradePlan 草稿
    生成、超配铁律检查、总仓位警告等全部副作用，与 REPL pos 命令完全同源）；
    update 为字段级修改（PortfolioManager.update_position_fields，REPL 没有
    的新能力：只改仓位/开仓价/名称，strategy_state 等系统字段不动）。
    每次写盘走 _save()（原子写 + portfolio.yaml.bak 滚动备份）。
    """
    action = (action or "").strip().lower()
    if action in ("rm",):
        action = "remove"
    elif action == "ow":
        action = "overweight"
    elif action == "ls":
        action = "list"
    if action not in ("add", "remove", "update", "plan", "overweight", "list"):
        return TOOL_ERROR_MARK + f"未知操作: {action}（可用: add/remove/update/plan/overweight/list）"

    if action == "list":
        return get_portfolio()

    # #N 引用：最近扫描第 N 只（code+name 取自 session_state 文件，与 REPL #N 同源）
    code = (stock_code or "").strip()
    if code.startswith("#"):
        from src.cli.session_state import resolve_index, last_scan_count
        if not code[1:].isdigit():
            return TOOL_ERROR_MARK + f"无效引用: {code}（应为 #数字，如 #1）"
        r = resolve_index(int(code[1:]))
        if r is None:
            total = last_scan_count()
            hint = ("还没有扫描结果，先跑 scan market 或 bz scan" if total == 0
                    else f"最近扫描共 {total} 只，序号越界")
            return TOOL_ERROR_MARK + f"无法解析 {code}: {hint}"
        item, warning = r
        if warning:
            logger.warning(f"#N 引用提醒: {warning}")
        code = item.get("code", "")
        if not stock_name:
            stock_name = item.get("name", "")

    if not code:
        return TOOL_ERROR_MARK + "缺少股票代码（6位数字或 #N 引用）"

    # v0.8.8.6：与 H05 持仓匹配同口径规范化——SH.600519 式带前缀/空格输入此前
    # 直接按原样进写盘路径（add 会建垃圾键、update/plan 报无持仓）
    code = _normalize_code(code)

    # ISS-078 入参校验：AI 传的数值不能原样进写盘路径——此前 add 走 CLI 的
    # `price if price > 0 else None` 会把负价静默降级成"没给价格"，ratio 负数/NaN
    # 曾直达数据层（数据层校验为本次新增的兜底，这里提前拦给 AI 可读的错误）
    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return TOOL_ERROR_MARK + f"开仓价不是数字: {price!r}"
        if price <= 0:
            return TOOL_ERROR_MARK + f"开仓价需为正数，收到 {price}（不填价格请传 null）"
    if ratio is not None:
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            return TOOL_ERROR_MARK + f"仓位比例不是数字: {ratio!r}"
        if not (0 < ratio <= 1):
            return TOOL_ERROR_MARK + f"仓位比例需在 0-1 之间（0%-100%），收到 {ratio}"

    # 写操作 confirm 硬门（plan 与 REPL 平价不设门：查看/生成单只计划）
    if action in ("add", "remove", "update", "overweight") and not confirm:
        return (TOOL_ERROR_MARK + "持仓修改是写盘操作。请先在对话中向用户确认具体内容"
                f"（{action} {code} 的名称/价格/仓位等），用户明确同意后带 confirm=true 重新调用")

    from src.cli.main import manage_positions
    buf = _TeeBuf()
    note = ""
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
                _InputPatcher(confirm, echo=buf):
            try:
                if action == "update":
                    # 字段级修改：fresh 实例防读到 chat 层陈旧缓存
                    from src.data.portfolio import PortfolioManager
                    pm = PortfolioManager()
                    changed = pm.update_position_fields(
                        code,
                        current_ratio=ratio if ratio is not None else None,
                        entry_price=float(price) if price else None,
                        stock_name=stock_name or None,
                    )
                    if not changed:
                        return TOOL_ERROR_MARK + f"{code} 无持仓记录或没有可修改的字段（仓位/开仓价/名称至少提供一项）"
                    pos = pm.get_position(code)
                    buf.write(f"✓ 已更新 {code}：仓位 {pos.current_ratio:.0%}"
                              f" / 开仓价 {pos.entry_price or '-'} / 名称 {pos.stock_name or '-'}\n")
                elif action == "remove":
                    manage_positions("remove", stock_code=code)
                elif action == "plan":
                    manage_positions("plan", stock_code=code)
                elif action == "overweight":
                    manage_positions("overweight", stock_code=code, name=basis or stock_name)
                else:  # add
                    manage_positions(
                        "add",
                        stock_code=code,
                        name=stock_name,
                        price=float(price) if price else 0.0,
                        ratio=float(ratio) if ratio is not None else 0.20,
                    )
            except ValueError as e:
                return TOOL_ERROR_MARK + f"参数校验失败: {e}"
            except SystemExit:
                note = "[命令异常退出]"
            except KeyboardInterrupt:
                note = "[操作被用户中断]"
            except Exception as e:
                note = f"[持仓操作错误] {type(e).__name__}: {e}"
    finally:
        _reload_portfolio_manager()
    return _finalize_output(buf, note)


# ── v0.8.9.3 ISS-092：本地文件读写工具（沙箱）──────────────────────
# 需求：AI 可读仓库内文件、可把内容（如对话总结）写成新文件。
# 安全边界：读=仓库根内任意文件（越界/密钥文件拒绝）；写=仅 AI笔记/ 专属目录
# （AI 自己的写区，可建子目录）。越界路径一律 TOOL_ERROR_MARK 拒绝，不抛异常。

_REPO_ROOT = Path(__file__).resolve().parents[2]   # 仓库根（本文件在 src/chat/ 下）
_FILES_READ_ROOT = _REPO_ROOT                      # 读沙箱：仓库根内
_FILES_WRITE_ROOT = _REPO_ROOT / "AI笔记"           # 写沙箱：AI笔记/（新目录）
# 密钥文件：内容进入对话=发给 AI 服务商，绝不允许读
_SECRET_FILES = {"configs/settings.local.yaml", ".env"}
_MAX_FILE_IO_BYTES = 1_000_000   # 单文件读/写上限 1MB
_LIST_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache",
                   "node_modules", ".zcode", ".claude"}
_LIST_MAX_ENTRIES = 200


def _resolve_in_root(root: Path, rel_path: str) -> Path:
    """把 AI 给的相对路径解析进 root 内，返回 resolve 后的绝对路径。

    越界（../ 逃逸、绝对路径——Path 拼绝对路径会直接替换 root）抛 ValueError。
    """
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        raise ValueError("路径为空或为绝对路径（只允许沙箱内相对路径）")
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"路径越界：只允许 {root.name} 内的相对路径（{rel_path}）")
    return p


def read_file(path: str) -> str:
    """读取仓库内的文本文件（沙箱：不能读仓库外的文件）。"""
    try:
        p = _resolve_in_root(_FILES_READ_ROOT, path)
    except ValueError as e:
        return f"{TOOL_ERROR_MARK}{e}"
    rel_norm = p.relative_to(_FILES_READ_ROOT).as_posix()
    if rel_norm in _SECRET_FILES or p.name == ".env":
        return f"{TOOL_ERROR_MARK}该文件包含密钥（API Key），不允许读取: {path}"
    if not p.exists():
        return f"{TOOL_ERROR_MARK}文件不存在: {path}"
    if not p.is_file():
        return f"{TOOL_ERROR_MARK}不是普通文件（是目录?）: {path}"
    if p.stat().st_size > _MAX_FILE_IO_BYTES:
        return f"{TOOL_ERROR_MARK}文件超过 {_MAX_FILE_IO_BYTES // 1000}KB 上限，不读取: {path}"
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        return f"{TOOL_ERROR_MARK}二进制文件不支持读取: {path}"
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return f"{TOOL_ERROR_MARK}无法按 utf-8/gbk 解码（疑似二进制）: {path}"
    return f"【{path} | 共{len(text)}字】\n{text}"


def write_file(path: str, content: str) -> str:
    """把文本写入 AI笔记/ 专属目录（可带子目录自动创建；已存在则覆盖）。"""
    try:
        p = _resolve_in_root(_FILES_WRITE_ROOT, path)
    except ValueError as e:
        return f"{TOOL_ERROR_MARK}{e}"
    if not isinstance(content, str):
        return f"{TOOL_ERROR_MARK}content 必须是字符串"
    if len(content.encode("utf-8")) > _MAX_FILE_IO_BYTES:
        return f"{TOOL_ERROR_MARK}内容超过 {_MAX_FILE_IO_BYTES // 1000}KB 上限，未写入"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"{TOOL_ERROR_MARK}写入失败: {e}"
    act = "覆盖" if existed else "新建"
    return f"✅ 已{act} {p}（{len(content)}字）"


def list_files(subdir: str = "") -> str:
    """列出仓库（或其子目录）内的文件与目录，供 AI 找文件。subdir 空=仓库根。"""
    sub = (subdir or "").strip()
    try:
        base = (_FILES_READ_ROOT.resolve() if not sub
                else _resolve_in_root(_FILES_READ_ROOT, sub))
    except ValueError as e:
        return f"{TOOL_ERROR_MARK}{e}"
    if not base.exists():
        return f"{TOOL_ERROR_MARK}目录不存在: {subdir or '.'}"
    if not base.is_dir():
        return f"{TOOL_ERROR_MARK}不是目录: {subdir}"
    try:
        entries = sorted(
            base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return f"{TOOL_ERROR_MARK}列目录失败: {e}"
    lines = [f"【{subdir or '.'} 下】"]
    n = 0
    for e in entries:
        if e.is_dir() and e.name in _LIST_SKIP_DIRS:
            continue
        n += 1
        if n > _LIST_MAX_ENTRIES:
            lines.append(f"...（超过 {_LIST_MAX_ENTRIES} 项已截断，请指定子目录缩小范围）")
            n -= 1
            break
        if e.is_dir():
            lines.append(f"  {e.name}/")
        else:
            try:
                size = e.stat().st_size
                size_h = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
            except OSError:
                size_h = "?"
            lines.append(f"  {e.name}  ({size_h})")
    lines.append(f"（共 {n} 项；.git/.venv/__pycache__ 等噪音目录已隐藏）")
    return "\n".join(lines)


def get_fear_index(scope: str = "", history: str = "5,10,22,66") -> str:
    """获取市场恐慌指数（v0.8.10，纯客观计算）：总分+成分明细+多周期摘要。

    只读查询、零 AI 费用，无需 confirm。返回文本中 MISSING 状态的成分
    表示数据缺失（聚合时已剔除权重），agent 解读时严禁脑补。
    """
    try:
        from src.core.fear_index import get_fear_history_summary, get_fear_report

        report = get_fear_report(scope_text=scope)
        if "error" in report:
            return f"恐慌指数不可用: {report['error']}"
        wins = tuple(
            min(int(x), 250) for x in (history or "").split(",")
            if x.strip().isdigit() and int(x) >= 2
        ) or (5, 10, 22, 66)
        summary = get_fear_history_summary(windows=wins, make_chart=True)

        lines = [
            f"市场恐慌指数: {report.get('score')}（{report.get('tier')}）"
            f" 基准交易日: {report.get('as_of')}（0-100，越高越恐慌）",
            "成分明细（原始值/恐慌分/状态/数据源）:",
        ]
        for c in report.get("components", []):
            mark = "" if c.get("in_aggregate", True) else "（仅展示）"
            sc = "--" if c.get("score") is None else f"{c['score']:.1f}"
            raw = "--" if c.get("raw") is None else c["raw"]
            lines.append(
                f"- {c['label']}{mark}: 原始值={raw} 恐慌分={sc} "
                f"状态={c.get('status')} 来源={c.get('source')} ｜ {c.get('note', '')}")
        if summary.get("windows"):
            lines.append("多周期回顾（交易日口径）:")
            for w in summary["windows"]:
                lines.append(
                    f"- {w['label']}({w['start']}~{w['end']}): 均值{w['mean']} "
                    f"最低{w['min']}/最高{w['max']} 当前{w['cur_rank']:.0f}%分位 "
                    f"趋势{w['trend']} 样本{w['samples']}/{w['window']}")
            lines.append(f"摘要: {summary.get('headline', '')}")
        else:
            lines.append("多周期回顾: 历史序列不足（首次使用可运行 fear backfill 回填）")
        if summary.get("chart_path"):
            lines.append(f"走势图已保存: {summary['chart_path']}")
        if report.get("missing"):
            lines.append(
                f"注意: 以下成分数据缺失已剔除权重({','.join(report['missing'])})——"
                f"解读时严禁编造这些维度的数值。")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"get_fear_index失败: {e}")
        return TOOL_ERROR_MARK + f"获取恐慌指数时出错: {e}"
