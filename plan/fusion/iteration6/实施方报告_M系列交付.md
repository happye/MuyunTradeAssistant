# 第六轮 M 系列实施方报告（致架构师）

2026-10-01｜实施 Agent：Claude Code｜基线 `52946b7`（R12 审批落库，REVIEW_INDEX 一致）
本批逐卡 commit：**M0 `21da3ff` → M1 `96f6f1d`**（+ docs 收尾一批）。版本统一 **v0.8.25**。逐卡账本见 [EXECUTION_RECORD](EXECUTION_RECORD.md)；全量隔离证据 [FULL_TEST_RESULTS](FULL_TEST_RESULTS.txt)。

## 一、交付总览

按 [DELIVERY_PLAN](DELIVERY_PLAN.md) M0/M1（READY）与 M2（CONDITIONAL）执行：每反例先写独立测试红灯 → 修转绿 → 同类点扫描 → code-quality-guard 监督审查 → P1/P2 处置后提交。目标回归 **27 文件 420 passed**（含 R12 的 281 目标集）；隔离离线全量 **1440 passed, 2 skipped, 1 deselected**（基线 1398+2 skipped；runner 含 HOME 重定向＋7 保护文件哈希＋知识库文件集合前后核对，见 FULL_TEST_RESULTS）。**实现≠审批；W1/W2 修复以架构师按修正后源码指纹的独立复验为准。**

| 卡 | 反例 | 探针/测试结果 | 结论 |
|---|---|---|---|
| M0 修 W1 | 有股旧比例 0：CLOSE_ALL→WAIT、未知权重 REDUCE→WAIT | 28 例新测试全绿；19 场景规范合同逐条锚定 | 实施完成，待独立复验 |
| M1 修 W2 | 旧日收盘被标抓取时点，错锁权重 10% | 14 例新测试全绿；W2 主反例全链拒精确权重 | 实施完成，待独立复验 |
| M2（条件卡） | B1：日常查看触发研究重跑 | 只读视图原型 16/16（临时根内真实服务） | 隔离原型交付，**生产未接线** |

## 二、逐项回应（DELIVERY_PLAN 验收要求）

### M0｜账户事实到最终行动的同源闭环

- **必须通过1（W1 反例族）**：`tests/core/test_m0_account_terminal.py` 覆盖 100股/旧比例0、900股/旧比例.1（经 `apply_quantity_fill` 投影）×硬退出/普通减仓/增仓/持有、已知空仓、真未知账户（账本冲突/持仓文件损坏）——适配器对齐 [design_contract_probe](design_contract_probe.py) 19 场景：`EXIT+HELD/UNKNOWN→EXIT(目标0)`、`EXIT+NONE→WAIT`、`REDUCE+HELD→REDUCE(未知权重目标None)`、`REDUCE+NONE/UNKNOWN→REVIEW`、`OPEN/ADD+HELD+未知→HOLD`、`HOLD 不取占位0`。**未知权重 REDUCE 不再变 WAIT**；EXIT 目标 0 是动作定义（delta=None 不伪造）。
- **必须通过2（隔离竖向）**：真实 parse_input→run_cli——`pos confirm --qty` 公开回执 → 重启新实例 → `l`（行情替身 get_stock_data；装配/适配器/影子/证据/建议全真）→ 断言影子记录带账户版本、证据 JSONL 带 position_state/account_version、硬退出输入经装配原语贯穿两臂 EXIT、today 显示数量事实/待重估；la 批次与 chat 入口同款接线断言。
- **必须通过3（请求内同版本）**：`RequestAccountFacts` 构造读快照一次（`AccountService.snapshot` 计数断言=1）、多股共享同 account_version；确认新成交后**下一请求**看到新数量与新版本；无全局长寿命缓存。旧比例（RATIO_ONLY）账户回归零变化（直通判持仓/权重）。
- **必须通过4（回归）**：281 目标集全绿＋全量隔离离线（FULL_TEST_RESULTS）；既有断言更新 1 处（空仓 REDUCE WAIT→REVIEW，19 场景规范合同覆盖旧口径，测试内披露）。
- **消费者清单**：策略（to_strategy_state 共享请求 NAV）、最终包（portfolio_revision）、周期策略（position_state）、影子（两臂/包重建/记录）、摘要（数量事实+退出方向保留人话）、证据（追加式两字段+diff 比较 position_state）、建议（既有 L1 守卫复用）；扫描/TUI/Web 零改动（has_position 已数量感知，回归确认）；回测路径零触碰。

### M1｜行情来源时间与观察合同

- **必须通过（适配函数接离线供应商行）**：`_baostock_quote_from_row` 纯函数保留原行 date→effective_at（脏行如实缺省）；新浪 fields[30]/[31] 解析（格式校验）；真实 EM 分支（monkeypatch ak 层）产物无 effective_at；W2 主反例全链（Baostock 行→StockData→`_qualified_weight`）拒精确权重；同日合法组合可算（新浪本地墙钟按交易所日期解释、aware 偏移归一 Asia/Shanghai）；未来/缺时点拒绝。预取命中返回原 dict（effective_at/来源随行）；120s 缓存随 StockData 对象冻结；K 线磁盘缓存路径未动（无实时价用最近 K 线日期，诚实陈旧）。
- **观察合同 v8**：`SHADOW_DERIVATION_VERSION="shadow_v8"`；binding 增 `quote_fetched_at`（采集时点诊断，**不入资格门**——资格仍是有效时点 quote_cutoff，不为凑分母降低资格）；输出指纹绑定内排除采集时点（纯抓取时间变化→deduped、行情真实变化→留痕，测试锚定）；报告分桶通用化 `cur_*`＋`older_versions`（v7/v6 及更早按记录原样只诊断不追认）；记录/去重/报告三处共用同一 `_binding_eligibility`。旧 v7 不追认、不清理。
- **用户收益**：l/la/chat 三通道降级如实提示「Baostock 日线收盘（日期）（非实时）——当前权重待重估」；evidence_cutoff 语义（被消费评估的 evaluated_as_of）v7 起未变，v8 继续明确不宣传为原文最大发布时间。

### 旧兼容

- RATIO_ONLY 老账户：比例判持仓/权重直通逐分支零变化（`to_strategy_state` 等价重构经 guard 逐行核对+回归）；无持仓记录股票 OPEN 建仓语义不变（空仓 ADD→OPEN 契约钳制保留）；`position_state=None` 的旧调用方按 ratio 推导（决策表路径与 v1 逐字节等价，回归锚 test_horizon_backward_compat_without_state）。
- 影子旧调用方（不传 account_context）行为不变（k1/l0/k2a/j4 全绿）；唯一无条件变更是 legacy 臂 HOLD 占位 0→None（本卡声明意图）。
- 回测红线：DataFeeder 不消费 quote_as_of/quote_fetched_at/price_source（guard grep 实证）；recent_announcements live-only 填充不变。

### 隔离证据

- 全量 runner：HOME/USERPROFILE→一次性临时目录、`MUYUN_TESTS_REAL_HOME` 清除、保护 7 文件（portfolio.yaml/portfolio.yaml.bak/knowledge/index/*）SHA256 前后一致＋知识库文件集合一致（FULL_TEST_RESULTS 四行断言）；测试内持久化路径显式重定向 tmp_path。
- **披露（外联归因）**：全量/竖向运行中 l 流程存在既有外联尝试——笨总数据提供方 margin 表拉取（失败即降级警告，`warning` 日志实证）；非本卡引入（L2 同流程先例），guarded runner 口径下验证为离线安全降级。runner 层面的 socket 审计钩子属架构师工具职责，本批以哈希+集合核对与离线缺省（conftest 排除外网用例）兜底。

### 协议与产品版本

- 决策表版本 **v1→v2**（guard P1 处置：W1 受影响人群的行2/5/6/8/9/10 裁决翻转须在 decision_rule_version 上可见；未请豁免，直接升版——请架构师裁决是否追认）。
- 影子协议 **shadow_v8**（独立新协议；旧 v7/v6 只诊断不追认；分桶键名通用化 `cur_*`/`older_versions`，k1/l0/l2 断言同严格度更新）。
- 产品版本 **v0.8.25**：start.py/banner/--version/README（状态段+版本历史）/AGENTS.md（当前版本段重写）五处同步；`src/__version__=0.1.0` 经 grep 核定零消费者，按 R12 裁决六不动。CACHE_VERSION 未 bump（笨总评分域零改动，guard 两轮实证）。
- fusion.mode 缺省仍 capture_only；K1 未冻结（待 M0/M1 独立复验后裁定）；K2b 生产未放行。

## 三、如实披露与裁决请求

1. **M2 前置未过，未写生产完成**：仅交付计划目录内原型（16 场景：正常/缺资料/候选/失效＋B1 重复读取零变更＋零写哈希断言），未改任何 src/ 文件。
2. **摘要面板显示判据**：面板「权重待重估」以 pos.weight_unknown（记录侧标志）为判据而非 AccountContext——账实冲突态显示记录侧数量不提待对账；M1 接 NAV 锁定权重后该标志语义需同步（登记后续卡，本轮不可达）。
3. **预取命中时 quote_fetched_at 记复用时刻**（≤120s 偏界）——诊断字段不入资格/指纹；如需精确请在预取命中路径补 dict 携带原抓取时刻（留后续）。
4. **nav_priced_at 为 aware-UTC 形态时的日解释**：`_quote_day` 会按 naive 本地日解释——当前 src 无写入方设置 nav（实证），属潜伏项非现实缺陷。
5. **测试外联**（见上）：既有行为，非本卡引入；建议架构师复验时沿用 guarded runner。
6. **guard 两轮全部处置**：M0 P1×1（表版本）+P2×5 已修、P2×2 留后续（第 2 条同源）；M1 无 P0/P1、P2×4 已修、披露×2（第 3/4 条）。
7. **裁决请求**：(a) M0/M1 是否进入独立复验窗口；(b) DECISION_TABLE_VERSION v2 升版追认；(c) shadow_v8 分桶键名通用化（cur_*/older_versions 替代 v7_*/v6_*）追认；(d) v0.8.25 版本口径与五处同步追认。

## 四、用户可见结果（start.py 可感知）

- 有仓旧比例 0 的持仓股：l/la/chat 不再出现「风险需退出」与「无仓等待」矛盾——输出「持有 N 股（权重待重估）——退出方向保留」；未知权重 REDUCE 保留减仓方向（目标待重估）；已知空仓减仓信号转人工复核。
- Baostock 降级日：行情行下明示「Baostock 日线收盘（日期）（非实时）——当前权重待重估」，旧价不再与当天 NAV 拼出错误权重；影子报告能解释该观察为何不入有效比较（缺有效时点→诊断桶）。
- `shadow` 报告新口径：当期 v8 协议分母 + 旧版本明细一行（只诊断不追认）。
