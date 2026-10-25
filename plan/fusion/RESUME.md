# 融合项目恢复入口

末项补修身份：原任务锁定后，新建同批有限目标`muyun-t05h-final-values-20261026`，session=`codex-fd73d051-ad2c-4fff-805c-cf1bf5913532`，合同SHA=`18f69ad1a098e56e91cc9e5665a929314c4bc48643994b55b97478427ce7b388`；仅补状态值异常隔离，不是下一产品批次。最新请求、红绿与停止状态见T05H回执的final_incremental_repair；新入口不得重投已完请求。

## 当前入口：2026-10-26 T05-H 已限定接收；按用户要求收尾停止

2026-10-26 T05-H限定接收：批量任务每次独立完整UUID，旧运行/清单外结果不能串账；ba按结构化结果区分已完成/降级/失败/未记录结果，降级维度不作数值或强弱点评；tasks只读隔离坏账本（包括空/非字符串状态值）并展示配置未冻结。修订反例12红→276相关通过（8.38s，守卫IO=0），末项精确红绿增量见回执，独立复核通过；本批未跑全量，也不声称最后增量后重跑了276矩阵。整批deadline/资源、有效配置及cache资格仍待，E48.0%/A36.0%/P42.0%–68.6%、18/47不变，G7/T05/V1未关闭。

**停止边界**：仅本批必要验收、文档和Git交付；交付后停止，不开启下一批、不追加缓存/AI实验。未来继续须按用户新指派恢复。

**身份与证据**：main基线`ce49724`；task=`muyun-batch-budget-20261025`，session=`codex-3f90283a-5f9a-4775-ada8-e90240268a42`；合同`muyun-v1-g7-batch-identity` rev1 SHA=`cc90d83e0a0848e1414a48d124b469d308f007642d943a752e125a1c679470d3`。bridge请求`1eb16278-ebe0-4777-9294-17d9c3ad82f3`缺凭证失败，用户启动正确Host后原生手动重试turn3，COMPLETED seq1013；不得盲重投。主控已读完整答复及真实diff/证据。回执：`iteration10/T05H_BATCH_IDENTITY_ACCEPTANCE.json`、`T05H_BATCH_IDENTITY_SOURCE_PINS.json`、`T05H_BATCH_IDENTITY_RESULTS.txt`；Git交付SHA以实际日志为准。

**停止状态**：已阻止新派发；工具STOP_PENDING因用户原生重试属于未绑定活动，未取消共享活动；实测running=false、queue=0，不能据此宣称整个Host后台全部停净。不要再次启动任务。9保护hash未变、原218未跟踪均存在（非218内容hash证明）；临时原件`C:/Users/Crux/AppData/Local/Temp/muyun-batch-budget-20261025`。

## 上一批：2026-10-25 V1-G7-SESSION 限定接收

**当前摘要**：用户恢复Codex+DSH协作；Codex审批/独立验收/Git，DSH实施。正式task=`muyun-dev-native-20261020`、session=`codex-1de974ba-34f0-4de8-a092-6fdfa43e822c`；最新完成请求`32577d22-b00e-4628-8ad0-6fce6a7e6763`/seq4096，已停写。基线main=`5dcfaa74ef4920cc782994bde0c3d04b2f58e5bd`；本段记录实施基线，交付SHA以Git实际日志为准。合同`muyun-v1-g7-session` rev3，hash=`ab5c64ad394ac4cac91a147b34e0e02b9b4dc7970e7d555b7686fa15d54370f7`。

**已验摘要**：统一12个直接生产消费者及CLI/chat关闭入口；独占非阻塞租约由实际daemon worker finally释放，忙即降级，调用方超时不释放租约/不另登出；复用祖先abandoned与同线程更早deadline，跨线程不借ID重入。登录/心跳/查询/fields/error/分页耗尽/必要close在worker及monotonic截止内；心跳终止error非0不健康。单查询15/20/30及原配置从登录前计；DataFeeder双query与正常close整体60秒，空stock同worker立即close且不查index，完整payload接收后才发布；独立ensure/login与logout 8秒，scanner保留整批预算。

**证据与限制**：真实round3当前未修代码4 failed→修后224 passed/1 skipped；心跳终止错误1红1绿；receiver完成Event后精确1 passed。全量3611 passed/1 failed/12 skipped/1 deselected（pytest 525.09s，process 542.18s）；唯一失败为`tests/core/test_j1_mapping_boundary.py::test_has_quarter_evidence_and_skip_archived`旧无限mock，局部改有限fixture/EOF且原断言不变后，J1+financial+session+consumers 148 passed/1 skipped（pytest 9.16s，process 12.4s）。无最终树单次全绿全量声明。9保护hash及原218未跟踪存在性稳定；1018全量前后哈希相同，修后仅该测试文件变化；受测生产字节未变。独立Sol/high最终PASS，接受增量证据；专用child round2 1 passed只复用未变daemon退出路径。真实外源、产品AI/账户、全部CLI退出及供应商线程强杀未验；shim、业务身份/PIT/复权/评分/缓存版本未改。回执、source pins、结果见`iteration10/T05G_SESSION_ACCEPTANCE.json`、`T05G_SESSION_SOURCE_PINS.json`、`T05G_SESSION_RESULTS.txt`。

**状态与下一步**：本批V1-G7-SESSION工程/验收各+3；冻结47条/111权重，E44/98→47/98，A37/111→40/111，P39.1%–65.7%→42.0%–68.6%，closed 17→18。唯一账本为PROGRESS；S1 18条均闭合，但G7/T05及V1整体未完成。下一产品任务V1-G7-BATCH-BUDGET：先核现有批量deadline/资源界、任务输入及配置身份、同日成功缓存与失败续跑，复用既有scanner整批截止；另按G3/G4原门独立取证，不假填G6日期。本次交付后不自动开始新实验。

本合同hash只标识已接收的SESSION批次；下一目标先核实际main新HEAD及有效合同，不直接持旧hash派发。

旧“仅Codex/下一SESSION未实施”及旧下一步段落仅作历史记录；它们不得覆盖本段当前身份和状态。恢复时核正式task最新请求、Git实际diff和三个回执资产，不盲目重投。

跨会话/跨工具进度接续统一执行[量化合同第7节](PROGRESS_CONTRACT.md#7-跨会话跨工具恢复与看板更新)：新对话先核共享账本，交付时由唯一写入者更新并生成看板；Claude Code/Grok Build/Copilot/Cursor入口已同步。产品批次按实际验收更新共享JSON；纯维护允许分数不变。

**当前管理入口**：先看[量化账本](PROGRESS.md)和[计分合同](PROGRESS_CONTRACT.md)，JSON是唯一进度事实源。本批V1-G7-SESSION已限定接收，增量和限制见上方及第十轮计划；进度前后由唯一账本记录。下一产品卡`V1-G7-BATCH-BUDGET`，先核现有批量deadline/资源界、任务输入与配置身份、成功缓存及失败续跑，复用scanner整批截止。G3/G4独立数据取证按原门，G6不假填真实观察日期；本批后不自动启动新实验。


## 历史批次入口（不得覆盖上方当前指派）

2026-10-25 T05-F2限定接收：备用行情、历史K线、指数趋势的query创建与耗尽共用各自既有登录后30秒预算，保留供应商异常/socket重置、迭代异常、空/error与正常输出。正式9红→54绿，主控8文件守卫159 passed（9.12s），532项受测指纹、9保护hash和原218未跟踪存在性稳定。本批未跑全量；登录/心跳、整批预算与原生会话重叠仍待，G7/T05/V1未关闭。 本批生产与测试独立复核PASS，见[验收回执](iteration10/T05F2_ACCEPTANCE.json)和[合同](iteration10/DELIVERY_PLAN.md)。E3/F1已分别推送e28c674/ba0fc87；本批SHA核main实际Git日志。下一项是完整Baostock非阻塞会话租约合同，需覆盖全部消费者的登录/心跳/查询/迭代/关闭，不能单站点加锁或仅补更多计数探针。当前只用Codex原生Subagent，主控审批与Git；保留用户配置/账户/索引。

2026-10-25 T05-F1限定接收：主题代码核验的查询创建、错误/字段读取与结果耗尽共用既有登录后15秒预算，首包挂起可降级；正常名称与退市过滤保持。正式6红→15绿，主控五文件守卫204 passed（3.64s），531项受测指纹、9保护hash及原218未跟踪存在性稳定。未跑本批全量；登录/心跳、整批预算及原生共享会话仍未解决，G7/T05/V1未关闭。 生产与回归独立复核PASS，当前证据见[回执](iteration10/T05F1_ACCEPTANCE.json)和[第十轮合同](iteration10/DELIVERY_PLAN.md)。E3已推送`e28c674`；本批交付SHA以main实际Git历史核对。主控只在Codex+原生Subagent实施/Git，不调用DSH。下一项先将非阻塞会话租约的完整消费者范围落合同再实施；不加只覆盖单站点的伪全局锁。

用户改派只在Codex实施，主控规划/审批/Git，原生Subagent开发与复核；不调用DSH。本批基线main@ba0fc87；当前9项保护文件（含用户本地.codex/config.toml）与218个原有未跟踪文件保持，Git交付核实际main/origin/main。

2026-10-25 T05-E3增量限定接收：嵌套取数继承祖先放弃Event，迟到快照/K线写入受既有门约束。守卫全量3514 passed/2 failed/11 skipped/1 deselected（550.35s）；修正两项测试越界写入后，受影响两文件10 passed（3.77s）。其余528项受测源码/配置/全局fixture未变，最终530项指纹登记；8保护hash与原207未跟踪保留，八child专用8通过。没有最终树全绿全量声明；Baostock会话、写检查后竞态、真实MID及G7/T05/V1仍未关闭。 见[验收回执](iteration10/T05E3_ACCEPTANCE.json)与[合同](iteration10/DELIVERY_PLAN.md)。产品、runner与最后两项测试均独立复核PASS；主控负责本提交和普通推送，交付SHA核main实际历史。下一批先限定Baostock查询与迭代会话边界合同；不追加无收益T04机械拆分。

下方Claude/DSH身份与旧“下一张”保留为历史。恢复先读本段与实际Git；3494项全量是2026-10-22 T04-L历史证据，不是T05-E3结果。

> **第十轮（2026-10-21 起，Claude Code 主控）**：用户指派 Claude Code 接手迭代并兼任架构师，方案落盘即推送。T05-D（ISS-120 剩余 4 个旧并行 executor 的退出界）与 BUG-OW（ISS-122 超配涨幅禁入被 rich 未配对标签绕过）已实施完成并过独立复核，全量3184 passed/0 failed/11 skipped/1 deselected（656.61s），commit T05-D `ac6ce8a`、BUG-OW `c67b85e`；BUG-IDX 测试侧修复 `28b7e68`；T04-A 账本编排下沉 `c7fb92b`（全量 3210 passed/0 failed/11 skipped）。T04-B（TUI 加仓假成功修复 + chat 删仓去 CLI 桥）`03cab94`，全量3252 passed/0 failed/11 skipped/1 deselected（764.51s）。BUG-FEE `9d13a3f`（ISS-125），全量3259 passed/0 failed/11 skipped/1 deselected（626.36s）。BUG-FINITE 写侧 `7de1e59`（ISS-126），全量3294 passed/0 failed/11 skipped/1 deselected（659.74s）。**本提交**合入 BUG-FINITE-READ（ISS-126 读侧：重放 `_isolate` 非有限事件 → PARTIAL；nan 成本可读可再写；today/NAV 未知语义；定向35+相关161；全量3293 passed/1 failed（cash_material折行无关）/11 skipped）——系 Claude 写侧后明确留下的下一张队列卡，非平行新轨。Claude 本轮已接受主线（T05-D/BUG-OW/BUG-IDX/T04-A/T04-B/BUG-FEE/BUG-FINITE 写侧）不动。**本提交**合入 **T04-C**（草稿编排下沉 + chat add/plan/overweight 去 CLI 桥；全量 3308 passed/0 failed/11 skipped/2 deselected）。**本提交**关闭 ISS-123 生产残余（索引/知识目录锚仓库根）。**本提交**关闭 BUG-CASH-WRAP（ISS-128）。此前提交关闭 BUG-TUI-MARKUP（ISS-127：分析面板字面 markup → `rich.Text`）。**本提交**关闭候选 **测试写报告目录隔离**（ISS-123 残余：`evidence._REPORT_DIR` conftest session 隔离+守卫；定测 `test_report_dir_isolation`；全量 3323 passed/0 failed/11 skipped/1 deselected（339.82s））。**本提交**关闭 **T04-D**（CLI pos plan --update 再抽：prepare_generate_or_update_plan + CLI 薄封装保留 Y/n）。下一张按候选：T05-G7 剩余等；不因本卡改道。合同与执行记录见 [iteration10/DELIVERY_PLAN.md](iteration10/DELIVERY_PLAN.md)。Claude Code 分工按 `CLAUDE.md`「Claude Code role routing」。下方 DSH 身份段仅适用于 Codex 那台机器。

更新2026-10-21。读AGENTS/CODEX→MILESTONES→STATUS→本文件。T05共享等待与远端4cc3bf6增量限定接收；最终隔离全量3157 passed/0 failed/7 skipped/1 external deselected（265.49s），494项指纹稳定，原8保护项/109未跟踪保留。独立四退出4通过，静态审查限定PASS；G7/T05整体、真实MID及G4/V1仍未完成。

## 历史交付摘要（当前批之前）

裁决`ACCEPTED_SHARED_TIMEOUT_AND_REMOTE_INTEGRATION_SCOPE_ONLY`。Git由Codex负责，默认直接main、无新命名分支/PR；最终交付SHA以实际main与origin/main日志核对，不沿用旧bd9e5d0。远端4cc3bf6的13文件增量已在临时detached树与本批补丁整合验收，最终全量和指纹见第九轮R15、REVIEW_INDEX及FULL/TARGETED原始日志。T05自身基线3119结果与最终整合分轮保留，E全量因F新发现由主控中止，不作产品失败或完整通过。

用户可见：start.py帮助显示超时等待/底层请求仍可能运行；缓存坏时间不复活、写失败保旧；扫描补算超时/缺必需指标不绕过条件，日期/期间窗口及返回证券同门核验；主题不把电力设备误作发电行业，fear旧TTL展示不强制联网。底层调用不能强杀，缓存/会话等晚到副作用未被本批证明消失；原剩余4个旧executor站点已由第十轮T05-D修复（ISS-120），晚到写缓存/Baostock会话重叠仍待办，G7/T05整体不关闭。

下一产品动作先按既有G/T路线选必要缺口，固定有限合同与停止条件；不追加泛计数探针、不改评分阈值或替代真实研究。v42当次C15现金观察及此前cv6/binding_v4/assertion_v2、shadow_v15/capture_only/default保持；历史行业/PIT、ROE、现金持续性UNKNOWN，真实MID NO_VALID，G4及V1未完成。

## DSH恢复身份

个人Skill local-settings沿用共享账本`C:/Users/Crux/.codex/codex-dsh-state`、连接native。正式task=`muyun-dev-native-20261020`；session=`codex-1de974ba-34f0-4de8-a092-6fdfa43e822c`；原生cwd=`G:\Tools\暮云思辨投资助手`；workspace=`c2168aee-0263-45c8-8dd3-2595db031930`，显示名称“暮云正式工作区｜Codex委派DSH实施”。新Codex聊天先resume/status，再核原生header/cwd、main/HEAD、实际diff与最近resultFile，未知状态不盲目重投。A-F为历史六请求，均COMPLETED；该阶段最后F=`fe5a34e9-6b35-4400-9f39-17b334a3281b`/seq1277；DSH已停写，Git归Codex。原生仍是完整DSH会话，不声称嵌套Agent。

历史D-F仅允许写OS Temp detached验收树，未迁移原生cwd；此临时树不再是后续产品默认写入口。正式Git交付后新合同回原工作区，先核main实际HEAD。旧task muyun-g7-readonly-20261020保留只读试用，muyun-dev-20261020是零prompt空会话，均不作继续入口。工具长期问题唯一在其独立仓docs/validation.md，本项目不迭代工具。

## 历史阶段保护与限制（当前保护见本页入口）

portfolio/备份、knowledge/index原有脏改与原109个未跟踪文件只保护，不恢复、不提交。项目venv、-B、显式UTF8；review_test_runner全量显式传tests，专用四child另行精确许可，不放宽普通守卫；不git add -A。原生模型实施获用户授权，无产品AI/真实账户迁移；费用未量化，不承诺节省比例或额度耗尽后常驻主控。

本次Codex运行时未提供read_thread/create_thread，引用聊天未读取、新Codex聊天未创建；工作依据已读磁盘交接。下次运行时若工具可用按用户指派处理，不能把本次缺失推广为产品永久限制。

## 历史置顶摘要（仅历史，不作为当前指派）

> **2026-10-22 T04-L 本提交关闭**：抽出 `_dispatch_backtest_cli`（backtest/batch_backtest）；定测 151；相关 503；全量 3494 passed/0 failed/11 skipped/1 deselected（323.53s）。**下一张**：T04 剩余主要为更厚项（scan-portfolio/chat/noai-debug）需判断，停；T05-G7 仍待产品裁决。
> **2026-10-22 T04-K 本提交关闭**：抽出 `_dispatch_fusion_cli`（shadow/plan2/research）；定测 147；相关 378；全量 3490 passed/0 failed/11 skipped/1 deselected（321.44s）。**下一张**：T04 其余薄内联（backtest/batch_backtest）可继续；scan-portfolio/chat/noai-debug 更厚则停；T05-G7 仍待（不抢道）。

> **2026-10-22 T04-J 本提交关闭**：抽出 `_dispatch_ops_cli`（doctor/diff_evidence/diff_list/batch_tasks/today）；定测 140；相关 290；全量 3483 passed/0 failed/11 skipped/1 deselected。**下一张**：T04 其余薄内联（shadow/plan2/research/backtest/scan/chat）若无产品判断则可继续，否则停；T05-G7 仍待（不抢道）。

> **2026-10-22 T04-I 本提交关闭**：抽出 _dispatch_observe_cli（expect/fear/watch_pool）；定测 135；相关 236；全量 3478 passed/0 failed/11 skipped/1 deselected（292.25s）。**下一张**：T04 其余 CLI 内联拆分（doctor/diff/today/…，不急）；T05-G7 仍待 Baostock 会话回流 / 真实 MID（需产品裁决，不抢道）。

> **2026-10-22 T04-H 本提交关闭**：抽出 `_dispatch_scan_cli`（scan_market/rules/industries/concepts/events/scan_review/scan_review_import）；定测 132；相关 172；全量 3475 passed/0 failed/11 skipped/1 deselected（380.07s）。**下一张**：T04 其余 CLI 内联拆分（不急）；T05-G7 仍待 Baostock 会话回流 / 真实 MID（需产品裁决，不抢道）。

> **2026-10-22 T04-G 本提交关闭**：抽出 `_dispatch_live_cli`（live/live_multi/live_all/live_scan_all）；定测 127；相关 167；全量 3470 passed/0 failed/11 skipped/1 deselected（476.46s）。**下一张**：T04 其余 scan 内联拆分（不急）；T05-G7 仍待 Baostock 会话回流 / 真实 MID（需产品裁决，不抢道）。

> **2026-10-22 T04-F 本提交关闭**：抽出 `_dispatch_benzong_cli`（benzong_*）；定测 121；全量 3464 passed/0 failed/11 skipped/1 deselected（484.94s）。**下一张**：T04 其余大分支拆分（live_multi/live_*/scan 内联，不急）；T05-G7 仍待 Baostock 会话回流 / 真实 MID（需产品裁决，不抢道）。

> **2026-10-22 T04-E 本提交关闭**：parse_input/run_cli 分发黄金锁 + `_dispatch_pos_cli` 抽出（定测 118；全量 3461 passed/0 failed/11 skipped/1 deselected（427.50s））。**下一张**：T04 其余大分支拆分（不急）；T05-G7 仍待 Baostock 会话重叠 / 真实 MID（需产品裁决，不抢道）。


> **2026-10-22 T05-E2 本提交关闭**：MarketCache ETF/行业/概念共享内存写亦受 `_live_cache_write_allowed` 抑制。**下一张**：T05-G7 仍待 Baostock 会话重叠 / 真实 MID（需产品裁决，不抢道）；T04 大函数拆分（不急）。

> **2026-10-22 T05-E 本提交关闭**：晚到缓存写抑制（`is_call_abandoned` + MarketCache/K线写跳过）。**下一张**：T05-G7 仍待 Baostock 会话重叠 / 真实 MID（均缺有限反例与产品裁决，不抢道）；T04 大函数拆分（不急）。不绑 G7/v43。
