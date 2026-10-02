# 融合项目当前实施与审批状态

**更新：2026-10-02（O0–O2 交付，待独立验收）｜交付基线：基线 `4eb5c1b` + O 批实施提交｜v0.8.27｜整体未完成。** 长期目标见根[MILESTONES](../../MILESTONES.md)，恢复见[RESUME](RESUME.md)。R14 审批状态（下方"当前审批"）未被本更新覆盖。

## 当前审批

[N系列交付](iteration7/实施方报告_N系列交付.md)已完成[R14独立审批](iteration8/R14_ACCEPTANCE.md)：**分项接收，整卡补齐；K1不冻结，M2/K2b生产不放行，capture_only不变。** R12/R13已通过范围不重开。

独立30文件**423 passed**；R13合同重放**17/17通过**；新增6条检查**5失败/1通过**，归为Y1–Y4（不是5个P1）；M2原型**25项断言通过**。实施方全量1482 passed/2 skipped/1 deselected仅引用，未独立重跑。实施报告462与本次423须按收集清单核对，不算39个失败。7保护文件哈希/知识库集合不变；[证据索引](iteration8/REVIEW_INDEX.json)。

| 块 | 接收范围 | 当前剩余 | 长期里程碑 |
|---|---|---|---|
| K0a/L1/M0/N0账户 | 策略ctx、单股/聊天无投影退出、部分异常分支 | Y1零投影+读失败/PARTIAL无lot仍判空；Y2 la漏账本独有持仓→O0 | G1/G2/G5/T01 |
| K0b事实/命题 | 既有明确范围通过不变 | LONG规则/数据不完整，不整体背书自然语言 | G1/G3/G4 |
| K0c/L0/N1计划 | 规范两臂/生成版本门/旧桶计数、MID行5/6 | Y4 LONG行8未知权重仍ADD→O2；完整MID正例待G3 | G2/G4/G6/T03 |
| K2a/L2公开研究 | 公开引用/checkpoint TRUE通过 | 完整MID评估正例，不以checkpoint替代VALID | G2/G3/T02 |
| K1/M1/N2观察合同 | 估值严格时间/NAV偏移/finite/原fetched_at | Y3 shadow未来naive仍有效→O1；shadow_v9未冻结 | G1/G6/T03 |
| K2b/M2统一l | 原型损坏库/零写/旧方法/重启，25断言通过 | O0–O2联合独立过门+合同冻结前生产不放行 | G2/G7/T02 |
| K3/L3原件与规则 | 6/6原件离线复验历史范围接收；PARTIAL | J1缺URL、更正/人工/PIT/异机获取、B/C/规则未完成 | G4/G5/T07 |
| K4真实观察 | 未验收合格真实样本 | 修正合同冻结后积累；旧v9及更早不追认 | G6 |
| E1b/E5b | NOT_RUN | 前置不齐且未获付费授权 | G6 |
| E2b–E4b | BLOCKED_DATA | 严格历史信息集/覆盖不足 | G6 |
| E6b及其他效果 | NOT_RUN/待证据 | 账户组合资格/场景/指标冻结后回放 | G5/G6 |
| 性能/跨端/必要重构 | PARTIAL | T01–T07持续维护；B2不是性能瓶颈证明 | G7 |

## 当前执行窗口

[第八轮O0–O2任务卡](iteration8/DELIVERY_PLAN.md)已交待实施职责与验收边界：O0异常账户+持仓集合；O1同源时间资格；O2 LONG未知权重。Claude Code负责产品实施，Codex负责复验。建议v0.8.27/表v4/shadow_v10候选，旧观察原件保留。

**[O0–O2 已实施交付](iteration8/实施方报告_O系列交付.md)（2026-10-02，待架构师独立验收）**：Y1–Y4 全部红→绿——Y1 异常账户（读取失败+旧零投影/PARTIAL无幸存lot）不再判空仓（UNKNOWN/None/待对账、不反推数量）；Y2 la/CLI批量清单经同一请求快照形成（账本独有持仓到达同次分析/终态/影子记录；snapshot==1+下一请求新版本；异常不输出"当前无持仓"），三处扫描排除集与 chat 查仓同类接线；Y3 时点解析收敛 `src/core/source_time.py`（naive按上海实际时刻，估值门as_of参数化），**shadow_v10候选**；Y4 LONG行8未知权重冻结HOLD，**表v4**（受影响行8，None推导路径零变化）。口径核对：同HEAD复现30文件423 passed；33文件（+O批3）=462 passed（423+39）；462/423=收集文件集不同。guard审查1×P0+2×P1+3×P2全部修复后复核可合入；全量1521 passed/2 skipped/1 deselected、7保护文件哈希不变。证据见 [FULL_TEST_RESULTS](iteration8/FULL_TEST_RESULTS.txt)/[TARGETED_TEST_RESULTS_O](iteration8/TARGETED_TEST_RESULTS_O.txt)。实施账本 [iteration8/EXECUTION_RECORD](iteration8/EXECUTION_RECORD.md)。

追认v0.8.26/表v3/shadow_v9命名，不等于冻结或策略晋级。正常账本缺席的RATIO_ONLY兼容通过；decision_rule_version同表达式不另列阻断，不宣传为通用兼容屏障。预取fetched_at及摘要ctx已实现，不再笼统留后续；具体未覆盖项以Y1–Y4为准。

## 数据与协作边界

- 不自动修真实账户/投影，不覆盖历史观察/核定登记；真实库五条旧登记保留。
- R14产物与入口文档本轮仅落盘未Git提交；此前本地持仓/知识库/资料变更保留，iteration7旧探针仍未跟踪。
- 计算成功、事实核实、命题成立、计划接受、有效观察、投资效果分列。零付费AI，非实盘下单，主策略不晋级。

## 历史与恢复

[F](EXECUTION_RECORD.md)→[R](iteration2/EXECUTION_RECORD.md)→[J](iteration3/EXECUTION_RECORD.md)→[K](iteration4/EXECUTION_RECORD.md)→[L](iteration5/EXECUTION_RECORD.md)→[M](iteration6/EXECUTION_RECORD.md)→[N](iteration7/EXECUTION_RECORD.md)→[R13](iteration7/R13_ACCEPTANCE.md)→[R14](iteration8/R14_ACCEPTANCE.md)。

最新增量基准为[iteration8/REVIEW_INDEX](iteration8/REVIEW_INDEX.json)。无新交付不重复已知红例，不越权代写产品；交付/审批同步MILESTONES、STATUS、RESUME。
