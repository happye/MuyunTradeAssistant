# R9 独立验收审查报告（fusion 第二轮 R0–R8）

> 独立会话产出，实施方**原样收录**（不改一字）；实施方按报告完成 R9 收尾（文档收敛/
> 承接缺口登记/哈希回填），收尾动作见 EXECUTION_RECORD.md R9 节尾注。

审查者：独立会话（与实施方隔离）｜基线 main HEAD `980e925`｜只读审查，未修改任何文件
证据基线：全量 `pytest -q` 实测 **1222 passed, 2 skipped, 1 deselected**（43.6s，离线默认）；各卡目标测试逐文件实跑全绿；E0b/E7b/E2b-E4b 场景脚本实跑 exit 0；`research 600519` REPL 实跑；语料/报告/留样产物逐项开箱核对。

## 一、逐卡裁决表

维度顺序：implemented / connected / scenario_validated / empirically_validated / release_ready。★ = 与实施方自评不一致或账本缺陷。

| 卡 | 我的五维判定 | 实施方自评 | 核对证据（抽查条款） |
|---|---|---|---|
| **R0** d21b341 | PASS / PASS / PASS / PARTIAL / PARTIAL | 五维表：✅/✅/✅/PARTIAL/PARTIAL | **一致**。资格门 4 反例锁死；latest-only strict 拒（test_evidence_integration_and_pit_gate 已按新语义反转）；shadow 逐周期标注（shadow_v3）；E0/E1/E7 报告顶部注记在位 |
| **R1** 5c0fda2 | PASS / PARTIAL / PASS / PARTIAL / PARTIAL | 同左 | **一致**。ISS-114 映射边界锁死（边界/原值保留/隔离/留样）；探针为真（双样本交叉核定正文+769 文件留样）；connected PARTIAL 判定正确——`--capture` 链路已通到 research 命令，但主分析 `l` 路径仍不展示证据资格 |
| **R2** 2ab7c25 | PASS / PARTIAL / PASS / NOT_RUN / PARTIAL | 逐条款 PASS/NOT_RUN 如实 | 16 例语料 16/16 一致；运行器报告多维度在位（等级覆盖+12 类失败原因+成本留痕 llm_status=NOT_RUN）。**语料期望修正裁定：正当**——(a) 独立监督员驱动、changelog 留痕；(b) 修正方向更诚实而非凑绿；(c) 语料为零 AI 确定性夹具，冻结纪律的真实约束对象是 E5b 的 LLM 评测（NOT_RUN 未被污染）。程序瑕疵：修正后应视为修正条目即二次冻结，后续扩展必须新文件 |
| **R4** cddd191 | PASS / PARTIAL / PASS / NOT_RUN / PARTIAL | 逐条款 PASS/可见交付 PARTIAL | 能力ID 拆分坐实（capital_return_v1/valuation_range_v1 翻转回不可计算态）；弃用委托带「请改用新ID」note；漏斗 130→35 机器核对；分片预算硬顶/失败隔离测试在位。**遗留**：e1/e7 旧脚本仍消费弃用委托，"R7 迁移后删"未发生——R7 未迁移旧脚本（R9 删除条件表已补登） |
| **R3** 9b93d3f | PASS / PARTIAL / PASS / NOT_RUN / PARTIAL | 逐条款含 6=PARTIAL 如实 | 优先级链与 docstring/代码一致（空命题→UNESTABLISHED 等）；`is_accepted_version` 三元组绑定；active_refs 生命周期测试在位；条款 6"失败恢复未实现"如实 PARTIAL——确认 |
| **R5** 12fecbc | PASS / **PARTIAL（零生产消费方）** / PASS / NOT_RUN / PARTIAL | 条款 5=PARTIAL、7 带单写者注记 | 串行扣减坐实（remaining_cash 现金池→断言两行合计不超可部署且第二笔拒绝）；FIFO 批次/幂等重放/坏事件隔离/费用边界/无未来回撤通道测试全部在位。**★跨账本承接缺口**：AccountSnapshot/allocate/事件账本在 src/ 零生产消费方；R5 写"today 显示与 confirm 流联动随 R8"，但 R8 只交付 research 命令——**有脱账风险，必须补进 R9 阻断清单** |
| **R6** 12fecbc | PASS / PARTIAL / PASS / NOT_RUN / PARTIAL | 逐条款如实 | E0b 实跑复核：blocked=1、残余持仓按最后价估值（不虚构强平）；场景5 net_cash_gap=45.0 查明为预期税差（非记账漏洞）；DATED_RULES 4 条带来源。nit：减半前条目 source 是描述文字而非官方文件号；全市场现行规则复核确实未做（如实登记） |
| **R7** 980e925 | PASS / PARTIAL / PASS / NOT_RUN / PARTIAL | 逐条款如实；AI 臂 NOT_RUN 如实 | E7b 报告与脚本逐字段一致（七类 action_paths 全命中、EXIT+BLOCKED T+1 原因、四预算——重跑 exit 0 复现）；E_SPEC_V2 注册表诚实定级测试在位；E2b/E3b/E4b 实跑全 BLOCKED_DATA 如实 |
| **R8** 980e925 | PASS / PARTIAL / PARTIAL / NOT_RUN / PARTIAL | 逐条款 1=PASS(6/8)/3=PARTIAL/6=PASS(核心链路) | **实跑复核**：research 离线输出与账本/手册一致；R8_WALKTHROUGH 6/8 合成证据+P0 修正记录诚实。**性能声明边缘乐观**：独立复测 n=20 得 p50=0.125ms/p95=0.300ms（账本称 <0.1/0.2）——同数量级、纯函数量级结论成立，建议改口径"亚毫秒级"。nit：缺口列表打印总数但正文只显示前 5 条无省略号 |

### 与实施方自评的主要不一致（★汇总）

1. ★R2–R8 无五维度登记表（VALIDATION §1 明文要求）——账本仅 R0/R1 有五维表；R9 收尾应补齐（本报告上表已代为补齐判定）。
2. ★R5→R8 承接项脱账：today「可行数量或唯一阻塞字段」显示 + pos confirm 流与 AccountEventLog 联动——R5 写"随 R8"、R8 未做、R9 阻断清单未完整登记。必须补进。
3. ★R4"R7 迁移后删"未发生：弃用委托仍被 e1/e7 旧脚本消费——R9 删除条件表已补登，R4 节应注记。
4. ★R3/R7/R8 delivered_commit 三处未回填（R3=9b93d3f、R7/R8=980e925）。
5. ★R8 性能数字：复测 0.125/0.300ms——建议降格为"亚毫秒级"。

## 二、文档不一致清单（R9 验收5）

| # | 位置 | 问题 | 建议改法 |
|---|---|---|---|
| 1 | plan/fusion/RESUME.md:5,30,33 | 「R0–R9 均 TODO」与已交付并存 | 顶部加日期注记，正文保留历史不改写 |
| 2 | plan/fusion/iteration2/README.md:3,86 | 「设计已交付，R0–R9 均待实施」过时 | 加注记「R0–R8 已实施，R9 独立验收中」 |
| 3 | plan/fusion/iteration2/DATA_TRUST.md:3 | 「提案，未实施」——R1/R2 已实施 | 加实施注记 |
| 4 | plan/fusion/iteration2/RESEARCH_LOOP.md:3 | 同上 | 同上加注记 |
| 5 | plan/fusion/EXPERIMENTS.md:15（E1 行） | 「5568 只…产业 100」与权威产物矛盾（e1_run.log 市场全集 5221、detail.json INDUSTRY=75） | 数字改为 5221/50/75/5 |
| 6 | plan/fusion/ROLLOUT.md:15 | 「当前位置」停在 v0.8.20；晋级条件是第一轮旧口径 | 补 v0.8.22 现状行；晋级条件指向 VALIDATION §7 |
| 7 | 使用手册.md plan2 节 | 「还没有给你挂引用的界面（在路线图 R3）」——R3 已交付 research 自动挂引用 | 改为「R3 已交付 research 命令自动挂引用；plan2 手工挂引用界面仍未排期」 |
| 8 | 版本口径 v0.8.22 五处 | 一致 ✅ | 无需改 |

## 三、发布建议矩阵 + 阻断清单

### 逐能力发布建议

| 能力 | 我的建议 | 依据 |
|---|---|---|
| 可信数据提示/资格门/缺口话术 | **可发布** | R0/R1 反例回归 + 实跑可见；不改变投资主结论 |
| research 单股研究工作台（CLI） | **可发布（实验性）**，caveat：①「已核验主张可打开原文」当前不可达（--capture 只采财务不采公告正文）；②chat/Web/TUI 未消费同输入 |
| AccountSnapshot/allocate/事件账本 | **暂不发布到用户路径**（零生产消费方）——接线批完成 today 显示后随界面发布 |
| shadow/capture | **维持 capture_only**。resolve_fusion_mode 解析器实现方向正确——随 R9 commit 一并验收 |
| 融合主决策 opt_in | **不晋级**。VALIDATION §7 三下限全部未达：影子账本仅 11 条且全部是 shadow_v1 旧 schema，v3 逐周期记录为零——0/0 不是通过 |
| default | **不晋级**（无策略效果独立门） |

### 阻断/未完成清单（合并后）

1. R3 步骤级缓存/失败恢复未实现（如实 PARTIAL）。
2. 【新增★】R5 承接缺口：today 可行数量/唯一阻塞字段显示、pos confirm 流→AccountEventLog 联动——无卡承接，须登记。
3. R8 验收3：chat/Web/TUI 三端一致性仅 CLI；②③ 真实用户试用未发生。
4. E1b/E5b AI 真跑 NOT_RUN；E2b/E3b/E4b 严格历史 BLOCKED_DATA；E6b 全量 rerun 未做。
5. ISS-114 其余 balance 字段待逐字段核定；映射待逐份原始财报确认升级。
6. factor_compute 弃用委托删除债（依赖 e1/e7 迁移）；ShadowDiffRecord v2 兼容字段删除债——已登记。
7. DATED_RULES 全市场现行规则官方复核未做（规则表当前只喂回放/场景，不下真实单）。
8. 文档收敛八项 + 账本三处哈希回填 + R2–R8 五维表补齐。
9. research CLI 缺口列表截断显示（cosmetic）。

## 四、一行总结论

**第二轮 R0–R8 交付验收通过（有条件）**：八张卡账本声称的测试、场景、探针经我独立实跑全部复现，无一处「账本 PASS 但代码做不到」；五处★均为账本形式缺陷或承接登记缺口而非语义造假；发布口径应为——资格门/提示/缺口话术与 research CLI（实验性）可发布，影子维持 capture_only，opt_in/default 因影子数据 0/0/0 明确不晋级，R9 收尾须完成文档收敛、承接缺口登记与哈希回填后方可关账。
