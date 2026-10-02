# -*- coding: utf-8 -*-
"""第九轮版本登记同步：测试字面断言 shadow_v10→shadow_v11（Z1 账户输入语义升位）。"""
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
    n = text.count('"shadow_v10"')
    text = text.replace('"shadow_v10"', '"shadow_v11"')
    if "test_n1_canonical_arms" in rel:
        text = text.replace(
            "锁死语义（R13 X2 转绿；shadow_v9 候选协议 + 决策表 v3；O1/O2 后为 shadow_v10 + v4）：",
            "锁死语义（R13 X2 转绿；shadow_v9 候选协议 + 决策表 v3；O1/O2 后为 shadow_v10 + v4；P0/Z1 后为 shadow_v11）：")
    p.write_text(text, encoding="utf-8", newline="\n")
    print(f"{rel}: shadow_v10 x{n} -> shadow_v11")
