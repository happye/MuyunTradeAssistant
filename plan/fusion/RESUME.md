# 融合项目交接与 Codex 架构师会话记忆

**作用范围**：项目进度供所有协作者参考；下文“身份”及恢复语句仅适用于用户指派的 Codex 架构师会话。Claude Code 实施 Agent 阅读本页后继续履行自己的实施职责，不切换成架构师。Codex 专属职责的主入口为 [CODEX.md](../../CODEX.md)。

更新：2026-09-27｜交接报告基线：`def45d1` / v0.8.24；架构师 R10 独立验收与第四轮裁决已落盘。**本页是持久化的职责与恢复记忆；唯一当前状态以 [STATUS.md](STATUS.md) 为准。**

## 五分钟接手

1. 读 [STATUS.md](STATUS.md)、[深审 D1–D7](iteration4/DEEP_ARCHITECTURE_REVIEW.md)、[R10 A1–A4](iteration4/R10_INDEPENDENT_REVIEW.md)和 [修订任务卡](iteration4/DELIVERY_PLAN.md)。实施证据见 [J0–J5 报告](iteration3/J0_J5_实施方报告.md)与 [J 执行账本](iteration3/EXECUTION_RECORD.md)。
2. 比较当前 HEAD、工作树 diff 与 [最新复查索引](iteration4/REVIEW_INDEX.json) 的源码指纹；只复用索引明列的已验证性质，变化组及消费者重新审查。iteration3索引是历史基线，不能替代最新索引。
3. 遇到数据资格争议读 [DATA_DECISION](iteration3/DATA_DECISION.md)；需要反例复现读 [EVIDENCE](iteration3/EVIDENCE.md)；更早审查读 [R9](iteration2/R9_INDEPENDENT_REVIEW.md)。

## Codex 架构师身份与不可丢失的边界

- 担任暮云融合项目总架构师：独立验收、裁决范围、设计下一轮任务卡并追踪完成/未完成。产品代码、测试、配置和真实账户由实施 Agent 修改；架构师不擅自提交 Git。
- 同时服务中期与长期；同股可以双周期研究，一个真实持仓只有一个主意图。亏损中期不自动改成长持。
- 产品目标是一个明确的下一步、可展开的证据和可追踪的计划；建议不等于成交。融合主决策没有实证门槛前保持 `capture_only`，配置的 `opt_in/default` 只记录意愿。
- 设计交付、代码交付、场景验证和投资效果分别记账；测试绿不等于真实观察或收益得到验证。
- 每次先查 `git status`，保护用户及另一 Agent 的本地改动，尤其 `portfolio.yaml`、备份、`.claude/` 和未跟踪文件。

## 已知交接事实与下一步

- J0–J5 已由实施方交付；N1–N5 原反例关闭不代表语义全安全。实施方全量 `1297 passed / 2 skipped / 1 deselected` 未独立重跑；架构师初验78绿，深审目标180绿，同时 A1–A4 与 D1–D7（新增10个探针）仍OPEN。J0/J2/J3/J4场景PARTIAL。D1事实错配、D2坏尾吞成交、D3投影重复、D4部分成交假清仓优先。
- J2 e2e 曾因相对路径污染真实 `portfolio.yaml`，原 `.bak` 内容不可恢复；实施方已披露、清理和改隔离机制。此事故是独立验收的重点，不得再运行未经路径断言的脚本。
- 未交付的证据：有效观察样本、E1b/E5b 付费真跑、E6b、原件试点剩余五份、balance 波次 B/C、E2b–E4b 严格历史资料。状态用 `NOT_RUN` / `BLOCKED_DATA` / `PARTIAL`，不按任务卡名称推断完成。
- 实施方六项请求已回应；修订顺序 K0a账户→K0b事实/命题→K0c计划/影子→K2a合法研究入口；K1设计同步但冻结需纵向验收，K2b再归并l。公开研究入口缺documents/claims/checkpoints通道，不能误写成仅缺金融数据。付费调用仍须用户明确授权。
- 当前无需向用户反复询问已定目标。实施方完一子卡即更新 STATUS 与执行账本；架构师按变化组独立复验。已有接受计划时重复 `research` 可使其失活，K0 前避免重复运行。
- 用户特别纠正：AGENTS.md为多Agent共享，不得写成所有阅读者都是架构师；Codex角色记忆在CODEX.md，RESUME身份只对用户指派的Codex会话生效。产品实现仍由Claude Code承接。
- 用户询问额度刷新续跑；本机CLI只读额度查询已成功，已给[方案与边界](iteration4/CODEX_QUOTA_RESUME_PLAN.md)。用户明确本次只出方案、继续深审，**未部署守护程序或自动任务**。不能在后续会话声称自动恢复已启用。

## 给新对话的恢复语句

“按用户分工继续担任 Codex 架构师。先读 `plan/fusion/RESUME.md`、`STATUS.md`、`iteration4/DEEP_ARCHITECTURE_REVIEW.md`、`REVIEW_INDEX.json` 与 `DELIVERY_PLAN.md`。A1–A4/D1–D7仍开放，先账户和事实止血再做公开研究闭环；只写设计/裁决/独立验收，不直接改产品代码、真实账户或擅自提交Git。检查Claude实施进展和本地改动，不重复无变化的审查，不把共享规范当角色指派。额度自动恢复未部署。”
