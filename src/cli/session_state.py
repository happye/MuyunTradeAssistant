"""会话状态管理 - 让命令间能"接住"上次 scan 结果（体验重构方向 A，Step 1）

职责：
- save_last_scan(items, source): 存最近 scan 结果到 ~/.muyun/last_scan.json
- get_last_scan() / resolve_index(n): 读最近 scan / 取第 n 只（供 #N 快捷，Step 2 用）
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
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 状态目录（跨会话，不污染仓库；与 benzong_cache 同级）
_STATE_DIR = Path.home() / ".muyun"
_LAST_SCAN_FILE = _STATE_DIR / "last_scan.json"

# 报告目录（项目内，gitignored；src/cli/session_state.py -> parents[2]=项目根）
_REPORT_DIR = Path(__file__).resolve().parents[2] / "分析报告" / "scan"

# 过期阈值（分钟）--超此提示 scan 可能已旧，#N 引用时提醒
_STALE_MINUTES = 30


def save_last_scan(items: list[dict], source: str) -> bool:
    """存最近 scan 结果列表。source 为来源命令（如 "bz scan AI,半导体"）。

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
        return True
    except OSError as e:
        logger.warning(f"save_last_scan 失败: {e}")
        return False


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
