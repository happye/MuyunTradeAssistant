# 暮云思辨投资助手 — AI Agent 开发手册

> 任何 AI 编码工具打开此项目时，请先阅读本文件。
> 详细文档索引见末尾。

---

## 一、怎么跑

```
uv run python start.py          # 交互式 REPL（推荐日常使用）
python -m src.cli.main -l 600519  # CLI 模式：分析单只股票
# l 600519,000001               # REPL 多代码批量深分析（v0.8.9.0，中英文逗号/空格分隔均可）
```

笨总评分 + 主题选股（v0.8.6）：
```
bz 600519            # 6 维 AI 自动评分（每维 1 次 AI，约 30-60s）
bz 600519 --refresh  # 强制刷新跳过缓存
bz 600519,000001     # 多代码批量评分排名（v0.8.9.0，中英文逗号/空格分隔均可，缓存优先）
bz scan 氮化镓,钽电容 # 法C 主题精准定位+笨总评分排名（细分赛道也能精准）
bz scan shrink_pullback 科技 # 规则+主题一起用：科技板块内按缩量回调形态初筛（首词可模糊匹配规则名）
bz --check           # 数据源连通性体检
bz --manual          # 旧交互式手动打分（兜底，只支持单只）
```

预期事件日历（v0.8.7，事前预期透支）：
```
暮云> expect           # 未来30天事件日历 + 预期透支度面板（环境温度计）
暮云> expect 60        # 自定义天数
# 详见 使用手册.md「预期事件日历」章节；与 events（事后）互补
```

一键/批量（v0.8.7.1）：
```
暮云> la               # 一键分析所有持仓——每只一张「人话摘要」卡（该做什么/阶段/笨总评分），compact 模式
暮云> ba               # 对最近一次扫描（bz scan / scan market）结果批量笨总评分+白话点评排名表
暮云> l all            # bz scan / scan market 出结果后接续用：批量深分析（v0.8.7.9），逐只 compact 简明卡
#                        当天已析股默认跳过（防重复花AI费用），l all -f 强制全部重析；失败股不记账、下次自动重试
# l 命令输出顶部现在带「📖 人话摘要」面板：白话结论+原因+趋势阶段+笨总评分+术语小词典；详细报告保留在下方不变
```

依赖安装：
```
uv sync
```

Python 3.11+，包管理器 uv。不需要手动 pip install。

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

核心能力：全市场扫描、深度分析（含 Weinstein 阶段）、买卖点精确触发、回测框架、RAG 策略知识检索、**TradePlan 持久化交易计划**（v0.8.5）、**笨总 6 维 AI 自动评分 + 主题精准选股**（v0.8.6）、**气宗持有模式 + 事件四要素**（v0.8.6.3）、**笨总视频理念优化：板块层信号+三倍定律+超配+市场宽度+自主可控强制剑宗**（v0.8.6.8）。

当前版本：**v0.8.9.3**（chat 会话中断恢复 + 本地文件读写工具，ISS-092，2026-09-11）：① **会话逐消息原子落盘** `~/.muyun/chat_sessions/current.json`（tmp+os.replace，`src/chat/session_store.py`）——q 退出/进程崩溃/发送失败（额度墙 402）中断后，下次启动 chat 检测未归档会话提示恢复（y/N；EOF/Ctrl+C 不恢复也不归档）；拒绝/reset 重置/并发双开防覆盖一律**归档 `session_时间戳.json` 留档不删**；损坏文件隔离 `corrupt_` 前缀；恢复时 system 提示词换当前版+按 max_history 裁剪（配对保护）；`sessions` 命令列会话文件；`chat.session_persist` 可关（settings.yaml）。② **本地文件读写工具**（用户需求：仿 manage_portfolio 扩展，"把当前对话总结成精华写入本地"类场景）：`read_file`（沙箱=仓库根内，越界/密钥文件 `configs/settings.local.yaml`/`.env` 拒绝，二进制/超1MB 拒绝，utf-8/gbk 自适应）、`write_file`（沙箱=仅 `AI笔记/` 专属目录，可建子目录，覆盖提示，逃逸拒绝）、`list_files`（仓库目录清单，.git/.venv 等隐藏）——三件注册进 TOOL_REGISTRY+schema+系统提示文件读写规范。测试：test_chat_session_store 14 + test_chat_session_resume 11 + test_chat_file_tools 15（对抗审查 2×P1+8×P2 修复：恢复裁空保留原文件/测试重定向编码容错），全量 619 passed / 4 skipped（含 2 项 data_sources 外源抖动）。上一版 **v0.8.9.2**（盘中实时价+CLI接RAG+杂项加固，ISS-089/090/091，2026-09-07）：① **盘中实时价**——单只 `l` 行情链路改为新浪实时优先（原 baostock 优先盘中给的是昨收，技术面/AI 全基于过期价格），失败自动落回既有降级链；② **CLI 分析路径接 RAG**（ISS-085 拍板落地）——cli/main 4 处 Orchestrator + scanner 深析懒接线传 `get_cli_rag_service()`（MUYUN_CLI_RAG=0 关闭；单次启用提示可感知；get_rag_service 失败记忆防反复 18s 加载）；③ 杂项加固——benzong 评分缓存 90 天自动清理、exit_signal 日缓存 30 天清理、add_position 无开仓价显式告警、plan_guard 日期解析失败告警、个股新闻当日磁盘缓存 ~/.muyun/news_cache/；④ 测试账实同步——AGENTS tests 表修正（tests/ui 已不存在，web 零测试为已知缺口）。上一版 **v0.8.9.1**（网络防护层+多代码批量，ISS-087/088，2026-09-07）：反爬四件套落地——`src/data/net_guard.py`（按源节流+连续失败熔断，进程级单例）、新浪 list 批量行情+东财全市场/ETF 逐层降级的 `get_realtime_quotes` 批量原语（部分失败合并结果）、`_QUOTE_PREFETCH` 预取映射（live_multi 开头预取一次，逐只 get_realtime_quote 零请求）、历史K线当日磁盘缓存 `~/.muyun/kline_cache/`（17:30 灰区/晚间新鲜度规则，仅 live 路径，回测 DataFeeder 不经过）。上一版 **v0.8.9.0**（RAG 知识库扩充+检索质量批+对抗审查修复，ISS-085，2026-09-06）：① 知识库 712→837 块（公众号教程 73 篇入库），系列隔离 doc_id 前缀（rz/jrz/qp/ol/ot）修 91 个跨系列 ID 冲突；② 分块修复（超长按句硬切+50 字重叠，全块 ≤550 字）+ jieba 金融词典 100 词 + 同章多样性截断（单章最多 2 席）+ get_context 层过滤（Chat/Decision/AI_Modifier）；③ 评估器标注守卫（doc_id 命中率<50% 自动重标）；④ 重排器代码保留默认关（A/B 实测负收益）；⑤ 审查修复：**重建分支换新 store 根治僵尸索引**（已删/改名文件的块曾会永久残留）、store.add 批内 doc_id 去重+摄入撞号告警、**embedding.model 动态解析本地 HF 快照**（settings 回归可移植模型名，命中快照零联网——断网重试风暴=蓝屏事故嫌疑机制已封死）、chat search_knowledge 双检索并一次；⑥ **底层挖掘 ISS-086：strategy_layer 日频推进按交易日去重**（last_tick_date 防同日重入）——live 同日重复分析不再烧穿 5 天禁买/10 天减仓保护/污染信号史（此前每次分析烧一天，探针实证），回测每 bar 一次/不传日期调用方走旧语义=零变化，行为矩阵逐调用点核实。全量 **554 passed / 2 skipped**（tests/data_sources 偶发外部抖动，单跑即过）。**RAG 消费方现状（重要）**：仅 chat（search_knowledge+chat 内分析，单例对齐）与 TradePlan（pos add/pos plan 经 get_rag_service）接 RAG；**CLI/REPL 分析路径（main.py:175/215/457/875 + scanner_engine.py:91 深析）自 v0.8.1 起未传 rag_service**——AIModifier/EventLayer 的 RAG 增强在这些场景静默跳过，接线影响评估已交用户拍板（ISS-085）。⑦ **多代码批量（ISS-087）**：`l`/`bz`/chat analyze_stock 支持 `l 600519,000001` 一次性多代码（中英文逗号/顿号/分号/空格混分、#N 可混用、非法 token 告警、单只失败不拖垮批次）；bz 多代码复用 ba 批量管道（auto_score_batch：AI client 建一次/整批共享成交额/缓存优先）；chat confirm 门加 live_multi/benzong_multi；prompts 三处明示能力（agent 不再瞎猜）。回测不碰 RAG（backtest 构造无 ai/event config）。上一版 **v0.8.8.7**（景气度接线方案A，ISS-083，2026-09-05）：`industry_prosperity` 从「行业名+新闻标题猜」升级为「客观数据主导」——`data_provider.get_industry_metrics` 三级桥接（L1 手写链代表公司代码直配 > L2 链名/别名 > L3 COMMODITY_MAP 13 类商品关键词，**自举链不参与**）拉取商品价格分位/仓单趋势/需求同比/宏观 PMI-PPI（复用 industry_data 全套缓存+超时+降级），注入维度 prompt 并声明「客观证据优先、矛盾以客观数据为准、[数据缺失]不得脑补」；兜底变严（行业名+新闻+metrics 三者全空才 50）。四项验收门槛全过：回测结构隔离+冒烟前后逐格一致 / CACHE_VERSION→v0.8.8.7 / scorer.py 零改动 / 兜底变严。live 验收：6 命中股 sources 全带客观标签、reasoning 引用具体价格分位（融捷 ip 70→30 修正新闻情绪误判），未命中股行为不变（探针命中率 56%，证据 tests/artifacts/iss083_wiring_round1/）。**回测行为零变化**（结构性隔离）。评分系统不重设计（闸门公式属笨总域，病根是输入；方案B 夹逼留独立立项）。上一版 **v0.8.8.6**（第三轮审查小项清扫批，ISS-082，2026-09-05）：① PlanGuard 规则 4.5/P1/3 强制清仓补写 COOLDOWN（对齐规则4 ISS-068，四条口径一致）；② ba 白话点评 conf=0 维度显示"未评出"（中性 50 不再冒充真实打分）；③ manage_portfolio 代码规范化（SH.600519 式输入）；④ bz flag 大小写不敏感（-R 不再静默失效）；⑤ settings.yaml 死键清理 + 显式补 ai.request_timeout/max_retries；⑥ 军工自举链补 created_at；⑦ 删 suggest_update 死代码（62 行）+ 修两处失真注释；⑧ chat 系统提示加工具输出防火墙条款。**新立 ISS-081（TRIM 类 force_exit 不在 P1a 重断言范围，待 A/B）**。上一版 **v0.8.8.5**（静态事件表自动化，ISS-080，2026-09-05）：`expect` 不再逐条刷「静态事件表可能过时…请更新」催办——① 过期一次性事件自动归档（7 天宽限，无需手动标 landed，landed 仍可用于主动标记复盘）；② 新增 `recur` 周期事件（monthly/quarter + day + window_days，`{m}`=数据月/`{q}`=数据季占位符）自动滚动，CPI/PPI、季度GDP 不再需要每月手动加条目；③ 逐条催办改为至多一条汇总（防静默空表）。static_events.yaml 已迁移（年维护量从 ~20 次手动编辑降为 1 次美联储日期更新）。顺带实测：akshare `news_economic_baidu` 已存在但本环境被 BAIDUID cookies 挡住，接入留待办。上一版 **v0.8.8.4**（expect 财报披露空态修复，ISS-079，2026-09-05）：`expect` 命令期 1 条「Length mismatch: Expected axis has 0 elements, new values have 10 elements」英文告警根治——巨潮预约披露接口对**尚未发布预约表的报告期**返回空列表，akshare `stock_report_disclosure` 对空数据直接 `temp_df.columns=[10列]` 崩 Length mismatch（实测 2026-09 的 2026三季；每年预约表未发布的强制披露期同窗口复发，如 5-6 月半年报表）。「该期间无预约表」是合法业务空态非故障：`calendar_client._fetch` 仅对 Length mismatch 签名转空 DataFrame（其余异常原样上抛告警，防 fail-open 吞真故障）+ 业务空态结果入缓存（原 `df.empty` 早退绕过缓存写入，每次 expect 重复打接口）。用户可感知：expect 不再刷英文告警、重复执行不再重复打接口。同类点扫描：src 内无其它无防护列赋值/轴操作（崩点全在 akshare 上游内部）。上一版 **v0.8.8.3**（扫描校验误报修复，ISS-078 残留，2026-09-04）：`bz scan` 启动期 8 条「未知操作符 starts_with/contains」误告警消除——ISS-078 给 `_load_rules` 接的加载期校验把普通 filters 的 `VALID_OPS` 错套到 `global_exclude` 段（其执行器 `apply_global_exclude` 本就合法支持 starts_with/contains/is_nan，运行时排除一直在正常工作，告警文案「筛选变宽松」是假的），现新增 `ScannerFilter.validate_excludes`（`EXCLUDE_VALID_OPS` 专属操作符集）校验排除段、`validate_filters` 只管规则 filters 段；回归测试含「真实 scan_rules.yaml 零误报」+「真拼错操作符仍告警」双侧锁死（tests/core/test_scan_rules_validation.py）。上一版 **v0.8.8.2**（持仓数据安全批，ISS-078，2026-09-03 第三轮对抗审查落地）：chat 回写保留 TradePlan(P0) + PlanGuard 规则1守卫/orchestrator P1a 覆写防强制清仓被压成 HOLD + confirm 硬门补齐 la/lall/scan/events + 持仓值校验 isfinite+0-1 + 会话外修改 mtime 检测 + P2 批（kimi-k2.6 兜底/产业链字符串归一/M-G 先富集再排序再截断等），全量测试 455 passed / 2 skipped——明细见 ISSUES.md ISS-078 与 README 版本历史。上一版 **v0.8.8.1**（个股数据短 TTL 缓存，ISS-077：`calculate_indicators` 类级 120 秒缓存防 chat 高频反爬；完整结果才缓存，回测走 `DataFeeder._build_stock_data` 不受影响）。上一版 **v0.8.8**（chat 全命令桥 + 持仓文件修改，ISS-076：① `run_command` 工具——chat 里执行 REPL 命令（复用 start.parse_input+run_cli 单一真相源，Tee 回显+捕获喂 AI，批量 AI 费用操作带 confirm 硬门，ISS-078 起覆盖 la/scan/events）；② `manage_portfolio` 工具——建仓/清仓/字段级修改/交易计划/超配（复用 CLI 全部副作用，confirm 硬门+自动 .bak+ISS-078 值校验）；③ scan_market 同步写 session_state + RAG 单例对齐 + 命令后无条件 reload 持仓）。**回测行为基线提醒：v0.8.7.8 的 D03/D02/H01/G03 修复改变回测行为，基线以 ISS-074 A/B 对照为准；v0.8.8.2 的 P1a 覆写与 PlanGuard 守卫也改变回测路径行为（force_exit 不再被 weak_sell 压制），涉及 force_exit 场景的 A/B 对比需注意。**

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
| `tests/core/` | 核心引擎单测（技能/决策/持仓/TradePlan/信号） | test_conditions / trade_plan / fundamental_alert / top_signal 等；test_tech_context_e2e 为脚本式E2E（pytest 默认 skip，直跑） |
| `tests/data_sources/` | 打第三方接口的脚本（真实网络） | test_all_api（全数据源连通性体检）/ test_source_check / test_industry_data / probe_iss053（数据可得性探针） |
| ~~`tests/ui/`~~ | **已不存在（账实漂移，2026-09-07 核实）**：src/web/app.py（378 行实验性 flask UI）与 src/tui 当前零自动化测试——已知缺口，见 ISS-091 | 无 |
| `tests/artifacts/` | 历史回测/issue 验证的输出产物（json/log，非脚本） | extended_backtest_2024* / issue_033_round* |
| `tests/rag_eval/` | RAG 检索质量评估工具 | evaluator.py + relevance_labels |

跑法：`pytest tests/ -q --ignore=tests/artifacts --ignore=tests/rag_eval` 全量（统计口径固化 L05：每次全量的 passed/skipped 数字写进 commit message，历史对比以此为准）；单文件 `.\.venv\Scripts\python.exe tests\chat	est_xxx.py`（脚本自带 sys.path 修复）；网络类脚本先看文件头注释。

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
- **网络超时三层硬保护**（ISS-047，根治 WinError 10060/10054）：akshare/baostock 底层 requests 无 timeout 会挂起。`data_provider._safe_call`(30s) + `akshare_client._retry_with_backoff`(30s) + baostock `bs.next()` 读取(30s) 全用 `ThreadPoolExecutor` 线程级硬超时，超时即放弃走降级不冻结。新增网络调用必须包超时；且**不能用 with 块包 ThreadPoolExecutor**——__exit__ 的 shutdown(wait=True) 会 join 卡死线程使超时失效，须显式 shutdown(wait=False)（2026-08-24 ISS-066 审查发现 _safe_call/_call_with_timeout 两处中招已修，范本 source_check._probe_t）
- **笨总等级展示/决策一律用 `effective_grade()` 不用 `grade()`**（ISS-047）：`grade()`/`total_score` 是 Excel 保真值（试金石 7/7 锁死，不能改）；`effective_grade()`（一票否决判F/景气度=0判F/≤30最高C/≤50最高B）+ `normalized_score()`（÷(正权和×当期流动性系数) 归一化到100，B16 修复后满分恒=100 不随成交额漂移）才是展示/排序/定 mode 用的。改了等级逻辑必须改 effective_grade 而非 grade
- **建仓笨总评分一次定 mode，更新不重跑**（跳法A 阶段1）：`pos add`/回测建仓时笨总 grade→`generator._mode_from_grade`（A→气宗180天/B→剑宗30天）。`pos plan --update` 由旧 mode 反推 grade 不重跑评分（避免纪律摇摆）。回测走 `backtest_engine._resolve_benzong_mode`（rule_scorer 规则版，带 code 缓存）
- **AI client 必须设 timeout**（ISS-047）：`auto_scorer._build_ai_client` 已加 `timeout=60`+`max_retries=2`，防 bz scan 长批量挂起到 WinError 10060/10054。新写 OpenAI client 别忘 timeout
- **bz scan 双通道选股**（ISS-047）：法C（theme_locator AI 报股，细分材料）+ 全市场客观筛选（`quick_scan(market_query=)` 走 THS 成分股，含中小盘）。提示词已去龙头偏向，别再加"宁可少报"类措辞
- **DeepSeek-V4 模型迁移 + thinking=disabled**（2026-07-21）：`deepseek-chat` 2026-07-24 弃用 -> `deepseek-v4-flash`（`configs/settings.yaml` model）。V4 默认思考模式，保持非思考需调用处传 `extra_body={"thinking":{"type":"disabled"}}`，判断用 `model.startswith("deepseek-v4")`（7 处：chat/agent、ai_modifier、benzong dimensions/theme_locator、event_layer×2、source_check、scanner_engine）。回退旧 deepseek-chat 会因 thinking 参数不认报错
- **chat analyze_stock 必须回写 strategy_state**（H1 修复，2026-07-21）：与 CLI 一致，调完 `_orchestrator.analyze` 后回写 `pm.update_from_strategy_decision`（仅持仓股，非持仓回写会创建虚假记录）。`portfolio.update_from_strategy_decision` 已修保留 `high_since_entry`（取已存值/entry_exit 算的/当前价三者大值，CLI 也受益）。chat 层 max_tokens 拉满 384K（V4 输出上限）+ finish_reason=length 截断检测
- **scan market [参数] 第一个参数当 rule_name**（2026-07-23 修复）：parse_input（start.py）之前把 `scan market <参数>` 的参数全当 market_query（主题词），rule 固定 healthy_pullback。现第一个参数当 rule_name（resolve_rule_name 模糊匹配，失败才当主题词）。`scan market oversold_watch` 现走 oversold_watch 规则
- **pandas replace(0, pd.NA) 会变 object dtype 致 .round() 崩**（rsi_6_series bug，2026-07-23）：`_loss.replace(0, pd.NA)` 在 float Series 上变 object，`.round(2)` 对 NAType 报 `TypeError`。用 `_loss.replace(0, float('nan'))` + `.astype(float).round(2)` 保 float NaN。**测试 oversold_confirm 必须跑真实 calculate_indicators**（mock StockData 不暴露此 bug）
- **akshare 股东户数/融资余额 API 参数反直觉**（v0.8.6.8，LRN-20260812-001）：`stock_zh_a_gdhs(symbol)` 参数是**日期**(YYYYMMDD)非代码，传代码会 hang（em 端点反爬）。取单股股东户数用 `stock_zh_a_gdhs_detail_em(symbol=代码)`（列"股东户数-本次/上次"）。`stock_margin_detail_sse/szse` 参数是**单日期**返回全市场（无 start_date/stock_code），取单股需查日期+过滤代码。`AKShareClient._ensure_baostock_login` 不存在--是模块级函数 `from src.data.akshare_client import _ensure_baostock_login`；代码前缀用 `AKShareClient._normalize_stock_code` 类方法。新增 akshare 调用前先 `inspect.signature` 查参数，别按函数名猜
- **exit_signal 日缓存 + live 门控**（v0.8.6.8）：股东户数/融资余额信号 `stock.py:_check_holder_count_surge/_check_margin_surge` 用日缓存（`~/.muyun/exit_signal_cache/{code}_{date}_{signal}.json`，`_MISS` 哨兵区分未命中vs缓存None）。`check_stock_top_signal(live=...)`：回测 `is_backtest=True` 传 `live=False` 跳过 akshare（point-in-time+网络），orchestrator 传 `live=not is_backtest`。**回测路径不可触发 akshare 网络调用**。**宏观层同款门控（ISS-063，2026-08-23）**：`check_top_signals` 里 macro 层 `live=False` 且未显式传入成交额时必须跳过——`getattr(data,'market_turnover_trillion',None)` 恒为 None 会触发实时拉取全市场快照，拉取失败拖垮回测速度、拉取成功=未来信息注入。新增信号层先问'回测路径会不会碰网络'。`ThreadPoolExecutor` 超时后 `shutdown(wait=False)`（`wait=True` 会阻塞等孤儿线程）
- **告警人话化三处同步（v0.8.7.3，硬纪律）**：任何 logger.warning 新增/改动，**必须同一 commit 同步三处**--① `src/cli/plain_errors.py` 的 `WARNING_PATTERNS` 映射表（命中才翻译，未命中只透传不崩）② `docs/报错速查手册.md`（人话字典唯一详版）③ 触发该告警的代码本身。人话话术必须留余地（写"数据源慢或不可用"而非押单一成因），影响级别只准用"终止/缺失"两档
- **三倍定律气宗跳过**（v0.8.6.8 回测发现）：`_check_triple_up_rule`（price/low_60d≥3+破MA5）是 force-exit top_signal，气宗牛股被过早离场。`check_stock_top_signal(mode=...)`：气宗(`qizong`)跳过三倍定律（同 take_profit_trim 可压，气宗靠换手/减持/渗透率/旗手等真见顶信号逃顶），剑宗/非气宗(`mode!=qizong`，含None)照常触发

### 数据源

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

### Git

- **Git 操作需用 `cmd /c` 前缀**：PowerShell 直接执行 git 可能因中文路径出错
- **提交备注用中文详细描述**：每个 commit 写清楚改了什么、为什么改
- **推送前检查代理**：代理在线时 GitHub 推送会 Connection reset

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

### 已完成（v0.8.0 → v0.8.6.3）

- AI 调节层（技术面感知 + 新闻情绪）
- 全市场扫描（4 条规则 + 大盘风控 + 60 日趋势排序）
- 事件驱动预警 + **现象级事件四要素**（v0.8.6.3，真实性<30 信号一票否决 v0.8.6.8 强化）
- 四维排名 + RAG 策略知识检索（Recall@5=0.800）
- 买卖点精确触发（Chandelier/趋势破坏/止盈）
- 金字塔仓位管理（历史能力，已于 2026-07-17 拍板移除，commit 9b89ecd）
- Weinstein 四阶段分类（S1-S4）
- 买卖点分歧检测（技术 vs AI）
- 回测框架 + 分层对照 + 偏差审计
- **TradePlan 子系统 + PlanGuard**（v0.8.5）：`pos plan <code|all> [--update]` 生成/更新/批量
- **笨总 6 维 AI 评分 + 主题法C选股**（v0.8.6）：`bz`/`bz scan <主题>`/`bz --check`
- **气宗持有模式**（v0.8.6.3，ISS-033）：PlanGuard 压 trend_exit/take_profit_trim 拿住牛股（验证边际收益，指向"跳出现有思维"）
- **数据源连通性体检**（ISS-043）：`bz --check` 一键测全源
- **笨总当中长期决策主驾「跳法A」阶段1+2**（v0.8.6.4，ISS-046）：建仓笨总评分定气宗/剑宗 mode（笨总首次真正参与决策）+ 高位止盈3维度（`src/core/exit_signals/`，宏观成交额/个股换手缩量减持，PlanGuard P1 不可压）+ 气宗走固定长持参数（MarketState 降级为展示）
- **阶段4 五年回测验证通过**（v0.8.6.5，ISS-046）：12 股 × 2020-2024。**2026-08-23 安全网修复后全量复跑：牛股组平均 Δ +12.32pp 正向 8/8**（寒武纪从 -14.59pp 翻正到 +13.62pp），见顶/对照组不恶化；批次3（2026热门板块含 8/19 暴跌）qizong 5/5 正向平均 +17.3pp。明细见 ISSUES ISS-046 更新记录
- **景气度硬闸门 + 双通道选股 + 网络超时修复**（v0.8.6.4，ISS-047）：`effective_grade()` 让景气度成为真闸门；`bz scan` 双通道去龙头偏向；AI client 加 timeout
- **网络根治+缓存版本+风险维降级+截断优化**（v0.8.6.5，ISS-047 续）：akshare/baostock 三层线程级硬超时根治冻结；缓存版本校验杜绝"改代码不生效"；风险维新闻缺失改中性50；业务纯度 prompt 限 reasoning≤60字
- **笨总视频理念优化**（v0.8.6.8，来源 51 份视频总结）：宏观流动性状态 `assess_liquidity_state`(0.8/1.3/1.5万亿分档) + 市场宽度 `assess_market_breadth`(通杀/分化/普涨) advisory 展示；自主可控标的强制剑宗(`_mode_from_grade` is_self_reliance 闸门)；板块层 `sector.py`(渗透率30%魔咒+旗手滞涨baostock fetch)；个股层三倍定律(气宗跳过)+股东户数/融资余额激活(日缓存+live门控)；`pos overweight` 超配策略(三铁律)；永不满仓警告+弱势期+高位利好提示；AI标注旗手+渗透率。单股回测 sanity(宁德2020 Δ+68.64pp)未破坏气宗。详见 `docs/2026-08-10_笨总视频理念优化报告.md`+`docs/2026-08-12_未做事项评估.md`

### 待办（优先级排序）

详见 `ISSUES.md`。近期落地：ISS-053 建仓后基本面恶化硬退出（ST+业绩预亏，独立 fundamental_alert 通道）/ ISS-052 激活实控人减持公告 top_signal 子信号（live 生效，回测跳过）/ ISS-055 景气闸门降级+低置信度透明度警告（不动笨总公式）。待办重点：跳法A 阶段5（板块维度信号）；行业景气度维接入客观数据源（治本，撞数据可得性天花板，评估见 `docs/2026-08-24_景气度客观数据源评估.md`，决策待用户）。
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
| `ISSUES.md` | 所有问题追踪（ISS-001 ~ ISS-063）+ 顶部当前待办汇总 |
| `docs/archive/` | 历史版本文档归档（v0.7.x~v0.8.6.x 迭代规划/里程碑/旧交接，不再维护），索引见 `docs/archive/README.md` |
| `portfolio.yaml` | 当前持仓记录 |
| `src/scanner/scan_rules.yaml` | 扫描规则定义（healthy_pullback/steady_advance/shrink_pullback/value_pick/theme_members/oversold_watch超跌错杀观察） |
| `configs/settings.yaml` | 全局配置（API key 不在此，见 `configs/settings.local.yaml.example`） |
