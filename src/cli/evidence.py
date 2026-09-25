"""分析证据层（plan/C1，v0.8.17）：每次深分析落一条结构化证据，同股两次可比较。

- JSONL ~/.muyun/analysis_evidence.jsonl：机器层，diff 命令的比较基础（追加式，同
  scan_history 纪律——不 prune）
- 分析报告/analysis/{ts}_{code}_{name}.md：人话证据卡（用户既有偏好：分析输出
  落盘 时间_代码_名字 方便回看）

version 字段刻意不进证据记录——「区分真实变化与口径变化」靠字段语义稳定：
新增字段只追加不改语义，旧记录缺字段视为「当时未记录」，不误报为变化；
评分域的口径变化由 benzong CACHE_VERSION 标注（既有纪律）。
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_DIR = Path.home() / ".muyun"
_EVIDENCE_FILE = _STATE_DIR / "analysis_evidence.jsonl"
_REPORT_DIR = Path(__file__).resolve().parents[2] / "分析报告" / "analysis"


def record_evidence(decision_result, strategy_decision, *, source: str) -> Optional[dict]:
    """从一次深分析结果提取证据：JSONL 追加 + 人话证据卡落盘。

    失败不抛异常（返回 None，分析主流程不受影响——证据是附属产出）。

    Args:
        decision_result: DecisionResult（决策/评分/信号/警告）
        strategy_decision: StrategyDecision（sell_path/position_action；可 None）
        source: 触发来源（如 "l 600519" / "la" / "live_multi" / "chat"）
    """
    try:
        stock = decision_result.stock
        code = stock.stock_code
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "code": code,
            "name": (stock.stock_name or "")[:12],
            "source": source,
            "price": stock.price,
            "decision": decision_result.decision.value if hasattr(
                decision_result.decision, "value") else str(decision_result.decision),
            "score": round(float(decision_result.score), 3),
            "position_action": (strategy_decision.position_action.value
                                if strategy_decision and hasattr(strategy_decision.position_action, "value")
                                else None),
            "position_ratio": decision_result.position_ratio,
            "sell_path": getattr(strategy_decision, "sell_path", None) if strategy_decision else None,
            "signals": [
                {"skill": s.skill_alias, "signal": (s.signal.value if hasattr(s.signal, "value") else str(s.signal)),
                 "conf": round(float(s.confidence), 2)}
                for s in (decision_result.signals or [])[:8]
            ],
            "warnings": list(decision_result.warnings or [])[:6],
        }
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _EVIDENCE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _write_card(rec, decision_result)
        return rec
    except Exception as e:
        logger.debug(f"分析证据落盘失败(不影响分析): {e}")
        return None


def _write_card(rec: dict, decision_result) -> None:
    """人话证据卡 → 分析报告/analysis/{ts}_{code}_{name}.md（同分钟同股 _2/_3 防覆盖）。"""
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug(f"分析证据卡目录创建失败: {e}")
        return

    ts = rec["ts"].replace(":", "-")
    safe_name = re.sub(r'[<>:"/\\|?*\s]+', "_", rec.get("name") or rec["code"]).strip("_.") or rec["code"]
    base = f"{ts}_{rec['code']}_{safe_name}"
    L = [
        f"# 分析证据 - {rec['code']} {rec.get('name') or ''}",
        "",
        f"> 时间：{rec['ts']} ｜ 来源：{rec['source']}",
        f"> 决策：**{rec['decision']}** ｜ 评分：{rec['score']:.3f} ｜ 价格：{rec['price']}",
        f"> 仓位动作：{rec.get('position_action') or '-'} ｜ 卖出路径：{rec.get('sell_path') or '-'}",
        "",
        "## 技能信号",
        "",
    ]
    for s in rec.get("signals") or []:
        L.append(f"- {s['skill']}：{s['signal']}（置信度 {s['conf']:.2f}）")
    if not rec.get("signals"):
        L.append("- （无信号记录）")
    reasons = getattr(decision_result, "reason", None) or []
    if reasons:
        L.extend(["", "## 决策理由", ""])
        for r in reasons[:5]:
            L.append(f"- {r}")
    if rec.get("warnings"):
        L.extend(["", "## 数据缺口 / 风险提示", ""])
        for w in rec["warnings"]:
            L.append(f"- {w}")
    L.extend(["", f"> 结构化记录已追加 ~/.muyun/analysis_evidence.jsonl（`diff {rec['code']}` 比较两次）", ""])
    content = "\n".join(L)

    for suffix in ("", "_2", "_3"):
        cand = _REPORT_DIR / f"{base}{suffix}.md"
        try:
            with open(cand, "x", encoding="utf-8") as f:
                f.write(content)
            return
        except FileExistsError:
            continue
        except OSError as e:
            logger.debug(f"分析证据卡写入失败: {e}")
            return
    logger.debug(f"分析证据卡同名冲突超限: {base}")


def load_evidence(code: str, limit: int = 2) -> list:
    """读某股最近 limit 条证据（按文件出现序取尾部——JSONL 追加序即时间序，
    同秒两条记录 ts 相同时 ts 排序无法定先后，出现序才是真序）。
    坏行跳过（M2 同款逐行隔离）。"""
    try:
        if not _EVIDENCE_FILE.exists():
            return []
        raw_lines = _EVIDENCE_FILE.read_bytes().splitlines()
    except OSError as e:
        logger.debug(f"analysis_evidence.jsonl 读取失败: {e}")
        return []
    rows = []
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            r = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and str(r.get("code") or "") == str(code):
            rows.append(r)
    return rows[-limit:] if limit and limit > 0 else rows


def diff_evidence(code: str) -> Optional[dict]:
    """同股最近两次证据比较。不足两条返回 None（调用方提示需先积累）。

    返回 {old, new, changes: {字段: (旧, 新)}, new_warnings, signal_changes}——
    只列**发生变化的字段**；数值型给 (旧, 新, delta)。old=文件中倒数第二条
    （先发生），new=最后一条（后发生）。
    """
    rows = load_evidence(code, limit=2)
    if len(rows) < 2:
        return None
    old, new = rows[-2], rows[-1]

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    changes = {}
    for field in ("decision", "position_action", "sell_path"):
        if (old.get(field) or None) != (new.get(field) or None):
            changes[field] = (old.get(field), new.get(field))
    for field in ("price", "score"):
        a, b = _f(old.get(field)), _f(new.get(field))
        if a is not None and b is not None and abs(b - a) > 1e-9:
            changes[field] = (a, b, b - a)

    new_warnings = [w for w in (new.get("warnings") or []) if w not in (old.get("warnings") or [])]

    old_sig = {s.get("skill"): s for s in (old.get("signals") or [])}
    new_sig = {s.get("skill"): s for s in (new.get("signals") or [])}
    signal_changes = []
    for skill, s in new_sig.items():
        o = old_sig.get(skill)
        if o is None:
            signal_changes.append({"skill": skill, "kind": "新增", "old": None, "new": s})
        elif o.get("signal") != s.get("signal"):
            signal_changes.append({"skill": skill, "kind": "转向", "old": o, "new": s})
    for skill, o in old_sig.items():
        if skill not in new_sig:
            signal_changes.append({"skill": skill, "kind": "消失", "old": o, "new": None})

    return {"old": old, "new": new, "changes": changes,
            "new_warnings": new_warnings, "signal_changes": signal_changes}
