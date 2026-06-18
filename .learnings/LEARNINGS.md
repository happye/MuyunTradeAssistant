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