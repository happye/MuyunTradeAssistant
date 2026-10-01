# 融合项目恢复入口与Codex架构师记忆

**更新2026-10-01｜受审HEAD 73ba005｜最新独立审批R13。** 进度共享；身份仅适用于用户指派Codex总架构师的会话，Claude Code保持实施职责。角色主入口[CODEX.md](../../CODEX.md)，用户改派优先。

## 恢复顺序

1. 先读AGENTS.md共同规范、CODEX.md角色；根[MILESTONES](../../MILESTONES.md)固定R01–R09、G0–G8、T01–T07和结项门槛。
2. [STATUS](STATUS.md) → [R13审批](iteration7/R13_ACCEPTANCE.md) → [第七轮N0–N2任务](iteration7/DELIVERY_PLAN.md)。
3. 比较实际HEAD/工作树与[最新指纹](iteration7/REVIEW_INDEX.json)，先找最新实施方报告，只复核变化组及消费者。历史[R12](iteration6/R12_ACCEPTANCE.md)/原型按需读，不重复审批。
4. 简短告知恢复进度与下一动作后继续。常规接手、隔离验收及文档维护已有授权，不再请求确认。

## 职责与长期目标

- Codex负责架构、隔离探针、独立审批、任务卡和持续里程碑；Claude Code实施产品。未经改派，不改产品源码/产品测试/配置/真实账户，不擅自Git提交。
- 用户要求固定总体目标、架构和完成标准，再按依赖小步验证；不能想到一批做一批。每次交付/审批同时更新MILESTONES、STATUS、本页。未达G8不称整体完成。
- 中长期并存；笨韭产业/公司逻辑、技术纪律、AI事实反证解释协同；一个实际持仓一个主意图，不因浮亏转长期。today/l/la/批量输出清楚同源，研究查看只读、刷新和接受显式。
- 正确性/可用性/投资效果分开。capture_only不变；真实有效观察和分周期证据未过门不晋级。LONG不能以MID/延长时间替代。
- 真实持仓、知识库、账户备份、.claude/.zcode、教材与记忆备份不属于清理范围。共享文档不改变用户指派的角色。

## 最新裁决与下一动作

- M0/M1已提交：21da3ff/96f6f1d，收尾73ba005，v0.8.25。R13已独立审批，**分项接收、整卡退回补齐**。381目标回归绿（27文件）；17新合同场景15断言红/2正例绿，详见iteration7结果，不是15个独立P1。实施方1440 passed/2 skipped/1 deselected全量本轮未独立重跑。
- W1数量投影存在的最终适配修复通过；W2旧收盘来源日期修复通过。不重开这两个具体正例，但全链仍有X1/X2/X3遗漏。
- **X1 P1**：ctx=账本HELD/weight=None，策略仍从投影给.1或FLAT/0；chat无投影跳过ctx，账本100股时上游CLOSE_ALL经真实最终适配变WAIT；账本读取异常变NONE/0。建议/摘要/影子仍有pos存在门。N0统一消费且保持账本只读，不自动补真实投影。
- **X2 P1**：shadow正比例HOLD记录.1/KNOWN但规范包target=None；两臂execution动作枚举/状态枚举混用；任意非空policy_version仍计有效。附属P2：UNKNOWN技术减仓给REDUCE、HELD未知权重+budget=True给ADD（后者仅公开接口边界）；旧双周期桶elif漏LONG。N1修规范包投影/版本组合资格/规则边界。
- **X3 P2**：坏日期/时间/垃圾尾缀与同日未来时刻可算.1权重；当前NAV暂无生产写入方，不夸大真实影响，但M1资格承诺未达。N2严格解析、保留NAV偏移、交易所时区、按精度判未来；预取fetched_at精确化是诊断项。
- **X4 M2原型**：正常反复查看不写盘已独立验证；损坏库视图仍报告空库，原失效零写断言自比较。可补原型/测试准备，**N0–N2联合独立过门、修正合同冻结前不得接生产**。
- 追认v0.8.25/表v2/shadow_v8命名方向，K1不冻结；下一修复版本v0.8.26，表规则v3、shadow_v9候选。旧v8及更早原样保留只诊断，不追认新合同有效样本。冻结不是策略晋级授权。
- 下一动作：先核对Claude Code的N0–N2新交付；有交付就按本轮指纹增量审批，无交付不重跑已知红例，不越权代写产品。任务卡已可开工，不重复规划一套。

## 持续保留的未完成项

- L2公开引用/checkpoint TRUE已验收，完整MID VALID样板待G3。L3六点原件离线核定范围保留；J1缺URL、更正/人工/PIT/异机获取/波次B消费者未完成，五条真实旧登记不清理。
- E1b/E5b NOT_RUN：前置不齐且付费未授权；E2b–E4b BLOCKED_DATA；K4无已验收真实合格样本。数据/授权受阻不是完成，也不妨碍非依赖工作。
- B1墙钟+1秒导致research新revision，所以M2只读查看/显式刷新；不删as_of散列语义。B2请求快照首要价值是同版本，微型账本30次约15ms不是性能瓶颈证明。G7性能/依赖重构先测量。
- J2假隔离曾写真实portfolio；R12原生FAISS绕过Python审计已补守卫并披露。用iteration7 runner：构造前注入路径、临时根写守卫、阻网络/子进程/真实.muyun、禁可选RAG、原生FAISS守卫、保护7文件哈希与集合。HOME隔离本身不够。
- Codex额度守护仅[方案](iteration4/CODEX_QUOTA_RESUME_PLAN.md)，后台自动恢复/监控未部署。不承诺聊天停止后持续工作。

## 工作区交接快照

- 实际HEAD为73ba005；旧RESUME的679afa6和“iteration6未跟踪”已过期：R12资产由52946b7落库，M批随后提交。
- R13审批/任务/探针/指纹在iteration7；本轮入口文档及learnings更新**仅落盘，未Git提交**。跨机器/新工作副本必须携带这些未提交文件，不能只checkout HEAD。
- 原有portfolio.yaml、knowledge/index/embedder_metadata.json、备份、.claude/.zcode、教材及本地资料保持原状；最终测试7个保护文件hash/知识库集合不变。网络中断恢复先查产物，避免重复生成或覆盖历史结果。
