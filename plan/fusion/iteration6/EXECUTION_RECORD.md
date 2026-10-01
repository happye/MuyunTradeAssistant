# 第六轮 M 系列实施账本（M0–M2）

实施 Agent：Claude Code｜基线 `52946b7`（R12 审批落库）｜逐卡 commit：**M0 `21da3ff` → M1 `96f6f1d`**。

每卡流程：反例测试红灯 → 修转绿 → 同类点扫描 → code-quality-guard 监督审查 → P1/P2 处置 → 提交。交付计划与验收标准见 [DELIVERY_PLAN](DELIVERY_PLAN.md)；实施方浓缩报告见 [实施方报告_M系列交付](实施方报告_M系列交付.md)。

## M0｜账户事实到最终行动的同源闭环（修 W1）— commit `21da3ff`

**反例（R12 W1）**：`quantity=100, ratio_stale=true, current_ratio=0` 时策略输入终态 `CLOSE_ALL/fundamental_alert`，`build_decision_packet` 与 `capture_shadow` MID 两处输出 WAIT；独立对照证明传 None 时 REDUCE 也被适配器吞成 WAIT。

**红灯**：`tests/core/test_m0_account_terminal.py` 初版 8 失败（ImportError 起步：AccountContext/RequestAccountFacts 未实现；适配器旧映射 WAIT/吞减仓；请求内重复读账本）。

**修复**：
- `src/data/portfolio.py`：新增 `POSITION_HELD/NONE/UNKNOWN` 常量、`AccountContext`（frozen dataclass：三态+合格权重+资格原因+账户版本）、`RequestAccountFacts`（构造读账本快照**一次**——版本/NAV/各股数量；`context_for` 逐股装配；下一请求新建实例刷新，无全局长寿命缓存）。装配规则：pm 损坏→UNKNOWN；账本数量>0→HELD（账本事实源优先，含 RATIO_ONLY 记录+期初导入未同步 quantity_fact 形态）；数量投影≠账本→HELD 但权重 None（待对账不编造）；记录有仓账本无仓→UNKNOWN；RATIO_ONLY 比例直通（语义零变化）。合格估值判据抽 `_qualified_weight` 共享核心（`to_strategy_state` 逐分支等价重构，杠杆>1 日志带数值）。
- `src/core/analysis_service.py`：`build_decision_packet` 增 `position_state`/`account_version`；映射对齐 [design_contract_probe](design_contract_probe.py) 19 场景合同——EXIT+HELD/UNKNOWN→EXIT(target 0)、EXIT+NONE→WAIT、REDUCE+HELD→REDUCE(未知权重目标 None)、REDUCE+NONE/UNKNOWN→REVIEW、OPEN/ADD+HELD+未知权重→HOLD、HOLD 不取占位 0；position_state=None 按 ratio 推导兼容旧调用方；`portfolio_revision=account_version`。
- `src/core/decision_policy.py`：`evaluate_horizon`/`evaluate_horizon_intent` 增 `position_state`（None=旧推导等价）；**DECISION_TABLE_VERSION v1→v2**（guard P1：数量账户人群行2/5/6/8/9/10 裁决翻转，影子 `decision_rule_version` 真实升版；受影响人群正是 W1 实证持仓——未请豁免，直接升版并披露）。
- `src/core/shadow_diff.py`：`capture_shadow` 增 `account_context`；包重建与两周期评估消费 ctx；legacy 臂 HOLD 占位 0→None。
- `src/cli/main.py`（l/la）+ `src/chat/tools.py`：请求级 facts 一次装配贯穿 orchestrator/终态包/影子/证据；摘要面板 REDUCE/EXIT 分支加「账户事实：持有 N 股（权重待重估）——退出方向保留」，持仓行权重待重估不再打印过期比例 0%；直调 `to_strategy_state` 共享请求 NAV（B2 名副其实）。
- `src/cli/evidence.py`：`position_state`/`account_version` 追加式记录；diff 比较字段加 position_state。
- `src/core/orchestrator.py`：`analyze_packet` 透传新参（防同一陷阱复发）。

**绿灯与回归**：M0 28 例全绿；24 文件目标集 **394 passed**（含架构师 281 目标集）；既有断言更新 1 处（test_analysis_service 空仓 REDUCE WAIT→REVIEW，按规范合同披露）。

**guard 审查处置**：P1（表版本升 v2）已修；P2 五项落实（chat 无记录 NONE 口径与 l 对齐、B2 直调 to_strategy_state、测试隔离补 PLANS_FILE/RESEARCH_DIR、杠杆日志带值、恒真断言清除）；P2 两条留后续（摘要面板判据 M1 接 NAV 后同步、外联 socket 归因——实证为 l 流程内笨总数据提供方既有行为，非本卡引入）。

## M1｜行情来源时间与观察合同 shadow_v8（修 W2）— commit `96f6f1d`

**反例（R12 W2）**：供应商行日期 2026-09-29 走实际 `_fetch_baostock_realtime` 提取后，`quote_as_of` 变成抓取时刻 2026-10-01；数量 100×价格 10/当天 NAV 10000 锁出权重 10%——本应因价格与 NAV 日期不一致保持未知。

**红灯**：`tests/data_sources/test_m1_quote_time.py` 初版 9 失败（`_baostock_quote_from_row` 未实现、quote_as_of=墙钟、shadow_v8 缺失）。

**修复**：
- `src/data/akshare_client.py`：`_fetch_baostock_realtime` 提取纯函数化 `_baostock_quote_from_row`（原行 date→`effective_at` 日精度，脏行如实缺省）；`_parse_sina_batch` 解析 fields[30]/[31]（格式校验，坏值缺省）；`_calculate_indicators_uncached` 三个 StockData 构造点 `quote_as_of` ← 来源有效时点（无实时价用最近 K 线日期、无源时点明确 None——**抓取墙钟永不做行情时点**），`quote_fetched_at`=抓取时刻（诊断）、`price_source`=来源；预取命中返回原 dict、120s 缓存随对象冻结（元数据不重打）。
- `src/data/models.py`：StockData 增 `quote_fetched_at`/`price_source`；`quote_as_of` 语义改「价格有效时点」。
- `src/data/portfolio.py` 估值门：`_quote_day` 按交易所时区（Asia/Shanghai）归一（同一实际时刻不同偏移可比；新浪本地墙钟按日解释）+ 未来时点拒绝——旧日收盘×当天 NAV 保持 None、同日合法组合可算。
- `src/core/shadow_diff.py` **shadow_v7→v8**：binding 增 `quote_fetched_at`（采集时点诊断，不入 `_binding_eligibility` 资格门——资格仍是有效时点 quote_cutoff）；输出指纹绑定内排除采集时点（纯抓取时间变化折叠不虚增、行情真实变化留痕）；报告分桶通用化 `cur_*`/`older_versions`（v7/v6 及更早按记录原样只诊断不追认，不再逐版改名键）；渲染同步。
- l/la/chat 传 `quote_fetched_at`；三通道降级如实提示「Baostock 日线收盘（日期）（非实时）——当前权重待重估」。

**绿灯与回归**：M1 14 例全绿；27 文件目标集 **420 passed**（含 281 目标集 + M0）；test_k1/test_l0/test_l2 报告键名与版本串按 v8 合同更新（同等严格度：cur_*/older_versions 语义对位替换）。

**guard 审查处置**：无 P0/P1。P2 落实四项（EM 恒真测试改走真实 EM 分支、la/chat 降级披露与 l 对齐、未来判定 today 与 _quote_day 同基准上海时区、测试 docstring 口径 v8）；披露两项（预取命中时 fetched_at 记复用时刻——诊断字段、≤120s 偏界；`_quote_day` 形状兜底对语义非法日期 fail-closed 方向不变）。

## M2｜K2b 最小日常研究接线（CONDITIONAL——仅隔离原型）

按 DELIVERY_PLAN 前置约束（M0/M1 独立复验通过前只允许计划目录内原型/测试准备），**未做任何 src/ 生产改动**。交付 [m2_readonly_view_prototype.py](m2_readonly_view_prototype.py)（临时根内对真实 HorizonPlanStore/AssessmentStore/shadow 评估核对路径演示）＋结果 [m2_readonly_view_results.json](m2_readonly_view_results.json)：

- **16/16 场景通过**：正常（已接受计划+评估齐，重复读取 revision/accepted_ref 不变）、缺资料（给具体 research 命令、零写入）、候选存在（双槽可见、不替换主意图、重复查看不增 revision）、失效（评估错配→REVIEW_REQUIRED 待复核不静默盖章）；
- **零写入断言**：视图前后目录树内容哈希不变（B1 教训：日常查看不触发研究重跑/候选创建）；
- 原型过程抓到两处视图语义缺口并修复（已接受计划在位时新候选也提示下一步；零写基线取点）。

## 版本与全量

版本统一 **v0.8.25**：start.py / cli banner / --version / README（状态段+版本历史新条目）/ AGENTS.md（当前版本段重写）；`src/__version__=0.1.0` 经核定无任何消费者（grep 实证），按 R12 裁决不动。
- 隔离离线全量：`impl_full_test_runner.py`（HOME 重定向+保护文件哈希+知识库文件集合核对）→ 结果见 [FULL_TEST_RESULTS.txt](FULL_TEST_RESULTS.txt)。
- CACHE_VERSION 未 bump：笨总评分域零改动（guard 两轮 grep 实证）。
- 已知披露：全量/竖向测试中 l 流程经笨总数据提供方存在既有外联尝试（margin 表拉取失败降级，非本卡引入；架构师 guarded runner 同样观察到该路径并验证其离线降级）。
