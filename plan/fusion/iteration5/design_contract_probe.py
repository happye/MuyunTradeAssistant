"""Small normative design model. This is not a production implementation."""
import copy
from datetime import datetime
import hashlib
import json
import math


def qualify(record):
    missing = [k for k in ("plan_ref", "assessment_id", "snapshot_id", "account_version",
                           "method_version", "policy_version", "evidence_as_of", "quote_as_of")
               if not record.get(k)]
    for key in ("evidence_as_of", "quote_as_of"):
        if record.get(key) and datetime.fromisoformat(record[key]) > datetime.fromisoformat(record["as_of"]):
            missing.append(key + "_future")
    for arm in ("legacy", "fusion"):
        if not all(k in record.get(arm, {}) for k in ("action", "target", "target_state", "blockers", "execution")):
            missing.append(arm + "_incomplete")
    return not missing, sorted(missing)


def fingerprint(record):
    data = {k: v for k, v in record.items() if k not in ("as_of", "captured_at")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def quantity_context(quantity, price=None, nav=None, same_pricing_time=False):
    qualified = same_pricing_time and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                        and math.isfinite(x) and x > 0 for x in (price, nav))
    return {"has_position": quantity > 0, "weight": quantity * price / nav if qualified else None,
            "precise_addition_allowed": qualified}


def main():
    record = {"plan_ref": ["p", 2, "hash"], "assessment_id": "a", "snapshot_id": "s",
              "account_version": "v", "method_version": "m", "policy_version": "r",
              "evidence_as_of": "2026-10-01T09:00:00+08:00", "quote_as_of": "2026-10-01T09:01:00+08:00",
              "as_of": "2026-10-01T09:02:00+08:00", "captured_at": "2026-10-01T09:02:01+08:00",
              "legacy": {"action": "HOLD", "target": .1, "target_state": "KNOWN", "blockers": [], "execution": "HOLD"},
              "fusion": {"action": "WAIT", "target": None, "target_state": "NOT_APPLICABLE", "blockers": [], "execution": "WAIT"}}
    results = []
    def check(name, value):
        assert value, name
        results.append({"case": name, "passed": True})
    check("complete_trace_even_when_wait", qualify(record)[0])
    missing = copy.deepcopy(record); missing["quote_as_of"] = None
    check("missing_quote_diagnostic", not qualify(missing)[0])
    future = copy.deepcopy(record); future["quote_as_of"] = "2026-10-02T09:01:00+08:00"
    check("future_quote_diagnostic", not qualify(future)[0])
    changed = copy.deepcopy(record); changed["legacy"]["target"] = .2
    check("legacy_target_change_retained", fingerprint(record) != fingerprint(changed))
    changed = copy.deepcopy(record); changed["fusion"]["blockers"] = ["cash_unknown"]
    check("fusion_blocker_change_retained", fingerprint(record) != fingerprint(changed))
    changed = copy.deepcopy(record); changed["captured_at"] = "2026-10-01T09:03:00+08:00"
    check("identical_inputs_outputs_dedup", fingerprint(record) == fingerprint(changed))
    unknown = quantity_context(100)
    check("quantity_holding_unknown_weight_not_flat", unknown["has_position"] and unknown["weight"] is None and not unknown["precise_addition_allowed"])
    known = quantity_context(100, 10, 10000, True)
    check("qualified_valuation_weight", known["weight"] == .1)
    check("mismatched_pricing_time_blocked", quantity_context(100, 10, 10000, False)["weight"] is None)
    check("zero_quantity_no_holding", not quantity_context(0)["has_position"])
    print(json.dumps({"scope": "Normative contract feasibility only; no production or investment-effect validation",
                      "results": results}, indent=2))


if __name__ == "__main__":
    main()
