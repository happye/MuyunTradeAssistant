# 融合项目当前实施与审批状态

**更新：2026-10-02（R15独立审批）｜受审HEAD：28c32cf｜v0.8.27｜整体未完成。** 长期路线见[MILESTONES](../../MILESTONES.md)，恢复见[RESUME](RESUME.md)。

## 当前裁决

[O批交付](iteration8/实施方报告_O系列交付.md)已完成[R15](iteration9/R15_ACCEPTANCE.md)：**O1/O2接收，Y3/Y4关闭；O0部分接收，Z1–Z3待补。K1未冻结，M2/K2b生产未放行，capture_only不变。** R12–R14已通过范围不重复审批，M2原型仍按R14接收。

独立**33文件462 passed**；合同13项**9 passed/4 failed**（R14六项全绿；新增7项3绿4红，分三组发现）。O1真实捕获→存储→报告的未来naive/过去naive正反例通过。实施方全量1521 passed/2 skipped/1 deselected仅引用未独立重跑。测试7保护文件哈希/知识库集合均未变。[指纹与证据](iteration9/REVIEW_INDEX.json)。

| 块 | 已接收 | 剩余/下一动作 | 里程碑 |
|---|---|---|---|
| O0账户/批量 | R14具体异常反例、账本独有股进la/CLI、混合去重/一次快照、单股失败续跑 | Z1 PARTIAL+零投影仍判空；Z2记录解码失败清单却完整→P0 | G1/G2/G5/T01 |
| O0展示 | 单股最终包和摘要消费ctx范围通过 | Z3 chat混合清单漏股、l/la启动提示仍FLAT→P1 | G2/G7/T01 |
| O1时间 | 共用解析，naive时刻/偏移/日精度矩阵，捕获落盘分母一致 | Y3关闭；无变化不重开设计 | G1/G6/T03 |
| O2 LONG规则 | 行8未知权重HOLD、已知权重ADD、硬退出优先，表v4 | Y4关闭；LONG完整数据/研究仍未完成 | G4/G6/T03 |
| K1观察合同 | shadow_v10候选、旧协议独立桶 | 账户残余阻断冻结；不晋级策略 | G6 |
| M2日常研究 | 原型25断言R14接收；接口/入口/验收方案已细化 | DESIGN_READY，P0/P1过门+合同冻结前不接生产 | G2/G3/G7/T02 |
| L2公开研究 | 公开引用/checkpoint TRUE | 完整MID VALID证据样板未完成 | G3 |
| L3原件 | 六点核定范围通过 | 缺URL、更正/人工/PIT/异机获取及LONG波次B待办 | G4/G5/T07 |
| K4/效果实验 | 无已验收合格真实样本 | E1b/E5b NOT_RUN；E2b–E4b BLOCKED_DATA；其余前置未齐 | G6 |
| 多端与工程 | 请求级事实、部分清单已接线 | today卡并集/analyze_industry/TUI/Web清单尚未归并；T01–T07未整体关闭 | G2/G7 |

## 当前任务与披露

[P0/P1任务与M2设计](iteration9/DELIVERY_PLAN.md)：P0固定账户状态组合矩阵和异常返回；P1共享清单/ctx进入查仓和启动提示。Claude Code实施、Codex独立复验。建议v0.8.28、表v4不改，账户输入语义变化随shadow_v11候选登记；旧观察不改写、不追认。

**[P0/P1 已实施交付](iteration9/实施方报告_P系列交付.md)（2026-10-02，待架构师独立验收）**：Z1 PARTIAL保护贯穿投影分支（旧零投影→UNKNOWN/None/待对账）；Z2 holding_entries投影枚举失败传播incomplete（账本独立持仓保留、la不说"当前无持仓"）；Z3 chat get_portfolio全路径共享清单+ l/la启动提示按ctx三态（不再同屏宣称FLAT；pos存在但ctx未对账追加缺口原因）。固定验收矩阵9行参数化落回归（tests/core/test_p0_account_exception_matrix.py 14项(含guard补row10) + tests/chat/test_p1_shared_portfolio_view.py 9项）；多端支持范围转录[ISSUES.md ISS-116](../../ISSUES.md)持久台账。目标回归35文件485 passed（=R15基准462+23）；guard两轮复核可合入；全量1544/2/1与保护哈希见 [FULL_TEST_RESULTS](iteration9/FULL_TEST_RESULTS.txt)/[TARGETED_TEST_RESULTS_P](iteration9/TARGETED_TEST_RESULTS_P.txt)。实施账本 [iteration9/EXECUTION_RECORD](iteration9/EXECUTION_RECORD.md)。

正常无数量账本RATIO_ONLY兼容接收，不强制用户迁移；正常la无差异不是拒收理由。但投影解码失败同样影响旧比例模式，不能笼统说全部异常合同只在数量账户生效。历史N批462解释仍缺旧清单，不背书；本轮33文件462已实证。

## 协作与恢复

R14文档已随28c32cf落库；R15资产和当前入口同步仅落盘未Git提交。iteration7旧探针未跟踪、本地持仓/知识库/备份/.claude/.zcode/教材均保留。禁止清理真实状态、自动补投影或覆盖历史证据。

[R13](iteration7/R13_ACCEPTANCE.md)→[R14](iteration8/R14_ACCEPTANCE.md)→[O实施账本](iteration8/EXECUTION_RECORD.md)→[R15](iteration9/R15_ACCEPTANCE.md)。新交付按最新指纹增量复验；无新交付不重复已知红例、不代写产品。付费实验未授权，非实盘下单，整体未完成。
