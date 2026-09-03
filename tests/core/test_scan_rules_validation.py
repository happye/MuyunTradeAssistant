"""bz scan 误报回归：global_exclude 的 starts_with/contains 被校验层当「未知操作符」（2026-09-04）

锁死语义：global_exclude 由 ScannerFilter.apply_global_exclude 执行，合法操作符集是
contains/starts_with/eq/lt/lte/gt/gte/is_nan。ISS-078 在 ScannerEngine._load_rules 加的
加载期校验却拿普通 filters 的 VALID_OPS（无 starts_with/contains/is_nan）去套它——
合法配置被误报「未知操作符」，且人话告警谎称「该规则筛选变宽松」（实际排除逻辑
一直在正常执行）。bz scan shrink_pullback 实测一次误报 4 条 × 2 轮。

跑法：pytest tests/core/test_scan_rules_validation.py
"""
import logging
import os
import sys

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.scanner.scanner_engine import ScannerEngine
from src.scanner.scanner_filter import ScannerFilter

_REAL_RULES = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "scanner", "scan_rules.yaml"
))


def _load_real_global_exclude() -> list[dict]:
    with open(_REAL_RULES, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("global_exclude") or []


def test_validate_excludes_accepts_exclude_only_ops():
    """真实 scan_rules.yaml 的 global_exclude（lte + starts_with×3 + contains）必须校验通过"""
    excludes = _load_real_global_exclude()
    assert excludes, "scan_rules.yaml 应有 global_exclude 配置"
    errors = ScannerFilter.validate_excludes(excludes)
    assert errors == [], f"合法 global_exclude 被误报: {errors}"


def test_validate_excludes_rejects_filter_only_ops():
    """普通 filters 专属操作符（like/between/in）在 global_exclude 里不合法，必须报错"""
    errors = ScannerFilter.validate_excludes([
        {"field": "code", "op": "like", "value": "8"},
    ])
    assert errors and "like" in errors[0], "validate_excludes 不能放行 like"


def test_real_yaml_loads_without_false_alarm(caplog):
    """用户症状回归：真实 scan_rules.yaml 构造 ScannerEngine，不得出现「校验失败」告警"""
    with caplog.at_level(logging.WARNING, logger="src.scanner.scanner_engine"):
        ScannerEngine(rules_path=_REAL_RULES)
    false_alarms = [r for r in caplog.records if "校验失败" in r.getMessage()]
    assert not false_alarms, f"误报: {[r.getMessage() for r in false_alarms]}"


def test_load_rules_still_catches_real_typos(tmp_path, caplog):
    """防矫枉过正：真拼错的操作符（规则 filters 和 global_exclude 各一处）仍必须告警"""
    rules = {
        "global_exclude": [{"field": "code", "op": "starst_with", "value": "8"}],
        "rules": {
            "typo_rule": {
                "name": "拼写回归",
                "filters": [{"field": "price", "op": "grater", "value": 1.0}],
            },
        },
    }
    p = tmp_path / "scan_rules_typo.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(rules, f, allow_unicode=True)
    with caplog.at_level(logging.WARNING, logger="src.scanner.scanner_engine"):
        ScannerEngine(rules_path=str(p))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("global_exclude" in m and "starst_with" in m for m in msgs), msgs
    assert any("typo_rule" in m and "grater" in m for m in msgs), msgs
