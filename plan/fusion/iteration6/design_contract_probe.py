"""Normative design examples only. Does not patch or certify production code."""
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass(frozen=True)
class Holding:
    presence: str
    weight: float | None


def action_view(action, holding):
    if action == "EXIT":
        return ("WAIT", None) if holding.presence == "NONE" else ("EXIT", 0.0)
    if action == "REDUCE":
        return ("REDUCE", None) if holding.presence == "HELD" else ("REVIEW", None)
    if action in ("OPEN", "ADD") and holding.presence == "HELD" and holding.weight is None:
        return "HOLD", None
    return action, None


def qualified_weight(qty, price, price_at, nav, nav_at, now):
    if not all((price_at, nav_at)) or price <= 0 or nav <= 0:
        return None
    p, n = datetime.fromisoformat(price_at), datetime.fromisoformat(nav_at)
    if p.tzinfo is None or n.tzinfo is None or p > now or n > now:
        return None
    if p.astimezone(now.tzinfo).date() != n.astimezone(now.tzinfo).date():
        return None
    return qty * price / nav


def main():
    rows = []
    def check(name, observed, expected):
        assert observed == expected, (name, observed, expected)
        rows.append({"case": name, "pass": True})
    held = Holding("HELD", None)
    check("unknown_weight_exit", action_view("EXIT", held), ("EXIT", 0.0))
    check("unknown_weight_reduce", action_view("REDUCE", held), ("REDUCE", None))
    check("unknown_weight_add", action_view("ADD", held), ("HOLD", None))
    check("flat_exit", action_view("EXIT", Holding("NONE", 0)), ("WAIT", None))
    check("unknown_presence_exit", action_view("EXIT", Holding("UNKNOWN", None)), ("EXIT", 0.0))
    check("unknown_presence_reduce", action_view("REDUCE", Holding("UNKNOWN", None)), ("REVIEW", None))
    check("hold_has_no_zero_target", action_view("HOLD", held), ("HOLD", None))
    now = datetime.fromisoformat("2026-10-01T12:00:00+08:00")
    today = "2026-10-01T10:00:00+08:00"
    yesterday = "2026-09-30T15:00:00+08:00"
    def weight(p, n):
        return qualified_weight(100, 10, p, 10000, n, now)
    check("known_same_day", weight(today, today), .1)
    check("old_close_current_nav", weight(yesterday, today), None)
    check("unknown_source_time", weight(None, today), None)
    check("future_time", weight("2026-10-01T13:00:00+08:00", today), None)
    check("naive_time", weight("2026-10-01T10:00:00", today), None)
    check("timezone_same_instant", weight("2026-10-01T02:00:00+00:00", today), .1)
    # Frozen request context: repeat consumers see the same version; a new request refreshes.
    reads = []
    def load():
        reads.append(1)
        return {"version": len(reads), "holding": held}
    ctx = load()
    check("batch_one_account_read", [ctx["version"] for _ in range(30)], [1] * 30)
    check("next_request_refreshes", load()["version"], 2)
    # K2b proposal: normal analysis consumes accepted state; refresh stays explicit.
    accepted = {"assessment": "old", "revision": 1}
    candidate = {"assessment": "new", "revision": 2}
    def daily_view():
        return {"accepted": dict(accepted), "candidate_available": bool(candidate)}
    before = json.dumps([accepted, candidate], sort_keys=True)
    for _ in range(3):
        check(f"daily_view_keeps_accepted_{_}", daily_view()["accepted"]["assessment"], "old")
    check("daily_view_zero_plan_mutation", json.dumps([accepted, candidate], sort_keys=True), before)
    print(json.dumps({"scope": "normative prototype, not production or investment validation",
                      "passed": len(rows), "cases": rows}, indent=2))


if __name__ == "__main__":
    main()
