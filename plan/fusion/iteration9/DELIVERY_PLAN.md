# 第九轮：账户与行动说明统一补修 → 合同冻结 → M2只读研究 → T01 today卡片 → G3研究资格

更新2026-10-17。**本文件仍是第九轮唯一合同与历史证据入口；本批后暂停。** v0.8.42限定接收`ACCEPTED_CURRENT_CONDITIONAL_CASH_OBSERVATIONS_ONLY`，未commit/tag/push。父12文件328 passed/1 skipped（29.05s），最终全量3049 passed/0 failed/3 skipped/1 external deselected（229.65s）；460项源码/测试/材料指纹起止稳定，7保护hash/知识库集合不变。network 785、child 12均阻断、real_state 0。独立8组按轮次7 passed+1临时夹具错误（4.04s）后仅受影响组1 passed（2.19s），真实worker共2次；原guard两正式文件107 passed/1 skipped（6.03s），最终进程退出case1 passed。真实PDF和SSE各请求一次，产生1个当前条件化组2期。v39-v41历史验收保持。真实MID仍NO_VALID，历史行业/PIT、ROE、现金持续性UNKNOWN；G4整体/V1未完成；capture_only/default、旧接受态、缓存、阈值不变。

**此前G3合同保持**：数值/来源资格修复升`cv6/g3.binding_v4`，综合method自动吸收组件变化，使已保存cv5评估待复核；旧原件及接受主槽保留，新结果只入候选。旧cv3冻结语料不覆盖。命题算法assertion_v2、笨总缓存和交易阈值保持；shadow_v15仅冻结已验工程合同，不宣称真实MID/投资效果过门。未来新增解析能力独立版本化，不追改本合同。

## 实施与交接：只从这里进入

**当前窗口**：v42按下方既有合同限定接收：首个C15非金融样例为SSE当次转述，真实产品形成1个条件化组2期；J金融类为不适用，其余未知分类保持UNKNOWN。历史行业/PIT、ROE、现金持续性UNKNOWN，真实MID NO_VALID，G4整体/V1未完成。v39-v41历史结果不重算。当前批次结束后暂停，等待用户新指派拟议的Codex管理审批+DSH开发协作方案；不启动G7/T05候选或v43。

| 需要时读什么 | 现有文件及用途 |
|---|---|
| 项目共同约束 | 根[AGENTS.md](../../../AGENTS.md)；当前角色由用户指派，CODEX专属架构师职责不替实施者分配身份 |
| 架构裁决与审批依据 | [R15_ACCEPTANCE.md](R15_ACCEPTANCE.md)，查看G3最新审查段；本文件负责执行，R15负责独立裁决 |
| 中断后恢复与整体进度 | [RESUME.md](../RESUME.md)、[STATUS.md](../STATUS.md)、[MILESTONES.md](../../../MILESTONES.md) |
| 验证及交付证据 | [保护测试入口](review_test_runner.py)、[证据索引](REVIEW_INDEX.json)、[定向日志](TARGETED_TEST_RESULTS_P.txt)、[全量日志](FULL_TEST_RESULTS_P.txt)；当前批次以R15最新段和对应日志为准 |

证据索引、既有日志与R15已包含当前复核材料；本文件只维护当前窗口和长期待办，不复制反例清单。v34历史全量2261/定向547与v35本批全量2301/定向290各自保留指纹；G4原型及后继各批验收分别保留，不把旧待审说明当作当前开工项。不要重新按历史失败清单开工。

## 当前增量｜Grok交付审查与缓存实际补修（v0.8.36限定接收）

Grok从3b4f07e至ec94bfc共92提交、105文件：103个测试文件和2个证据工具/数据文件，生产src零改。T07哈希一致的来源保留/离线复算有用，限定接收；T05的102文件16332行主要是既有行为基线，不算功能、修复或性能改善。停止无限追加count-only探针；详证保留原日志/Git，四份入口已去重复流水。

本次Codex继续实施G7/T05的明确完整性修复，范围固定：同一market_cache模块中拒绝无效/未来时间戳，新鲜命中与失败回退都不借异常缓存；有效过期缓存回退、原TTL边界和交易时段规则保持。磁盘改同目录唯一临时文件写完后原子替换，部分写失败保留旧文件且清自己临时文件。关联镜像测试用紧凑参数矩阵承接，不改投资阈值、账户或付费来源。Sol/medium实施、Sol/high独立复核；正式反例先红、再定向和必要联合全量。v0.8.36本批限定验收通过，未commit/tag/push；全量2730 passed/0 failed/2 skipped/1 external deselected（224.53s）。独立缓存306目标及8项并发/失败探针分别通过。7保护hash和知识库集合不变，源码/测试/原型起止指纹一致；网络785、子进程2次尝试均阻断，真实状态访问0。 本地工程接收不等于已发布，不声称T05整体或性能改善。

G4原型的未来报告期漏检已独立复现并补修：报告期末晚于分析截止不贡献点。父正式4红→关联83绿；独立49正式+2原反例通过，限定接收离线原型，未接生产。T07独立5项（包含6/6原件复算）限定PASS，不替代原件主体/期间/URL独立核验或PIT。下一能力仍是G4已有归档的只读观察与缺口展示接线，须先明确可展示字段与方法隔离，不直接填values_by_period或宣称长期逻辑成立。

**接续停止线**：有限矩阵完成即停止新增计数测试。等待模型或资料时只做合同中独立可交付项，依赖不具备就交付缺口；不能自行串出无限T05子任务、把已知错误行为写成正例或把日志流水复制进四份入口。生产资格、真实研究样板、性能/投资效果分别验收。

## 当前交付｜G4归档财务观察侧栏（v0.8.37限定接收）

**映射与用户结果**：G4/G7/T04。在既有`research <代码>`文本及`--json`中，看见实际读取的现金流/净利润/ROE字段留样、实际报告期、原件引用和合并缺口，区分“已归档待核”与“未读到可用资料”。未资格项不显示数值；资料观察不代表长期逻辑成立。无资料/读取异常只占简短提示，不刷一页UNKNOWN。

**验收状态**：v0.8.37归档观察展示本批限定验收通过，未commit/tag/push；最终全量2749 passed/0 failed/2 skipped/1 external deselected（361.44s），5文件101定向及独立文本增量20项分别通过。 独立审查曾发现文本只有“核对所列原件”却无引用，真实合同反例落正式后补显示≤2份归档留样短号、其余数量/完整引用见--json，以及≤3类中文隔离原因。补前2749全量（328.43s）只为历史快照，不能代替补后最终验收。getter新增读取零网络/AI/写入，整个research仍保留原有补采/评估/草稿流程。下一步按G4核定真实发行人×报告期的现金三字段原件、期间起点/合并口径及行业依据，先固定证据登记合同，再扩数据资格；不继续堆展示或计数测试。

**设计批准与所有权**：Luna/high核实际接线；Astra/low批准独立只读侧栏，不能沿用丢期间/重造时间的四季财务投影。Sol/medium独占`src/core/cash_observations.py`、原plan原型兼容导出、`research_application.py`、`main.py`研究渲染和一个正式接线测试；主控版本/文档/整合，Sol/high只读独立复核。生产不import plan，已验算法单份提升，原49矩阵复用。

**固定边界**：应用层`get_cash_observations`一次查现有索引，每个唯一raw digest最多读一次；只读实际已归档期，不改默认四季assessment输入。核64位digest、payload schema/sha与原内容重算，索引/raw证券×指标×报告期×来源版本一致；缺主体不从请求补齐。实际时间严格解析且不得墙钟补证，未来期/未来公开资料拒收，同身份异值冲突隔离，同digest跨不同observed_at重复也不增加字段或期数。research --capture在分析起点后写出的queried_at/observed_at/fetched_at只作采集元数据，可列为本次可读留样但须标不证明起点时已可得；不能据此授予历史/可比资格，不能重造或延后as_of。旧query会静默跳坏行，故coverage_complete必须false/unknown，只承诺本次可读索引，不拿空列表证明没有历史。

**两层数据与身份**：归档声明的日期/单位/引用须标待核；未资格的value/EvidenceRecord不得漏入JSON或文本。字段语义/起日/行业缺失明确汇总；无核验器行业恒UNKNOWN，不放宽cash/ROE组，不填values_by_period。新`financial_observations`为可缺省的独立display回执/版本/快照，不参与综合method七组件、factor/provenance、评估、计划、接受态、shadow或默认策略；旧结果缺字段不补假0，`get_daily_view`保持。本批新侧栏零网络/AI/写入；整个research原有评估/草稿写流程保持，不能称整个命令只读。

**完成门与停止线**：真实临时ResearchStore→应用→同一Result文本/JSON/REPL桥回放；旧归档覆盖可见但未资格sentinel值不泄漏；hash/证券/期间/未来/缺raw/读失败/重复正反例；getter封写且真实文件/目录hash不变；加入侧栏前后run/snapshot/assessment/draft身份和接受主槽相同；保留49原型矩阵。先业务红再定向、一次独立复核及必要联合全量，生产可见结果和生命周期冻结均过门即收口，不追加计数quota、不扩新源/行业核验/财务阈值或通用表格。

## 历史交付：v0.8.35工程验收（本批增量见上）

最终16文件290 passed/0 failed（107.26s），全量2301 passed/0 failed/2 skipped/1 external deselected（342.60s），各自统计。外联/子进程尝试均被guard阻断，真实状态访问0，7保护hash与知识库集合不变，源码/测试/模板起止及停写SHA一致。独立Sol/high T01最终17边界回归及10格探针通过、T04小步PASS；完整证据只见R15最新v35段及REVIEW_INDEX，不重跑无变化的旧批。

已关闭T01的显式快照混用、重复证券覆盖、负股数/非有限现金误判与精确集合回归尾项。ISS-116范围仅已登记业务读取，管理pos视图豁免；不扩大为全部成交、并发事务或完整研究视图验收。T04九处配置消费者直接用src.config，配置反向依赖0，保留4处有意CLI桥；不声称全部T04或性能优化完成。

历史交付原型为43项/关联144项；本轮补报告期门后为49正式、独立2反例通过，已被本轮2730全量覆盖，状态ACCEPTED_OFFLINE_PROTOTYPE_ONLY，仍未接生产。 仅支持显式日历年YTD及已明确单位/定义；未知行业或语义、冲突版本保留缺口，行业资格只是外部前提，不是生产核验；无经营解释始终UNKNOWN。Grok已核指纹且未重写原型。Sol/high已完成本批限定复核，G4离线原型PASS、T07来源保留/复算PASS。T07只补上一条哈希相符的来源URL，负债率原件不证明现金语义。

## 历史：P批裁决与证据（v0.8.31）

修复前基线为35文件485 passed；修复前新增探针分三次共19个不同场景7通过/12失败，不是一轮19项。v0.8.31最终全量回归1962 passed、2 skipped、1 external deselected、0 failed（338.26s）；定向回归131 passed、0 failed（18.55s，含原19探针全绿）。另一次110 passed（19.16s）为单独运行，不累加。785次网络尝试与2次子进程尝试均被阻断，成功0、真实状态读取0；7项保护hash与知识库集合不变。当前基础审批见[R15_ACCEPTANCE.md](R15_ACCEPTANCE.md)；2026-10-14最终独立Sol/high复核已PASS，限定P0/P1/P2补修过门并冻结shadow_v14观察合同。

不重批已通过Z1–Z3、O1/O2、ISS-117日期/身份/固定分母及同源预算；用户新反馈暴露了行动说明和止盈抑制的验收遗漏，仅局部重开这部分。没有查询603992真实行情，隔离案例不等于该股票真实决策。

## P0｜一次请求的账户事实和版本不能被旧投影或重读覆盖

**映射** R03/R05/R09→G1/G2/G5，T01。**所有权** src/data/portfolio.py请求事实/策略状态装配、src/core/shadow_diff.py同源账户绑定，以及正式账户/影子回归；不要修改已确认成交或修复事件协议。

| 已复现缺口 | 必须达到的行为 |
|---|---|
| PARTIAL无幸存lot+正RATIO_ONLY投影仍确认权重.1 | 不以旧比例确认当前精确权重。持仓方向按可独立事实判定，未知不反推数量/成本；有幸存正lot保留已知方向，精确新增冻结 |
| 快照读取失败+旧零比例投影给NONE/0 | 读取失败不能借无账本兼容证明空仓；UNKNOWN/None及原因贯穿state/packet，合法独立退出方向保留 |
| 合法YAML条目42+独立账本100股：ctx丢持仓，to_strategy_state裸读异常 | 持仓事实与投影元数据解码分离；独立账本已知持仓继续装配/分析，缺成本/日期如实未知；l/la/chat不因旧投影坏条目丢掉该股硬退出机会 |
| 请求ctx版本缺失，shadow另读retry_success并mid_effective=True | ctx显式提供即为同请求真值，不二次补版本。失败请求保持diagnostic，packet/两臂/落盘版本相同；下一新请求才允许重新读。ctx=None旧调用单独兼容，不混入当前请求 |

复用RequestAccountFacts，不建平行账户模型。将规范化持仓集合、可用投影metadata及缺口留在同请求结果中，消费者不能再独立解析坏条目、重复读快照或补版本。坏的非计划metadata逐项隔离后，同请求独立核定的ctx.trade_plan仍传入l/la/chat/shadow；trade_plan本身为None不得授予退出资格。账本对该股有明确数量事实时按事实与矛盾处理；正常无账本比例账户仍兼容，不强制迁移。

**固定矩阵**：保留原九行和PARTIAL零比例回归；补齐健康/PARTIAL/快照失败 × 无投影/数量0/正数量/比例0/正比例/坏记录 × 无lot/独立正lot的合法组合。每格先验证构造前提，同时断言ctx持仓/数量/权重、共享集合/incomplete、策略状态和packet。关键异常再打真实l/la/chat与capture→落盘→报告，不只断言清单有代码。健康账本明确零数量与无该股数量信息须分开；投影未知不能当作新确认事实。只在临时账户测试，源文件字节不变，同请求快照/版本读取次数不增加，下一请求刷新。

## P1｜查仓和分析消费同一规范化清单及权重资格

**映射** R03/R09→G2/G7，T01/T03。**所有权** src/chat/tools.py、src/chat/formatter.py、src/cli/main.py持仓装配/提示与正式公开入口回归；与P0顺序集成。

- 共享清单归一后的代码集合必须约束实际渲染；600519/sh600519只显示一次，不读共享集合后再裸枚举原投影。
- ctx权重None不得再显示旧10%为当前确认仓位；旧投影细节若保留，明确标旧投影且同时显示权重待核/数量事实。
- 坏投影但账本独立持仓的l/la/chat分析能继续，单股错误不阻断下一股。正常RATIO_ONLY展示保持；数量缺失不补0。
- 固定验证投影独有/账本独有/重叠/别名重复/坏条目/明确空/不完整及未知权重。集合相等、来源清楚、同屏无假FLAT；合法硬退出在最终包/影子/可见输出一致。
- today持仓卡、analyze_industry、TUI/Web及pos管理投影视图范围仍按ISS-116登记，不扩大为本轮全端重写，也不把登记当实现。

## P2｜同一终态说明：建议、执行、依据、风险和待核事项分开

**编号P2是任务卡编号，不是所有问题的严重性。** 此卡包含用户反馈和真实止盈抑制出口缺口。**映射** R02/R03/R07/R09→G2/G6/G7，T01/T03；**所有权** src/cli/action_view.py/main.py、src/chat/formatter.py、src/core/analysis_service.py，以及decision_engine.py/strategy_layer.py独立抑制来源的必要补修和正式回归。main.py与P1由同一实施整合者负责，不能让并行worker互相覆盖。

1. **终态只有一个来源**：已有packet的摘要/共用行动卡/chat读取其desired_action、execution_status、executable_action及适配后的目标；不再绕回StrategyDecision单独推终态。无packet旧路径显式降级，不编造缺失字段。
2. **首句核执行资格**：OPEN/ADD+UNKNOWN写“出现建仓/加仓建议，成交资格待确认”；BLOCKED写“意图保留，当前执行受阻”。ELIGIBLE也不承诺明天必成交。REDUCE/EXIT同口径保留退出意图，不因UNKNOWN变成看好/持有；买入待核订单数量/可成交量，卖出才核可卖份额。不能把研究系统没有实际委托/盘口信息说成K线全部缺失。
3. **依据不拿参数冒充**：展示实际采纳的普通动作强度/资格或独立来源；市场参数档、极端模式另标策略环境。普通A、P、C与独立强度继续分开，不从中文trace反推权限。无已记录依据就说未记录，不能补假理由。
4. **风险不是缺数据**：普通均线/市场风险归风险提醒；实际字段、注册资格、条件和执行UNKNOWN归待核事项。不要依字符串猜出授权。research_status不能因任意warnings就INCOMPLETE，也不能因无warnings推断公司研究/命题已核验；区分技术数据资格与AssessmentStore研究状态。保留完整提醒，不截断回去掩盖问题。
5. **止损/止盈提示核采用资格**：低confidence、非action、无适用持仓或未采纳的原始SELL只能明确为技能线索/未采纳诊断，不能称已授权退出；已合格独立止损/ST/期限正例保持。合成例take_profit .8已把BUY压HOLD，公开Strategy.process却ADD/.1→.3：独立抑制来源必须贯穿下游，不允许被普通BUY门重新打开。普通HOLD的既定买入门继续保留，不把所有HOLD一概硬禁，也不把弱止盈提醒升级清仓。
6. **解释趋势与公司评分的角色**：当前均线空头不是既有buy_gate硬禁，C缓存评分也不是本段代码的建仓否决。若现政策给逆势技术建议，清楚说明趋势/公司评分未提供同向支持；不得为消除文字矛盾暗加均线、C级、覆盖率/票数新阈值。政策效果如需改动，另按VALIDATION做对照，不夹带本修复。

**可见验收矩阵（已落正式pytest并通过）**：OPEN/ADD/REDUCE/EXIT × UNKNOWN/BLOCKED/ELIGIBLE；适配器钳制目标后SD与packet方向不同；完整行情+普通趋势风险不假报缺数；低置信止损止盈提示与已采用保护分别显示；止盈压制BUY后全链不增仓；参数说明不冒充采用依据；l/chat/行动卡/JSONL同方向、同资格；14项以上提醒完整保存/展示。隔离场景现由正式回归覆盖，不以单独探针输出代替验收。

**预期示意（不是603992的新行情结论）**：

> 当前建议：有建仓候选，成交资格待确认。
> 技术背景：趋势转弱，趋势没有提供同向支持。
> 采用依据：展示当次真实已记录的普通强度/资格或独立来源。
> 风险提醒：市场分歧、均线空头。
> 待核事项：事件真实性、订单数量与可成交量。

## 联合验收、版本与停止条件

先P0，再P1/P2；共用文件顺序整合。只修已验证缺口，不重写评分体系、参数家族、账户成交协议或旧缓存。版本按真实交付统一五处，告警改动三处同步；笨总评分输入没改就不bump CACHE_VERSION。若动作/观察推导语义改变，明确新的shadow候选协议并将旧v13及更早版本独立分桶，不用修改旧测试字面冒充回归。当前协议候选shadow_v14；权限预算协议iss117-s3-v1不变。

完成证据：原19个探针及当前定向矩阵均通过；全量1962 passed、2 skipped、1 external deselected、0 failed。核心4场景E2E以live/模拟bar经过真实Orchestrator关键层到evidence/shadow/actioncard；技能/外源使用固定替身，AI/Event/EntryExit未开启。网络尝试785、子进程尝试2均阻断，成功0、真实状态读取0；7项保护hash与知识库集合不变。此范围外的其他模块、券商执行及投资效果未验证。实现/回归门槛已通过，独立Sol/high复核PASS，K1/shadow_v14观察合同已冻结。停止条件固定，不追加泛化门槛。

独立Sol/high复核已PASS，限定P0/P1/P2补修过门并冻结shadow_v14观察合同；本次局部重开尾项关闭。M2有限生产l/la只读接线已最终验收。该状态不包含策略默认晋级或投资效果结论；capture_only、零付费实验、无真实账户迁移和投资效果未验仍保留。

## M2｜日常只读研究视图：ACCEPTED_READONLY

**映射** R02/R03/R04/R09→G2/G3/G7，T02/T04；**验收状态**`ACCEPTED_READONLY`：仅指有限生产l/la只读接线通过最终验证，不含默认策略晋级或投资效果结论。复用[R14已接受原型](../iteration6/m2_readonly_view_prototype.py)；不新做第二套原型。此设计使正常RATIO_ONLY用户也能在l/la看到已接受研究与下一步，不依赖用户先建数量账本。

**产品落点（已实现）**：既有研究服务提供显式只读查询并接入l/la共用视图；查询不调用`run(capture=False)`，不触发采集、AI或研究写通道。

**输出合同（已实现）**：显示MID/LONG各自已接受主版本、独立候选、assessment状态/缺口与next steps。候选不替换已接受态；损坏或null记录提示待核，不冒充无研究；旧方法明确待复核。评估沿用既有核对规则，不从facts文本重新算VALID；未核验研究不宣称VALID，默认resolver未装配时保持fail-closed。

**入口接线（已实现）**：l和la共享装配/渲染，只增加简明研究段；正常持仓/退出主结论保持原职责。chat/Web/TUI不增加研究入口。新增查询本身不运行research、不补采、不调用AI、不写计划；l整体仍有原有行情、分析证据和观察读写行为，不能把整个l描述为零网络或零写入。详细研究仍由显式research/--capture生成，plan2 accept和风险确认保持显式。

**最终验收**：全量1997 passed、0 failed、2 skipped、1 external deselected（334.44s）；network 785、subprocess 2次尝试均被guard拒绝，real_state读取0；7项保护hash及知识库集合不变。独立Sol/high核对7项当前源码/测试SHA一致，结论为“有限生产只读接线最终PASS，无本范围阻断项”。6文件68项定向结果独立记录，不与全量累加。小样本16次查询耗时0.634–3.567ms，仅覆盖查询，不含构造、UI或完整命令，不据此宣称整条命令提速。原始结果见[全量记录](FULL_TEST_RESULTS_P.txt)和[定向记录](TARGETED_TEST_RESULTS_P.txt)的M2 v0.8.32标头。

M2验收时登记的下一项为G3首个完整MID VALID证据样板；当前材料与缺口见文末“当前交付窗口”。样板必须包括`real_exposure`、`change_to_profit`、`window_and_refutation`三个必需命题；合成VALID或仅checkpoint TRUE不构成完整样板，实际订单不冒充已兑现利润。G4 LONG数据、G5组合、G6真实观察与效果仍属后续工作。

## 历史：v0.8.34工程验收与真实MID缺口

**状态：工程限定接收，具体范围以R15最新段为准。** HEAD仍`3b4f07e`加工作树。R01/R03/R06、同请求账户版本与影子执行状态、capture微秒、行业长输出/外源隔离、TUI/Web扫描与分析及Web日历同次账户、Web alias原键回写均已补齐并独立限定通过。最终父全量2261 passed/0 failed/2 skipped/1 external deselected（201.01s），23文件定向547 passed/0 failed（25.22s）；7保护hash与知识库集合不变，源码/测试/HTML起止及停写指纹一致。`FULL_T01_FINAL`显式传tests才是本次全量；`FULL_T01`的471项仅默认目标集，已勘误。

P/M2/T01既有合同、范围和验收数均为历史证据，保持原样。T01的六格矩阵和已限定业务读出口继续适用；管理`pos`视图明确豁免。v34受验时T01整体尚未关闭，以下旧兼容/集合缺口后由v35收口。旧`build_today_view(pm, account=...)`兼容入口的账户区可用显式快照、卡片另建facts；生产`today_command`只传`account_facts`，目前没有生产`account=`调用，故这是兼容范围待定，不是已证生产故障。`chat.get_portfolio`回归尚未精确断言完整代码集合/排除第4股；当前两段源码都从共享entries生成，未见产品错误证据，属于防御性回归缺口。ISS-116其余核对仍待办，不能因当前跨端增量PASS而清零。G3历史反例和本次过程日志见[R15](R15_ACCEPTANCE.md)及现有日志，不在本计划重复列旧失败清单。

**工程收尾已完成**：当前v34通过本批工程验收，尚未commit/tag/push；v15按已验工程合同限定冻结。全量网络785、子进程2次尝试均被拒、真实状态访问0；不称没有尝试。`cv6`/`g3.binding_v4`、`assertion_v2`保持；`capture_only`/default、笨总缓存、阈值和真实账户状态均不变，v14历史冻结保留。独立UI复核已发最终PASS和原日志，结束turn随后额度失败，此限制如实记R15；不冒称由其新跑父全量。

**真实MID仍缺什么**：龙蟠科技候选不再推进。真实原件002957年报PDF（SHA256前缀`0449ea…`）完整段落中的17,421.56万元现可识别，但主体/期间继承、并列业务关系不受当前核验语法支持，因此只到`EXCERPT_GROUNDED`。隔离research保存并重启后，as-of与assessment身份一致且仍为`UNESTABLISHED`，结果`NO_VALID`。另一份官方PDF无可用文本且主体未核实，不作为样板。Astra判断这段材料至多支持业务暴露，不能补足利润机制或反证条件；停止改写原文或为这两个候选扩展语法，保留具体缺口和NO_VALID。

**后续接续**：v34时登记的T01两项尾项已由v35关闭；当前按下方G4/T07原型与Grok五小时队列推进。G5、T05–T07、G6效果和G8的长期依赖继续见MILESTONES，不在此复制第二份路线。

## T01增量｜today共享账户卡片（v0.8.33，已限定验收）

### 2026-10-16收口：显式快照兼容与精确集合尾项（已验收）

**状态/映射**：ACCEPTED_REGISTERED_BUSINESS_READ_SCOPE，G1/G2/G7/T01；这是旧兼容接口合同收口和已登记防御回归，不重批v34工程范围。用户授权继续开发；Sol/medium独占`src/data/portfolio.py`请求装配局部、`src/cli/today_service.py`及对应today/P1共享清单正式回归，主控维护版本/文档和集成。T04由Luna只读准备，不与此项共享文件写入。

**固定合同**：`build_today_view(pm, account=显式AccountSnapshot)`须让账户区、卡片、ctx与版本全部取同一显式快照；通过既有RequestAccountFacts一次装配，不新建平行账户模型、不再读默认账本。显式快照先复制，后续调用者修改不改变本请求；坏显式输入按读取失败保守处理，不偷偷回落默认账本或旧零投影。传`account_facts`时该事实实例优先，冲突`account`不混入；缺省路径仍每请求一次读取，下个请求刷新。计划仍来自同请求投影隔离核定，不修改成交/提案协议、阈值、NAV资格或已冻v15合同。

**验收**：显式A与默认B的证券/数量/现金/版本冲突，最终渲染只能使用A账户事实且默认snapshot调用0次；提供facts与冲突account时全页服从facts；PARTIAL显式快照+旧零投影不能证明空仓；坏输入不二读洗白；显式快照及临时账户原件不改；下一请求新快照刷新；旧J2直调与生产真实REPL today回归保持。chat测试按真实持仓行解析证券集合，与共享清单精确相等且去重，覆盖投影/账本/重叠/别名/坏记录/空，不能把价格或六位数量误作代码。若仅测试缺口，无需改chat产品逻辑。反例先红→修复→受影响目标，独立Sol/high复核数据边界后再集成。

**原范围与证据保留**：today账户事实区与共享持仓卡共用一次RequestAccountFacts；账本/投影并集去重、未知权重不补值、坏metadata逐项隔离，下一命令刷新。查询只读，不拉行情/AI或写账户/研究，不改交易阈值。原4文件55 passed/0 failed（5.86s，新增16项），v0.8.33全量2013 passed/0 failed/2 skipped/1 external deselected（315.85s），独立Sol/high核对5项SHA后`ACCEPTED_READONLY`；网络785/子进程2次尝试被拒、真实状态0，7保护hash及集合不变。旧定向日志标题误M2，实际命令为today四文件。以下v34跨端增量不改这些历史计数，也不把原today验收扩为全端。

### TUI/Web业务读出口｜本次指定范围已最终接收

本次将同一请求的账户事实接入TUI/Web扫描与分析，并接入Web日历过滤；管理`pos`视图明确不在范围内，保持原语义。每个业务请求只取一次`RequestAccountFacts`，复用规范持仓并集、三态、已确认权重和独立交易计划；未知状态/权重及不完整清单在最终界面明示，不用旧投影或零值补齐，不把建议写成成交，也不增加交易授权。

**保留的六格状态矩阵与验收边界**：健康比例账户、账本独有持仓与代码别名、PARTIAL且有幸存持仓、快照失败叠旧投影、坏非计划元数据但有独立持仓/计划、健康空仓与异常空仓分别核对。扫描同时核实际排除集合和最终提示；分析核引擎输入及界面；Web日历核过滤代码及HTML。隔离账户改动须在下一请求刷新；至少一组使用真实临时PortfolioManager/AccountService，外部数据保持mock。管理`pos`视图和T01旧显式account直调同源范围不在本次增量。

**当前验收**：独立Sol/high指定范围PASS，父23文件定向547 passed（25.22s），最终全量2261 passed（201.01s），各自统计不相加；保护7项与知识库集合不变，停写SHA见REVIEW_INDEX。旧兼容直调与精确集合防御回归仍按上一节承接，生产today路径没有已证混用故障，chat清单也未见当前产品错误。这是v34历史验收范围；其余兼容/集合尾项现已由v35补齐，T01登记业务读取的当前状态见本页v35段。

## 2026-10-16 T04小步｜配置读取不反向依赖CLI

**状态/映射**：ACCEPTED_CONFIG_STEP，G7/T04，独立于上述T01写入。Luna静态盘点：非CLI模块有9个load_config调用点经`src.cli.main`，而`src/config.py`已有项目根定位/深合并/校验实现，main本身只是兼容wrapper。故仅迁移这9处配置消费者，不新建加载器、不拆大函数、不改变配置路径、合并/校验、环境兜底或缓存行为。

**所有权与边界**：另一路Sol/medium只改source_check、Web/TUI app、claim_llm_extractor、shadow_diff、rag.service、auto_scorer、chat.__main__的配置import及对应配置/evidence/shadow正式回归；main兼容wrapper和src.config实现保留。`manage_positions`、`_watch_pool_touch`命令桥与normalize_stock_code不在本步，不能据仍有CLI依赖宣称迁移失败，也不能宣称T04整体完成。配置与全局导入测试涉及作用域/首次import，需完整离线全量。

**验收与停止**：所有迁移调用实际读取src.config，禁止CLI配置入口时正常/异常路径仍有效；现有显式路径、任意cwd、深合并、空/非法根错误测试保持，失败注入转到真正消费模块，不能因异常被吞而假绿。新增测试须证明配置查询/下游调用真实发生，且捕获开关读取失败仍禁捕获；旧main直调兼容保持，零新增网络/付费或配置泄露。只有预定9处配置反依赖清零才算本步完成，保留的命令桥逐项计数；不改变策略/模型/方法身份、不升级笨总缓存或v15。反例/回放→目标→稳定全量→独立复核，收益仅声明依赖边界减少，不凭此声称性能提升。

## 已验窗口 G4/T07｜本地现金资料安全进入与回读（v0.8.39限定验收）

**前置已验**：v38新增采集主体/交易所/精确报告期门已限定接收（2844全量）；v37归档展示与此前原型保持，不重复开发。research自v0.8.22存在，R04资料→核验→候选→plan2接受→l/la只读→shadow工程链已接；现金资料独立于评估，长期资格仍未成立。原v38细节只见R15/index，不在此复制实施流水。

**本批合同与收益（已验）**：G1/G4/G7/T07。已有research新增`--cash-materials <位置清单.json>`，可配`--json`；导入分支只接收原件/供应商留样，不调用app.run或写评估/草稿。与--capture、--claims、检查点/引用/风险参数混用明确拒绝。普通research保留旧流程并从既有cash getter读回导入材料。用户只给文件位置与可选来源声明，不编事实值、不写VERIFIED；重启能找到材料及具体待核原因，不靠单股测试PDF静态捆绑。

**材料和资源界限**：清单schema为cash_materials.v1，original给path及可选source_uri/published_at，supplier_raw_paths给完整留样文件，pages为可选零基提取提示（不是语义证明）。路径相对清单目录。清单≤64KiB、PDF≤20MiB/40页、供应商合计≤4MiB/最多4份、最多6个提示页；PDF提取固定私有worker、20秒总限时，不OCR/解密/执行附件/网络/AI。资源界限不是投资阈值。原件来源/公开时间仅声明，严格解析、不截断；未来/同日时序不明显式待核，采集时间不补作历史公开时间。

**存储与完整性**：现有ResearchStore下独立cash_materials/blobs及receipts；原PDF及供应商完整文件按真实字节SHA保存，旧content hash另重算，不能用其替完整metadata绑定。内容寻址、不可覆盖，同名坏文件拒绝而不洗白。所有blob完成后最后提交receipt，失败不承诺成功，孤立blob不进完成清单。读回重核hash/格式/绑定、严格JSON重复键与路径界限；新材料不写普通raw/observations/originals/snapshots，不触发旧财务加载或同PDF负债率回执碰撞。重复/乱序/重启稳定，冲突保留而不最新赢家，getter只读且一轮同blob至多加载一次。

**核对能力与权限**：仅有界Q1合并表识别证券/标题/期间/单位/行列定位；不请求回填文内证券，不以归母利润替合并净利润。若表列/口径不能唯一证明，保留原件并给精确待核回执，不硬编码600519补绿。本批以安全intake及可回读为最低完整交付；字段内部一致也不等于来源真实。供应商content身份优先核对；旧content缺证券仅据文件内metadata.stock_code/bs_code/interface一致绑定，缺/矛盾/错接口/错期拒该项，清单和命令不得补齐。ROE定义、来源真实性、行业、PIT各自待核；行业premise=None、可比期0、持续性UNKNOWN。

**输出与兼容**：分别展示intake_status、document_identity_check、field_consistency、source_authenticity、pit_status。文本与JSON白名单均不输出未资格数值/比例/含值摘录、正文或编码blob。2023比较期仅属2024版可见材料，不能叫2023首发。只扩独立资料展示版本，不改cash_observations、旧四季因子/快照、综合method、评估/接受态、shadow_v15、capture_only/default与投资阈值。

**所有权与完成门**：Sol/medium独占cash_material_intake.py、ResearchStore窄接口、research_application独立入口/getter、main研究参数与一个正式测试；主控版本/文档/整合，Sol/high只读独立复核。先公开入口红→实现→真实临时库的CLI/REPL与重启文本/JSON→篡改/metadata/错股缺主体/错期/比较期/归母单位歧义/重复冲突/未来同日/坏超限超时/半写/旧接受态与未核值隔离矩阵→一次独立及必要全量。正式单测可注入提取器，但不能当作真实PDF worker过门；原review_test_runner不放宽，真实worker在另一个隔离验证入口仅允许固定受验worker，其余网络/子进程/真实状态仍拒。固定矩阵过门即收口，不追加计数quota、不造通用导入或PDF平台。

**验收与边界**：父9文件262 passed/0 failed（71.08s）；最终全量2889 passed/0 failed/2 skipped/1 external deselected（489.80s）。454项源码/测试/材料指纹起止稳定，7保护hash与知识库集合不变；全量1005网络/2子进程尝试全阻断、真实状态0。目标235网络尝试阻断，子进程/真实状态0。早期2红、precision 1红、尾项8红均为验收反例；独立8固定项经分轮修正后全部覆盖，无产品缺陷，各轮不机械相加。实际worker累计5次（4+1），精确venv命令与模块SHA受限执行，外联/其他子进程/真实状态均0；每轮一次预期Temp外硬链接阻断。真实PDF b706e237…与4条raw均`LOCAL_PAYLOAD_CHECKED/MATCHED_LOCAL_TEXT`；导入不run，getter零写，普通research后cash子树及accepted_refs不变。验证对象/服务重建后的持久化回读；未另起OS产品进程，也未声称独立资源超限case已运行（资源门由正式测试覆盖）。失败日志保留既有TARGETED，最新索引由主控记录。

**真实材料进度/后继**：仅600519、2023Q1/2024Q1；合并NP零基8、CFO零基10，2023来自2024版比较栏。ROE/可重放行业、来源真实性、字段语义及PIT正式资格仍缺；下一步独立`research --cash-verify <material_id>`派生`cash_numeric_proof.v1`，只做本地Q1合并NP+CFO/NP一致性核验，不升格来源真实性/PIT、不填`values_by_period`激活旧TRUE。

### 后继实施合同 v40｜本地现金数值一致性（2026-10-17，已批准实施）

**目的与入口**：G4/T07继续真实原件链，不重做v39归档。新增独立`research <代码> --cash-verify <material_id> [--json]`，只核指定回执，不自动扫描历史、不调用app.run/AI/外源、不写评估或草稿；与capture/claims/检查点/风险参数互斥。用户无需手填行值、比例或VERIFIED。Astra/low核真实本地PDF及算术后给合同，主控接收；核验完成后仍不称G4整体成立。

**唯一受支持口径**：有界Q1合并利润表及合并现金流量表；文内证券独立匹配，利润/现金表各自明确人民币元、当前/比较年份及1–3月累计期间。worker保留逐页边界；跨页只接连续页且表头不冲突，不从拼接全文任挑相邻数值。只认合并“净利润”总行和“经营活动产生的现金流量净额”，不拿归母/持续经营子项/摘要/经营流入小计/投资现金流替代；多候选或单位/列归属不唯一待核。万元、外币不在本步开发换算。

**计算合同**：全程Decimal保留原始词元和定位；空白/破折号不当0，非有限值拒。NP与供应商同单位精确比较，CFO直接取PDF，禁止比率×NP反推。CFO/NP按`decimal_ratio_half_up_6.v1`的ROUND_HALF_UP六位工程规则对照，供应商本身不先舍入；可十进制等值补零到六位，超过六位有效小数则精度待核。零分母单独阻断，负值可算术比较但不产生投资结论。六位规则不证明供应商真实舍入规范。

**版本、身份与存储**：独立`cash_numeric_proof.v1`、`cash_q1_table.v1`，新`cash_materials/proofs`。proof稳定身份绑定material_id/准确原回执内容hash、完整PDF/raw字节hash及raw content hash、证券/报告版本、期间起止与当前/比较角色、页/表/行/列/原始词元、解析/计算版本、结果及缺口；recorded_at不入稳定ID，存储字节另有hash。同material_id/同输入同算法重放幂等，内部枚举排序规范化；并发可保留不同采集时间的物理记录，但getter按proof_id合并一次展示、保留collection_times/storage_references，串行重放不新增等价proof；不同material_id不跨回执合并。旧receipt/proof/负债率originals不可覆盖，不写旧raw/observations/snapshots，不最新赢家。

**回读诚实边界**：显式核验当次真实worker可返回LOCAL_NUMERIC_CONSISTENT、NUMERIC_MISMATCH或PENDING_REVIEW。普通getter不启动worker/写入，仅展示recorded_result、依赖完整性、算术检查及`replay_status=NOT_REPLAYED_READONLY`，文案“留有本地核对一致记录”。getter核依赖hash/绑定/类型/算术，不能声称重新从PDF验证词元。自洽重写且重新寻址的伪造无法仅靠hash认证，不引入密钥平台或冒称producer认证。getter同blob只加载一次：可窄扩v39 reader的私有已核输入上下文，仅传紧凑摘要/供应商指纹给proof reader，不缓存整库PDF字节、不改变v39公开返回。

**公开权限**：仅白名单proof/reference/version、期间/列角色/定位、结果及中文缺口，未资格原值/比例/含值摘录/全文不进入文本或JSON。`source_authenticity`仍待独立核验，公开时间仍声明，FactStatus/semantic_status不晋级；行业未知、合格可比期0、持续性UNKNOWN。2023比较栏仍属2024版，不包装成2023首发/PIT。不创建EvidenceRecord、不填values_by_period，不改factor/method/assessment/accepted/shadow_v15/default/capture_only/笨总cache及阈值。本地原字节足够验本步算术，不为此新增网络下载；官方来源与PIT另过门。

**文件所有权**：一个Sol/medium顺序独占新增cash_numeric_verification.py及正式测试，ResearchStore窄proof接口、research_application入口/getter、main旗标/渲染；确需单次读取时才窄改cash_material_intake私有context，原归档协议/行为保持。不改cash_observations/research_snapshot。主控维护版本/start帮助、已有计划/状态/证据；Sol/high一次独立数据边界复核。其他贡献者成果保留，不递归分工。

**固定验收矩阵与停止线**：①真PDF/4条留样，经真实worker→临时库→两期NP与六位比率一致→服务重建文本/JSON/REPL回读；另一个证券/不同值合法表证明无样板硬编码。②归母/持续经营/摘要/母公司/CFO小计/投资流/错列/跨页缺头/重复行冲突拒绝。③NP差一分、相邻六位/半舍入、负数/零分母/百分数/万元/空白/非有限值。④错证券/交易所/接口/期间/metric/单位/metadata/content hash。⑤未来/同日/缺公开日、比较期不成首发，本地通过不升来源/PIT。⑥相同material幂等/枚举乱序、新算法另proof、PDF/raw/proof损坏缺失/半写、旧receipt/负债率/接受态不变。⑦getter零worker/写/外联且单blob单读，历史记录不冒充当次重放。先真实反例红→实现→固定正式/独立矩阵→一次必要全量；测试和真worker分别绑定最终SHA。过门即交付，不追加基线计数、通用PDF框架、新投资门槛或远期功能。

**v0.8.40验收结果**：裁决`ACCEPTED_LOCAL_CASH_NUMERIC_CONSISTENCY_ONLY`，未commit/tag/push；父9文件172 passed/0 failed（42.55s），最终全量2942 passed/0 failed/2 skipped/1 external deselected（396.40s）。456项源码/测试/材料指纹前后稳定，7保护hash与知识库集合不变；独立8组8 passed（10.67s），另原guard两正式文件98 passed（19.09s）；真实numeric worker独立3次，服务对象重建回读通过。只在600519原PDF运行真实worker，另一证券/边界由正式提取seam覆盖；未另起OS产品进程，未声称超限PDF逐项worker实跑。全量1005网络/2子进程尝试阻断、real_state 0；父目标235网络尝试阻断、子进程/真实状态0。原5类红为runtime工具输出，现已逐字恢复至既有TARGETED；正式worker/目标/全量计数分轮保留、不机械相加。来源真实性、语义/FACT、PIT、行业、ROE与长期命题仍未晋级。

### 后继实施合同 v41｜当次官方原件与报表字段资格（2026-10-17，已批准实施）

**目标与收益**：延续G4/T07，在已有`research <代码> --cash-verify <material_id>`增加显式`--source-check`，保留原离线模式。只有本参数才联网一次，核对官方字节并重新提取表格；成功的当次结果首次展示有来源、期间及精确谱系的合并NP/CFO与派生比率。普通research仍只读历史核定摘要，不恢复当前VERIFIED或给旧因子供数。本批不与行业取证捆绑，不以字段资格代替可比组或长期命题。

**独立取证**：只用材料已声明的官方PDF URI，不从请求股、文件名或URL日期补证。限HTTPS、精确`static.cninfo.com.cn`及`/finalpage/YYYY-MM-DD/十位公告ID.PDF`形态；无userinfo/片段/query/非默认端口、无域名子串放行、无自动重定向。默认TLS核验不关闭。一次有界请求，总硬限时30秒、PDF最多20MiB，类型/头部核对；限流、失败、超时、重定向或字节不符一律无当次合格字段。项目网络超时保护必须真正返回、不因executor join失效；超时后的工作不得落盘或宣布成功。不调用AI/真实财务供应商，不抓公告列表、付费或增加搜索平台。主控已免费只读预核同一URL：HTTP200、380443B、b706e237…与本地一致；这仅为可行性证据，不替产品当次执行，不证明首次发布日期/无更正/PIT。HTTP取证记录与离线测试网络计数分开。

**资格来自当次执行**：材料依赖先重核，下载hash必须等于材料PDF，随后经受控worker从这些字节重新解析；不得读旧numeric/source回执的PASS或tokens发资格。复用v40有界worker与唯一表头/行列解析，不复制第二套解析器。证券/币种/期间等共同歧义阻断相关字段；NP、CFO各自唯一时部分产出，单行失败不牵连另一合格行。NP=0是合法观测、仅阻断除法；CFO=0且NP非零允许真0比率。supplier缺失、错值、错股或冲突只影响交叉对照与警示，不能一票否决已从官方原件独立核定的字段，也不借匹配升级供应商全局定义。原v40离线比较合同不变。

**精度**：保存NP/CFO原始Decimal十进制字符串、各自证据ID及`CFO / NP`表达式；非终止小数不称精确商。新报表派生商用版本化34位有效数字、ROUND_HALF_EVEN规则，展示保留Decimal表示及精度标识，不量化为六位。六位HALF_UP仅用于supplier交叉比较，不回写财务观测。typed EvidenceRecord.value沿现有有限float/int表示，金额及比例转换须有限；非零下溢成0或表示超界只隔离该typed值、不造假0、不全局改EvidenceValue。保留精确操作数和显示串，不让格式化把小非零值写成0。

**时点**：声明日期单独保留，`published_at=None`、`version_available_at=None`、knowledge_basis=UNKNOWN，严格历史快照仍拒。本次实际取得并完成解析核定的时刻才可作available_at，提供独立observed_as_of/字段快照as_of；不把URL日期、supplier pubDate、旧receipt时间作可信公开时点。新source-check应用入口requested_as_of=None表示本次实时取证；若调用者显式指定旧截止而真实available_at晚于它，拒绝当次字段，不偷偷修改用户截止。旧离线verify(as_of)及任何旧研究/决策截止不改。2023栏仍属2024文档比较期，不能变成2023首发。

**受控typed快照**：使用既有EvidenceRecord与独立EvidenceSnapshot strict=False/current路径，不写普通raw/observations/snapshots或financial loader。新来源/字段/公式/核定协议各自版本化，只限本证券×PDF版本×报告期×合并总行定义；quality=OBSERVED、semantic_status=VERIFIED，不授FACT_CHECKED。CFO为独立经营现金流FLOW指标，不冒充比率，既有observe_cash不扩指标范围。supplier留样只为本样本交叉谱系，不改FIELD_DEFINITIONS、baostock_field_registry_v1或负债率originals。普通四季factor输入、method七组件、assessment/draft/accepted/v15/default/capture_only/cache与阈值保持。行业premise=None、现金合格可比组0、cash_sustainability=UNKNOWN；资本开支/营运资本/一次性项目等经营解释仍缺。

**保存、公开与信任边界**：在cash_materials独立source_checks命名空间追加来源/字段核定记录，依赖真实PDF/raw完整hash、材料与numeric关联、表格/字段定位和版本；保存完成后才承诺成功，半写不破坏旧记录。不复用旧证券×期×PDF originals键。资格构造器只消费本次受控取证/解析的内存结果；不提供序列化自报VERIFIED→当前快照渠道。普通getter零网络/worker/写，只给历史核定记录和依赖完整性、NOT_REPLAYED_READONLY，不输出历史记录中的金额/比例冒充当次已核。无producer签名，不能承诺辨识完整自洽重新寻址伪造；但该伪造不能绕过当次网络/解析进入当前typed字段。只有当次成功核定区可公开精确金额/派生展示串、单位/起止/当前比较角色、官方URL/字节hash/证据身份与实际观察时点，源失败时保留离线比较结果而无合格值。

**所有权与验证**：Sol/medium独占新窄source/字段构造模块、一个正式测试，顺序改ResearchStore追加接口、应用独立source-check入口/getter、CLI参数/展示；仅确需时复用/窄改v40纯解析接口，旧proof读取保持兼容、算法变化显式新版本。主控版本/start帮助/原有文档及证据，Sol/high独立复核。固定矩阵：①真实官方一次取证+真实worker+两期typed值/谱系经CLI/JSON/REPL，另一证券/不同值正例；②非官方/恶意URL、redirect、限流/类型/大小/超时/hash不符不给资格；③证券/总行/单位/错列/比较角色，部分行资格独立；④supplier缺/错/冲突仍保官方字段，NP=0、CFO=0、1/3、极小非零、float溢出/下溢不洗白；⑤旧PASS及完整自洽重写不能绕过当次取证解析；⑥显式历史截止/缺公开时间/比较期不成首发，strict历史拒；⑦旧生命周期/receipt/proof/接受态不变、getter只历史且单blob单读、失败存储不报成功。正式先红后修必须当场保存完整日志，不仅口述；固定正反/实际网络产品正例/独立/必要一次全量过门即收口，不追加计数quota、行业推断或通用爬虫平台。

**v41独立静态集中补修（2026-10-17）**：确认一个P1：`ThreadPoolExecutor`的`future.result`超时和`shutdown(wait=False)`不能解除解释器退出时对工作线程的join，慢流可能拖住CLI退出。批准窄改为daemon线程/有界结果队列、HTTP单调总截止；后台仅内存，无存储或授资格回调。验收既测函数返回，也用受控子进程验证永不完成fetcher不拖住CLI退出；不承诺DNS/socket在第30秒被强杀，不扩独立HTTP进程平台。源失败另保留可见的离线核对结果。显式未来requested_as_of可作请求截止，observed/available仍为实际取证时刻，不将其另判缺陷。正式失败现场当场保存，补修后按原固定矩阵独立复核；本段不是验收通过记录。

**v41验收结果（2026-10-17）**：`ACCEPTED_CURRENT_OFFICIAL_CASH_FIELDS_ONLY`。最终全量2994 passed/0 failed/3 skipped/1 external deselected（379.53s），458项源码/测试/材料指纹稳定，7保护hash/知识库集合不变；network 1005、child 12均阻断、real_state 0。父10文件224 passed/1 skipped（45.81s），独立8组8 passed（4.01s），原guard两正式文件105 passed/1 skipped（21.53s）；child用例红1（5.02s）→绿1（0.81s）为独立轮次，进程退出专用case最终SHA复核1 passed。实施结果与限制详见[R15](R15_ACCEPTANCE.md)。本接收仅覆盖当次官方NP/CFO字段资格路径；真实MID仍NO_VALID，行业条件化组仍0，持续性UNKNOWN。v42合同及其限定验收结果见本节后文；上述下一步是当时的历史接续状态。

### 后继实施合同 v42｜当次行业适用性与现金可比观察（2026-10-17，v41验收后实施）

**完整交付单元**：沿G4/T07，把当次官方字段接到有实际消费者的当前观察，不再单独建无消费者行业回执。新增唯一显式组合`research <代码> --cash-verify <material_id> --source-check --industry-check [--json]`。不带industry-check时v41合同不变；单独/重复/混参早拒。PDF成功形成当次合格字段后才查询SSE一次，PDF失败不花第二次请求；帮助明确PDF一次、SSE一次，普通research/getter/离线核对均不新增网络。先等v41停写验收完成，禁止在其全量期间改受验源码。

**已核来源及权限裁决**：主控由SSE列表HTML明示的`search_stocksDepositoryReceipts_2021.js`解析公司资料参数，未猜接口。2026-10-17实际GET `https://query.sse.com.cn/commonQuery.do?isPagination=false&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C&COMPANY_CODE=600519`，200/1710字节/SHA d3351117…，真实单行COMPANY_CODE与A_STOCK_CODE均600519、门类C/制造业、大类15/酒饮料和精制茶制造业。原字节只在系统Temp，见既有index的industry_source_preflight，不是产品验收。SSE页面说明转述CAPCO且具体结果以CAPCO为准：允许限定`CURRENT_SSE_REPORTED_NON_FINANCIAL`作本次条件化现金观察适用性，不能称CAPCO原始核验或2023/2024行业已证明。首次小范围映射只支持C/15这一个明确非金融分类；金融J/66–69代码及准确描述需对应已核标准/官方字段，返回NOT_APPLICABLE；其余未知、代码描述不符、错股/冲突/缺失为UNKNOWN，禁止“非J全是非金融”或按名称/业务/请求股补证。保留代码/原描述、CAPCO机构/SSE转述方、URI、真实字节hash、解析版本与实际取得时间，公开日/历史有效区间/版本公开日未知。

**有界、主体独立的采集**：固定HTTPS `query.sse.com.cn/commonQuery.do`、固定上述sqlId/isPagination，证券仅规范6位参数；TLS核验、无redirect/retry/失败换源。总硬限时30秒，JSON响应最多256KiB，严格UTF8/JSON任意层重复键拒绝、包络错误/非唯一主体/实际证券不符拒绝。不把回显请求参数当主体，以返回COMPANY_CODE/A_STOCK_CODE核实；不回显电话/地址等无关公司资料。沿v41已修的daemon+结果队列/monotonic截止窄复用，后台不持store/资格回调；不建通用网络平台。原字节可追加隔离blobs，保存联合记录后才承诺成功。

**当前观察新合同**：在现有cash_observations模块增加`g4.cash_observations.current_official.v2`受控入口，v1/prototype及旧研究路径保持。仅消费本请求官方PDF重提取、SSE当次返回构造的私有内存上下文，不提供JSON/旧PASS/自报VERIFIED快照反序列化授资格入口；私有类型不是签名，不承诺抵御任意进程内恶意代码。核当前受控来源/字段/公式版本、证券、PDF、币种/单位、合并范围、期间与角色、操作数evidence_id、原始Decimal及34位HALF_EVEN复算，只接受指定CFO/NP公式，不放行全部derived。保留v41字段；需补比率顶层期间/资格元数据时新建后继构造协议及证据身份，不无声修改旧记录。缺typed操作数/表示溢出或非零下溢、冲突/坏公式不形成完整点；NP=0仅不成比率，真零CFO保零，原始Decimal显示不六位量化。

**比较与时点**：仅同一当次PDF内明确的当前列与比较列，以已核日历年YTD经济窗口/定义/单位/合并范围及公式构成比较键；原period_basis=COMPARATIVE和column_role保留，禁止改成YTD/financial来凑组。财报两期齐备的真实正例应产出“1个当前条件化可比组、2期”，明确2023为2024文档比较栏、当前仅指本次资料版本和当次SSE适用性，不证明历史行业或2023首发。部分字段/单期如实展示缺口，不称完整跨期。联合快照as_of为本次完成时刻或显式请求截止，各字段available_at仍保实际取得时刻；旧截止前拒晚到资料，不改旧研究/决策时点。所有未知历史信息保持UNKNOWN，strict历史继续拒；持续性仍缺资本开支/营运资本/一次性项目等经营解释，ROE与长期命题不升级，不填values_by_period。

**保存及可见行为**：在cash_materials/source_checks追加独立后继协议，保存原字节依赖hash与联合观察谱系；旧v41回执兼容且不覆盖，不新增通用行业库。行业失败不撤销官方财报字段，但无可比组。文本/JSON/REPL同源显示本次SSE转述分类/适用性、条件化组数与期数、两期NP/CFO/比率和来源短号、比较角色及历史/经营缺口。getter仍零HTTP/worker/写、NOT_REPLAYED_READONLY，只报“历史记录曾形成N期观察”，不得恢复当前行业资格或重新输出历史金额冒充本次。旧factor、7组件method、assessment/draft/accepted、shadow_v15/default/capture_only/阈值/笨总cache与真实账户/知识库均保持。

**所有权与有限验收**：一个Sol/medium顺序独占新窄cash_industry_qualification.py、cash_source_qualification、cash_observations、research_application、main旗标/渲染及一个新正式集成测试；必要时窄改对应回归，尽量不改ResearchStore/research_snapshot。主控版本/start/已有文档与索引，Sol/high一次独立数据边界复核；保留他人成果，不递归。矩阵8组：①真实官方PDF+SSE各1请求/真worker→两期→CLI/JSON/REPL，另股不同值seam；②非金融/金融/未知/错股/冲突/未知映射/重复键；③固定URL/参数/TLS/redirect/包络/体积/限流/超时与迟到无写；④COMPARATIVE保留且可按经济口径同组，错窗口/定义/币种/PDF拒混、重复不增期；⑤缺字段/零分母/真零/负数/1÷3/小非零/表示边界/公式篡改；⑥旧PASS/自报VERIFIED/任意derived/自洽历史伪造不绕当次取证，getter不恢复；⑦旧截止/未来期/UNKNOWN strict拒、当前行业不追认历史；⑧保存失败/半写/旧回执与旧业务保护。先红后修当场存完整日志→有限正式/独立→必要一次全量，过门即交付，不追加计数quota/孤立record阶段/行业投资阈值或批量抓源。真实来源失败就明确本次缺口，不用搜索缓存替实际返回。

**已知后继G7/T05（只读定位，待新指派）**：`data_provider._safe_call`、`akshare_client._call_with_timeout`、`source_check._probe_t`仍用线程池超时与`shutdown(wait=False)`，分别由评分/行情/体检可达。项目Python3.11.13的`_python_exit`会join pool线程；仅为同类机制风险，3个旧helper尚无专用子进程红例，不称已复现或修复；v41专用下载已修，不混入v42。该候选需等待用户新的Codex管理审批+DSH开发协作指派后再定合同；本轮不启动，不在后台执行。

**v42验收结果（2026-10-17）**：`ACCEPTED_CURRENT_CONDITIONAL_CASH_OBSERVATIONS_ONLY`。父12文件328 passed/1 skipped（29.05s）；最终全量3049 passed/0 failed/3 skipped/1 external deselected（229.65s）。460项源码/测试/材料指纹起止稳定，7保护hash/知识库集合不变；network 785、child 12均阻断、real_state 0。独立8组分两轮：首轮7 passed、1个临时夹具路径错误（4.04s）；修正CLI入口后仅重跑受影响组1 passed（2.19s），累计真实worker共2次，不能表述为一次8组全绿。原guard两正式文件107 passed/1 skipped（6.03s），真实worker 0；独立实际网络0，child 10阻断、real_state 0。55项正式新例中，超64KiB联合回执曾红3项，去除重复覆盖；非法count（3/bool）历史摘要曾红2项（2.39s）并已修复。最终实施3文件111 passed（5.33s）。最终产品case：一次PDF+一次SSE请求，HTTP/worker共2次，7保护hash与知识库集合稳定；联合回执25,219字节，getter只显示历史2期摘要并标`NOT_REPLAYED_READONLY`，不恢复当前资格。形成1个当前条件化可比组、2期，保留`COMPARATIVE`与派生值；SSE分类仅为当次转述C15非金融，历史行业/PIT、ROE、持续性UNKNOWN，真实MID仍NO_VALID，G4整体与V1未完成。进程退出专用case以最终10 pins复核1 passed。分轮日志已归入既有TARGETED/FULL；本批未commit/tag/push。

## Grok后续五小时接续队列（2026-10-16）

**当前交接点（2026-10-16，v38已收口）**：原Grok五小时队列仅作历史，不再执行原型/计数循环。v38新增采集身份门已限定验收（2844全量），未提交；下一步仅按上方G4真实现金字段/行业/安全资料进入通道继续。v37及更早范围保持，不把新采集producer追认为旧归档资格；G4整体、真实MID与默认晋级未通过。

**用途与总界**：供后续执行者按序接续，预算上限300分钟，建议五段20/80/65/75/60分钟；这是上限，不是必须做满的配额。当前v0.8.35的T01显式快照与T04配置迁移已停写。父线程报告：T01边界独立复核17用例及10内存格PASS、T04本步PASS；父整合目标16文件290 passed（107.26s），显式`tests`的FULL_V35为2301 passed/0 failed/2 skipped/1 external deselected（342.60s），网络785与子进程2次尝试均被guard阻断、real_state读取0，7项保护hash和知识库集合未变。仍以最新[R15](R15_ACCEPTANCE.md)及[REVIEW_INDEX](REVIEW_INDEX.json)中的停写SHA和最终记录为准；这些父报告不授权重跑已通过范围，也不代替独立评估。v34既有2261全量/547定向及shadow_v15有限合同冻结保留。真实MID仍`NO_VALID`；无付费AI、真实账户迁移、default晋级或投资效果结论。执行时先确认源码及测试未偏离索引；若父尚未完成最终收口，只处理索引明确的真实遗留，不重复既有通过项，`IMPLEMENTED_AWAITING_ARCHITECT`不得自行批准。

**角色与文件边界**：队列执行者先依据当时可用运行配置核实Grok能力，并按[agent-routing](../../../docs/agent-routing.md)标明实际模型/角色；若指定子Agent型号或独立审查工具不可用，先记明实际限制，不冒称调用GPT或已完成独立复核；该步骤保留待复核，继续不依赖它的T07，不静默换未获授权型号，也不因等待而改别处。G4原型实施者独占可新增`plan/fusion/iteration9/g4_cash_observation_prototype.py`及`tests/core/test_g4_cash_observation_prototype.py`（源代码/测试，不是新规划文档），独立复核者只读；T07由主实施者顺序处理既有`tests/evidence/k3_original_pilot/evidence_manifest.json`的已核来源元数据。除此以外只改实现合同明确批准的源码/测试。计划与结果回填仅限本文件及既有R15、REVIEW_INDEX、STATUS、RESUME、MILESTONES；不得新建计划/报告/交接文档。保留工作树他人成果，不覆盖脏改；不改产品账户、保护知识库、默认策略或真实状态，不外联付费服务。

1. **恢复与停写对照｜20分钟。** 对照R15、REVIEW_INDEX和当前工作树的文件指纹、测试记录及保护项，先看是否发生索引后的改动；不得覆盖或清理脏改。确认FULL_V35及16文件目标记录已在索引/验收记录中落定后，核对新增G4两文件SHA并进入第3段独立复核，不重做第2段已交付原型，不重跑父已通过测试。只有发现具体未记录差异或父未收口的明确遗留时才补其必要核验，并记录实际文件、SHA和结果；指纹不符且无法解释、保护项变化或证据缺失时停止并交主控。
2. **G4离线消费者原型｜80分钟上限，已实施，后继跳过重复开发。** 仅沿本计划既有`cash_sustainability`合同，对现有`CFOToNP`、`netProfit`、`roeAvg`消费者做只读、有界的typed多期观测、可比组、证据谱系与行业资格原型；复用合格EvidenceSnapshot和已有时点/质量门，不新增行情源。原型把“能观察到序列”和“命题成立”分开，并显式保留缺口。`research.py`已有至少两期和`values_by_period`正值分支，禁止直接补该字段来激活旧TRUE；多期正值但缺资本开支、营运资本或一次性项目等经营解释时仍为UNKNOWN。不定四季/六季门槛、比率阈值或行业投资标准。写入限一个新原型`.py`与对应正式测试；不改生产消费者、评估身份、缓存、影子协议或默认值。验收为原型输入/输出类型和每点证券、字段/单位、期间累计口径、证据/版本/修订及时点可追溯，现有消费者缺口可复现；若无法在时间内形成不触碰生产语义的原型，保存已验证差异并停止，不扩范围。
3. **正反矩阵与独立复核｜65分钟；43项原型矩阵已交付，独立复核待做。** 为原型建立固定、可长期复跑的正反用例，至少覆盖：同口径跨年观测；同一期间重复采集仍计一期；Q1与Q3混组拒比较；错证券/错期/缺分母；缺期；同期冲突修订；乱序与重复及重启身份稳定；未来修订不得进入旧as_of；latest-only可作当前展示但不得进入strict历史；金融行业为NOT_APPLICABLE、未知行业为UNKNOWN；NaN/Inf/bool拒绝；多期正值却无经营解释仍UNKNOWN。正例只证明可比观察，不证明长期能力。安排一次独立subagent复核原型和反例，复核者不得兼任实施者或自批。仅当矩阵通过且独立复核指出的缺口已收敛，才允许整理“可审查的小实现补丁”；生产变更仍须先取得对应设计/评估身份前置批准，不能借原型通过改评估、缓存、接受态或影子分母。时间不足或关键反例失败则修复原型/测试一次后停在原型状态，列明未通过项。
4. **T07便携证据补核｜75分钟，其中免费官方外源最多2份且不超过45分钟。2026-10-16结果：离线6/6 PASS；六点的证券、期间、页行、hash与extract_results一致。唯一缺URL的000002_2024Q2，官方`1221084198.PDF`（4,997,764字节）SHA256等于既有`3c7e7373…`，已写入evidence_manifest.json的source_url；隔离下载未覆盖仓库原件。第二份是已有URL的000002_2024Q1，远程字节与本地及清单hash一致，清单URL未改。六点supplier.raw仍为null，correction_search_status仍为not_performed，pit_availability仍为not_assessed。download_log仍无000002_2024H1行，禁止用build_evidence_manifest.py默认输出覆盖这份清单。2026-10-16后续增量：显式路径重建仅在既有点位file_hash与本次提取一致时保留000002_2024Q2的source_url，hash不符则丢弃；不补supplier.raw、更正检索或PIT。L3守卫10 passed（42.31s），网络/子进程/真实状态尝试0，7保护hash未变。默认覆盖与verify_and_register.py均未运行。T07仍未关闭。此前同日L3守卫为8 passed（39.40s）。负债率原件不证明现金流。** 不依赖G4审批，只复用现有6点manifest与旧验证脚本，先离线复验其可重建性并逐项核缺失URL、原PDF hash、页码/行号、证券和期间；把剩余时间用于复验及记录差异。最多查找两份独立官方原件，不下载无关批量材料、不付费；只有原件SHA逐字节匹配既有manifest/记录，才可补充来源元数据。找不到就准确记缺，不补造供应商raw、更正检索、PIT或原件hash；负债率原件不能证明现金流语义。不得写入真实账户或保护知识库，不运行真实登记通道`verify_and_register.py`；只用`verify_manifest_offline.py`并将结果放隔离根，已有原件不覆盖。若脚本将写真实状态或保护目录，停止该写入并报告。
5. **必要验证与现有状态回填｜60分钟。** 仅对本次实际改动运行对应guard/目标测试；若改动会影响正式源码或测试收集/共享导入，则最多再运行一次显式全量。对照最新停写SHA、7项保护hash和知识库文件集合，记录尝试与阻断情况，不把阻断说成“无尝试”。把改动、验证证据、未验事项、阻断原因和下一步更新到上列既有计划/状态入口，不覆盖历史验收数字，不新建文档。未独立复核的原型维持“待复核”，不得写G4接收、G4整体完成、T07关闭或投资效果结论。若无必要改动，不为消耗时段而重复测试或扩展任务；停止时交代实耗时间及剩余预算。

**全程停止线**：G4/G5未授权范围、真实MID样板、付费AI、真实账户迁移、策略调参、全市场扫描、`main`重写均不在此队列；不得因等待审批转去改别处。原型不通过或信息不足时保留UNKNOWN/NO_VALID并明确缺口。后续Grok执行结果由主控按证据独立接收；本队列本身不是实施授权的扩大、生产批准或G4验收。

**验证入口（先创建原型与测试，再执行）**：使用项目venv，stdout/stderr统一UTF8；新增原型`.py`也必须纳入测试前后SHA及最终索引，不能只记录src/tests而漏掉plan下的原型。测试结果追加既有TARGETED/FULL日志，原始失败保留，不新建结果报告。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -B plan/fusion/iteration9/review_test_runner.py tests/core/test_g4_cash_observation_prototype.py tests/core/test_factor_compute.py tests/core/test_financial_data.py
# 仅当本轮改动需要全量时，显式传tests；无参数是旧默认目标集。
.\.venv\Scripts\python.exe -B plan/fusion/iteration9/review_test_runner.py tests
```

## Codex与DSH直接交接工具（2026-10-20，独立于产品v43）

**当前入口**：用户授权使用个人`C:/Users/Crux/.codex/skills/codex-dsh-collab/SKILL.md`和`G:/Tools/ChatGPTRepo/codex-dsh-双端联通/bin/codex-dsh.mjs`。工具0.1.0、HEAD `60be2a32084ff2902ba98891b7fb6dd2adfe4b0e`已发布；先读其Codex手册。旧G1研究缺口不再是该薄控制器的实现前置，但全功能无损、官方最大长度、费用节省仍未证明。保持当前原生Codex聊天管理、单一原生DSH Host执行，不恢复旧极简SDK/独立管理聊天路线。

**本次有限合同**：在OS Temp读取当前未提交文件的副本，核对G7/T05三个helper的实际行为、调用点、返回/异常语义，提出独立进程反例及最小修复方案；原合同最多两条根prompt（一轮分析、一轮必要追问），用户随后明确追加一条量化复盘；都不是提供方内部请求硬上限。DSH只用原生read/search，不写文件、不跑命令/测试/网络、不起子Agent/jobs/schedules。主控和Luna/high只读定位交叉核源码，区分已读/推断/未测，不宣称已复现或修复。G7实施、v43及业务AI仍未启动。

**真实验收**：`PASS_REAL_NATIVE_READONLY_COLLAB_ONLY`。个人配置共享账本`C:/Users/Crux/.codex/codex-dsh-state`、连接`native`已通过一次本地掩码输入/原生stdin attach认证，不在聊天或Git保存凭证。任务`muyun-g7-readonly-20261020`、同一session `codex-f3ed1ac4-9ed0-41ba-bd78-364e9f33d477`：分析、追问、用户追加量化自评三个请求各自匹配COMPLETED（终态seq105/114/126）。首轮45秒观察超时后新CLI进程resume/follow继续完成，没有重发；此处实测为跨CLI观察进程恢复，不是故障断网实验。前两轮使用工具HEAD `60be2a3`，量化复盘时工具已更新`6694d2`（setup/connections）；不把混合版本当配对基准。早前27/0本地回归属于60be2a3，未冒称新版全量重跑。

**量化结论（样本N=1任务，不外推总体成功率）**：前两轮完成2/2，21/21原生工具结果成功（glob1/grep5/read15），一次认证后用户代传0次；追问及量化复盘均零工具调用。前两轮468源码/测试、7保护hash及6副本内容未变；原生会话running=false、两inbox为空。不证明全Host/子树停净、写代码/测试执行、审批或事务合入通过；当前安装的原生自动许可改装仍不宣称已恢复。

| 实测窗口 | 非缓存输入 | 缓存读取 | 输出 | 缓存占累计输入 |
| --- | ---: | ---: | ---: | ---: |
| 分析轮（12条模型返回） | 283730 | 3098880 | 12875 | 91.6121% |
| 同会话追问（1条） | 497 | 303744 | 1112 | 99.8366% |
| 原两轮合计（13条） | 284227 | 3402624 | 13987 | 92.2908% |
| 用户追加量化自评（1条，独立于原样本） | 1059 | 305152 | 3114 | 99.6542% |

上述是原生assistant/message事件usage，不是全部提供方HTTP尝试、Codex账单或货币费用。含自评累计非缓存285286、缓存3707776、输出17101，总token流水4010163，包含重复缓存读取。首请求输入含缓存261692，其中缓存7808（约2.98%）；原生249种工具定义序列化807492字节、system内容34709字节，体量已测，未把字节换成各项实际token。未删原生工具或改profile；3类工具实际使用不等于249类功能全验。无同版本原生直接操作对照，速度/总费用改善均N/A。

**DSH自评与主控纠偏**：DSH确认只读合同与副本无原Git，因此未提交，不是工具禁止。首轮提出无限Event夹具违背有限阻塞合同，追问后修为有限sleep；追问又混写subprocess.run与proc句柄，仍是设计草案。自评把缓存分母误用含输出总量，并称首请求以缓存为主；主控按输入口径独立纠正以上表，原自评不改写为“模型已算对”。通信完成率不能代替交付正确率，本样本不能认证可直接实施的修复代码。工具反馈优先项：机器计算并脱敏导出usage；显示观察超时/运行/完成阶段；绑定任务/请求/产物与验收项；合同固定git_owner及里程碑提交；建立同任务/版本/配置的配对基准。此轮不直接修改工具部署。

**历史试用资料**：`C:/Users/Crux/AppData/Local/Temp/muyun-native-collab-20261020-aPua6A/`保留六文件副本、提示和基线。旧task `muyun-g7-readonly-20261020`及其3轮请求只作只读试用证据，原基线ec94bfc/468源码测试/7保护hash保持，不充当当前开发工作树；不删除旧会话、失败记录或锁。

**当前正式Git工作区（2026-10-20用户要求后配置）**：task=`muyun-dev-native-20261020`，session=`codex-1de974ba-34f0-4de8-a092-6fdfa43e822c`，cwd=`G:\Tools\暮云思辨投资助手`。正常原生workspace/create与session/create(workspaceId)绑定到workspace `c2168aee-0263-45c8-8dd3-2595db031930`、标题“暮云思辨投资助手”，随后幂等回读确认成员。CLI尚无workspace封装，本次为API配置，不改部署或原生session头，也不冒称浏览器视觉验收。首个零prompt task `muyun-dev-20261020`因G:/与G:\原始字符串不同被原生cwd门拒绝，保留作设置记录；规范路径新task绑定成功，不绕完整性门。拒绝时还观察到Node25的UV_HANDLE_CLOSING退出断言，尚无稳定复现，列为工具诊断待办。

**接入确认范围与职责**：真实请求`2dd80fc1-21f1-4431-b993-1a50878fe252`完成（seq29）；2次read、1次pwsh及3结果，查到Git根/分支codex/v42-native-collab/HEAD 8ba7571。其2条assistant usage累计非缓存275529、缓存288256、输出885，独立于上表原3轮，不能混算样本。DSH按实施subagent接受具体合同，Codex负责设计/验收/追修/Git；原生仍是普通session，不声称嵌套Agent身份。DSH只读AGENTS前60行，写任务前须补读相关约束；pwsh含cmd/echo包装，不能声称严格仅3个Git命令合同完全符合。无业务修改或测试，不计G7/v43通过。

**跨对话恢复与唯一工具待办**：新Codex聊天读个人Skill/local-settings与本项目RESUME，在同一共享账本先resume当前正式task，核实际cwd、分支/HEAD及未完成请求；后续send默认继续同session。用户可指定其他session，先核目录、活动及task映射，已绑定则复用原task；换Codex聊天不会自动迁移上下文或改DSH目录。当前CLI对附加旧session不核cwd，不能用换填--cwd伪迁移。协作工具的长期问题/完成判据集中在其既有`docs/validation.md`“已知问题与迭代记录”，本机progress只导航；不再在业务计划复制工具TODO。

**2026-10-17旧桥历史**：原`G:/Tools/DeepseekHarness/codex-dsh/`任务d4175bb5d27a4545b7d4030da948087b的round1/round3在Temp样例完成并显式接收，父7/9项通过；原round2续接失败保留。SDKserver限定补丁53项、两独立PythonSDK进程mock及独立增量复核通过。旧自动run6e435382833347e9a7c573937b7873fe未获全自动闭环验收；这些历史不计为新通用CLI或暮云产品通过，不继续旧收费循环。

**里程碑Git收口（用户2026-10-20授权）**：重大改动验收后由Codex一次commit+push，DSH当前只读任务不提交；不再把“未提交”状态当永久禁令。独立Sol/high只读复核最新v42 460项、最终10 pins、7保护hash及12原日志均一致，pending为空，历史3049/0/3 skipped/1 external不重跑冒新验收。本次快照承接v31–v35前置和v36–v42既有接收范围，不全归为v42新增。18镜像测试删除及11历史资料清理有R15/S2AC索引依据；可移植.codex路由与必要新cash源码/测试随允许清单收入。发现正式财报测试依赖ignored原件，精确补入公开PDF和4份raw JSON，其余artifacts与私人状态排除；提交树已用Git archive导出且不带.git/未跟踪文件，正式原件用例1 passed/0 failed（9.66s），网络/子进程/真实状态尝试均0；导出树6保护项及知识库集合不变，原工作区7保护hash另核。实际原始日志追加既有TARGETED，不冒称新全量。

远端main已到`84fc530`，相对原本地`ec94bfc`新增7提交、13文件，含另一Agent的G3/NAV/可选垫片/pipeline；本次没有审核/整合该增量。里程碑已在`codex/v42-native-collab`提交推送`8ba7571b326cf643d90c0b12c6245d729f9cc369`，远端SHA核同，不强推或冒称main已同步。账户/knowledge、私人配置、Temp、无关教程/评估产物均未收录。当前接入和文档增量随下次实质工具修复收口，不为每条反馈碎提交；保护原有脏改，G7修复、v43、真实MID/G4/V1整体仍未推进。
