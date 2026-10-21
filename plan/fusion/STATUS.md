**2026-10-22 T05-E（晚到缓存写抑制 / ISS-120 切片）本提交关闭**：`src/data/timeout_call.py` 增加 `is_call_abandoned`（超时置位 abandoned 线程栈；嵌套任一外层放弃即 True）；`MarketCache._disk_save`/`_accept_snapshot` 与 `AKShareClient._kline_disk_save` 在放弃后跳过写入。定测 `tests/core/test_t05e_late_cache_write.py` 8 项；相关回归 371 passed/8 skipped；全量 3341 passed/0 failed/11 skipped/1 deselected（490.04s）。不处理 Baostock 会话重叠 / 真实 MID，不宣称关闭 G7/T05 整体，不绑 v43。仍待：T05-G7 剩余（Baostock 会话/真实 MID 等，需产品裁决）（不抢道）。以下为既有第十轮状态。

**2026-10-22 T04-D（CLI pos plan --update 再抽）本提交关闭**：`src/core/trade_plan/plan_update_prep.py` `prepare_generate_or_update_plan`→`PlanGenUpdateOutcome`（NO_POSITION/DATA_UNAVAILABLE/GENERATE_FAILED/READY_NEW/READY_UPDATE；无打印、无 input、不写盘）+ `accept_plan_update`；CLI `_generate_or_update_plan` 薄封装保留 Y/n；内部复用 `prepare_trade_plan_draft`。定测 `tests/core/test_t04d_plan_update_prep.py`；相关回归（iss117 plan update / T04-C）全绿；全量 3333 passed/0 failed/11 skipped/1 deselected（309.47s）。不绑 G7/v43。仍待：T05-G7 剩余（晚到缓存/Baostock 会话重叠/真实 MID 等）（不抢道）。以下为既有第十轮状态。

# 融合项目当前实施与审批状态

**2026-10-21 测试写报告目录隔离（ISS-123 残余）本提交关闭**：`tests/conftest.py` session 隔离 `src.cli.evidence._REPORT_DIR`→临时目录 + 仓库`分析报告/analysis` 文件名集合守卫（同 BUG-IDX 手法）；定测 `tests/core/test_report_dir_isolation.py` 4 项（含未隔离红灯演示+清理）。不改生产路径语义。定测 4 passed；全量 3323 passed/0 failed/11 skipped/1 deselected（339.82s）。不绑 G7/v43。仍待：CLI pos plan --update 再抽 / T05-G7 剩余（不抢道）。以下为既有第十轮状态。

**2026-10-21 BUG-CASH-WRAP（ISS-128）本提交关闭**：`tests/core/test_cash_material_intake.py` 捕获 Console 补 `height=40` 使 `width=220` 生效（Rich `size` 需 width+height 同时给定，否则回落终端 80 列）；回执文案断言去换行匹配用户可读语义。不改生产折行。定测文件 45 passed；全量 3319 passed/0 failed/11 skipped/1 deselected（361.80s）。不绑 G7/v43。仍待：测试写报告目录隔离（`evidence.py` `_REPORT_DIR`）/CLI pos plan --update 再抽 / T05-G7 剩余（不抢道）。以下为既有第十轮状态。

**2026-10-21 BUG-TUI-MARKUP（ISS-127）本提交关闭**：`src/tui/app.py` 分析面板四处失败提示由字面 `[red]...[/red]` 改为 `rich.Text(..., style="red")`（与 T04-B 加仓路径一致；未开 RichLog markup）。修前定测 4 红 → 修后 4 绿；相关 TUI 12 passed；全量 3318 passed/1 failed（BUG-CASH-WRAP 折行）/11 skipped/1 deselected（341.62s）。不捆绑 BUG-CASH-WRAP。仍待：测试写报告目录隔离 / BUG-CASH-WRAP / CLI pos plan --update 再抽 / T05-G7 剩余（不抢道）。以下为既有第十轮状态。

**2026-10-21 第十轮**：Claude Code 主控，合同与执行记录见 [iteration10/DELIVERY_PLAN.md](iteration10/DELIVERY_PLAN.md)。T05-D（ISS-120 剩余 4 处 executor 退出界）与 BUG-OW（ISS-122 超配涨幅禁入被 rich 未配对标签绕过）已实施完成：T05-D 修复前 4 个新退出 case 全红、修复后 8/8 退出 case 通过，定向 46 passed/8 skipped；BUG-OW 修复后新文件 3 passed、overweight 相关共 42 passed；独立复核（code-quality-guard）结论可合入。全量3184 passed/0 failed/11 skipped/1 deselected（656.61s）；commit T05-D `ac6ce8a`、BUG-OW `c67b85e`。BUG-IDX（离线全量改写受保护 knowledge/index）测试侧修复 `28b7e68`；T04-A 持仓确认账本编排下沉 AccountService `c7fb92b`，全量 3210 passed/0 failed/11 skipped。T04-B TUI 加仓假成功修复与 chat 删仓去 CLI 桥 `03cab94`，全量3252 passed/0 failed/11 skipped/1 deselected（764.51s）。BUG-FEE pos confirm 入口拒负费用与非有限值 `9d13a3f`（ISS-125，旧版 --price inf 会入账），全量3259 passed/0 failed/11 skipped/1 deselected（626.36s）；BUG-FINITE 写侧拒非有限值与超大数量 `7de1e59`（ISS-126），全量3294 passed/0 failed/11 skipped/1 deselected（659.74s）；**本提交**关闭 Claude 写侧交付后留在 iteration10 队列的下一张 **BUG-FINITE-READ**（读侧重放隔离旧非有限事件，`test_bug_finite_ledger.py` 35 passed；相关账户161；全量3293 passed/1 failed（cash_material折行无关）/11 skipped，ISS-126 读侧尾巴关闭）——这是同一 ISS-126 读侧收尾，**不是**另开平行于 T04/T05 的新轨道。本轮 Claude 已接受且保持有效：T05-D `ac6ce8a`、BUG-OW `c67b85e`、BUG-IDX `28b7e68`、T04-A `c7fb92b`、T04-B `03cab94`、BUG-FEE `9d13a3f`、BUG-FINITE 写侧 `7de1e59`。**本提交**关闭 iteration10 队列下一张 **T04-C**（全量 3308 passed/0 failed/11 skipped/2 deselected，含已知 cash_material 折行 deselect；定测+相关回归全绿）（chat add/plan/overweight 去 CLI 桥：prepare_trade_plan_draft + `activate_overweight_checked；CLI 薄封装；chat 改调服务；定测 test_t04c_draft_prep / test_t04c_chat_bridge）。Claude 本轮已接受主线（T05-D/BUG-OW/BUG-IDX/T04-A/T04-B/BUG-FEE/BUG-FINITE 写侧+读侧）保持。**本提交**关闭候选 **ISS-123 生产残余**（相对索引/知识目录锚仓库根 + faiss Unicode 安全 I/O）。仍待：T05/G7 剩余（晚到缓存副作用、Baostock 会话重叠、真实 MID 等）、候选 BUG-TUI-MARKUP / 测试写报告目录隔离 / CLI pos plan --update 再抽 / BUG-CASH-WRAP 等。下一张推荐按候选队列价值排序（**不抢 T05/G7**）。以下为第九轮末状态。

T05共享等待与远端4cc3bf6增量限定接收；最终隔离全量3157 passed/0 failed/7 skipped/1 external deselected（265.49s），494项指纹稳定，原8保护项/109未跟踪保留。独立四退出4通过，静态审查限定PASS；G7/T05整体、真实MID及G4/V1仍未完成。

更新：2026-10-20。main整合限定通过，原v42当前行业条件化现金观察与v39-v41等历史范围保持。最终隔离全量3091 passed/0 failed/3 skipped/1 external deselected，2条既有测试转义警告（399.29s）；486项源码/测试/公开材料/隔离器指纹起止一致，6项保护hash与知识库集合不变。network 1011、subprocess 12、real_state 2次尝试均被守卫阻断。 当前范围及分轮证据见[R15最新段](iteration9/R15_ACCEPTANCE.md)与[第九轮末节](iteration9/DELIVERY_PLAN.md)，不以旧3049结果代替本轮。

v0.8.42唯一完整命令为`research <代码> --cash-verify <material_id> --source-check --industry-check [--json]`，单次PDF和SSE请求；普通research、getter和离线核对不后台联网。当前仅取得当次SSE转述C15非金融适用性，形成1个条件化组2期；支持映射边界仍限C15与J金融，其他未知。历史行业/PIT、ROE、现金持续性UNKNOWN；不形成长期TRUE/MID，不修改旧factor、评估/接受态、capture_only/default、缓存或阈值。G4整体及V1未完成。

v0.8.41历史范围保持：官方PDF当次字段核验、NP/CFO独立资格、精确Decimal展示及只读历史摘要。其数值和结论不替换为v42结果；公开时间等历史资格仍未知。

## 本轮关闭与下一步

产品原批已限定接收，未扩G7/v43。用户明确单人维护、默认直接main；本轮Codex已完成84fc530远端7项增量与759095e本地成果的整合验收，处理账户/今日视图冲突，收紧纯查询联网及pipeline不实股数建议。原生DSH正式任务仍为muyun-dev-native-20261020，后续恢复先核main当前HEAD。整合验证与交付结果见第九轮计划末节及REVIEW_INDEX，禁止以原批3049结果代替本轮。

显式快照整页同源，坏数量/重复证券/非有限现金不洗成空仓或零元，默认请求仍一次读取；chat精确集合防御回归已补。九处配置消费者迁到src.config，配置反依赖0，四处管理/观察池/代码规范化桥有意保留。数据结构重验不代表真实成交授权；管理投影豁免，G1/G2/G7整体仍按长期里程碑推进。

Grok交付核实：92提交、105文件中src零改，T07小改有用，T05大量基线不是生产优化。已合并18镜像测试，保留有用覆盖。缓存时点/半写保全、G4未来报告期门及v37归档观察展示已限定通过；T07来源保留/复算限定通过，整体未关闭。v42当前条件化观察限定接收；后续T05候选须等用户新指派，不在本批继续，不激活旧TRUE、不修改已接受研究或默认策略。

## ISS-117 原已验范围与局部重开

原S2a/S2b/S3已验收范围保持接收，既有S0/S1/S2预算与安全正例不回退。早前独立只读复核未发现其所审范围内当前可达确定P0/P1阻断；reviewer未运行测试。该审查不覆盖本次发现的行动说明与独立止盈抑制尾项，两项局部重开并归入既有P2，不重批已接收范围。原结论见[S2AC_ACCEPTANCE.md](signal_authority_review/S2AC_ACCEPTANCE.md)，本轮唯一实施入口仍为[第九轮交付计划](iteration9/DELIVERY_PLAN.md)。

修复前历史验证：35文件485 passed（138.73s），另三次探针分别为15场景7通过/8失败、3场景0/3、1场景0/1，共19个不同场景7通过/12失败，其中R15原7探针全通过；不是一次19项运行。该组旧验证中的guard网络尝试计数562/235/0/0均被隔离阻断；子进程及真实账户读取尝试为0；各轮7项保护哈希与知识库集合不变。以上不代表v0.8.31验证结果。

新增失败归三组，均为隔离验证，不外推为真实用户账户已发生：①账户与同源ctx：PARTIAL正比例仍被当作已确认.1；快照失败叠旧零投影仍归NONE；坏投影值42阻断100股真实测试账本事实进入state；失败ctx的version=None被影子再次读取成retry_success并得到mid_effective=True。②共享展示：chat归一后再次裸读产生重复证券；未知ctx权重显示为10%。③行动说明与末端动作：OPEN+UNKNOWN/BLOCKED仍显示“条件凑齐明天买”；低confidence、非action的止损/止盈建议被说成已触发；普通市场风险将research_status从COMPLETE改成INCOMPLETE；隔离案例中独立take_profit=.8使BUY降HOLD后，真实Strategy.process又转为ADD（.1→.3）。用户反馈l603992摘要在趋势偏弱、资格未知并带止损/止盈提醒时仍说“明天买”；该股实际止盈confidence与是否发生ADD均未确认。

ISS-117原已验范围保持接收，其余历史范围不重批；本次行动说明与独立止盈抑制尾项关闭。默认external resolver未装配且fail-closed，真实券商执行、生产resolver全链及投资效果未验证。K1/shadow_v14观察合同已冻结；M2有限生产l/la只读接线`ACCEPTED_READONLY`，不代表策略默认启用或投资效果通过；capture_only保持。权限预算协议iss117-s3-v1不变；笨总评分缓存与投票阈值未改，旧影子协议不追认。

## 独立的融合发布门槛

本轮工程验收不重复P/M2/T01历史接收。002957官方年报的金额格式已支持，完整段仍因范围绑定能力不足降摘录；真实公开research保存与同截止重启只读身份一致且MID未建立。真实样板还缺利润兑现机制和完整核验/反证条件，不能用相同收入重复计证。当前两候选窗口停止，不为追求VALID继续追加关键词。

工程冻结只约束已验cv6/binding_v4及v15记录消费；未来新增范围语法必须独立能力合同和版本隔离。T01登记尾项及T04配置小步已按最新v35接收；G4真实观察及T07便携补核按各自限定结果保留。v42已限定接收后暂停，协作已接通，下一产品批仍按第九轮现有T05候选与独立验收合同安排。真实观察、效果、真实MID各自留缺口，不能以模拟或代码通过代替。

## 工作区与保护

v0.8.31原批证据保持独立：全量1962 passed、2 skipped、1 external deselected、0 failed；定向131 passed、0 failed（含原19探针）。该批独立Sol/high复核PASS，范围与时长见本轮历史记录；这些数字不承包M2。v0.8.32 M2全量1997 passed、0 failed、2 skipped、1 external deselected（334.44s）；网络785、子进程2次尝试均被拒，真实状态读取0，7项保护hash及知识库集合不变。独立Sol/high核对7项当前源码/测试SHA一致，判定有限生产只读接线最终PASS、无本范围阻断。6文件68项定向结果单独保留、不与全量相加。原始记录见[全量结果](iteration9/FULL_TEST_RESULTS_P.txt)和[定向结果](iteration9/TARGETED_TEST_RESULTS_P.txt)的M2标头。未核实的portfolio.yaml及备份更新方不作归因；不得恢复旧账户文件。

v0.8.33 T01：4文件55 passed/0 failed（5.86s，新增16项），全量2013 passed/0 failed、2 skipped、1 external deselected（315.85s）；网络785、子进程2次尝试被拒，真实状态读取0，7项保护hash与知识库集合不变。独立Sol/high核对5项SHA与当前源码一致、55/2013日志匹配，有限today只读功能最终PASS，无本范围P1。目标/全量证据见iteration9/FULL_TEST_RESULTS_P.txt T01标头；TARGETED_TEST_RESULTS_P.txt相邻块旧标题误标M2，实际COMMAND和四文件列表为today/T01。未核实的portfolio.yaml及备份更新方不作归因；不得恢复旧账户文件。
