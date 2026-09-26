# 第二轮只读架构核验

2026-09-26｜源码基线 `5b2e262`｜实际执行成功。只导入纯模型/纯函数，无网络、无 AI、无产品启动，不读写真实账户，不运行完整回测。启动命令使用 `python -B` 避免生成 pyc。

## 1. 实测结果与边界

| 探针 | 输入 | 实际结果 | 能证明/不能证明 |
|---|---|---|---|
| P1 逻辑资格 | LONG，空业务、空机制，facts_observed只含空白字符串 | `VALID` | 核心评估仅依赖列表非空；CLI会去空白，不能声称此空白样例能从CLI直接进入，但任意无依据非空文字没有证据核验门 |
| P2 内容相反的主张 | 原文“Company reports no new order.”，claim声称新订单900万元，引用hash/主体匹配 | 五项checks全部通过；`MODEL_INFERRED` | verify_claim没有检查正文蕴含关系；不证明生产已把其升级成客观事实或据此交易 |
| P3 无证据库 | 同一claim，不传evidence_pool | 仍含`citation_resolves` | 检查报告会将未执行的来源解析记为通过 |
| P4 历史版本缺证 | 今天构造的latest-only来源，旧pubDate=2024-04-01，截止2024-05-01，无revision | strict保留1条、丢弃0条；fetched晚于as_of | latest-only缺少版本证明仍能入strict；不是说所有晚抓数据都非法，原始历史文档可以合法晚抓 |
| P5 未知现金 | 无持仓，目标10%，压力参数20%，cash_nav=None | 新增额度10%，拒绝0条 | solver当前是跳过未知现金的数学分配，不是可执行额度证明；需调用/输出层明确限制语义 |

这些探针没有通过mock给函数“期望结果”，使用当前源码真实函数处理合成边界输入。它们验证边界条件，不提供金融数据真值或策略收益证据。

## 2. 复现脚本

在仓库根、现有虚拟环境运行下列 Python 内容（可用 PowerShell 单引号 here-string 管道传入 `.\.venv\Scripts\python.exe -B -`）。只在隔离输入上计算；不需要安装依赖。

```python
import json
import hashlib
from datetime import datetime, timezone
from src.core.research import ThesisRecord, assess_thesis
from src.core.claim_extraction import ClaimRecord, verify_claim
from src.data.research_snapshot import financial_record, EvidenceSnapshot
from src.core.portfolio_policy import AddProposal, BudgetConstraints, solve_budget

out = {}
t = ThesisRecord(
    thesis_id="probe", horizon="LONG",
    beneficiary_business="", profit_mechanism="", facts_observed=["   "],
)
out["blank_fact_thesis"] = assess_thesis(t).value

body = "Company reports no new order."
h = hashlib.sha256(body.encode()).hexdigest()
c = ClaimRecord(
    security_id="600000",
    statement="Company won a new order worth 9000000 yuan.",
    citation_uri="probe://doc", citation_hash=h, value=9000000, unit="CNY",
    published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
)
c.unit = ""  # 本探针测试内容核验，选择现有白名单接受的空单位
v = verify_claim(c, [
    {"uri": "probe://doc", "hash": h, "security_id": "600000", "body": body}
])
out["contradictory_claim_accepted"] = {
    "checks": v.checks, "status": v.fact_status.value,
}
out["no_pool_claim_accepted"] = verify_claim(c).checks

r = financial_record("600000", {
    "metric": "netProfit", "value": 999, "unit": "CNY",
    "period_kind": "cumulative", "period_end": "2023-12-31",
    "published_at": "2024-04-01", "source_uri": "probe://latest-only",
    "source_version": "latest-only-2026",
})
s = EvidenceSnapshot.build(
    "600000", datetime(2024, 5, 1, tzinfo=timezone.utc), [r], strict=True,
)
out["latest_only_strict_snapshot"] = {
    "kept": len(s.records), "dropped": s.dropped_pit,
    "fetched_after_asof": r.fetched_at > s.as_of, "revision_id": r.revision_id,
}

p = AddProposal(stock_code="600000", target_weight=0.1, pressure_loss_rate=0.2)
b = solve_budget([p], [], BudgetConstraints(per_stock_max=0.2, cash_nav=None))
out["cash_unknown_budget"] = {
    "adds": [x.model_dump(mode="json") for x in b.adds],
    "rejected": len(b.rejected),
}
print(json.dumps(out, ensure_ascii=True, indent=2))
```

输出要点（长说明字段省略）：

```json
{
  "blank_fact_thesis": "VALID",
  "contradictory_claim_accepted": {
    "checks": ["citation_present", "citation_resolves", "no_future_date", "unit_allowed", "statement_present"],
    "status": "MODEL_INFERRED"
  },
  "no_pool_claim_accepted": ["citation_present", "citation_resolves", "no_future_date", "unit_allowed", "statement_present"],
  "latest_only_strict_snapshot": {
    "kept": 1, "dropped": 0, "fetched_after_asof": true, "revision_id": ""
  },
  "cash_unknown_budget": {
    "adds": [{"stock_code": "600000", "current_weight": 0.0, "add_weight": 0.1, "target_weight": 0.1, "horizon": "MID"}],
    "rejected": 0
  }
}
```

执行环境：Windows PowerShell + 项目 `.venv`。已知沙箱无法启动该 uv 环境的基础解释器，本轮只读命令经自动审批后运行，exit_code=0。没有为探针增加文件、修改系统环境或安装包。

## 3. 静态复核坐标

以下为本轮 HEAD 的定位线索，实施前请重新 rg：

| 位置 | 复核内容 |
|---|---|
| `src/data/financial_data.py` 模块说明与 get_financial_quarterly | 承认最新版本不可区分但声称PIT；所有字段统一cumulative |
| `src/data/research_snapshot.py` EvidenceRecord.pit_confident / financial_record / EvidenceSnapshot.build | available_at非空及截止比较；pubDate赋available_at |
| `src/core/research.py` assess_thesis | facts非空分支 |
| `src/core/shadow_diff.py` capture_shadow | 另写facts非空判断；任一周期接受聚合整行plan_source |
| `src/data/horizon_plans.py` save/accept | 同键覆盖；接受绑定最新存储内容，缺完整版本历史 |
| `src/core/claim_extraction.py` verify_claim | 来源级检查；as_of使用当前机器时间；无正文语义验证 |
| `src/core/factor_compute.py` capital_return_v1 / valuation_pe_v1 / relative_trend_v1 | 代理与能力ID混用；两条无日期数组各自取窗口 |
| `src/core/portfolio_policy.py` solve_budget | cash未知跳过；pressure未知阻断；现有持仓循环只聚合暴露，portfolio_pressure初值0 |
| `tests/backtest/e6_portfolio_budget.py` load_drawdown_estimates/run_arm | 使用全测试案例回撤估计；当前行业映射；非严格资格 |
| `tests/backtest/e7_funnel_smoke.py` main | entry_condition固定False，研究COMPLETE是演示输入 |

## 4. 未做与后续检验

未做：1121项全量重跑、真实数据源取样、财报原件数值核验、真实用户走查、新AI评测、新收益回测、产品源码修复。

只读源码和局部探针不等于全链安全审计。下一轮由对应R卡实现反例回归，并验证生产消费者不会把中间状态升级成已核实/可执行。上述问题目前状态是**已确认设计或函数边界缺口，待实施修复**。
