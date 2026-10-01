# 暮云持续迭代计划

**当前入口（2026-10-01）**：[长期里程碑与完成标准](../MILESTONES.md) → [实施/审批状态](fusion/STATUS.md) → [R12审批与第六轮](fusion/iteration6/R12_ACCEPTANCE.md) → [恢复入口](fusion/RESUME.md)。以下M/C表格与角色叙述为历史工程记录及当时作者分工，不替代当前进度或给阅读者重新分配身份；第六轮M卡须带目录与早期M卡区分。

更新：2026-09-24。接手基线：`e84fac4`（v0.8.12.1）。

总架构、模块边界、能力增强路线和 Agent 协作机制见 [总体架构与演进设计](ARCHITECTURE.md)。首批技术任务卡、测试失败分析及读入顺序见 [技术交接](TECHNICAL_HANDOFF.md)。

这是跨 Claude Code / Codex / 其他 Agent 的计划与交接入口。**用户最新要求：本任务只写计划，不改代码。** 下表是供后续执行者认领的工作，不代表本任务正在实施。历史产品问题仍以 `ISSUES.md` 为索引；未来实施范围由接手任务的用户指令决定。

**角色约定**：当前任务担任总架构师，负责规划、技术设计、任务拆分、审查验收与后续迭代；执行 Agent 负责实现。目标是更优雅、更可靠、能力更强、代码更精简，具体判据见架构总纲。新增设计建议与已核实事实分别标注，不把设计当成实现。

## 目标与边界

先保证数据不丢、失败可理解、验证可复现，再缩小模块职责并优化日常工作流。成功指标是命令的实际行为及回归证据，不是删了多少行。

- 保持现有 REPL 命令和 chat 工具协议兼容。
- 研究/决策辅助定位不变；不接实盘交易，不用当前公告/基本面补历史回测。
- 评分公式、气宗/剑宗、Weinstein 双口径、force_exit 行为有既有回测约束，独立立项后才改。
- 保留用户工作区：`portfolio.yaml`、Claude 配置/计划、知识索引备份、未跟踪教材及临时研究资产。禁止目录级暂存。
- 当前记忆里的“WorkBuddy 只负责审查”属于旧角色分工，不限制本次明确授权的开发工作。

## 已核实的架构

```text
start.py parse_input / run_cli ─┬─ src/cli/main.py 分析、扫描、复盘、持仓
chat/tools.py run_command ─────┘
chat/tools.py analyze_stock ───── Orchestrator
                                  ├─ Skill → Decision → Event → AI Modifier
                                  ├─ EntryExit → Strategy → PlanGuard → Execution
                                  └─ 持仓状态回写 / WATCH 自动入池
scanner ── session_state ── last_scan / scan_history / watchlist ── review
RAGService ── ingestion / embedding / FAISS / retrieval ── CLI、chat、TradePlan
BacktestEngine ── DataFeeder ── Orchestrator(is_backtest=True)
```

2026-09-24 机械盘点：`src` 99 个 Python 文件，36,455 行；`src/cli/main.py` 4,395 行；`AGENTS.md` 395 行 / 63,836 字节。行号会变化，接手时重新搜索符号。

已读取：AGENTS / CLAUDE / CODEX、ISSUES 当前表、`.learnings` 近期记录、Claude 项目 memory 索引及条目、WorkBuddy memory 索引、旧重构与测试体系交接、现有 Claude 体验计划。记忆资产存在状态漂移，例如指标数学验证已通过 `test_indicator_math_script.py` 回归，不能照旧报告重复立项。

## 里程碑与验收

| 编号 | 状态 | 范围 / 技术方案 | 验收与可感知结果 |
|---|---|---|---|
| M0 | 已完成初步盘点 | 建立架构图、资产索引、实际测试基线；识别外源测试和副作用 | 已记录基线与边界；不声称完成全部源码逐行审计 |
| M1 | 已实施（2026-09-24） | pytest 默认离线；真实接口显式开关；测试收集前隔离 home，保留离线 data_sources 单测 | 一条命令得到稳定结果；不碰真实 `~/.muyun`，不默认花 AI 费用 |
| M2 | 已实施（2026-09-24） | 会话 JSON 快照同目录临时文件原子替换；验证 last_scan / deep_analyzed / 历史 / watch 读取边界；损坏行告警可见 | `last` / `#N` / `watch` 遇坏记录不崩，保留可读记录；写入失败旧快照完整 |
| M3 | 已实施（2026-09-24） | 扫描报告命名防同分钟覆盖，兼容旧报告导入；故障注入及真实 REPL 冒烟 | 连续相同主题扫描保留两份报告，历史导入仍幂等 |
| M4 | 已实施（2026-09-24/25，两批） | 分批提取 CLI 的 review 纯计算与命令服务；main 保留兼容出口；共享入口只依赖服务，不反向依赖界面 | 精确数字及命令桥确认门不变；CLI/chat 相同输入同结果 |
| M5 | 已实施（2026-09-25） | 持仓并发写保护：先梳理所有写入方法及调用方，再以内容版本检测拒绝旧快照覆盖；失败必须传到输出层 | 两实例修改不会静默覆盖，REPL/chat/Web/TUI 都不报假成功 |
| M6 | 已实施（2026-09-25） | 集中运行诊断、缓存新鲜度与缺失原因呈现；核对安装入口；压缩 Agent 指令中的历史叙事 | 新环境可按文档启动，诊断不泄密；知识入口短而准确 |

执行顺序：M0 → M1 → M2 → M3；M4/M5 各独立提交与验证，M6 随批次收口。不得把“待实施”描述成已完成。

能力增强 C1–C6 与装配/数据质量/超时专项见架构总纲，等首批可靠性门槛通过后排入执行，不与 M1–M3 混做。

## 技术约束与风险

1. **测试**：不能按目录整组排除 `tests/data_sources`，其中大多数是纯 mock 测试。`test_all_api.py` 有 import 时环境/证书副作用，应在收集前排除，不能只靠 marker 事后筛选。外源体检保持显式可运行。
2. **持久化**：JSONL 保持追加式，不恢复 prune；快照原子替换不等于并发事务。暂不把所有缓存搬进新存储框架。不可修坏记录时直接重写原文件。
3. **数据兼容**：保留现有 JSON/YAML schema、`#N` 顺序、入池时间锚点和旧报告格式。告警新增同步 `plain_errors.py` 与报错速查手册。
4. **拆分**：先提取没有 IO 的 review 算法，再拆展示/命令。保留 `main` 的旧导入点及测试 patch 接口，确认 consumer 后再逐步迁移，避免一次移动整个大文件。
5. **安全网**：`position_action=CLOSE_ALL` 是执行语义；评分改动必须失效评分缓存；RAG 重排默认关闭；不并行加载多个真实模型。
6. **持仓（原基线问题，M5 已实施）**：原 `_save` 外部修改后仍覆盖、mutator 未传播失败，现已增加指纹冲突拒绝与失败传播，见执行记录。新一轮另审“建议与真实持仓分离”，不要把并发保存保护误当成交语义已解决。

## Agent 协作约定

- 接任务先读本文件、AGENTS、工程纪律技能与相关源码；旧文档只作线索。
- 在执行记录写清负责文件、状态与验收命令。避免同时编辑 `main.py` / `start.py` / AGENTS。
- 可并行的后续任务：M4 review 计算提取、M5 持仓调用链设计、M6 安装诊断设计；每项须明确文件所有权，与其他 Agent 的改动兼容，不回退他人修改。
- 修复先给回归红灯，转绿后扫同类点，教训进入 `.learnings`；运行与文档证据同批交付。
- 有全局状态改动必须跑完整离线回归。真实数据接口/AI 验证单独记账，不能用它们的抖动掩盖离线失败。

## 执行记录

### 2026-09-25 / Claude Code / 用户追问触发：条款级穷尽核对 + 4 处遗漏补齐

- 用户质疑测试速度与文档遗留，触发逐条款穷尽核对。测试速度实证：809 收集/806 执行/56s——`--durations` 显示最慢 12 项占 45s（回测等价性 20.6s 等），其余 ~790 项为 mock 内存单测（均 <50ms）；快的原因是 M1 起网络体检 test_all_api（分钟级大户）已分离出离线集合。
- **条款级核对发现的 4 处遗漏，本批补齐**：
  1. ADR-02「配置校验给出字段路径与修正方式」未做 → load_config 补根类型/空文件/本地覆盖根类型三重校验（ValueError 带文件路径与修正指引；ai 节不校验，不阻止纯客观命令）+ 3 项测试
  2. ADR-07「缓存键元数据集中」只写在 docstring → registry 补 CACHE_KEY_FIELDS 常量 + 测试锁与 cache.get 实际签名一致（首版就抓到声明与实现漂移：stock_code/date/dimension vs code/date/dim）
  3. C5「分组表无专项测试」（批 8 漏登记，监督员批 8 核对点名）→ 补 2 项专项测试（两来源出分组表+口径说明；单来源不刷屏）
  4. 批 8 的 C5 缺口登记本身漏写 plan/README → 本条补记
- **核对后仍登记不修的条款（附理由）**：
  - C2「以配置版本识别任务」：现有调用链外层无 config 上下文，强传=接线改动大于收益；输入标识（source/codes）已做
  - C4「回答级引用归属」（AI 回答追到检索块）：chat 层归因，架构师「以后逐步迁移」条款，检索层分组视图已做
  - ADR-06「任务 ID/结果时点可辨识」「全链路预算」：动 _safe_call 返回契约影响全部调用方，超最小化边界，按需专项
  - ADR-02「RAG 退出可释放」：单例纪律已有，释放路径未专项验证，登记
- 测试 +5（runtime 校验 3 + C5 分组 2）；全量 **811 passed / 0 failed**。

### 2026-09-25 / Claude Code / 批 8：ADR-04/06/07 收口（阶段 D）+ C 批次收口

- **ADR-07 评分元数据**：新增 `src/core/benzong/registry.py`（DIM_ORDER/DIM_CN 集中声明）；main._BZ_DIM_CN 保持原名兼容并加注册表指针；测试锁 registry 与 scorer 权重键集合一致（防漂移）。唯一公式仍由 scorer.py 拥有，未动公式/未 bump CACHE_VERSION（无评分行为变化）。
- **ADR-06 超时资源测量**：新增 `scripts/measure_timeout_overhead.py`（零网络模拟阻塞）并实跑基线：6 次模拟超时总耗时 12.2s 无卡死（防冻结生效），孤儿线程 +2 后自然消亡，进程退出不等待（shutdown(wait=False) 纪律验证）。专项测量结论：当前语义开销可控，无需专项改造。
- **ADR-04 判定（文档级收口）**：OK/STALE/MISSING 语义已由 fear（v0.8.10）、doctor（M6）、分析证据 as_of（C1）实现最小闭环；"每个新增字段必须有消费方"红线下不建通用元数据层，跨模块扩展按需逐例。
- **阶段门槛核对**：A（M1–M3）✅；B（M4+live 装配统一+M5）✅ ADR-02 批 1 收口；C（M6+C1+C2）✅；D（C3–C6+超时专项）✅ 本批收口。C 批次全部完成。
- 测试 +2（test_bz_registry）；全量 **806 passed / 0 failed**。

### 2026-09-25 / Claude Code / 批 6：C5 扫描方法有效性（阶段 D）+ 批 7：C6 评分证据增强（阶段 D）

- **C5**：scan_review 汇总尾部新增「按扫描方法分组」表（控制台+报告 md 双输出）：每 source 的只次/胜率/平均涨跌/平均超额/无行情数。口径全透明（架构师 C5 验收项）：同股多次扫描按只次独立计、基准=各自扫描日同窗沪深300、缺口剔除、样本 <10 只次只看方向不下结论。零额外请求（复用复盘已取数据）。
- **C6**：`_bz_industry_sources_line` helper——行业景气维的数据来源透出（["行业: 稀土", "商品锚(氧化镝)", "新闻 12 条"] 等，v0.8.8.7 三级桥接成果可视化）；接 bz 单股建仓引导与 pos plan 两处输出；无来源时不输出假来源行（行缺席=覆盖不足的信号）。新维度未加（按 C6 口径"新维度先独立观察"）。
- 测试 +3（test_c6_bz_evidence 3 + C5 随既有 scan_review 断言覆盖）；全量 804 passed / 0 failed。

### 2026-09-25 / Claude Code / 批 5：C4 RAG 评估增强 + 批 4 更正（监督员 P1）

- **批 4 更正（监督员核对抓到的 P1，65f81c6 已推送含失实声称，本 commit 更正）**：ba 的 `batch_task_start` 原放在 `auto_score_batch` 之后——_progress 回调里的逐项 mark 对未登记任务 no-op，"修中断丢进度"未生效。已挪到 auto_score_batch 之前 + 新增时序锁测试（test_ba_task_ledger_timing，进度回调首次触发时任务必须已登记）。教训：回调插桩没有时序测试=不可见的 no-op；commit message 声称的落账必须与 diff 一致。
- **C4 交付**：evaluator.py 新增 `series_breakdown`（top-k 命中按 doc_id 系列前缀分组——引用归属的分组视图，命中过度集中=知识覆盖面偏科可见）+ 报告带生成时间（索引变更后重跑对比）；无下划线 doc_id 归 other 不污染系列统计。既有 IR 指标（Recall/MRR/NDCG）与 check_label_coverage 已存在，本批未重造；auto_label 自证与重排默认关闭的既有结论保持。
- 纯计算指标纳入离线回归（test_rag_eval_metrics 4 项）；真实检索评估仍为手动跑（tests/README.md 有跑法）。
- 测试 +5（metrics 4 + 时序锁 1）；全量 **801 passed / 0 failed**。

### 2026-09-25 / Claude Code / 批 4：C3 doctor 增强（阶段 C）

- doctor ④ 节：+ analysis_evidence 健康项（条数+末条距今——监督员批 2 建议采纳，把证据静默失败变成 doctor 可见症状）+ batch_tasks 健康项 + 缓存目录磁盘大小；③ 节：AI 配置透明（provider/model 显示，api_key 只报布尔绝不回显）；尾部体检耗时行。
- **C3 收窄如实声明**（ISS-102 已补记未交付段）：立项口径的"分阶段耗时/请求数/缓存命中率"未做——缓存命中率需运行时计数器（新框架，违背最小化），按需后排；已交付"总耗时+缓存磁盘大小+请求透明的成本行（既有）"。
- 测试 +1；全量 796 passed / 0 failed。
- **P3 两条登记于 ISS-102**（ba 失败项缺 code 键产出 None 条目 / 保留窗挤出），不修。

### 2026-09-25 / Claude Code / 批 3：C2 批量任务可续跑（阶段 C）+ 批 2 账本补齐

- **C2 交付**：session_state 批量任务账本（batch_task_start/mark/get_recent_batch_tasks，`~/.muyun/batch_tasks.json` 原子写、坏账本重置、保留最近 5 个任务）；l 多代码/la/l all/ba 四个批量循环逐项记账（成功/失败+错误摘要）；REPL 新命令 `tasks`（进度 n/总数、失败项、续跑提示）。成功项复用沿用既有 deep_analyzed/评分缓存语义（核实过，未改变）；失败项自动重试语义保持。任务账本只做可见性，不引入通用工作流框架（C2 口径）。
- **批 2 账本补齐**（监督员核对发现的缺口）：plan/README 补批 2 条目；README.md "787"→"789" 笔误更正。
- **监督员建议采纳**：批 4（C3）将给 doctor 加 evidence 文件健康项（存在性/末条距今），把证据静默失败变成 doctor 可见症状。
- 测试 +7（batch_tasks：记账/继承/no-op/保留 5 个/损坏重置/坏编码）；全量数字见 commit message。

### 2026-09-25 / Claude Code / 批 2：C1 分析证据层 + diff 对比（v0.8.17，阶段 C）

- 新增 `src/cli/evidence.py`：record_evidence（JSONL `~/.muyun/analysis_evidence.jsonl` 追加 + 人话证据卡 `分析报告/analysis/{时间}_{代码}_{名字}.md`）满足用户既有「分析输出落盘回看」诉求；diff_evidence 同股两次比较只列变化项。
- 接线四入口（analyze_live/live_multi/chat/…）；REPL 新命令 `diff <代码>`（别名 对比）；版本 v0.8.17；ISS-100。
- 测试 +7；全量 789 passed / 0 failed。关键实现决策：新旧按 JSONL 出现序（同秒 ts 排序不可靠）、旧记录缺字段视为"当时未记录"不误报、坏行逐行隔离、证据写盘失败不影响分析。

### 2026-09-25 / Claude Code / 批 1：ADR-02 装配统一（阶段 B 收口，内部重构无行为变化）

- 新增 `src/config.py`（load_config + _deep_merge 自 main 迁入；**路径基于项目根推导，不依赖调用者 cwd**——ADR-02 验收项，test_chat_command_bridge 的 os.chdir 兜底不再必要）。
- 新增 `src/core/runtime.py` `build_live_orchestrator(config, *, rag_service)`：live 装配单一入口，config 各键逐项映射 Orchestrator 参数（漏传 entry_exit/rag = chat H2/ISS-032 同族事故的病根收敛）。RAG 生命周期由调用方持有，工厂不偷加载。
- 6 处手写装配收敛：main×4（`build_live_orchestrator(config, rag_service=_cli_rag())`，ai_debug 覆盖语义保持——先改 config 再传工厂）、chat init_engines、scanner_engine（_orchestrator_args dict → _orchestrator_config，懒 RAG 门控保留）。main.load_config 为兼容出口。
- 回测装配独立守卫：test 断言 backtest_engine 源码不含 live 工厂调用。
- 测试 +6（test_runtime：契约逐键映射/缺节容错/cwd 无关/深合并/自定义路径/回测隔离守卫）；test_iss090 结构断言随工厂化形态更新（4 处 build_live_orchestrator + patch 点随迁）。全量 **782 passed / 0 failed**。

### 2026-09-25 / Claude Code / M4 第二批实施（同构评估收敛，内部重构无行为变化）

- 第二批按 ADR-01「首轮只收口已经存在多个调用方的逻辑」收敛真正重复的实现：scan_review 与 watch_pool 的 `_bench_point`（基准同窗对齐，15 行×2）、逐票 `chg/excess` 计算、`bench_paths` 组装全部同构——提取为 `src/core/review.py` 的 `bench_point`/`compute_chg_excess`/`bench_path`，两命令改调同一实现（复用增加、改动面缩小；两份维护变一份）。
- **展示层留在 cli 是有意决策**（非未完成）：scan_review/watch_pool 的 Rich 渲染与 main 的 console（chat Tee 捕获依赖）深度耦合，搬到独立 render 模块只是文件搬家（架构师口径："main.py 行数下降只说明搬家"），收益低于风险；ADR-02 口径下复用增加才算重构收益，本批达成了它。
- main 兼容出口不变（`_sparkline`/`_review_path` 等身份别名 + `_bench_point` 局部薄壳）；全量 776 passed / 0 failed，既有精确数字断言即等价锁。

### 2026-09-25 / Claude Code / M1 任务卡第 5 步收口（离线纯净性审计）

- 按任务卡"把未 mock 的网络路径逐一查清"：写 socket.connect 阻断审计插件（scripts/audit_offline_net.py，含 asyncio socketpair 自唤醒放行），实测离线全量——首次 total=404，逐条定性后修复 6 处隐藏触网，复跑 **total=0**（776 passed / 0 failed）。
- 修复清单：① test_video_report_tier1 `liquidity(None)` 实为自动拉取路径（409 次 THS 全市场）→ 显式故障注入；② tier2 `top_signals(live=True)` 宏观成交额真拉 → mock get_market_turnover；③④ watch_pool/scan_review 的逐票 K 线兜底真连 baostock 登录 → mock get_historical_kline（最小非空 df 保住成本行断言）；⑤ multi_code 两用例的新浪预取/取数链 → mock 入口；⑥ fear adversarial 缺 pkg 层 recent_trade_date patch（M1 assembles 修复的同类点漏网——铁律 1 ③同类点扫描当时只修了被点名一处）。
- 已知盲区（如实）：子进程测试与 curl_cffi（libcurl C 层）不走 Python socket，审计覆盖不到；127.0.0.1 命中为 asyncio socketpair 自唤醒（进程内 IPC，放行）。

### 2026-09-25 / Claude Code / M6 实施

- **安装事实源**：实测 `uv sync` 因仓库无 pyproject.toml 直接报错（新环境装不起来）——按任务卡二选一取「统一为 requirements 安装命令」（requirements.txt 本身是钉版本已验证清单；避免 uv 解析器重解析传递依赖造成"盲目升级"）；AGENTS/CLAUDE/chat `__main__` 提示/架构文档全部更正，依赖升级须走独立验证。
- **doctor 诊断命令**（REPL/chat 命令桥通用）：解释器/17 项依赖（importlib.metadata 取版本，不 import 模块防告警副作用）/配置存在性/`~/.muyun` 状态缓存/RAG 知识库/报告目录；缺失如实标注；settings.local.yaml 只报存在性（泄密红线有测试锁死）。
- **Agent 入口压缩**：AGENTS.md「当前版本」段的 v0.8.14 及更早叙事链（~15KB）迁入 docs/archive/（保留指针 + archive/README 索引更新），两条跨版本硬约束补进 §五。
- 全量 775 passed / 0 failed / 2 skipped / 1 deselected。验收对照：新环境可按文档启动（安装命令实测口径）✓ 诊断不泄密（test_doctor 泄密红线测试）✓ 知识入口短而准确（AGENTS.md 63.8KB→43.7KB）✓。

### 2026-09-25 / Claude Code / M5 实施

- `PortfolioManager._save` 内容指纹冲突拒绝（sha256，升级 ISS-078 mtime：touch 不误报；自写基线取写入字节哈希）+ `_load()` 内存回滚 + `SaveResult(ok, conflict)`；5 个 mutator 返回保存成败 bool；REPL pos add/rm/overweight/plan、web /pos/add、chat update、chat/web/tui 回写四端失败提示接线。
- 顺带修复既有测试隔离脆弱性（监督 Agent 逐一排查 6 个顺序依赖失败后定位）：test_tui patch `portfolio.PortfolioManager` 期间 tui/app 首次导入 `src.cli.main`，main 顶层 by-value 绑定永久捕获 lambda——修复 = 测试文件收集期预导入 main。
- code-quality-guard：2×P1（overweight 假成功/chat update 错因误导 AI）+ 6×P2 全部同批修复；核心并发保护经两实例探针实测（A 存→B 拒→回滚可见 A→B 重做成功）。全量 771 passed / 0 failed。已知剩余（check→replace 竞争窗口、TUI/web 回写日志级告警）登记 ISS-097。

### 2026-09-24 / Claude Code / M4 第一批实施（纯计算提取，内部重构无行为变化）

- 新增 `src/core/review.py`（ADR-03 第一批）：`sparkline`/`offset_curve`/`curve_spark`/`review_path` 四个无 IO 纯函数原样提取（含 `SPARK_CHARS`），零网络零文件零终端；`_review_index_bars`/`_review_base_from_kline` 带 IO 留在 main（按任务卡"无 IO 先拆"）。
- main.py 原定义替换为兼容导出（`from src.core.review import sparkline as _sparkline, ...`）——tests 对 `cli_main._sparkline` 的引用与 patch 点不变，scan_review/watch_pool 内部调用走模块全局仍可被拦截。
- 测试 +1（兼容导出身份锁：`cli_main._sparkline is core_review.sparkline`，防未来复制实现双源漂移）。全量 766 passed / 0 failed——既有精确数字断言即行为等价锁。
- 展示（review_render）与命令（review_commands）迁移按 ADR-03 拆分顺序留后续批次：scan_review/watch_pool 的 Rich 渲染与 console/Tee 交互需先定 ReviewInput/ReviewResult 契约（与现有报告逐项对照），不宜与本批混做。

### 2026-09-24 / Claude Code / M2 + M3 实施

- **M2（commit 66e5e2d，v0.8.13）**：`_atomic_write_json`（mkstemp+os.replace，失败清理保留旧快照）应用于 last_scan/deep_analyzed；`get_last_scan` 结构校验坏快照整体拒绝（#N 不错位，修数组根 AttributeError）；JSONL 逐行 decode 坏行计数告警；timestamp 混排排序防御；`ScanSaveResult(snapshot, history)` 部分成功如实上报。全量 756 passed / 0 failed。code-quality-guard：无 P0/P1，5 条 P2 中 4 条同批修复、2 条登记 ISS-095 已知剩余。
- **M3（commit 1709975，v0.8.14）**：报告文件名秒级 + open("x") 独占创建 + _2/_3 递增（同分钟同主题不再互相覆盖）；import 已知记录改集合且**查重分粒度**（旧格式分钟键 / 新格式精确键——监督 Agent P1 实证：v0.8.11~13 live 秒级历史与分钟报告用精确键永不命中会重复导入，真实数据沙箱 29→32 行）；成功追加才入集合；`ScanSaveResult.timestamp` 同源传递（save/persist 两次 now() 跨秒错位消除）；旧 fixture 换固定文本 `_LEGACY_REPORT_V1`。全量 765 passed / 0 failed。code-quality-guard：1×P1 + 2×P2 全部同批修复。
- 未做（任务卡范围外，ISS-096 已知剩余）：`review_*.md`/`watch_*.md` 复盘报告仍是分钟命名。

### 2026-09-24 / Claude Code / M1 实施

- 负责文件：`pytest.ini`（新增）、`tests/conftest.py`、`tests/README.md`（新增）、3 个时间依赖测试（`test_fear_index_adversarial.py` / `test_fear_index_history.py` / `test_trade_plan.py`）、`test_indicator_math_script.py`、`test_source_check.py` / `test_tech_context_e2e.py`（标记）、`AGENTS.md` / `CLAUDE.md`（测试命令行同步）。零 `src/` 改动。
- 隔离方案采用 conftest import 期 HOME/USERPROFILE 环境变量（未用根 autouse fixture，避开 §3 记录的 chat_command_bridge 替身覆盖坑）；`test_all_api.py` 用收集期排除 + `MUYUN_RUN_EXTERNAL=1` 显式启用。
- **未定性已定性**：`test_indicator_math_actually_ran` 的失败与 HOME 无关——fixture `subprocess.run(text=True)` 未指定 `encoding=`，中文 Windows 下父进程按 GBK 解码子进程 UTF-8 输出，汇总行正则匹配不到。修法 = 显式 `encoding="utf-8"`。
- 验收证据：裸 `pytest -q` = **744 passed / 0 failed / 2 skipped / 1 deselected（70s）**（= 架构师隔离基线 741 + 3 个时间依赖修复转绿）；跑前跑后真实 `~/.muyun` 文件数 1047 不变、无任何更新时间晚于标记文件的文件；data_sources mock 用例 30/31 照常收集（structure 1 项被 external 标记排除）；`MUYUN_RUN_EXTERNAL=1` 下 test_all_api 15 项可收集（未实跑）。
- 3 个修复只固定时钟/名字/日期，业务断言零改动；跨日期稳定机制 = 冻结类 / patch 名字实际所在模块 / `evaluate(today=)`。

### 2026-09-24 / Codex / 调研与计划交付

- 当前负责人：本任务仅负责调研与计划；M1–M6 尚无实施负责人。
- 已发现：无 `pyproject.toml`/pytest 配置、测试说明漂移、会话快照非原子写、读取只验证 JSON 语法而未验证数据结构、同主题报告以分钟命名可覆盖。
- 原代码基线：排除 `test_all_api.py` 与 `test_check_all_sources_structure`，临时目录隔离 HOME / USERPROFILE 后，**741 passed / 3 failed / 2 skipped / 1 deselected，114.02 秒**。这不是含真实外源的全量验收。
- 失败项：恐慌指数 STALE 回退、恐慌指数快照日期、PlanGuard 止盈路径，详见配套技术交接。未改断言或业务逻辑来消除失败。
- 范围纠正：用户明确只要计划后，已撤回试作的 `pytest.ini`、`tests/conftest.py` 修改与新增隔离测试，结束后续测试运行；无 `src/` 改动、无 commit/push。试作测试配置的结果不作为产品基线，教训见技术交接。
- 交接：后续执行者实施时更新上表及记录；本任务交付的是计划，不是 M1–M6 的完成声明。
