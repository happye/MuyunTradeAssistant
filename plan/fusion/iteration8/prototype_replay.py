"""Execute implementation M2 prototype cases without rewriting historic results."""
import json
import r13_contract_replay as replay


def test_m2_delivered_prototype_cases(tmp_path, monkeypatch):
    module = replay.prototype()
    # The prototype chooses its JSON destination from __file__. Redirect only
    # that destination; its actual daily_research_view and cases stay unchanged.
    monkeypatch.setattr(module, "__file__", str(tmp_path / "prototype.py"))
    assert module.main() == 0
    result = json.loads((tmp_path / "m2_readonly_view_results.json").read_text(encoding="utf-8"))
    assert result["passed"] == 25
    print("R14_M2_PROTOTYPE_ASSERTIONS", result["passed"])
