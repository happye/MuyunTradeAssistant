# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260829-001] correction

**Logged**: 2026-08-29T00:00:00+08:00
**Priority**: critical
**Status**: pending
**Area**: workflow

### Summary
**教训写进了不会被读取的位置，等于没写。** 各 Agent 工具的 skill 目录互不兼容且没有交集，只部署到其中一个 = 对其他工具完全不可见。本项目把自我提升 skill 只放在 `.github/skills/`（Copilot 路径），而实际干活的 Agent 不扫那个目录 → 那条「失败后必须先走 self-improvement skill」的强制规则**从未执行过一次**。同理，教训写进工具自带的会话记忆目录（本项目是 `.workbuddy/memory/`，`.gitignore:23` 忽略）→ 换工具/换机器/清缓存即归零。

### Details
2026-08-29 两轮对抗性审查累计 69 条发现后回溯 git 历史，实测：

1. `.github/copilot-instructions.md` 自 `c8cbcaa`（2026-05-26）冻结至今（57 行），而 `AGENTS.md` 已 280 行（08-29）。两份「项目规则」严重漂移：copilot-instructions 仍写着 `src/skills/` contains YAML、"tests 主要用脚本式不用 pytest" —— 均已过时。
2. 该文件里最关键的规则「Before continuing after a meaningful failure, first consult the `self-improvement` skill」**指向一个实际干活的 Agent 永远加载不到的路径 → 从未被执行过一次**。
3. `.learnings/LEARNINGS.md` 停在 2026-08-25、`ERRORS.md` 停在 07-29 → 本周 69 条发现**零条进入**。
4. 23 条 LRN 中 **13 条 Status 永远 pending**（57%），其中 `LRN-20260511-002` 自己就写着「发生纠偏必须立即落 learning」，却躺了 3 个多月没闭环。
5. 量化佐证：`fix : feat = 75 : 71`；网络/超时/线程主题提交 23+ 次横跨 5 个月；文档类改动（ISSUES 63 + AGENTS 32 + README 30 = 125 次）超过任何核心模块。

### Suggested Action（已落地，2026-08-29）
1. **教训必须落进仓库内 `.learnings/`** —— 这是唯一跨工具、跨机器、跨会话可靠的沉淀点。工具自带的会话记忆目录因工具而异且大多不进版本控制，只写那里 = 没写。
2. **跨工具资产多入口部署（不要依赖任何单一目录的自动发现）**：
   - 主副本放**项目根 `skills/muyun-dev-discipline/SKILL.md`**（工具中立，任何工具都能按路径读到）
   - 用 `scripts/sync-agent-skills.sh` 同步副本到 `.claude/skills/`、`.github/skills/`、`.cursor/rules/`、`.codex/skills/`、`.workbuddy/skills/`
   - **并在各工具的入口指令文件里写死显式指针**：`AGENTS.md`（§五·五）、`CLAUDE.md`、`.github/copilot-instructions.md`、`.cursorrules` —— 入口文件的覆盖面远大于 skill 目录的自动发现，这是最可靠的兜底
3. 每次会话收尾：把会话记忆里有长期价值的部分蒸馏进 `.learnings/`。
4. 每条硬约束要么配一个能自动跑的检查，要么删掉 —— 没有自动化检查的规则 = 建议 = 不存在。
5. **新增任何跨工具资产时，先问一句：这个路径在所有我可能会用的 Agent 工具里都能被发现吗？** 不能就补入口，不要假设。

### Metadata
- Source: self_discovery
- Related Files: `.github/copilot-instructions.md`, `.github/skills/self-improvement/SKILL.md`, `.learnings/LEARNINGS.md`, `.workbuddy/skills/muyun-dev-discipline/SKILL.md`, `AGENTS.md`, `.gitignore`, `开发问题根因复盘_20260829.md`
- Tags: knowledge-pipeline, skill-loading-path, cross-session-memory, agents-md, process-debt

**See Also**: LRN-20260511-002, LRN-20260618-004

---

## [LRN-20260829-002] correction

**Logged**: 2026-08-29T00:00:00+08:00
**Priority**: critical
**Status**: pending
**Area**: workflow

### Summary
修 bug 有三类「不彻底」，是本项目 bug 持续复发的直接原因：①只修被点名的那一处不扫同类 ②文档/注释动作冒充行为修复 ③修完不落回归测试导致下一轮审查重新付费。

### Details

**① 只修被点名的那一处（同类点不扫）**
- `with ThreadPoolExecutor` 陷阱：ISS-066 / `a47783b` 只改了 `data_provider.py:51/76`，漏了 `akshare_client.py:34` → 08-29 审查 A04 又抓到。
- HF 镜像源：修了 3 次（`8f5eb34` → `afe2406` → `87047f8`），08-29 B12 又发现同文件 `HF_ENDPOINTS` 元组顺序官方在前 + `os.environ` 写入不还原 → 第 4 次。
- 版本号统一：A31 只统一了 start.py / main.py / AGENTS.md，README 停在 v0.8.7.2、笨总文档已写 v0.8.7.6 → 继续漂。

**② 文档/注释动作冒充行为修复**
- `87047f8` message 写「HF_ENDPOINT默认改国内镜像」，`git show --stat` 显示**只加了 1 个 76 行 .md，零行代码**。真正的修复两天前 `afe2406` 已做过 → 内容为空的重复提交。
- `c993c6d fix(全局审查M-B): plan_guard stale docstring 对齐` 只改 docstring；08-29 复查 `plan_guard.py:177` **依然压制** `take_profit_trim`，与 `:14` docstring 自相矛盾 → M-B 至今未修。
- `814963c` 把 with 块陷阱写进 AGENTS.md 算修完 → `akshare_client.py` 那个 with 块活到 08-29。
- 讽刺点：ISS-066 自己的记录里就写着「「注释提醒」不构成修复」，下一条提交又这么干。

**③ 修完不落回归测试**
- 四个月至少 4 轮大审查（07-16 全局审查 / 07-21 对抗审查 / 08-24 网络共享性审计 / 08-29 两轮对抗审查），累计 150+ 条发现，**转成的回归测试 = 0**。
- 反证：08-29 最有效的手段恰恰都不是读代码 —— 跑全量测试抓到 B12（全局 `os.environ` 污染 → 测试顺序依赖，两轮静态审查都漏）；跑脚本交叉比对一次跑出 9 个缺失条件 + 25 个死条件（人工读 14 个 YAML × 82 个注册表项必然漏）。

### Suggested Action
1. **修 bug 四步法**：①写回归测试（先红灯）→ ②修（转绿灯）→ ③扫同类点 → ④落 `.learnings/`。缺一步不许提交。
2. **同类点扫描清单**（修完必跑）：`with ThreadPoolExecutor` / `os.environ[...] = ` / `except`+`logger.debug`+`continue`（静默 fail-open，条件不计入分母导致规则**降档触发**，比不触发更危险）/ `if <DataFrame>:` / 中文全角 `（）` / `0\.8\.7\.[0-9]`。扫出剩余 → 要么一起修，要么在 ISSUES.md 记「已知剩余 N 处」，不许默默留着。
3. **非空校验**：提交前跑 `git show --stat HEAD | tail -5`，确认有代码改动。message 声称改了但 diff 零代码 → 不许用 `fix` 前缀。
4. **验证靠跑不靠读**：动全局状态 → 必须跑全量测试（不能只跑单文件）；动 YAML↔代码映射 → 必须脚本交叉比对（但结论要回读代码确认，`price_position` 会假阳性）。
5. **审查产物处置顺序改掉**：找到问题 → **先写测试** → 再修 → 扫同类 → 落 `.learnings/`。修完不落测试 = 下次重付一次审查费。

### Metadata
- Source: self_discovery
- Related Files: `src/data/akshare_client.py`, `src/rag/embedding.py`, `src/core/skill_engine.py`, `src/core/plan_guard.py`, `src/cli/main.py`, `start.py`, `AGENTS.md`, `README.md`, `开发问题根因复盘_20260829.md`, `对抗审查_20260829_待裁决清单.md`, `对抗审查_20260829_第二轮_待裁决清单.md`
- Tags: regression, incomplete-fix, docs-as-fix, no-test-coverage, same-class-scan, verification-by-running

**See Also**: LRN-20260829-001, LRN-20260619-001, LRN-20260618-002

---

## [LRN-20260825-001] correction

**Logged**: 2026-08-25T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: tooling

### Summary
工作区外的 Downloads 文件用 grep/search 读不到。若目标不在 workspace，必须立刻说明限制并改用终端解析，不能空等。

### Details
用户把 LanguageService 日志放在 `C:\Users\HanPeiyi\Downloads\...` 时，工作区搜索返回 empty，会话看起来卡住。用户随后把目录挪到 `C:\Project\Muyun\LanguageServiceUnitTestResult_60d` 后才能正常扫。

### Suggested Action
日志/下载产物若在 workspace 外：先明确说搜不到，立刻用终端读；需要反复分析时请用户先拷进工作区。

### Metadata
- Source: user_feedback
- Related Files: LanguageServiceUnitTestResult_60d/
- Tags: workspace-scope, downloads, search-limit

**See Also**: 无

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

## [LRN-20260812-001] knowledge_gap

**Logged**: 2026-08-12
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
akshare 股东户数/融资余额 API 参数与直觉相反，直接传股票代码会 hang

### Details
- `stock_zh_a_gdhs(symbol)` 参数是**日期**(YYYYMMDD)非股票代码，传代码会 hang（em 端点反爬+参数错）。
  正确取单股股东户数历史：`stock_zh_a_gdhs_detail_em(symbol=代码)`，列含「股东户数-本次/上次/增减」。
- `stock_margin_detail_sse` / `stock_margin_detail_szse` 参数是**单日期**(YYYYMMDD)，返回全市场该日融资余额，
  无 start_date/end_date/stock_code 参数。取单股近5日增幅需查2个日期+过滤代码（2次调用）。
- `AKShareClient._ensure_baostock_login` 不存在（是模块级函数 `from src.data.akshare_client import _ensure_baostock_login`）。
  baostock 代码前缀用 `AKShareClient._normalize_stock_code(code)` 类方法。
- akshare 底层 requests 无 timeout，卡住会拖垮；必须用 `_safe_call`(data_provider) 或 `ThreadPoolExecutor`+`shutdown(wait=False)` 包硬超时。
  注意 `with ThreadPoolExecutor` 超时后 shutdown(wait=True) 仍阻塞等线程，要显式 `shutdown(wait=False)`。

### Suggested Action
新增 akshare 调用前先查 signature(`inspect.signature`)，别按函数名猜参数；网络调用必包超时。

### Metadata
- Source: error
- Related Files: src/core/exit_signals/stock.py, src/core/exit_signals/sector.py, src/data/source_check.py
- Tags: akshare, api-gotcha, timeout, connectivity
- Pattern-Key: akshare.api_signature_guess
- Recurrence-Count: 3
- First-Seen: 2026-08-12
- Last-Seen: 2026-08-12

---

## [LRN-20260816-001] insight

**Logged**: 2026-08-16
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
AI 输出"没专业度"类问题，先查数据供给缺口，再动 prompt--garbage in garbage out。

### Details
用户反馈 chat 行业分析"毫无专业性"（不结合需求端/供给不细分/不挖上下游）。排查确认根因不是 prompt：chat 6 个工具全是技术面/新闻/策略知识，模型手里根本没有供需/价格/产业链数据，只能靠训练记忆空谈。补数据层（期货价格/仓单/需求数据/产业链图谱）后同一问题回答质量质变（真实数据带日期、供需分端、持仓链条定位、四阶段周期判断）。另一个教训：chat"提问无回答"的根因是模型在无 tools 的调用里把 <tool_calls> 伪 XML 当正文输出并停止--工具轮次不足时优先让模型能继续真调工具，而不是逼它"直接回答"。

### Suggested Action
模型输出质量问题先做"输入盘点"（模型实际拿到什么数据），缺口在数据层就补数据层；function calling 循环达上限后宁可追加轮次/提示消息，也不要发不带 tools 的"逼答"调用（会触发伪工具调用文本）。

### Metadata
- Source: ISS-061 全程 / ISS-058
- Related Files: src/data/industry_data.py, src/chat/agent.py
- Tags: data-first, prompt-engineering, function-calling, root-cause

---

## [LRN-20260816-002] best_practice

**Logged**: 2026-08-16
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
外部数据接口必须登记台账（来源/性质/坑/实测状态），接入前必须 live-probe 实测，不许凭记忆写接口调用。

### Details
ISS-061 实测踩坑清单：东财板块接口(_em)本环境反爬断连（akshare 文档说能用）；PMI/PPI 表倒序（tail 会拿到 2008 年数据）；stock_zygc_em 必须带 SZ/SH 前缀且收入比例是小数；乘用车表未来月份为 NaN；动力煤 ZC0 期货休眠停在 2022-12（旧数据会冒充新数据）；现货接口非交易日返回空表需日期回退；THS 成分股通道是自研爬虫（v_code cookie 机制）需限速保护。全部坑都是 live-probe 发现的，没有一个能从文档看出来。台账落地 docs/数据接口台账.md，规矩：改代码不改进台账=半成品。

### Suggested Action
新接任何外部接口：(1) 先 live-probe 打印真实返回结构（列名/顺序/单位/空值行为）再写 formatter；(2) 登记台账含失败行为；(3) 全部调用带超时+降级标注；(4) 数据带日期+新鲜度守卫防旧数据冒充新数据。

### Metadata
- Source: ISS-061 v2-v4
- Related Files: docs/数据接口台账.md, src/data/industry_data.py, src/scanner/market_cache.py
- Tags: api-contract, live-probe, anti-crawl, data-hygiene, ledger-discipline

---

## [LRN-20260816-003] insight

**Logged**: 2026-08-16
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
知识库类功能的全量覆盖用"通用引擎+模型自举沉淀"架构，不手写全量（不可维护）也不留降级路径（用户会质疑二等公民）。

### Details
产业链图谱最初手写 6 条，用户质疑"其他 100+ 行业为什么降级、6 条你维护了吗"。正解不是手写 100 条：手写不可维护（那 6 条其实也没人持续维护）。落地 v4：通用引擎保证任意行业数据待遇对等（板块解析+商品锚+主营抽样+持仓定位），save_chain_graph 让 AI 首次分析后把梳理的结构沉淀到 auto yaml（同名覆盖防冗余、created_at+30天超期提示防过时、手写优先防污染、"只写工具结果出现过的代码"防幻觉、chains rm 给用户删除权）。知识自增长且每层降级不说谎。

### Suggested Action
覆盖型知识需求：先做通用路径保证人人平等，再让模型在使用中沉淀高质量结构（带时效标注+人工可删改），种子数据只做质量标杆不做特权层。

### Metadata
- Source: ISS-061 v4 用户质疑驱动
- Related Files: configs/industry_chains_auto.yaml, src/data/industry_data.py
- Tags: knowledge-bootstrap, self-growing-knowledge, tier-parity, anti-redundancy

---

## [LRN-20260701-001] correction

**Logged**: 2026-07-01T17:40:00+08:00
**Priority**: critical
**Status**: completed
**Area**: network/data-source

### Summary
解决 A 股全市场行获取失败的核心死穴：多线程 os.environ 竞态代理清空冲突 + 局域网防火墙/360/天擎对 HTTP 重定向与注入干扰（导致的 JSONDecodeError） + 502 Bad Gateway 丢包无保护。

### Details
2026-07-01 用户报告 `bz scan` 运行时，新浪 API 报空数据且 efinance fallback 备用源抛出 `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。
经过极高精度的 debug 发现三层联动超级 Bug：
1. **新浪多线程竞态代理改写冲突**：`_fetch_sina_market` 并发拉 73 页时在 `ThreadPoolExecutor` 中调用了 `with self._without_proxy():`，高并发下不断修改/恢复全局 `os.environ` 变量，由于竞态，90% 的线程在发送时其实代理又被恢复了，导致新浪 API 被爬虫拦截。
2. **efinance 同步与 http 协议重定向干扰**：efinance 的 `get_realtime_quotes` 并行抓东财 push2.eastmoney.com 的 `http://` 接口。在有本地防火墙、360安全网络盾审计、公司网关的局域网下，`http://` 请求被强行重定向为含有警告、提示的 HTML，而没有公网证书的裸 requests session 无法处理拦截（并且没有调用 fix_curl_ssl_paths() 注入合并证书），导致请求失败。因为 efinance 未判断 5xx 或内容，盲目调用 `.json()` 从而引发 Expecting value 崩溃。
3. **东方财富行情 502/504 并发丢包无保护**：多线程高密集突发拉几十页东财接口时，极易因防爬机制，使其中一页随机遭遇 `502 Bad Gateway`。由于这一个 HTML 垃圾页面，efinance 内部的 responses 列表转换在 requests 转化 json 的瞬间全盘崩溃。

### Suggested Action
针对复杂的国内多线程行情拉取及多系统 SSL 过滤拦截，实施三层极客级安全护航：
1. **线程安全显式免代理**：放弃在多线程内部修改全局 `os.environ` 的 `_without_proxy` context；对 `Session` 显式强制注入：`session.trust_env = False; session.proxies = {"http": None, "https": None}`。
2. **行情源初始化一键注入证书修复**：在 `MarketCache` 行情源的 `__init__` 函数顶部以及入口文件 `start.py`, `main.py` 入口点最开始显式同步拉取 `fix_curl_ssl_paths()`，确保 requests 对 SSL 审计证书库全通无阻。
3. **对 efinance 会话实施高级多线程重试与故障隔离 Monkey Patch**：在 `MarketCache` 内对 efinance.common.getter 的 CustomedSession 实施覆盖拦截，强制将 `http://` 升级为 `https://` 抵御明文劫持重定向，同时设定最深 3 次避让重试，在重试失败时投喂安全的空 JSON，避免整个 A 股行情拼接被单个 502 连累崩溃。

### Metadata
- Source: self_discovery
- Related Files: src/scanner/market_cache.py, start.py, src/cli/main.py, src/data/source_check.py
- Tags: multithreading-concurrency, environment-concurrency, monkey-patch, ssl-intercept, efinance-fix, sina-fix
- See Also: LRN-20260618-004

---

## [LRN-20260702-001] correction

**Logged**: 2026-07-02T13:40:00+08:00
**Priority**: critical
**Status**: completed
**Area**: network/data-source

### Summary
解决 A 股批量打分时高频重复爬网爆 IP 触发 456/502 封禁的问题，修复 efinance 零除崩溃 (division by zero)，引入「腾讯指数成交额极速通道」实现 20ms 全无阻获取大盘总成交额。

### Details
在执行 `bz scan` （批量打分）时：
1. **多实例内存缓存失效**：`get_market_turnover` 内部在每只股评分时都新鲜实例化 `mc = MarketCache()`，由于是新实例，内存中 `_stock_df` 为 None，强制重试拉网 73 页，对全市场 5300 只股票高频重复多线程拉网。
2. **IP 遭遇新浪 WAF/502 封禁**：短时间拉网 5+ 遍后触发新浪 WAF 的反爬机制，返回 HTTP 456 封禁；同时备用源 efinance 遭遇 502 Bad Gateway 丢包，抛出 `division by zero` 零除崩溃（因空 diff 列表导致 efinance 内部 `divmod(total, pz)` 的 `pz=0`）。

### Suggested Action
1. **共享类级别属性缓存**：在 `MarketCache` Class-level 申明前置静态变量缓存，利用 `@property` 和对应的 setter 使多实例（`mc = MarketCache()`）共享完全相同的内存指针，杜绝跨实例重复拉网。
2. **腾讯指数成交额极速通道**：重构 `get_market_turnover()`，不再高吞吐拉取 A股 5800 只股票。改用首选直连腾讯指数接口（`http://qt.gtimg.cn/q=s_sh000001,s_sz399001`），直接累加上证指数与深证成指的成交额（万元）。仅需 1 个请求仅耗时 15-30ms，0压力 100% 精准获取大盘成交量，一劳永逸避开各种 WAF 反爬限制。
3. **安全抛出防零除崩溃**：如果 efinance 备用源确实重试仍然失败，Monkey Patch 安全抛出 `RuntimeError` 让 `MarketCache` 从 `try-except` 捕获后优雅 fallback，绝不抛出 `ZeroDivisionError`。

### Metadata
- Source: user_feedback
- Related Files: src/scanner/market_cache.py, src/core/benzong/data_provider.py
- Tags: class-cache, property-redirection, tencent-index-api, market-turnover, zero-division-error
- See Also: LRN-20260701-001

---

## [LRN-20260724-001] correction

**Logged**: 2026-07-24T14:00:00+08:00
**Priority**: high
**Status**: completed
**Area**: chat/rag/bootstrap

### Summary
RAG startup dependency preflight does not make a Hugging Face embedding model available. Sentence-transformers loads lazily, so model download failure can occur during the first embedding call after the embedder object was created.

### Details
The Chat preflight added by `0eb6bd7` only checks Python package imports and suggests `uv sync`. It does not download or validate `BAAI/bge-small-zh-v1.5`. `RAGService.initialize()` previously propagated the lazy model error to a generic failure, and `chat.tools` then disabled `search_knowledge` instead of degrading.

### Resolution
Validate the sentence embedder during RAG initialization, fall back to TF-IDF when the model is unavailable, and persist embedder metadata so an index built with a different provider is rebuilt rather than reused.

### Metadata
- Source: user_feedback
- Related Files: src/rag/service.py, tests/test_rag_bootstrap_fallback.py, src/chat/tools.py
- Tags: rag, sentence-transformers, lazy-loading, offline-fallback, tfidf
- See Also: LRN-20260701-001


---

## [LRN-20260824-001] best_practice

**Logged**: 2026-08-24T10:05:00+08:00
**Priority**: high
**Status**: completed
**Area**: network/data-source

### Summary
全市场行情源失败时必须分别探测协议、证书链和响应正文；`fix_curl_ssl_paths()` 成功不代表目标站 HTTPS 证书一定可验证。

### Details
`bz scan --allrules` 获取不到全市场快照。用项目 `.venv` 并完整复现 `start.py` 的代理和 CA 初始化后确认：新浪 HTTPS 与东财 HTTPS 均报 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`，东财 HTTP 返回 502，而新浪同一公开 JSON 的 HTTP 端点一度返回 200。代码还残留在线程内调用 `_without_proxy()` 的全局环境竞态，与既有代理故障记录相符。协议切换后首次全量抓取成功，但稍后连续 5 次新浪 HTTP 单页请求均返回 456 HTML，证明协议修复只消除了确定性的 TLS 故障，不能消除新浪 WAF/反爬的间歇性封禁。

### Resolution
共享代码保留 HTTPS 默认值；当前机器通过 gitignored 的 `configs/network.local.yaml` 覆盖为当时可用的 HTTP 端点。`requests.Session` 显式设置 `trust_env=False` 与空代理，并移除并发线程内对全局代理环境的改写。新增离线回归测试锁定默认协议、本机覆盖和 session 配置。真实 `get_all_stocks(force_refresh=True)` 曾返回 5470 行、5467 行有效价格，但该结果只能证明协议路径可用，不能宣称外部源已稳定；交付前必须重复探测并明确 WAF 剩余风险。

### Metadata
- Source: error
- Related Files: src/scanner/market_cache.py, tests/data_sources/test_market_cache_sources.py, start.py
- Tags: scanner, sina, ssl, certificate-chain, proxy, protocol-fallback
- See Also: LRN-20260701-001, LRN-20260515-001

---

## [LRN-20260824-002] best_practice

**Logged**: 2026-08-24T11:30:00+08:00
**Priority**: high
**Status**: completed
**Area**: tests/network

### Summary
真实第三方接口体检必须匹配产品启动顺序，并用独立子进程实施硬超时；否则会产生 AI 证书假阴性或被第三方线程永久拖住。

### Details
`test_all_api.py` 原先在 SSL 初始化前调用 AI，导致实际可用的 Kimi 被误报为证书失败；“DeepSeek”用例又通过当前 provider 构造客户端，当前 provider 为 Kimi，标签与调用对象不一致。线程池 future 超时后即使 `shutdown(wait=False)`，Python 退出阶段仍会等待非 daemon 网络线程，整份体检无法完成。

### Resolution
测试启动时先执行与 `start.py` 相同的 `fix_curl_ssl_paths()`；DeepSeek/Kimi 分别读取自己的配置节点；每项真实接口测试放入独立子进程，由父进程按项强制超时。最终标准体检可稳定输出完整 15 项报告。

### Metadata
- Source: error
- Related Files: tests/data_sources/test_all_api.py, tests/data_sources/test_source_check.py
- Tags: integration-test, subprocess-timeout, startup-parity, ai-provider, false-negative
- See Also: LRN-20260824-001

---

## [LRN-20260824-003] best_practice

**Logged**: 2026-08-24T12:00:00+08:00
**Priority**: high
**Status**: completed
**Area**: config/network

### Summary
跨机器 TLS/网络差异不能硬编码进共享数据源实现，应保留安全的 HTTPS 默认值，并通过 gitignored 本机配置覆盖协议。

### Details
新浪 HTTPS 在初始开发机器可用、当前机器证书链校验失败，说明差异属于本机信任库、网络出口或 TLS 检查环境，而不是接口对所有机器失效。把 HTTP 直接写进 `market_cache.py` 会让其他机器也失去传输认证与完整性保护；使用 `git update-index --skip-worktree` 隐藏源码修改又会漏掉远端安全修复并制造隐形分叉。

### Resolution
共享默认恢复 HTTPS；新增 `configs/network.local.yaml` 本机覆盖入口和 `MUYUN_SINA_MARKET_URL` 环境变量入口。本机文件由 `.gitignore` 排除，`git pull` 不覆盖；仓库只跟踪不含本机值的示例文件和解析能力。

### Metadata
- Source: user_feedback
- Related Files: src/scanner/market_cache.py, configs/network.local.yaml.example, .gitignore
- Tags: local-config, https, machine-specific, git-pull, trust-store
- See Also: LRN-20260824-001

---

## [LRN-20260825-001] best_practice

**Logged**: 2026-08-25T00:00:00+08:00
**Priority**: medium
**Status**: completed
**Area**: tests/data-source

### Summary
行情质量校验必须兼容只含最小字段的测试替身，不能对 `DataFrame.get()` 的标量缺省值直接调用 Series API。

### Details
rebase 合并“共享缓存”和“全零行情拒绝入缓存”时，聚焦测试使用不含“最新价”列的最小 DataFrame。`pd.to_numeric(df.get("最新价"))` 在字段缺失时返回标量 NaN，继续调用 `.fillna()` 会抛出 `AttributeError`。

### Resolution
仅在“最新价”列存在时执行全零质量校验；字段缺失时保持既有最小测试替身兼容。真实行情含该列时仍拒绝全零快照，成功数据继续写入类级共享缓存并清除失败冷却。

### Metadata
- Source: error
- Related Files: src/scanner/market_cache.py, tests/core/test_market_cache_shared.py
- Tags: pandas, dataframe, test-double, market-cache, validation
- See Also: LRN-20260515-001, LRN-20260824-001

---

---

## [LRN-20260829-002] best_practice

**Logged**: 2026-08-29T23:50:00+08:00
**Priority**: critical
**Status**: completed
**Area**: data/indicators

### Summary
数学公式类修复必须"已知输入→已知输出"对照权威参考实现，且结论要回读代码做机理确认——两轮实证：B16 归一化首修差 100 倍因子被冒烟抓出；C06"一字板被判中性"经实测是假阳性。

### Details
第三轮审查（C 区块）验指标公式：RSI/ATR 用 SMA 而非 Wilder（通达信 SMA(X,N,1)=ewm(alpha=1/N)），判反率最高 14%。修复本身不难，两个教训更值钱：
1. **修 B16 归一化时第一版分母写错（差 100 倍因子）**——"读代码像对的"和"算出来是对的"是两回事。total_score 已含 liquidity_coeff 乘法，归一化分母必须是 正权和×coeff 而非 100×正权和×coeff。靠已知输入（满分三档流动性→都应=100）的冒烟当场抓出。
2. **C06 假阳性**：审查脚本证明"全平 K 线 KDJ=50"，据此推断"一字板被判中性丢信号"。回读机理+构造连板数据实测：单日一字涨停时 9 日窗口含前日区间，分母>0，RSV 自然=100（K=94.85 进超买区），根本不会走 fillna(50)；NaN 只在窗口全平时出现，而那正是无波动平盘，50=中性**正确**。若照审查建议改成方向 mask，是给不可能分支写死代码。

### Suggested Action
涉及公式：①先用已知输入算已知输出（全档位边界值），不信"代码看起来对"；②审查的"实测发现"也要复核机理——脚本构造的边界（全平序列）可能不代表它声称的现实场景（一字板）；③修复引入新公式时先推一遍量纲/因子（total 已含什么系数？分母该含几次？）。

### Metadata
- Source: self_discovery
- Related Files: tests/core/verify_indicator_math.py, tests/core/test_indicator_math_regression.py, src/data/data_feeder.py, src/data/akshare_client.py
- Tags: formula-verification, known-input-known-output, false-positive, wilder-rma
- See Also: LRN-20260829-001

---

## [LRN-20260829-003] best_practice

**Logged**: 2026-08-29T23:50:00+08:00
**Priority**: high
**Status**: completed
**Area**: workflow

### Summary
live/回测两套指标 builder 手工维护是结构性漂移源——C04（守卫 60 vs 120）是 A02（live 缺 ATR）同款 bug 第三次复发；回归测试只锁单侧抓不住不对称。

### Details
C03/C04/C07 三个问题的共同根因：`akshare_client.calculate_indicators`（live）与 `DataFeeder._build_stock_data`（回测）两套 builder 各写各的守卫/公式/字段，任何一边改动另一边不知道。测试全绿也抓不到——因为测试各自 mock 单侧。同类点：high_60d 守卫 live=20/回测=20（都错但一致）；high_120d 守卫 live=120/回测=60（不一致）。

### Suggested Action
改任何指标公式/守卫/字段：①两边必须同一 commit 同步改；②回归测试必须**同一份数据喂两个 builder 断言同值**（见 test_rsi_live_backtest_consistent），单侧测试无效；③新增指标字段时优先抽共享常量（窗口长度+最小样本数）而非复制字面量；④verify_indicator_math.py 已接入回归，跑它就是跑"live↔权威参考↔回测"三角。

### Metadata
- Source: self_discovery
- Related Files: src/data/akshare_client.py, src/data/data_feeder.py, tests/core/test_indicator_math_regression.py
- Tags: dual-builder-drift, live-backtest-asymmetry, regression-test-design
- See Also: LRN-20260829-002
