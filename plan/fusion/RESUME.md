# 融合项目恢复入口与Codex架构师记忆

**更新2026-10-02｜受审HEAD 2b98842｜最新独立审批R14。** 身份以用户指派为准；Codex角色入口[CODEX.md](../../CODEX.md)，Claude Code保持实施职责。

## 恢复顺序

1. AGENTS.md→CODEX.md→根[MILESTONES](../../MILESTONES.md)：固定R01–R09、G0–G8、T01–T07及结项门槛。
2. [STATUS](STATUS.md)→[R14审批](iteration8/R14_ACCEPTANCE.md)→[第八轮O0–O2任务](iteration8/DELIVERY_PLAN.md)。
3. 比较实际HEAD/工作树与[最新指纹](iteration8/REVIEW_INDEX.json)，找最新实施报告；只复核变化组及消费者。R13历史审批不重复，旧探针保留。
4. 简短说明恢复进度和下一动作后继续；常规隔离验收/文档维护已授权，不再请求确认。

## 最新进度与下一动作

- N批产品154b556，文档5c0f51d，HEAD2b98842；v0.8.26。R14分项接收：30文件423绿；R13的17合同反例全绿。实施方称462，需给文件/用例清单核对；全量1482/2skip/1deselect仅引用未独立重跑。
- 新增6检查5红1绿；另M2原型包装测试25断言全绿。Y1 P1异常账本+零数量投影或无幸存lot仍NONE/0；Y2 P1 la先看投影清单，漏账本独有持仓；Y3 P1影子naive时刻只比日期，同日未来可计有效；Y4 P2 LONG行8已持有但权重未知+预算True仍ADD（公开策略接口边界，不夸大当前影子生产可达性）。
- 追认v0.8.26/表v3/shadow_v9标识；**K1未冻结、M2生产未放行、capture_only不变**。R14接收M2原型/X4，不重做旧审批；O0–O2联合独立过门且合同冻结才接生产。
- [O任务](iteration8/DELIVERY_PLAN.md)READY：O0异常状态+批量持仓集合（G1/G2/T01）；O1估值/影子统一精度时区（G6/T03）；O2 LONG行8（G4/T03）。下一版建议v0.8.27、表v4/shadow_v10候选，旧v9及更早不追认当前样本。
- 正常无账本RATIO_ONLY兼容已独立通过；不得扩大成异常账本也可判空的豁免。捕获时规则版本同表达式不另列阻断，真实当前表执行+旧协议分桶有效，不宣传通用兼容屏障。fetched_at/摘要ctx已修，只记录具体剩余。
- 下一动作：有O批新交付即按指纹增量复验；无新交付不重跑已知红例，不自行代写产品或提前启动M2。用户授权的协作由任务卡持续承接。

## 职责与长期目标

Codex负责架构/隔离探针/审批/任务卡/里程碑；未经改派不改产品源码、产品测试、配置、真实账户，不擅自Git提交。保持Python单体、固定所有权、同源终态；中长期独立，一个实际持仓一个主意图。研究查看只读，刷新/接受显式。正确性、可用性、投资效果分开；未达G8不称整体完成。

## 持续未完成项

- L2公开引用/checkpoint TRUE通过，完整MID VALID样板待G3；LONG数据/估值待G4，组合待G5。L3六点原件复算范围保留；J1缺URL、更正/人工/PIT/异机获取/波次B未齐，五条真实旧登记不清理。
- E1b/E5b NOT_RUN（前置/付费授权未齐）；E2b–E4b BLOCKED_DATA；K4无已验收合格真实样本，数据受阻不是完成。真实观察不拿模拟填满。
- B1墙钟+1秒生成research新revision，M2用只读查看解决、不删as_of散列语义。B2微型账本30次约15ms不证明性能瓶颈，G7先测量再优化。
- J2假隔离曾写真实portfolio；R12原生FAISS绕过Python审计已补守卫。使用iteration8 runner：导入前临时HOME，阻网络/子进程/真实.muyun，临时根写守卫、禁可选RAG及原生FAISS写守卫，7保护文件哈希/集合核对。不能只改HOME自称密闭。
- Codex额度守护仅[方案](iteration4/CODEX_QUOTA_RESUME_PLAN.md)，未部署后台自动恢复/监控，不承诺聊天停后持续运行。

## 工作区交接

- R13文档已由5c0f51d落库；`iteration7/acceptance_probes.py`仍未跟踪，必须保留。R14审批/任务/runner/结果/指纹在iteration8，当前入口和learnings同步仅落盘、未提交。
- 原有portfolio.yaml、knowledge/index/embedder_metadata.json、备份、.claude/.zcode、教材及记忆资料均保留。测试7保护文件哈希/集合未变，不将用户dirty当作待清理项。
- 新机器不能只checkout旧HEAD接手，需携带当前未提交架构产物。断网恢复先查成果，避免覆盖历史结果。
