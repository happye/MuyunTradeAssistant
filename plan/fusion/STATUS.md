# 融合项目当前实施与审批状态

**更新：2026-10-01（M 批交付）｜受审HEAD：679afa6（M0 `21da3ff`/M1 `96f6f1d` 待独立复验）｜整体未完成。** 长期愿景、能力里程碑与必要重构统一在根目录 [MILESTONES.md](../../MILESTONES.md)；本页只记录当前交付、审批和下一动作。恢复职责见 [RESUME](RESUME.md)。

## 当前审批

上一轮：实施方[L系列报告](iteration5/实施方报告_L系列交付.md)已完成独立审批：[R12](iteration6/R12_ACCEPTANCE.md)——分项接收，L0/L1退回补齐（W1/W2两P1）。K1暂不冻结，K2b生产归并暂不放行，fusion effective仍capture_only。

**M 批已实施待独立复验**（实施方报告[实施方报告_M系列交付](iteration6/实施方报告_M系列交付.md)，逐卡账本[EXECUTION_RECORD](iteration6/EXECUTION_RECORD.md)）：M0 修W1（AccountContext/RequestAccountFacts 同源闭环，规范动作视图19场景对齐，表版本v2）、M1 修W2（三时点分离，shadow_v8）、M2 仅隔离原型。目标回归420全绿、隔离离线全量见[FULL_TEST_RESULTS](iteration6/FULL_TEST_RESULTS.txt)。**实现≠审批；W1/W2修复以架构师按修正后源码指纹的独立复验为准。**

| 块 | 当前状态 | 剩余/承接 | 长期里程碑 |
|---|---|---|---|
| K0a/L1账户 | 策略读数/总权重未知通过；W1修复已实施 | M0同源闭环待独立复验；复验过后裁定K1冻结 | G1/G5/T01 |
| K0b事实/命题 | 带明确范围限制通过 | 只认证已支持门；LONG规则/数据仍不完整，不能整体背书自然语言 | G1/G3/G4 |
| K0c/L0计划/去重 | V2当前arms字段变化指纹通过 | B1墙钟变化使研究revision增长；M2只读视图原型16场景通过、生产接线待放行 | G2/G6/T03 |
| K2a/L2公开研究 | 公开引用与检查点TRUE通过 | 全MID评估正例仍UNESTABLISHED，完整样板待G3 | G2/G3/T02 |
| K1/L0观察合同 | 字段门进步；W1/W2修复落地 | shadow_v8协议（旧v7/v6只诊断不追认）；M0/M1联合复验后裁定冻结 | G6/T03 |
| K2b统一l | M2限定设计+原型已验证，生产未放行 | M0/M1独立过门后实施只读研究视图；不自动重跑生成草稿 | G2 |
| K3/L3原件与规则 | 可跟踪清单与6/6离线复验接收；整体PARTIAL | J1缺URL、更正/人工/PIT/异机获取、B/C/规则仍未完成 | G4/G5/T07 |
| K4真实观察 | 待协议冻结；合格样本未验收 | v8起按新协议积累；旧v7/v6保留诊断不追认 | G6 |
| E1b/E5b | NOT_RUN | 前置包未齐且未获付费授权；不以资源上限代授权 | G6 |
| E2b–E4b | BLOCKED_DATA | 严格历史信息集/样本覆盖不足 | G6 |
| E6b及其他效果 | NOT_RUN/待证据 | 账户与组合资格、场景和指标冻结后回放 | G5/G6 |
| 性能/跨端/必要重构 | PARTIAL | 根里程碑T01–T07持续跟踪；B2请求内同版本已随M0落地；先测量再细化 | G7 |

## 当前执行窗口

[第六轮M0–M2](iteration6/DELIVERY_PLAN.md)：**M0/M1 已实施并全量验证（待独立复验）；M2 仅隔离原型（16场景，生产接线未放行）**。设计验证见[19场景与B1/B2基线](iteration6/DESIGN_VALIDATION.md)；M 批全量隔离证据见 [FULL_TEST_RESULTS](iteration6/FULL_TEST_RESULTS.txt)。

版本统一 **v0.8.25**（start.py/banner/--version/README/AGENTS.md 同步；src/__version__=0.1.0 经核定无消费者不动）。CACHE_VERSION 未动（笨总评分域零改动）。M0/M1独立验收通过后再裁定K1冻结与M2生产接线；后续完整MID样板、LONG质量/估值、组合、效果与工程收敛已有G0–G8承接，未到前置时不铺开细节。

## 发布与数据边界

- 不自动修真实账户或覆写历史记录。M0/M1修复未改变确认协议/账本/旧核定记录；R12时点的W1/W2未修复声明随独立复验更新。测试全程隔离临时根，7个保护持仓/知识库文件哈希与文件集合经全量前后核对不变（见FULL_TEST_RESULTS）。
- 事实核验、计算能力、命题成立、计划接受与有效观察分别判定；测试数量不能替代用户工作流和效果证据。
- 新旧shadow协议分别计数：v8为当期协议，v7/v6及更早进older_versions只诊断不追认。
- 原件数值核定不等于严格PIT资格；映射修订追加版本，限定证券/字段/期间/来源范围，不全域升格。
- 非实盘下单；主决策不晋级；未授权不调用付费AI。

## 历史交付索引

[早期M/C工程批](../README.md) → [F账本](EXECUTION_RECORD.md) → [R账本](iteration2/EXECUTION_RECORD.md) → [J账本](iteration3/EXECUTION_RECORD.md) → [K账本](iteration4/EXECUTION_RECORD.md) → [L账本](iteration5/EXECUTION_RECORD.md) → [M账本](iteration6/EXECUTION_RECORD.md)。历史PASS只对当时范围生效；最新受检源码指纹待架构师复验后更新 [REVIEW_INDEX](iteration6/REVIEW_INDEX.json)。

## 持续更新规则

实施方每子交付更新代码commit/命令/产物/未完成项；架构师审批后同时更新根MILESTONES对应G/T、本页当前动作、RESUME和复查指纹。实现、审批、真实效果分列，不能只改本页顶部而留下总表/恢复记忆矛盾。角色按用户当前指派，不由共享文档自动分配。
