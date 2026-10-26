# 暮云思辨投资助手 — AI Agent 开发手册

当前交接（2026-10-27收尾）：用户发出新额度不足8%信号，已停止派发并中止T05-L追修。T05-K已交付3f08bf5；L未验收、产品补丁仍在工作树并已快照，禁止当作完成/提交。DSH已锁且观测空闲，工具仍STOP_PENDING；新授权前不继续，恢复详见[RESUME](plan/fusion/RESUME.md)。

> 任何 AI 编码工具打开此项目时，请先阅读本文件。
> 详细文档索引见末尾。

> 长期目标、持续里程碑与完成标准见根 [MILESTONES.md](MILESTONES.md)，当前融合交付验收见 [plan/fusion/STATUS.md](plan/fusion/STATUS.md)。批次测试和历史反例关闭仅对当批范围有效，不代替最新独立验收；交付/审批后同步里程碑、状态与恢复指针。

### 量化任务与每批交付

V1任务/权重/状态唯一登记在`plan/fusion/PROGRESS.json`，读`plan/fusion/PROGRESS.md`或`docs/开发进度看板.html`查看生成视图；计分规则见`plan/fusion/PROGRESS_CONTRACT.md`。实质开发先关联稳定任务ID，交付报告说明行为、证据、工程/验收/合同指数前后与剩余/阻断；维护任务允许进度不变。改账本后运行`python -B -X utf8 scripts/check_progress.py --render`及`--check`；不手改生成文件、不用测试数或commit数涨分。范围/权重/适用性变更须留版本与前后影响，未知证据不当作未实施，百分比不能绕过必需验收门。

**跨会话、跨工具必须执行**：在同一项目打开任何新对话，先读`plan/fusion/PROGRESS_CONTRACT.md`第7节、`plan/fusion/RESUME.md`，核Git/工作树并运行项目解释器的`check_progress.py --check`，再读取当前任务段；不假定聊天历史或某工具私有memory已共享。批次状态/证据变化后由唯一账本写入者更新JSON及变更记录、执行`--render`与`--check`后再交付；其余Agent只提交任务ID/证据增量，避免相互覆盖。关闭对话前同步恢复指针；无证据或纯维护允许分数不变。规则与账本随Git持久化，不代表浏览器后台跟踪代码或模型绝对服从规则。

> **多 Agent 共用规则**：本文件只定义项目共同规范，不给阅读者自动分配架构师或实施者身份。角色以用户对当前会话的指派为准；Codex 专属约定见 `CODEX.md`，Claude Code 专属约定见 `CLAUDE.md`。共享交接文档中的作者职责不得覆盖当前 Agent 已获指派的任务。

<!-- harness-project-init: model-routing -->
### 主动按需分工

用户明确要求主动使用subagent：每项实质任务开始时先判断能否拆出有价值的独立定位、文档整理、实施或复核；能减少主上下文或提供必要独立验证时，实际派发最少所需角色，不等用户再次提醒。简短、依赖紧密或低影响的单文件工作直接完成，不凑Agent数量。临时“不用agent”只适用于该项任务，新的指派优先。

型号与执行证据见[docs/agent-routing.md](docs/agent-routing.md)。定位用Luna/high、整理用Luna/medium；开发/普通审查用Sol/medium，关键数据边界用Sol/high；仅复杂设计用Astra/low。主模型不改。型号覆盖必须用小范围fresh context；当前工具要求时设 `fork_turns="none"`。角色不可发现时用支持的default/worker显式型号，不静默继承主模型；不可用立即说明。

派发前简述分工与型号，写任务须明确文件所有权并保留他人成果；不递归委派、不重复审查、不轮询无变化进度。实质里程碑先确定合同和验收条件，再按依赖实施与独立复核；主控可承担计划，不固定要求三或四个Agent。实际型号以运行元数据核实，无法读取时标未核实。聊天指令 `按需分工：<任务>` 明确触发此规则；配置存在不能当作任务已派出或节省已验证。 基线/探针任务必须限定矩阵和停止条件，完成即转实际缺陷或交付；不能因依赖受阻无限追加只读计数测试。现行为不等于正确合同，发现缺陷先红后修，生产改善须有对应代码和用户可见结果。
<!-- /harness-project-init: model-routing -->

---

## 一、怎么跑

Python 3.11+；日常运行 `uv run python start.py`，CLI单股分析 `python -m src.cli.main -l 600519`。命令输出、工作流与报告说明见 [使用手册](使用手册.md)，速查：

```
l 600519,000001                  # 多代码深分析；中英文逗号/空格分隔
bz 600519 [--refresh]            # 6维AI评分；--refresh跳过缓存
bz 600519,000001                 # 多代码评分排名，缓存优先
bz scan 氮化镓,钽电容            # 主题定位+评分
bz scan shrink_pullback 科技     # 规则+主题，规则名可模糊匹配
bz --check / bz --manual         # 数据源体检 / 单股手动评分兜底
expect [天数]                   # 默认未来30天事件/预期透支；events为事后事件
fear / fear history / fear backfill  # 客观恐慌指数 / 历史图 / 断点回填
scan review [天数]              # 默认7天扫描收益、沪深300同窗超额/走势
scan review import              # 导入旧扫描报告，同(时间,来源)幂等
watch / watch add <代码|#N> / watch rm <代码>
la / ba / l all [-f]             # 所有持仓深分析 / 上次扫描评分 / 上次扫描深分析
ba --budget 60                  # 可选时限；到点不开始下一股，当前股完成后返回
doctor / 诊断                  # 只读环境体检，零AI零网络
diff <代码> / 对比             # 同股最近两次证据变化
tasks / 任务                   # 批量进度、失败与续跑
```

- **费用/数据纪律**：bz每维1次AI；fear、scan review零AI。l all默认跳过当天已析股，`-f`才强制重析，失败股不记成功账、下次重试；同类批量重跑复用当日成功缓存。fear缺失标MISSING并剔除权重，分位样本不足降固定阈值且标注，不补假数据；backfill有供应商约20–30交易日历史限制。
- **扫描与观察池**：save_last_scan统一记录scan market/bz scan/chat，`~/.muyun/scan_history.jsonl`全量追加不prune，展示窗口由查询控制；当天扫描不进统计，窗口无可评估而有更早历史时扩大并明说。行情预算为一轮批量（60只/请求）、逐票日线各1次全窗口、沪深300日线1次，当日kline_cache复用。无持仓且l/la/l all/chat判WATCH自动入池；手工add缺省实时价、已有不重复，rm是唯一出池通道，再次WATCH入池另立锚点；`~/.muyun/watchlist.jsonl`按add/remove重放，入池失败不得承诺成功。
- **证据/任务存储**：l/la/l all/chat追加 `~/.muyun/analysis_evidence.jsonl` 不prune，证据卡在 `分析报告/analysis/` 同分钟防覆盖；旧缺字段为“当时未记录”，笨总评分版本用CACHE_VERSION、决策评分按聚合协议比较。批量账本 `~/.muyun/batch_tasks.json` 原子写、保留最近5个任务。扫描/观察报告在 `分析报告/scan/`，fear图在 `~/.muyun/fear_index/charts/`；详细展示见使用手册。
- **诊断隐私**：doctor只查解释器/依赖/配置存在性/状态缓存/RAG/报告目录，缺失如实标注；settings.local.yaml只报存在性，API key与内容绝不回显。

依赖事实源为钉版本的 `requirements.txt`，不要盲目升级，升级须独立验证：

```
uv venv .venv
uv pip install -r requirements.txt
# 或标准venv
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

仓库没有pyproject.toml，`uv sync`不可用。

---
## 二、这是什么项目

AI 驱动的 A 股交易策略系统，**非实盘交易**，定位是研究/回测 + 决策辅助。

七层架构：Signal → Decision → Event → AI Modifier → EntryExit → Strategy → **PlanGuard** → Execution

> 注：EntryExit 在 AI Modifier 与 Strategy 之间作为「最高优先级覆盖步」实现（`src/core/orchestrator.py:255-301`），不是独立层级。
>
> v0.8.5 新增 **PlanGuard**（`src/core/plan_guard.py`）：在 Strategy 之后、Execution 之前，根据 `TradePlan` 决定是否压制 weak_sell。详见 `docs/TradePlan_使用指南.md`。
>
> v0.8.6 新增 **笨总评分体系**（`src/core/benzong/`，注意拼音平舌 z）：基于 B 站 up 主「笨笨的韭菜」超景气价值投机教学，6 维 AI 自动评分。`bz <code>` 输入代码直接出结果；`bz scan <主题>` 双通道精准定位细分赛道标的。详见 `docs/笨总6维评分_说明文档.md`。
>
> v0.8.6.4「跳法A」**笨总升为中长期决策主驾**：建仓时笨总评分定气宗/剑宗 mode（笨总首次真正参与买卖决策，此前仅展示），技术层降级为持仓内择时。详见 `ISSUES.md` ISS-046。

核心能力：全市场扫描、深度分析（含 Weinstein 阶段）、买卖点精确触发、回测框架、RAG 策略知识检索、**TradePlan 持久化交易计划**（v0.8.5）、**笨总 6 维 AI 自动评分 + 主题精准选股**（v0.8.6）、**气宗持有模式 + 事件四要素**（v0.8.6.3）、**笨总视频理念优化：板块层信号+三倍定律+超配+市场宽度+产业线索**（v0.8.6.8）。

当前产品基线为 **v0.8.42 当次行业条件化现金观察限定接收**，共享会话生命周期按第十轮限定接收；真实 MID 仍 NO_VALID，G3/G4/G7/T04 及 V1 整体未完成。cv6/binding_v4/assertion_v2、v15、capture_only/default、笨总缓存和阈值保持。T01 仅登记业务读取范围通过，管理投影视图豁免。当前进度、待办与证据分别见 [MILESTONES](MILESTONES.md)、[RESUME](plan/fusion/RESUME.md) 和 [第九轮计划](plan/fusion/iteration9/DELIVERY_PLAN.md)；历史测试数字不作为当前验收。

版本变更见 [README 版本历史](README.md#版本历史)；较早叙事保存在现有 [截至v0.8.14归档](docs/archive/AGENT版本叙事归档_截至v0.8.14_2026-09.md) 与 [v0.8.15–v0.8.19归档](docs/archive/AGENT版本叙事归档_v0.8.15-v0.8.19_2026-09.md)。归档与历史实施账本只作历史证据，不作为当前执行或通过保证。

### 融合与信号权限的持续约束

以下保留跨版本安全边界；具体实现是否过门，以独立审批为准。

- **事实/评估唯一真值**：归档覆盖与可比/事实资格分开，留样及采集时点不自证核验或历史可得；自由文本最高 EXCERPT_GROUNDED；类型化数值/否定事实须谓词一致、词元边界与来源公开时点齐备才 FACT_CHECKED。VALID 必须有实存可解析引用，无 evidence_resolver 不得判 VALID；AssessmentStore 按 assessment_id 提供草稿/影子同一状态，缺失/错配待复核，不以旧 facts+refs 重判。REFUTES/否定主张不得支撑正面结论，单主张单必需命题；checkpoint走合法建立渠道，因子须绑定合格快照，禁止裸 raw dict 旁路；财务映射须单位幂等及已核定证券×已观测报告期范围门，保留原件核对记录。latest-only财务不得入历史严格PIT快照；资格安全门不随策略回滚撤销。
- **账户事实与建议分离**：分析只记录观察量和待确认建议，实际成交经 `pos confirm` 才改持仓；PARTIAL账本或不完整持仓枚举不能证明空仓，UNKNOWN/None与缺口原因须贯穿投影、共享清单、chat及l/la。坏的非计划metadata逐项隔离后，同请求独立核定的 `ctx.trade_plan` 仍须传入l/la/chat/shadow；计划本身为None不授予退出资格。旧shadow按版本独立分桶，不追认当前分母。多端范围以ISSUES的G2/G7/T01为准。
- **检测与硬授权分离**：SignalFinding缺字段默认最严；硬动作消费端核signal_id白名单、策略绑定精确相等、证券、时点、质量，缓存重放走同门，PlanGuard P1同时核已授权标记与同源结构化退出发现。复算须有行情主体/时点，主体证券独立核对（代码后缀规范化），结果质量OK且signal_id/证券一致；不得用请求股回填自证。旧字符串exit_signal缓存不得复活；holder/margin缓存各按signal_id核验。非法价格不得判穿止损；子信号/层异常显式隔离，不吞独立保护；未知技能条件fail-closed。
- **弱证据不得清仓**：减持标题的否定/澄清/未实施、宏观10万亿、未经核定旗手关系均为research；缩量加速缺连续证据为UNKNOWN。板块30+仅研究提醒，旧标签同样受限，禁止把行业成熟换算30+；标注须允许不适用/证据不足，不新增冷静期或行业例外。AI invalidate仅红线候选，不硬invalidate/强制F；规则硬排除/ST纪律保留。AI black_swan不强制PANIC，软调节按置信度缩放，零置信度无调节；风险红线不做跨字段子串匹配。
- **事件与软限仓**：scope缺省/非法为unknown，先拒unknown再核证券；stock须显式affected_codes含当前股，不以请求上下文重写事件主体。四要素缺失为UNKNOWN，confidence取真实性而非impact；无独立核验器不force_state/硬限仓，软限仓仅空头方向。增量cap为 `max(0,min(目标,上限)-当前)`，零增量转HOLD/HOLD_POSITION，REDUCE不参与，未知当前不伪造增量。
- **数据与时点资格**：日期完整严格解析，不截断/回退洗白；证据不得晚于分析数据时点（live行情快照、回测bar），三倍价格须有限正值、quote_as_of；不恢复已撤销的live统一7天证据政策。预告缺失/非法/未来公开日期跳过，同日时序不明标注。sector行须在请求窗口、未来剔除、同日冲突放弃、qfq同口径、至少21收盘观测并核跨度，末观测距请求端点>12天拒绝；股东户数按统计日期倒序去重，显式本次/上次户数映射，无统计日期fail-closed、无公开时点UNKNOWN；融资as_of用实际两端日期，备用Baostock透传adjust。
- **日历硬授权只读且拒绝不完整覆盖**：`trading_days(allow_refresh=False)`不得登录/建目录/刷新；缓存须版本正确、built_at在7天TTL内，覆盖日期与声明区间自然日全集精确相等、唯一、标志合法、查询正常结束。strict在原始JSON解析拦任意层级重复键；门内交易日限定核定区间，分析日须在覆盖内，行情快照不得早于独立最近交易日。缺失/过期/损坏/旧schema/覆盖不足一律UNKNOWN，不猜星期、12天范围或借未来日期补证明。旧缓存可继续服务fear展示，TTL内不重建，不承诺跑一次fear立即恢复或要求手工删缓存；三倍纪律降提醒，价格止损/ST/期限保护保留。
- **冻结投票合同**：启用base集合E由目标名单/目录预先确定，加载/执行失败仍占W；坏权重显式拒绝/降级，缺E不从成功票缩分母，普通票不产方向。槽=(skill_id,证券,evaluation_as_of,rule_version)，版本为YAML内容sha1；注册时点/版本不得由票自报补齐，缺失/错股/错时点/错版本/冲突槽UNKNOWN保留W、不投不计覆盖、不入组，同槽同票合一。regulator同注册同校验，重复不能放大；confidence=0计覆盖但贡献0，非法confidence拒绝不clamp。A=clip(M·U/W)、P仅展示、C独立；调节幅度 `1+c(m_legacy−1)`，零/坏置信度不贡献不稀释，不创造U=0方向。
- **动作出口与记录合同**：仓位/强卖/强止损用具名A，不抓中文trace名；独立override触发即携带，同向不清，_gate_score取独立与普通通道较大值，AI/事件加分不得绕基础资格。BUY/HOLD→ADD须过当前状态buy_gate，目标≤已知当前转HOLD_POSITION/终态HOLD；无roster降级不妨碍独立止损/止盈/PlanGuard。协议/A/P/C与独立action依据按冻结合同贯穿记录；旧缺字段标当时未记录，不补假0。score跨协议不算数值Δ（标双方协议），价格Δ照常；shadow旧v12及更早版本不进v13当前分母。decision score语义以协议分桶，笨总输入不变不改CACHE_VERSION、不迁磁盘缓存；展示动作强度/覆盖率不得称胜率。

融合命令速查：`today` 今日工作台（待确认建议/继续持有/等待条件）；`shadow` 影子对照（`fusion.shadow_capture`，记录 `~/.muyun/shadow_diff.jsonl`）；`plan2 <代码> mid|long <意图> --facts 事实` 与 `plan2 accept <代码>`（每股每周期计划存 `~/.muyun/horizon_plans.json`，事实不自动使逻辑VALID）；`diff` 无参数列清单，`diff <代码> --ai` 为可选AI解读。

---
## 二·五、最高优先级开发原则：用户可感知（2026-06-18 立项）

> **任何代码改动必须让用户在跑 `start.py` 时实测得到差异。** 看不见的改动 = 没做。

判定一次改动是否合格的硬条件（满足任一即可）：

1. `start.py` banner / 帮助文本 / 命令输出有可见变化（版本号、新提示行、新字段）
2. `-l <code>` 单股分析输出多/少了具体内容（如新增"当前参数档：牛市档"行）
3. `-b <code>` 回测输出的指标值有变化（收益、回撤、交易次数等）
4. `scan` 排名表 / 触发详情段有可见差异
5. 命令本身新增 / 删除（如新加 `pos diff` 命令）

不合格的改动模式（往往看着像在干活但用户感知不到）：

- ❌ 只改了文档 / 注释 / commit message — 那是 docs 不是功能
- ❌ 只调内部参数但 CLI 输出没变化 — 必须把"现在用的什么参数"露出来给用户看
- ❌ 修了 bug 但没加诊断行让用户知道"以前会怎么坏 / 现在好了"
- ❌ 重构了内部模块但行为完全等价 — 这种改动单独一个 commit、commit message 注明"内部重构无行为变化"

每次 commit 之前自问：**用户跑一次 start.py 看得到这次改了什么吗？看不到就说明改动没价值或者输出层没补完。**

## 三、项目结构速查

```
src/core/           # 核心引擎（orchestrator, skill_engine, decision_engine, strategy_layer, plan_guard, event_layer）
src/core/benzong/   # 笨总评分体系（scorer/auto_scorer/batch_scorer/theme_locator/rule_scorer/dimensions）
src/core/entry_exit/# 买卖点模块（calculator, entry_rules, exit_rules, price_level）
src/core/trade_plan/# TradePlan 子系统（generator, adjuster）
src/scanner/        # 全市场扫描（market_cache, scanner_engine, scan_rules.yaml）
src/data/           # 数据层（models, data_feeder, akshare_client, portfolio, news_client, source_check）
src/rag/            # RAG 向量检索（FAISS + sentence-transformers + jieba）
src/chat/           # AI 对话模式（agent, tools, formatter, prompts）
src/cli/main.py     # CLI 入口 + 所有命令实现
start.py            # 交互式 REPL 入口
configs/            # 配置文件（settings.yaml, industry_chains*.yaml 产业链图谱）
tests/              # 测试（2026-08-17 按用途目录化，见下方 tests/ 目录说明）
docs/               # 详细文档
投资策略（持续更新）/ # 65+ 策略 txt/md → RAG 文档块（含笨总教学 .md）
```

---
### tests/ 目录说明（2026-08-17 整理）

| 目录 | 用途 | 典型脚本 |
|------|------|---------|
| `tests/chat/` | chat 模块单测（纯 mock，无网络） | test_chat_streaming / tool_failure / reply_finalize / shutdown / history_trim / formatter |
| `tests/benzong/` | 笨总评分体系（mock 为主） | test_benzong_scorer / auto_scorer / batch_scorer / mode_from_grade / video_report_tier1-3 / issue_033 气宗回测验证 |
| `tests/backtest/` | 回测与策略层 | test_jumpA_backtest_5year（5年回测载体）/ strategy_layer_sell_split / issue_041 规则版AI版相关性 |
| `tests/core/` | 核心引擎单测（技能/决策/持仓/TradePlan/信号/模型规格） | test_conditions / trade_plan / fundamental_alert / top_signal / test_ai_model_family（模型系判定+上下文窗口+token 估算）等；test_tech_context_e2e 为脚本式E2E（pytest 默认 skip，直跑） |
| `tests/rag/` | RAG 摄取/检索/隔离/重排（mock 为主） | test_ingestion_chunking / rag_diversity / rag_series_isolation / reranker / index_freshness |
| `tests/data_sources/` | 打第三方接口的脚本（真实网络） | test_all_api（全数据源连通性体检）/ test_source_check / test_industry_data / probe_iss053（数据可得性探针） |
| `tests/ui/` | web/tui 集成测试（mock 底层引擎，不接真引擎） | test_web（flask test client：threading 后台 + htmx 轮询）/ test_tui（textual pilot：@work worker 异步填表）。**2026-09-11 修正：此前误记为「已不存在」，实为 2 文件 16 用例、且自 `705df2c` 起一直在 HEAD 中**（`git ls-tree -r HEAD -- tests/ui` 可证） |
| `tests/artifacts/` | 历史回测/issue 验证的输出产物（json/log，非脚本） | extended_backtest_2024* / issue_033_round* |
| `tests/rag_eval/` | RAG 检索质量评估工具 | evaluator.py + relevance_labels |

跑法：`pytest -q` 离线全量（M1 起 pytest.ini 固定收集根/排除与 HOME 隔离，统计口径固化 L05：每次全量的 passed/skipped 数字写进 commit message，历史对比以此为准）；单文件 `pytest tests/core/test_scan_review.py -q` 或脚本直跑 `.\.venv\Scripts\python.exe tests\core\test_xxx.py`（脚本自带 sys.path 修复）；真实外源/AI 显式启用方式见 tests/README.md；网络类脚本先看文件头注释。

---


## 四、技术栈与数据源

| 层 | 技术 |
|----|------|
| 语言 | Python 3.11+ / uv |
| 模型 | Pydantic / PyYAML / Rich / NumPy |
| AI | OpenAI SDK（兼容 DeepSeek/Kimi） |
| 主数据 | Baostock（K线+财务+大盘指数） |
| 扫描数据 | 新浪财经 API（快，无量比/60日涨跌幅）→ eFinance 备用（有量比） |
| 板块数据 | AKShare THS 接口（行业/概念成分股） |
| RAG | FAISS + BAAI/bge-small-zh-v1.5 + jieba |
| 回测统计 | NumPy |

---

## 五、关键约束与踩坑警示

### 代码编写

- **不要用中文全角标点在 Python 字符串里**：`（ ）` 等全角括号在 Set-Content 写入的 .py 文件中会导致 SyntaxError
- **修改 YAML 键名时，追踪所有硬编码引用**（ISS-078 复核更新）：scan_rules.yaml 的 `default` 键 v0.8.4 已删；现行默认规则是 `healthy_pullback`，main.py/start.py/scanner_engine.py 共 14 处硬编码 + chat/tui/web 各 1~2 处——改默认规则/规则名前先 grep `healthy_pullback` 全库同步
- **RAGDocument 字段名是 `content`，不是 `text`**
- **StrategyDecision.entry_exit 是 dict，不是 EntryExitResult 对象**：通过 `ee.get("key")` 访问
- **StockData 的 MA 字段通过 `dr.stock` 访问**：`_weinstein_stage()` 需要 StockData，不是 StrategyDecision
- **回测路径必须显式从 `settings.yaml` 读取并传 `entry_exit_config` 给 `BacktestEngine`**：CLI 默认参数不会兜底，缺传会让买卖点**整体失效**（ISS-032）。金字塔仓位已按 2026-07-17 审查拍板移除（commit 9b89ecd），2026-09-05 重构会话进一步拆除兼容壳：`configs/position_tiers.yaml`、`load_pyramid_config()`、全链 `pyramid_config` 参数均已删除——别再引用 position_tiers
- **调策略卖出参数前先打 `trade['sell_path']`**：回测中实际卖出走的是 `src/core/strategy_layer.py` 的 `_infer_sell_path`（`take_profit_trim` / `trend_exit` / `weak_sell` / `stop_loss_*`），不是 `src/core/entry_exit/exit_rules.py`。两者各管一半，不能互相替代（详见 `.learnings/LEARNINGS.md` LRN-20260618-003）
- **execution_layer 只读 `position_action`（不读 decision/sell_path，无 STOP 概念）**（审查 P0/P1a 修复）：PlanGuard 不可压规则（4 致命止损/4.5 fundamental_alert/P1 top_signal/3 时间止损）+ EntryExit force_exit 救回**必须设 `position_action=CLOSE_ALL`**，否则 strategy_layer 输出 HOLD 时安全网静默失效。规则3/4 原只设 `decision=SELL` 致穿止损/到期不卖（已修，设 CLOSE_ALL+sell_path）；Chandelier force_exit 被 strategy_layer 5机制降级为 HOLD 也丢失（P1a：orchestrator 在 strategy_layer 后重新断言 SELL+CLOSE_ALL+trend_exit 覆盖降级）。**新增 PlanGuard 规则/force_exit 救回别忘设 position_action**
- **`StockData.recent_announcements` 只在 live builder 填充，回测 builder 绝不可填**（ISS-052）：该字段供 top_signal 实控人减持子信号用，由 `calculate_indicators`（live，`get_stock_data` 别名）填充；回测走独立的 `DataFeeder._build_stock_data`（不调 calculate_indicators）保持 None -> 减持子信号回测跳过（公告是 akshare"最近N天"接口，非 point-in-time，回测取会拿未来公告前瞻）。**别为"完整性"给 DataFeeder 也填公告**--会注入未来信息污染回测。回测安全靠两套 builder 隔离，无 `is_backtest` 开关；若要回测验证减持信号须先备 point-in-time 历史公告数据源
- **回测调 `orchestrator.analyze` 必须传 `is_backtest=True`**（ISS-053 审查修复）：`fundamental_alert`（被ST/业绩预告预亏）走 baostock **当前**数据，非 bar 时点 point-in-time--回测里未来预亏预告会在第一根持仓 bar 即触发退出（前瞻偏差）。`is_backtest=True` 守卫跳过整个 fundamental_alert（live 不变，仍启用）。**新增回测调用点别忘了传**，否则回测被未来数据污染。回测路径只有 `backtest_engine.py:576`（已传）；`backtest_validator` has_position 默认 False 不触发
- **笨总评分体系的缓存键是 `(stock_code, date, dimension)`**：同股同日同维度只算一次 AI（`src/core/benzong/cache.py`，存 `~/.muyun/benzong_cache/`）。**改了任何维度评分逻辑（prompt/降级/公式）必须 bump `CACHE_VERSION`**（cache.py 顶部），否则旧缓存命中导致"改代码不生效"——get() 读旧版本自动当未命中。`bz <code> --refresh` 可单股强刷
- **个股全量数据有类级 120s TTL 缓存**（v0.8.8.1，ISS-077）：`AKShareClient._stock_data_cache`（code → StockData），`calculate_indicators`/`get_stock_data` 同股 2 分钟内直接命中（防 chat/REPL 高频爬取被反爬）。只缓存完整结果，失败/降级不缓存；回测走 `DataFeeder._build_stock_data` 不受影响。**改了 calculate_indicators 逻辑联调时先清缓存**（`AKShareClient._stock_data_cache.clear()`），否则 2 分钟内旧结果命中造成"改代码不生效"错觉
- **bz 不依赖 RAG**（v0.8.6.3）：笨总评分标准内嵌 SYSTEM_PROMPT，industry_prosperity 不再调 RAG。RAG 仍用于 scan/analysis 的事件/持仓策略增强
- **curl_cffi SSL 证书路径修复**（v0.8.6.3，ISS-045）：项目路径含中文「暮云思辨投资助手」致 curl_cffi 的 libcurl 找不到 CA 证书（curl 77）。修复在 `src/data/source_check.py:fix_curl_ssl_paths`（公共函数），data_provider/news_client 共用，启动时把 certifi 证书复制到 ASCII 路径 `~/.muyun_cacert.pem`
- **RAG ingestion 用 `rglob` 扫 `.txt` + `.md`**（`src/rag/ingestion.py`）：新增策略文档放子目录也会被扫到
- **AI 评分 JSON 截断容错**（v0.8.6.5）：`_call_ai_for_score(max_tokens=)` 按维度可调（业务纯度 2000，其余 1500）+ finish_reason==length 自动重试 + `_parse_score_json` 容错截断。截断退化 confidence=0.3（不再假装 0.5）。维度 prompt 须显式限 reasoning 长度防超 max_tokens
- **网络超时与退出边界**（ISS-047）：akshare/baostock底层requests无timeout会挂起，现有调用点使用线程超时并提供降级。新增网络调用须核总期限与进程退出行为。`shutdown(wait=False)`只表示调用方不等待executor关闭，不能证明解释器退出时不会join仍运行线程；v41专用取证及本批三个共享包装器/robust调用用daemon和独立结果槽限定等待；新扫描补算有同一monotonic批截止。不能强杀DNS/socket或担保晚到副作用/全CLI退出；原剩余4个旧executor站点已由第十轮T05-D改用共享daemon并行helper`run_parallel_with_deadline`并有专用退出反例（ISS-120）。不要用with块包`ThreadPoolExecutor`，其`__exit__`的`shutdown(wait=True)`会等待工作线程。 Baostock生产消费者须走`src/data/baostock_session.py`的`run`，登录/心跳/查询/分页/必要关闭同worker、同绝对截止；只有实际worker结束才释放非阻塞租约，调用方超时不得另登出。复用既有祖先放弃传播，核接收门和分页终止错误；不让可联网ResultData逃逸。
- **笨总等级展示/决策一律用 `effective_grade()` 不用 `grade()`**（ISS-047）：`grade()`/`total_score` 是 Excel 保真值（试金石 7/7 锁死，不能改）；`effective_grade()`（一票否决判F/景气度=0判F/≤30最高C/≤50最高B）+ `normalized_score()`（÷(正权和×当期流动性系数) 归一化到100，B16 修复后满分恒=100 不随成交额漂移）才是展示/排序/定 mode 用的。改了等级逻辑必须改 effective_grade 而非 grade
- **建仓笨总评分一次定 mode，更新不重跑**（跳法A 阶段1）：`pos add`/回测建仓时笨总 grade→`generator._mode_from_grade`（A→气宗180天/B→剑宗30天）。`pos plan --update` 由旧 mode 反推 grade 不重跑评分（避免纪律摇摆）。回测走 `backtest_engine._resolve_benzong_mode`（rule_scorer 规则版，带 code 缓存）
- **AI client 必须设 timeout**（ISS-047）：`auto_scorer._build_ai_client` 已加 `timeout=60`+`max_retries=2`，防 bz scan 长批量挂起到 WinError 10060/10054。新写 OpenAI client 别忘 timeout
- 🔴 **模型能力判定必须走 `src/core/ai_model.py`，禁止再写版本号硬编码**（2026-09-11 实测事故）：DeepSeek 2026-09-10 发布 V4.1 Flash，官方 API 名 `deepseek-flash`（legacy `deepseek-v4-flash` 兼容路由）。项目改名后全库 10 处 `startswith("deepseek-v4")` 全失配 → 不再传 `thinking.type=disabled` → **全项目静默退回思考模式**（官方默认 effort=high；思考模式不支持 `temperature`——官方原文「设置不报错但不生效」；top_p 被抬到下限 0.95）。**新增 AI 调用点一律 `**thinking_disabled_body(model)`**，别自己判断模型名。官方事实：`deepseek-flash` = 1M 上下文 / 384K 最大输出 / 思考默认开（api-docs.deepseek.com/quick_start/pricing）。`tests/core/test_ai_model_family.py` 已锁：settings 里配置的模型名必须被识别 + src 内不得再出现 `startswith("deepseek-<数字>")`
- **chat 上下文护栏**（v0.8.9.4）：轮次上限提到 20 后单轮内工具结果累计翻倍，`_guard_before_call` 每轮请求前估算输入 token（`ai_model.estimate_messages_tokens`，含 **tools 定义的固定开销**约 2.8K，漏算会低估），超「`ai.<provider>.context_window` × `chat.context_guard.budget_ratio`」即折叠最早的 tool 结果——**折叠只改 content、绝不删除 tool 消息**（删了会破坏 assistant(tool_calls)/tool 配对 → API 400），折叠幂等（`_FOLDED_MARK` 前缀判重）。窗口是**官方数值写进配置**（改前回官方文档复核）：DeepSeek 1,000,000 / Kimi 262,144，未声明时兜底取较小者。新告警两条已同步人话化三处：`chat上下文预算触发折叠` / `chat上下文接近上限`
- **bz scan 双通道选股**（ISS-047）：法C（theme_locator AI 报股，细分材料）+ 全市场客观筛选（`quick_scan(market_query=)` 走 THS 成分股，含中小盘）。提示词已去龙头偏向，别再加"宁可少报"类措辞
- **DeepSeek-V4 模型迁移 + thinking=disabled**（2026-07-21）：`deepseek-chat` 2026-07-24 弃用 -> `deepseek-v4-flash`（`configs/settings.yaml` model）。V4 默认思考模式，保持非思考需调用处传 `extra_body={"thinking":{"type":"disabled"}}`，判断用 `model.startswith("deepseek-v4")`（7 处：chat/agent、ai_modifier、benzong dimensions/theme_locator、event_layer×2、source_check、scanner_engine）。回退旧 deepseek-chat 会因 thinking 参数不认报错
- **chat analyze_stock 必须落观察量+建议**（H1 修复 2026-07-21；**F1 2026-09-25 三拆重构**）：调完 `_orchestrator.analyze` 后持仓股调 `pm.record_analysis_observation`（观察量/信号历史，不改仓位/成本/日期）+ `pm.record_proposal`（建议入 `~/.muyun/proposals.json` 待确认账本）；**`update_from_strategy_decision`（建议整包当持仓回写）已删除**——建议与成交分离（plan/fusion ADR-F03），用户 `pos confirm` 确认实际成交才改持仓。观察量保留 `high_since_entry` 三者取大语义。chat 层 max_tokens 拉满 384K（V4 输出上限）+ finish_reason=length 截断检测
- **scan market [参数] 第一个参数当 rule_name**（2026-07-23 修复）：parse_input（start.py）之前把 `scan market <参数>` 的参数全当 market_query（主题词），rule 固定 healthy_pullback。现第一个参数当 rule_name（resolve_rule_name 模糊匹配，失败才当主题词）。`scan market oversold_watch` 现走 oversold_watch 规则
- **pandas replace(0, pd.NA) 会变 object dtype 致 .round() 崩**（rsi_6_series bug，2026-07-23）：`_loss.replace(0, pd.NA)` 在 float Series 上变 object，`.round(2)` 对 NAType 报 `TypeError`。用 `_loss.replace(0, float('nan'))` + `.astype(float).round(2)` 保 float NaN。**测试 oversold_confirm 必须跑真实 calculate_indicators**（mock StockData 不暴露此 bug）
- **akshare 股东户数/融资余额 API 参数反直觉**（v0.8.6.8，LRN-20260812-001）：`stock_zh_a_gdhs(symbol)` 参数是**日期**(YYYYMMDD)非代码，传代码会 hang（em 端点反爬）。取单股股东户数用 `stock_zh_a_gdhs_detail_em(symbol=代码)`（列"股东户数-本次/上次"）。`stock_margin_detail_sse/szse` 参数是**单日期**返回全市场（无 start_date/stock_code），取单股需查日期+过滤代码。`AKShareClient._ensure_baostock_login` 不存在--是模块级函数 `from src.data.akshare_client import _ensure_baostock_login`；代码前缀用 `AKShareClient._normalize_stock_code` 类方法。新增 akshare 调用前先 `inspect.signature` 查参数，别按函数名猜
- **exit_signal 日缓存 + live 门控**（v0.8.6.8）：股东户数/融资余额信号 `stock.py:_check_holder_count_surge/_check_margin_surge` 用日缓存（`~/.muyun/exit_signal_cache/{code}_{date}_{signal}.json`，`_MISS` 哨兵区分未命中vs缓存None）。`check_stock_top_signal(live=...)`：回测 `is_backtest=True` 传 `live=False` 跳过 akshare（point-in-time+网络），orchestrator 传 `live=not is_backtest`。**回测路径不可触发 akshare 网络调用**。**宏观层同款门控（ISS-063，2026-08-23）**：`check_top_signals` 里 macro 层 `live=False` 且未显式传入成交额时必须跳过——`getattr(data,'market_turnover_trillion',None)` 恒为 None 会触发实时拉取全市场快照，拉取失败拖垮回测速度、拉取成功=未来信息注入。新增信号层先问'回测路径会不会碰网络'。`shutdown(wait=False)`只保证当前调用不等待关闭；不能据此声称解释器退出不join运行线程。v41 source-check的daemon/monotonic修复及其限度见上条；旧调用仍需逐点核验。
- **告警人话化三处同步（v0.8.7.3，硬纪律）**：任何 logger.warning 新增/改动，**必须同一 commit 同步三处**--① `src/cli/plain_errors.py` 的 `WARNING_PATTERNS` 映射表（命中才翻译，未命中只透传不崩）② `docs/报错速查手册.md`（人话字典唯一详版）③ 触发该告警的代码本身。人话话术必须留余地（写"数据源慢或不可用"而非押单一成因），影响级别只准用"终止/缺失"两档。**过滤器改写须幂等（2026-09-11）**：`PlainLanguageFilter.filter` 开头有 `if raw.startswith("⚠ "): return True` 守卫——filter 直接改写 `record.msg`，同一 record 若流经**多个挂本 filter 的 handler**（或同 handler 挂多个 filter）会被二次翻译，嵌套成「⚠ …｜原始：⚠ …｜原始：…」且 `_recent` 重复记账致末尾汇总结论数字虚高。生产 REPL 只有 1 个 root handler 故不触发，但**新增 handler/在子进程里 install() 前要先想这条**
- **三倍定律气宗跳过**（v0.8.6.8 回测发现）：`_check_triple_up_rule`（price/low_60d≥3+破MA5）是 force-exit top_signal，气宗牛股被过早离场。`check_stock_top_signal(mode=...)`：气宗(`qizong`)跳过三倍定律（同 take_profit_trim 可压，气宗靠换手/减持/渗透率/旗手等真见顶信号逃顶），剑宗/非气宗(`mode!=qizong`，含None)照常触发

### 数据源

- **akshare 对未来日期返回最新数据**（v0.8.10 实证，LRN-20260913-011）：向 akshare 查未来日期会静默返回最新可得数据而非空——一切回填/历史查询必须过滤未来交易日，否则产生脏数据（曾产生 16 个脏文件已清）
- **DataFeeder 向量化的等价性参照基准不得删除**（v0.8.9.5）：旧 `_build_timeframe_snapshot`/`_calc_*` 是 tests/backtest/test_datafeeder_vectorized_equiv.py 的数值等价性参照（4 种子×双口径×1193 bar 全字段 0 分歧），删了参照=等价性门失效
- **Weinstein 阶段双实现分歧是已知权衡，勿单独"修复"**（ISS-065，2026-08-24 实证）：generator._detect_weinstein_stage(定mode,4分法)与 cli 显示(6分法)在价格处于 MA20/MA60 之间时判定相反。统一口径试验曾实施——边界单测13项全过但三批回测门退化（批次1牛股组 +12.32pp/8正向 -> +6.28pp/6正向），旧"错判"客观充当波动率过滤器（回调建仓降剑宗=紧止损躲过死扛），已回退。重试前提=配套气宗准入质量条件重新校准（独立课题）。另注意：三批回测脚本人工标注 mode 绕过此函数，mode 层 bug 在现有回测体系中不可见
- **新浪数据源无 `量比` 和 `60日涨跌幅`**：scan_rules.yaml 中含这些字段的过滤器会静默跳过（scanner_filter.py L118）
- **60 日涨跌幅由 `_enrich_trend_data()` 后补**：在 quick_scan 初筛后通过 Baostock 逐只计算，仅候选 ≤50 只时执行
- **eFinance 有量比但无市净率和 60 日涨跌幅**
- **AKShare `stock_zh_a_spot_em()` 有完整字段但需 4 分钟**：已废弃，改用新浪+eFinance
- **Baostock preclose 字段仅日线支持**：周线/月线查询时 fields 字符串不含 preclose
- **faiss 读写索引路径必须用相对路径或 ASCII**（2026-09-05 实证）：faiss C++ 层 fopen 按 ANSI 代码页解析 UTF-8 中文绝对路径 → ENOENT（文件明明存在）；产品代码本就用相对路径（cwd 由 OS 层解析）不受影响，写探针/脚本时 `cd` 到仓库根再传相对路径即可

### 网络

- **系统代理 `127.0.0.1:7890` 会干扰东方财富 API 和 GitHub**：金融数据需 `_without_proxy()` 绕过。Git 推送注意：全局 .gitconfig 配的是 **URL 级代理** `http.https://github.com.proxy`，`-c http.proxy=` 覆盖不了它，要绕过须用 `git -c http.https://github.com.proxy= push`；若报 'via 127.0.0.1 ... Could not connect' 说明代理客户端没开，此时直连通常也被墙，先开代理再推
- **THS 板块 API 可能超时**：`get_stocks_by_industry/concept` 有 15 秒超时保护

### 硬件

- **用户机器 i7 14700 缩肛（Intel 已知退化），RAG 首次加载嵌入模型有蓝屏/死机风险**（2026-09-26 用户叮嘱）：模型加载时 torch 默认吃满全部核心 → 功耗峰值是主嫌疑（用户靠 Intel XTU 限频压制）。软件侧保险丝：设环境变量 `MUYUN_RAG_MAX_THREADS=N` 可把加载线程压到 N（`src/rag/embedding.py::_apply_thread_cap`，默认不设=行为零变化）。**任何会提高加载期 CPU 并行度/重复触发模型加载的改动都要先想这条**；跑批脚本避免无谓的多进程 RAG 冷启动

### Git

- **Git与中文路径**：使用当前Shell及正确引用的绝对路径，输出显式UTF8；本轮PowerShell直接执行Git正常，不强制再套cmd。
- **提交备注用中文详细描述**：每个 commit 写清楚改了什么、为什么改
- **里程碑提交与推送**（用户2026-10-20明确要求）：本项目单人维护，默认直接在main开发、验收、集中commit+push，不自动创建命名分支或PR，不把合并留给用户。远端领先时由集成方处理冲突并验证，可用临时detached检出，不强推覆盖。DSH合同写明git_owner=Codex；只读任务不提交。禁止git add -A或夹带账户、知识库与私人配置；各项目只提交各自仓库。
- **推送前核连通性**：GitHub路由按实测选择命令级代理/TLS后端；不把旧Connection reset归因当永久禁用代理规则，不改全局代理或关闭证书核验。金融接口另按数据源规则。

---

## 五·五、防复发硬门禁（2026-08-29 立项）

> 背景：两轮对抗性审查累计 69 条发现 + 259 条 git 提交考古，论证见 `开发问题根因复盘_20260829.md`。
> **本项目 `fix : feat` = 75 : 71。根因不是代码质量，是教训从未闭环。**
> **开工前必读：`skills/muyun-dev-discipline/SKILL.md`**（工具中立主副本）。
> 各 Agent 工具的 skill 目录互不兼容，该 skill 已同步部署到 `.claude/skills/`、`.github/skills/`、`.cursor/rules/`、`.codex/skills/`、`.workbuddy/skills/`；改动主副本后跑 `bash scripts/sync-agent-skills.sh`。

### 铁律 0：教训必须落进仓库内（不做 = 没做）

会话级记忆目录**因 Agent 工具而异，且大多不进版本控制**（有的工具根本没有持久记忆）。
**唯一跨工具、跨机器、跨会话可靠的沉淀点 = 仓库内的 `.learnings/`。**

| 位置 | 跨工具 | 版本控制 | 用途 |
|---|---|---|---|
| `.learnings/LEARNINGS.md` | ✅ | ✅ | **必写**，唯一可靠沉淀点 |
| `.learnings/ERRORS.md` | ✅ | ✅ | 失败与复现条件 |
| 工具自带的会话记忆目录 | ❌ | ❌ | 可选，只服务当前会话 |

只写工具自带的会话记忆 = 换工具/换机器/清缓存即归零 = 没写。
> 2026-08-29 实测：本周 69 条发现**零条**进入 `.learnings/`；`LEARNINGS.md` 停在 08-25，`ERRORS.md` 停在 07-29；23 条 LRN 中 13 条 Status 永远 pending。
> 同一个坑：自我提升 skill 只放在 `.github/skills/`（Copilot 路径），而实际干活的 Agent 不扫那个目录 → 那条「失败后必须先走 self-improvement skill」的强制规则**从未执行过一次**。

### 铁律 1：修 bug 四步，缺一步不许提交

```
① 写回归测试（先红灯）→ ② 修（转绿灯）→ ③ 扫同类点 → ④ 落 .learnings/
```

**③ 扫同类点**（本项目最常漏）：修完先 grep 全库同类模式，报告剩余数量。
已确认的同类点模式：`with ThreadPoolExecutor` / `os.environ[...] = ` / `except`+`debug`+`continue`（静默 fail-open）/ `if <DataFrame>:` / 中文全角 `（）` / `0\.8\.7\.[0-9]`。
**扫出来还有同类点 → 要么一起修，要么在 ISSUES.md 记「已知剩余 N 处」，不许默默留着。**

### 铁律 2：文档/注释动作 ≠ 修复

以下一律判「未修复」，不许标 ✅：
1. commit message 声称改代码，但 `git show --stat` 零行代码改动（实证：`87047f8`）
2. 只改 docstring 让它「与行为一致」，行为没改（实证：`c993c6d` → `plan_guard.py:177` 至今仍压 `take_profit_trim`）
3. 把教训写进 AGENTS.md 就算修完（实证：`814963c` → `akshare_client.py` 的 with 块活到 08-29）

**自检查句：我的 diff 里有改变运行时的代码行吗？没有就是没修。**

### 铁律 3：验证靠「跑」，不靠「读」

- 动全局状态（env / 类级缓存 / 单例）→ **必须跑全量测试**，不能只跑单文件
- 动 YAML ↔ 代码映射 → **必须写脚本交叉比对**，不能人工读
- 脚本结论 → **必须回读代码确认再报**（`price_position` 是特例分支，会假阳性）
- 调卖出参数前 → **先打 `trade['sell_path']` 分布**

### 铁律 4：调参红线（LRN-20260619-001，写了但从未被执行）

30 分钟前提验证 / 第三轮失败原则 / 改善门槛（单股<2pp、整体<1pp 视为噪声）/ 基准 A/B 对照。
**单股样本无发言权**（ISS-068：柯力单股 -10.9pp 在全量 19 只视角下是 +0.63 噪声）。

### 铁律 5：提交前自检

- [ ] `git show --stat` 有代码改动吗（非空校验）
- [ ] 同类点扫过了吗，剩余几处记下来了吗
- [ ] 能给出可复现的验证命令 + 期望输出吗
- [ ] 用户跑 `start.py` 看得到吗（§二·五 五选一）
- [ ] 改版本号了吗？改了就 5 处全改（start.py / main.py / AGENTS.md / README.md / docs）
- [ ] ISSUES 状态带 `file:line` + 日期 + commit 短哈希了吗
- [ ] 告警人话化三处同步了吗
- [ ] 教训双写了吗

---

## 六、开发习惯与维护流程

遵循全局开发工作流 skill：`$dev-flow`

核心流程：**Plan → Diagnose → Implement → Verify → Sync → Ship**

详细规范（六步法、知识管理、业界最佳实践、反模式）见 `$dev-flow` skill。

> 脚注：`$dev-flow` 是用户级全局 skill，不在本仓库内。
>
> **⚠ skill 目录互不兼容（本项目踩过的真坑）**：各 Agent 工具扫描 skill 的目录各不相同——
> Claude Code 用 `.claude/skills/`、VS Code Copilot 用 `.github/skills/`、Cursor 用 `.cursor/rules/`、
> Codex 用 `.codex/skills/`、WorkBuddy/CodeBuddy 用 `.workbuddy/skills/`。
> 仓库内的 `self-improvement` skill 只放在 `.github/skills/`，而实际干活的 Agent 不扫那个目录，
> 导致 `copilot-instructions.md` 里那条「失败后必须先走 self-improvement skill」的规则**从未生效过**。
>
> **因此纪律**：新纪律一律写进**主副本 `skills/muyun-dev-discipline/SKILL.md`**（项目根，工具中立），
> 写完跑 `bash scripts/sync-agent-skills.sh` 把副本推到上述 5 个目录。
> 不要依赖"某个目录一定会被加载"——真正的兜底是各工具入口文件（`AGENTS.md` / `CLAUDE.md` /
> `.github/copilot-instructions.md` / `.cursorrules`）里那句显式的「开工前先读 skills/…」。

### 本项目特有的补充

**修改 YAML 配置的检查清单**：
- [ ] 所有代码中硬编码的键名已同步（grep 确认）
- [ ] scan_rules.yaml 的字段在 scanner_filter.FIELD_MAP 中
- [ ] 字段在当前数据源中可用（新浪无 量比/60d）
- [ ] start.py 帮助文本已更新
- [ ] 使用手册.md 规则表已更新

**文件命名**：
- 临时脚本：`_xxx.py`（开发完立即删除，不提交）
- 测试文件：`tests/test_xxx.py`
- 配置：`configs/xxx.yaml` 或 `src/xxx/config.yaml`

---

## 七、当前状态

当前交付与阻断见 §二的独立审批指针及 plan/fusion/STATUS.md，历史已完成清单见 README 版本历史与现有归档，不在此重复维护。

持仓计划命令：`pos plan <code|all> [--update]` 生成/更新/批量计划；`pos overweight` 超配须守三铁律（见使用手册）。产业关键词只作研究线索，不强制切换持有模式；宏观流动性/市场宽度为advisory展示，MarketState不替代气宗固定持有纪律。金字塔仓位管理已移除，禁止重新引用已删配置/兼容壳。
### 待办（优先级排序）

待办以 `ISSUES.md` 顶部和当前独立审批为准，不从旧版本清单推定仍待实施。行业景气度客观数据源的可得性及用户待决策项见 `docs/2026-08-24_景气度客观数据源评估.md` 与 ISSUES 对应记录。
> 已拍板关闭勿再提（2026-08-24）：换手率 fetcher（不做，死代码已移除）、宏观 10 万亿阈值（维持现状）、
> **气宗压 trend_exit/chandelier 开闸（ISS-078 P1(b)，2026-09-05 拍板：维持现状勿再提——该口径承载 ISS-033 全部回测基线，要动先走 env 开关 A/B 立项）**、
> 公告关键词扩展（不做）、美债监测面板（ISS-062）、拥挤度代理面板（ISS-064）、M-A 单独统一口径（ISS-065）。

> 安全提醒：API key 已迁至 `configs/settings.local.yaml`（gitignored，ISS-042 已修）。历史 commit 泄露的旧 key 用户需去 DeepSeek/Moonshot 后台 rotate。

### 持仓

实时见 `portfolio.yaml`，本文档不内嵌。

---

## 八、文档索引

| 文档 | 内容 |
|------|------|
| `README.md` | 项目概述、架构图、版本历史、技术栈 |
| `使用手册.md` | 命令详解、工作流、买卖点说明、常见问题 — **首部含「🌱 新手入门 5 分钟」+ 末尾含「📖 术语对照表」**，专业术语看不懂时翻这里 |
| `docs/TradePlan_使用指南.md` | **v0.8.5 新增**：建仓 plan 草稿生成、PlanGuard 压制 weak_sell、动态调整建议——让股票"持得住" |
| `docs/技术架构文档.md` | 七层架构详解、数据流、模块关系 |
| `docs/AI系统说明.md` | AI 角色、影响范围、可控性、降级策略 |
| `docs/chat模块架构文档.md` | **v0.8.9.3 更新**：chat Agent 全景（核心循环/流式/降级/14工具数据来源/子进程化/**v0.8.8 命令桥：run_command+manage_portfolio+confirm 硬门**/**v0.8.9.3 会话中断恢复+文件读写沙箱**） |
| `docs/回测到实盘对齐指南.md` | **v0.8.7 新增**：如何实盘复现回测验证过的纪律（mode 对齐/T+1 执行/live 独有信号） |
| `docs/v0.8.6_调研报告.md` | 笨总超景气价值投机体系融合架构 + 4 阶段路线 + 4 决策点 |
| `docs/笨总6维评分_说明文档.md` | **v0.8.6.3 新增**：笨总 6 维评分器工作原理/数据来源/降级策略/命令用法/输出解读 |
| `docs/选股与持仓规划实战工作流.md` | **v0.8.6.5 新增**：端到端用法主线（选股→评分→建仓→持有→离场），含不同市况用法 |
| `docs/2026-08-23_美债风险文档评估+量化拥挤监测思路.md` | **v0.8.7 新增**：美债监测面板评估（拍板不做 ISS-062）+ 监测信号先过历史迷你回测纪律 + 拥挤度代理候选清单 |
| `docs/实盘操作指南.md` | 回测验证框架、参数调优方法论 |
| `ISSUES.md` | 所有问题追踪+ 顶部当前待办汇总 |
| `docs/archive/2026-09-25_交接_C批次实施与F系列接手.md` | 历史M/C与F接手记录；当前状态见 `plan/fusion/STATUS.md`，不从归档推定完成状态 |
| `docs/archive/2026-09-24_交接_扫描复盘观察池与架构师迭代计划.md` | 历史交接（已归档）：scan review/观察池交付明细 |
| `plan/` | 当前里程碑 = [plan/fusion/STATUS.md](plan/fusion/STATUS.md)，跨对话交接 = [RESUME](plan/fusion/RESUME.md)（含明确标注的 Codex 架构师会话记忆，不向其他 Agent 分配身份）；当前实施唯一入口为第九轮DELIVERY_PLAN.md，审批为同目录R15_ACCEPTANCE.md；S2AC文件只保留专项原范围与证据；iteration历史只作证据。按复查索引比较源码变更，只重看受影响块；设计交付、实现、场景验证和策略效果不得混为完成 |
| `tests/README.md` | **v0.8.13 新增**：测试目录说明与跑法（离线默认 `pytest -q`/外源与 AI 显式启用/用户目录隔离机制） |
| `docs/archive/` | 历史版本文档归档（v0.7.x~v0.8.6.x 迭代规划/里程碑/旧交接 + 2026-09 迁出的 AGENTS 版本叙事归档，不再维护），索引见 `docs/archive/README.md` |
| `portfolio.yaml` | 当前持仓记录 |
| `src/scanner/scan_rules.yaml` | 扫描规则定义（healthy_pullback/steady_advance/shrink_pullback/value_pick/theme_members/oversold_watch超跌错杀观察） |
| `configs/settings.yaml` | 全局配置（API key 不在此，见 `configs/settings.local.yaml.example`） |
