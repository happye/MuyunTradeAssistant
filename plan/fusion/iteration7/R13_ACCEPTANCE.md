# R13：M 系列增量独立审批

2026-10-01｜Codex 总架构师｜受审 HEAD `73ba005`，M0 `21da3ff`、M1 `96f6f1d`｜v0.8.25。

## 裁决

**分项接收，M0/M1 整卡退回补齐；K1 暂不冻结，M2/K2b 生产接线暂不放行。** R12 已完成审批不重做。保留已通过范围，下一窗口只补既定合同的断点，不另起架构。

| 交付 | 接收范围 | 未通过范围 |
|---|---|---|
| M0 | 显式三态最终适配器：有仓未知权重的 EXIT/REDUCE 保留；请求快照一次读取和下请求刷新；相关旧比例回归 | 新上下文没有真正成为策略、chat 无投影分支、建议及展示的统一事实源；见 X1 |
| M1 | W2 原反例关闭：Baostock 原行日期保留，旧价配当天 NAV 为 None；行情/采集字段分离及采集时间排除指纹；版本分桶方向 | shadow 两臂仍混用原始值与规范值，未知生成版本可进有效桶；非法/同日未来时间可过估值门；见 X2/X3 |
| M2 | 只读查询方向及正常接受态重复读取不写盘，仍是隔离原型 | 损坏库在返回视图被显示成空库；原16项中有自比较哈希，不能作为完整失败场景证据；见 X4 |

这不是推翻 W1/W2 已通过的具体修复：W1 的“数量投影存在”范围通过，**账本有仓而投影缺失的同类点尚未闭环**；W2 的“旧收盘重标墙钟”通过，时点合法性仍有遗漏。

## 独立证据

- [目标回归](TARGETED_TEST_RESULTS.txt)：**27 文件，381 passed，75.47 秒**，含 R12 的21文件和 M0/M1、最终适配器、周期策略、批量行情等增量。该选择集与实施方420集不完全相同，不冒充重现420。
- [合同探针](acceptance_probes.py) / [完整输出](ACCEPTANCE_PROBE_RESULTS.txt) / [结构化读数](ACCEPTANCE_PROBE_RESULTS.json)：**17 场景，15 条合同断言失败、2 条正例通过**。失败集中在下列四组，不是15个独立P1；退出码1是预期的未满足合同证据，不写成PASS。
- 两次最终运行均在临时根，阻断网络、子进程、真实 `.muyun` 读取和越界写入；可选RAG关闭并守卫FAISS原生写。**7个持仓/知识库文件哈希与知识库文件集合不变**。这些是测试守卫，不能宣称操作系统级沙箱。
- 实施方全量 **1440 passed、2 skipped、1 deselected** 见[原报告](../iteration6/FULL_TEST_RESULTS.txt)，本轮未独立重跑全量。其runner是HOME重定向和前后哈希检查，不具有本轮socket/越界写阻断；不能把“默认离线收集”误写成“禁止外联”。
- 未改产品源码、产品测试、配置、真实账户；未调用付费AI；未Git提交。工作树已有持仓/知识库/教材/本地工具资料改动保留。验证工具初版文件名/枚举/patch位置错误已纠正，最终17条均实际到达行为断言；工具失误不计产品缺陷。

复现：

```powershell
./.venv/Scripts/python.exe -B plan/fusion/iteration7/review_test_runner.py
./.venv/Scripts/python.exe -B plan/fusion/iteration7/review_test_runner.py plan/fusion/iteration7/acceptance_probes.py -s
```

## X1｜P1：账户上下文没有贯穿策略与无投影入口

- `src/data/portfolio.py:386` 的 context_for 在账本100股、无NAV时给 HELD/weight=None；`to_strategy_state`（677起）仍独立读取投影。原比例记录.1时策略得到.1，无投影时得到FLAT/0。`src/cli/main.py:896`、519及`src/chat/tools.py:315`仍调用此第二套装配，而不是消费ctx事实。
- `src/chat/tools.py:305` 只在遍历到投影记录后创建ctx。**真实chat入口+离线行情/上游策略替身**：账本100股、投影缺失，传给orchestrator的是 `has_position=False/ratio=0`；上游给定CLOSE_ALL后，真实最终适配器得到 **WAIT、无账户版本**。这是接线/适配器组合反例，不是用户真实账户现场触发的止损。
- `RequestAccountFacts.__init__`（350起）快照读取异常只debug，`_ledger_present=False`；无投影时变 **NONE/0**，读失败被当成明确空仓。已有快照的 `isolated_events/data_completeness` 也未贯穿资格（静态同类点，待N0加用例）。
- `record_proposal`（`portfolio.py:1008`）仍消费原StrategyDecision与旧比例；`main.py:994`和`shadow_diff.py:473`仍以pos存在为前置。无投影时建议/影子缺失、摘要以pos判据展示，不可宣称“全链同源”。

影响G1/G2/G5、T01/T03。N0负责；不自动修改真实投影或账户历史来掩盖读路径缺陷。

## X2｜P1：观察合同两臂语义不同、未知版本仍合格

`src/core/shadow_diff.py:643–674`：legacy target来自 `strategy_decision.position_ratio`，只有HOLD且比例<=0才清空；legacy execution取PositionAction，fusion execution取ExecutionStatus。

实测正常 **HOLD_POSITION/ratio=.1**：规范包target=None，影子legacy却为 `.1/KNOWN`，且mid_effective=True。正比例HOLD在 `strategy_layer.py:332` 的减仓保护等真实分支可产出。正常REDUCE的规范包execution=ELIGIBLE，legacy字段却是REDUCE，fusion是ELIGIBLE。两臂并未各自消费规范包。

`shadow_diff.py:317`只检查版本非空。实测 `policy_version=UNSUPPORTED_FUTURE_VERSION`、其余绑定齐备 → **mid_effective=True、drop_reasons=[]**。R12第四项要求的已验证生成版本×方法×决策表组合尚未落实，不能冻结。

同域P2：

1. `decision_policy.py:247`：UNKNOWN+VALID+MID技术退出给REDUCE，既定规范要求REVIEW；HELD+未知权重+预算True给ADD，规范要求HOLD。前者capture可达，后者当前capture固定budget=None，**仅证明公开策略接口边界，不夸大为已发生生产加仓**。
2. `shadow_diff.py:902`旧桶用if/elif：同条MID/LONG均有效的旧记录只计MID。当前桶未被污染，但旧诊断统计失真。

影响G6/T03；N1负责。旧v8记录保留，未来修正另起协议版本，不回填追认。

## X3｜P2：估值时点解析存在放行兜底

`src/data/portfolio.py:266–308`：ISO解析失败后只凭两个短横线取前10位；先截到日再判未来。实测以下输入均得到 **0.1、“合格估值锁定”**：

- 行情与NAV日均为不存在的 `2026-02-30`；
- 行情 `2026-09-30T99:99:99` 或 `2026-09-30garbage`，NAV日 `2026-09-30`；
- 当天未来23:59:59的行情时刻配当天NAV。

它与影子严格ISO解析口径也不一致。当前没有生产NAV写入方，故不宣称真实账户已经算错；但这是M1已承诺的资格边界，**不得以“非法日期fail-closed”登记已完成**。N2同时修NAV先strftime丢偏移、naive新浪时间的交易所时区解释及同日未来检查，保持合法日精度输入。

## X4｜M2原型限制（P2，非生产故障）

独立正例：同一已接受计划按不同查看时刻读3次，研究/计划树哈希不变。负例：损坏plans.json，底层corrupted=True且日志告警，但daily_research_view仍返回“无研究记录→建立”，未把失败带给视图消费者。原型 `m2_readonly_view_prototype.py:204` 的mismatch_zero_write比较同一时刻的同一树，是恒真检查。

只接收方向与正常只读范围，不认证“16项覆盖完整失败行为”；补损坏/缺失区分及真正前后哈希后再复验。M2生产前置不变。

## 对实施方请求与披露的答复

1. M0/M1独立复验已完成，结论如上；无需再次申请进入复验窗口。
2. **追认决策表v2升级**：语义变化必须有版本；追认版本号不等于v2全部规则通过。N1修规则后继续升版并说明兼容范围。
3. **追认shadow_v8及cur_*/older_versions命名方向**，不冻结v8合同。N1统一语义/资格后用shadow_v9候选，旧v8及更早只诊断。
4. **追认产品v0.8.25及已核对的版本同步**；src.__version__无消费者不动、CACHE_VERSION评分域未变不动。下一修复交付统一v0.8.26，测试通过后同步用户可见版本。
5. 摘要判据留后续不成立于全部情况：**账实冲突与无投影路径已经可达**，并入N0；合格NAV显示的未来路径一起收口。预取fetched_at精确化仍为诊断P2，可留后续但不能继续称三时点完全实现，N2触及时优先一并修。
6. NAV偏移丢失虽暂无生产写入方，N2正好修改时间边界，一并补，不扩建NAV估值系统。
7. K1未冻结、K2b未放行、effective保持capture_only；K4未提交合格真实样本。E1b/E5b未授权且前置不齐，E2b–E4b数据受阻。R12其他已通过范围及真实旧核定记录不重审、不清理。

下一执行窗口见[第七轮任务卡](DELIVERY_PLAN.md)，长期状态见[MILESTONES](../../../MILESTONES.md)。
