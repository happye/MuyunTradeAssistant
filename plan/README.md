# 暮云持续迭代计划

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
6. **持仓**：当前 `_save` 发现外部修改只警告仍覆盖，多数 mutator 未传播 bool。M5 必须端到端设计，不能只在 `_save` 加一个 return 就声称修复。

## Agent 协作约定

- 接任务先读本文件、AGENTS、工程纪律技能与相关源码；旧文档只作线索。
- 在执行记录写清负责文件、状态与验收命令。避免同时编辑 `main.py` / `start.py` / AGENTS。
- 可并行的后续任务：M4 review 计算提取、M5 持仓调用链设计、M6 安装诊断设计；每项须明确文件所有权，与其他 Agent 的改动兼容，不回退他人修改。
- 修复先给回归红灯，转绿后扫同类点，教训进入 `.learnings`；运行与文档证据同批交付。
- 有全局状态改动必须跑完整离线回归。真实数据接口/AI 验证单独记账，不能用它们的抖动掩盖离线失败。

## 执行记录

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
