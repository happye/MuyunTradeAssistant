# 融合项目当前状态与里程碑

**唯一当前状态入口**｜更新2026-09-27｜实施交付基线`def45d1`/v0.8.24，J0–J5 已实施。**[R10 A1–A4](iteration4/R10_INDEPENDENT_REVIEW.md) 与 [深审 D1–D7](iteration4/DEEP_ARCHITECTURE_REVIEW.md)仍 OPEN；J0/J2/J3/J4 场景资格 PARTIAL。** 独立目标测试180绿，新增10个反例同时成立。下一步 = [第四轮任务卡](iteration4/DELIVERY_PLAN.md)：K0a账户→K0b事实/命题→K0c计划/观察→K2a公开研究闭环；K1设计同步、纵向验收后冻结，再K2b归并 `l`。六项裁决见[第四轮裁决](iteration4/README.md)。

新对话先读本页、[RESUME](RESUME.md)、[深审](iteration4/DEEP_ARCHITECTURE_REVIEW.md) 与 [K 任务卡](iteration4/DELIVERY_PLAN.md)，按 [当前复查索引](iteration4/REVIEW_INDEX.json) 复核变化。不要先从第一轮长账本开始重读。实施和设计是不同交付，本页勾选只对写明范围有效。

## 用户目标与边界

同时服务中期和长期投资；笨韭提供产业/企业假设，暮云负责扫描/择时/纪律，AI负责有来源的事实、反证与解释；最终统一为可跟踪计划与单一行动。当前用户分工：Codex 承担架构设计/裁决/验收，Claude Code 实施产品代码；本页不改变任何 Agent 的会话指派。非实盘下单系统；不以测试通过承诺投资收益。

## 里程碑总览

| 阶段 | 设计 | 实现交付 | 主流程/证据状态 | 当前入口 |
|---|---|---|---|---|
| 早期M/C工程迭代 | 历史已交付 | 用户告知完成，历史账本保留 | 本轮不重验 | [plan总入口](../README.md) |
| F0–F9融合基础 | 已交付 | 实施方VERIFIED | 架构有条件接受，不能代表效果全验证 | [F账本](EXECUTION_RECORD.md) |
| R0–R9可信研究 | 已交付 | 第二轮实现批交付 | 多项PARTIAL；原P探针修复，另有N反例 | [R独立审查](iteration2/R9_INDEPENDENT_REVIEW.md)、[R账本](iteration2/EXECUTION_RECORD.md) |
| J0–J5日常闭环 | 第三轮已设计 | J0–J5 实施交付（v0.8.24，实施方全量 1297 绿） | 独立目标180绿；A1–A4及D1–D7仍 OPEN，J0/J2/J3/J4 场景 PARTIAL，协议未冻结 | [深审](iteration4/DEEP_ARCHITECTURE_REVIEW.md)、[J账本](iteration3/EXECUTION_RECORD.md) |
| K0–K4第四轮 | **已裁决/已给任务卡** | TODO | 先修已复现反例，再从新协议积累观察；付费/严格历史实验仍受门控 | [第四轮裁决](iteration4/README.md)、[K任务卡](iteration4/DELIVERY_PLAN.md) |

## 已完成的可复用结论

- [x] 角色与目标：中长期并存、一个实际持仓主意图、建议不等于成交；不再每轮询问。
- [x] P1原空白facts反例关闭；P2在cv2矛盾摘录反例关闭；P3无池虚标关闭；P4 latest-only旧日期strict拒收。本轮纯函数实测。
- [x] P5分层裁决：允许连续indicative，未知现金离散层CONDITIONAL、quantity=0；**未证明生产接线已完成**。
- [x] 109项目标离线测试通过；交付方全量1222口径本轮未重跑。
- [x] ROE/PE单独能力、不冒充ROIC/区间的设计选择认可。
- [x] 第二轮交付/偏差复核；第三轮接线优先+前瞻留存+原始档案试点方向确定。

上述第二轮历史结论只在其[历史索引](iteration3/REVIEW_INDEX.json)的范围内有效，不能代表现在的代码全部安全。最新检查与源码哈希见[当前索引](iteration4/REVIEW_INDEX.json)；新反例重新打开语义性质，不篡改旧测试成绩。

## 尚未完成：每项有承接卡

| 块 | 状态 | 明确剩余项 | 承接 |
|---|---|---|---|
| 事实→命题 | **J0 已交付；深审 PARTIAL** | N1原例仍绿，但D1四例重新打开事实语义资格；D5因子OK被当命题TRUE；D6快照缺成员证券边界 | K0b |
| 评估唯一真值 | **J0 已交付；R10 PARTIAL** | A2：计划快照有值而评估快照为空仍可消费 VALID；旧计划/方法降级门保留 | K0 评估快照资格 |
| 单位/字段核定 | **J1 已交付** | N4重复映射/源恢复已关闭（幂等+范围门）；万科2024H1负债率原件核定0.72937（orig_6366a5eff46fb65a）；**试点其余5份 NOT_RUN**、波次B/C（currentRatio/quickRatio/assetToEquity）未启动 | J1 后续批（DATA_DECISION 排期） |
| 原件/历史时点 | **PARTIAL / BLOCKED_DATA** | 原件核对机制已建+1/6 点位核定；严格历史全集不足 | J1 后续批/观察期 |
| 账户记账 | **J2 已交付；深审 PARTIAL** | N5原例关闭；D2坏尾行吞新成交且仍ACCEPTED；D3投影内部崩溃重试重复生效；D7现金冻结口径矛盾 | K0a |
| today/confirm | **J2 已接线；深审 PARTIAL** | D4实际卖100/剩900却投影清仓；主计划/成交日期跨层接线仍缺；逐股可买数量待价格源 | K0a/K0c；数量呈现接K3 |
| 自动研究日常使用 | **J3 已接线；深审 PARTIAL** | A1重跑撤销接受；公开应用入口未接documents/claims/checkpoints，财务补全仍不能建立MID正例；`l`与其他端未归并 | K0c；K2a研究闭环；K2b归并 |
| 模式和有效影子 | **J4 已实现；R10 PARTIAL** | A3 同分钟不同版本被吞、A4 决策变更同指纹被吞；观察协议未冻结、未提交合格样本证据；自动化未创建 | K0 去重；K1 协议冻结；K4 观察 |
| 实验效果 | **J5 已收口** | E1b/E5b manifest 已立（60请求/200k 上限）付费执行 NOT_RUN；E6b NOT_RUN；E2b–E4b 维持 BLOCKED_DATA | 付费授权后按 manifest |
| 全市场规则/用户走查 | **PARTIAL** | 适用规则核验、跨端/真实用户场景和完整性能待真实使用 | 观察期 |
| 兼容清理 | **J5 已收口** | factor_compute 弃用委托删除（e1/e7 已迁移真实能力ID）；ShadowDiffRecord v2 聚合字段删除（报告保留旧记录读适配） | 无失踪项 |

影子0/0/0来自R9交付审查口径；R10 没有读取用户HOME里的实时账本，不能称此刻实际数量依然精确为零。状态是**尚无按冻结协议提交到本次验收的合格观察证据**；旧 `shadow_v5` 只作诊断，不能事后追认。

## 发布边界

- legacy 主研究仍维持原入口；数量确认/自动投影不予完整验收，D2–D4修复前部分成交及异常恢复需核对，不能沿用“账户确认整体安全”的结论。未据合成反例断言用户真实账本已损坏；本轮不改真实账本。
- 已有已接受 plan2 计划时重复 research 可使其失活（A1），K0c前避免重复运行并核对激活状态。类型化 FACT_CHECKED 也不能整体背书投资事实（D1）；原N1–N5测试关闭仅指原反例。
- 融合主决策不晋级，effective继续capture_only；晋级所需合格观察证据尚未提交，本次未读取用户实时影子数量。
- 融合主决策opt_in/default：不晋级。配置意愿不等于生效，已实施枚举也不等于达到发布门；R10 新反例使观察冻结前置更明确。
- 数据采集可同步推进；合法影子观察在所需链路贯通后计有效样本，不要求单个用户制造30份计划。

## 更新本页的规则

1. 执行Agent完一个**子交付**就更新对应行：commit、实际命令、产物、结果、仍缺什么。代码写完和策略有效分列。
2. 没有真实输出不勾完成；外源未跑写NOT_RUN；资料不足写BLOCKED_DATA；卡内有剩余条款仍PARTIAL。
3. 新任务必须说明承接本页哪行，旧行补指针；不留“随后续批”无卡项。
4. 历史长报告留原样，本页替换过期现状，不每轮再复制一份历史叙事。
5. 恢复时比HEAD、工作树diff、相关源码hash；只重看变动组与消费者，外部制度/源变化另核。详见[复查索引](iteration4/REVIEW_INDEX.json)。
