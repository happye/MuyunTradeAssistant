"""会话状态管理 - 让命令间能"接住"上次 scan 结果（体验重构方向 A，Step 1）

职责：
- save_last_scan(items, source): 存最近 scan 结果到 ~/.muyun/last_scan.json，并追加历史到 scan_history.jsonl
- get_last_scan() / resolve_index(n): 读最近 scan / 取第 n 只（供 #N 快捷，Step 2 用）
- append_scan_history() / get_scan_history(days): 扫描历史追加/读取（v0.8.11，scan review 复盘用）
- append_watch_event() / get_watch_active() / watch_entry_of(): 观察池事件流（v0.8.12，watch 命令用）
- persist_scan_report(items, source): scan 结果落盘 markdown 到 分析报告/scan/

设计原则：
- 失败不抛异常（IO 失败返回 None/False/空串，不阻塞主流程，与 _persist_benzong_report 一致）
- 状态文件在 ~/.muyun/（与 benzong_cache 同级，跨会话保留，不污染仓库）
- markdown 落盘复用 _persist_benzong_report 的 safe_name/时间戳/异常兜底
- items 统一格式：[{code, name, score, grade, confidence, dims, ...}]，调用方负责转换
- JSON 快照原子写（M2，v0.8.13）：先序列化 → 同目录唯一临时文件 → os.replace，
  失败清理临时文件保留旧快照；读取做结构校验，坏快照整体拒绝不冒充数据
- JSONL 历史逐行隔离（M2，v0.8.13）：单行坏编码/截断跳过并计数告警，原文件不动
"""

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# 状态目录（跨会话，不污染仓库；与 benzong_cache 同级）
_STATE_DIR = Path.home() / ".muyun"
_LAST_SCAN_FILE = _STATE_DIR / "last_scan.json"
# 扫描历史（v0.8.11，scan review 复盘用）：JSONL 追加式，一行一条扫描记录。
# 与 last_scan.json（只存最近一次）互补——历史供 scan review 验证选股涨跌表现。
# v0.8.12.1 起全量保留不 prune（导入旧记录不能被静默删），窗口由查询侧控制。
_SCAN_HISTORY_FILE = _STATE_DIR / "scan_history.jsonl"
# 当日已深分析记录（v0.8.7.9，l all 去重用）：{日期: {code: {time, source, outcome}}}。
# 只读当天键（跨天自动失效）；T05-L 起写入按原始根合并，保留其它日期与同日其它条目，
# 新记录带完成资格标记 outcome=complete（旧无标记记录不当作已核完成）。
_DEEP_ANALYZED_FILE = _STATE_DIR / "deep_analyzed.json"
# T05-L：当日已析记录的最小完成资格标记——只有完整深分析（LiveOutcome.COMPLETE）才写入
DEEP_ANALYZED_COMPLETE = "complete"

# 报告目录（项目内，gitignored；src/cli/session_state.py -> parents[2]=项目根）
_REPORT_DIR = Path(__file__).resolve().parents[2] / "分析报告" / "scan"

# 过期阈值（分钟）--超此提示 scan 可能已旧，#N 引用时提醒
_STALE_MINUTES = 30


def _atomic_write_json(path: Path, data, indent: int = 2) -> bool:
    """原子写 JSON 快照（M2，v0.8.13）。

    先序列化（失败不碰磁盘）→ 同目录唯一临时文件（tempfile.mkstemp，不复用
    固定 .tmp 名防两写者互抢）→ 写完关闭 → os.replace 原子替换。
    任何失败清理本次临时文件并保留旧文件，返回 False——读者要么看到旧快照
    要么看到完整新快照，绝不会看到半个文件。
    """
    try:
        text = json.dumps(data, ensure_ascii=False, indent=indent)
    except (TypeError, ValueError) as e:
        logger.warning(f"{path.name} 序列化失败（旧文件保留）: {e}")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, path)
            return True
        finally:
            if os.path.exists(tmp_name):  # 替换成功时临时名已不存在；失败则清理
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
    except OSError as e:
        logger.warning(f"{path.name} 写入失败（旧文件保留）: {e}")
        return False


class ScanSaveResult(NamedTuple):
    """save_last_scan 结果：快照与历史分别报告，部分成功不冒充全成（M2）。

    timestamp 是本条记录写入时的精确 ISO 时间——调用方把它透传给
    persist_scan_report 可保证报告文件名/正文与历史记录同源（M3，
    消除两次 datetime.now() 跨秒边界导致的时间戳错位）。
    __bool__ = 快照与历史都成才算 True，保持"整体成功才真"的旧布尔语义。
    """
    snapshot: bool
    history: bool
    timestamp: str = ""

    def __bool__(self) -> bool:
        return self.snapshot and self.history


def save_last_scan(items: list[dict], source: str) -> ScanSaveResult:
    """存最近 scan 结果列表，并追加一条结构化历史（scan review 复盘用）。

    本函数是全部扫描路径（scan market / bz scan / chat 工具）的唯一收口，
    在此追加历史可零 call-site 改动覆盖所有扫描类型。
    source 为来源命令（如 "bz scan AI,半导体"）。

    快照原子写（_atomic_write_json）；历史追加独立尝试——快照挂了历史仍值得记
    （复盘数据更贵），两者成败分别报告，不冒充事务完成。

    Returns: ScanSaveResult(snapshot, history, timestamp)；__bool__ = 两者都成
    """
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "count": len(items),
        "items": items,
    }
    snap_ok = _atomic_write_json(_LAST_SCAN_FILE, data)
    hist_ok = append_scan_history(items, source, timestamp=data["timestamp"])
    return ScanSaveResult(snap_ok, hist_ok, data["timestamp"])


def append_scan_history(items: list[dict], source: str,
                        timestamp: Optional[str] = None) -> bool:
    """追加一条扫描历史（JSONL，一行一条，scan review 复盘用）。

    timestamp 默认 now；旧报告导入（scan review import）传原报告时间戳回填历史。
    纯追加不重写不 prune：导入的旧记录（可能 >90 天）不能被后续扫描静默删除
    （对抗审查 P0-1 实证过 prune 会同轮互删）；体量有界（~百行/年），展示窗口
    由查询侧（scan review 的 days 参数）控制，与存储解耦。
    失败不抛异常（返回 False，不阻塞扫描主流程）。
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "count": len(items),
            "items": items,
        }
        with _SCAN_HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as e:
        logger.warning(f"复盘历史追加失败: {e}")
        return False


def _read_scan_history_rows() -> tuple[list[dict], int]:
    """读历史原始行。返回 (可解析行列表, 损坏行数)。文件不存在返回空。

    按物理行读取并逐行解码（M2）：单行无效 UTF-8 或截断（崩溃残留）不拖垮
    其余有效行；坏行计数上报，原文件绝不动（保留供人工恢复）。
    """
    try:
        if not _SCAN_HISTORY_FILE.exists():
            return [], 0
        raw_lines = _SCAN_HISTORY_FILE.read_bytes().splitlines()
    except OSError as e:
        logger.debug(f"scan_history.jsonl 读取失败: {e}")
        return [], 0
    rows, corrupt = [], 0
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            corrupt += 1
            continue
        try:
            r = json.loads(text)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(r, dict):
            rows.append(r)
        else:
            corrupt += 1
    return rows, corrupt


def get_scan_history(days: Optional[int] = None) -> list[dict]:
    """读扫描历史。按 timestamp 倒序；days 非空时只保留最近 N 天。

    损坏行跳过并告警计数（不假装完整）；timestamp 混排 None/数字/字符串时
    排序键统一转字符串，不 TypeError。
    """
    rows, corrupt = _read_scan_history_rows()
    if corrupt:
        logger.warning(f"scan_history.jsonl 有 {corrupt} 行损坏已跳过（复盘可能少几条记录）")
    rows.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = [r for r in rows if str(r.get("timestamp") or "") >= cutoff]
    return rows


def _valid_last_scan(data) -> bool:
    """last_scan 快照结构校验（M2）：根 dict / timestamp 字符串可解析 /
    items 是列表且每项是带非空 code 的 dict。

    只做整体校验——坏快照整体拒绝，绝不过滤坏条目后重排（#N 会错位指向别的股票）。
    """
    if not isinstance(data, dict):
        return False
    ts = data.get("timestamp")
    if not isinstance(ts, str):
        return False
    try:
        datetime.fromisoformat(ts)
    except ValueError:
        return False
    items = data.get("items")
    if not isinstance(items, list):
        return False
    for it in items:
        if not isinstance(it, dict) or not str(it.get("code") or "").strip():
            return False
    return True


def get_last_scan() -> Optional[dict]:
    """读最近 scan。返回 {timestamp, source, count, items} 或 None。

    坏快照（损坏 JSON / 结构校验不过）→ 告警并返回 None（调用方按"无扫描"
    处理，告警已向用户说明需重扫）。
    """
    try:
        if not _LAST_SCAN_FILE.exists():
            return None
        data = json.loads(_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"最近扫描快照读取失败: {e}")
        return None
    if not _valid_last_scan(data):
        logger.warning("最近扫描快照损坏已忽略（结构校验不过）——请重新 scan 后再用 #N 接续")
        return None
    return data


def resolve_index(n: int) -> Optional[tuple[dict, str]]:
    """取最近 scan 第 n 只（1-based）。供 #N 快捷用（Step 2）。

    Returns:
        (item, warning): n 合法；warning 非空表示过期等提示
        None: 无 scan 或 n 越界（调用方据此提示"先跑 scan"或"序号超出"）
    """
    data = get_last_scan()
    if not data:
        return None
    items = data.get("items", [])
    if n < 1 or n > len(items):
        return None
    return items[n - 1], _stale_warning(data.get("timestamp"))


def last_scan_count() -> int:
    """最近 scan 的数量（无 scan 返回 0）。供 #N 越界提示用。"""
    data = get_last_scan()
    return data.get("count", 0) if data else 0


def _stale_warning(timestamp: Optional[str]) -> str:
    """若 scan 超 _STALE_MINUTES 分钟，返回过期提示，否则空串。"""
    if not timestamp:
        return ""
    try:
        ts = datetime.fromisoformat(timestamp)
        minutes = (datetime.now() - ts).total_seconds() / 60
        if minutes > _STALE_MINUTES:
            return f"最近 scan 是 {int(minutes)} 分钟前，结果可能已过期"
    except (ValueError, TypeError):
        return ""
    return ""


def get_deep_analyzed() -> dict:
    """今天的已深分析记录 {code: {"time": "HH:MM", "source": str}}。非今天返回 {}。

    供 l all 去重：同一天内重复 l all 默认跳过已分析过的股票（v0.8.7.9）。
    根对象非 dict（数组/null/字符串，M2）→ 返回 {} 不崩（防 .get AttributeError）。
    """
    try:
        if not _DEEP_ANALYZED_FILE.exists():
            return {}
        data = json.loads(_DEEP_ANALYZED_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        rec = data.get(datetime.now().strftime("%Y-%m-%d"))
        if not isinstance(rec, dict):
            return {}
        # 过滤脏条目（值必须是 {time, source} 结构），防调用方 .get("time") 崩
        return {k: v for k, v in rec.items() if isinstance(v, dict)}
    except (OSError, ValueError, RecursionError) as e:
        # T05-L：ValueError 覆盖 JSONDecodeError/UnicodeDecodeError；超深嵌套坏文件
        # 的 RecursionError 同样按损坏处理（返回 {}），不让 l all 在确认前崩溃
        logger.warning(f"get_deep_analyzed 失败: {e}")
        return {}


def deep_record_complete(rec) -> bool:
    """当日已析记录是否带完成资格标记（T05-L）。

    旧版记录（无 outcome，修复前可能把仅行情当成已析）与任何其它值一律 False——
    只用于 l all 是否跳过的判断，不授予缓存/信号资格。
    """
    return isinstance(rec, dict) and rec.get("outcome") == DEEP_ANALYZED_COMPLETE


def mark_deep_analyzed(codes: list, source: str = "") -> bool:
    """把今天**完整**深分析的股票记入 deep_analyzed.json（带完成资格标记）。失败不抛异常。

    T05-L：只应在 analyze_live 返回 LiveOutcome.COMPLETE 后调用。写入按原始根合并：
    保留其它日期与同日其它条目（含坏/旧条目，原样不动）；文件存在但无法解析、
    根不是 dict 或当天节点不是 dict → 返回 False 且不改写原文件（不把坏根洗成新根）。
    空文件（0 字节/仅空白）视为无记录。
    """
    try:
        root: dict = {}
        if _DEEP_ANALYZED_FILE.exists():
            text = _DEEP_ANALYZED_FILE.read_bytes().decode("utf-8")
            if text.strip():
                root = json.loads(text)
        if not isinstance(root, dict):
            logger.debug("deep_analyzed.json 根类型非法：拒绝写入（原文件保留）")
            return False
        today = datetime.now().strftime("%Y-%m-%d")
        day = root.get(today, {})
        if not isinstance(day, dict):
            logger.debug("deep_analyzed.json 当日节点类型非法：拒绝写入（原文件保留）")
            return False
        now_hm = datetime.now().strftime("%H:%M")
        for c in codes:
            if c:
                day[str(c)] = {"time": now_hm, "source": source,
                               "outcome": DEEP_ANALYZED_COMPLETE}
        root[today] = day
        return _atomic_write_json(_DEEP_ANALYZED_FILE, root, indent=1)
    except (ValueError, RecursionError) as e:
        # ValueError 覆盖 JSONDecodeError/UnicodeDecodeError/超大整数字面量；
        # RecursionError 为超深嵌套坏文件——都按无法解析拒写，绝不抛出打断批次
        logger.debug(f"deep_analyzed.json 无法解析：拒绝写入（原文件保留）: {type(e).__name__}")
        return False
    except OSError as e:
        logger.warning(f"mark_deep_analyzed 失败: {e}")
        return False


# ── 观察池（v0.8.12）────────────────────────────────────────

# 观察池事件流（JSONL，一行一事件）：add=入池（记入池价锚点），remove=出池。
# 在池 = add 减去后续 remove 的事件重放。规模靠入池去重 + 手动 rm 控制，不做时间 prune。
_WATCH_FILE = _STATE_DIR / "watchlist.jsonl"


def append_watch_event(action: str, items: list[dict], source: str) -> bool:
    """追加观察池事件。action 仅限 add/remove；失败返回 False（调用方决定措辞）。"""
    if action not in ("add", "remove") or not items:
        return False
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "source": source,
            "items": items,
        }
        with _WATCH_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as e:
        logger.debug(f"watchlist.jsonl 写入失败: {e}")
        return False


def get_watch_events() -> list[dict]:
    """读观察池全部事件（按时间升序）。

    按物理行逐行解码（M2）：坏编码/截断行隔离计数告警，不拖垮其余事件；
    timestamp 混排统一转字符串排序，不 TypeError。
    """
    try:
        if not _WATCH_FILE.exists():
            return []
        raw_lines = _WATCH_FILE.read_bytes().splitlines()
    except OSError as e:
        logger.debug(f"watchlist.jsonl 读取失败: {e}")
        return []
    rows, corrupt = [], 0
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            corrupt += 1
            continue
        try:
            r = json.loads(text)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(r, dict):
            rows.append(r)
        else:
            corrupt += 1
    if corrupt:
        logger.warning(f"watchlist.jsonl 有 {corrupt} 行损坏已跳过（观察池可能少几条记录）")
    rows.sort(key=lambda r: str(r.get("timestamp") or ""))
    return rows


def get_watch_active() -> list[dict]:
    """重放事件得「在池记录」：remove 抵消对应 code 的 add。

    返回结构与 scan 记录同构 [{timestamp(入池时间), source, items:[{code,...}]}]，
    供 watch 命令直接复用 scan review 的复盘引擎。
    结构坏的事件（items 非列表）跳过并计数告警，不拖垮重放也不静默消失（M2）。
    """
    active: dict = {}   # code -> 在池记录
    skipped = 0
    for ev in get_watch_events():
        action = ev.get("action")
        items = ev.get("items")
        if not isinstance(items, list):
            skipped += 1
            continue
        for it in items:
            code = it.get("code") if isinstance(it, dict) else None
            if not code:
                continue
            if action == "add":
                rec = active.get(code)
                if rec is None:
                    rec = {"timestamp": ev.get("timestamp", ""),
                           "source": ev.get("source", ""), "items": []}
                    active[code] = rec
                rec["items"] = [it]   # 同 code 防御性取最新 add 的 item
            elif action == "remove":
                active.pop(code, None)
    if skipped:
        logger.warning(f"watchlist.jsonl 有 {skipped} 条事件结构异常已跳过（观察池可能少几只）")
    return sorted(active.values(), key=lambda r: str(r.get("timestamp") or ""))


# ── 批量任务账本（v0.8.17，plan/ C2；T05-H 运行隔离）────────────
# l all / la / ba / l 多代码 的任务清单与逐项状态：中断后 tasks 命令可见
# 进度与失败项。T05-H 起每次启动都是独立 run（不可混淆 run_id + 完整 roster），
# 不继承上一 run 的 done/failed/degraded，旧 run 晚到 mark 一律 fail-closed。
# 只做可见性记录，不引入通用工作流/队列/跨进程事务；保留最近 5 条运行记录。
# 重要：这里只记「本次运行实际返回的结果」，不改变各命令原有缓存规则，也不代表
# 缓存消费资格（任务记录 ≠ 缓存命中/可跳过）。

_BATCH_TASKS_FILE = _STATE_DIR / "batch_tasks.json"
_BATCH_TASKS_KEEP = 5


def _load_batch_tasks() -> dict:
    """读批量任务账本。根类型/结构坏 → 重置（可见性数据，损坏不值得人工抢救）。"""
    try:
        if not _BATCH_TASKS_FILE.exists():
            return {"tasks": []}
        data = json.loads(_BATCH_TASKS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            return {"tasks": []}
        return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.debug(f"batch_tasks.json 读取失败(重置): {e}")
        return {"tasks": []}


def _new_batch_run_id() -> str:
    """本 run 的不可混淆令牌：启动时间 + 完整 UUID（同秒连续启动也不会重复）。

    只存 UUID 短前缀会在同秒两次启动时碰撞（≈24bit），因此保存完整 32 位 hex。
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex


def _canonical_batch_snapshot(kind, raw):
    """配置身份描述的写入端按 kind 白名单门（T05-I；T05-M 按 kind 选门）。

    只有通过该 kind 同一口径校验（ba = ``muyun.ba.exec_param_snapshot/v1``；
    live 三类 = ``muyun.live.config_fingerprint/v1``）的描述才允许落盘，并只保存
    **规范化副本**——绝不把调用方原始 dict、额外键或秘密写进账本。不可用/跨 kind
    时返回 None（run 仍登记为未冻结）；校验异常只记固定原因类型，不拼接任意异常正文。
    """
    try:
        return validate_batch_snapshot(kind, raw)
    except Exception as e:
        logger.debug("batch_task_start 配置身份描述校验失败(按未冻结记录，"
                     f"原因类型 {type(e).__name__})")
        return None


# ── T05-L：live 深分析批（l 多代码 / la / l all）的启动预算描述 ──────────
# 与 ba 的 muyun.ba.budget/v1 分开的独立 schema：按 kind 读写同门校验，ba 合同不变；
# 只落有限秒数，不落进程内单调截止点。到期只阻止「开始下一只」，不是硬墙/强取消。
LIVE_BATCH_KINDS = ("l", "la", "l all")
LIVE_BUDGET_SCHEMA = "muyun.live.budget/v1"
LIVE_BUDGET_POLICY = "stop_starting_new_stock"
LIVE_BUDGET_STOP_REASON = "budget_exhausted"
_LIVE_BUDGET_KEYS = ("budget_seconds", "policy", "schema")


def _finite_positive_seconds(value) -> Optional[float]:
    """有限正数秒归一化；bool/非数值/nan/inf/<=0 一律 None（不静默当无预算）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def validate_live_budget_descriptor(raw) -> Optional[dict]:
    """校验 live 批启动预算描述：schema/精确键集/策略/有限正数秒全过才返回规范化副本。

    缺/坏/额外键/布尔/nan/inf/非正数/非白名单策略/ba schema 一律 None（显示未记录，
    绝不渲染原始字符串）。
    """
    if not isinstance(raw, dict) or set(raw) != set(_LIVE_BUDGET_KEYS):
        return None
    if raw.get("schema") != LIVE_BUDGET_SCHEMA or raw.get("policy") != LIVE_BUDGET_POLICY:
        return None
    seconds = _finite_positive_seconds(raw.get("budget_seconds"))
    if seconds is None:
        return None
    return {"schema": LIVE_BUDGET_SCHEMA, "policy": LIVE_BUDGET_POLICY,
            "budget_seconds": seconds}


def live_budget_descriptor(seconds) -> Optional[dict]:
    """有限正数秒 → live 批账本预算描述；非法返回 None（写侧与读侧同一道门）。"""
    return validate_live_budget_descriptor({
        "schema": LIVE_BUDGET_SCHEMA, "policy": LIVE_BUDGET_POLICY,
        "budget_seconds": seconds,
    })


def validate_batch_budget(kind, raw) -> Optional[dict]:
    """按 kind 选预算描述门：ba → batch_scorer 原合同；live 三类 → live 合同；其它 None。

    跨 kind 的描述（live run 带 ba schema 或反之）一律不通过，避免互相洗白。
    """
    if kind == "ba":
        from src.core.benzong.batch_scorer import validate_budget_descriptor
        return validate_budget_descriptor(raw)
    if kind in LIVE_BATCH_KINDS:
        return validate_live_budget_descriptor(raw)
    return None


def batch_budget_stop_reason(kind) -> Optional[str]:
    """该 kind 的白名单预算终止原因；不支持预算的 kind 返回 None。"""
    if kind == "ba":
        from src.core.benzong.batch_scorer import BUDGET_STOP_REASON
        return BUDGET_STOP_REASON
    if kind in LIVE_BATCH_KINDS:
        return LIVE_BUDGET_STOP_REASON
    return None


# ── T05-M：live 批冻结配置的非秘密身份指纹 ───────────────────────────
# 只覆盖有限白名单分析参数：decision/skills/event/entry_exit/fusion 整节，ai 只取
# enabled/provider/当前 provider 的 model/direction_aware_adjustment/request_timeout/
# max_retries/modifier。密钥/凭据/URL 类键与 URL/sk- 形状字符串递归剔除；只落
# {"schema","scope","sha256"}。不保存配置内容/URL/密钥，也没有密钥摘要；不证明技能
# YAML 内容/数据/提示词/端点身份，不授予缓存命中或投资信号资格。
LIVE_CONFIG_SCHEMA = "muyun.live.config_fingerprint/v1"
LIVE_CONFIG_SCOPE = "live_batch_effective_config"
_LIVE_CONFIG_KEYS = ("schema", "scope", "sha256")
_LIVE_CONFIG_SECTIONS = ("decision", "skills", "event", "entry_exit", "fusion")
_LIVE_AI_KEYS = ("enabled", "provider", "direction_aware_adjustment",
                 "request_timeout", "max_retries", "modifier")
# ai.provider 合法省略时的有效默认值：与真实消费者一致（AIModifier / EventLayer /
# chat agent / claim_llm_extractor 均为 config.get("provider", "deepseek")）。
# 只用它定位「当前 provider 的 model」，不把默认值写进指纹（不扩默认参数语义）。
_DEFAULT_AI_PROVIDER = "deepseek"
# 键名里出现这些词（大小写/下划线/连字符/驼峰变体）即当作秘密或端点剔除；
# 逐词判定，避免 keyword_scan 这类普通键被误伤。已知保守过度剔除：以 key/secret/
# token/url 等词尾结尾的普通键（如 monkey）也会被剔除——只影响指纹覆盖度，方向安全。
_SECRET_KEY_WORDS = frozenset({
    "key", "keys", "apikey", "secret", "secrets", "token", "tokens",
    "password", "passwd", "pwd", "url", "urls", "uri", "endpoint", "endpoints",
    "credential", "credentials", "auth",
})
_SECRET_KEY_SUFFIXES = ("key", "secret", "token", "password", "passwd", "pwd",
                        "url", "uri", "endpoint")
_URL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+.\-]*://")
_SK_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])sk[-_]", re.IGNORECASE)
_LIVE_SHA256_RE = re.compile(r"[0-9a-f]{64}")
# 递归上限：既限制异常深嵌套，也让循环引用以 fail-closed 结束（而非 RecursionError）
_LIVE_CONFIG_MAX_DEPTH = 32
_DROPPED = object()   # 「该项被当作秘密剔除」的内部标记（不是合法值）


def _secret_key_name(name: str) -> bool:
    """键名是否密钥/凭据/端点类（大小写、下划线/连字符/驼峰变体都算）。"""
    words = []
    for chunk in re.split(r"[^A-Za-z0-9]+", str(name)):
        if not chunk:
            continue
        words.append(chunk.lower())
        for token in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+", chunk):
            words.append(token.lower())
    for word in words:
        if word in _SECRET_KEY_WORDS:
            return True
        if len(word) > 4 and any(word.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES):
            return True
    return False


def _secret_string(value: str) -> bool:
    """值是否含**独立** URL 或 sk- 凭据 token（前后空白/标点/引号/中文相邻/文本中均可）。

    按 token 边界 search（ASCII 字母数字之外的左边界），所以 ``(sk-…)``、``"sk-…"``、
    ``凭据=(sk-…)``、``详见https://…。`` 都命中；单词内部的普通 sk 字样（task-run /
    disk-usage / sklearn）不误识为 token。只剔这两类，不做通用秘密识别——risk-off、
    普通文本、相对路径的变化仍然可见。命中即剔除**整字符串**，绝不把 URL 端点哈希进
    配置身份。已知保守过度剔除保留：``sk-learn`` 这类 sk- 前缀词仍被当作凭据剔除。
    """
    text = str(value)
    return bool(_URL_TOKEN_RE.search(text) or _SK_TOKEN_RE.search(text))


def _sanitize_live_config(value, depth: int = 0):
    """白名单配置的非秘密规范化副本。

    返回 ``(副本, True)``；被当作秘密剔除返回 ``(_DROPPED, True)``；
    无法安全规范化（非字符串键/NaN/inf/bytes/datetime/超深或循环结构）返回
    ``(None, False)``——绝不静默用 repr 冒充可哈希身份。
    """
    if depth > _LIVE_CONFIG_MAX_DEPTH:
        return None, False
    if value is None or isinstance(value, bool):
        return value, True
    if isinstance(value, int):
        return value, True
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, False
        return value, True
    if isinstance(value, str):
        if _secret_string(value):
            return _DROPPED, True
        return value, True
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return None, False
            if _secret_key_name(key):
                continue
            sub, ok = _sanitize_live_config(item, depth + 1)
            if not ok:
                return None, False
            if sub is _DROPPED:
                continue
            cleaned[key] = sub
        return cleaned, True
    if isinstance(value, (list, tuple)):
        items = []
        for entry in value:
            sub, ok = _sanitize_live_config(entry, depth + 1)
            if not ok:
                return None, False
            if sub is _DROPPED:
                continue
            items.append(sub)
        return items, True
    return None, False


def _live_config_view(config: dict):
    """按实际配置结构取白名单有效值；结构不可安全解释时返回 None（fail-closed）。"""
    view = {}
    for section in _LIVE_CONFIG_SECTIONS:
        if section not in config:
            continue
        body = config[section]
        if not isinstance(body, dict):
            return None
        view[section] = body
    if "ai" in config:
        ai = config["ai"]
        if not isinstance(ai, dict):
            return None
        effective = {name: ai[name] for name in _LIVE_AI_KEYS if name in ai}
        if "provider" in ai:
            provider = ai["provider"]
            if not isinstance(provider, str):
                return None
        else:
            # 合法省略 provider：按真实消费者默认值定位当前 provider 的 model
            provider = _DEFAULT_AI_PROVIDER
        provider_cfg = ai.get(provider)
        if isinstance(provider_cfg, dict) and "model" in provider_cfg:
            effective["model"] = provider_cfg["model"]
        view["ai"] = effective
    return view


def derive_live_config_descriptor(config) -> Optional[dict]:
    """从批冻结后的有效配置派生 live 配置指纹描述；不可安全派生返回 None。

    只输出 ``{"schema","scope","sha256"}``：不落配置内容、URL、密钥或密钥摘要。
    fail-closed：非 dict / 白名单节结构非法 / 非字符串键 / NaN/inf / 非 JSON 类型 /
    循环结构一律 None（调用方显示未冻结，绝不冒称已冻结身份）。
    """
    if not isinstance(config, dict):
        return None
    view = _live_config_view(config)
    if view is None:
        return None
    cleaned, ok = _sanitize_live_config(view)
    if not ok or not isinstance(cleaned, dict):
        return None
    try:
        payload = json.dumps({"schema": LIVE_CONFIG_SCHEMA, "scope": LIVE_CONFIG_SCOPE,
                              "config": cleaned},
                             sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"schema": LIVE_CONFIG_SCHEMA, "scope": LIVE_CONFIG_SCOPE, "sha256": digest}


def validate_live_config_descriptor(raw) -> Optional[dict]:
    """校验账本/内存里的 live 配置指纹：schema/固定 scope/精确键集/64 位小写 hex。

    返回**规范化副本**（不信任调用方原始 dict/额外键）；旧/缺/坏/被篡改描述返回
    None，由调用方显示未冻结，绝不当作已验证身份。
    """
    if not isinstance(raw, dict) or set(raw) != set(_LIVE_CONFIG_KEYS):
        return None
    if raw.get("schema") != LIVE_CONFIG_SCHEMA:
        return None
    if raw.get("scope") != LIVE_CONFIG_SCOPE:
        return None
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _LIVE_SHA256_RE.fullmatch(digest):
        return None
    return {"schema": LIVE_CONFIG_SCHEMA, "scope": LIVE_CONFIG_SCOPE, "sha256": digest}


def validate_batch_snapshot(kind, raw) -> Optional[dict]:
    """按 kind 选配置身份门（T05-M）：ba 原执行参数快照合同，live 三类配置指纹。

    跨 kind 描述（live run 带 ba 快照或反之）与不支持的 kind 一律 None，避免互相
    洗白；写侧（batch_task_start）与读侧（tasks 展示）共用这一道门。
    """
    if kind == "ba":
        from src.core.benzong.batch_scorer import validate_exec_snapshot
        return validate_exec_snapshot(raw)
    if kind in LIVE_BATCH_KINDS:
        return validate_live_config_descriptor(raw)
    return None


def _canonical_batch_budget(kind, raw):
    """启动预算描述的写入端白名单门（T05-J；T05-L 按 kind 选门）。

    只有通过该 kind 同一口径校验（schema/精确键集/策略/有限正数秒）的描述
    才允许落盘，并只保存**规范化副本**——绝不把调用方原始 dict、额外键或任意字符串
    写进账本。不可用时返回 None（run 仍登记，预算显示未记录）；校验异常只记固定
    原因类型，不拼接任意异常正文。
    """
    try:
        return validate_batch_budget(kind, raw)
    except Exception as e:
        logger.debug("batch_task_start 预算描述校验失败(按未记录处理，"
                     f"原因类型 {type(e).__name__})")
        return None


def batch_task_start(kind: str, key: str, codes: list,
                     snapshot: Optional[dict] = None,
                     budget: Optional[dict] = None) -> Optional[str]:
    """批量任务启动：新建独立 run 并返回令牌；写入失败返回 None（不抛）。

    - 每次调用插入一条新运行记录（保留最近 5 条），不按 kind+key 合并，
      也不继承上一 run 的 done/failed/degraded
    - codes 完整复制为本 run 不可变 roster；后续 mark 必须同时核令牌与 roster
    - ts_started 只标本次运行开始，不是全批分析数据的 as_of
    - 仅可见性记录：不改变调用方原有缓存/跳过语义，不承诺跨进程事务
    - snapshot（T05-I，可选）：ba 的执行参数描述，与 run 同时登记。写入前按与
      读侧相同的 schema/精确键集/类型/范围/hash 口径校验（含 URL/凭据形状拒绝），
      合法才保存**规范化副本**；非法/额外键/坏 hash 一律不落盘，run 仍正常登记为
      未冻结。它只是元数据，不授予缓存命中/复用或投资信号资格；未提供时记录保持
      旧形状（旧调用方兼容，且不引入额外依赖）。
      T05-M：按 kind 选门——ba 只认 ba 执行参数快照，l/la/l all 只认 live 配置指纹
      （muyun.live.config_fingerprint/v1）；跨 kind 描述不落盘。run 与该描述在
      同一次原子写里落盘，不产生「有 run 无描述」的中间状态。
    - budget（T05-J，可选）：ba 共用批的启动预算描述。写入前按 schema/精确键集/
      策略/有限正数秒校验，合法才保存**规范化副本**（只落秒数，绝不落进程内单调
      截止点）；非法/额外键/nan/inf/bool 一律不落盘（显示未记录），run 仍登记。
      未提供时记录保持旧形状（旧账本/旧调用方兼容，无迁移、不改写旧文件）。
      T05-L：按 kind 选门——ba 只认 ba schema，l/la/l all 只认 live schema；
      跨 kind 描述不落盘。
    """
    try:
        roster = [str(c) for c in codes]
        now = datetime.now().isoformat(timespec="seconds")
        run_id = _new_batch_run_id()
        data = _load_batch_tasks()
        tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
        record = {
            "run_id": run_id,
            "kind": kind,
            "key": key,
            "codes": roster,
            "done": {},
            "degraded": {},
            "failed": {},
            "ts_started": now,
            "ts_updated": now,
        }
        if snapshot is not None:
            canonical = _canonical_batch_snapshot(kind, snapshot)
            if canonical is not None:
                record["exec_snapshot"] = canonical
        if budget is not None:
            canonical_budget = _canonical_batch_budget(kind, budget)
            if canonical_budget is not None:
                record["budget"] = canonical_budget
        tasks.insert(0, record)
        data["tasks"] = tasks[:_BATCH_TASKS_KEEP]
        if not _atomic_write_json(_BATCH_TASKS_FILE, data, indent=1):
            return None
        return run_id
    except Exception as e:
        logger.debug(f"batch_task_start 失败(不影响批量主流程): {e}")
        return None


def batch_task_mark(kind: str, key: str, code: str, ok: bool, note: str = "",
                    run_id: Optional[str] = None, degraded: bool = False) -> bool:
    """批量任务逐项状态更新；返回是否真的记账成功（失败不抛，返回 False）。

    fail-closed（任一不满足 → False 且不写文件、不改新旧记录）：
    - 必须携带本次 run 令牌；缺令牌或旧 run 晚到一律拒绝
    - kind+key 当前最新 run 必须正是该令牌
    - code 必须在该 run 的 roster 内
    - ok / degraded 必须是 bool（未知/坏结果不伪造成功）
    - T05-L：done/degraded/failed 映射缺失或非 dict、not_started 字段存在但非 dict
      （含显式 null）→ 无法核定旧状态，拒绝改写（不把坏映射重置成 {} 洗白）
    完成、降级、失败三种状态互斥转移；写盘失败返回 False（原子写保留旧文件）。
    """
    try:
        if not isinstance(run_id, str) or not run_id:
            return False
        if not isinstance(ok, bool) or not isinstance(degraded, bool):
            return False
        code = str(code)
        data = _load_batch_tasks()
        tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
        latest = next((t for t in tasks if t.get("kind") == kind and t.get("key") == key), None)
        if latest is None or latest.get("run_id") != run_id:
            return False
        roster = [str(c) for c in latest.get("codes") or []]
        if code not in roster:
            return False
        # T05-J fix2：已确认未开始的同 run 晚到结果不得洗成已开始；not_started
        # 映射损坏（非 dict）时无法核定，一律拒绝改写（不抹未知）。
        # T05-L：字段存在即须为 dict——显式 null 同样是坏映射，不当作“未登记”。
        if "not_started" in latest:
            ns_map = latest.get("not_started")
            if not isinstance(ns_map, dict) or code in ns_map:
                return False
        for name in ("done", "degraded", "failed"):
            if not isinstance(latest.get(name), dict):
                return False
        now = datetime.now().isoformat(timespec="seconds")
        if ok:
            if degraded:
                latest["done"].pop(code, None)
                latest["failed"].pop(code, None)
                latest["degraded"][code] = (note or "部分维度未评出")[:120]
            else:
                latest["degraded"].pop(code, None)
                latest["failed"].pop(code, None)
                latest["done"][code] = now
        else:
            latest["done"].pop(code, None)
            latest["degraded"].pop(code, None)
            latest["failed"][code] = (note or "失败")[:120]
        latest["ts_updated"] = now
        if not _atomic_write_json(_BATCH_TASKS_FILE, data, indent=1):
            return False
        return True
    except Exception as e:
        logger.debug(f"batch_task_mark 失败(不影响批量主流程): {e}")
        return False


def batch_task_not_started(kind: str, key: str, code: str,
                           run_id: Optional[str] = None,
                           reason: str = "") -> bool:
    """登记调度器确认「从未开始」的代码（T05-J 预算到期）；返回是否真的记账成功。

    fail-closed（任一不满足 → False 且不写文件、不改新旧记录）：
    - 必须携带本次 run 令牌；kind+key 当前最新 run 必须正是该令牌
    - code 必须在该 run 的 roster 内
    - reason 必须是该 kind 的白名单预算终止原因（budget_exhausted）
    - 该 run 必须已登记该 kind 的合法预算描述（T05-L：ba 认 ba schema，
      l/la/l all 认 live schema；无预算 run 不允许凭空出现未开始）
    - 与 done/degraded/failed 互斥：已接受结果不得改判为未开始
    - T05-L：run 级 stop_reason 已存在但非白名单，或已有未开始项却缺 stop_reason
      → 不得借新 code 把旧坏/缺原因洗白
    只描述可见性，不授予缓存资格；不写绝对单调钟值，只落原因字符串。
    """
    try:
        if not isinstance(run_id, str) or not run_id:
            return False
        stop_reason = batch_budget_stop_reason(kind)
        if stop_reason is None or reason != stop_reason:
            return False
        code = str(code)
        data = _load_batch_tasks()
        tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
        latest = next((t for t in tasks if t.get("kind") == kind and t.get("key") == key), None)
        if latest is None or latest.get("run_id") != run_id:
            return False
        roster = [str(c) for c in latest.get("codes") or []]
        if code not in roster:
            return False
        if validate_batch_budget(kind, latest.get("budget")) is None:
            return False
        if "stop_reason" in latest and latest.get("stop_reason") != stop_reason:
            return False
        # T05-J fix2：已有 done/degraded/failed 映射必须可读（dict）才能核定
        # “是否已有结果”；损坏/缺失映射一律 fail-closed，不写未开始、不抹未知。
        for name in ("done", "degraded", "failed"):
            current = latest.get(name)
            if not isinstance(current, dict):
                return False
            if code in current:
                return False
        # final_gate B：not_started 只在字段**缺失**时新建 {}；字段已存在但非 dict
        # （list/string/null/bool）时无法核定旧状态 → 拒绝且不改盘，不重置抹掉未知。
        # 已有 dict 中的其它坏项原样保留；同 code 已有非白名单原因也不覆盖。
        if "not_started" in latest:
            not_started = latest.get("not_started")
            if not isinstance(not_started, dict):
                return False
            if code in not_started:
                # 幂等成功必须与 tasks 读门一致：run.stop_reason 也须为白名单原因；
                # 缺失/非法时返回 False 且不改盘（不自动修补旧记录）。
                return (not_started.get(code) == stop_reason
                        and latest.get("stop_reason") == stop_reason)
            # T05-L：已有未开始项却缺 run 级原因 → 旧状态不可核定，新 code 不得补写洗白
            if not_started and "stop_reason" not in latest:
                return False
        else:
            not_started = {}
            latest["not_started"] = not_started
        not_started[code] = stop_reason
        latest["stop_reason"] = stop_reason
        latest["ts_updated"] = datetime.now().isoformat(timespec="seconds")
        if not _atomic_write_json(_BATCH_TASKS_FILE, data, indent=1):
            return False
        return True
    except Exception as e:
        logger.debug(f"batch_task_not_started 失败(不影响批量主流程): {e}")
        return False


def get_recent_batch_tasks(n: int = 3) -> list:
    """读最近 n 个批量任务（最新在前）。坏记录跳过。"""
    tasks = _load_batch_tasks().get("tasks") or []
    return [t for t in tasks if isinstance(t, dict)][:n]


def watch_entry_of(code: str) -> Optional[dict]:
    """某股票的在池信息 {timestamp(入池时间), source, item}；不在池返回 None。"""
    code = str(code).strip()
    for rec in get_watch_active():
        for it in rec.get("items") or []:
            if (it or {}).get("code") == code:
                return {"timestamp": rec.get("timestamp", ""),
                        "source": rec.get("source", ""), "item": it}
    return None


def persist_scan_report(items: list[dict], source: str,
                        columns: list[tuple[str, str]] = None,
                        timestamp: Optional[str] = None) -> str:
    """scan 结果落盘 markdown 列表。返回路径；失败返回空串。

    columns: 表格列 [(key, label)]，默认笨总评分三列（评分/等级/置信度）。
        scan market 等技术面结果可传 [("price","价"),("change_pct","涨跌%"),...]。
    timestamp: ISO 时间戳（save_last_scan 的 ScanSaveResult.timestamp 透传，M3），
        缺省取当前时间——传入时文件名/正文与历史记录精确同源。

    v0.8.14（M3）起文件名带秒级时间戳 + 独占创建（open "x"，同秒同主题冲突时
    _2/_3 递增）：同分钟同主题连续扫描不再互相覆盖；文件名秒级即新格式标志
    （导入侧按此分流），正文带「报告格式：v2」标记行供人工辨识。
    """
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"scan 报告目录创建失败: {e}")
        return ""

    if columns is None:
        columns = [("score", "评分"), ("grade", "等级"), ("confidence", "置信度")]

    if timestamp:
        try:
            now = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            logger.warning(f"scan 报告 timestamp 参数非法，回退当前时间: {timestamp!r}")
            now = datetime.now()
    else:
        now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M-%S")   # v0.8.14: 秒级（原分钟级，同分钟互相覆盖）
    full_ts = now.strftime("%Y-%m-%d %H:%M:%S")

    safe_source = re.sub(r'[<>:"/\\|?*\s]+', "_", source).strip("_.") or "scan"
    base = f"{ts}_{safe_source}"

    def _fmt(k, v):
        if v is None:
            return "-"
        if k == "score" and isinstance(v, (int, float)):
            return f"{v:.0f}"
        if k == "confidence" and isinstance(v, (int, float)):
            return f"{v:.2f}"
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)

    L = []
    L.append(f"# 扫描结果 - {source}")
    L.append("")
    L.append(f"> 时间：{full_ts}")
    L.append(f"> 共 {len(items)} 只")
    L.append("> 报告格式：v2（v0.8.14 起，文件名秒级时间戳）")
    L.append("")
    header = "| # | 代码 | 名称 | " + " | ".join(label for _, label in columns) + " |"
    sep = "|---|------|------|" + "|".join("------" for _ in columns) + "|"
    L.append(header)
    L.append(sep)
    for i, it in enumerate(items, 1):
        code = it.get("code", "")
        name = str(it.get("name", ""))[:10]
        cells = [_fmt(k, it.get(k)) for k, _ in columns]
        L.append(f"| {i} | {code} | {name} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## 明细")
    L.append("")
    for i, it in enumerate(items, 1):
        L.append(f"### {i}. {it.get('code', '')} {it.get('name', '')}")
        for k, v in it.items():
            if k in ("code", "name"):
                continue
            L.append(f"- {k}: {v}")
        L.append("")

    content = "\n".join(L)
    # 独占创建防覆盖：同名已存在（同秒同主题）则 _2/_3 递增，绝不覆盖已有报告
    for suffix in ("", "_2", "_3", "_4", "_5"):
        cand = _REPORT_DIR / f"{base}{suffix}.md"
        try:
            with open(cand, "x", encoding="utf-8") as f:
                f.write(content)
            return str(cand)
        except FileExistsError:
            continue
        except OSError as e:
            try:
                cand.unlink(missing_ok=True)  # 半截文件不残留
            except OSError:
                pass
            logger.warning(f"persist_scan_report 写入失败: {e}")
            return ""
    logger.warning(f"scan 报告同名冲突超过重试上限，未落盘: {base}")
    return ""
