# 第七轮 N 系列实施方报告（致架构师）

2026-10-02｜实施 Agent：Claude Code｜基线 `73ba005`｜交付版本 **v0.8.26**
逐卡实施、联合同一 HEAD 回归交付。逐卡账本见 [EXECUTION_RECORD](EXECUTION_RECORD.md)；全量隔离证据 [FULL_TEST_RESULTS](FULL_TEST_RESULTS.txt)。

## 一、交付总览

按 [DELIVERY_PLAN](DELIVERY_PLAN.md) N0/N1/N2（READY）与 M2 原型修订执行：每卡先把 R13 反例转为产品回归红灯 → 修转绿 → 同类点扫描 → code-quality-guard 监督审查 → P1/P2 处置 → 联合回归。联合目标回归 **30 文件 462 passed**（R13 的 27 文件集 + N0/N1/N2）；隔离离线全量 **1482 passed, 2 skipped, 1 deselected**（guard P1 修复后实测 0 failed；runner 含 HOME 重定向＋7 保护文件哈希＋知识库集合核对；输出路径改至 iteration7，不覆写 iteration6 受审证据）。**实现≠审批；K1 冻结与 M2 放行待独立复验裁定。**

| 卡 | R13 反例 | 结果 | 结论 |
|---|---|---|---|
| N0（X1） | ctx 未贯穿策略/chat 无投影 WAIT/读取失败 NONE | 12 例新测试全绿；四探针语义逐条转绿并落正式回归 | 实施完成，待独立复验 |
| N1（X2） | 两臂混用原始值/未知版本计有效/行5行6 | 13 例全绿；shadow_v9 + 决策表 v3 | 实施完成，待独立复验 |
| N2（X3） | 非法/未来时间过估值门 | 17 例全绿（X3 四条+正例+边界） | 实施完成，待独立复验 |
| M2（X4） | 损坏库当空库/哈希自比较 | 原型 25/25（计划目录内） | 原型补证据完成，生产前置不变 |

## 二、逐项回应（DELIVERY_PLAN 验收要求）

### N0｜账户事实成为所有消费者的事实源

- **必须通过1（X1 四条转绿）**：账本 100 股+投影旧比例 .1 → 策略权重=ctx 合格权重（None，非 .1，`test_strategy_state_consumes_ctx_weight_not_projection_ratio`）；账本 100 股+投影缺失 → 重建仓 OPEN/权重 None/成本不伪造（`test_strategy_state_held_no_projection_not_flat_zero`）；chat 无投影+硬退出 → **同一调用内**最终 EXIT+账户版本随影子/证据（`test_chat_no_projection_hard_exit_same_call_exit`，上游策略替身=行情/上游 fixture；装配/最终适配/研究账本存储全真）；读取失败 → UNKNOWN（`test_ledger_read_failure_is_unknown_not_none`；数量投影在账本读不到同样未对账）。
- **必须通过2（冲突/隔离/损坏分别验证）**：数量投影冲突（M0 回归锚）+ 隔离事件（真账本损坏行重放→isolated_events，权重冻结+「账本含隔离事件——待对账」）+ 损坏持仓 UNKNOWN；l/la/chat/today/影子/建议同版本无矛盾；未知不显示旧比例；旧比例无账本兼容（`test_ratio_only_no_ledger_still_compat`）。
- **必须通过3（竖向）**：真实 parse_input→run_cli `pos confirm --qty`→重启→公开入口；风险信号在**同一调用**到达最终包/公开输出（`test_l_no_projection_hard_exit_same_call_exit`——未用「入口跑过+另手造包」）；la 批次同款。
- **必须通过4（读取次数真断言）**：la 批次 `AccountService.snapshot` 计数==1（`test_la_batch_snapshot_read_count_is_one`）；下一请求见新成交与新版本。建议读取的额外状态：record_proposal 不读账本（消费注入的 StrategyDecision 与投影记录）；确认类建议在无投影时明确缺口不生成（`test_proposal_no_projection_not_fabricated`——pos confirm 依赖投影会死路，退出方向由终态包/摘要/影子承载）。
- **同类点（guard P1-3 补齐）**：scanner/TUI/Web 三入口同款 ctx 装配+三态 has_position——X1 同型缺陷（无投影按空仓分析）在该三通道关闭；接线共用 to_strategy_state ctx 合同（`test_strategy_state_*` 覆盖语义核心），入口层经既有套件回归。

### N1｜两臂规范包 + 有效版本资格

- **必须通过1（两臂逐字段=各自规范包）**：真实策略分支形态（BUY+未知权重→(HOLD_POSITION, 0.0) 占位+行334 装配覆盖 .1——R13 探针同形态）→ legacy 臂 action HOLD/target **None**/target_state UNKNOWN/execution **NOT_NEEDED**；REDUCE 未阻→legacy execution **ELIGIBLE**（同枚举）；BLOCKED 时 legacy 臂取 legacy 包的 BLOCKED+blockers，fusion 臂取自己包的执行语义（互不混用）。
- **必须通过2（版本资格）**：`UNSUPPORTED_FUTURE_VERSION` → mid_effective=False+`unsupported_policy_version_MID` drop+当期分母 0；支持组合正例（RESEARCH_SERVICE_VERSION×ASSERTION_METHOD_VERSION×v3）仍 eligible；版本字符串存在≠兼容，未换字符串绕门。
- **必须通过3（行5/行6）**：UNKNOWN 技术退出→REVIEW；HELD+未知权重+budget=True→HOLD（target None）；HELD+已知+budget=True→ADD 不回退；不传三态路径行1 旧行为保留、行5 明确迁移（v3 受影响行=行5/行6，注释登记）。
- **必须通过4（旧桶与协议）**：旧双有效记录 MID/LONG 各计 1（修 if/elif）；v9 当期分母、v8/v7 进 older_versions；纯采集时间变化折叠、行情真实变化留痕（M1 语义 v9 保持）。

### N2｜时间资格严格解析

- **必须通过（X3 四条转绿+正负例矩阵）**：`2026-02-30`/`2026-09-30T99:99:99`/`2026-09-30garbage`/当天未来时刻 → None+人话原因；合法日期/同一实际时刻不同偏移/新浪本地墙钟同日可算；缺失/未来日期拒绝；W2 旧价+新 NAV 保持 None、合法同日组合可算（M1 回归锚定）。
- **边界收口**：NAV aware-UTC 保留偏移到资格门（account_nav/RequestAccountFacts isoformat，上海日归一后同日可算）；交易所午夜边界（越午夜→异日拒绝）与 shadow `_cutoff_in_future` 分类一致（同一输入同判未来）；price/NAV NaN/Inf → None。
- **披露收口**：预取 dict 携带原 `fetched_at`（四类源齐），`_calculate` 优先消费——预取复用不再记成复用时刻（R13 答复5 的遗留项关闭）；纯 K 线回退显式 `price_source="kline_only"`，l/la/chat 降级提示扩展（「K 线收盘（实时行情未取得）（非实时）」）。
- **缓存不重打**：预取命中返回原 dict；120s 缓存随 StockData 冻结（M1 回归继续绿）。

### M2 原型（X4）

损坏 plans.json → `store_status="corrupted"`+人工修复指引（不装空库、不给建立建议）；每次视图调用真前后哈希（`zero_write_after_view` 逐场景断言，废除自比较恒真）；旧方法（非当期已验证生成版本）标注+下一步；重启读取一致；评估状态只消费产品核对规则（错配/未来时点/缺引用——不发明期限、不用字符串「过期」）。**25/25**。生产接线前置不变（N0–N2 联合过门+合同冻结后才允许）。

## 三、guard 审查与如实披露

1. **guard P1×3 全部修复**：批量分支缺 import（l A,B / l all 全灭——4 既有测试红实证）补 import；午夜窗口测试改动态时刻；scanner/TUI/Web 同类点接线（X1 同型关闭）。
2. **guard P2 落实**：la/chat kline_only 提示；l0/m1 去重测试午夜 flake；`_ledger_partial` 纳入 PARTIAL；测试替身签名补 `account_facts`。
3. **披露不改**：(a) `decision_rule_version` 子项检查在捕获时与绑定构造同表达式（恒真）——跨版本隔离实效靠 derivation_version 分桶；不宣称其为防旧表混入的实效屏障（如需实效门须另立兼容清单，N1 合同允许后续）；(b) 账本文件不存在（≠读取失败）仍按明确空仓——RATIO_ONLY 用户无账本属正常形态，登记为已知边界；(c) l/chat 竖向测试存在笨总 margin 表既有外联尝试（失败降级，非本批引入；测试文件头已如实标注）；(d) 报告/文档分层：STATUS/AGENTS/README 的 N 批交付更新分层于架构师 R13 文档同步之上（工作树未提交），待审批汇合提交——避免代提交架构师未落库文档。
4. **裁决请求**：(a) N0–N2 是否进入独立复验窗口；(b) shadow_v9 + 决策表 v3（受影响行5/行6）追认；(c) v0.8.26 版本口径追认；(d) K1 冻结与 M2 放行仍待复验后裁定（本批未动）。

## 四、用户可见结果（start.py 可感知）

- 账本有仓但投影缺失/未同步的股票：l/la/chat/扫描/TUI/Web 不再显示成空仓——显示「账本有仓 N 股（权重待重估/待对账）——退出方向保留」；硬退出同调用内贯穿到终态与影子。
- 账本读取失败/含隔离事件：明确「待对账」而非假装空仓或假装知道权重。
- 影子报告：差异来自两种策略真实终态（legacy 臂是规范包投影）；未知生成版本的观察不再进有效分母（能解释为何不能比较）。
- 非法/未来时间不再获得精确仓位（原因可读）；纯 K 线降级行情在三个通道都有明示。
