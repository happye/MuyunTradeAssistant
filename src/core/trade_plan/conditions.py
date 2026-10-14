"""Accepted plan conditions: complete predicates, three-valued logic, no AI inference."""
import math
import re

from src.core.decision_contract import TruthValue
from src.core.source_time import parse_source_time


def positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def combine(values, operator="OR"):
    if not values:
        return TruthValue.UNKNOWN
    if operator == "AND":
        if TruthValue.FALSE in values:
            return TruthValue.FALSE
        return TruthValue.TRUE if all(v == TruthValue.TRUE for v in values) else TruthValue.UNKNOWN
    if TruthValue.TRUE in values:
        return TruthValue.TRUE
    return TruthValue.FALSE if all(v == TruthValue.FALSE for v in values) else TruthValue.UNKNOWN


def _legacy(text):
    # Strip only recognized descriptive suffixes, never part of an executable predicate.
    if not isinstance(text, str):
        return {}
    parts = re.split(r"\s*[+＋]\s*", text.strip())
    if len(parts) > 1:
        return {"version": "plan-condition-v1", "op": "AND", "children": [_legacy(p) for p in parts]}
    if re.fullmatch(r"跌破\s*MA60(?:\(¥\d+(?:\.\d+)?\))?", text.strip()):
        return {"version": "plan-condition-v1", "predicate": "below_ma60"}
    if re.fullmatch(r"MA20\s*(?:死叉|下穿)\s*MA60(?:（上升趋势结构破坏）)?", text.strip()):
        return {"version": "plan-condition-v1", "predicate": "ma20_below_ma60"}
    if text.strip() == "跌破初始止损价（致命）":
        return {"version": "plan-condition-v1", "predicate": "below_initial_stop"}
    # "异常放大" has no accepted numeric threshold and must remain UNKNOWN.
    return {}


def _evaluate(rule, plan, data, time_ok):
    if not isinstance(rule, dict) or rule.get("version") != "plan-condition-v1":
        return TruthValue.UNKNOWN, "条件未完整定义或版本未知"
    if rule.get("op") in ("AND", "OR"):
        children = rule.get("children")
        if not isinstance(children, list):
            return TruthValue.UNKNOWN, "复合条件缺子项"
        results = [_evaluate(r, plan, data, time_ok) for r in children]
        return combine([r[0] for r in results], rule["op"]), "；".join(r[1] for r in results)
    if not time_ok:
        return TruthValue.UNKNOWN, "指标有效时点待确认"
    pred = rule.get("predicate")
    if pred == "below_ma60":
        a, b = data.price, data.ma60
    elif pred == "ma20_below_ma60":
        a, b = data.ma20, data.ma60
    elif pred == "below_initial_stop":
        a, b = data.price, plan.locked_initial_stop
    elif pred == "below_stop":
        a, b = data.price, plan.current_stop
    elif pred == "volume_ratio_above":
        if not all(positive(v) for v in (data.volume, data.avg_volume_20, rule.get("threshold"))):
            return TruthValue.UNKNOWN, "成交量或已接受阈值缺失/非法"
        value = data.volume / data.avg_volume_20 > rule["threshold"]
        return (TruthValue.TRUE if value else TruthValue.FALSE), "量比与明确阈值比较"
    else:
        return TruthValue.UNKNOWN, "条件未完整定义（不能截取半句判定）"
    if not positive(a) or not positive(b):
        return TruthValue.UNKNOWN, "价格/均线/止损值缺失或非法"
    return (TruthValue.TRUE if a < b else TruthValue.FALSE), f"{pred}: {a:g} < {b:g}"


def evaluate_conditions(plan, data, today=None):
    quote = parse_source_time(getattr(data, "quote_as_of", None))
    cutoff = parse_source_time(today) if today is not None else quote
    # Daily indicators belong to the same analysis day; do not invent a freshness policy.
    time_ok = quote is not None and cutoff is not None and quote[0].date() == cutoff[0].date() and (not cutoff[1] or not quote[1] or quote[0] <= cutoff[0])
    rules = plan.invalidation_rules or [_legacy(t) for t in plan.when_sell_invalidate]
    rows = []
    for i, rule in enumerate(rules):
        value, reason = _evaluate(rule, plan, data, time_ok)
        rows.append({"condition": plan.when_sell_invalidate[i] if i < len(plan.when_sell_invalidate) else str(rule),
                     "value": value.value, "reason": reason, "rule": rule})
    result = combine([TruthValue(r["value"]) for r in rows])
    return {"protocol": "plan-condition-v1", "value": result.value, "conditions": rows,
            "gaps": [r["reason"] for r in rows if r["value"] == "UNKNOWN"] or
                    (["未配置可评估条件"] if not rows else [])}
