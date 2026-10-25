# 融合项目恢复入口

## 当前入口：2026-10-25 Codex原生Subagent

跨会话/跨工具进度接续统一执行[量化合同第7节](PROGRESS_CONTRACT.md#7-跨会话跨工具恢复与看板更新)：新对话先核共享账本，交付时由唯一写入者更新并生成看板；Claude Code/Grok Build/Copilot/Cursor入口已同步。本批仅完善持久规则，不改产品任务或进度分数。

**当前管理入口**：先看[量化账本](PROGRESS.md)和[计分合同](PROGRESS_CONTRACT.md)，JSON是唯一进度事实源。产品基线`f07b743`，本批只整理路线/量化/协作复盘，不改产品能力、不自加进度；历史成果已纳入基线。下一产品卡关联`V1-G7-SESSION`：先核全消费者合同，再做非阻塞会话协调。并行可推进`V1-G3-REAL-MID`及G4数据资格取证，真实费用仍按专项授权；G6真实观察先冻结协议，不假填日期。每批更新JSON并重新生成视图，交付说明前后进度和剩余，纯维护允许零增量。


2026-10-25 T05-F2限定接收：备用行情、历史K线、指数趋势的query创建与耗尽共用各自既有登录后30秒预算，保留供应商异常/socket重置、迭代异常、空/error与正常输出。正式9红→54绿，主控8文件守卫159 passed（9.12s），532项受测指纹、9保护hash和原218未跟踪存在性稳定。本批未跑全量；登录/心跳、整批预算与原生会话重叠仍待，G7/T05/V1未关闭。 本批生产与测试独立复核PASS，见[验收回执](iteration10/T05F2_ACCEPTANCE.json)和[合同](iteration10/DELIVERY_PLAN.md)。E3/F1已分别推送e28c674/ba0fc87；本批SHA核main实际Git日志。下一项是完整Baostock非阻塞会话租约合同，需覆盖全部消费者的登录/心跳/查询/迭代/关闭，不能单站点加锁或仅补更多计数探针。当前只用Codex原生Subagent，主控审批与Git；保留用户配置/账户/索引。

2026-10-25 T05-F1限定接收：主题代码核验的查询创建、错误/字段读取与结果耗尽共用既有登录后15秒预算，首包挂起可降级；正常名称与退市过滤保持。正式6红→15绿，主控五文件守卫204 passed（3.64s），531项受测指纹、9保护hash及原218未跟踪存在性稳定。未跑本批全量；登录/心跳、整批预算及原生共享会话仍未解决，G7/T05/V1未关闭。 生产与回归独立复核PASS，当前证据见[回执](iteration10/T05F1_ACCEPTANCE.json)和[第十轮合同](iteration10/DELIVERY_PLAN.md)。E3已推送`e28c674`；本批交付SHA以main实际Git历史核对。主控只在Codex+原生Subagent实施/Git，不调用DSH。下一项先将非阻塞会话租约的完整消费者范围落合同再实施；不加只覆盖单站点的伪全局锁。

用户改派只在Codex实施，主控规划/审批/Git，原生Subagent开发与复核；不调用DSH。本批基线main@ba0fc87；当前9项保护文件（含用户本地.codex/config.toml）与218个原有未跟踪文件保持，Git交付核实际main/origin/main。

2026-10-25 T05-E3增量限定接收：嵌套取数继承祖先放弃Event，迟到快照/K线写入受既有门约束。守卫全量3514 passed/2 failed/11 skipped/1 deselected（550.35s）；修正两项测试越界写入后，受影响两文件10 passed（3.77s）。其余528项受测源码/配置/全局fixture未变，最终530项指纹登记；8保护hash与原207未跟踪保留，八child专用8通过。没有最终树全绿全量声明；Baostock会话、写检查后竞态、真实MID及G7/T05/V1仍未关闭。 见[验收回执](iteration10/T05E3_ACCEPTANCE.json)与[合同](iteration10/DELIVERY_PLAN.md)。产品、runner与最后两项测试均独立复核PASS；主控负责本提交和普通推送，交付SHA核main实际历史。下一批先限定Baostock查询与迭代会话边界合同；不追加无收益T04机械拆分。

下方Claude/DSH身份与旧“下一张”保留为历史。恢复先读本段与实际Git；3494项全量是2026-10-22 T04-L历史证据，不是T05-E3结果。

> **第十轮（2026-10-21 起，Claude Code 主控）**：用户指派 Claude Code 接手迭代并兼任架构师，方案落盘即推送。T05-D（ISS-120 剩余 4 个旧并行 executor 的退出界）与 BUG-OW（ISS-122 超配涨幅禁入被 rich 未配对标签绕过）已实施完成并过独立复核，全量3184 passed/0 failed/11 skipped/1 deselected（656.61s），commit T05-D `ac6ce8a`、BUG-OW `c67b85e`；BUG-IDX 测试侧修复 `28b7e68`；T04-A 账本编排下沉 `c7fb92b`（全量 3210 passed/0 failed/11 skipped）。T04-B（TUI 加仓假成功修复 + chat 删仓去 CLI 桥）`03cab94`，全量3252 passed/0 failed/11 skipped/1 deselected（764.51s）。BUG-FEE `9d13a3f`（ISS-125），全量3259 passed/0 failed/11 skipped/1 deselected（626.36s）。BUG-FINITE 写侧 `7de1e59`（ISS-126），全量3294 passed/0 failed/11 skipped/1 deselected（659.74s）。**本提交**合入 BUG-FINITE-READ（ISS-126 读侧：重放 `_isolate` 非有限事件 → PARTIAL；nan 成本可读可再写；today/NAV 未知语义；定向35+相关161；全量3293 passed/1 failed（cash_material折行无关）/11 skipped）——系 Claude 写侧后明确留下的下一张队列卡，非平行新轨。Claude 本轮已接受主线（T05-D/BUG-OW/BUG-IDX/T04-A/T04-B/BUG-FEE/BUG-FINITE 写侧）不动。**本提交**合入 **T04-C**（草稿编排下沉 + chat add/plan/overweight 去 CLI 桥；全量 3308 passed/0 failed/11 skipped/2 deselected）。**本提交**关闭 ISS-123 生产残余（索引/知识目录锚仓库根）。**本提交**关闭 BUG-CASH-WRAP（ISS-128）。此前提交关闭 BUG-TUI-MARKUP（ISS-127：分析面板字面 markup → `rich.Text`）。**本提交**关闭候选 **测试写报告目录隔离**（ISS-123 残余：`evidence._REPORT_DIR` conftest session 隔离+守卫；定测 `test_report_dir_isolation`；全量 3323 passed/0 failed/11 skipped/1 deselected（339.82s））。**本提交**关闭 **T04-D**（CLI pos plan --update 再抽：prepare_generate_or_update_plan + CLI 薄封装保留 Y/n）。下一张按候选：T05-G7 剩余等；不因本卡改道。合同与执行记录见 [iteration10/DELIVERY_PLAN.md](iteration10/DELIVERY_PLAN.md)。Claude Code 分工按 `CLAUDE.md`「Claude Code role routing」。下方 DSH 身份段仅适用于 Codex 那台机器。

更新2026-10-21。读AGENTS/CODEX→MILESTONES→STATUS→本文件。T05共享等待与远端4cc3bf6增量限定接收；最终隔离全量3157 passed/0 failed/7 skipped/1 external deselected（265.49s），494项指纹稳定，原8保护项/109未跟踪保留。独立四退出4通过，静态审查限定PASS；G7/T05整体、真实MID及G4/V1仍未完成。

## 当前交付与下一步

裁决`ACCEPTED_SHARED_TIMEOUT_AND_REMOTE_INTEGRATION_SCOPE_ONLY`。Git由Codex负责，默认直接main、无新命名分支/PR；最终交付SHA以实际main与origin/main日志核对，不沿用旧bd9e5d0。远端4cc3bf6的13文件增量已在临时detached树与本批补丁整合验收，最终全量和指纹见第九轮R15、REVIEW_INDEX及FULL/TARGETED原始日志。T05自身基线3119结果与最终整合分轮保留，E全量因F新发现由主控中止，不作产品失败或完整通过。

用户可见：start.py帮助显示超时等待/底层请求仍可能运行；缓存坏时间不复活、写失败保旧；扫描补算超时/缺必需指标不绕过条件，日期/期间窗口及返回证券同门核验；主题不把电力设备误作发电行业，fear旧TTL展示不强制联网。底层调用不能强杀，缓存/会话等晚到副作用未被本批证明消失；原剩余4个旧executor站点已由第十轮T05-D修复（ISS-120），晚到写缓存/Baostock会话重叠仍待办，G7/T05整体不关闭。

下一产品动作先按既有G/T路线选必要缺口，固定有限合同与停止条件；不追加泛计数探针、不改评分阈值或替代真实研究。v42当次C15现金观察及此前cv6/binding_v4/assertion_v2、shadow_v15/capture_only/default保持；历史行业/PIT、ROE、现金持续性UNKNOWN，真实MID NO_VALID，G4及V1未完成。

## DSH恢复身份

个人Skill local-settings沿用共享账本`C:/Users/Crux/.codex/codex-dsh-state`、连接native。正式task=`muyun-dev-native-20261020`；session=`codex-1de974ba-34f0-4de8-a092-6fdfa43e822c`；原生cwd=`G:\Tools\暮云思辨投资助手`；workspace=`c2168aee-0263-45c8-8dd3-2595db031930`，显示名称“暮云正式工作区｜Codex委派DSH实施”。新Codex聊天先resume/status，再核原生header/cwd、main/HEAD、实际diff与最近resultFile，未知状态不盲目重投。A-F六请求均COMPLETED，最后F=`fe5a34e9-6b35-4400-9f39-17b334a3281b`/seq1277；DSH已停写，Git归Codex。原生仍是完整DSH会话，不声称嵌套Agent。

本轮D-F仅允许写OS Temp detached验收树，未迁移原生cwd；此临时树不再是后续产品默认写入口。正式Git交付后新合同回原工作区，先核main实际HEAD。旧task muyun-g7-readonly-20261020保留只读试用，muyun-dev-20261020是零prompt空会话，均不作继续入口。工具长期问题唯一在其独立仓docs/validation.md，本项目不迭代工具。

## 保护与限制

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
