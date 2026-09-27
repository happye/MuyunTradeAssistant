# 第四轮 K0–K4 实施任务卡

2026-09-27｜架构师任务卡，**尚未实施**。本次深审修订：验收反例为 [R10 A1–A4](R10_INDEPENDENT_REVIEW.md) + [深审 D1–D7](DEEP_ARCHITECTURE_REVIEW.md)。唯一当前状态回填 [STATUS](../STATUS.md)。实现 Agent 不是独占代码库：先查工作树，保护 `portfolio.yaml` 与其他 Agent 修改；账户、研究、影子的 CLI 集成串行合入。

| 卡 | 前置 | 主责边界 | 用户可见结果 | 完成判据 |
|---|---|---|---|---|
| K0a 账户止血 | 立即，首批 | account_service/account_snapshot/portfolio/proposals + confirm/today | 部分卖出保留剩余股数；坏账本拒写；恢复不重复记账 | D2/D3/D4/D7 与逐写点故障注入 |
| K0b 事实与命题 | K0a 可独立推进，CLI 合入串行 | claim_extraction/research/research_snapshot/factor_compute | 错金额/否定不核实；可计算与命题成立分开显示 | D1 四例/D5/D6，规则版本化，正向解析不误封 |
| K0c 计划与观察止血 | K0a/K0b 接口稳定 | research_application/horizon_plans/shadow_diff | 重放不撤接受；缺快照待复核；动作变化不丢 | A1–A4 + 接受引用精确版本 |
| K1 观察协议冻结 | 设计可同步；冻结须 K0 + K2a 纵向验收 | shadow_diff/decision_policy/analysis_service + schema/报告 | `shadow` 分周期显示有效/诊断数及阻塞原因 | v6 合同、真实入口、旧记录只诊断 |
| K2a 最小研究闭环 | K0b/K0c，K1 候选接口 | ResearchApplicationService + 原文/主张/检查点持久化 + research/plan2 | 用户能补具体缺口并接受可追溯计划 | 不直调内部服务造正例，公开入口完整 MID 正反链 |
| K2b `l` 归并 | K2a + K1 冻结 | CLI `l`/`la`/`l all` 与同一应用服务 | 深分析卡给研究下一步与证据/计划；legacy 动作不变 | 同服务、同回执、缓存/预算、隔离入口链 |
| K3 数据与规则证据 | K0 可并行 | J1 原件试点 + 官方交易规则核定 | 哪些数据可用、哪些仍缺证据可见 | 剩余 5 份逐点账本，B/C 范围门，规则证据 |
| K4 观察与实验准备 | K1–K3 证据 | 观察审计/manifest/实验基线 | 0 样本仍显“尚无证据”；有样本可追溯 | 真实样本审计；E1b/E5b 前置包；不自启付费 |

## K0a｜账户事实与可恢复投影（第一优先）

1. **完整性先于写入**：日志解析失败返回结构化损坏状态，进入 snapshot.isolated_events / PARTIAL 并冻结精确新增；锁内拒绝向截断 JSON 尾部继续追加。保留原件与偏移供恢复；合法完整 JSON 缺换行另行处理。回执必须区分未写、已持久化、写入状态待核对，fsync 失败不能当普通成功。未授权时不自动修用户旧账本。
2. **股数是事实，建议是意图**：数量路径不再用 target_ratio-current_ratio 当实际成交变化。has_position、quantity、lot cost 从账户快照派生；价格/NAV缺失时权重 UNKNOWN。不从比例反造数量；比例旧模式标 RATIO_ONLY。主流 CLI/chat 持仓上下文与 today 消费同一账户状态；产品应明确显示“已成交100 / 剩余900”，不显示清仓。目标数量未知则显示本笔已记录，不能默认为建议全额完成。
3. **投影值和消费水位同一原子保存**：每个投影视图记录 ledger version / last applied event，与视图值一起提交；重放计算目标状态后替换，不能重复增量叠加。proposal 完成状态是可重建派生结果。保留人工元数据，迁移数量账户时显式记录来源；已有冲突不能静默挑一边覆盖。并发用同一事实提交锁/版本，处理旧投影实例覆盖新水位。
4. **贯穿字段**：实际 trade_date、fill_id、数量、价格、费用、proposal_ref、精确主计划引用与偏离说明入事件；派生视图使用真实成交日和批次成本。不满足建议/计划时记录真实成交与偏离，而不是把用户真实事实改写为建议值。
5. **现金口径**：cash_available 按已声明合同为扣冻结后的净可用额，reserved 不再二次减。迁移/显示显式版本；缺 NAV/price/cash 不给伪精确股数。
6. **验收**：D2/D3/D4/D7 先红后绿；追加前、追加后、fsync、投影写后/水位前（应成为不可出现状态）、proposal写失败、重启、同 fill 重试、两笔同价真实成交均覆盖。一次 quantity 部分卖出必须贯穿真实 REPL 解析→CLI→账本→重启→today/legacy持仓读取。所有路径构造前断言临时根；旧数据迁移用 fixture。用户可见输出与错误字典同步。

## K0b｜事实核验、命题规则与成员资格

1. **类型核验用绑定元组**：主体/谓词/带符号完整数字/单位/期间/阶段/否定对象必须指向同一原文局部。支持句式闭集，无可靠解析则降到摘录级；不把添加关键词当语义修复。D1 四例之外，覆盖两个主体、多期对比、预计/已实现、单位换算、千分位与数字相邻标点的正反例。核验协议与规则版本更新，旧 FACT_CHECKED 重验或降级，不篡改历史原件。
2. **能力状态与命题评价分开**：FactorResult.OK 只表示计算成功。每个可自动建立的必需命题登记明确 rule_id/version、输入证据/期间/口径、适用范围、支持/反证/未知条件；无规则默认 UNKNOWN。多期持续性不得被单期 CFO/ROE 自动建立；资本约束不得因任一分量存在就为真。阈值有效性未验证时保留研究性判断和范围，不冒充已实证策略。
3. **输入谱系**：派生因子返回成员 evidence_ids、对应报告期、累计/单季口径和修订，不只绑定顶层 snapshot_id。证券财务记录进入快照时严格核主体；宏观/行业范围显式建模，不能以空ID放行。补跨主体/跨期/重述数据拼接反例；未知与反证分别保留，不能统一用OK吞并。
4. **验收**：D1/D5/D6 + 正向合法资料；同一记录进入 claim、assertion、assessment、shadow 后语义不升级。LONG 缺规则时维持 UNESTABLISHED并指出原因；不以减少 required 命题换通过。

## K0c｜计划生命周期与观察消费（原 K0 保留并收紧）

1. **研究计划双槽语义**：同 run_id/同内容重跑幂等，已接受版本保持 active、revision 不增。新材料只写候选草稿，不覆盖已接受计划和主意图引用；用户显式 accept 后原子切换。旧格式迁移保留 accepted_refs/active_refs。若旧评估在新证据下失效，标待复核并抑制新增风险，不自动延长持有期。测试同输入、材料更新、重启、接受冲突、影子读取。若短期做不到双槽，先在 `research` 对已接受计划作可见保护并停止覆盖，不能假装 J3 全闭环。
2. **评估快照资格**：计划与评估 snapshot 均须非空、相等；方法/证券/周期/时效继续核对。缺值、错值、旧格式三路分别降级，统一用于 thesis 状态与观察有效性。检测 AssessmentStore 文件内容与内容寻址 ID 不一致时按坏文件隔离，避免唯一真值被损坏记录改写。
3. **影子去重**：取消单纯“同股同分钟即重复”；补齐输入/输出指纹的业务字段，锁定真正重复与同分钟计划变更/跨分钟行情决策变更。append 结果向调用方/诊断回执可见；写失败不能被当成成功观察。检查 `l`、`la`、chat 三调用点及旧记录读取兼容。
4. **回归纪律**：先写独立反例，验证红灯，再修转绿；扫相同空字段通配、重跑覆盖接受状态、分钟级去重的同类点。全量 pytest 与隔离端到端在最终集成后各跑一次；按项目工程纪律落 `.learnings/`。不改证券账户实盘文件。
5. **精确接受引用**：不能仅依 accepted_at 非空判已接受；消费时核 accepted_ref 的 plan_id/revision/content_hash。主意图使用 account×security active_ref，不按 MID/LONG 查找顺序选。数量确认入口连接计划库并保留引用；不存在/过期引用允许记录真实成交为偏离或待核对，不能虚构已按计划成交。

## K1｜冻结可比较的影子记录

- `shadow_v6` 记录按 **MID 与 LONG 分别**登记 `plan_id/revision/content_hash/accepted_ref/assessment_id/status/snapshot_id/method_version/account_version/policy_id/decision_rule_version/as_of/evidence_cutoff/quote_cutoff`，以及两臂 action、target_weight、blockers、execution_status/blocked。字段缺失按 diagnostic，机器可读 `drop_reasons`，不以空值充资格。
- 先定义配对单元与去相关观察窗，再统计有效/诊断/拒收；旧 `shadow_v5` 原样保留但不进入 v6 有效分母。切换 schema 需版本化读适配和 CLI `shadow` 可见变更。
- 隔离场景须从 `research` 草稿→用户接受→真实 `l` 持仓分析→影子落盘→报告计数纵向走通；至少一个双周期并存、一个仅单周期接受、一个计划/账户同分钟变更、一个行情变化导致动作翻转、一个缺评估/缺账户反例。测试构造前断言 PortfolioManager、proposals、assessment、plan、account、shadow 所有路径均在临时根；HOME 重定向本身不算隔离。
- 观察协议冻结仅表示记录可比较，不表示策略效果或发布门已过。上线后有新反例用新 schema/规则版本，旧样本不追认。

## K2a｜先打通公开研究入口

- 补 ResearchApplicationService 的归档原文/verified claim/checkpoint 通道；复用已有 ResearchStore 与服务，不新增平行引擎。原文URI/hash、摘录位置、核验版本可追溯到原件；source_uris 不能取自一直为空的参数。
- 具体界面可用 `research` 子命令或同等少量入口：选择归档证据→显示逐命题支撑/反证/缺口→提出到期/反证检查条件→用户显式确认风险意愿→产生候选。接受计划不自动替代风险确认，AI 不能自行确认。
- 正向验收从公开解析器输入开始：一套合法 MID fixture → 评估 → 用户接受精确版本 → 主意图引用 → 隔离分析/影子落盘；反向覆盖不支持摘录、未确认风险、错主体、缺原件、证据更新但未接受候选。LONG 继续如实缺口。不能直接调用 ResearchService 注入完美结果后宣称用户闭环已通。
- 本卡本地资料导入/确定性校验可零模型调用；付费提取不隐式开启。真实金融资料验证与合成正例分列，不把 fixture 计为有效观察。

## K2b｜`l` 归并边界

- `l` 深分析仍由现有 orchestrator/Strategy/Execution 产生唯一主结论与 `position_action`；调用 ResearchApplicationService 作为研究旁路，复用 `research` 的归档、评估、草稿与运行回执。不得把研究状态直接替换 legacy 买卖、修改 portfolio 持仓事实或自动接受计划。研究失败输出具体缺口，legacy 分析继续。
- 同输入同日缓存以证券、资料版本、as_of/报告期、方法版本与账户版本为边界；批量 `la`/`l all` 共享 IO/AI 预算，达到上限保存停止并显示本轮跳过数。服务补显式 IO/AI 预算参数；`--capture` 是否联网由既有入口语义控制，普通 `l` 不因归并自动新增付费 AI。
- 顶部人话摘要增加“研究下一步/证据状态”单行与展开链接；已有摘要/决策/持仓确认语义不变。至少一条真实 CLI 解析→应用服务→展示的隔离测试，另测 research 与 l 同快照同评估 ID、重复 l 不重建草稿。chat/Web/TUI 未适配继续标未支持，后续单独验收，不把常量声明当路由测试。

## K3｜原件、字段与规则

- 按 [第三轮 DATA_DECISION](../iteration3/DATA_DECISION.md) 第一批补其余五份发行人报告。每点保存官方原件 URL/hash、披露/更正时间、合并范围、页/表/行、数值单位、公式和复核记录；下载失败也记 NOT_RUN/UNAVAILABLE，不用供应商交叉一致替代原件。最多 6 份首批、总试点≤36份或 2 工作日先到即停。
- 波次 B 的 currentRatio/quickRatio/assetToEquity 只在各自定义核定后进入相应能力；波次 C 的 cashRatio/YOYLiability 在真实消费前再做。既有 liabilityToAsset 仅万科 2024H1 单点已核定，不能推广。
- 逐股可买数量与全市场日期化交易规则保持 PARTIAL；先用官方生效日/证券板块/数量/费用证据锁住支持范围，再接 today。缺规则/价/现金只显示唯一阻塞，不显示伪精确“可买 0 股”。

## K4｜观察与实验停止点

- 以 K1 冻结后的协议从零累计真实有效观察；按证券×周期×计划版本报告分母、WAIT/REVIEW 占比、安全门覆盖、同窗 legacy 差异。真实使用时间不能用合成测试替代。E6b 仅在固定意图、正确压力场景和账户资格齐后回放。
- E1b/E5b 先交冻结候选/名额/成本、独立留出集、请求/token 预计与停止点；原 manifest `60 请求/200k tokens` 仍只是硬上限。**任何付费请求必须在用户明确授权后执行**；未授权保持 NOT_RUN。E2b–E4b 历史全集仍 BLOCKED_DATA。
- `opt_in/default` 的 effective 继续 `capture_only`；K0–K4 代码完成、观察协议冻结、足量样本、效果检验和独立复验分别记账，不能互相代替。
