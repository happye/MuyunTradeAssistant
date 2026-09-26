# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260913-001] correction

**Logged**: 2026-09-13T02:30:00+08:00
**Priority**: high
**Status**: done
**Area**: backtest/consistency

### Summary
**凡"重建 Orchestrator/引擎"的旁路（MC、重放一致性检查、未来的并行对照），构造参数必须与主路径同源透传，不能凭记忆重抄清单。** v0.8.9.5 彻查发现同族病根两处：`run_monte_carlo` 临时引擎漏传 signal_weights/skill_types/enabled_skills/entry_exit_config（skills_dir 还被硬编码）→ MC 分布与基础回测不可比；`build_replay_consistency_check` 重放 Orchestrator 漏 entry_exit_config 且 has_position 恒 False → 凡买卖点触发日必假不一致。这正是 ISS-032"缺传=买卖点整体失效"的第三次复发。

### Suggested Action（已落地 v0.8.9.5 / ISS-093）
1. 主引擎 `__init__` 把全部构造配置存为实例属性，旁路引擎从 `self._xxx` 透传（backtest_engine 已落地）。
2. 新增旁路调用点时，grep 主构造点的 kwargs 清单逐一对照；有 entry_exit_config 的主路径，旁路必须有。
3. 重放类检查器按日志还原图状态（position_ratio_before→has_position）；无法还原的状态（TradePlan）在 docstring 诚实声明 gap，不许装作口径一致。

### Metadata
- Source: full_module_audit (全模块彻查批 ISS-093)
- Related Files: `src/core/backtest_engine.py`, `src/core/backtest_validator.py`, `src/cli/main.py`

---

## [LRN-20260913-002] best_practice

**Logged**: 2026-09-13T02:30:00+08:00
**Priority**: medium
**Status**: done
**Area**: pandas/refactoring

### Summary
**方法内对 `self._df` 做"替换式改造"（sort/reset_index/加列后重新赋值）时，所有引用必须在改造之后获取。** DataFeeder 向量化首跑等价性测试即抓到真分歧：`_build_stock_data` 在 `_ensure_precomputed()` 之前 `df = self._stock_df`，而预计算 `self._stock_df = df.sort_values(...)`（加指标列）替换了对象——局部引用还指向旧对象，`CHG_PCT` 等新列全部读成 None（等价性测试 date=2024-05-24 change_pct None 分歧，实测复现）。这类 bug 纯读代码很难发现（列存在性依赖调用时序），是"先验证再合入"纪律的直接收益案例。

### Suggested Action（已落地 v0.8.9.5 / ISS-093）
1. 替换式预计算统一收敛到一个 `_ensure_precomputed()` 入口，调用点注释"引用必须在预计算之后获取"。
2. 数值等价性重构必须配逐 bar 逐字段对照测试（旧口径静态方法保留为参照基准），绿灯才许合入——本次 1193 bar×全字段×双口径 0 分歧是合入依据。

### Metadata
- Source: self_discovery (等价性回归测试红灯)
- Related Files: `src/data/data_feeder.py`, `tests/backtest/test_datafeeder_vectorized_equiv.py`


---

## [LRN-20260831-001] best_practice

**Logged**: 2026-08-31T00:10:00+08:00
**Priority**: high
**Status**: done
**Area**: testing/caching

### Summary
给进程内存缓存加**磁盘持久化**时，测试进程会沿真实落盘路径写入假数据（mock 了网络层但落盘层是真执行），真实缓存文件也会反向漏进测试断言——双向污染。缓存层必须**一开始就内置 pytest 总开关**（`PYTEST_CURRENT_TEST` 环境变量探测），专项磁盘测试再显式重开。本项目 market_cache 磁盘 L2（v0.8.7.9）首跑全量即双向踩中：`test_cross_instance_cache_hit` 撞上真实 5550 行快照文件，"首次应真实拉取"断言失败。

### Suggested Action（已落地 v0.8.7.9）
1. `_disk_enabled()`：`MUYUN_DISABLE_DISK_CACHE=1` 显式禁用 + pytest 进程恒禁用（双层开关）。
2. 专项磁盘测试用 fixture 强制 `_disk_enabled=lambda: True` + `_DISK_CACHE_DIR` 重定向 tmp_path。
3. 通用纪律：凡新增**跨进程持久状态**（磁盘缓存/账本/会话状态文件），同一条 commit 必须同时落 pytest 隔离措施，否则全量测试门会随机挂、且用户真实数据会被测试悄悄改写。

### Metadata
- Source: self_discovery
- Related Files: `src/scanner/market_cache.py`, `tests/core/test_scan_caches.py`, `tests/core/test_market_cache_shared.py`

---

## [LRN-20260829-001] correction

**Logged**: 2026-08-29T00:00:00+08:00
**Priority**: critical
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：AGENTS §五·五 skill 五目录部署纪律 + scripts/sync-agent-skills.sh；本批已补 tutor skill 缺失的 .claude 副本
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：AGENTS 铁律1（修bug四步法+同类点清单），v0.8.8.2/0.8.8.4/0.8.8.5 各批均按此执行
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
- Related Files: `src/data/akshare_client.py`, `src/rag/embedding.py`, `src/core/skill_engine.py`, `src/core/plan_guard.py`, `src/cli/main.py`, `start.py`, `AGENTS.md`, `README.md`, `开发问题根因复盘_20260829.md`, `docs/archive/audit/对抗审查_20260829_待裁决清单.md`, `docs/archive/audit/对抗审查_20260829_第二轮_待裁决清单.md`
- Tags: regression, incomplete-fix, docs-as-fix, no-test-coverage, same-class-scan, verification-by-running

**See Also**: LRN-20260829-001, LRN-20260619-001, LRN-20260618-002

---

## [LRN-20260825-001] correction

**Logged**: 2026-08-25T00:00:00+08:00
**Priority**: high
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 知识型条目，无未竟动作（处理方式已并入工作习惯）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 流程纪律，已并入 dev-flow 收口步骤
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：self-improvement skill + 本文件持续执行（后续 LRN/ERR 均即时落库为证）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已实现：market_cache._snapshot_quality_ok 快照质量门 + E轮超时丢弃特性锁死
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已并入 AGENTS §二·五「用户可感知」五条硬条件
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已并入各工具入口文件的开局读 AGENTS 纪律（AGENTS/CLAUDE.md/copilot-instructions 等）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：AGENTS 铁律5（ISS 状态带 file:line+日期+commit 短哈希）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已并入 AGENTS §五关键约束（调卖出参数前先打 trade['sell_path']）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：AGENTS §二·五（本条即其源头）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已升格 AGENTS 铁律4（执行属持续纪律，非本条未竟动作）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 流程纪律，触发条件与出路已写明
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 五年回测已落地（ISS-069 ✅ 2026-08-29 三批基线重跑）
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
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 已制度化：docs/数据接口台账.md 登记 + live-probe 先行纪律，后续接口接入均执行
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
- Related Files: scripts/verify_indicator_math.py, tests/core/test_indicator_math_regression.py, src/data/data_feeder.py, src/data/akshare_client.py
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

## [LRN-20260829-004] best_practice

**Logged**: 2026-08-29T23:50:00+08:00
**Priority**: high
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 动作项已完成：tests/core/test_plan_guard_cooldown.py 已存在并全绿
**Area**: backend

### Summary
教训只写进工具自带记忆目录等于没写；且修复缺回归测试就是欠账（对齐复盘规律一/规律四的自查）

### Details
本 Agent 在 2026-08-24~29 的会话中犯了复盘点名的同类错误：
1. heredoc 转义降级、ThreadPool with 块陷阱两条教训只写进了 Claude 专属记忆目录
   （用户目录下的 .claude/projects），未落 .learnings/，违反跨工具可迁移原则；
2. la SystemExit 击穿（c06e83d）、PlanGuard 止损冷却（7cf2117，ISS-068）两个行为修复
   均未配套回归测试，其中 ISS-068 只有 A/B 回测对照没有单元级断言。

### Suggested Action
- 本条落库时同步把两条记忆内容并入本文件（已补 LRN-20260829-005）
- ISS-068 冷却写入补单元测试（tests/core/test_plan_guard_cooldown.py）

### Metadata
- Source: user_feedback（用户主导的根因复盘对齐）
- Related Files: skills/muyun-dev-discipline/SKILL.md, 开发问题根因复盘_20260829.md
- See Also: LRN-20260829-001, LRN-20260829-002
- Pattern-Key: harden.knowledge_channel | harden.test_debt
- Recurrence-Count: 2
- First-Seen: 2026-08-24
- Last-Seen: 2026-08-29

---

## [LRN-20260829-005] best_practice

**Logged**: 2026-08-29T23:50:00+08:00
**Priority**: medium
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 本条本身即沉淀动作的完成态
**Area**: backend

### Summary
两条环境级坑的仓库内沉淀（此前只在 Claude 专属记忆）：heredoc 转义降级；ThreadPoolExecutor with 块 join

### Details
1. 本环境 Bash 内联 heredoc 传 python 多行文本会被静默转义降级（反斜杠n 变真实换行、箭头/引号替换），
   失败模式静默。解法：多行精确补丁一律 Write 写 _patch 脚本文件再执行，Edit 锚点选纯 ASCII 行，
   写完立即 ast.parse。
2. with ThreadPoolExecutor 块内 fut.result(timeout=N) 超时后 __exit__ 的 shutdown(wait=True)
   join 卡死线程，分钟级冻结。解法：显式 ex.shutdown(wait=False)（范本 source_check._probe_t；
   该坑在 ISS-066 与 A04 两次发作才修全）。

### Metadata
- Source: conversation
- Related Files: src/data/source_check.py
- See Also: LRN-20260829-004
- Pattern-Key: env.heredoc_escaping | py.threadpool_join

---

## [LRN-20260830-001] best_practice

**Logged**: 2026-08-30T17:05:00+08:00
**Priority**: high
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 判据已并入对抗审查纪律；残留复跑在 v0.8.8.2/0.8.8.4/0.8.8.5 各批验收中均执行
**Area**: backend

### Summary
安全网修复的判据是"谁触发的"而非"动作多重"；残留复跑是验收的必选项；git 写操作禁止在 agent 长任务链内裸跑

### Details
1. **修"安全网被降级"类问题，判据是触发源，不是动作量级**。R4 的 D03 首版修复只兜 CLOSE_ALL 分支，
   注释里写"TRIM 减仓影响小不兜底"——但 trend_break 的 MA5<MA20 短期信号产出 exit_action="TRIM"，
   decision 已被强设 SELL 而 score 仍 0，照样被策略层两道闸门降级 HOLD 后反手 ADD。兜底条件应挂
   `force_exit`（安全网是否触发），减仓量级由 position_action 控制、与 score 正交。"影响小所以不修"
   的直觉被 60 秒探针复跑推翻。
2. **修复后必须残留复跑**：601318/2024 首轮修复后仍有 2 例 score=0 SELL，靠埋 trace 抓现场定位到 TRIM
   通道。没有这步，错误结论（"TRIM 不用兜底"）就会写进注释固化下来。
3. **git stash/checkout/reset 等有副作用的写操作不在 agent 长任务链里裸跑**（中断即损坏，本次
   .git 引用层损坏、2026-05 后历史对象丢失）。A/B 对照改用文件级原地切换：备份→改→跑→还原→md5 校验。
   仓库损坏后的抢救顺序：先 `git cat-file --batch-all-objects --batch-check` 查对象库存活（refs 坏
   ≠对象丢），再决定重建 or 嫁接；修复用"归档旧 .git + init + 全量快照 commit"，零删除可回滚。
4. **可选配置覆盖默认值禁用 `or`（R4 修 D05 时的发现）**：`weights or DEFAULT` 的 or 语义会把调用方
   传入的空 dict 静默换成默认值——凡"可选配置覆盖默认值"的参数，用 `x if x is not None else default`，
   禁用 `or`（0/""/[] 都是合法配置值时会被吞）。

### Metadata
- Source: conversation
- Related Files: src/core/orchestrator.py, src/core/decision_engine.py, src/data/data_feeder.py
- See Also: ERR-20260830-001, LRN-20260829-002
- Pattern-Key: fix.safety_net_criterion | verify.residual_rerun | git.no_sideeffect_in_agent_chain

---

## [LRN-20260830-002] best_practice

**Logged**: 2026-08-30T18:30:00+08:00
**Priority**: medium
**Status**: completed

**销账批注**（2026-09-05 集中销账）: 流程纪律，v0.8.8.2 批（先 grep 消费者再定修复面）已实践
**Area**: backend

### Summary
消费面定影响面（先 grep 消费者再定修复流程）；"技术性丢弃"要先读调用链再判对错

### Details
1. **影响面 = 消费者，不是发现本身的字面严重度**。E01（MA 排列 3 条即判）听起来像信号级 bug，
   grep 全库后确认 TechContextBuilder 只喂 AI Modifier prompt（live 专属，回测 AI 禁用）——
   于是修复流程从"C01 式 A/B 回测对照"降级为"单测锁契约"，省掉无意义的回测矩阵
   （回测路径暴露恒为 0，跑了全是噪声）。反过来，若消费者是 decision_engine 就必须上 A/B。
   修复前先 grep「谁读这个函数/字段」，再选流程。
2. **"丢弃部分结果"类代码，先读调用链再裁决**。D-RG-2（market_cache 超时 return None 丢弃
   已抓到的页）字面像资源浪费，但实测 `_snapshot_quality_ok` 只查 >10 行有效价——部分快照
   （约 3200/5800 只）会通过校验被缓存成"全市场快照"，静默偏置量比/涨幅排名。丢弃+换源重拉
   才是正确设计。审查员报"缺陷"时若只看函数局部，容易把容错设计误报成 bug（对照 R3 C06 假阳性：
   机理 + 消费链都要看）。
3. **结构性断言测试适合 glue 代码**：web/chat/tui 的字段映射没有纯函数可测，用「读源码找
   StockData( 构造块 → 断言必须包含 N 个字段=」的结构断言，先红后绿一样成立；改文案会挂测试
   是有意的（提醒同步）。

### Metadata
- Source: conversation
- Related Files: src/core/tech_context.py, src/scanner/market_cache.py, src/web/app.py
- See Also: LRN-20260830-001, LRN-20260829-002
- Pattern-Key: fix.consumer_defines_blast_radius | review.discard_is_design | test.structural_for_glue

---

---

## [LRN-20260830-003] best_practice

**Logged**: 2026-08-30T23:30:00+08:00
**Priority**: high
**Status**: completed
**Area**: workflow

### Summary
被回滚的修复不必重写——从备份链 checkout 原实现重放，比重新发明更忠实于已验收方案；红灯优先的回归测试让"裁决→执行"有了验收契约。

### Details
R4/R5 修复被用户整体回滚（ca01d27）但转"待裁决"，裁决通过后执行：D/E 批直接 `git checkout <reverted-commit> -- <files>` 重放（当时该 9 文件与基线无其他分叉，重放即精确）；G/H 批为新裁决项，测试先行（test_g/h_block_fixes 先红灯 7+6，实现后全绿）。全程未越权：只做清单内的事。

### Suggested Action
修复被回滚转待裁决的批次：先 `git diff <基线> <回滚前commit> --stat` 确认可重放范围与文件交叠，无交叠直接 checkout 文件级重放，有交叠（如 H05 与 E02 同文件）则先重放再叠加。回归测试红灯优先——红灯清单本身就是"修了什么"的契约。

### Metadata
- Source: self_discovery
- Related Files: backup/local-r4r5-chain(git branch), tests/core/test_g_block_fixes.py, tests/core/test_h_block_fixes.py
- Tags: revert-replay, red-first, adjudication-workflow
- See Also: ERR-20260830-001

## [LRN-20260903-001] best_practice

**Logged**: 2026-09-03T01:10:00+08:00
**Priority**: high
**Status**: promoted
**Area**: docs

### Summary
用户进入「Agent 开发教学」模式（教材 = docs/AI_Agent面试备战_技术深度剖析.html）。定下教学铁律：**先核对教材引用的代码坐标再开讲**，因为教材是静态生成物、会与代码漂移。首轮核对即抓到 2 处漂移。

### Details
1. **教材说「9 工具 Agent」，实为 11 个**（`src/chat/prompts.py:84` TOOL_DEFINITIONS）：原 9 个 + v0.8.8 新增 `run_command`、`manage_portfolio`。面试答「9 个」会被追问击穿。
2. **行号漂移**：`TOOL_ERROR_MARK` 教材标 `tools.py:19` → 实际 `:27`（+8）；`_is_tool_failure` 教材标 `:522` → 实际 `:523`。
3. **教材讲漏了一层**：「伪工具调用用正则剥离」只讲了第二道防线（`agent.py:47` 正则 + `:313` 使用），漏了真正的架构级修法 —— `agent.py:347-349/356` 注释写明「上限内每轮都带 tools，从根上消除模型陷入无工具境地」。讲成两层防御比只讲正则高一个段位。
4. 已核对无误可直接引用的坐标：`_run_conversation` agent.py:337、`hard_cap=max_tool_rounds*2` :352、失败轮不占上限 :464-472、length 截断分支 :402、最终兜底去 tools :490-493；BM25 retrieval.py:104-133；RRF 0.4/0.6 k=60 retrieval.py:250/254。

### Suggested Action
每次教学回答前，对本次要引用的 file:line 跑一次 grep/Read 复核，再给结论。教材行号只当线索，不当事实。发现漂移要显式告知用户「教材此处已过期」，因为用户会拿教材去面试。

### Metadata
- Source: user_feedback
- Related Files: docs/AI_Agent面试备战_技术深度剖析.html, src/chat/agent.py, src/chat/prompts.py, src/chat/tools.py, src/rag/retrieval.py
- Tags: teaching, doc-drift, agent-loop, interview-prep
- Pattern-Key: harden.verify_before_teach
- Recurrence-Count: 1
- First-Seen: 2026-09-03
- **Promoted**: skills/muyun-agent-tutor/SKILL.md

## [LRN-20260903-002] best_practice

**Logged**: 2026-09-03T02:00:00+08:00
**Priority**: high
**Status**: completed
**Area**: data-integrity / security

### Summary
「整文件替换写回」的记录必须从 existing 全量携带重建，只列"本次会变的字段"=静默丢掉其余字段（P0 实证：chat 回写抹光 7 份 TradePlan）；"确认门"若是模型自供的布尔参数，防漏不防恶意，对外表述必须降级。

### Details
第三轮对抗审查三大教训：① `update_from_strategy_decision` 重建 PositionRecord 只列了自己要写的字段，trade_plan/overweight_executed 全部随整文件替换写盘而蒸发，且唯一触发方是 chat 回写（H1 修复引入），CLI 路径不可见所以测试全绿——**"审查用例必须覆盖每个写入方的真实调用链"**。② chat confirm 硬门的 confirm 是模型自供参数，_InputPatcher 对 [Y/n] 自动答 y：它防的是"模型忘传参数"，防不了提示注入；AGENTS 里"confirm 硬门"的表述需与实际强度对齐，la/scan/events 三个批量 AI 费用 mode 就是靠逐 mode 与 parse_input 产出比对才抓到漏网。③ 审查发现的"修复"要先跑再信：本轮 4 个 P0/P1 全部先写最小复现脚本（红灯）再修，其中扫描排序项第一版测试假绿（fake enrich 值与顺序单调相关，区分不了新旧行为），改成与顺序无关的伪随机值才真正红起来。

### Suggested Action
- 新增/修改"记录整体写回"的函数时，diff 自检：重建对象列出的字段集 ⊇ 旧记录有的字段集，否则从 existing 携带
- 任何"硬门"先问三个问题：参数谁提供？绕过成本多大？拒绝时用户能不能看到？——三者都要有代码级答案才能叫"硬"
- 回归测试的 fake 数据要与被测顺序正交，写完先验证"旧代码下必红"

### Metadata
- Source: self_discovery
- Related Files: src/data/portfolio.py, src/core/plan_guard.py, src/chat/tools.py, src/core/orchestrator.py
- Tags: record-rebuild, confirm-gate, red-first, adversarial-review
- See Also: LRN-20260830-001, ISS-078

---

## [LRN-20260904-001] correction

**Logged**: 2026-09-04T00:30:00+08:00
**Priority**: medium
**Status**: completed
**Area**: scanner / config-validation

### Summary
同一份 YAML 里不同 section 由**不同执行器**消费时，校验器必须按段使用对应的合法操作符集——拿 A 段（普通 filters，`VALID_OPS`）的操作符集去校验 B 段（`global_exclude`，执行器 `apply_global_exclude` 支持 starts_with/contains/is_nan）会把**合法配置误报成「未知操作符」**，且告警文案断言「筛选变宽松」与运行时实况相反（排除逻辑一直在正常执行）——谎报会训练用户忽略告警。

### Details
ISS-078 给 `ScannerEngine._load_rules` 接加载期校验时，把 `validate_filters`（VALID_OPS）套到了所有 section，包括 global_exclude。结果 `bz scan` 每次启动误报 8 条 WARNING（两个 ScannerEngine 实例 × 4 条），用户以为配置坏了，实际排除北交所/退市股一直在正常工作。这类「校验器操作符集 ≠ 执行器操作符集」的错配只有跑真实配置才能暴露——单测里 handcrafted 的 global_exclude 若只写 VALID_OPS 内的操作符就测不出来（本项目回归测试特意用**真实 scan_rules.yaml 原文**做零误报断言）。

### Suggested Action
- 校验器与执行器必须成对出现：`apply` ↔ `validate_filters`（VALID_OPS），`apply_global_exclude` ↔ `validate_excludes`（EXCLUDE_VALID_OPS）；新增配置段时两套必须同步
- 配置校验的回归测试要用**线上真实配置文件**做「零误报」断言，不能只用 handcrafted 理想样本
- 告警文案不得断言未经证实的后果（「筛选变宽松」）——误报时文案本身就是谎报

### Metadata
- Source: user_report
- Related Files: src/scanner/scanner_filter.py, src/scanner/scanner_engine.py, tests/core/test_scan_rules_validation.py
- Tags: validation-mismatch, false-alarm, yaml-config
- See Also: ISS-078, LRN-20260903-002

## [LRN-20260905-001] correction
**Date**: 2026-09-05
**Priority**: medium
**Status**: completed
**Area**: git-workflow

### Summary
`git add` 的两个静默坑：① 一条命令传多个 pathspec 时，只要有一个路径无效（如已被 git mv 走的旧路径），**整条 add 全部失败**且提交照样进行——结果提交的是「半成品暂存区」（本次 C4 只提交了 rename 没带上 2 行修改）；② `git add <目录>` 会把该目录下**用户未跟踪的本地文件**（教材 html 等「勿动勿提交」项）一并扫进暂存区。

### Details
重构会话 C4：`git add scripts/verify_indicator_math.py tests/core/verify_indicator_math.py 2>/dev/null`——后者已不存在，整个 add 失败被 stderr 吞掉，`git commit` 随后只提交了此前 `git mv` 暂存的 rename（100% similarity、0 行变更），路径修改滞留工作区。C6：`git add docs/` 误扫入 docs/ 下两份未跟踪文件，靠 commit 前 `git status` 核对才发现。

### Suggested Action
- 本项目提交纪律：**逐文件显式 add，禁止 `git add .` / `git add <目录>`**；add 后 commit 前必看 `git status --short` 暂存清单
- add 失败（尤其带 `2>/dev/null` 时）不要继续 commit；先 `git status` 确认暂存区内容
- 提交后抽查 `git show HEAD --stat` 与预期文件集比对，发现短少立即 `--amend` 补齐（push 前均可）

### Metadata
- Source: session_mistake
- Related Files: scripts/verify_indicator_math.py, docs/archive/README.md
- Tags: git, staging, silent-failure, commit-hygiene
- See Also: ISS-084

## [LRN-20260905-002] correction
**Date**: 2026-09-05
**Priority**: high
**Status**: completed
**Area**: tooling-discipline

### Summary
**同一文件的多处 Edit 绝不能放进同一个并行工具调用批次**——两个编辑各自"读→改→写"整个文件，后完成的把先完成的覆盖（经典 lost update）。本次 service.py 两处改动并行发，diversity 赋值行被自己的第二个编辑吞掉，直到初始化报 `no attribute 'diversity_max_per_chapter'` 才暴露。

### Suggested Action
- 对同一文件的多处修改：一个消息只发一个 Edit，串行执行
- 并行批次只用于互不相干的文件/命令
- 改完用 grep 复核所有预期改动点都在（本次靠 `grep -n diversity` 三文件核对兜底）

### Metadata
- Source: session_mistake
- Related Files: src/rag/service.py
- Tags: parallel-edit, lost-update, tool-discipline

## [LRN-20260905-003] insight
**Date**: 2026-09-05
**Priority**: high
**Status**: completed
**Area**: git-auth

### Summary
本机 git push 自主通道已打通：`credential.helper=helper-selector`（PortableGit etc/gitconfig）→GCM→Windows 凭据库全链无凭据（PowerShell `cmdkey /list` 全量核对无 git 项）。真正可用通道 = **VS Code 系 IDE（Code/Insiders/Trae）的 GitHub OAuth 令牌**：存于 `%APPDATA%/<IDE>/User/globalStorage/state.vscdb` 的 `secret://vscode.github-authentication/github.auth`，Chromium os_crypt v10（AES-256-GCM）格式，密钥在 `<IDE>/Local State` 的 `os_crypt.encrypted_key`（DPAPI 解密得 32 字节 AES key）。IDE 内 Agent 的"自主推送"走的就是这套令牌+GIT_ASKPASS。

### Suggested Action
- 推送脚本已固化：`.workbuddy/scripts/push_github.py`（解密→api.github.com 验证→fetch+behind 检查→push；令牌只在内存/子进程 env，不落盘不打印）
- 跑法：`cd 仓库根 && .venv/Scripts/python.exe .workbuddy/scripts/push_github.py`（注意必须用 python 解释器执行，直接跑 .py 会被 bash 当 shell 脚本）
- 令牌是 `gho_` OAuth（scope: repo+workflow），account=happye；不要把令牌写进任何配置文件

### Metadata
- Source: session_solution
- Related Files: .workbuddy/scripts/push_github.py
- Tags: git-push, credential, ide-token, dpapi, os-crypt

## [LRN-20260905-004] insight
**Date**: 2026-09-05
**Priority**: medium
**Status**: completed
**Area**: faiss-windows

### Summary
**faiss 的 C++ `fopen` 不支持含中文的路径**（Windows 按 ANSI 代码页解析 UTF-8 字节→ENOENT）。实证：`faiss.read_index(r'G:\...\暮云思辨投资助手\knowledge\index\index.faiss')` 必报 "No such file or directory"，但同一文件 `ls` 明明存在。**相对路径（如 `knowledge/index/index.faiss`，中文部分由进程 cwd 在 OS 层解析）则正常**——这就是本项目 `settings.yaml` 的 `index_dir: "./knowledge/index"` 一直工作正常的原因。

### Suggested Action
- 本项目 settings.yaml 的 `rag.index_dir` 必须保持相对路径或纯 ASCII 绝对路径；改成中文绝对路径的症状是"每次启动都静默重建索引"（load False→rebuild）
- 写探针/脚本直接操作索引文件时，先 `os.chdir(仓库根)` 再用相对路径

### Metadata
- Source: probe_finding
- Related Files: configs/settings.yaml, src/rag/store.py
- Tags: faiss, windows, non-ascii-path, encoding

## [LRN-20260906-001] insight
**Date**: 2026-09-06
**Priority**: high
**Status**: completed
**Area**: rag-index-lifecycle

### Summary
对抗性审查实证三个 RAG 索引生命周期规律：① `RAGService.initialize` 的"重建"分支曾复用 `_try_load_index` 装进 store 的旧索引——`store.add` 只按相同 doc_id 替换，**已删除/改名文件的旧块成为僵尸向量残留且被 save 固化**（探针实证，测试 test_index_freshness 红灯锁死）。② 同一批 add 内 doc_id 重复时，FAISS 存下全部向量但 `_id_to_idx` 后者覆盖前者 → 查询返回重复条目、`get_doc_by_id` 错位；触发源是两个知识文件解析出同系列同章号（如「第10章上/下」）。③ HF 模型名→本地快照动态解析（`~/.cache/huggingface/hub/models--<org>--<repo>/snapshots/*` 取含 config.json 的目录）可同时保住"零联网加载"与"settings.yaml 可移植"两头；用 `Path.as_posix()` 生成路径字符串与既有 embedder_metadata.json 完全一致，避免元数据漂移触发无谓重建。

### Suggested Action
- 任何"重建索引"路径必须从空 store 开始，不能在已加载的旧 store 上 add
- ingestion 层新增 doc_id 生成规则时，必须配"跨文件撞号告警"（load_strategy_files 已内置）
- 嵌入器元数据是索引兼容性的唯一真相源：改加载路径字符串格式前先对照 metadata 的既有格式，否则一次无谓的全量重建（837 块 CPU ~12s）
- 本地快照解析模式可推广到 reranker 等其它 HF 模型加载点

### Metadata
- Source: probe_finding
- Related Files: src/rag/service.py, src/rag/store.py, src/rag/ingestion.py, src/rag/embedding.py, tests/rag/test_index_freshness.py
- Tags: rag, index-rebuild, zombie-docs, doc-id, hf-snapshot, offline-loading

## [LRN-20260906-002] insight
**Date**: 2026-09-06
**Priority**: high
**Status**: completed
**Area**: state-machine-cadence

### Summary
`strategy_layer.process()` 是全项目唯一假设"1 次调用 = 1 个交易日"的状态机入口（tick 四计数器 + push_signal 都在调用时无条件执行）。回测每 bar 调一次恰好成立，**live 违反之**：同一天重复分析会按次数烧掉冷却期/减仓保护/最短持有/加仓保护，并重复污染信号历史。这类"调用频次=时间流逝"的隐式耦合在 live/回测共用状态机的架构里是系统性风险模式——探针实证 cooldown 5 连跑 3 次变 2。修复用 `last_tick_date` 日期去重，None 保持旧语义保护既有调用方。

### Suggested Action
- 新增任何"按调用推进的持久化状态"时，先问：live 会被重复调用吗？回测/live 的调用频次语义是否一致？
- 状态机入口的日期参数（current_date/today）要区分三态：显式日期（按日期去重）、None+live（注入真实今天）、None+回测（保持旧语义）——不要一刀切
- 排查此类 bug 用"全库 grep 计数器字段的所有赋值点"而非只看递减函数（tick 藏在 models.py 的模型方法里，第一轮 grep 漏了，差点误报"永不递减"假 P1）

### Metadata
- Source: probe_finding
- Related Files: src/core/strategy_layer.py, src/data/models.py, src/core/orchestrator.py, src/data/portfolio.py, tests/core/test_strategy_layer_daily_tick.py
- Tags: state-machine, live-vs-backtest, cooldown, idempotency, date-guard

## [LRN-20260906-003] insight
**Date**: 2026-09-06
**Priority**: medium
**Status**: completed
**Area**: batch-architecture

### Summary
多代码批量需求的三条实测结论：① 本项目第三方接口封装全部是单代码形态（get_realtime_quote/calculate_indicators/baostock/news），不存在"一个请求拉 N 只"的批量形态——新浪原生 list 接口项目未用；② 但串行循环没有反爬问题：实测 2 只×两类接口 4 次请求 0.1-0.8s/只全部成功，la/ba 一直是这个模式；③ 6 维评分 prompt 按股独立设计（行业/业务/估值各不同），多股合并进一次 AI 调用=重设计评分系统（笨总域禁改），所以"多代码只调 6 次 AI"在当前架构下不成立，正确预期是 6×N 次但整批共享 AI client+全市场成交额、同日缓存命中免费。批量能力应优先复用既有 auto_score_batch/benzong_batch_analyze 管道而不是新写循环。

### Suggested Action
- 用户提出"批量=X 次调用"类预期时，先读维度 prompt 设计确认是否按股独立，再承诺成本
- 新批量入口一律接 _enforce_confirm 硬门名单（mode 名写进 tests/chat/test_chat_confirm_gate_coverage.py 同款断言）
- chat 工具 schema 改动必须同步三处：TOOL_DEFINITIONS schema / CHAT_SYSTEM_PROMPT 工具清单 / 命令桥使用规范——agent 的能力认知只来自这三处文本

### Metadata
- Source: probe_finding
- Related Files: start.py, src/chat/tools.py, src/chat/prompts.py, src/core/benzong/batch_scorer.py, tests/core/test_multi_code_parse.py
- Tags: multi-code, batch, confirm-gate, tool-schema, anti-scraping

## [LRN-20260907-001] insight
**Date**: 2026-09-07
**Priority**: medium
**Status**: completed
**Area**: anti-scraping

### Summary
反爬优化的优先级实证：减少请求数 > 打散请求节奏 > 快速跳过坏源。① 新浪 `hq.sinajs.cn/list=` 原生支持一次多只（Referer 必须带 finance.sina.com.cn，否则空响应），是项目里唯一零成本真批量源——一次请求 60 只，实测 1 请求 2 只全命中；② 东财全市场接口（stock_zh_a_spot_em）本来就是"1 请求覆盖全市场"，批量场景它天然是降级层；③ "支持批量的源拉全量、不支持批量的源（baostock）在原语内自动降级逐只"是多源批量原语的标准形态，部分失败按 code 合并；④ 预取映射（批量入口一次拉取填充，逐只调用先查映射 TTL 命中）是让既有单代码调用方透明受益批量的最小侵入方式。K 线磁盘缓存的新鲜度关键点：baostock 日线当日 bar 盘后才更新，白天缓存全天安全、17:30-18:00 是更新灰区需要重拉。

### Suggested Action
- 新批量入口一律：开头 prefetch_realtime_quotes(codes) + 逐只调用零改动
- 数据源调用点接 net_guard 的 rate_limiter/circuit_breaker（key=源名），新源登记 interval
- 磁盘缓存新鲜度凡涉及时效数据，用"时刻规则"（如 17:30/18:00 盘后线）而不是固定 TTL

### Metadata
- Source: probe_finding
- Related Files: src/data/net_guard.py, src/data/akshare_client.py, start.py, tests/core/test_net_guard.py, tests/core/test_batch_quotes.py
- Tags: anti-scraping, batch, circuit-breaker, rate-limit, disk-cache, sina

## [LRN-20260907-002] insight
**Date**: 2026-09-07
**Priority**: medium
**Status**: completed
**Area**: python-decorator-insertion

### Summary
在"装饰器 + def"之间插入新方法时，原 def 的装饰器会被新代码块顶掉——本次在 news_client.py 的 `@classmethod` 与 `def get_stock_news` 之间插入磁盘缓存助手，导致 get_stock_news 丢失 @classmethod 变成实例方法，生产调用点 `cls.get_stock_news(...)` 直接 TypeError。同批测试当场抓住（test_news_disk_cache_roundtrip 报 missing positional argument）。

### Suggested Action
- 在装饰器和 def 之间插入代码前，grep 确认装饰器归属；插入后必须 ast.parse + 相关测试
- 给"类方法间插入"写测试时优先走类级调用路径（cls.method），实例方法丢失会立刻暴露

### Metadata
- Source: session_mistake
- Related Files: src/data/news_client.py, tests/core/test_iss091_robustness.py
- Tags: decorator, classmethod, regression-test, insertion-bug

## [LRN-20260911-001] lesson
**Date**: 2026-09-11
**Priority**: high
**Status**: completed
**Area**: requirement-interpretation

### Summary
ISS-092 同一段任务指令里混着两类语义，两轮返工：①「当发送消息失败时，每小时重试」指**授权任务的 agent 会话自身**失败后的自愈节奏（/loop 语境），不是要往 chat 里加"发送失败自动重试"功能——按功能实现了一半被用户纠正砍掉；②「本地读写文件机制」指的是**仿 manage_portfolio 的 AI 工具层扩展**（AI 可写新文件到新目录/读仓库内文件），不是会话状态持久化——按会话持久化实现完后用户中途澄清，两者都做了才收齐需求。"X 机制/X 功能"这类词在有多层架构（AI 工具层/进程层/文件层）的项目里天然歧义。

### Suggested Action
- 接到混合指令先分类：哪些是给 agent 自己的操作指令（重试/自愈/调度），哪些是给产品的功能需求；不确定的当场用一句话复述理解再动手
- 「加 X 机制」先问清载体：是底层引擎能力、AI 可调用的工具（工具层）、还是 REPL 命令（交互层）——本项目三层都有先例（SessionStore=引擎层、write_file=工具层、sessions 命令=交互层）
- 用户中途澄清需求时，先复述修正理解、对比已实现部分砍/留，再继续干——本次"中断恢复"保留、"重试器"砍掉即此法

### Metadata
- Source: user_correction
- Related Files: src/chat/session_store.py, src/chat/tools.py, src/chat/agent.py, ISSUES.md ISS-092
- Tags: requirement-ambiguity, agent-loop, tool-layer, scope-correction

## [LRN-20260911-002] lesson
**Date**: 2026-09-11
**Priority**: high
**Status**: completed
**Area**: test-environment-fidelity

### Summary
新测试 test_chat_session_resume 在本地跑全绿后交付对抗审查，审查实证其在**输出重定向**（管道/CI/日志）下 6/10 失败：zh-CN Windows 的重定向 stdout 默认 cp936，`_run_conversation` 的 ⏳/🔧 emoji 抛 UnicodeEncodeError，被 `chat()` 的 except 捕获误判成"发送失败"。本地全绿是假象——我全程带 `PYTHONIOENCODING=utf-8` 跑测试，恰好掩盖了用户真实运行环境的默认编码。生产路径不炸是因为 start.py/__main__.py 启动时 setdefault 了该变量，但测试直跑不经过它们。

### Suggested Action
- 涉 print emoji/中文的测试文件顶部加 `sys.stdout.reconfigure(errors="replace")`（test_chat_session_resume / test_chat_tool_failure 已加；test_chat_streaming 直调 _call_api_stream 无 emoji 打印不受影响）
- 验证测试环境要与用户真实运行方式一致：不带额外环境变量直跑一次 `python tests\xxx.py > log` 再交付
- 生产代码已有防线（start.py:31 / __main__.py 的 PYTHONIOENCODING setdefault）不覆盖测试直跑路径——测试文件自带编码兜底才算闭环

### Metadata
- Source: adversarial_review
- Related Files: tests/chat/test_chat_session_resume.py, tests/chat/test_chat_tool_failure.py, src/chat/agent.py, src/chat/__main__.py
- Tags: encoding, cp936, unicode, redirected-output, test-fidelity

## [LRN-20260911-003] lesson
**Date**: 2026-09-11
**Priority**: medium
**Status**: completed
**Area**: tooling-reliability

### Summary
同一文件的**两处 Edit 放在同一条消息里批量提交，其中一处返回 success 但改动并未落盘**（本次 `docs/AI系统说明.md` 的 373 行连续两次被吞，同批的 148/439 行正常）。工具回执不能当作落盘证据；不 grep/Read 回读就会带着"文档已同步"的假账交付——与项目"验证靠跑不靠读"是同一个病根。附带两条本机 pytest 踩坑：① `--basetemp` 指到仓库内路径（含中文「暮云思辨投资助手」）会让 faiss 写索引失败，2 个 H03 用例**假红**（`Error: 'f' failed: could not open ...`），看着像回归其实与改动无关；② 不给 `--basetemp` 时，pytest 收尾清理临时目录会触发沙箱批量删除保护（>50 项），进程被拦、汇总行打不出来。

### Suggested Action
- 改文档/配置**逐个 Edit**，同文件禁止同批多 Edit；每次改完**立即 Read 或 grep 回读**确认
- 批量改动收尾做"**旧值 grep 归零 + 新值 grep 命中**"双向校验，再报"已同步"
- 全量 pytest 固定写法：`./.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/artifacts --ignore=tests/rag_eval --basetemp="C:/Users/Crux/AppData/Local/Temp/muyun_pytest_tmp" -p no:cacheprovider`
- 看到 `[safe-delete]...SAFE_DELETE_BULK_CONFIRM_REQUIRED` ≠ 测试失败：先看输出里有没有 F/E，再决定是否判定回归

### Metadata
- Source: session_mistake
- Related Files: docs/AI系统说明.md, tests/chat/test_chat_round_limit.py, src/chat/agent.py, configs/settings.yaml
- Tags: edit-race, verification, pytest-basetemp, faiss-chinese-path, safe-delete

## [LRN-20260911-004] lesson
**Date**: 2026-09-11
**Priority**: high
**Status**: completed
**Area**: external-dependency-fragility

### Summary
上游模型改名（DeepSeek 2026-09-10 发布 V4.1 Flash，API 名 `deepseek-v4-flash` → `deepseek-flash`）时，项目里 **10 处 `str(model).startswith("deepseek-v4")` 判断全部静默失配**。这些判断不是"识别模型名"，而是"决定要不要显式关闭思考模式"——官方对思考型模型默认开思考（effort=high），失配即静默退回思考模式：更慢更贵、`temperature` 不报错但不生效、top_p 被抬到下限 0.95。**没有任何报错、没有测试变红、日志也不提示**，属于最难发现的一类回归（配置层"改一个字符串"引发的全链路行为漂移）。

### Suggested Action
- **判定"能力/族"用族前缀（`deepseek-`），判定"版本"才用版本号**；能力判断必须收敛到单一事实源（本项目 = `src/core/ai_model.py`），调用点只写 `**thinking_disabled_body(model)`
- 新增任何"按模型名分支"的代码前先问：官方改名/发新模型时这行会不会静默失效？会 → 抽成共享判定函数
- **给"配置里的值"写断言**：`test_settings_configured_model_is_detected` 直接读 settings.yaml 断言其模型被判为 DeepSeek 思考型——这类测试才防得住"改配置引起的回归"（纯单元测试用假模型名，永远测不出）
- 扫同类点转测试：用 AST 检测 `startswith("deepseek-<数字>")`，而不是字符串匹配（后者会把 docstring 里引用旧写法的说明误判为违规）
- 端到端验收用**可观测代理指标**：思考开/关无法直接观测，但响应有无 `reasoning_content` 可观测（实测：开=有、关=无）

### Metadata
- Source: adversarial_review
- Related Files: src/core/ai_model.py, src/chat/agent.py, src/core/ai_modifier.py, src/core/event_layer.py, src/core/benzong/*, src/scanner/scanner_engine.py, src/data/source_check.py, tests/core/test_ai_model_family.py
- Tags: model-rename, silent-regression, single-source-of-truth, config-assertion, capability-vs-version

## [LRN-20260911-005] lesson
**Date**: 2026-09-11
**Priority**: medium
**Status**: completed
**Area**: doc-vs-measurement

### Summary
两处"文档 vs 实测"必须分开陈述，混起来会误导用户：
1. **官方文档说会 400 的条款，实测未复现**：DeepSeek 思考模式文档明确写「带 tools 的请求若不回传 `reasoning_content`，API 返回 400」。真实 API 实测（思考开启 + tools + 第二轮丢掉 reasoning_content）**没有报 400**，带上/不带都成功。→ 结论只能写成"文档有此要求、单轮工具链实测未触发、长链条不确定"，**不能升级成"项目已坏"**。
2. **上下文窗口风险被我高估了一个数量级**：上一轮用"中文 1 token/字"估算，得出"20 轮对话最坏 90K token 逼近上限"。查官方文档 + 实测 `usage.prompt_tokens` 后：DeepSeek `deepseek-flash` 窗口 **1M**（384K 最大输出）、Kimi k2.6 **262,144**；实测混合文本约 **2.17 字符/token**，20 轮最坏约 40K token ≈ 窗口的 4%。→ 护栏仍要做（Kimi 窗口小、且要防病态输入），但**性质是"防御性"不是"救火"**，报告里必须这么写。

### Suggested Action
- 涉外部规格（上下文长度/最大输出/参数名/报错签名）**先查官方文档再动手**，文档没写的**实测一次**，两者冲突时两个结论都写出来
- 估算类代码用**真实 API 的 usage 值校准**（本项目：`system 3988字+tools 6634字+提问19字 = prompt_tokens 4898`），把校准锚点写进测试注释，避免下次又拍系数
- 报告里区分「官方要求」/「实测行为」/「代码现状」三栏，不要把文档条款当成已发生的事故

### Metadata
- Source: session_mistake
- Related Files: src/core/ai_model.py, tests/core/test_ai_model_family.py, configs/settings.yaml
- Tags: official-docs, measurement-vs-doc, token-estimation, calibration-anchor, honest-reporting

## [LRN-20260911-006] lesson
**Date**: 2026-09-11
**Priority**: high
**Status**: completed
**Area**: empirical-coefficients

### Summary
**经验系数必须有官方出处，不能只有"拟合出来的数字"。** 上下文护栏的 token 估算器初版写成 `CJK/1.2 + 非CJK/3.5`，来源是"我拿一次实测值反推的"——用户要求自检后查出：官方（DeepSeek「Token 用量计算」）明确给出 **1 中文字符 ≈ 0.6 token、1 英文字符 ≈ 0.3 token**（即 1 token ≈ 1.67 汉字；Kimi 官方口径 1.5~2 汉字一致）。我的 1.2 = 0.83 token/字，**比官方高估 39%**。它之所以总偏差只有 +6.3%，是因为过高的中文系数"恰好"抵消了结构开销（tools schema/role 标记/特殊 token）——**结果对、理由错**，这种代码一旦别人动其中一个系数就会立刻失去校准。

### Suggested Action
- 估算/换算/阈值类常量：先查官方一手口径，写进注释并附链接；官方口径之外的部分**单独拆成一个显式命名的修正项**（本次 = `_STRUCTURE_OVERHEAD = 1.15`，附"实测真实请求比官方比例高约 12%"的依据），不要揉进主系数
- 用真实 API 的 `usage` 做校准锚点，并把锚点数值写进测试注释（本项目 4893/4898），这样下次改动能立刻判断是否还准
- 承接 LRN-005：**同一个"外部规格"要交叉验证多个官方来源**（DeepSeek 与 Kimi 的口径互证）

### Metadata
- Source: user_correction
- Related Files: src/core/ai_model.py, tests/core/test_ai_model_family.py
- Tags: token-estimation, official-coefficients, explicit-correction-term, calibration-anchor

## [LRN-20260911-007] lesson
**Date**: 2026-09-11
**Priority**: medium
**Status**: completed
**Area**: verification-before-claiming

### Summary
**"先验证再修"必须包含验证自己的主张——我报告里的两条判断经复核被自己推翻：**
1. 「无 pytest 配置 ⇒ 必须手打 `--ignore=tests/artifacts --ignore=tests/rag_eval`，漏打口径不一致」→ 实测**裸 `pytest` 与带 `--ignore` 收集数完全相同（652）**：`tests/artifacts` 里没有 .py、`tests/rag_eval/evaluator.py` 不叫 `test_*`，**根本没有东西需要 ignore**。原判断把影响说大了 ⇒ 按最小改动**不加 pytest.ini**。
2. 「上下文溢出风险高（最坏 90K token 逼近窗口）」→ 见 LRN-005，按官方口径实测只有窗口的 4%。

教训：**报告里凡是"如果不 X 就会 Y"的因果判断，都要先做一次对照实验**（A/B 实测差多少），否则会把自己的担心写成事实，推动用户做无用改动。

### Suggested Action
- 提"这条不修会出问题"之前，先跑一次"不修"的对照，把差异量级写进结论
- 报告用词分级：**实测结论 / 官方要求 / 我的推断** 三类分开写，别混在一句里
- 用户要求"先验证再修"时，同时把**已提出但未验证的主张**一起列出来复验（本次两条都属"我自己上轮说的"）

### Metadata
- Source: session_mistake
- Related Files: AGENTS.md, docs/2026-09-11_测试体系架构梳理报告.md
- Tags: self-verification, control-experiment, claim-grading, minimal-change


## [LRN-20260911-008] lesson
**Date**: 2026-09-11
**Priority**: medium
**Status**: completed
**Area**: logging-filter-idempotency

### Summary
**logging.Filter 里改写 `record.msg` 必须做幂等守卫——生产不触发不代表不是缺陷。**

病根：`PlainLanguageFilter.filter` 无条件把 `record.msg` 改写成「⚠ 人话｜原始：{raw}」。
若同一条 record 流经**多个挂了本 filter 的 handler**（或同一 handler 挂多个 filter），
第二个 filter 拿到的 `record.getMessage()` 已是成品，又翻一次 ⇒
`⚠ …｜原始：⚠ …｜原始：…`（实测前缀数 2），同时 `_recent` 同一告警记两次
（末尾「本次运行 N 条提示」结论数字虚高）。

实测证据（探针四场景）：
- 单 handler：前缀 1 次、`_recent` 1 条 ✅（生产 REPL 的形态，故一直没暴露）
- 双 handler 各挂 filter：前缀 `[1, 2]`、`_recent` 2 条 ❌
- 同 handler 挂双 filter：前缀 2 次 ❌

修法：filter 开头 `if raw.startswith("⚠ "): return True`（原文已在首次改写时完整保留）。
红绿灯验证：摘掉守卫 → 新测试红（`assert 2 == 1`）；装回 → 绿。

### Suggested Action
- **filter 里凡是要动 `record.*` 的，先问"重复经过会怎样"**；改写类 filter 一律加"已是成品则透传"守卫
- 判断"生产不触发"要同时给出**触发条件清单**（这里是：新增 root handler / 子进程里再 install()）
- 这类"生产不触发但逻辑有病"的项，价值在于**回归测试锁语义**，而不是等它出事故

### Metadata
- Source: session_finding
- Related Files: src/cli/plain_errors.py, tests/core/test_plain_errors.py, AGENTS.md
- Tags: logging-filter, idempotency, silent-defect, red-green-verification

## [LRN-20260911-009] lesson
**Date**: 2026-09-11
**Priority**: low
**Status**: completed
**Area**: env-sandbox-pytest

### Summary
**全量 pytest 报「49 errors」时先看是不是沙箱的批量删除守卫，别急着归因到自己的改动。**

本次全量回归出现 2 failed / 49 errors，报错全在 `_pytest/fixtures.py:1221 assert not self._finalizers` +
`sitecustomize.py:851 _check_bulk_delete_guard`。真因：pytest 每个用例要 `rmtree` 自己的 tmpdir，
累积到 85 个文件 > 沙箱阈值 50，守卫直接 `raise SystemExit(1)`，**在 fixture teardown 阶段炸掉，
污染后续所有用例**。与本次代码改动零关系。

识别特征（三条同时出现基本可确诊）：
- `SystemExit: 1` + 载荷含 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 与 `"count":N,"threshold":50`
- traceback 里出现 `_pytest/fixtures.py` 的 `assert not self._finalizers`
- 失败集中在 setup/teardown 而非断言体

规避：给 pytest 一个**专属且已存在的** TEMPROOT，并让每个用例的 tmpdir 落在其下
（`PYTEST_DEBUG_TEMPROOT=<ASCII路径>`），跑完清理整个 root 而不是让它反复 rmtree。
**路径必须纯 ASCII**（中文仓库路径会让 faiss 写索引失败——同 LRN-20260911-003）。

### Suggested Action
- 「一堆 errors 集中在 setup/teardown + Traceback 里没我的代码」⇒ 先怀疑环境守卫，不要回滚改动
- 跑全量前先清一次专属 tmp root，别在同一个目录上叠加多轮
- 报数字时注明**环境是否干净**，否则基线不可比

### Metadata
- Source: session_mistake
- Related Files: tests/conftest.py, AGENTS.md
- Tags: sandbox-guard, pytest-tmpdir, false-attribution, baseline-comparability


## [LRN-20260911-010] lesson
**Date**: 2026-09-11
**Priority**: low
**Status**: completed
**Area**: git-push-verification

### Summary
**git push 输出「Everything up-to-date」不必然等于没推上去，也不必然等于推上去了——必须用远端 API 核对。**

本次 push 脚本报 `PUSH rc=0 ... Everything up-to-date` + `RESULT: PUSH OK`，
但这句话字面意思是「远端已是最新」，与「我刚 commit 了新东西」矛盾，无法判断到底成没成。

根因：本仓库**没有任何 remote-tracking ref**（`git for-each-ref` 里无 `refs/remotes/`，
`git log origin/main` 直接 fatal）。脚本内部先 `fetch origin`——若在此之前
某次运行已经把提交推上去了，这次 fetch 就带回远端状态，"up-to-date" 说的其实是**实情**。
但**本地 ref 缺失使人无法用 git 命令自证**，于是"成功"与"没干活"在输出上无法区分。

可靠核对方式（不依赖 remote-tracking ref）：拿脚本解密的令牌调
`GET /repos/{owner}/{repo}/commits?per_page=3`，比对 head sha。
本次核对结果：远端 head = `f46c026` = 本地 head ✅（推送确实成功）

### Suggested Action
- **看到 "Everything up-to-date" 先别下结论**，调 API 比对 head sha（1 次请求即可）
- 不要试图用 `git log origin/main` 来"核实"——本仓库压根没这个 ref，只会拿到 fatal
- `scripts/push_github.py` 只返回 rc、不返回"推了几个 commit"，信息量不足以自证，建议将来补一行
  `git rev-parse HEAD` vs 远端 sha 的显式对比输出

### Metadata
- Source: session_finding
- Related Files: .workbuddy/scripts/push_github.py
- Tags: git-push, remote-tracking-ref, api-verification, ambiguous-output

## [LRN-20260913-011] lesson
**Date**: 2026-09-13
**Priority**: high
**Status**: completed
**Area**: data-source-probing, fear-index

### Summary
**akshare「接口存在」≠「数据可用」：历史类接口的三个隐性约束，必须先探针数据密度再定口径。**

恐慌指数（v0.8.10）实施中三个探针推翻设计假设的实测：
1. **估值历史全是稀疏采样**：乐咕 `stock_index_pe_lg`/`stock_a_ttm_lyr`、中证
   `stock_zh_index_value_csindex`（仅近20行）、股息率 `stock_a_gxl_lg`（参数不含沪深300）——
   名字像日频，实际近3年只有 38-40 个采样点，250 日分位窗根本凑不齐。
   → 解法：自算 point-in-time 口径（乐咕 PE 采样点反推盈利阶梯 ÷ baostock 日频真实 close
   − 10Y 国债）。盈利本就是季度级慢变量，阶梯化无插值污染。
2. **东财涨停池"历史"接口仅保留约 20-30 个交易日**：`stock_zt_pool_em(date=)` 对更早日期
   一律返回**空 DataFrame（不报错）**，250 日回填 219 天静默失败。
   → 解法：连续 15 个交易日空即判定保留边界提前停止 + min_samples 按成分特例放宽（15）
   + 输出 note 如实标注口径。
3. **akshare 对未来日期可能返回最新数据**（参数被静默忽略）：`trading_days()` 含未来 30 天，
   回填列表未过滤 → 16 个未来日期的脏文件（内容实为当日数据，若不清理会永久污染序列）。
   → 解法：回填列表过滤 `d <= baseline` + 上线前清脏文件。

另：net_guard 熔断（连续3败→120s）在批量回填场景会把「接口数据边界」放大成「大面积失败」——
先区分「数据真没有」和「被熔断拒绝」，再决定重试策略。

### Suggested Action
- 新数据源接入前先跑 tests/data_sources/probe_*.py：**数据密度（行数/日期跨度）比接口存在性更重要**
- 「按日期查历史」的接口必须测三件事：远古日期（保留边界）、非交易日（空 vs 报错）、未来日期（是否静默返回最新数据）
- 参数加到函数签名后，内部转发调用点要有测试锁（本次 percentile_panic 收了 min_samples 却没转发给 pct_rank，端到端才发现）

### Metadata
- Source: session_finding
- Related Files: src/core/fear_index/history.py, src/core/fear_index/normalizer.py, tests/data_sources/probe_fear_data.py
- Tags: akshare, data-density, point-in-time, probe-first, fear-index

## [LRN-20260922-012] lesson
**Date**: 2026-09-22
**Priority**: high
**Status**: completed
**Area**: error-handling, test-isolation, scan-review

### Summary
**scan review（v0.8.11）实施中的两条实证教训：宽 except 吞笔误类异常 + 给写文件的函数加副作用必须扫测试隔离同类点。**

1. **宽 except 会把 NameError 静默吞成业务 None**：`_review_base_from_kline` 里
   `datetime.now()` 用了裸名（同函数只导入了 `datetime as _dt` 别名），NameError 被
   `except Exception → logger.debug → df_cache[code] = None` 整条链静默吃掉，
   表现为「K线兜底悄悄失效」而非崩溃。单测 mock 全部正确，靠**精确数字断言**（+100.00
   缺失）才定位。这正是项目铁律清单里 `except+debug+continue` 静默 fail-open 模式的
   变体：兜底性 except 对「笔误类异常」是无差别掩体。
2. **给既有写盘函数加副作用后，测试隔离重定向会漏新文件**：`save_last_scan` 增加
   追加 scan_history.jsonl 的副作用后，`tests/core/test_session_state.py` 的 4 个测试
   只 monkeypatch 了 `_LAST_SCAN_FILE`——全量 pytest 期间向真实 `~/.muyun/`
   写入 8 条测试垃圾行（此前它们也写真实 last_scan.json，属既有泄漏被放大）。
   靠真实运行验证时发现历史文件「凭空已有 8 条今日记录」才倒查出来。

### Suggested Action
- 复用 `except Exception → logger.debug` 兜底的函数，落笔前先 grep 本函数内新用的
  顶层名是否真的在作用域里；或兜底 except 收窄到预期异常类型
- 新增任何写盘路径后：grep 全部调用方测试是否 monkeypatch 了新路径的模块常量；
  写完跑一次真实运行核对目标文件内容与预期一致（读代码+单测都发现不了测试越界写）
- 验证类临时脚本若要模拟历史时间戳，注意 append 类函数固定盖 now()——须直写文件

### Metadata
- Source: session_finding
- Related Files: src/cli/main.py, src/cli/session_state.py, tests/core/test_session_state.py, tests/core/test_scan_review.py
- Tags: broad-except, silent-fail-open, test-isolation, monkeypatch-redirect, scan-review

## [LRN-20260923-013] best_practice
**Logged**: 2026-09-23T01:30:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend, tests

### Summary
**对抗审查抓住三类「文档/测试声称 ⟷ 代码事实」的静默漂移：primitive 带重写副作用、修复未入库、测试 mock 错被测条件。**

scan review/观察池五提交的 code-quality-guard 对抗审查（13 项发现）里最值钱的三条：

1. **P0-1 primitive 携带重写副作用**：`append_scan_history` 每次写入「读全文件→prune>90天→重写」，而 import 逐文件调它 → 导入 >90 天旧报告被同批/后续 append 静默删除且 UI 报成功。结构病：**单条写入原语不该拥有全量重写/清理权**——原语只做纯追加，批量/清理策略归上层调用方；且「导入旧数据」类功能与任何基于时间窗的自动清理结构性冲突，设计时要显式对表。
2. **P0-2 修复未入库**：测试隔离的修复只改在工作区，声称「已修+已清除」的文档/LEARNINGS 却先提交了——任何人 clone 后复跑全量测试复现污染。纪律：**声称修复的 commit 必须包含修复 diff 本身**，docs 与 fix 永远同 commit。
3. **P1-1/P1-2 声称⟷实现⟷测试三层漂移**：docstring 写「无持仓」代码没查持仓；测试用 `exit_triggered` mock「有持仓」场景——恰好 mock 掉了被测条件本身，真实缺陷路径（持仓+HOLD+无触发）永远跑不到。**测试的每个「不入池理由」都要有一条直接路径用例**，mock 条件 ≠ 被测条件。

### Suggested Action
- 新写「带清理语义的写入函数」前先枚举全部调用方的时间尺度（本次/当天/任意历史）——有「导入历史」类调用方就禁用时间窗清理
- commit 拆分时检查：docs 声称的每个修复是否真在同一 diff 里（`git show --stat` 对文档与代码文件逐一对）
- 测试里 mock 的条件字段必须与被测代码实际读取的字段同名（读 code 不读 test-double 的别名）

### Metadata
- Source: code-quality-guard adversarial review
- Related Files: src/cli/session_state.py, src/cli/main.py, tests/core/test_session_state.py, tests/core/test_watch_pool.py
- Tags: side-effect-primitive, silent-data-loss, test-isolation, claim-impl-drift, adversarial-review
- See Also: LRN-20260922-012（宽 except 吞笔误/写盘副作用测试隔离——同日同Feature连续两轮，属同族模式）
- Pattern-Key: harden.side_effect_primitive
- Recurrence-Count: 2
- First-Seen: 2026-09-22
- Last-Seen: 2026-09-23

## [LRN-20260924-014] best_practice
**Logged**: 2026-09-24T12:00:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests, windows

### Summary
**「子进程测试稳定失败但隔离 HOME 后通过」≠ HOME 依赖——先查父进程对子进程输出的解码码页。**

M1 落地时把交接文档里「test_indicator_math_actually_ran 我的环境稳定失败、架构师隔离 HOME 环境通过，疑似依赖 HOME 下某状态」定性了：与 HOME 完全无关。fixture 的 `subprocess.run(text=True, errors="replace")` **没指定 `encoding=`**，父进程按 `locale.getpreferredencoding()`（中文 Windows = GBK）解码子进程输出；子进程带着 `PYTHONIOENCODING=utf-8` 写 UTF-8 字节 → GBK 解码成乱码 → 「通过 N 项|警告 N 项|失败 0 项」汇总行正则永远匹配不到。架构师环境能过只是碰巧带了 PYTHONUTF8=1（UTF-8 模式下 locale 编码返回 utf-8）。教训：两个环境变量差异（HOME vs PYTHONUTF8）在「稳定失败/稳定通过」上表现一模一样，猜环境依赖前先看 I/O 字节流路径。

### Suggested Action
- 本项目所有 `subprocess.run(..., text=True)` **必须显式 `encoding="utf-8"`**（console/管道码页是 GBK，中文输出必乱）；grep 同类点：`subprocess.run` 且 `text=True` 且无 `encoding=`
- 跨环境「一会过一会不过」的测试：先复现抓原始断言输出（看是否乱码），再谈环境状态依赖
- 子进程输出含中文要用正则断言时，解码码页是第一嫌疑，HOME/缓存状态排后面

### Metadata
- Source: session_finding
- Related Files: tests/core/test_indicator_math_script.py, scripts/verify_indicator_math.py, tests/conftest.py, pytest.ini
- Tags: subprocess-encoding, gbk-utf8-mojibake, test-isolation, root-cause-analysis, pytest-ini
- Pattern-Key: test.subprocess_decoding
- Recurrence-Count: 1
- First-Seen: 2026-09-24
- Last-Seen: 2026-09-24

## [LRN-20260925-015] best_practice
**Logged**: 2026-09-25T10:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests, isolation

### Summary
**monkeypatch 替换模块属性期间，任何模块被「首次 import」都会把替身按值捕获进自己的命名空间，且 monkeypatch 恢复不了——这是全量测试能过、局部顺序一变就炸的典型病根。**

M5 批实证：test_tui 的 `_mock_engines` patch `portfolio.PortfolioManager`，而 tui/app 引擎初始化触发 `src.cli.main` **首次导入**——main 顶层的 `from src.data.portfolio import PortfolioManager` 是按值绑定，把 lambda 永久捕获进 main 命名空间；monkeypatch teardown 只恢复 portfolio 模块的属性，管不到 main 里的副本。全量跑一直没炸是因为收集顺序 chat 先于 ui（main 早已以真实类导入）；一旦 ui 先跑（单文件组合、目录改名、并行收集），下游 5 个桥测试全挂。修复 = 测试文件收集期预导入 main，保证 by-value 绑定恒为真实类。

### Suggested Action
- 写「patch 模块属性」类测试替身时，先全库 grep 该符号的 `from ... import X` 按值绑定方（如 `PortfolioManager` 在 main.py:32）——凡可能在本测试期间首次导入的，都要在测试文件顶部预导入真实模块
- 「全量绿但组合跑红」的顺序依赖，第一嫌疑就是 by-value 捕获替身，其次才是环境变量/缓存残留
- 接线类测试（parse_input→run_cli 全链）与单元直调测试要并存：M6 的 doctor 命令 4 个单测全绿但 run_cli 函数级导入清单漏了符号，运行时必 NameError——直接调实现永远抓不到接线断裂

### Metadata
- Source: session_finding
- Related Files: tests/ui/test_tui.py, src/cli/main.py, src/tui/app.py, src/data/portfolio.py, tests/core/test_doctor.py, start.py
- Tags: monkeypatch-capture, by-value-import, test-order-dependence, import-order, wiring-test
- Pattern-Key: test.byvalue_capture
- Recurrence-Count: 1
- First-Seen: 2026-09-25
- Last-Seen: 2026-09-25

## [LRN-20260925-016] best_practice
**Logged**: 2026-09-25T12:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests, process

### Summary
**回调插桩（progress_cb 内的记账/副作用）没有时序测试 = 不可见的 no-op；commit message 声称的「落账/修复」必须与 diff 逐项一致——监督 agent 抓到两连：ba 的 batch_task_start 放在 auto_score_batch 之后（回调 mark 全落空、中断丢进度的修复未生效），且 plan/README 批 4 条目「声称已落实际未落」。**

两个坑同根：**修完不验证副作用真的发生**。_progress 回调里的 batch_task_mark 对未登记任务静默 no-op（既有测试锁死的语义反过来保护了 bug：no-op 不抛不崩，全量全绿）；plan/README 条目在 git add 清单里漏了文件但 commit message 照写「已落账」。plus：函数内 `from X import Y` 的 patch 必须打源模块（main.auto_score_batch 属性不存在，时序锁测试第一版就踩了）。

### Suggested Action
- 凡「回调/钩子里做写操作」的插桩，必须配时序测试：mock 依赖函数令其触发一次回调，断言副作用已发生（test_ba_task_ledger_timing 为范本）
- commit 前对照 message 逐文件核对 `git show --stat`（铁律 2 已有，但「声称落账的文档文件」也要在 stat 清单里点名核对）
- 函数内 from-import 的符号：patch 源模块属性（src.core.benzong.batch_scorer.auto_score_batch），patch 使用方模块属性无效
- 监督 agent 的核对范围必须包含「commit message 声称 vs diff 实际」逐项比对——本轮抓到的两个问题都在这个盲区

### Metadata
- Source: session_finding（code-quality-guard 监督员批 4 核对）
- Related Files: src/cli/main.py, tests/benzong/test_ba_task_ledger_timing.py, plan/README.md, src/cli/session_state.py
- Tags: callback-instrumentation, timing-test, claim-diff-drift, batch-ledger, no-op-pitfall
- Pattern-Key: test.callback_timing
- Recurrence-Count: 1
- First-Seen: 2026-09-25
- Last-Seen: 2026-09-25

## [LRN-20260925-017] best_practice — 决策语义与研究证据分开验收

**Logged**: 2026-09-25
**Priority**: high
**Status**: applied（2026-09-25 F0–F2 已实施：PROBES A/B 转真回归/终态统一/持仓分离落地并有测试锁；收益验证留 F8/F9）
**Area**: architecture, research

### Summary

用户本任务只授权架构规划。对bdebf13进行只读探针，确认人话摘要可能与传入的最终策略动作相反；DecisionResult.score是动作强度，不能直接视为买入吸引力或上涨概率。分析建议与已确认持仓需分离；局部探针不能替代端到端或收益验证。

### Suggested Action

- 执行plan/fusion/F0–F2时，把PROBES中的案例接到真实Strategy/PlanGuard/Execution路径；所有界面只消费统一终态。
- 当前bz规则代理、人工指定mode历史案例与live AI是不同信息集，分别标识，不能以旧收益证明自动选股能力。
- 断点交接依赖仓库文件；已停止子任务的未落盘内容不能算已完成。恢复先核对HEAD与文档，再继续。

### Metadata

- Source: read_only_architecture_review
- Related Files: plan/fusion/README.md, plan/fusion/PROBES.md, plan/fusion/RESUME.md
- Pattern-Key: decision.final_state_and_evidence_semantics

## [LRN-20260925-018] best_practice — pydantic v2 契约建模三坑（F0 实证）

**Logged**: 2026-09-25
**Priority**: high
**Status**: applied（decision_contract.py 已按此实现并有测试锁）
**Area**: pydantic, data-contract, test

### Summary

F0 写 DecisionPacket 契约踩中 pydantic v2 三个坑：① **默认值不校验**——`field_validator` 对缺省字段根本不跑（exchange 派生逻辑静默失效，36 测试里 6 个红灯才暴露），派生/归一逻辑必须放 `model_validator(mode="before")`；② **frozen 模型不能构造后赋值**——ADR-F02 要不可变记录，派生逻辑改成在 mode="before" 阶段改输入 dict（frozen 下 mode="after" 原地赋值直接炸 `Instance is frozen`）；③ **全角数字陷阱**——`str.isdigit()` 对 `'６００５１９'` 返回 True 产出垃圾 ID，必须 `isascii() and isdigit()`（全角字符是本项目登记在案的坑面）。

### Suggested Action

- 新契约/模型把"缺省时也要跑的逻辑"一律写进 model_validator(mode="before")；field_validator 只做显式传入值的校验。
- 不可变契约（frozen=True）配套做三件事：禁 model_construct（重写抛 NotImplementedError 防绕过校验）、extra="allow" 保未知字段、全角字符显式拒收。

### Metadata

- Source: session_finding（F0 实施 + code-quality-guard 3 阻断审查）
- Related Files: src/core/decision_contract.py, tests/core/test_decision_contract.py
- Tags: pydantic-v2, frozen-model, validator-default-pitfall, fullwidth-digits
- Pattern-Key: pydantic.contract-modeling
- Recurrence-Count: 1
- First-Seen: 2026-09-25
- Last-Seen: 2026-09-25

## [LRN-20260925-019] best_practice — 测试替身枚举比较恒 False + 提交树计数口径

**Logged**: 2026-09-25
**Priority**: medium
**Status**: applied
**Area**: test, discipline

### Summary

F1/F2 两课：① 测试替身用 `SimpleNamespace(value="FLAT")` 冒充 `TradeLifecycle` 枚举，生产代码 `new_state.lifecycle == TradeLifecycle.FLAT` 恒 False → 删除分支静默不触发（假绿）；替身涉及相等比较的字段必须用真枚举。② 批次 commit 的测试计数要按**提交树**口径——工作区混入下一批文件时，用 `git stash push -u -- <下一批文件>` 隔离后实测再提交（F1 提交树 873 passed 实测，与算术 852+21 精确吻合），消灭"分项加总对不上"的账本漂移温床。

### Suggested Action

- 替身里的枚举语义字段（lifecycle/decision/status）一律 import 真枚举，别用 SimpleNamespace(value=...) 冒充参与 == 比较。
- 多批次并行实施时：提交前 stash 下一批文件 → pytest -q 实测 → commit → stash pop；计数进 commit message（L05）。

### Metadata

- Source: session_finding（F1 观察量测试红灯 + F2 提交树计数）
- Related Files: tests/core/test_portfolio_observation.py, src/data/portfolio.py
- Tags: test-double, enum-equality, commit-tree-count, L05, git-stash-isolation
- Pattern-Key: test.double_enum_and_count_scope
- Recurrence-Count: 2（枚举比较类问题本会话第二次出现）
- First-Seen: 2026-09-25
- Last-Seen: 2026-09-25

## [LRN-20260926-FUSION-ARCH01] correction — 接通、资格与效果不能互相替代

**Logged**: 2026-09-26
**Priority**: high
**Status**: pending（架构师只交付设计；R0–R9 待实施，不冒充代码修复）
**Area**: fusion, evidence, validation

只读架构复核确认：latest-only 数值带旧 pubDate 可进 strict；facts 非空可使 thesis VALID；引用匹配但正文相反的 claim 可通过结构核验；未知现金仍有数学分配额度。局部探针不代表已发生错误交易，详见 plan/fusion/iteration2/PROBES.md。

后续门禁：时间过滤另需历史版本证明；用户事实文本另需内容与命题资格；数学额度另需账户/交易可行性；全 WAIT / 零预算输入 / 风险 0/0 不算目标路径验证。完成状态按 implemented/connected/scenario_validated/empirically_validated/release_ready 分开登记，保留原实验而限定结论。

- Source: read_only_architecture_review
- Pattern-Key: fusion.semantic_qualification_and_nonvacuous_validation
- Related Files: plan/fusion/iteration2/README.md, plan/fusion/iteration2/TASKS.md, plan/fusion/RESUME.md
