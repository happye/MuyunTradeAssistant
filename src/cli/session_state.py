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
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 状态目录（跨会话，不污染仓库；与 benzong_cache 同级）
_STATE_DIR = Path.home() / ".muyun"
_LAST_SCAN_FILE = _STATE_DIR / "last_scan.json"
# 扫描历史（v0.8.11，scan review 复盘用）：JSONL 追加式，一行一条扫描记录。
# 与 last_scan.json（只存最近一次）互补——历史供 scan review 验证选股涨跌表现。
# v0.8.12.1 起全量保留不 prune（导入旧记录不能被静默删），窗口由查询侧控制。
_SCAN_HISTORY_FILE = _STATE_DIR / "scan_history.jsonl"
# 当日已深分析记录（v0.8.7.9，l all 去重用）：文件只存当天 {日期: {code: {time, source}}}，
# 写入时自动覆盖旧日期 → 文件不膨胀；语义对齐笨总六维当日缓存（跨天自动失效）。
_DEEP_ANALYZED_FILE = _STATE_DIR / "deep_analyzed.json"

# 报告目录（项目内，gitignored；src/cli/session_state.py -> parents[2]=项目根）
_REPORT_DIR = Path(__file__).resolve().parents[2] / "分析报告" / "scan"

# 过期阈值（分钟）--超此提示 scan 可能已旧，#N 引用时提醒
_STALE_MINUTES = 30


def save_last_scan(items: list[dict], source: str) -> bool:
    """存最近 scan 结果列表，并追加一条结构化历史（scan review 复盘用）。

    本函数是全部扫描路径（scan market / bz scan / chat 工具）的唯一收口，
    在此追加历史可零 call-site 改动覆盖所有扫描类型。
    source 为来源命令（如 "bz scan AI,半导体"）。

    Returns: True 写入成功
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "count": len(items),
            "items": items,
        }
        _LAST_SCAN_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 历史追加失败不影响 last_scan 写入（append 内部已兜底不抛异常）
        append_scan_history(items, source)
        return True
    except OSError as e:
        logger.warning(f"save_last_scan 失败: {e}")
        return False


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
    except OSError as e:
        logger.warning(f"append_scan_history 失败: {e}")
        return False


def _read_scan_history_rows() -> tuple[list[dict], int]:
    """读历史原始行。返回 (可解析行列表, 损坏行数)。文件不存在返回空。"""
    try:
        if not _SCAN_HISTORY_FILE.exists():
            return [], 0
        rows, corrupt = [], 0
        for line in _SCAN_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                corrupt += 1
        return rows, corrupt
    except OSError as e:
        logger.debug(f"scan_history.jsonl 读取失败: {e}")
        return [], 0


def get_scan_history(days: Optional[int] = None) -> list[dict]:
    """读扫描历史。按 timestamp 倒序；days 非空时只保留最近 N 天。

    损坏行跳过（debug 日志计数，不阻塞）。
    """
    rows, corrupt = _read_scan_history_rows()
    if corrupt:
        logger.debug(f"scan_history.jsonl 有 {corrupt} 行损坏已跳过")
    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = [r for r in rows if (r.get("timestamp") or "") >= cutoff]
    return rows


def get_last_scan() -> Optional[dict]:
    """读最近 scan。返回 {timestamp, source, count, items} 或 None。"""
    try:
        if not _LAST_SCAN_FILE.exists():
            return None
        return json.loads(_LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"get_last_scan 失败: {e}")
        return None


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
    """
    try:
        if not _DEEP_ANALYZED_FILE.exists():
            return {}
        data = json.loads(_DEEP_ANALYZED_FILE.read_text(encoding="utf-8"))
        rec = data.get(datetime.now().strftime("%Y-%m-%d"))
        if not isinstance(rec, dict):
            return {}
        # 过滤脏条目（值必须是 {time, source} 结构），防调用方 .get("time") 崩
        return {k: v for k, v in rec.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"get_deep_analyzed 失败: {e}")
        return {}


def mark_deep_analyzed(codes: list, source: str = "") -> bool:
    """把今天成功深分析的股票记入 deep_analyzed.json。失败不抛异常。"""
    try:
        rec = get_deep_analyzed()
        now_hm = datetime.now().strftime("%H:%M")
        for c in codes:
            if c:
                rec[str(c)] = {"time": now_hm, "source": source}
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _DEEP_ANALYZED_FILE.write_text(
            json.dumps({datetime.now().strftime("%Y-%m-%d"): rec},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return True
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
    except OSError as e:
        logger.debug(f"watchlist.jsonl 写入失败: {e}")
        return False


def get_watch_events() -> list[dict]:
    """读观察池全部事件（按时间升序）。损坏行跳过。"""
    try:
        if not _WATCH_FILE.exists():
            return []
        rows = []
        for line in _WATCH_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict):
                rows.append(r)
        rows.sort(key=lambda r: r.get("timestamp", ""))
        return rows
    except OSError as e:
        logger.debug(f"watchlist.jsonl 读取失败: {e}")
        return []


def get_watch_active() -> list[dict]:
    """重放事件得「在池记录」：remove 抵消对应 code 的 add。

    返回结构与 scan 记录同构 [{timestamp(入池时间), source, items:[{code,...}]}]，
    供 watch 命令直接复用 scan review 的复盘引擎。
    """
    active: dict = {}   # code -> 在池记录
    for ev in get_watch_events():
        action = ev.get("action")
        for it in ev.get("items") or []:
            code = (it or {}).get("code")
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
    return sorted(active.values(), key=lambda r: r.get("timestamp", ""))


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
                        columns: list[tuple[str, str]] = None) -> str:
    """scan 结果落盘 markdown 列表。返回路径；失败返回空串。

    columns: 表格列 [(key, label)]，默认笨总评分三列（评分/等级/置信度）。
        scan market 等技术面结果可传 [("price","价"),("change_pct","涨跌%"),...]。
    """
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"scan 报告目录创建失败: {e}")
        return ""

    if columns is None:
        columns = [("score", "评分"), ("grade", "等级"), ("confidence", "置信度")]

    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    full_ts = now.strftime("%Y-%m-%d %H:%M:%S")

    safe_source = re.sub(r'[<>:"/\\|?*\s]+', "_", source).strip("_.") or "scan"
    path = _REPORT_DIR / f"{ts}_{safe_source}.md"

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

    try:
        path.write_text("\n".join(L), encoding="utf-8")
        return str(path)
    except OSError as e:
        logger.warning(f"persist_scan_report 写入失败: {e}")
        return ""
