# 融合架构研究：恢复入口

更新：2026-09-26｜**2026-09-27 更新：R0–R8 已实施**（逐卡账本与五维判定见 [iteration2/EXECUTION_RECORD.md](iteration2/EXECUTION_RECORD.md)；独立验收裁决见 [iteration2/R9_INDEPENDENT_REVIEW.md](iteration2/R9_INDEPENDENT_REVIEW.md)——有条件通过，opt_in/default 不晋级）｜本轮架构复核基线 `5b2e262` / v0.8.20。**恢复先读 [iteration2/EXECUTION_RECORD.md](iteration2/EXECUTION_RECORD.md)（R0–R9 账本）**。

第一轮 F0–F9 实施方报告全部 VERIFIED，另有四批接线和 E 实验。架构师已核对报告、相关源码和关键实验，作出**工程交付有条件接受，融合主决策暂不晋级 opt_in/default**的裁决。第二轮六份设计已落盘，R0–R8 已交付、R9 收尾中；历史实施证据见 [EXECUTION_RECORD.md](EXECUTION_RECORD.md)，原始汇报见 [ARCHITECT_REPORT.md](ARCHITECT_REPORT.md)。

## 用户授权与目标

- 本任务只写规划、设计、研究和实施任务卡；不修改产品源码、配置、测试或真实持仓，不提交代码。
- 用户要求 bz（笨韭）、scan（暮云策略）、AI 分析与其他能力深度融合，让用户得到清晰、有依据的下一步行动。
- 同时支持中期、长期持有，不能被迫只选一种；不得把亏损的中期交易自动解释为长期投资。
- 角色为总架构师；既有 plan/ 的 M1–M6、C1–C6 已有其他 Agent 实施记录，本轮是新的能力设计。

## 本轮已核实事实

1. latest-only 财务值配旧 pubDate 可进入 strict 快照；日期过滤不等于版本可得性证明。
2. 核心逻辑评估 facts 列表非空即可 VALID；shadow 另写同类判断。CLI虽去空白，也没有核验任意事实文本的证据。
3. claim 引用匹配但正文相反仍通过现有检查；无 evidence_pool 仍报告 citation_resolves。返回仍为 MODEL_INFERRED，不夸大为已发生错误交易。
4. cash_nav=None 仍可求出数学新增权重，需要区分理论预算与可执行数量；计划双周期研究还需一个实际持仓主意图与完整版本历史。
5. E0未触发T+1批次拦截、AI关闭；E1单截面/不同预算；E5是提取层；E6使用非严格行业/未来压力输入；E7全WAIT且无预算动作。不能把运行成功等同全部策略有效。

实际只读函数探针5项已完成，复现脚本及边界见 [iteration2/PROBES.md](iteration2/PROBES.md)。ISS-114财务真值本轮未独立核定；官方Baostock网页未取得可读字段正文，不把第三方转载当定义证据。

## 第二轮成果与下一步

- iteration2/README：验收矩阵、对五项实施选择的裁决、S0–S3里程碑。
- iteration2/DATA_TRUST：历史版本、财务字段语义、异常隔离、claim内容核验、增量快照。
- iteration2/RESEARCH_LOOP：自动研究与计划草稿、中长期独立评估、共享事实、账户预算与统一用户体验。
- iteration2/VALIDATION：E0–E7分层实验、有效场景命中、真实计划影子、发布门。
- iteration2/TASKS：R0–R9主责文件、依赖、验收与回退，全部待实施。
- iteration2/PROBES：本轮实际探针与限制。

执行下一步 R0，再按接口分工 R1/R2/R5/R6；R3/R4完善自动研究后接 R8，R7/R9负责效果验证与发布验收。不要先做E2/E4严格财务收益调参、先加总评分或默认启用融合主决策。

第一轮 RESEARCH/PROBES 保留为旧基线证据，不能当作当前缺陷清单。第二轮验收结论限制实施报告中的扩大表述，不改写历史运行数据。

## 恢复指令

“先读 plan/fusion/RESUME.md、plan/fusion/iteration2/README.md 与 TASKS.md，核对 git 状态、执行账本和相关源码。继续作为架构师设计和验收，不直接改产品代码。中期和长期都支持。”

仓库文件是跨工具交接依据；本轮架构设计未提交，需保留当前工作区。恢复时以文件和新HEAD重新核对，不假定旧工具过程一直存在。未建立定时重试对话任务。

范围：只修改plan文档及相关交接记忆。未运行全量pytest、真实AI实验或新收益回测，未提交Git。既有用户portfolio与Claude配置等外部改动保持原样；第一轮实施方的1121 tests计数未由本轮重跑。

交付检查：第二轮6份新文档及7份更新入口共13份已检查，47个本地链接有效、围栏配对、R0–R9编号齐全，文档内探针脚本编译通过；`git diff --check`通过。5项只读函数探针执行成功，详细结果在第二轮PROBES中；未以文档检查冒充产品测试。
