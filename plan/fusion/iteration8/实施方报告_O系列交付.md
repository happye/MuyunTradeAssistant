# 实施方报告：O0–O2 交付（Y1–Y4 缺口闭合）

> 2026-10-02｜Claude Code 实施会话｜基线 HEAD `4eb5c1b`（产品主提交 `154b556`，v0.8.26）
> 角色：Claude Code=实施方；本报告不代审批，待 Codex 架构师独立验收。
> 交付建议版本 **v0.8.27**；决策表 **v4**；影子协议 **shadow_v10 候选**。

## 0. 交付摘要

| 卡 | 病根 | 修法落点 | 状态 |
|---|---|---|---|
| O0/Y1 | `portfolio.py:441-442`（读取失败+旧零投影→NONE/0）；`:445-452`（PARTIAL 无幸存 lot→NONE"无持仓记录"） | 两分支改 UNKNOWN/权重 None/数量 None（待对账、不反推数量） | ✅ 红→绿 |
| O0/Y2 | `start.py` live_all 与 `src/cli/main.py` analyze_portfolio 清单只来自投影、读快照前判空 | 清单先经同一 `RequestAccountFacts` 快照形成（账本确定有仓 ∪ 投影代码，归一去重）；`holding_entries()` 新增；异常报告不完整/待对账 | ✅ 红→绿 |
| O1/Y3 | `shadow_diff.py` `_cutoff_in_future` naive 带时刻只比日期，当天未来 naive 可过资格 | 解析收敛至新 `src/core/source_time.py` 纯函数（两门共用）；naive 按 Asia/Shanghai 实际时刻比较；估值门 `as_of` 参数化；**shadow_v10 候选** | ✅ 红→绿 |
| O2/Y4 | `decision_policy.py` 行8：LONG+HELD+weight=None+budget=True → ADD | 行8 加 `not known` 分支 → HOLD/None（同 v3 行6 口径）；已知权重 ADD 保留；**表 v4** | ✅ 红→绿 |

## 1. 逐条回答 R14 Y1–Y4

### Y1（P1，异常被当明确空仓）——已闭合

**修复**（[portfolio.py](../../../src/data/portfolio.py) `context_for`）：
- 病根1：`_read_failed` 且投影 `quantity_fact` 存在且 `quantity_held==0` → 原 `POSITION_NONE/0/0.0`；现 `POSITION_UNKNOWN / weight=None / quantity=None`，原因"账本读取失败；旧数量投影为 0 不足以证明空仓——待对账"。
- 病根2：`pos is None` 且 `ledger_q==0` 且 `_ledger_partial` → 原 NONE"无持仓记录"；现 UNKNOWN / None / None，原因"账本存在隔离事件（部分不可读）——无剩余持仓记录不足以证明空仓，待对账"。
- 不编造数量：两分支 `quantity=None`——Y1 无剩余 lot 场景不得反推 100 股恢复（R14 要求）。

**勿伤核对**（全部有回归锚）：
- 有效空账本（健康快照、无持仓、无投影）→ 明确 NONE/0/0.0（`test_valid_empty_ledger_is_explicit_none`）。
- RATIO_ONLY 无账本 → HELD/.1 比例直通（`test_ratio_only_without_ledger_still_held`，R14 已接收正例）。
- PARTIAL 仍幸存正数量 lot → HELD/quantity=100/权重冻结+待对账（`test_partial_with_surviving_lot_holds_and_freezes_weight`，R14 已接收）。
- 持仓库损坏 → UNKNOWN（G01，回归）。
- 坏行隔离不改原账本：PARTIAL 场景断言 context_for 前后账本文件字节不变。

### Y2（P1，整只持仓被批量遗漏）——已闭合

**修复**：
- `RequestAccountFacts.holding_entries()`（新）：**构造时快照已读**——清单=账本确定有仓（quantity>0）∪ 兼容投影代码，`_normalize_stock_code` 归一去重；投影顺序优先、账本独有追加；账本独有条目（`HoldingEntry.from_ledger=True`）只带代码，成本/比例/建仓日期一律 None（不持久化、不伪造）；`incomplete=True`（读取失败/持仓库损坏）时调用方不得输出"当前无持仓"。
- `start.py` live_all：清单改 `holding_entries()`；空清单+incomplete → "持仓清单读取异常……待对账（不当作空仓）"；有效空仓 → "当前无持仓记录"（既有语义）；账本独有逐只卡带"[账本持仓·投影缺失——成本/比例未知]"标注。
- `src/cli/main.py` analyze_portfolio：同口径接线；汇总表三处 `current_ratio` None → 显示 "-"（不伪造 0%）。
- 快照纪律：同批恰读 1 次（`test_y2_repl_la_reaches_ledger_only_holding` 计数断言 `snapshot==1`；`test_y2_next_request_reads_new_version` 断言同请求重复取清单零读+账本追加事件后新请求读到新 `account_version`）。

**验收硬点核对**（真 REPL，非"输出含代码即过"）：
- 账本独有 100 股到达**同次调用**：影子记录 `security_id==600519` 且 `account_version`==快照版本（`test_y2_repl_la_reaches_ledger_only_holding`）；cli 直调路径捕获终态包 `position_state=="HELD"`+账户版本（`test_y2_cli_analyze_portfolio_reaches_ledger_only`）。
- 混合不漏不重：投影独有（000001）+ 重叠（600519）+ 账本独有（000688）三只同批全部有影子记录、版本一致、逐只卡输出（`test_y2_repl_la_mixed_sources_no_leak_no_dup`）。
- 异常不输出"当前无持仓"：`test_y2_repl_la_read_failure_reports_incomplete`。
- 硬退出不吞：既有 N0 竖向/影子测试全绿（m0 系列 EXIT 方向不受本轮影响）。
- 成本不伪造：HoldingEntry 账本独有字段全 None + 显示标注。

**同类扫描与最小接线**（所有权条款："其它入口只在同类扫描发现相同清单前置时最小接线"）：

| 同类点 | 裁决 |
|---|---|
| `start.py:1214` scanner auto_exclude_holdings 排除集 | 已接线——排除集改 `holding_entries()`（账本独有持仓不进扫描推荐；ISS-078 留痕保留） |
| `src/cli/main.py` scanner 排除集（同模式） | 已接线（同上） |
| `src/chat/tools.py` 扫描排除集 | 已接线（同上） |
| `src/chat/tools.py get_portfolio`（"当前无持仓记录"） | 已接线——投影空+账本有仓 → 如实列出账本独有代码并标注待对账；读取异常 → 未对账提示 |
| `src/cli/today_service.py:64` 持仓卡清单 | **不改**——today 账户事实区已逐只显示账本持仓股数（J2 account 参数），谎报空仓已被兜住；损坏提示已有（F7 P2-11）；持仓卡合并属展示增强，登记不做 |
| `src/cli/main.py` pos list / pos plan all / event 计数（:2301/:2703/:4919/:5005） | **不改**——管理命令/计数语义即投影文件视图，登记 |
| `src/chat/tools.py analyze_industry` 持仓定位（:628） | **不改**——账本独有股不进图谱定位；`current_ratio=None` 传入下游行为未验证，登记 |
| TUI/Web/scanner_engine 内部展示/排除 | **不改**——另行所有权，登记同模式可复用 `holding_entries()` |

### Y3（P1，影子同日未来 naive 可过资格）——已闭合

**修复**：
- 新 [src/core/source_time.py](../../../src/core/source_time.py)：`parse_source_time`（严格解析：aware 归一上海/naive 显式上海/纯日期上海日/非法 None）+ `source_time_in_future`（时刻精度按实际时刻、日精度按上海日期、不可解析不判未来）。
- 估值门（portfolio `_qualified_weight`）：实现改用共享解析；`as_of` 关键字参数（缺省当前上海墙钟——旧调用方零变化）；等价偏移的 as_of 归一上海。
- 影子门（shadow_diff `_binding_eligibility`）：`_parse_cutoff`/`_cutoff_in_future` 改共享实现薄包装（`_cutoff_in_future` 名字保留，既有引用稳定）；naive 带时刻从"只比日期"改为按上海实际时刻——当天未来 naive 判 `quote_cutoff_future`。
- 采集时间仍只诊断（quote_fetched_at 不入资格门——v8 合同不变）。

**验收矩阵**（[test_o1_time_gate_matrix.py](../../../tests/data_sources/test_o1_time_gate_matrix.py)，估值/影子两侧对同一输入断言、固定 as_of 无午夜偶发）：当天未来 naive / 当天过去 naive / 等价偏移 aware / 上海午夜（前一秒与恰等）/ 捕获 as_of 以 UTC 表示 / 合法纯日期 / 未来日期（evidence 与 quote 分列）/ 缺失 / 非法日历 / 非法时刻 / 尾部垃圾 / 固定时钟翻转 / 位置参数向后兼容。全部转绿。

**协议**：`SHADOW_DERIVATION_VERSION = "shadow_v10"`（候选）；v9 及更早独立桶保留不追认（既有分桶测试回归）。既有回归保持绿：test_n2_time_gate 全部（源日期/预取/120s 缓存/仅采集时间去重/午夜边界）、test_m1_quote_time 全部。

### Y4（P2，LONG 行8 未知权重仍 ADD）——已闭合

**修复**（[decision_policy.py](../../../src/core/decision_policy.py) 行8）：`held` 分支最前加 `if not known: HOLD/None`（"预算已知不弥补权重缺口"，与 v3 行6 同口径）；`budget_available=True`+已知权重仍 ADD（ELIGIBLE）；`budget=False/None` → HOLD（既有）；未持有 OPEN 候选（既有）；硬退出（行1）先于行8——冻结不回退退出方向（回归锚）。

**版本登记**：`DECISION_TABLE_VERSION = "v4"`，注释登记受影响行=行8；`position_state=None` 旧推导路径 held 蕴含 confirmed 非 None——行8 not known 分支不可达，旧行为零变化（兼容锚 `test_legacy_path_*`）。MID 行5/6 未顺改（对照矩阵回归）。

**诚实边界**（照 R14 披露）：Y4 是公开策略接口可复现边界；当前 shadow 捕获不产生 LONG 质量/买区/预算 True 组合——本报告不宣称实盘事故，测试注释同口径。

## 2. 测试统计与 462/423 口径解释

**红灯先行**：三个新测试文件先红后绿（O0 7红/4锚绿 → 修后转绿；O1 9红/1绿 → 转绿；O2 3红/12绿 → 转绿）。guard 审查后新增 2 个回归（P0 批次中止、P1-2 PARTIAL incomplete），O 批新增合计 **39 个测试项（O0 14 / O1 10 / O2 15，参数化展开）**。既有 4 文件的 shadow_v9/v3 字面断言随协议升位同步登记为 v10/v4（合同升级，非改测试凑绿——版本沿革见 shadow_diff docstring 与 decision_policy 注释）。

**462 vs 423 差异解释**（R14 遗留口径问题）：差异源于**收集文件集不同**，非测试失败差异——
- 架构师 R14：30 文件 = **423 passed**。本轮同 HEAD 用架构师 runner（review_test_runner.py）复跑 30 文件清单：**423 passed 精确复现**（保护哈希不变）。
- N 批实施方：34 文件 = 462（R13 的 27 文件集 + m0/m1 + iss089/batch_quotes + n0/n1/n2）——34 文件集与架构师 30 文件集相差 4 个文件（实施方清单含 iss089/batch_quotes/n0/n1/n2 中的 4 个，架构师清单含 27 文件中实施方未列的部分），各文件用例数不同导致 462-423=39。
- 本轮 O 批交付口径：**33 文件 = 462 passed**（=架构师 30 文件 + O0/O1/O2 三个新文件；423 + 39 新增测试项）。精确命令与收集文件清单见 [TARGETED_TEST_RESULTS_O.txt](TARGETED_TEST_RESULTS_O.txt)（COLLECTED_FILES=33 + COLLECTION 全列）。注意：此 462 与 N 批实施方报告的 462 数值巧合、清单与树均不同。

**全量**（隔离离线，HOME 重定向+保护哈希；实施方 runner 无网络阻断，如实表述）：**1521 passed / 2 skipped / 1 deselected**（基线 1482 + O 批 39；与目标回归增量 462-423=39 交叉一致）——见 [FULL_TEST_RESULTS.txt](FULL_TEST_RESULTS.txt)。guard 修复前的第一轮全量（1518+1failed，暴露 chat 测试单例顺序依赖）留档 [FULL_TEST_RESULTS_R1_pre_guard_fix.txt](FULL_TEST_RESULTS_R1_pre_guard_fix.txt) 不删。7 保护文件（portfolio.yaml/.bak + knowledge/index/*）前后哈希与文件集合不变（PROTECTED_*/KNOWLEDGE_* 均 True）。

## 3. 版本与文档同步

- 版本五处：start.py `VERSION`、cli/main.py banner + `--version`、README 状态段 + 版本历史、AGENTS 当前版本段 → **v0.8.27**。
- 决策表 v3→v4（decision_policy 注释登记）；影子协议 shadow_v9→shadow_v10 候选（docstring v10 段登记）。
- CACHE_VERSION 未动（评分域零改动）。
- 未新增 logger.warning 文案（holding_entries 内沿用既有 warning 模式、无新人话映射需求——plain_errors/报错速查手册无需同步）。
- 实施账本：[EXECUTION_RECORD.md](EXECUTION_RECORD.md)。
- 不覆盖 R13/R14 审批探针结果：架构师 TARGETED_TEST_RESULTS.txt / ACCEPTANCE_PROBE_RESULTS.* / R13_REPLAY_RESULTS.txt 原样未动；实施方结果另存 TARGETED_TEST_RESULTS_O.txt / FULL_TEST_RESULTS.txt。

## 4. 红线自查

- 未扩供应商、未建 NAV 系统、未升级依赖、未重写 main、未修真实账户。
- 工作树脏文件（用户本地状态 portfolio.yaml 等 + 架构师分层文档/审批资产）未回滚未覆盖；commit 只含本批相关文件。
- 零付费 AI、零网络（测试全程行情替身/离线；竖向测试走规则 fallback）。
- 无未来信息注入回测（本批不涉回测路径）。

## 4b. guard 审查发现与修复（第一轮 guard，1×P0 + 2×P1 + 3×P2）

| 级别 | 发现 | 修复 |
|---|---|---|
| P0 | live_all 循环变量 pos→e 与 `except Exception as e` 撞名——单只普通异常时 handler 内 `e.stock_code` 抛 AttributeError 并从 handler 传播，**整批中止** | handler 改 `as ex` + 注释锁死；新增回归 `test_y2_la_single_failure_does_not_abort_batch`（第一只抛 ValueError，断言失败回执+后续持仓仍分析） |
| P1 | HoldingEntry frozen 与 analyze_portfolio 既有内存观察量更新（`pos.high_since_entry = ...`）冲突——卖出信号触发时 FrozenInstanceError 被吞→误报"分析失败"+汇总重复行 | HoldingEntry 去 frozen（注释说明与 PositionRecord 同语义；仅显示条目，无持久化副作用） |
| P1 | holding_entries 的 incomplete 漏 `_ledger_partial`——PARTIAL 下 la 经新清单路径仍输出"当前无持仓"（正是本卡要禁的输出） | incomplete 补 `_ledger_partial`；新增回归 `test_y2_repl_la_partial_ledger_reports_incomplete` |
| P1(证据) | 第一轮全量含 1 failed（chat 测试单例顺序依赖，修复前树） | 旧全量证据留档 `FULL_TEST_RESULTS_R1_pre_guard_fix.txt`；修复后按最终树重跑全量另存 |
| P2 | 新增 logger.warning"投影持仓清单读取失败"未三处同步 | plain_errors.py WARNING_PATTERNS + docs/报错速查手册.md 同 commit 补条目 |
| P2 | 账本独有持仓每次 la 必打误导性"观察量未落盘:持仓文件被外部修改"告警（其投影必然无记录） | from_ledger 条目跳过 record_analysis_observation（record_proposal 保留——已核实对无投影股安全返回 None） |
| P2(存量) | 数据全失败分支 append 4 元组 vs 汇总 5 元组解包（HEAD 既有，非本批引入） | 顺手对齐 5 元组（同一循环体内一行） |

guard 复核结论：**可合入**（六处修复逐一确认、无新引入问题；一处 docstring nit 已顺手补——holding_entries Returns 说明补"/账本 PARTIAL"）。

## 5. 剩余披露

1. today 持仓卡清单未合并账本独有股（账户事实区已如实显示；展示增强登记不做）。
2. chat analyze_industry 持仓定位、TUI/Web 同类点未接线（登记，另行所有权）。
3. M2 生产仍未放行（O 批完成不改 K1/capture_only/M2 状态——按 DELIVERY_PLAN，修正观察合同冻结后架构师给放行结论）。
4. 真实数据只读验证：当前真实账户为 RATIO_ONLY（无数量账本），7 只投影持仓在 la/清单/ctx 行为与修复前零差异——Y1/Y2 合同在用户建立数量账本后生效（账本独有持仓进入 la、异常不误报清仓）；本次交付不改变现有用户可见输出。
5. 测试计数口径：O0 14 / O1 10 / O2 15（参数化展开项合计 39）。
6. 本报告测试数字以 runner 落盘文件为准。
