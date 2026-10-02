# -*- coding: utf-8 -*-
"""O1/O2 版本登记同步：测试字面断言 shadow_v9→shadow_v10、决策表 v3→v4。
只替换字面量与函数名，不改断言语义（合同升级登记，R14 授权的版本升位）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

FILES = [
    "tests/data_sources/test_m1_quote_time.py",
    "tests/core/test_k1_shadow_v6.py",
    "tests/core/test_l0_shadow_contract.py",
    "tests/core/test_n1_canonical_arms.py",
]

for rel in FILES:
    p = REPO / rel
    text = p.read_text(encoding="utf-8")
    n = text.count('"shadow_v9"')
    text = text.replace('"shadow_v9"', '"shadow_v10"')
    if "test_n1_canonical_arms" in rel:
        text = text.replace(
            "def test_decision_table_version_is_v3():",
            "def test_decision_table_version_is_v4():")
        text = text.replace(
            'assert DECISION_TABLE_VERSION == "v3"',
            'assert DECISION_TABLE_VERSION == "v4"')
        # 文件头锁死语义说明同步到当前协议（历史沿革在 plan/fusion 交付文档）
        text = text.replace(
            "锁死语义（R13 X2 转绿；shadow_v9 候选协议 + 决策表 v3）：",
            "锁死语义（R13 X2 转绿；shadow_v9 候选协议 + 决策表 v3；O1/O2 后为 shadow_v10 + v4）：")
    p.write_text(text, encoding="utf-8", newline="\n")
    print(f"{rel}: shadow_v9 x{n} -> shadow_v10")
