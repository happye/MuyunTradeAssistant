# 融合项目恢复入口与Codex架构师记忆

**更新2026-10-02｜受审HEAD 28c32cf｜最新独立审批R15。** 身份以用户指派为准，角色规则见[CODEX.md](../../CODEX.md)。

## 恢复顺序

1. AGENTS.md→CODEX.md→根[MILESTONES](../../MILESTONES.md)：R01–R09/G0–G8/T01–T07固定，不另起路线。
2. [STATUS](STATUS.md)→[R15审批](iteration9/R15_ACCEPTANCE.md)→[第九轮P0/P1与M2设计](iteration9/DELIVERY_PLAN.md)。
3. 比较实际HEAD/工作树与[iteration9指纹](iteration9/REVIEW_INDEX.json)，查最新实施报告，按变化组复核消费者。R12–R14已通过范围和M2原型不重复审批。
4. 简短说明恢复进度后继续，常规隔离验收/文档维护已授权，不重复问确认。

## 最新进度

- O批产品0de6d38，文档28c32cf；v0.8.27。R15独立33文件462绿；13合同项9绿4红（R14六项全绿、新增七项三绿四红）。实施方全量1521/2skip/1deselect仅引用未独立重跑。
- **O1/O2接收，Y3/Y4关闭**：source_time共用解析；真正capture→存储→分母未来naive为0/过去为1；LONG未知权重HOLD、已知权重ADD、硬退出优先。表v4/shadow_v10候选追认。
- **O0部分接收，三组残余**：Z1 P1 PARTIAL+旧零数量投影仍NONE/0；Z2 P1合法YAML但记录无法解码时holding_entries返回空且incomplete=False，真la说无持仓；Z3 P2聊天混合清单漏账本股，以及账本有仓的l/la启动提示仍说FLAT（实际策略ctx已正确，不夸大成策略回退）。
- **K1未冻结，M2生产未放行，capture_only不变**。P0账户状态组合矩阵+异常返回、P1共享清单/ctx展示READY。M2原型已接收；第九轮已细化只读查询接口、入口装配、八类验收为DESIGN_READY，前置通过才接生产，不重做原型。
- 下一版建议v0.8.28、表v4保持，账户输入语义修正随shadow_v11候选登记；旧协议原件保留、只诊断不追认。新交付时按指纹增量复验；无交付不重跑已知红例。
- 用户补充真实账户RATIO_ONLY、正常la零变化：接收为兼容说明，不强制建数量账本；但投影解码异常也影响旧模式。当前33文件462实证，历史N批34文件462说法未附旧清单，不背书且不作代码阻断。

## 职责与保护

Codex负责架构/只读审查/隔离探针/审批/任务卡/里程碑，Claude Code实施产品；未经改派不改产品源码、产品测试、配置、真实账户，不擅自Git提交。一个实际持仓一个主意图，中长期独立；查看研究只读，刷新/接受显式。正确性、可用性与效果分列，未达G8不称整体完成。

测试用iteration9 runner：导入前临时HOME，阻网络/子进程/真实.muyun，限制临时根写入、禁可选RAG、原生FAISS写守卫；7保护文件哈希和集合核对。外源尝试被阻断≠没有尝试。历史J2假隔离和R12原生写绕过事件不再重犯。

## 未完成长期项

- G3完整MID VALID样板未做，checkpoint TRUE不能代替；G4 LONG财务/质量/估值与G5组合仍部分完成。L3六点原件历史范围保留；缺URL、更正/人工/PIT/异机获取/波次B待办，五条旧登记不清理。
- today持仓卡并集、chat analyze_industry和TUI/Web清单仍未归并，登记G2/G7/T01；不因本轮暂不作为冻结前置而算关闭。管理命令投影视图语义可保留。
- E1b/E5b NOT_RUN（前置/付费授权未齐），E2b–E4b BLOCKED_DATA；K4无已验收合格真实样本。冻结不是opt_in/默认策略晋级，不用模拟填真实分母。
- B1墙钟变化生成research revision，M2查询不得调用run(capture=False)；B2微型账本30次约15ms不是性能瓶颈证明。G7优化先测量。
- Codex额度守护仅[方案](iteration4/CODEX_QUOTA_RESUME_PLAN.md)，后台恢复/监控未部署，不承诺聊天停止后持续运行。

## 工作区与断网恢复

R14资产已由28c32cf落库；iteration7/acceptance_probes.py仍未跟踪，必须保留。R15文档/探针/runner/结果/指纹位于iteration9，当前入口同步只落盘未提交。原有portfolio.yaml、knowledge/index/embedder_metadata.json、备份/.claude/.zcode/教材/记忆资料均保留；本轮保护检查全不变。

断网先查本地结果和HEAD，勿覆盖旧日志或重跑已完成检查。跨机器接手须携带未提交架构产物，不只checkout旧HEAD。详细合同和回执以R15及任务卡为准。
