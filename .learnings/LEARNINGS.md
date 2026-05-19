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