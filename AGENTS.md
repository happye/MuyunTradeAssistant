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

市场恐慌指数（v0.8.10，纯客观/零 AI）：
```
fear               # 一键总览：0-100 恐慌分（越高越恐慌）+ 7 成分溯源明细（原始值/数据源/状态）
fear history       # 近5日/10日/1个月/3个月回顾 + 走势图存 ~/.muyun/fear_index/charts/
fear backfill      # 涨停池历史回填（断点续传；东财仅保留约20-30个交易日，首次使用建议跑）
# chat 里问"市场恐慌吗/情绪如何"自动调 get_fear_index 工具；板块/个股下钻 P2/P3 上线
# 数据缺失显式标 MISSING 并剔除权重，绝不补假数据；分位样本不足自动降级固定阈值口径并标注
```

扫描复盘（v0.8.11，验证选股，零 AI）：
```
scan review [天数]  # 复盘近N天扫描历史（默认7）：每只票「扫描价→现价」累计涨跌 + 迷你走势列（▁▂▃▄▅▆▇，>10点均匀降采样保端点）
#                   #   + 沪深300同窗基准超额 + 涨跌胜率汇总 + 「扫描组 vs 沪深300」整体走势曲线；报告含逐日全量数值存 分析报告/scan/review_*.md
scan review import  # 一次性导入 v0.8.7~v0.8.10 的旧扫描报告（分析报告/scan/*.md，解析标题来源+文件名时间戳+明细字段），同(时间,来源)幂等跳过
#                   # 历史由 save_last_scan 收口自动落盘 ~/.muyun/scan_history.jsonl（scan market/bz scan/chat 三路全盖；v0.8.12.1 起全量保留不 prune，展示窗口由查询侧控制）
#                   # 反爬预算=批量行情1轮(60只/请求)+逐票日线各1次拉全窗口(A股历史无批量接口,逐票一次即下限;kline_cache当日缓存,同日重复零请求)+沪深300日线1次(当日缓存)；当天扫描不进统计
#                   # 窗口内无可评估记录但历史里有更早的 → 自动扩大到全部历史并明说（导入旧记录常超默认窗口）
```

观察池（v0.8.12，`l`/`la`/`l all`/chat 分析出 WATCH 且无持仓时自动入池）：
```
watch               # 复用 review 引擎看在池股「入池价→现价」涨跌+走势+沪深300对比；报告存 分析报告/scan/watch_*.md
watch add <代码|#N> # 手动入池（缺省拉实时价；已在池不重复）
watch rm <代码>     # 移出（唯一出池通道；移出后再分析 WATCH 会重新入池=新锚点价）
#                   # 存储 ~/.muyun/watchlist.jsonl 事件流（add/remove 重放得在池，同 scan_history 同构故 review 引擎直接吃）
#                   # scan review 表格「池」列标注在池股；人话摘要措辞随池状态变化（已放进/已在观察池/入池失败不承诺）
```

一键/批量（v0.8.7.1）：
```
暮云> la               # 一键分析所有持仓——每只一张「人话摘要」卡（该做什么/阶段/笨总评分），compact 模式
暮云> ba               # 对最近一次扫描（bz scan / scan market）结果批量笨总评分排名表
暮云> l all            # bz scan / scan market 出结果后接续用：批量深分析（v0.8.7.9），逐只 compact 简明卡
#                        当天已析股默认跳过（防重复花AI费用），l all -f 强制全部重析；失败股不记账、下次自动重试
# l 命令输出顶部现在带「📖 人话摘要」面板：白话结论+原因+趋势阶段+笨总评分+术语小词典；详细报告保留在下方不变
```

运行诊断（v0.8.16，零 AI 零网络）：
```
doctor / 诊断        # 只读环境体检：解释器/核心依赖/配置存在性/~/.muyun 状态缓存/RAG 知识库/报告目录
#                   # 缺失项如实标注「未生成/缺失」；settings.local.yaml 只报存在性，内容（API key）绝不回显
```

分析证据与对比（v0.8.17，plan/ C1）：
```
diff <代码> / 对比   # 同股最近两次深分析的关键证据比较：价格/评分 Δ、决策与仓位动作变化、
#                    # 技能信号转向/新增/消失、新增数据缺口提示——只列变化项
#                    # 数据源：l/la/l all/chat 每次分析自动追加 ~/.muyun/analysis_evidence.jsonl（追加式不 prune）
#                    # 同步落人话证据卡 分析报告/analysis/{时间}_{代码}_{名字}.md（同分钟 _2 防覆盖）
#                    # 口径纪律：证据字段语义跨版本稳定，旧记录缺字段视为"当时未记录"不误报；评分口径变化由 CACHE_VERSION 标注
```

批量任务账本（v0.8.17，plan/ C2）：
```
tasks / 任务         # 最近批量任务（l all/la/ba/l 多代码）的进度与失败项——中断续跑可见
#                    # 重跑同类命令即续跑：成功项由当日缓存复用（既有语义），失败项自动重试
#                    # 账本 ~/.muyun/batch_tasks.json 原子写（保留最近 5 个任务）
```

依赖安装（事实源 = `requirements.txt`，钉版本的已验证清单，2026-06-24 实测口径）：
```
# 推荐：uv 建 venv 后按清单安装
uv venv .venv
uv pip install -r requirements.txt

# 或标准 venv + pip
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Python 3.11+。注意：仓库**没有 pyproject.toml**，`uv sync` 不可用（plan/ M6 已把全部文档的安装入口统一到 requirements.txt，旧文档写 uv sync 是错的）；依赖版本不盲目升级，升级须走独立验证。

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

当前版本：**v0.8.19**（融合架构 F0–F9 已全部实施，2026-09-25）：`today` 今日工作台、`pos confirm` 成交确认、持仓事实与建议分离（分析不改持仓）、终态统一输出（摘要/diff/排名）、证据快照 PIT 资格、三路候选池与因子登记、周期决策表（fusion_mid/long，未接入生产）、claim 事件谱系、组合预算求解、实验矩阵与回放基建、五阶段发布阶梯（当前 capture_only）。
测试 811→1031。**版本逐批叙事**（v0.8.15–v0.8.19）已迁入 `docs/archive/AGENT版本叙事归档_v0.8.15-v0.8.19_2026-09.md`；用户可感知变更清单见 README「版本历史」；**F 批实施账本与交接入口 = `plan/fusion/EXECUTION_RECORD.md`**（F0–F9 全部 VERIFIED；待办：E0–E7 实验执行/影子运行/用户走查，均需数据或用户参与，见账本未完成条款）。

> **v0.8.14 及更早的版本叙事已迁入归档**：`docs/archive/AGENT版本叙事归档_截至v0.8.14_2026-09.md`（用户可感知的变更清单见 README.md「版本历史」；跨版本仍生效的硬约束以本文件 §五 为准）。

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
- **网络超时三层硬保护**（ISS-047，根治 WinError 10060/10054）：akshare/baostock 底层 requests 无 timeout 会挂起。`data_provider._safe_call`(30s) + `akshare_client._retry_with_backoff`(30s) + baostock `bs.next()` 读取(30s) 全用 `ThreadPoolExecutor` 线程级硬超时，超时即放弃走降级不冻结。新增网络调用必须包超时；且**不能用 with 块包 ThreadPoolExecutor**——__exit__ 的 shutdown(wait=True) 会 join 卡死线程使超时失效，须显式 shutdown(wait=False)（2026-08-24 ISS-066 审查发现 _safe_call/_call_with_timeout 两处中招已修，范本 source_check._probe_t）
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
- **exit_signal 日缓存 + live 门控**（v0.8.6.8）：股东户数/融资余额信号 `stock.py:_check_holder_count_surge/_check_margin_surge` 用日缓存（`~/.muyun/exit_signal_cache/{code}_{date}_{signal}.json`，`_MISS` 哨兵区分未命中vs缓存None）。`check_stock_top_signal(live=...)`：回测 `is_backtest=True` 传 `live=False` 跳过 akshare（point-in-time+网络），orchestrator 传 `live=not is_backtest`。**回测路径不可触发 akshare 网络调用**。**宏观层同款门控（ISS-063，2026-08-23）**：`check_top_signals` 里 macro 层 `live=False` 且未显式传入成交额时必须跳过——`getattr(data,'market_turnover_trillion',None)` 恒为 None 会触发实时拉取全市场快照，拉取失败拖垮回测速度、拉取成功=未来信息注入。新增信号层先问'回测路径会不会碰网络'。`ThreadPoolExecutor` 超时后 `shutdown(wait=False)`（`wait=True` 会阻塞等孤儿线程）
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
| `ISSUES.md` | 所有问题追踪（ISS-001 ~ ISS-098）+ 顶部当前待办汇总 |
| `docs/archive/2026-09-25_交接_C批次实施与F系列接手.md` | 历史交接（已归档）：M/C 批次完成 + F 系列接手指引——**现行交接入口 = `plan/fusion/EXECUTION_RECORD.md`** |
| `docs/archive/2026-09-24_交接_扫描复盘观察池与架构师迭代计划.md` | 历史交接（已归档）：scan review/观察池交付明细 |
| `plan/` | 架构师迭代计划（M1–M6 与 fusion **F0–F9 已全部实施 VERIFIED**；**F 批实施账本与交接入口 = `plan/fusion/EXECUTION_RECORD.md`**，发布阶梯 `ROLLOUT.md`，实验计划 `EXPERIMENTS.md`，数据资格 `DATA_COVERAGE.md`） |
| `tests/README.md` | **v0.8.13 新增**：测试目录说明与跑法（离线默认 `pytest -q`/外源与 AI 显式启用/用户目录隔离机制） |
| `docs/archive/` | 历史版本文档归档（v0.7.x~v0.8.6.x 迭代规划/里程碑/旧交接 + 2026-09 迁出的 AGENTS 版本叙事归档，不再维护），索引见 `docs/archive/README.md` |
| `portfolio.yaml` | 当前持仓记录 |
| `src/scanner/scan_rules.yaml` | 扫描规则定义（healthy_pullback/steady_advance/shrink_pullback/value_pick/theme_members/oversold_watch超跌错杀观察） |
| `configs/settings.yaml` | 全局配置（API key 不在此，见 `configs/settings.local.yaml.example`） |
