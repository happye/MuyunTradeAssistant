# 统一决策系统技术设计

版本：Fusion Design 1，2026-09-25。**待实施提案**。兼容基线v0.8.17。字段、模块及命令为建议契约，不表示仓库已有这些实现。

## ADR-F01：保留单体，收敛终态，避免再加一个投票层

保留现有数据适配器、SkillEngine、EntryExit、bz维度、扫描器、RAG与回测基础。第一阶段增加结果适配器，之后将Strategy/PlanGuard重复的动作裁决逐步收敛为有序纯函数；不是在现有七层后面再叠一套加权评分。

建议最小模块：

```text
src/core/decision_contract.py     # 公共不可变结果类型；不访问IO
src/core/decision_policy.py       # 周期策略、有序裁决与理由；纯函数
src/core/research.py              # 证据到投资逻辑状态；不负责抓数据
src/core/portfolio_policy.py      # 同一组合快照的批量预算分配；纯函数
src/data/research_snapshot.py     # 来源、时点、单位、快照与缓存适配
src/core/analysis_service.py      # 协调既有装配/抓取/研究/决策/保存
src/cli/action_view.py            # 统一行动卡投影；chat/Web/TUI共用语义
```

先放少数文件，出现真正独立生命周期后再拆包。复用`core/runtime.py`装配与`src/config.py`，UI只调用服务。`analysis_service`不反向import CLI。保留旧`Orchestrator.analyze`返回协议，新增`analyze_packet`适配出口；迁移完消费者再考虑删除旧协议。

## ADR-F02：所有入口消费同一个终态

### 2.1 三种不同对象

1. `SignalAssessment`：检测到什么信号，其方向、强度、来源、适用周期；允许相互矛盾。
2. `DecisionPacket`：基于指定计划和账户快照，系统建议采取什么行动、为何、受什么约束。
3. `HoldingSnapshot / ConfirmedFill`：用户已确认的持仓或成交事实。分析生成建议不创建成交。

### 2.2 最小DecisionPacket字段

| 字段 | 类型/约束 | 语义 |
|---|---|---|
| schema_version / decision_id | 版本、唯一ID | 决策记录不可变，重算是新记录；相同请求可幂等复用 |
| security_id / exchange | 规范证券ID | 股票代码不依赖任意字符串切割，名称仅展示 |
| as_of / market_phase | 带时区时间；盘前/盘中/收盘 | 决策截止时点，非“生成日期”等价 |
| snapshot_id / portfolio_revision | 内容hash、账户版本 | 复现依据；批量结果来自同一账户快照 |
| policy_id / parameter_version | 如legacy_v1 / fusion_mid_v2 | 说明采用哪套策略与参数；不混用分数 |
| plan_id / plan_revision / horizon | ID、版本、LEGACY/MID/LONG | 绑定已确认的持有意图 |
| research_status | COMPLETE / INCOMPLETE / CONFLICTED / NOT_APPLICABLE | 信息资格，不能用0或50代替缺失 |
| thesis_status | UNESTABLISHED / VALID / REVIEW_REQUIRED / INVALID | 投资逻辑状态；长期逻辑不随日涨跌自动翻转 |
| desired_action | OPEN / ADD / HOLD / REDUCE / EXIT / WAIT / REVIEW | 研究与策略想做什么 |
| target_weight / delta_weight | Optional[0..1] / Optional[-1..1] | 都以账户净资产为分母；delta=目标-已确认当前，未知则null |
| execution_status | ELIGIBLE / BLOCKED / CONDITIONAL / UNKNOWN / NOT_NEEDED | 时点可行性判断，不是已成交状态 |
| executable_action | Optional动作 | 当前许可动作；BLOCKED或UNKNOWN不能伪装成“已持有决策” |
| blockers / reason_codes | 有序结构化列表 | 资金、可卖份额、数据、期限、价格条件；展示不再猜sell_path |
| evidence_ids / dissent_ids | 引用列表 | 支持与反证，共享事件不可重复计票 |
| next_check / invalidation_rules | 结构化触发条件 | 何时重新评估、何种事实改变逻辑 |
| legacy_trace | Optional旧四元组投影 | 专家展开与回归，不参与第二次裁决 |

新契约不再叫模糊的`score`：保留`legacy_action_strength`、`bz_normalized_score`等有名称的诊断字段。无校准模型时不提供`win_probability`。`REVIEW`是需要核对的研究状态，若已确认重大退出证据同时存在，则退出意图优先，REVIEW不能掩盖它。

### 2.3 不变量

- EXIT意味着目标0；REDUCE目标必须小于已确认当前；ADD目标必须大于当前；HOLD不建议比例变化。
- 单股研究缺组合信息时，可给有条件的方向，`target_weight=null`；不能默认推荐20%或50%。
- `desired_action=EXIT, execution_status=BLOCKED`保留退出意图、阻塞原因和现有持仓；不改成“逻辑继续看好”。
- 规则原因与数值由后端生成；AI解释不创造新的目标价格、份额、止损或时间。
- 同一decision_id在CLI/chat/Web/TUI/evidence/diff中动作、周期、目标比例、数据截止时点完全一致。
- optional新闻失败不能消除已核实硬退出；必需买入证据缺失不能经“剩余维重新加权”变成强买入。
- 展示买点只是条件候选；当`execution_status!=ELIGIBLE`时不能输出“明日必买/必卖”。收盘评估一般是下一时段条件计划，而非明日保证成交。

## ADR-F03：分析不写成交，先修持仓事实再分配组合

`PortfolioManager.update_from_strategy_decision`当前将建议仓位与生命周期回写到持仓，应拆为：

- `record_analysis_observation`：写最新研究、日线最高价等观察量与已处理交易日；不改变已确认数量、成本、开仓日期。
- `record_proposal`：保存建议、目标、绑定决策与账户版本；状态PROPOSED。
- `confirm_fill`：由用户录入实际成交或明确导入凭据后才改变持仓与成交驱动的冷却期。现有pos手动维护路径兼容，记录修正原因。

状态机：`PROPOSED -> CONFIRMED/PARTIAL/REJECTED/EXPIRED`。部分成交保留剩余待办；重复fill_id拒绝重复入账。被阻止的卖出不能启动“已经清仓”的冷却状态。策略的信号历史与实际持仓生命周期分别记录。

初版维持单进程写入+现有原子替换/版本冲突拒绝；多实例必须通过写锁覆盖读取版本、写入与替换的整个临界区，不能把sha256检查单独当CAS事务。锁失败明确报冲突，不覆盖。无需把整个项目迁移数据库；若跨进程并发成为正式需求，单独评估窄范围SQLite事务存储。

旧记录没有真实成交来源标记时，置`holding_verification=LEGACY_UNVERIFIED`，保持原值，提示一次核对。禁止用新建议倒推出旧真实成交；`today`仍显示已有风险与研究，但缺可靠仓位基数时暂停精确加仓建议。

## ADR-F04：中期、长期与旧模式正交

### 4.1 PlanV2

保留原TradePlan，新增侧挂版本对象，避免强行改旧字段意义：

```text
PlanV2:
  plan_id, revision, created_at, accepted_at
  horizon: MID | LONG
  intent: 投资逻辑简述 + thesis_id
  entry_policy_id, exit_policy_id, risk_profile_id
  required_evidence: [证据类型及允许时效]
  review_triggers: [财报披露、新事件、指定复核日期]
  invalidate_if: [结构化、可计算/需人工确认的条件]
  max_hold_until: 可选，仅确实有催化期限/资金期限才填
  accepted_risk_limits: 从明确设置的用户风险档获取
  legacy_mode: 可选，仅兼容与对照
```

投资期限不是自动变化的市场状态。MID转LONG需要一份新的长期资格评估与用户确认，旧计划关闭/替换留下版本关系；浮亏和希望回本不是有效理由。LONG转MID同样显式记录。

同股两种意图研究可同时存在，但首版只有一个`active_plan_id`影响账户建议；待成熟后再做同股多份额计划，不用两个虚拟持仓重复计算同一实际仓位。

### 4.2 周期策略决策表（从上到下，首个决定性条件优先）

| 条件 | 已持有 | 未持有 | 解释 |
|---|---|---|---|
| 已验证硬退出条件/用户硬风险界限触发 | EXIT或明确REDUCE；再检查可卖约束 | WAIT，禁止新增 | legacy安全网兼容；硬风险有证据ID，单纯LLM“黑天鹅”标签不直接等同法定事实 |
| 必需价格/持仓信息不可靠 | 保留可独立成立的退出告警；其余REVIEW | WAIT/REVIEW | 数据坏不等于零仓位，也不等于继续看好 |
| 投资逻辑INVALID | 依已接受退出策略EXIT | WAIT | 不能被技术反弹抵消 |
| 关键逻辑证据CONFLICTED或过期 | REVIEW，冻结新增风险 | REVIEW | 不因未知就清仓；也不因过去A级永久长拿 |
| MID逻辑VALID且预设技术退出成立 | REDUCE/EXIT | WAIT | 不通过“长拿”口号压制此周期的退出 |
| MID逻辑VALID、入场条件成立且预算可用 | 依加仓计划ADD或HOLD | OPEN | 技术触发只解决时点；未验证景气不能由技术分凑资格 |
| LONG逻辑VALID、未触发硬退出，只有普通短期破均线 | HOLD或因事先预算条件REDUCE | 按长期入场策略评估 | 普通技术警告仍留痕，不擅自转短线策略 |
| LONG逻辑VALID、质量与估值研究合格、进入预先定义买价区间、无急性风险 | 有剩余预算才ADD | OPEN候选 | 不要求恰好当天缩量下跌；急性风险定义须版本化验证 |
| 逻辑有效但价位/预算/时机不合适 | HOLD | WAIT，给下次触发条件 | “好公司”和“现在适合买”分开 |
| 没有足够逻辑支持 | REVIEW | WAIT/REVIEW | 技术候选可以保留研究资格，不直接升级投资建议 |

LONG入场初版只支持有明确财务与估值情景的证券；高增长亏损、金融等特殊行业未有适配器时显示`NOT_APPLICABLE/INCOMPLETE`并保留研究，不填通用数字强行纳入。先支持可验证的一类普通非金融企业，再扩行业。

复核日到达只触发REVIEW；与legacy 180/30自然日到期清仓分开。新策略要说明是否接受现有硬风险与退出配置，不静默放宽安全网。改动旧Weinstein、Chandelier或自主可控纪律均属于独立实验，不夹带在契约迁移中。

## ADR-F05：证据驱动的研究层

### 5.1 EvidenceRecord

```text
evidence_id, source_kind, source_uri, source_document_hash, excerpt_locator
security_id / industry_id, metric_or_claim, value, unit, currency
period_end, occurred_at, published_at, available_at, fetched_at
as_of, source_version, revision_id, quality_status, lineage_ids
```

`period_end`不等于可用时间。历史决策要求`available_at <= decision.as_of`；没有可信公布时间时，不允许进入严格PIT回测。保存首次取得时间和后续修订版本，历史回放选择当时可得版本；现在下载的重述财报不能回填过去。

事实状态：OBSERVED / DERIVED / MODEL_INFERRED / USER_ASSERTED / MISSING / STALE / CONFLICTED / NOT_APPLICABLE。推导字段必须有输入引用、公式与单位；模型推断不能冒充OBSERVED。失效条件带`evaluation=TRUE/FALSE/UNKNOWN`，UNKNOWN不可自动变FALSE。

只为决策需要的证据加这些字段，不建立覆盖全仓的通用元数据平台。缓存引用快照ID，完整原文可用受控本地归档或hash+来源定位；关键公开证据不可仅保存一段模型摘要。

### 5.2 ThesisRecord

每个逻辑最少包括：受益业务、收益机制、预期实现区间、已经发生与尚待发生的事实、关键反证、估值假设、下一可验证节点。状态转移由证据和明确条件驱动，AI只提取候选事实、提出解释，不自己改变已接受的风险界限。

中期例子：需求/订单变化→公司对应业务占比→产能/毛利转化→财报或交付验证。行业商品涨价只有结合公司上下游位置才有方向；上游利好可能是下游成本压力。

长期最小财务包（研究初始规范，样本充分性待验证）：至少12个季度及3个年度可比数据，不足标明覆盖不足；记录营收/利润增速与现金流、ROE/ROIC及其适用性、负债与偿债、资本开支、稀释/分红。金融行业不得套用工业企业净债务/FCF规则。增速分母≤0时不使用普通百分比结论。

估值先做可审计情景：例如正常化盈利×行业可比倍数范围，明确盈利定义、倍数参照、净债务/股数调整适用性；或现金流估值记录折现与终值敏感性。价值区间不是确定目标价。价格位置只作为单独风险维度，不替代基本面估值。

### 5.3 因子登记与数据实施

每个新因子登记`factor_id/version/horizon/definition/inputs/unit/missing_policy/sector_scope/availability_rule/economic_hypothesis/experiment_id`。最初少量因子足够：产业需求变化、业务真实暴露、盈利质量、估值位置、相对趋势、流动性/拥挤与硬风险。

从现有Baostock财务接口、官方公告/年报、已有industry_metrics做可得性探查，先按公告公布日构建；不能只根据接口名字假设字段可靠。分部营收难以稳定自动取得时，允许用户给来源的人工证据，并标明输入者/时间/适用期。

首批因子定义如下。它们保留原始值和解释，不再求一个覆盖所有用途的总分。表中门槛由训练窗实验选择并冻结，未完成验证前仅供研究。

| 因子ID | 计算/提取契约 | 用途与缺失规则 |
|---|---|---|
| business_exposure_v1 | 相关业务营收/总营收；同报告期同口径，另存利润贡献（如可得） | 产业受益相关性。没有分部数字则UNKNOWN，不能从主营文字猜90% |
| demand_change_v1 | 同口径需求量同比及较上期同比变化；价格、库存各自存原值 | 行业经营方向。价格上涨需结合上下游身份，库存变化需排季节性，三者不盲加 |
| earnings_quality_v1 | TTM经营现金流/TTM净利润；另报利润为负、非经常损益、应收增速 | 长期质量研究。分母≤0时不用比值排名，改专门风险解释 |
| capital_return_v1 | 适用行业NOPAT/平均投入资本，或经核对的ROE；保留计算构成 | 长期盈利能力。金融与负投入资本不硬套ROIC；不把高杠杆ROE当质量 |
| balance_risk_v1 | 净债务、现金/短债、利息保障及到期分布，各自独立 | 硬风险/复核。缺到期数据不认定无偿债压力 |
| valuation_range_v1 | 正常化每股盈利×可比倍数区间，或经审核的现金流情景；记录摊薄股数 | 确定研究买价区间。亏损企业不使用普通PE；行业倍数用同一时点样本 |
| relative_trend_v1 | 例如过去60交易日个股收益减行业收益；扣除当日未收盘信息 | 中期候选排序与纪律。窗长只是首个实验候选，非已验证最优 |
| trading_capacity_v1 | 拟交易金额/过去20完整交易日日均成交额，附停牌、价差与限价状态 | 执行容量。未知量额不能默认正常；成交额元与万元统一 |

行业内分位使用当时可见的合格同业集合与最小样本政策，缺少同业时显示不可比较；不要把0分位当缺失。研究初期允许使用绝对原值和区间，不为展示整齐强行产生分位或综合分。

时效按类型而非统一24小时：行情依交易时段；财报按披露/更正刷新；行业数据按其官方发布周期；新闻按新事件与更正刷新。缓存TTL仅控制获取频率，不能代替证据资格。

## ADR-F06：AI共享事实，按事件增量工作

AI调用两类：①带来源的结构化提取/反证，②最终结果解释。可在同一次结构化响应生成多个证据字段，逐步替代六维重复读相同材料；保留独立旧bz作为对照，批量合并须验证各维质量没有下降。

事件去重先用源公告ID/正文hash；跨来源再按主体、事件类型、发生时间、关键数值聚类，保存转载关系。更正公告是新版本，不被去重吞掉。一个经济事件可关联多个公司，但公司业务暴露不同，不能复制同一影响分。

失败规则：解析失败、无引用、引用不存在、未来日期、单位不合法→拒收对应claim；必要证据不足则INCOMPLETE。自报confidence可保存供诊断，不能直接进入仓位计算。若未来确需模型概率，必须有独立标签、校准集与Brier/ECE及稳定性验证。

RAG提供方法依据与历史研究，不能把教学观点当当前公司事实。检索块保留ID/内容hash/版本，解释引用可追到块；将已提出但未完成的回答级引用归属纳入F6。

`expect`记录计划事件/预期状态，`events`记录已发生事实；两者可关联同一event_id但状态不同。`fear`进入环境证据，可影响预算实验与研究优先级，不直接给另一份买卖票。AI看到技术状态后产生的判断标记相应lineage，不能再作为独立技术确认。

## ADR-F07：组合预算统一求解，禁止逐股各自满额

输入：已确认持仓/现金与净资产、各计划、候选动作、同一价格快照、已设置风险档。未设置风险偏好时复用可确认的现有配置并标来源；若没有可用配置，不假装知道用户承受能力，只输出方向及缺少的信息。用户只需一次建立账户风险档，不每只重复问。

预算提案采用确定性约束分配，首版不做收益协方差优化：

```text
w = 当前已确认的个股权重
add_budget = max(0, min(
    策略计划目标权重 - w,
    个股权重上限 - w,
    周期总预算 - 当前该周期总暴露,
    行业/主题上限 - 当前该组总暴露,
    扣费后可用现金 / NAV,
    允许交易金额 / NAV,
    个股损失预算 / 压力损失率 - w,
    组合剩余压力损失预算 / 该股压力损失率
))
target_weight = w + add_budget
```

该式仅分配新增风险，必须区分“总目标”和“可新增额度”。如已有暴露越限，另由减仓策略生成动作，不能把负新增额度直接解释成已成交减仓。压力损失率未知/非正数时不给此模型的精确仓位；各新增分配后同步更新剩余额度。对行业与主题同时有多个约束时，每个约束都要满足。

所有比例以账户净资产为分母。压力损失率必须显式建模跳空/连续跌停风险；止损价不能保证最大损失。相关主题（如同一产业链多个概念名）聚合风险，不因名称不同当分散。

求解顺序：先识别必须退出/减仓的风险，再分配新增；只有已确认卖出或情景中明确可实现的现金才能用于新增。`cash_if_all_proposals_filled`仅是情景，不能当实际现金。按“风险紧迫度→计划内到期→合格新增”的稳定排序分配；同级使用明示因子排序与security_id稳定打破平局，不能BUY/SELL混在一张强度排行榜。

建议目标比例转换份额前扣除费用并按对应市场数量规则取整；持仓只记录比例而无净资产/份额时，不展示精确股数及T+1可卖量。增加一只风险更高或证据更差的候选不得自动增加总预算。

批量输入排列变化不改变结果；同一个证券多路候选合并一次；全量冻结账户版本，计算完成后如账户变动，建议标STALE要求重算，而不是继续复用当日成功缓存。

## ADR-F08：体验围绕行动与计划变化

`today`分“需要处理”“等待条件”“继续持有”三组。紧急风险始终显示全部数量与入口；默认仅展开少数最优先卡，其余可展开，不隐藏退出风险。无操作是合法且清晰的结果，不为了显得有用每天推荐新票。

卡片顺序：行动+周期→最多两条决定理由→一个主要阻塞/风险→下次触发→数据与来源入口。对未建仓用户展示“研究候选/入场条件”，不混用已有持仓的“加仓/继续持有”。对同股另一周期研究加显式“比较视图，不影响当前计划”。

`diff`比较同policy及可比数据语义；跨策略版本先显示“口径变化”，不计算无意义评分Δ。区分证据更新、市场变化、用户计划变化、规则版本变化。chat用固定事实骨架加可选解释；LLM失败则模板卡仍完整可用。

自动watch准入需单独定义：无持仓、有尚可验证的投资逻辑或明确待满足条件才入研究观察池；硬风险否决、仅接口失败和无逻辑支持的WAIT不一律入池。手动watch保持用户主导，移出语义保留；状态变化可解释，避免首页精简后观察池继续无限堆积低质量候选。

全量决策及证据落盘，首页精简不减少研究可追溯性。为截图/导出报告保留decision_id与截止时间。内部hash、类名、置信度算法留诊断视图，不占普通用户卡片。

## ADR-F09：缓存、迁移与回滚

快照、研究、决策三层缓存分开：数据按源版本+时点；研究按snapshot/content hash+prompt/model/scorer版本；决策按research_id+plan_revision+portfolio_revision+policy/parameter版本+market_phase。现有bz缓存不偷偷改语义，沿用CACHE_VERSION纪律；v2新增命名空间。

`evidence_v2`与legacy记录并存。旧证据缺字段表示NOT_RECORDED，不按现在数据补造历史结论。旧`score/decision`保留legacy含义，新字段明确终态；渲染与diff采用显式适配器。JSONL保留追加式，不prune研究证据；坏行隔离、有告警。

推荐启用阶梯：`legacy_only -> capture_only -> shadow -> opt_in -> default`。capture_only记录旧路径，不改变决策；shadow运行新策略但不改用户行动卡主结论、不回写实际持仓。两者差异报告标记原因，不能只比较总收益。

回滚切回旧策略计算时，**不得恢复分析即成交的写入行为或撤销已确认成交**；持仓事实分离和动作一致性属于安全性修正，独立保留。新计划版本、证据与成交日志不删除，未知字段保留。迁移先dry-run生成差异，明确用户真正需要确认的只是已有持仓意图/事实，不为普通读分析反复询问。
