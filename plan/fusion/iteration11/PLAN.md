# 第十一轮迭代计划（当前计划入口）

- 起始：2026-10-28，基线 `main@984dd03`（第十轮收口后）。主控：本机 Claude Code；Codex 机器恢复时按 [WORKFLOW](../WORKFLOW.md) 第 2 节交接，同一时间只有一个主控推进同一张卡。
- 本文件是**当前计划与任务卡的唯一入口**：卡片队列、合同、待用户决定事项都在这里。流程规则见 [WORKFLOW](../WORKFLOW.md)，分数只看 [PROGRESS](../PROGRESS.md)，恢复看 [RESUME](../RESUME.md)。
- 第十轮（T04/T05/BUG 系列，10-21 至 10-28）已收口，历史合同与验收见 [iteration10/DELIVERY_PLAN.md](../iteration10/DELIVERY_PLAN.md)。

## 1. 当前状态（2026-10-28，来自台账）

47 条条款、总权重 111；已闭合 18 条。工程 47/98（48.0%）、验收 40/111（36.0%），合同进度指数 42.0%–68.6%；必需门未闭合。未闭合 29 条中：7 条有具名候选，16 条只有方向，6 条被外部材料、真实时间或依赖阻断。

第十轮结论：外源超时退出、共享会话、批量入口启动预算等工程可靠性子范围已逐张限定接收，但 13 张子卡没有让任何条款闭合。本轮改为**按能力选卡、每张卡写明预期台账变化**（WORKFLOW 第 3 节）。

## 2. 卡片队列（按 WORKFLOW 3.1 排序）

| 序 | 卡号 | 条款（权重） | 预期台账变化 | 用户可见 | 本机离线 | 依赖 | 状态 |
|---|---|---|---|---|---|---|---|
| 1 | G5-SOLVER-1 | V1-G5-SOLVER（2） | 验收 unknown→met（+2） | 弱（验收与缺陷修复） | 是 | 无 | 合同已备，待开工 |
| 2 | G1-TXN-1 | V1-G1-TRANSACTION-MATRIX（3） | 工程 unknown→met（+3）、验收 unmet→met（+3） | 是：`pos confirm`/today/l/la 成交事实一致 | 是 | 已满足 | 待写合同 |
| 3 | G2-READ-1 | V1-G2-RESEARCH-READ（2） | 工程/验收 unknown→met（+2/+2） | 是：l/la 共用研究视图 | 是 | 已满足 | 待写合同 |
| 4 | G5-CASHNAV-1 | V1-G5-CASH-NAV（3） | 工程 unknown→met（+3）、验收 unmet→met（+3） | 是：现金/NAV/价格时点缺失时条件化显示 | 是 | G1-TXN-1 | 待写合同 |
| 5 | G5-SNAPSHOT-1 | V1-G5-SNAPSHOT（3） | +3/+3（可能分两期） | 是：多候选不逐股满额 | 是 | G5-SOLVER-1 | 待写合同 |
| 6 | G2-PACKET-1 | V1-G2-PACKET（3） | 首期不动（消费者矩阵+黄金场景），二期 +3/+3 | 是：today/l/la/chat 同一终态包 | 是（Web 需 preview 验证） | G2-READ-1 | 待写合同 |
| — | MAINT-1 | 无条款（维护） | 不动 | 是：小缺陷修复 | 是 | 无 | 随时插空 |

若 1–4 全部达成（按权重投影，不是承诺）：工程 47→55/98（约 56.1%），验收 40→50/111（约 45.0%）。

**维护队列 MAINT-1**（每项按“修 bug 四步”单独提交，不另开字母卡）：ISS-131 证据来源字段按入口区分；ISS-139 `bb` 成功数为 0 时不显示无意义的平均收益；ISS-136 残余（共享代码判定放行全角数字代码；只给一个未来起始日期时未校验默认结束日期）。

## 3. 第一张卡合同

### G5-SOLVER-1 组合预算求解与事件账本独立验收映射（合同 2026-10-28，主控：Claude Code）

- **关联条款与预期台账变化**：V1-G5-SOLVER：验收 unknown→met（+2/111）；工程保持 met，但证据说明改为“库层满足；产品路径跨候选统一消费归 V1-G5-SNAPSHOT”，与 [ROLLOUT.md](../ROLLOUT.md) “组合预算求解无生产接线”一致。
- **目标**：给“预算求解”和“事件账本”各做一张“性质 → 测试 → 状态”的独立验收映射；缺测试的性质补测，测试变红就按四步修。
- **范围**：求解器只核定库层单次调用不变量（`src/core/portfolio_policy.py::solve_budget`，src 内无生产调用）；账本核定 `src/data/account_snapshot.py::AccountEventLog` 与 `src/data/account_service.py::AccountService` 的写入、重放、隔离、PARTIAL 语义。**不做**：跨候选现金池与统一分配（归 V1-G5-SNAPSHOT）、净额/预留、费用/整手/板块/日期规则（归 V1-G5-CASH-NAV）。
- **求解器补测**：S1 单股上限生效；S2 已持仓扣减；S3 周期预算 `horizon_budgets`（现无测试）；S4 压力损失率为 0 按未知处理；S5 同股多提案只补差额且排列不变；S6 零额度拒绝原因（现金为 0 与现金未知分开）；S7 `sell_eligible_nav` 为 inf/nan/负数时不得放大可用现金（现状未校验，预计需最小修复：入口校验有限且 ≥0，否则 ValueError）。已有 15 条测试覆盖的性质不重复写。
- **账本明示语义**：L2 买入不校验现金、负现金如实保留（只记已发生成交；现有测试已锁定）；L3 期初导入晚于成交时，期初现金按导入值锚定，此前未锚定期间的成交现金变动不累计（导入值视为当时余额），补测试锁定；L1 跨进程争写与 L4 停牌明示排除（单用户本地单写者前提；停牌属交易规则，归 CASH-NAV）。
- **文件所有权**：新 `tests/core/test_g5_solver_acceptance.py`；`src/core/portfolio_policy.py` 仅在业务红时最小修复；账本源码不改（测试与明示语义不符则停下报告）。
- **兼容锁**：`tests/core/test_portfolio_policy.py` 15 条与账本既有约 70 条测试不改断言全绿。
- **验收**：新测试先在未改代码上跑出红/绿分布；修后定向 + `test_portfolio_policy`/`test_account_snapshot`/`test_j2_account_service`/`test_k0a_account_integrity`/`test_bug_finite_ledger` 守卫拦截三项为 0；卡终全量一次；独立复核方核映射完整性与测试真实性（必要时突变）。回执 `G5_SOLVER_ACCEPTANCE.json` 含映射表。
- **停止条件**：账本语义与上述明示不符、或需改分配薄层/资金规则时，停下回到本计划改合同。
- **同时登记**：ISS-138（分配薄层价格 NaN/inf 走 REJECTED 并显示“≈nan 元”）已在 ISSUES，归 G5-CASHNAV-1。

## 4. 后续卡合同要点（开工前按 WORKFLOW 3.4 补成一页合同）

- **G1-TXN-1**：验收条件“核定的部分成交、异常恢复和并发更新场景不丢失/重复成交事实，各业务入口一致；组合资金规则另计 G5”。先把既有场景映射列全（起点 `tests/core/test_p0_account_exception_matrix.py`、`test_j2_account_service.py`、`test_t04a_confirm_golden.py`、`test_k0a_account_integrity.py`），只对缺口补反例与实现；“并发”以单用户本地单写者为支持范围，跨进程明示排除。
- **G2-READ-1**：验收条件“T02 核定消费者查询不写研究、候选不覆盖接受态、l/la 共用研究视图；逐条有接线证据”。先分列 M2 已验与剩余消费者，按真实调用链补接线测试（parse_input→run_cli 全链与单元直调并存，见 AGENTS 死坑第 10 条）。
- **G5-CASHNAV-1**：验收条件“净现金/NAV/价格时点/日期化交易规则在核定范围齐备，缺失时条件化，不把未知变零或伪精确可执行”。对照现有账户字段建规则与时点矩阵，含 ISS-138。
- **G5-SNAPSHOT-1**：验收条件“同轮候选基于同一合法组合事实快照求解，不能逐股各自满额，不伪造未知持仓”。先写混用快照/缺持仓反例；决定 `solve_budget` 的第一个生产消费者（候选：`scan market deep` 的排名后分配展示或 today 待确认建议），必要时分两期。
- **G2-PACKET-1**：验收条件“支持范围内 today/l/la/chat/Web/TUI 对同一终态 packet 展示一致”。首期只固定跨入口消费者矩阵与黄金场景（台账不动，合同里写明），二期迁移不一致路径。

## 5. 待用户决定（决定前对应卡不开）

| # | 事项 | 影响 | 默认 |
|---|---|---|---|
| D1 | V1-G7-BATCH-BUDGET 剩余深水项（缓存输入资格、硬资源界、物理取消、费用上限）是否纳入 V1；纳入则经 scope_change 拆成可独立关闭的子条款 | 权重 2；不决定则该条永远不闭合 | 暂停开卡 |
| D2 | G7-BOUNDARY-RESERVE（权重 2）与 G7-WRITE-RACE（权重 1）：主控起草“无必要/有限修复”裁决包后，由用户或独立审查拍板 | 可移动 +2/+2 与 +0/+1 | 主控在 MAINT 间隙起草 |
| D3 | G6-PREREG：观察指标阈值、比较基线、成本门槛（真实日历时间不可补，越早冻结越好） | 解锁 G6-OBSERVATION/EFFECT | 主控起草，不自行冻结 |
| D4 | G7-RAG-QUALITY 验收要求人工标注，与 ISS-028“用户关闭为不需人工复核”冲突 | 权重 3 | 维持未满足 |
| D5 | 跨机器传递未完成切片是否允许 `wip/<卡号>` 分支（需改 CODEX.md 单人 main 纪律） | 额度中断时的交接方式 | 不建分支，补丁留本机并登记 |

## 6. 本机受限（不排进队列，列出所需条件）

| 条款 | 需要什么 |
|---|---|
| G3-REAL-MID、G4-INDUSTRY-PIT/ROE/CASH-PERSISTENCE/SUPPLIER-RAW、G4-VALUATION、G7-CORRECTIONS | 可连公告/财报源的环境或用户提供的合规原件，以及独立核验 |
| G6-OBSERVATION、G6-EFFECT | D3 冻结后持续积累的真实日历时间；EFFECT 另需付费 AI 授权 |
| G5-RECALL、G7-EXTRACTION | 明确的付费模型授权（EXTRACTION 可先做离线样本与错误分层） |
| G7-PORTABILITY → G7-LEGACY-MIGRATION → G8-OPERATIONS/HANDOFF/RELEASE | 第二台独立环境加外网 |
| G7-PERF-COST | 同环境的真实请求与费用基线（需外网）；离线只能测工程耗时 |
| G7-RAG-QUALITY | D4 决定 + 约 150 对人工标注 |

## 7. 卡号与台账条款对照（台账暂无机器字段，以此表为准）

| 卡号 | 条款 |
|---|---|
| G5-SOLVER-1 | V1-G5-SOLVER |
| G1-TXN-1 | V1-G1-TRANSACTION-MATRIX |
| G2-READ-1 | V1-G2-RESEARCH-READ |
| G5-CASHNAV-1 | V1-G5-CASH-NAV |
| G5-SNAPSHOT-1 | V1-G5-SNAPSHOT |
| G2-PACKET-1 | V1-G2-PACKET |
| 历史：T05-H…Q | V1-G7-BATCH-BUDGET；T05-D/F1/F2→WAIT-EXIT；T05-E/E2/E3→LATE-CACHE；T05-G→SESSION；T04-A…D→SERVICES；T04-E…L→DISPATCH；BUG-FINITE/READ→G1-FINITE |

## 8. 本轮复盘点

每完成 3 张卡或每周一次，按 WORKFLOW 第 10 节指标复盘，结果写 RESUME 一行；队列顺序随复盘调整，调整写在本节下方。
