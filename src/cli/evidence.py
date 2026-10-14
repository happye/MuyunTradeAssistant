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


def record_evidence(decision_result, strategy_decision, *, source: str,
                    packet=None, position_state=None, account_version=None) -> Optional[dict]:
    """从一次深分析结果提取证据：JSONL 追加 + 人话证据卡落盘。

    失败不抛异常（返回 None，分析主流程不受影响——证据是附属产出）。

    Args:
        decision_result: DecisionResult（决策/评分/信号/警告）
        strategy_decision: StrategyDecision（sell_path/position_action；可 None）
        source: 触发来源（如 "l 600519" / "la" / "live_multi" / "chat"）
        packet: DecisionPacket（F2 终态，可选；传入时追加 decision_id/desired_action/
                target_weight/execution_status/research_status/policy_id 终态字段——
                旧记录缺这些字段=「当时未记录」，diff 侧同步消费）
        position_state: 账户事实三态 HELD/NONE/UNKNOWN（M0 追加式——旧记录缺=未记录）
        account_version: 账本内容版本（M0 追加式；缺省回退 packet.portfolio_revision）
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
            # ISS-117 S2a：聚合协议与 A/C 随证据落盘——旧记录按"旧协议"读取，
            # 不把旧 score（归一化占比语义）读成新 A（动作强度）
            "signal_authority_protocol": getattr(decision_result, "signal_authority_protocol", None),
            "aggregation_protocol": getattr(decision_result, "aggregation_protocol", "pre-s2a"),
            "action_strength": (getattr(decision_result, "action_strength", None) or None),
            "opinion_share": (getattr(decision_result, "opinion_share", None) or None),
            "coverage_ratio": getattr(decision_result, "coverage_ratio", None),
            # S2a-Z：独立 action 通道（止损/止盈一票否决/压制的触发票置信度）同源
            # 落盘——普通 A_SELL=0、C=0 但 override=.9 仍 EXIT 时，回看明确这是
            # 独立纪律 .9（不从中文 reason/sell_path 猜身份）
            "action_override_confidence": getattr(decision_result, "action_override_confidence", None),
            "action_override_signal_id": getattr(decision_result, "action_override_signal_id", None),
            "position_action": (strategy_decision.position_action.value
                                if strategy_decision and hasattr(strategy_decision.position_action, "value")
                                else None),
            "position_ratio": decision_result.position_ratio,
            "sell_path": getattr(strategy_decision, "sell_path", None) if strategy_decision else None,
            # v0.8.28.1（架构师裁决 E）：top_signal 专门触发字段——此前该 reason 只进
            # INFO 日志，事后无法定位是哪条子信号触发的强制清仓（渗透率30+误触发排查）
            "top_signal": (getattr(strategy_decision, "top_signal", None)
                           if strategy_decision else None),
            "signals": [
                {"skill": s.skill_alias, "signal": (s.signal.value if hasattr(s.signal, "value") else str(s.signal)),
                 "conf": round(float(s.confidence), 2), "signal_id": getattr(s, "skill_name", None),
                 "rule_version": getattr(s, "rule_version", None), "evidence_binding": getattr(s, "evidence_binding", None)}
                for s in (decision_result.signals or [])
            ],
            "warnings": list(decision_result.warnings or []),
            "trace": ([t.model_dump(mode="json") for t in decision_result.trace]
                      if getattr(decision_result, "trace", None) is not None else None),
            "decisive_reasons": list(getattr(strategy_decision, "strategy_reasons", []) or []),
            "signal_findings": [f.model_dump(mode="json") for f in getattr(strategy_decision, "signal_findings", [])],
            "condition_evaluation": getattr(strategy_decision, "condition_evaluation", None),
        }
        if packet is not None:
            # F2 终态字段：追加式，不改旧字段语义（ADR-F09 缺字段=未记录）
            rec.update({
                "decision_id": packet.decision_id,
                "desired_action": packet.desired_action.value,
                "target_weight": packet.target_weight,
                "execution_status": packet.execution_status.value,
                "research_status": packet.research_status.value,
                "policy_id": packet.policy_id,
                "signal_effects": getattr(packet, "signal_effects", None),
                "execution_diagnostics": getattr(packet, "execution_diagnostics", []),
                "execution_simulated": getattr(packet, "execution_simulated", None),
                "reason_codes": packet.reason_codes,
                "blockers": [b.model_dump(mode="json") for b in packet.blockers],
                "decision_basis": packet.decision_basis,
                "strategy_environment": packet.strategy_environment,
                "risk_reminders": packet.risk_reminders,
                "data_gaps": packet.data_gaps,
                "research_scope": packet.research_scope,
            })
        if position_state:
            rec["position_state"] = position_state  # M0：账户事实三态（追加式）
        av = account_version or (getattr(packet, "portfolio_revision", None)
                                 if packet is not None else None)
        if av:
            rec["account_version"] = av  # M0：账户版本（追加式）
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
    from src.cli.action_view import action_headline
    headline = (action_headline({"bucket": rec["desired_action"],
                                "execution_status": rec.get("execution_status", "UNKNOWN"),
                                "blocked": rec.get("execution_status") == "BLOCKED",
                                "blocked_reason": "；".join(b["detail"] for b in rec.get("blockers") or [])})
                if rec.get("desired_action") else "旧记录未记录统一终态，请勿从聚合分数推断可执行动作。")
    L = [
        f"# 分析证据 - {rec['code']} {rec.get('name') or ''}",
        "",
        f"> 时间：{rec['ts']} ｜ 来源：{rec['source']}",
        "> " + headline,
        f"> 技术聚合（诊断）：{rec['decision']} ｜ 展示评分：{rec['score']:.3f} ｜ 价格：{rec['price']}",
        f"> 旧引擎末端（诊断）：{rec.get('position_action') or '-'} ｜ 卖出路径：{rec.get('sell_path') or '-'}",
        *( [f"> 信号口径：动作强度(普通票) {rec['action_strength'].get(rec['decision'], 0):.2f}"
            f" ｜ 覆盖率 {rec['coverage_ratio']:.0%}"]
          if rec.get("action_strength") is not None and rec.get("coverage_ratio") is not None
          else ["> 信号口径：当时未记录"] ),
        *( ["> 意见占比（仅展示）：" + " / ".join(
            f"{direction} {value:.1%}" for direction, value in rec["opinion_share"].items())]
          if rec.get("opinion_share") is not None else ["> 意见占比：当时未记录"] ),
        *( [f"> 独立纪律：{rec.get('action_override_signal_id') or '来源当时未记录'}"
            f" ｜ 强度 {rec['action_override_confidence']:.2f}"]
          if rec.get("action_override_confidence") is not None
          else ["> 独立纪律：未触发" if rec.get("aggregation_protocol") == "s2a-v1"
                else "> 独立纪律：当时未记录"] ),
        *( [f"> 触发原因（top_signal）：{rec['top_signal']}"] if rec.get("top_signal") else [] ),
        "",
        "## 技能信号",
        "",
    ]
    for s in rec.get("signals") or []:
        L.append(f"- {s['skill']}：{s['signal']}（置信度 {s['conf']:.2f}）")
    if not rec.get("signals"):
        L.append("- （无信号记录）")
    L.extend(["", "## 采用依据", ""])
    if rec.get("decision_basis") is not None:
        L.extend("- " + r for r in rec["decision_basis"])
    else:
        L.append("- 当时未记录结构化采用依据")
    L.extend("- 策略环境：" + r for r in rec.get("strategy_environment") or [])
    L.extend("- 待核事项：" + r for r in rec.get("data_gaps") or [])
    if rec.get("execution_status"):
        L.append("- 成交资格：" + rec["execution_status"] + "（建议不等于成交）")
    L.extend("- " + r for r in rec.get("execution_diagnostics", []))
    evaluation = rec.get("condition_evaluation")
    if evaluation:
        L.append("- 失效条件：" + evaluation["value"])
        L.extend("- 待复核：" + r for r in evaluation["gaps"])
    findings = rec.get("signal_findings", [])
    research = [f for f in findings if f["action_scope"] == "research"]
    if research:
        L.extend(["", "## 辅助研究（不授权交易）", ""])
        L.extend("- " + f["signal_id"] + "：" + f["detail"] + "；" + f["reason"] for f in research)
    reasons = getattr(decision_result, "reason", None) or []
    if reasons:
        L.extend(["", "## 决策理由", ""])
        for r in reasons:
            L.append(f"- {r}")
    if rec.get("warnings"):
        L.extend(["", "## 风险提醒与技能线索", ""])
        for w in rec.get("risk_reminders") if rec.get("risk_reminders") is not None else rec["warnings"]:
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


def list_diffable() -> list[dict]:
    """证据里至少有两条记录的股票（`diff` 无参数时的可对比清单；用户走查 2026-09-26：
    「我不知道哪些股票能用 diff」）。返回 [{code, name, count, latest_ts}] 按最近时间降序。"""
    if not _EVIDENCE_FILE.exists():
        return []
    by_code: dict[str, dict] = {}
    with _EVIDENCE_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行隔离（既有纪律）
            code = str(r.get("code") or "")
            if not code:
                continue
            ent = by_code.setdefault(code, {"code": code, "name": "", "count": 0, "latest_ts": ""})
            ent["count"] += 1
            if not ent["name"]:
                ent["name"] = str(r.get("name") or "")
            if str(r.get("ts") or "") > ent["latest_ts"]:
                ent["latest_ts"] = str(r.get("ts") or "")
    return sorted((e for e in by_code.values() if e["count"] >= 2),
                  key=lambda e: e["latest_ts"], reverse=True)


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


def _diff_records(old: dict, new: dict) -> dict:
    """两条证据记录的字段级比较（S2a 补修 D 组抽纯函数，测试可直调）。

    返回 {old, new, changes, new_warnings, signal_changes}——只列**发生变化的
    字段**；数值型给 (旧, 新, delta)。score 的跨协议 Δ 特殊处理见循环内。
    """

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    changes = {}
    for field in ("decision", "position_action", "sell_path",
                  "desired_action", "execution_status", "position_state"):
        if (old.get(field) or None) != (new.get(field) or None):
            changes[field] = (old.get(field), new.get(field))
    for field in ("price", "score", "target_weight"):
        a, b = _f(old.get(field)), _f(new.get(field))
        if a is not None and b is not None and abs(b - a) > 1e-9:
            if field == "score":
                # ISS-117 S2a 补修 D 组：跨协议 score 不给数值 Δ（口径不同不可比——
                # 旧归一化占比 vs 新动作强度），标明双方协议；价格 Δ 不受影响；
                # 同协议 Δ 保留
                op = old.get("aggregation_protocol") or "pre-s2a（当时未记录）"
                np_ = new.get("aggregation_protocol") or "pre-s2a（当时未记录）"
                old_authority = old.get("signal_authority_protocol")
                new_authority = new.get("signal_authority_protocol")
                if old_authority != new_authority:
                    op += "/" + (old_authority or "权限口径当时未记录")
                    np_ += "/" + (new_authority or "权限口径当时未记录")
                if op != np_:
                    changes["score"] = (a, b, "口径不同，数值不可比",
                                        {"old_protocol": op, "new_protocol": np_})
                    continue
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


def diff_evidence(code: str) -> Optional[dict]:
    """同股最近两次证据比较。不足两条返回 None（调用方提示需先积累）。"""
    rows = load_evidence(code, limit=2)
    if len(rows) < 2:
        return None
    return _diff_records(rows[-2], rows[-1])
