"""告警人话翻译层（v0.8.7.3，方案 A+C）

用户看不懂原始技术告警（"Length mismatch""NoneType not subscriptable"）。
本模块在日志出口统一做两件事：

- 方案 A（逐条人话前缀）：命中 WARNING_PATTERNS 映射表的告警，改写为
  「⚠ 人话｜原始：技术细节」——人话给用户，原文留给排障，零信息丢失
- 方案 C（末尾汇总）：收集本次命令产生的降级告警，命令结束时由 start.py
  输出一行汇总，明确"这些是降级不是故障、结论有效"

维护纪律（AGENTS.md §五 已立规）：**新增/修改任何 logger.warning 时，
必须同一 commit 同步本文件 WARNING_PATTERNS 与 docs/报错速查手册.md**。
未命中映射表的告警原样透传（宁可夹生不可误导），不会崩。

线程安全：孤儿拉取线程也可能发日志；_recent 用锁保护，record 改写仅动自身。
"""
import logging
import threading

# (匹配子串, 人话, 影响级别)
# 影响级别："终止"=该股分析作废需重跑 / "缺失"=某路数据或信号当日缺失但结论有效
# 匹配为子串包含，按列表顺序首个命中生效。新增条目请同步 docs/报错速查手册.md。
WARNING_PATTERNS = [
    # ── 终止类（该股本次分析作废）──
    ("所有实时行情接口均失败", "这只股连价格都没拿到，本次分析已跳过——重跑即可，其余持仓不受影响", "终止"),
    ("降级返回实时数据", "只拿到价格、没拿到历史K线，本次无法做技术面分析——重跑即可", "终止"),
    ("计算技术指标失败", "指标计算出错，本次无技术面数据——重跑即可", "终止"),
    # ── 全市场快照类 ──
    ("market_cache.get_all_stocks 超时", "全市场行情快照没拉到（网络慢），该股成交额/流动性数据缺失——不影响买卖点", "缺失"),
    ("全市场行情源均失败", "新浪和东财都暂时挂了（可能被限流），60秒后自动重试——成交额/宽度数据暂缺，不影响买卖点", "缺失"),
    ("双源失败冷却中", "行情源刚失败过，正在歇60秒防封禁——成交额/宽度数据暂缺，不影响买卖点", "缺失"),
    # ── 新闻公告类 ──
    ("公告获取失败", "减持公告扫描失败——仅'实控人减持'这一个见顶信号当日缺失，其余照常", "缺失"),
    ("个股新闻返回空", "AI情绪要看的新闻为空——AI按中性处理，技术面不受影响", "缺失"),
    ("获取个股新闻失败", "新闻拉取失败——AI情绪按中性处理，技术面不受影响", "缺失"),
    ("宏观快讯返回空", "宏观新闻为空——事件检测降级，核心链路不受影响", "缺失"),
    ("获取宏观快讯失败", "宏观新闻拉取失败——事件检测降级，核心链路不受影响", "缺失"),
    # ── 高位止盈信号类 ──
    ("margin_table", "融资余额表没拉到（当天数据要收盘后才发布，凌晨属正常）——'融资余额激增'信号当日跳过", "缺失"),
    ("stock_zh_a_gdhs_detail_em 失败", "股东户数接口没返回数据——'股东户数激增'信号当日跳过", "缺失"),
    ("FundamentalAlert", "ST/业绩预告体检项失败——宁可不报也不误杀，该项当日跳过", "缺失"),
    # ── AI 类 ──
    ("AI Modifier初始化失败", "AI情绪调节未启用（API Key 未配置？）——输出为纯技术面决策", "缺失"),
    ("AI Modifier分析异常", "AI情绪调节本次缺席——纯技术面决策，核心结论不受影响", "缺失"),
    ("EventLayer初始化失败", "事件驱动层未启用——事件的加减分缺席", "缺失"),
    ("AI事件分类失败", "事件的AI分类失败——该事件按默认规则处理", "缺失"),
    # ── 大盘指数类 ──
    ("Baostock读取大盘数据超时", "沪深300趋势超时——'大盘环境'技能本次少一票", "缺失"),
    ("大盘数据不足", "大盘历史数据不够算趋势——'大盘环境'技能降级", "缺失"),
    ("Baostock未登录，跳过大盘数据获取", "baostock 登录失败——大盘趋势数据跳过", "缺失"),
    # ── 重试耗尽类（通用兜底）──
    ("超时最终失败", "多次重试后仍超时——对应数据本次缺失走降级", "缺失"),
    ("网络不可恢复错误，跳过重试", "网络错误无法重试（代理干扰常见）——对应数据走降级", "缺失"),
]

_lock = threading.Lock()
_recent: list[tuple[str, str]] = []   # 本次命令产生的 (人话, 影响级别)


def translate(msg: str):
    """按映射表翻译。返回 (人话, 影响级别) 或 (None, None)=不认识，透传。"""
    for key, plain, tag in WARNING_PATTERNS:
        if key in msg:
            return plain, tag
    return None, None


class PlainLanguageFilter(logging.Filter):
    """挂在根 handler 上：命中的告警改写为「⚠ 人话｜原始：…」，并记入会话汇总。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            raw = record.getMessage()
        except Exception:
            return True
        plain, tag = translate(raw)
        if plain is None:
            return True
        with _lock:
            _recent.append((plain, tag))
        record.msg = f"⚠ {plain}｜原始：{raw}"
        record.args = None   # 新 msg 已是成品字符串，清掉旧参数防 % 格式化错乱
        return True


def install() -> None:
    """把过滤器挂到根 logger 的所有 handler（幂等，重复调用只装一次）。

    若根 logger 尚无 handler（basicConfig 未被调用，如测试/子进程早期），
    先补一个基础 StreamHandler 再挂，保证过滤器一定生效。
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.WARNING)
    for h in root.handlers:
        if not any(isinstance(f, PlainLanguageFilter) for f in h.filters):
            h.addFilter(PlainLanguageFilter())


def drain_new() -> list[tuple[str, str]]:
    """取走自上次 drain 后新产生的 (人话, 级别) 列表（供命令结束时的汇总）。"""
    with _lock:
        items = _recent[:]
        _recent.clear()
    return items


def render_summary(items: list[tuple[str, str]]) -> str | None:
    """把本轮告警渲染成一行汇总（C 方案）。无告警返回 None。

    示例：
      📋 本次命令有 4 条数据源降级提示（均为可自愈项，不影响核心结论）：
         · 融资余额表没拉到… ×2
         · 沪深300趋势超时… ×1
    """
    if not items:
        return None
    fatal = [p for p, tag in items if tag == "终止"]
    missing = [p for p, tag in items if tag == "缺失"]

    parts = []
    if fatal:
        uniq = sorted(set(fatal))
        shown = "；".join(u[:40] for u in uniq[:2])
        more = f" 等{len(uniq)}项" if len(uniq) > 2 else ""
        parts.append(f"⚠ {len(fatal)} 条需注意（{shown}{more}——对应股票建议重跑）")
    if missing:
        uniq = sorted(set(missing))
        shown = "；".join(u[:30] for u in uniq[:3])
        more = f" 等{len(uniq)}类" if len(uniq) > 3 else ""
        parts.append(f"{len(missing)} 条数据缺失（{shown}{more}，不影响已有结论）")

    head = f"📋 本次运行 {len(items)} 条提示"
    body = "\n         · ".join(parts)
    tail = "（逐条已用 ⚠ 标在上方日志里；分类详见 docs/报错速查手册.md）"
    return f"{head}：\n         · {body}\n         {tail}"
