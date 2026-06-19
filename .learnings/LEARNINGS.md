# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260511-001] correction

**Logged**: 2026-05-11T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Phase 迭代中不能边收主线边持续扩外围，一旦核心闭环跑通就必须先收口再进入下一块。

### Details
用户明确指出当前开发出现了“走一步看一步、越做越多、一直干不完”的问题。复盘后确认，最近 Phase 4 的核心交易语义重构本身大多仍在主线内，但已经开始混入解释文本补充、fallback 一致性补洞、测试产物堆积等外围扩张，导致完成边界变模糊。

### Suggested Action
后续 Phase 开发按固定收口规则执行：先定义该 Phase 的完成判据；每次只做一个直接控制行为的局部切片；首次验证通过后，只允许补同一切片的必要一致性修复，不再继续向解释层、文档层、额外导出层自然扩张；生成产物不纳入持续工作面。

### Metadata
- Source: user_feedback
- Related Files: docs/v0.8.2_里程碑.md, src/core/strategy_layer.py, src/core/backtest_reporter.py
- Tags: phase-discipline, scope-control, milestone, phase4

---

## [LRN-20260511-002] best_practice

**Logged**: 2026-05-11T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
当仓库已经提供自我提升 skill 时，发生用户纠偏或自我发现流程问题后必须立即按 skill 落 learning，而不是只口头承认。

### Details
本仓库在 .github/skills/ 下提供了 self-improvement 和 self-improving 两个 skill，但此前虽然发生了用户对开发节奏和路线执行方式的纠偏，没有同步写入 .learnings，也没有把这类流程性教训固化为后续约束。

### Suggested Action
以后遇到以下信号立即落 learning：用户纠正路线或工作方式、命令/工具失败、自己发现更好的重复性做法。并在结束阶段检查是否需要把高价值规则同步到仓库记忆。

### Metadata
- Source: conversation
- Related Files: .github/skills/self-improvement/SKILL.md, .github/skills/self-improving/SKILL.md, .learnings/LEARNINGS.md
- Tags: self-improvement, process, workflow, memory

---

## [LRN-20260515-001] best_practice

**Logged**: 2026-05-15T15:40:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
全市场批量行情源不能只按“非空”判定成功，必须额外校验快照质量并区分盘前/盘后缓存阶段。

### Details
扫描链路直接信任 MarketCache.get_all_stocks() 的返回值，下游不会自行修正涨跌幅。此次问题中，全市场行情源返回了结构完整但疑似降级的快照，导致涨跌幅全为 0 仍被缓存并用于扫描。同时，盘后缓存过期逻辑会把当日 15:00 前生成的缓存继续沿用到收盘后，放大了错误快照的停留时间。

### Suggested Action
批量行情源成功条件改为“非空 + 关键字段有效 + 在交易/午休/盘后阶段涨跌幅非零比例达标”；盘后仅允许复用当日 15:00 之后生成的缓存，盘前和周末仅复用最近交易日收盘缓存。

### Metadata
- Source: conversation
- Related Files: src/scanner/market_cache.py
- Tags: scanner, market-cache, realtime, cache, fallback
- See Also: LRN-20260511-002

---

## [LRN-20260519-001] best_practice

**Logged**: 2026-05-19T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
买卖点功能上线后，必须同时验证“计算链路”和“展示链路”；仅修计算参数不足以解决用户“看不到”的问题。

### Details
本次排查中，EntryExitCalculator 与配置均正常，但持仓扫描路径未传递完整持仓上下文（has_position/entry_price）且采用简报输出，导致用户体感为“看不到买卖点”。问题不在规则本身，而在调用参数和展示口径不一致。

### Suggested Action
涉及策略结果可见性的改动，统一执行三步：
1. 校验调用参数是否完整传递（尤其持仓上下文）；
2. 校验不同命令路径的展示是否一致（-l 与 -p 分别验证）；
3. 同步 README + 使用手册 + 实盘指南，明确“触发/无触发”的可见性差异。

### Metadata
- Source: conversation
- Related Files: src/cli/main.py, src/core/orchestrator.py, 使用手册.md, README.md
- Tags: entry-exit, visibility, portfolio, cli, docs-sync
- See Also: LRN-20260515-001

---

## [LRN-20260618-001] correction

**Logged**: 2026-06-18T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: workflow

### Summary
接手会话开局漏读 `AGENTS.md`（项目单一事实源），被用户当场纠偏；根因是用 `Glob *` 列举根目录时被高基数子目录污染、关键文件未命中。

### Details
本次接手 v0.8.4 第 0 周对齐归一任务时，初始上下文采集只执行了 `Glob *` 与若干宽口径搜索，没有定向校验 `AGENTS.md` 是否存在并被读取。结果是把 `README.md`/`使用手册.md` 当作主索引，错过了仓库已经在 commit `1c6ed7a` 锁定的"AI Agent 开发手册"。用户提示"先读 AGENTS.md"才纠回主线。这类列举型搜索在大目录下极易丢文件，必须配二次校验。

### Suggested Action
任何"列举仓库根 / 收集事实源"的开局动作执行三步：
1. `Glob *.md` 或 `Glob AGENTS.md` 显式过滤主文档；
2. 与 `git ls-files | grep -i agents\|claude\|copilot` 等定向命令交叉确认；
3. 若仓库在 `.github/copilot-instructions.md`、`AGENTS.md`、`CLAUDE.md` 任一存在，必须先全文读取再动手，而非靠目录浏览推断重要性。

### Metadata
- Source: user_feedback
- Related Files: AGENTS.md, .github/copilot-instructions.md, docs/AI协作工作范式.md
- Tags: onboarding, search-discipline, source-of-truth, agents-md
- See Also: LRN-20260511-002

---

## [LRN-20260618-002] insight

**Logged**: 2026-06-18T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: docs

### Summary
ISSUES.md 中 ISS-030 状态写为 📋 待办，但代码事实显示该功能已实现；状态字段与代码已脱节，必须用 `file:line` 锚点重新确认才能信任。

### Details
2026-06-18 接手审计时发现 ISS-030（买卖点分歧检测层）实际代码已落地：
- `src/core/orchestrator.py:303-322` 实现技术信号 vs AI 情绪分歧检测；
- `src/data/models.py:313` `StrategyDecision` 已带 `divergence` 字段；
- `src/cli/main.py:305-310` 与 `src/cli/main.py:580-583` 在 `-l` 与 `-p` 两条路径输出"分歧提示"。

但 ISSUES.md 的状态行仍是"📋 待办"，给后续会话和 AI Agent 造成误导（已规划 vs 已交付边界模糊）。这是典型的"代码先行、状态滞后"案例。

### Suggested Action
ISSUES.md 状态变更必须满足：
1. 状态行（📋/🔄/✅/❌）与一组 `file:line` 锚点同时更新；
2. 解决日期与对应 commit 短哈希出现在更新记录里；
3. 若仅做了部分实现，单独拆出后续 ISS 编号（例如本次 scan market 路径未确认 → 转 ISS-031），不要把"已部分实现"含糊写在原条目里。

### Metadata
- Source: self_discovery
- Related Files: ISSUES.md, src/core/orchestrator.py, src/data/models.py, src/cli/main.py
- Tags: issue-tracking, status-drift, code-anchor, divergence-detection
- See Also: LRN-20260519-001

---

## [LRN-20260618-003] correction

**Logged**: 2026-06-18T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
看到回测"错过率高"先别盲调 entry_exit 参数 — 必须先打 trade.reason / trade.sell_path 看真正的卖出走的是哪条路径，不能假设 ISS-027 修复的模块就是触发卖出的模块。

### Details
ISS-033 原方案基于"Chandelier Exit 过早离场"假设展开了 4 个调参方向。但实际审第二轮回测 9 股的 trade['reason']/['sell_path'] 字段后发现：
- 宁德时代 8 笔 SELL 全部走 `take_profit_trim` (5) + `trend_exit` (3)
- 工商银行 8 笔 SELL 全部走 `take_profit_trim` (7) + `trend_exit` (1)
- 立讯精密 23 笔 SELL 多数走 `trend_exit` + `take_profit_trim`，**没有一笔走 entry_exit/Chandelier**

也就是说回测的卖出根本没经过 ISS-027 修复的 `src/core/entry_exit/exit_rules.py`，而是经过 `src/core/strategy_layer.py:88-97` 的 hardcoded 常量（`TAKE_PROFIT_KEEP / TAKE_PROFIT_MIN_GAIN_PCT / TREND_EXIT_BREAK_PCT`）。基于错误根因调 entry_exit 参数会浪费 3-5 天且毫无效果。

### Suggested Action
任何"策略调参 / 错过率优化 / 频繁交易"类任务的开局动作必须先做：
1. 跑代表股回测，导出 `--export-analysis-json`
2. 读 `trades[].sell_path` 与 `trades[].reason` 字段，统计 sell_path 分布
3. 确认调参目标和实际触发链路对得上，再下笔
4. 如不对应：在 ISSUES.md 记录"原方案错位"，重新定根因，再行动

具体到本仓库：策略层 `strategy_layer.py:88-97` 有 11 个 hardcoded 阈值（TAKE_PROFIT_KEEP/MIN_GAIN_PCT、STOP_LOSS_*、NORMAL_REDUCE_KEEP、TREND_EXIT_BREAK_PCT 等），它们才是回测中卖出执行参数的主战场，不是 entry_exit/config.yaml。

### Metadata
- Source: self_discovery
- Related Files: src/core/strategy_layer.py, src/core/entry_exit/exit_rules.py, ISSUES.md, tests/issue_027_round2/, tests/issue_033_round1/
- Tags: backtest, root-cause, parameter-tuning, sell-path, strategy-layer-vs-entry-exit
- See Also: LRN-20260618-002

---

## [LRN-20260618-004] best_practice

**Logged**: 2026-06-18T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: workflow

### Summary
本项目所有改动都必须在 `start.py` 路径上让用户实测得到差异。"看不见的改动 = 没做"是用户立的硬规则，不是建议。

### Details
2026-06-18 用户接手会话末尾明确表态："开发工作能够让我的使用有确实的感知，做出的任何改动或者是提升，我都能在使用启动脚本的时候感受到真实的变化"。背景是：之前 v0.8.0→v0.8.4 期间累积了大量内部改动（决策追溯、回测框架、Phase C 买卖点、金字塔仓位等），但 `start.py` banner 还停在 v0.8.0、CLI 输出没暴露任何"当前用的什么策略档"等元信息，用户跑命令时无法感知系统在迭代。

接手会话也踩中这个坑：前置 Bug 修复 commit `77aaf9d`（回测路径未传 entry_exit_config）从代码角度是真 Bug，但单独看 start.py 的输出根本看不出修没修——只有跑回测看 trades 数量才能间接验证。这种"修了等于没修"的体验导致用户失信。

### Suggested Action
每次 commit 前自问 5 条硬条件（详见 AGENTS.md § 二·五）：
1. start.py banner / help / 命令输出有可见变化
2. -l 单股分析输出多/少具体内容
3. -b 回测输出指标值变化
4. scan 排名表 / 触发详情有差异
5. 命令本身新增/删除

任一条满足即合格；都不满足且不是纯内部重构 → **必须在输出层补一行让用户看见**。例如：
- 调了策略参数 → 在 CLI 加"当前使用参数档：牛市档/震荡档/熊市档"诊断行
- 修了静默 Bug → 在受影响命令加"已生效"或"启用 X 模块"提示
- 改了配置加载 → 启动时打印"加载配置：xxx.yaml (key=value)"

### Metadata
- Source: user_feedback
- Related Files: start.py, src/cli/main.py, AGENTS.md
- Tags: user-perception, observable-output, principle, dev-discipline
- See Also: LRN-20260618-003

---
## [LRN-20260619-001] best_practice

**Logged**: 2026-06-19T00:00:00+08:00
**Priority**: critical
**Status**: pending
**Area**: workflow

### Summary
调参类工作必须有 4 条硬纪律：30 分钟前提验证 / 第三轮失败原则 / 改善门槛 / 基准对照。否则会陷入"改一点点参数→看一点点改善→继续改"的兔子洞。

### Details
v0.8.5 ISS-033 + 阶段 3 累计跑了 5 轮调参（一阶段 +0.22pp / 二阶段 +0.19pp / 三阶段 +0.41pp / 阶段 3.1 +0pp / 阶段 3.3 +0.56pp），累计改善 +1.4pp，离目标 +15pp 仍然差 13.6pp。

每一轮我都说"方向对、改善小、继续做"。但事实是：第二轮就该停下来质疑架构。继续做三轮纯属浪费时间。

根因有四：
1. 调参前没确认前提（如 ISS-033 二阶段没看 MarketState 实际分布就调分档参数，结果 156/242 天是 TRANSITION 没走分档）
2. 没设"第 N 次失败就停手"的红线
3. 没用"基准噪声门槛"判断改善是否真实
4. 看绝对收益不看对照，把噪音当成功

### Suggested Action
所有"调参 / 优化参数 / 改阈值"类工作必须满足：

1. **30 分钟前提验证法则**：动手前花 30 分钟做"我要改的参数实际生效几天/几次？"验证。例如调 RISK_ON 分档参数前先打印 RISK_ON 命中天数；调 weak_sell 阈值前先看 trade.sell_path 分布。前提不成立直接放弃这个方向。

2. **第三轮失败原则**：同一参数家族（如 take_profit / chandelier / position_cap）调过 2 次都没显著改善（< 改善门槛），第 3 次直接停手。换方向或退一步质疑架构本身。

3. **改善门槛**：单股改善 < 2pp / 整体平均改善 < 1pp 视为噪声水平，不算成功，不写"已解决"，不进 commit。改善必须 >= 噪声门槛才算有效。

4. **基准对照纪律**：开发新功能前**先跑一次对照基线**（同期同股同参数 PlanGuard 关闭等），新功能跑完和基线 diff，超过门槛才算有效。不要看绝对收益。

### Metadata
- Source: self_discovery
- Related Files: src/core/strategy_layer.py, src/core/plan_guard.py, docs/v0.8.5_阶段3_final.md, ISSUES.md (ISS-033 / ISS-037)
- Tags: parameter-tuning, decision-discipline, anti-pattern, rabbit-hole
- See Also: LRN-20260618-003

---

## [LRN-20260619-002] best_practice

**Logged**: 2026-06-19T00:00:00+08:00
**Priority**: critical
**Status**: pending
**Area**: workflow

### Summary
当发现自己在兔子洞（连续 2 次试验改善 < 1pp 噪声水平）时，**强制跳出来**去 web search / 读学术资源 / 咨询其他 agent，找更好的算法/方法论，而不是继续在当前架构里调参。

### Details
2026-06-19 用户明确建议："如果你发现你正在掉入一个兔子洞陷阱，那么你就要需要跳脱出来，从互联网上寻找更好的解法。"

具体到本仓库：v0.8.5 三阶段累计改善 +1.4pp，本应在阶段 3.1 失败（+0pp）后跳出来，去查"为什么 PlanGuard 在长样本回测无效"——可能学术上有现成方法（cooldown_period / regime_dependent stop / multi-factor exit）能解决，但我没去查，继续做了阶段 3.3。

### Suggested Action
触发条件：连续 2 次同方向试验改善 < 1pp 噪声水平（按 LRN-20260619-001）

跳出动作（按优先级）：

1. **WebSearch 先行**：用 web search 查"当前问题 + 主流量化方法论 + 学术 paper"。例如调"持有期"问题先搜 holding period optimization quantitative trading 而不是再调 max_hold_days 参数。

2. **读策略库 RAG**：本仓库有 65 个策略 txt + RAG 索引。先用 RAG 检索看有没有现成方法，再决定要不要重新发明。

3. **咨询专门 agent**：spawn code-architect 或 general-purpose agent 让它独立做 research，避免我自己在原方案里循环。

4. **WebFetch 学术资源**：对找到的 paper / 主流框架（AQR / 桥水风格 / RiskParity / Event-driven）用 WebFetch 抓实际方法描述，不凭训练知识拍脑袋。

只有当上述 4 步都没找到更好方案时，才回到原架构继续优化。

### Metadata
- Source: user_feedback
- Related Files: docs/v0.8.5_阶段3_final.md
- Tags: rabbit-hole-escape, web-search-first, research-discipline
- See Also: LRN-20260619-001

---

## [LRN-20260619-003] insight

**Logged**: 2026-06-19T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
回测结果不能证明工具好坏，最多只能证明"这个工具在测试期那个特定环境下的表现"。要回答"工具好不好"必须满足：(1) 多年覆盖多个市场周期 (2) 接入宏观/新闻/基本面 (3) 不与训练期重叠（避免过拟合）。

### Details
2026-06-19 用户提问："是不是回测的结果就证明了目前我们的量化工具的结果是好是坏？有没有可能是我们当前我们的量化策略不适用于2024年的股票数据呢？"

这个问题点中本仓库回测体系的根本局限。具体事实：

1. **当前回测覆盖期**：2024 单年 = 1 个市场周期样本。学术界量化最少要 5-10 年 + 多个市场周期才能下结论
2. **当前回测无新闻面 / 无基本面 / 无地缘风险**：技术架构文档第 1000 行自承"无基本面数据 | 纯技术分析+新闻情绪"。地缘局势 / 金融风暴 / 美元体系 / 行业政策切换 全部不在策略输入里
3. **2024 是 A 股结构性分化极端年**：半数行业涨 30%+ / 半数跌 -10%~-30%，**任何基于 MA 的趋势策略都会同时显示"风控好 + 错过率高"**——不是策略不好，是 2024 年环境特殊
4. **过拟合风险**：本仓库 5 轮调参全是"看 2024 数据→调参→再看 2024 数据"，已经过拟合 2024，跑 2025 / 2023 可能完全是另一个画像

### Suggested Action
1. **诚实声明回测局限**：技术架构文档 / README / 使用手册都应明确说"回测仅反映测试期表现，不能证明工具好坏"
2. **跑多年回测**：扩到 2020-2024 五年，覆盖 2020 牛市、2021 高位、2022 熊市、2023 震荡、2024 分化。任何"调参→改善"声明都必须在多年样本上验证
3. **接入宏观/新闻**：v0.8.6+ 真正方向不是再调参，是接入新闻面（财联社/RSS）+ 基本面（财务 API）+ 地缘事件（影响行业的政策/国际新闻）让策略对"濒临崩塌环境"有感知
4. **保留训练-验证分离**：调参用 2020-2022，验证用 2023-2024，避免过拟合

### Metadata
- Source: user_feedback
- Related Files: docs/技术架构文档.md, docs/v0.8.5_阶段3_final.md, docs/v0.8.5_扩样本验证报告.md
- Tags: backtest-limits, overfitting, multi-year-validation, macro-input
- See Also: LRN-20260619-001, LRN-20260619-002

---
