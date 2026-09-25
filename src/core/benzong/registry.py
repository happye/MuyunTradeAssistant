"""维度元数据注册表（ADR-07，v0.8.17）：维度名称/中文名/展示顺序的集中声明。

边界（ADR-07 口径）：唯一公式仍由 scorer.py 拥有（WEIGHT_* 常量与
normalized_score 不迁不改）；本注册表只做元数据的单一事实源，供 CLI 展示
（main._BZ_DIM_CN）与后续缓存键/文档引用。测试锁 registry 与 scorer 权重键
集合一致（防两处漂移）。改公式仍须 bump CACHE_VERSION（既有红线）。
"""

# 展示顺序（笨总六维，行业景气/主业纯度为权重最高两维，见 scorer.py WEIGHT_*）
DIM_ORDER = [
    "industry_prosperity",
    "business_purity",
    "valuation_position",
    "industry_leader",
    "market_recognition",
    "risk_deduction",
]

DIM_CN = {
    "industry_prosperity": "行业景气",
    "business_purity": "主业纯度",
    "valuation_position": "估值位置",
    "industry_leader": "龙头地位",
    "market_recognition": "市场认可",
    "risk_deduction": "风险控制",
}

# 缓存键构成声明（ADR-07 元数据）：实际键实现唯一归 cache.py
# （cache.get(code, date, dim)，~/.muyun/benzong_cache/）。此处只集中
# "缓存键由哪三个字段构成"这一事实，供文档与后续工具引用；与实现的
# 一致性由 test_registry_cache_key_matches_cache_implementation 锁定。
CACHE_KEY_FIELDS = ["code", "date", "dim"]
