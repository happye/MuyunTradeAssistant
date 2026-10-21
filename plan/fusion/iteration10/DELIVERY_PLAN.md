# 第十轮：T05-D 旧并行 executor 退出界（ISS-120 剩余 4 处）

更新 2026-10-21。本文件是第十轮唯一合同与执行入口；第九轮历史合同与证据仍在 [iteration9/DELIVERY_PLAN.md](../iteration9/DELIVERY_PLAN.md)，不重算。

## 角色与分工（本轮）

- 用户 2026-10-21 指派 **Claude Code** 接手迭代开发，并授权其兼任架构师主导方案；方案必须落盘并同步推送（多机接续）。
- 本机工作区：`C:\Users\HanPeiyi\SVN\Muyun`，默认直接 main，Claude Code 主控负责 Git。
- 分工按 `CLAUDE.md`「Claude Code role routing」：muyun-coder 写测试/实现，muyun-runner 跑测试，code-quality-guard 做一次高风险独立复核（本卡属于并发/超时边界），主控整合、验收、提交。
- 原 Codex+DSH 流程（RESUME「DSH恢复身份」）保留为另一台机器的入口，本轮不使用。

## 卡片 T05-D

| 项 | 内容 |
|---|---|
| 对应 | G7 / T05；ISS-120「同类剩余4处」 |
| 消费者路径 | ① `bz`/笨总评分 → `get_data_summary` → `get_industry_metrics`（`src/core/benzong/data_provider.py`，外层 1 个 + 内层 2 个 executor）；② 扫描/全市场快照 → `MarketCache._fetch_sina_market`（`src/scanner/market_cache.py`，10 并发） |
| 问题 | `ThreadPoolExecutor.shutdown(wait=False)` 只让调用方不等，但卡住的工作线程会在解释器退出时被 `concurrent.futures` 的 atexit join。数据源超时后，`start.py`/CLI 退出可被拖住最长约 150s（行业）或 30s/页（新浪）。第九轮已对 3 个单槽 helper 证明并修复同类问题，本卡处理剩下的 4 处。 |
| 用户收益 | 行业景气或新浪快照超时降级后，退出 REPL/CLI 不再被悬挂的后台请求拖住。 |
| 修改边界 | `src/data/timeout_call.py`（新增并行版 daemon helper）、`src/core/benzong/data_provider.py`（industry_metrics 内 3 处）、`src/scanner/market_cache.py`（新浪并行 1 处）、`tests/core/test_t05_timeout_exit.py`（新增退出 case）、新增 helper 单测文件；文档：ISSUES ISS-120、本文件、STATUS/RESUME/MILESTONES 的 T05 行。 |
| 不改 | 默认超时预算（行业 150s、新浪总 45s、单页 30s）、降级文案语义、返回三态（matched/not_matched/failed）、超时即放弃整份新浪快照并换源的既有裁决、评分/阈值/CACHE_VERSION、账户、网络来源。不处理晚到写缓存与 Baostock 会话重叠（ISS-120 保留限制）。 |
| 前置 | 本机 Python 3.11+ 与 `.venv`（2026-10-21 本机缺 Python，需用户批准安装）。 |

### 设计

1. `src/data/timeout_call.py` 新增 `run_parallel_with_deadline(fns, *, max_workers, timeout)`：
   - 起 `min(max_workers, len(fns))` 个 daemon 线程从任务队列取活；结果按输入顺序返回列表。
   - 总预算到点抛 `TimeoutError` 子类，带 `completed` 计数（供新浪日志「已完成 N/73 页」）；`None` 无限等，负数/NaN 按 0（与 `call_with_timeout` 一致）。
   - 任一 `fn` 抛异常 → 原实例在调用线程重抛（与旧 `future.result()` 一致）。
   - 不 join、不全局登记；迟到结果只进本次调用的队列。线程不能强杀，副作用可能继续（同 `call_with_timeout` 限制说明）。
2. `get_industry_metrics`：外层改 `call_with_timeout(_pull, timeout=INDUSTRY_METRICS_TIMEOUT_S)`；内层三节/两节改 `run_parallel_with_deadline(..., timeout=INDUSTRY_SECTION_TIMEOUT_S)`。预算抽成模块常量，默认值不变（150.0）。`_BridgeMiss` 仍按原实例透传为 not_matched，超时仍为 failed。
3. `_fetch_sina_market`：改 `run_parallel_with_deadline`（10 并发，`MarketCache.SINA_PARALLEL_TIMEOUT_S = 45.0`），超时日志保留完成页数并返回 None 换源。结果按页序合并（旧为完成顺序，后续去重与列名标准化不依赖顺序）。

### 正常 / 失败 / 迁移

- 正常：所有节/页按时返回 → 输出与旧实现一致（新浪页序更稳定）。
- 失败：单节异常仍由 `_safe_section` 转缺失文本；超时 → failed / 换源，行为与旧一致，但解释器可立即退出。
- 迁移：无持久化格式变化，无缓存迁移。

### 反例与验收命令

先红后绿（铁律 1）：

1. 预算常量抽取（行为不变）先单独落地，使退出 case 能用小预算，红灯只因「退出被拖住」而不是「调用本身阻塞」。
2. `tests/core/test_t05_timeout_exit.py` 新增 4 个子进程 case（默认 skip，需 `MUYUN_T05_EXIT_PROBE=1`）：
   - `industry_outer`：`_pull` 本身挂起（桩 `industry_data.load_chains` 睡 8s），外层预算 0.05s；
   - `industry_chain_sections`：命中手写链，三节桩函数睡 8s；
   - `industry_commodity_sections`：L3 商品关键词路径，两节睡 8s；
   - `sina_parallel`：桩 `requests.Session.get` 睡 8s，`SINA_PARALLEL_TIMEOUT_S` 设 0.05。
   断言沿用现有夹具：调用 ≤1s 返回、语义正确（None/failed）、子进程返回后 1s 内退出、父进程观察窗口有效。旧实现应红在 EXIT ASSERTION。
3. helper 进程内单测（默认运行）：输入顺序、异常原实例重抛、超时与 `completed`、并发上限、`None`/负数/NaN 预算、空输入、线程为 daemon。
4. 验收命令（本机 venv 建好后）：
   - `.venv/Scripts/python.exe -B -X utf8 -m pytest -q tests/core/test_t05_timeout_exit.py <helper单测> -p no:cacheprovider`
   - `MUYUN_T05_EXIT_PROBE=1` 同上，只跑退出 case
   - 全量：`.venv/Scripts/python.exe -B -X utf8 -m pytest -q tests -p no:cacheprovider`，0 failed
5. 同类扫描：`grep -rn "ThreadPoolExecutor" src/` 结果应为 0（`scripts/measure_timeout_overhead.py` 是测量脚本，不在产品路径，记录不改）。

### 资源上限、停止与回滚

- 离线，零网络、零付费 AI、不读写真实账户。
- 停止条件：某站点在旧代码上**复现不出**退出阻塞 → 不改该站点，在 ISS-120 记「未复现」；若 helper 需要改动默认预算或返回语义才能通过 → 停下回到设计。
- 回滚：本卡为独立 commit，`git revert` 即可。

## 卡片 BUG-OW：超配涨幅禁入被展示异常静默绕过（P1）

| 项 | 内容 |
|---|---|
| 对应 | G5（组合约束/纪律）/ T04 质量；笨总教学十「涨幅3-10倍禁入」铁律 |
| 实证 | `src/cli/main.py` `manage_positions` overweight 分支：倍数 ≥3 时第二行 `console.print(...[/red])` 有未配对闭标签，rich 抛 `MarkupError`（2026-10-21 本机实测）；外层 `except Exception` 把它当作「数据获取失败」吞掉并继续激活超配。拒绝路径实际不可达。 |
| 修法 | 去掉未配对标签；收窄 try 只包数据获取与倍数计算，展示与 return 在 try 外，防止同类展示异常再被当成数据失败。数据获取失败「继续但提示人工核实」的既有裁决不变。 |
| 反例 | 新增 `tests/core/test_overweight_rise_guard.py`：倍数 3.5 → 不激活（从磁盘回读 overweight_executed）；倍数 2.0 → 激活；数据异常 → 既有行为。修复前第一例红。 |
| 同类扫描 | ast 遍历 `src/`、`start.py` 所有 `console.print` 字符串参数，用 `rich.markup.render` 校验，列出 MarkupError 位置并按「是否在 try 内改变控制流」分级。 |
| 范围 | 只改 overweight 分支与扫描确认的同文件同类问题；其他文件的发现记入 ISSUES，不顺手修。 |

## 卡片 BUG-IDX：离线全量测试改写受保护的 knowledge/index

| 项 | 内容 |
|---|---|
| 对应 | G7 测试隔离 / 受保护文件约定（RESUME「保护与限制」） |
| 实证 | 干净克隆跑离线全量后 `knowledge/index/config.json` 由 512 维变 5000 维，index.faiss 1.7MB→16.7MB（TF-IDF 兜底重建并 save）。写入链：`tests/core/test_cli_smoke_paths.py::test_pipeline_runs` → `scripts/pipeline.py` `init_engines` → `src/chat/tools.py` `_rag_service.initialize()` → `src/rag/service.py` `_store.save(index_dir)`；以及 `tests/core/test_l2_public_checkpoint.py` 的 `l CODE` → `get_cli_rag_service` → 同一 save。根因：settings 的相对 `./knowledge/index` 无测试覆盖点。 |
| 修法 | 仅改 `tests/conftest.py`：session 级根因隔离（包装 `RAGService.__init__`，index_dir 落在仓库索引时改指临时副本）+ sha256+mtime 兜底守卫（会话结束若被写则失败）。不改 src。 |
| 反例 | 只有守卫无隔离时跑 `test_pipeline_runs` → error「测试改写了受保护的 knowledge/index」；加隔离后 13 passed、mtime 不变。 |
| 登记不修 | ① 生产 `index_dir` 为相对 CWD 路径，从其他目录启动会在别处建索引（产品问题，另卡）；② `src/cli/evidence.py` `_REPORT_DIR` 使测试每次在仓库 `分析报告/analysis/` 新增文件（已 gitignore，非受保护，另卡用同手法隔离）。 |

## 卡片 T04-A（实现复验通过，c7fb92b）：持仓确认账本编排下沉

| 项 | 内容 |
|---|---|
| 对应 | G1（账户事实可信）/ G7 / T04 |
| 现状 | `manage_positions` confirm 数量路径（`src/cli/main.py` 约 2554-2668）在 UI 函数里编排：主意图引用、fill_id 崩溃恢复复用、偏离判定、`AccountService.confirm_fill`、快照前后对比、`pm.apply_quantity_fill` 投影同步。chat 通过 `redirect_stdout` 复用整个 CLI 函数（`src/chat/tools.py` 约 1071），TUI 直接调 CLI（`src/tui/app.py:264`）。账户事实的唯一写入编排长在 UI 层，只能经由捕获输出测试。 |
| 目标 | 编排下沉为 `src/data/account_service.py` 的纯服务函数（PortfolioManager 依赖注入，避免循环导入），返回结构化结果（状态、fill_id、是否恢复态、持股前后、批次成本、投影同步结果、偏离标记）；CLI 只渲染该结果，文案与顺序逐字不变。 |
| 收益 | 账户写入编排可直接单测；chat/TUI 后续可改调服务并自行渲染，去掉 stdout 捕获桥（另卡）。 |
| 不改 | 账本格式、fill_id 规则、`pick_reusable_fill_id`、投影语义、比例路径、提示文案；不迁移旧账本。 |
| 前置 | 先对现有 confirm 数量路径补「黄金输出」测试：正常买入、部分卖出、清仓、DUPLICATE、恢复态、PERSIST_UNKNOWN、投影失败七种，锁住 CLI 输出与磁盘结果；下沉后逐字比对。 |
| 停止条件 | 黄金测试无法在离线隔离下稳定构造某分支 → 该分支不下沉，记录原因；任何文案/顺序差异 → 回滚。 |
| 提交形式 | 单独 commit，message 注明「内部重构无行为变化」（AGENTS 用户可感知条款的纯重构例外）。 |

## 卡片 T04-B：TUI 加仓与 chat 删仓改调非 UI 服务（含 TUI 假成功修复）

| 项 | 内容 |
|---|---|
| 对应 | G2（跨入口一致）/ G7 / T04；ISS-116 登记的 TUI/chat 管理桥 |
| 现状 | `src/tui/app.py` 约 264 行 `asyncio.to_thread(manage_positions, "add", ..., price=0.0, ratio=0.20)` 后**无条件**显示「已加仓」——`manage_positions` 遇到已有持仓/参数非法/保存失败只打印并 `return None`，TUI 拿不到失败信号（假成功）；CLI `console.print` 直接写 stdout，与 Textual 界面冲突。`src/chat/tools.py` 约 1097 行 `remove` 经 `redirect_stdout` 复用整个 CLI 函数。 |
| 用户收益 | TUI 加仓失败时如实显示原因（已有持仓、保存失败等），不再显示假的「已加仓」；TUI 界面不再被 CLI 输出污染。 |
| 设计 | `src/data/portfolio.py` 新增模块级 `add_position_checked(pm, stock_code, name, price, ratio)` / `remove_position_checked(pm, stock_code)`，返回结构化结果（状态：MISSING_CODE / ALREADY_HELD 或 NOT_HELD / INVALID_PRICE / INVALID_RATIO / SAVE_FAILED / ADDED 或 REMOVED；附显示名、总仓位用于 80% 底牌提示）。校验与写入逐行取自 CLI add/remove 分支。CLI add/remove 改为调用服务后按状态渲染，文案逐字不变（TradePlan 草稿引导仍在 CLI）。TUI add 与 chat remove 改调服务并自行渲染；chat remove 文本与原 CLI 输出逐字一致。 |
| 不改 | chat 的 add / plan / overweight（依赖 CLI 内 TradePlan 草稿与 Y/n 交互，另卡 T04-C）；`_watch_pool_touch` 与 `normalize_stock_code` 两个桥；持仓文件格式与 PortfolioManager 语义。 |
| 反例与验收 | ① 先锁黄金：CLI `pos add`/`pos rm` 各分支（缺代码、已有持仓、负价、比例越界、保存失败、成功含总仓位 >80%/未知提示、无价跳过草稿提示；删仓缺代码、无持仓、保存失败、成功）输出与磁盘结果，以及 chat `manage_portfolio(action="remove")` 返回文本——须先在 HEAD 通过。② TUI 红灯：对已有持仓的股票执行加仓，旧实现显示「已加仓」（红），修复后显示失败原因（绿）；并断言 TUI 路径不向 stdout 写 CLI 输出。③ 服务单测覆盖各状态。④ 全量 0 failed。 |
| 停止与回滚 | 黄金在 HEAD 不能稳定通过的分支不改；CLI/chat 任何文案差异 → 回滚。独立 commit，可 revert。 |

## 候选队列（未开工，按价值排序；每张开工前补全卡片字段）

1. **T04-C chat add/plan/overweight 去 CLI 桥**：需先把 `_try_attach_trade_plan`（TradePlan 草稿与 Y/n 交互）拆成「生成草稿（服务）+ 确认交互（各入口）」，再让 chat 改调服务。
2. **BUG-FEE 负费用未校验**：REPL `pos confirm ... --fee -1` 只在 `FillInput(fee ge=0)` 处抛 pydantic ValidationError（start.py 约 587-596 只校验 --qty），用户看到原始异常。入口层补校验并给人话提示（同步 plain_errors 与报错速查手册）。
3. **生产 RAG 索引路径相对 CWD**（ISS-123 登记）：`./knowledge/index` 随启动目录漂移；改为基于仓库根解析，或显式配置。需确认对 Web/TUI/脚本入口的影响。
4. **测试写仓库报告目录**（ISS-123 登记）：`src/cli/evidence.py` `_REPORT_DIR` 用 conftest 同手法隔离。
5. **T04 大函数拆分**：`start.py` `parse_input`（约 542 行）/`run_cli`（约 501 行）命令分发表化——风险高，需先有分发黄金测试，不急。

## 执行记录

| 日期 | 事项 | 结果 |
|---|---|---|
| 2026-10-21 | 合同落盘 | 本文件；实施等待本机 Python 环境 |
| 2026-10-21 | T05-D 红 | 修复前 4 个新子进程退出 case（industry_outer / industry_chain_sections / industry_commodity_sections / sina_parallel）全部红在 EXIT ASSERTION（调用已返回、子进程 1s 后仍存活）；原 4 个 case 绿 |
| 2026-10-21 | T05-D 绿 | 新增 `ParallelTimeout`/`run_parallel_with_deadline`（`src/data/timeout_call.py`）；`get_industry_metrics` 外层 `call_with_timeout`、内层两处并行 helper，常量 `INDUSTRY_METRICS_TIMEOUT_S`/`INDUSTRY_SECTION_TIMEOUT_S`=150.0；`_fetch_sina_market` 改并行 helper，`SINA_PARALLEL_TIMEOUT_S`=45.0。`MUYUN_T05_EXIT_PROBE=1` 下 8/8 退出 case 通过（exit_after 约 0.13–0.30s）；定向 `tests/core/test_timeout_call_parallel.py` + `tests/core/test_t05_timeout_exit.py` 46 passed / 8 skipped（8 skipped 为默认跳过的退出 case）；helper 单测 18 项。默认预算、三态返回、告警文案不变 |
| 2026-10-21 | T05-D 扫描与复核 | `src/` 中 `ThreadPoolExecutor`/`as_completed` 为 0（仅 `akshare_client.py:54` 一处注释）；所有 `threading.Thread` 均 `daemon=True`；`scripts/measure_timeout_overhead.py` 为测量脚本不在产品路径。code-quality-guard（opus）结论「可合入」，3 个 P2 已处理。新浪超时后不再开始剩余页（优于旧实现）。保留限制：线程不能强杀，已开始的请求/缓存写可能晚到；Baostock 会话重叠未处理。全量3184 passed/0 failed/11 skipped/1 deselected（656.61s）；commit `ac6ce8a` |
| 2026-10-21 | BUG-OW 红 | 修复前新测试 `test_rise_3x_or_more_rejects_overweight` 红；日志「超配涨幅检查失败(600519): closing tag '[/red]' ... doesn't match any open tag」，输出「拒绝超配」后紧接「超配已激活」（登记 ISS-122） |
| 2026-10-21 | BUG-OW 绿与扫描 | 修复点 `src/cli/main.py` 约 2775-2791（补开标签、收窄 try、展示与 return 移出 try）；新文件 `tests/core/test_overweight_rise_guard.py` 3 passed，连同 overweight 相关测试共 42 passed。ast 检查 `src/` 与 `start.py` 共 544 处 `console.print`/`rprint` 首个字符串参数，MarkupError 0 处；局限：变量传入的文案扫不到。未新增/改动 `logger.warning`。全量3184 passed/0 failed/11 skipped/1 deselected（656.61s）；commit `c67b85e` |
| 2026-10-21 | BUG-IDX | 仅改 `tests/conftest.py`（根因隔离 + sha256/mtime 守卫）；先红后绿；全量 3184 passed/0 failed/11 skipped，索引未被改写；commit `28b7e68` |
| 2026-10-21 | T04-A 黄金 | 13 项锁定 confirm 数量路径输出与磁盘结果；commit `61a212f` |
| 2026-10-21 | T04-A 下沉 | `confirm_quantity_from_proposal` + `QuantityConfirmOutcome`；复核 3 个 P2（异常时序/入账后丢行/投影崩溃状态）修复，新增 3 项异常时序黄金先在 HEAD 通过；黄金 16、服务 10、回归 96 passed；全量 3210 passed/0 failed/11 skipped（584.52s）；commit `c7fb92b` |
