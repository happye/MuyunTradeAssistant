# 实施方交接文档：第八轮 O0–O2（Y1–Y4 补齐）

> 交接时点：2026-10-02｜写给学生：下一个实施会话（Claude Code）
> 交接人：本轮实施会话（完成 N0–N2 交付并经 R14 分项接收）
> 角色铁律：Claude Code=实施方，Codex=独立验收；不互改身份，不代审批。

## 一、当前状态（先核对再动手）

- **HEAD `2b98842`**（N批 feat `154b556` + docs `5c0f51d`/`2b98842`），产品版本 **v0.8.26**，决策表 **v3**，影子协议 **shadow_v9**。
- **R14 已分项接收 N0–N2 的已验证修复**（[R14](R14_ACCEPTANCE.md)）：R13 的 17 条反例全部转绿（R13_REPLAY_RESULTS）；M2 原型 25 项断言接收。K1 不冻结、M2/K2b 生产不放行、capture_only 不变。
- **已接收范围不要再重做**：策略显式消费 ctx；单股/chat 账本有仓无投影退出方向；快照抛错→UNKNOWN；有幸存持仓的 PARTIAL 冻结权重；legacy 臂来自规范包且 execution 同枚举；未知生成版本降级；MID 行5/6；旧桶 MID/LONG 独立计数；估值门严格时间解析/NAV 偏移/finite/预取 fetched_at/kline_only 标注。
- **架构师留的统计口径问题（交付时必须解释）**：同一 30 文件目标回归架构师 runner 得 **423 passed**（381+42），实施方报 462——差 39。原因基本确定是**收集文件集不同**（实施方联合跑实际列了 34 个文件：R13 的 27 + m0/m1 + iss089/batch_quotes + n0/n1/n2；架构师按其 30 文件清单）。O 批交付须附**精确命令+收集文件清单**并同口径复跑，不作策略门槛。

## 二、本轮任务（O0–O2，全部 READY；建议交付 v0.8.27）

任务卡：[DELIVERY_PLAN](DELIVERY_PLAN.md)；审批：[R14](R14_ACCEPTANCE.md)。**一次同 HEAD 联合交付**；先把 R14 反例搬入正式产品回归（红）→ 修转绿 → 查相邻分支 → 联合同 HEAD 回归。禁止扩供应商/建 NAV 系统/升级依赖/重写 main/修真实账户。

### O0｜异常账户判定与批量持仓集合（Y1/Y2，P1）

- **Y1 病根锚点**：`src/data/portfolio.py` `RequestAccountFacts.context_for` 的 `_read_failed` 分支——投影 quantity_fact 存在但 quantity=0 + 快照抛错 → 现在返回 NONE/0（异常源用旧零投影证明了空仓）；PARTIAL 且无幸存 lot → 现在落「无持仓记录」NONE。**修法方向**：异常源（读取失败/PARTIAL 无法证明无仓）→ UNKNOWN/权重 None+待对账原因；有独立成立的正数量证据保留持仓与退出方向；不能反推丢失事件的数量（Y1 无剩余 lot 时不得编造 100 股恢复）。有效空账本明确 NONE、RATIO_ONLY 无账本 HELD 正例保留（R14 已接收，勿伤）。
- **Y2 病根锚点**：`start.py` live_all（~1702–1716）与 `src/cli/main.py` analyze_portfolio（~408–416）——批量清单只来自 `pm.list_positions()`（投影），账本独有持仓在**读快照之前**就「当前无持仓记录」返回。**修法方向**：请求先经同一 RequestAccountFacts 快照形成持仓代码集合（账本确定有仓 ∪ 兼容投影代码，规范化去重），逐股 context_for；无投影股只带显示用临时元数据（不持久化假成本/比例/建仓日期）；异常无法列全持仓时报告不完整/待对账（不输出「当前无持仓」）；不自动补真实投影。
- **验收硬点**：真 REPL `la` 账本独有 100 股到达**同次调用**的分析/终态包/输出（捕获 ctx/终态/账户版本，不是输出含代码即过）；硬退出不吞、成本不伪造；snapshot==1 真断言+下一请求新版本；today/建议/摘要扫同类投影清单或旧比例判据（给清单+最小回归）。坏行隔离不自动改原账本。
- 所有权：portfolio.py 请求级读侧、start.py live_all、cli/main.py analyze_portfolio 及测试；其它入口只在同类扫描发现相同清单前置时最小接线。

### O1｜估值与影子时点资格一致（Y3，P1）

- **Y3 病根锚点**：`src/core/shadow_diff.py` `_cutoff_in_future`（~313–327）——naive 时刻仍只比日期，当天未来 naive 行情可过资格。
- **修法方向**：收敛解析规则为小型纯函数供估值门（portfolio `_parse_source_time`/`_qualified_weight`）与影子共用（不建新架构层）；naive 按 Asia/Shanghai 时刻比较、aware 按实际时刻、纯日期按上海日期；非法/缺失/垃圾不获资格；`as_of` 参数化（可重复测试，固定时钟避免午夜偶发）；采集时间仍只诊断。
- **协议**：资格语义变更升 **shadow_v10 候选**（v9 及更早独立桶保留不追认）；与 O2 表 v4 共同验收。原 M1 回归（源日期/预取/120s 缓存/仅采集时间去重）保持绿。
- **验收矩阵（估值/影子两边对同一输入断言）**：当天未来 naive、当天过去 naive、等价偏移、上海午夜、捕获 as_of 以 UTC 表示、合法纯日期、未来日期、缺失/非法/尾部垃圾。

### O2｜LONG 未知权重边界（Y4，P2）与交付证据

- **Y4 病根锚点**：`src/core/decision_policy.py` 行8（~293–298）——LONG+HELD+weight=None+VALID+质量/买区合格+budget=True → 现在 ADD。**修法**：行8 同样冻结未知权重新增为 HOLD/None+解释缺口；已知权重+budget=True 仍 ADD；硬退出/减仓方向不回退；规则版本 **v4**（登记受影响行8 及不传三态的兼容/迁移），不顺改 MID 行5/6。
- **诚实边界**：Y4 是公开策略接口可复现边界，当前 shadow 捕获不产生 LONG 质量/买区/预算 True 组合——不得宣传成实盘事故。
- **验收**：MID/LONG 分别覆盖 HELD/NONE/UNKNOWN × 权重已知/未知 × 预算 True/False/None 的有意义组合（含 R14 反例与已知权重正例）；检查有序规则先后（行8 不覆盖硬退出）。

### 联合交付要求（照抄任务卡，逐条落实）

同 HEAD 精确命令；收集文件/用例清单（解释 462 vs 423）；目标与隔离离线全量 passed/skipped/deselected；7 保护文件前后哈希与集合；网络阻断证据；版本五处同步（v0.8.27：start.py:32、cli/main.py banner+--version、README 状态段+版本历史、AGENTS 当前版本段）；iteration8/EXECUTION_RECORD.md + 实施方报告（逐条回答 Y1–Y4）；**不覆盖 R13/R14 审批探针结果**（修复结果另存，如 iteration8/FULL_TEST_RESULTS.txt）；全量 runner 参照 `plan/fusion/iteration6/impl_full_test_runner.py`（输出路径改 iteration8）。

## 三、工作区与协作纪律（踩过的坑）

- **保护他人改动**：工作树脏文件=用户本地状态（portfolio.yaml、knowledge/index/embedder_metadata.json）+架构师分层文档（STATUS/RESUME/MILESTONES/AGENTS/README/.learnings 的 R14 同步、iteration8/ 未跟踪审批资产）——**不回滚不覆盖**；iteration8 架构师资产按 iteration7 先例可在交付时归档落库（commit 信息注明归属原样入库），或留给架构师——看当时工作树状态定。
- **每卡 commit 前必起 code-quality-guard**（用户指示）；R14 之前的 guard 战绩：批量分支缺 import（UnboundLocalError 全灭）、午夜窗口硬编码测试、scanner/TUI/Web 同类点漏扫——同类扫描要真扫。
- **测试纪律**：patch 前预导入真实模块（LRN-20260925-015）；持久化路径注入临时根并断言（HOME 隔离不足——架构师明确"HOME隔离本身不足"）；测试替身签名与真实签名同步（LRN-20261002-L012）；零付费 AI；网络阻断证据属架构师 runner 职责，实施方 runner 是 HOME 重定向+保护哈希（如实表述，勿写成"禁止外联"）。
- **环境坑**：bash heredoc 会吃反斜杠/换引号——多行补丁用 Write 写脚本文件执行；push 网络窗口性——代理开着用 `git -c http.https://github.com.proxy=http://127.0.0.1:7890 push`，代理关了试 `git -c http.https://github.com.proxy= push` 直连；中文路径 git 在 bash 可直跑。
- **告警三处同步**：新增 logger.warning → plain_errors.py + docs/报错速查手册.md 同 commit。CACHE_VERSION 不动（评分域零改动）。
- 全量跑约 4 分钟（1482 passed 基线），后台跑省时间；结果写 iteration8 不覆写 iteration6/7。

## 四、执行顺序建议

1. 读 R14_ACCEPTANCE + DELIVERY_PLAN（本文为索引，以卡面为准）→ 核对 HEAD/工作树。
2. R14 Y1–Y4 反例搬入正式回归：`tests/core/test_o0_exceptional_account.py`（Y1×2+矩阵）、`test_o2_long_unknown_weight.py`（行8）、O1 时间矩阵（估值+影子两侧同输入）；红灯确认。
3. O0 修 portfolio 读侧 + la/批量清单 → 绿 → guard。
4. O1 抽纯函数收敛两消费方 → shadow_v10 → 绿。
5. O2 行8 冻结 + 表 v4 → 绿。
6. 联合同 HEAD：目标回归（附收集清单）+ 隔离离线全量（iteration8/FULL_TEST_RESULTS）+ 保护哈希。
7. v0.8.27 五处 + STATUS/AGENTS/README/RESUME 分层更新 + EXECUTION_RECORD + 实施方报告（逐条答 Y1–Y4 + 解释 462/423）→ guard 终审 → commit/push（代理按需）。

## 五、记忆与恢复入口

- 跨会话记忆：`project_iteration6_m_series`（会随 O 批更新）；读序 RESUME → STATUS → iteration8 卡面。
- 上一轮实施报告：`plan/fusion/iteration7/实施方报告_N系列交付.md`；本轮账本将建 `plan/fusion/iteration8/EXECUTION_RECORD.md`。
