# 融合项目当前实施与审批状态

**更新：2026-10-02（N 批交付）｜受审HEAD：73ba005（N0–N2 交付待独立复验）｜v0.8.26｜整体未完成。** 长期目标及完成标准见根[MILESTONES](../../MILESTONES.md)，恢复见[RESUME](RESUME.md)。

## 当前审批

[M系列交付](iteration6/实施方报告_M系列交付.md)已完成[R13独立审批](iteration7/R13_ACCEPTANCE.md)：**分项接收、M0/M1整卡退回补齐；K1不冻结，M2/K2b生产不放行，effective保持capture_only。** R12既有通过范围不重复审批。

**N 批已实施待独立复验**（实施方报告[实施方报告_N系列交付](iteration7/实施方报告_N系列交付.md)，逐卡账本[EXECUTION_RECORD](iteration7/EXECUTION_RECORD.md)）：N0 修X1（ctx 贯穿策略/chat/建议/摘要，无投影不按空仓，读取失败/PARTIAL 待对账，scanner/TUI/Web 同类点接线）、N1 修X2（两臂各投影规范包+有效版本资格门，行5/行6 升 v3，shadow_v9）、N2 修X3（严格解析/先比时刻再取日/finite/NAV 偏移保留，kline_only 标注）、M2 原型按 X4 补证据（25 场景）。联合目标回归 **30文件462 passed**、隔离离线全量 **1482 passed, 2 skipped**（[FULL_TEST_RESULTS](iteration7/FULL_TEST_RESULTS.txt)，保护哈希不变）。**实现≠审批。**

| 块 | 当前状态 | 剩余/承接 | 长期里程碑 |
|---|---|---|---|
| K0a/L1/M0账户 | X1 修复已实施（ctx 贯穿策略/无投影/读取失败/建议/摘要/scanner-TUI-Web） | N0 待独立复验；复验后联合裁定 K1 冻结 | G1/G2/G5/T01 |
| K0b事实/命题 | 既有明确范围通过不变 | LONG规则/数据不完整，不整体背书自然语言 | G1/G3/G4 |
| K0c/L0计划/去重 | 规范两臂与版本资格已实施（N1） | N1 待独立复验；完整 MID 评估正例仍待 G3 | G2/G6/T03 |
| K2a/L2公开研究 | 公开引用与checkpoint TRUE通过 | 完整MID评估正例仍待G3，不以checkpoint替代VALID | G2/G3/T02 |
| K1/M1观察合同 | X2/X3 修复已实施（shadow_v9 候选+表 v3；严格时间解析） | N1/N2 待独立复验；冻结仍须独立审批 | G1/G6/T03 |
| K2b/M2统一l | 原型按 X4 补证据（损坏库区分/真前后哈希/旧方法/重启读取，25 场景） | 生产未放行；待 N0–N2 联合过门+合同冻结 | G2/G7/T02 |
| K3/L3原件与规则 | 可跟踪清单与6/6离线复验历史范围接收；PARTIAL | J1缺URL、更正/人工/PIT/异机获取、B/C/规则未完成 | G4/G5/T07 |
| K4真实观察 | 未验收合格真实样本 | shadow_v9 经冻结后再积累，旧 v8 及更早只诊断不追认 | G6 |
| E1b/E5b | NOT_RUN | 前置不齐且未获付费授权 | G6 |
| E2b–E4b | BLOCKED_DATA | 严格历史信息集/样本覆盖不足 | G6 |
| E6b及其他效果 | NOT_RUN/待证据 | 账户组合资格、场景与指标冻结后回放 | G5/G6 |
| 性能/跨端/必要重构 | PARTIAL | 批量入口同请求快照已接线（la/l all/多代码）；T01–T07持续维护 | G7 |

## 当前执行窗口

[第七轮N0–N2任务卡](iteration7/DELIVERY_PLAN.md)：**N0/N1/N2 已实施并联合回归（待独立复验）；M2 仅原型补证据，生产前置不变。**

决策表 **v3**（受影响行=行5/行6，注释登记）、观察协议 **shadow_v9 候选**（旧 v8 及更早只诊断不追认）、产品版本 **v0.8.26**（五处同步）。版本追认不等于合同冻结；冻结仍须独立复验。CACHE_VERSION评分域不变不动。

已收口：摘要 pos 判据披露（N0 ctx 判据）、预取 fetched_at 复用时刻（N2 携带原值）、NAV 偏移丢失（N2 保留到资格门）。完整MID、LONG、组合、效果与工程收敛继续由G0–G8承接。

## 数据与协作边界

- 不自动修真实账户/投影，不覆盖历史观察或核定登记。真实库五条旧登记保留；未来变更先明确范围、dry-run和追加修订。
- N 批改产品源码/测试（逐卡 guard 审查）；架构师 R13 文档同步与实施方 N 批文档分层汇合于工作树，随交付落库。已有持仓/知识库/本地资料工作树变更保留。
- 测试成功、事实核实、命题成立、计划接受、有效观察、投资效果分列；不以测试数量或版本号代验收。
- 零付费AI；非实盘下单，主策略不晋级。

## 历史与恢复

[F账本](EXECUTION_RECORD.md) → [R账本](iteration2/EXECUTION_RECORD.md) → [J账本](iteration3/EXECUTION_RECORD.md) → [K账本](iteration4/EXECUTION_RECORD.md) → [L账本](iteration5/EXECUTION_RECORD.md) → [M账本](iteration6/EXECUTION_RECORD.md) → [N账本](iteration7/EXECUTION_RECORD.md) → [R13审批](iteration7/R13_ACCEPTANCE.md)。历史结果保留，各自仅对受审范围生效。

最新增量复查以[iteration7/REVIEW_INDEX](iteration7/REVIEW_INDEX.json)为基准（N 批复验时架构师更新）；交付/审批同步MILESTONES、STATUS、RESUME。无新交付不重复已知红例，不擅自代实施Agent改产品。
