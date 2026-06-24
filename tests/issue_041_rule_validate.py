"""ISS-041 规则版↔AI版 相关性验证脚本

目的：验证规则版评分能否近似 AI 版排序，决定是否 full-scale 接入回测。
方法：取 5 只不同类型股票，同时跑 AI 版（auto_score）+ 规则版（rule_score），
      算总分排序一致性 + 每维 Spearman 相关性。

判定线（按 LRN-20260619-001 改善门槛精神）：
- 总分排序一致性（Spearman）≥ 0.6 → 规则版方向对，可 full-scale 接入回测
- < 0.6 → 规则版路线不可行，报告并停手

注：本脚本真实调 deepseek AI（5 股 × 5 维 ≈ 25 次，约 5-10 分钟），
    不进 CI，手动跑。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 绕代理
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

from src.core.benzong import auto_score, cache
from src.core.benzong.rule_scorer import rule_score

# 5 只不同类型股票
STOCKS = [
    ("600519", "贵州茅台"),   # 大市值消费
    ("300750", "宁德时代"),   # 新能源大市值
    ("600989", "宝丰能源"),   # 中市值化工
    ("601888", "中国中免"),   # 旅游
    ("002475", "立讯精密"),   # 电子
]

DIM_ORDER = ["industry_prosperity", "business_purity", "valuation_position",
             "industry_leader", "market_recognition", "risk_deduction"]
DIM_CN = {"industry_prosperity": "行业景气", "business_purity": "业务纯度",
          "valuation_position": "历史估值", "industry_leader": "细分龙头",
          "market_recognition": "辨识度", "risk_deduction": "风险"}


def spearman(a: list, b: list) -> float:
    """手算 Spearman（转 rank 后 Pearson），不依赖 scipy"""
    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        ranks = [0.0] * len(xs)
        i = 0
        while i < len(xs):
            j = i
            while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1  # 平均秩
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma = sum(ra) / n
    mb = sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = sum((ra[i] - ma) ** 2 for i in range(n)) ** 0.5
    vb = sum((rb[i] - mb) ** 2 for i in range(n)) ** 0.5
    if va == 0 or vb == 0:
        return 0.0
    return cov / (va * vb)


def main():
    print("=" * 70)
    print("  ISS-041 规则版 ↔ AI版 相关性验证（真实 deepseek，约 5-10 分钟）")
    print("=" * 70)

    ai_results = {}
    rule_results = {}

    for code, name in STOCKS:
        print(f"\n▶ {code} {name} — AI版评分中...")
        cache.clear(code)
        try:
            r_ai = auto_score(code, force_refresh=True)
            ai_results[code] = r_ai
        except Exception as e:
            print(f"  AI版失败: {e}")
            ai_results[code] = None
            continue

        print(f"▶ {code} {name} — 规则版评分中...")
        try:
            r_rule = rule_score(code, name)
            rule_results[code] = r_rule
        except Exception as e:
            print(f"  规则版失败: {e}")
            rule_results[code] = None

    # 收集对比数据
    valid = [c for c, _ in STOCKS if ai_results.get(c) and rule_results.get(c)]
    if len(valid) < 3:
        print(f"\n✗ 有效样本不足（{len(valid)}/5），无法算相关性")
        return

    print("\n" + "=" * 70)
    print("  对比表（AI版 vs 规则版）")
    print("=" * 70)
    print(f"  {'代码':<8}{'名称':<8} | {'AI总分':>7} {'规则总分':>8} | {'AI级':>4} {'规则级':>5}")
    print("  " + "-" * 60)
    ai_totals, rule_totals = [], []
    for code, name in STOCKS:
        if code not in valid:
            continue
        r_ai = ai_results[code]
        r_rule = rule_results[code]
        ai_t = r_ai.score.total_score
        rule_t = r_rule["total_score"]
        ai_totals.append(ai_t)
        rule_totals.append(rule_t)
        print(f"  {code:<8}{name:<8} | {ai_t:>7.1f} {rule_t:>8.1f} | {r_ai.score.grade():>4} {r_rule['grade']:>5}")

    # 总分排序一致性
    total_sp = spearman(ai_totals, rule_totals)
    print("\n  ── 总分排序一致性（Spearman）──")
    print(f"  Spearman = {total_sp:.3f}")
    print(f"  判定线: ≥0.6 可接入回测 | <0.6 规则版路线不可行")
    if total_sp >= 0.6:
        print(f"  ✅ 达标（{total_sp:.3f} ≥ 0.6）→ 规则版可 full-scale 接入回测")
    else:
        print(f"  ❌ 未达（{total_sp:.3f} < 0.6）→ 规则版路线不可行，停手报告")

    # 每维相关性（仅对两边都出真分的维度）
    print("\n  ── 每维 Spearman（AI版 conf>0 的维度才计入）──")
    for dim in DIM_ORDER:
        a_vals, r_vals = [], []
        for code in valid:
            r_ai = ai_results[code]
            m_ai = r_ai.dimensions_meta[dim]
            m_rule = rule_results[code][dim]
            # AI 版该维降级（conf=0）则跳过
            if m_ai.get("confidence", 0) <= 0:
                continue
            a_vals.append(m_ai["score"])
            r_vals.append(m_rule["score"])
        if len(a_vals) >= 3:
            sp = spearman(a_vals, r_vals)
            print(f"  {DIM_CN[dim]:<8} Spearman={sp:+.3f}  (n={len(a_vals)})")
        else:
            print(f"  {DIM_CN[dim]:<8} 样本不足 (n={len(a_vals)})")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
