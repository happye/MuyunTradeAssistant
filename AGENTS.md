# 暮云思辨投资助手 — AI Agent 开发手册

> 任何 AI 编码工具打开此项目时，请先阅读本文件。
> 详细文档索引见末尾。

---

## 一、怎么跑

```
uv run python start.py          # 交互式 REPL（推荐日常使用）
python -m src.cli.main -l 600519  # CLI 模式：分析单只股票
```

笨总评分 + 主题选股（v0.8.6）：
```
bz 600519            # 6 维 AI 自动评分（每维 1 次 AI，约 30-60s）
bz 600519 --refresh  # 强制刷新跳过缓存
bz scan 氮化镓,钽电容 # 法C 主题精准定位+笨总评分排名（细分赛道也能精准）
bz --check           # 数据源连通性体检
bz --manual          # 旧交互式手动打分（兜底）
```

预期事件日历（v0.8.7，事前预期透支）：
```
暮云> expect           # 未来30天事件日历 + 预期透支度面板（环境温度计）
暮云> expect 60        # 自定义天数
# 详见 使用手册.md「预期事件日历」章节；与 events（事后）互补
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

核心能力：全市场扫描、深度分析（含 Weinstein 阶段）、买卖点精确触发、金字塔仓位、回测框架、RAG 策略知识检索、**TradePlan 持久化交易计划**（v0.8.5）、**笨总 6 维 AI 自动评分 + 主题精准选股**（v0.8.6）、**气宗持有模式 + 事件四要素**（v0.8.6.3）、**笨总视频理念优化：板块层信号+三倍定律+超配+市场宽度+自主可控强制剑宗**（v0.8.6.8）。

当前版本：**v0.8.6.8**（笨总 51 份视频理念优化：宏观流动性状态/真实性一票否决/自主可控强制剑宗/板块层渗透率+旗手/三倍定律/超配策略/市场宽度/股东户数+融资余额激活；详见 `docs/2026-08-10_笨总视频理念优化报告.md` + `docs/2026-08-12_未做事项评估.md`）。

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
| `tests/ui/` | Web/TUI 界面测试（flask test client，mock 引擎） | test_web / test_tui |
| `tests/artifacts/` | 历史回测/issue 验证的输出产物（json/log，非脚本） | extended_backtest_2024* / issue_033_round* |
| `tests/rag_eval/` | RAG 检索质量评估工具 | evaluator.py + relevance_labels |

跑法：`pytest tests/ -q --ignore=tests/artifacts --ignore=tests/rag_eval` 全量；单文件 `.\.venv\Scripts\python.exe tests\chat	est_xxx.py`（脚本自带 sys.path 修复）；网络类脚本先看文件头注释。

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
- **修改 YAML 键名时，追踪所有硬编码引用**：如 scan_rules.yaml 的 `default` 改名时，main.py/start.py/scanner_engine.py 中有 8 处硬编码需要同步
- **RAGDocument 字段名是 `content`，不是 `text`**
- **StrategyDecision.entry_exit 是 dict，不是 EntryExitResult 对象**：通过 `ee.get("key")` 访问
- **StockData 的 MA 字段通过 `dr.stock` 访问**：`_weinstein_stage()` 需要 StockData，不是 StrategyDecision
- **回测路径必须显式从 `settings.yaml` 读取并传 `entry_exit_config` / `pyramid_config` 给 `BacktestEngine`**：CLI 默认参数不会兜底，缺传会让买卖点/金字塔仓位**整体失效**（ISS-032 已修复，用 `load_pyramid_config()` helper 加载）
- **调策略卖出参数前先打 `trade['sell_path']`**：回测中实际卖出走的是 `src/core/strategy_layer.py` 的 `_infer_sell_path`（`take_profit_trim` / `trend_exit` / `weak_sell` / `stop_loss_*`），不是 `src/core/entry_exit/exit_rules.py`。两者各管一半，不能互相替代（详见 `.learnings/LEARNINGS.md` LRN-20260618-003）
- **execution_layer 只读 `position_action`（不读 decision/sell_path，无 STOP 概念）**（审查 P0/P1a 修复）：PlanGuard 不可压规则（4 致命止损/4.5 fundamental_alert/P1 top_signal/3 时间止损）+ EntryExit force_exit 救回**必须设 `position_action=CLOSE_ALL`**，否则 strategy_layer 输出 HOLD 时安全网静默失效。规则3/4 原只设 `decision=SELL` 致穿止损/到期不卖（已修，设 CLOSE_ALL+sell_path）；Chandelier force_exit 被 strategy_layer 5机制降级为 HOLD 也丢失（P1a：orchestrator 在 strategy_layer 后重新断言 SELL+CLOSE_ALL+trend_exit 覆盖降级）。**新增 PlanGuard 规则/force_exit 救回别忘设 position_action**
- **`StockData.recent_announcements` 只在 live builder 填充，回测 builder 绝不可填**（ISS-052）：该字段供 top_signal 实控人减持子信号用，由 `calculate_indicators`（live，`get_stock_data` 别名）填充；回测走独立的 `DataFeeder._build_stock_data`（不调 calculate_indicators）保持 None -> 减持子信号回测跳过（公告是 akshare"最近N天"接口，非 point-in-time，回测取会拿未来公告前瞻）。**别为"完整性"给 DataFeeder 也填公告**--会注入未来信息污染回测。回测安全靠两套 builder 隔离，无 `is_backtest` 开关；若要回测验证减持信号须先备 point-in-time 历史公告数据源
- **回测调 `orchestrator.analyze` 必须传 `is_backtest=True`**（ISS-053 审查修复）：`fundamental_alert`（被ST/业绩预告预亏）走 baostock **当前**数据，非 bar 时点 point-in-time--回测里未来预亏预告会在第一根持仓 bar 即触发退出（前瞻偏差）。`is_backtest=True` 守卫跳过整个 fundamental_alert（live 不变，仍启用）。**新增回测调用点别忘了传**，否则回测被未来数据污染。回测路径只有 `backtest_engine.py:576`（已传）；`backtest_validator` has_position 默认 False 不触发
- **笨总评分体系的缓存键是 `(stock_code, date, dimension)`**：同股同日同维度只算一次 AI（`src/core/benzong/cache.py`，存 `~/.muyun/benzong_cache/`）。**改了任何维度评分逻辑（prompt/降级/公式）必须 bump `CACHE_VERSION`**（cache.py 顶部），否则旧缓存命中导致"改代码不生效"——get() 读旧版本自动当未命中。`bz <code> --refresh` 可单股强刷
- **bz 不依赖 RAG**（v0.8.6.3）：笨总评分标准内嵌 SYSTEM_PROMPT，industry_prosperity 不再调 RAG。RAG 仍用于 scan/analysis 的事件/持仓策略增强
- **curl_cffi SSL 证书路径修复**（v0.8.6.3，ISS-045）：项目路径含中文「暮云思辨投资助手」致 curl_cffi 的 libcurl 找不到 CA 证书（curl 77）。修复在 `src/data/source_check.py:fix_curl_ssl_paths`（公共函数），data_provider/news_client 共用，启动时把 certifi 证书复制到 ASCII 路径 `~/.muyun_cacert.pem`
- **RAG ingestion 用 `rglob` 扫 `.txt` + `.md`**（`src/rag/ingestion.py`）：新增策略文档放子目录也会被扫到
- **AI 评分 JSON 截断容错**（v0.8.6.5）：`_call_ai_for_score(max_tokens=)` 按维度可调（业务纯度 2000，其余 1500）+ finish_reason==length 自动重试 + `_parse_score_json` 容错截断。截断退化 confidence=0.3（不再假装 0.5）。维度 prompt 须显式限 reasoning 长度防超 max_tokens
- **网络超时三层硬保护**（ISS-047，根治 WinError 10060/10054）：akshare/baostock 底层 requests 无 timeout 会挂起。`data_provider._safe_call`(30s) + `akshare_client._retry_with_backoff`(30s) + baostock `bs.next()` 读取(30s) 全用 `ThreadPoolExecutor` 线程级硬超时，超时即放弃走降级不冻结。新增网络调用必须包超时
- **笨总等级展示/决策一律用 `effective_grade()` 不用 `grade()`**（ISS-047）：`grade()`/`total_score` 是 Excel 保真值（试金石 7/7 锁死，不能改）；`effective_grade()`（景气度=0判F/≤30最高C/≤50最高B）+ `normalized_score()`（÷1.44 归一化到100）才是展示/排序/定 mode 用的。改了等级逻辑必须改 effective_grade 而非 grade
- **建仓笨总评分一次定 mode，更新不重跑**（跳法A 阶段1）：`pos add`/回测建仓时笨总 grade→`generator._mode_from_grade`（A→气宗180天/B→剑宗30天）。`pos plan --update` 由旧 mode 反推 grade 不重跑评分（避免纪律摇摆）。回测走 `backtest_engine._resolve_benzong_mode`（rule_scorer 规则版，带 code 缓存）
- **AI client 必须设 timeout**（ISS-047）：`auto_scorer._build_ai_client` 已加 `timeout=60`+`max_retries=2`，防 bz scan 长批量挂起到 WinError 10060/10054。新写 OpenAI client 别忘 timeout
- **bz scan 双通道选股**（ISS-047）：法C（theme_locator AI 报股，细分材料）+ 全市场客观筛选（`quick_scan(market_query=)` 走 THS 成分股，含中小盘）。提示词已去龙头偏向，别再加"宁可少报"类措辞
- **DeepSeek-V4 模型迁移 + thinking=disabled**（2026-07-21）：`deepseek-chat` 2026-07-24 弃用 -> `deepseek-v4-flash`（`configs/settings.yaml` model）。V4 默认思考模式，保持非思考需调用处传 `extra_body={"thinking":{"type":"disabled"}}`，判断用 `model.startswith("deepseek-v4")`（7 处：chat/agent、ai_modifier、benzong dimensions/theme_locator、event_layer×2、source_check、scanner_engine）。回退旧 deepseek-chat 会因 thinking 参数不认报错
- **chat analyze_stock 必须回写 strategy_state**（H1 修复，2026-07-21）：与 CLI 一致，调完 `_orchestrator.analyze` 后回写 `pm.update_from_strategy_decision`（仅持仓股，非持仓回写会创建虚假记录）。`portfolio.update_from_strategy_decision` 已修保留 `high_since_entry`（取已存值/entry_exit 算的/当前价三者大值，CLI 也受益）。chat 层 max_tokens 拉满 384K（V4 输出上限）+ finish_reason=length 截断检测
- **scan market [参数] 第一个参数当 rule_name**（2026-07-23 修复）：parse_input（start.py）之前把 `scan market <参数>` 的参数全当 market_query（主题词），rule 固定 healthy_pullback。现第一个参数当 rule_name（resolve_rule_name 模糊匹配，失败才当主题词）。`scan market oversold_watch` 现走 oversold_watch 规则
- **pandas replace(0, pd.NA) 会变 object dtype 致 .round() 崩**（rsi_6_series bug，2026-07-23）：`_loss.replace(0, pd.NA)` 在 float Series 上变 object，`.round(2)` 对 NAType 报 `TypeError`。用 `_loss.replace(0, float('nan'))` + `.astype(float).round(2)` 保 float NaN。**测试 oversold_confirm 必须跑真实 calculate_indicators**（mock StockData 不暴露此 bug）
- **akshare 股东户数/融资余额 API 参数反直觉**（v0.8.6.8，LRN-20260812-001）：`stock_zh_a_gdhs(symbol)` 参数是**日期**(YYYYMMDD)非代码，传代码会 hang（em 端点反爬）。取单股股东户数用 `stock_zh_a_gdhs_detail_em(symbol=代码)`（列"股东户数-本次/上次"）。`stock_margin_detail_sse/szse` 参数是**单日期**返回全市场（无 start_date/stock_code），取单股需查日期+过滤代码。`AKShareClient._ensure_baostock_login` 不存在--是模块级函数 `from src.data.akshare_client import _ensure_baostock_login`；代码前缀用 `AKShareClient._normalize_stock_code` 类方法。新增 akshare 调用前先 `inspect.signature` 查参数，别按函数名猜
- **exit_signal 日缓存 + live 门控**（v0.8.6.8）：股东户数/融资余额信号 `stock.py:_check_holder_count_surge/_check_margin_surge` 用日缓存（`~/.muyun/exit_signal_cache/{code}_{date}_{signal}.json`，`_MISS` 哨兵区分未命中vs缓存None）。`check_stock_top_signal(live=...)`：回测 `is_backtest=True` 传 `live=False` 跳过 akshare（point-in-time+网络），orchestrator 传 `live=not is_backtest`。**回测路径不可触发 akshare 网络调用**。`ThreadPoolExecutor` 超时后 `shutdown(wait=False)`（`wait=True` 会阻塞等孤儿线程）
- **三倍定律气宗跳过**（v0.8.6.8 回测发现）：`_check_triple_up_rule`（price/low_60d≥3+破MA5）是 force-exit top_signal，气宗牛股被过早离场。`check_stock_top_signal(mode=...)`：气宗(`qizong`)跳过三倍定律（同 take_profit_trim 可压，气宗靠换手/减持/渗透率/旗手等真见顶信号逃顶），剑宗/非气宗(`mode!=qizong`，含None)照常触发

### 数据源

- **新浪数据源无 `量比` 和 `60日涨跌幅`**：scan_rules.yaml 中含这些字段的过滤器会静默跳过（scanner_filter.py L118）
- **60 日涨跌幅由 `_enrich_trend_data()` 后补**：在 quick_scan 初筛后通过 Baostock 逐只计算，仅候选 ≤50 只时执行
- **eFinance 有量比但无市净率和 60 日涨跌幅**
- **AKShare `stock_zh_a_spot_em()` 有完整字段但需 4 分钟**：已废弃，改用新浪+eFinance
- **Baostock preclose 字段仅日线支持**：周线/月线查询时 fields 字符串不含 preclose

### 网络

- **系统代理 `127.0.0.1:7890` 会干扰东方财富 API 和 GitHub**：金融数据需 `_without_proxy()` 绕过；Git 推送失败时 `git -c http.proxy= push`
- **THS 板块 API 可能超时**：`get_stocks_by_industry/concept` 有 15 秒超时保护

### Git

- **Git 操作需用 `cmd /c` 前缀**：PowerShell 直接执行 git 可能因中文路径出错
- **提交备注用中文详细描述**：每个 commit 写清楚改了什么、为什么改
- **推送前检查代理**：代理在线时 GitHub 推送会 Connection reset

---

## 六、开发习惯与维护流程

遵循全局开发工作流 skill：`$dev-flow`

核心流程：**Plan → Diagnose → Implement → Verify → Sync → Ship**

详细规范（六步法、知识管理、业界最佳实践、反模式）见 `$dev-flow` skill。

> 脚注：`$dev-flow` 是用户级全局 skill，不在本仓库内；仓库内最相近的是 `.github/skills/self-improvement/SKILL.md`。

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
- 金字塔仓位管理
- Weinstein 四阶段分类（S1-S4）
- 买卖点分歧检测（技术 vs AI）
- 回测框架 + 分层对照 + 偏差审计
- **TradePlan 子系统 + PlanGuard**（v0.8.5）：`pos plan <code|all> [--update]` 生成/更新/批量
- **笨总 6 维 AI 评分 + 主题法C选股**（v0.8.6）：`bz`/`bz scan <主题>`/`bz --check`
- **气宗持有模式**（v0.8.6.3，ISS-033）：PlanGuard 压 trend_exit/take_profit_trim 拿住牛股（验证边际收益，指向"跳出现有思维"）
- **数据源连通性体检**（ISS-043）：`bz --check` 一键测全源
- **笨总当中长期决策主驾「跳法A」阶段1+2**（v0.8.6.4，ISS-046）：建仓笨总评分定气宗/剑宗 mode（笨总首次真正参与决策）+ 高位止盈3维度（`src/core/exit_signals/`，宏观成交额/个股换手缩量减持，PlanGuard P1 不可压）+ 气宗走固定长持参数（MarketState 降级为展示）
- **阶段4 五年回测验证通过**（v0.8.6.5，ISS-046）：12 股 × 2020-2024，牛股组 8 只平均 Δ +8.13pp（7/8 正向），见顶/对照组不恶化。相比 ISS-033 的 +2.46pp 边际收益是质变级证据。反例寒武纪 -14.59pp 暴露高波动题材股该定剑宗（后续优化方向）
- **景气度硬闸门 + 双通道选股 + 网络超时修复**（v0.8.6.4，ISS-047）：`effective_grade()` 让景气度成为真闸门；`bz scan` 双通道去龙头偏向；AI client 加 timeout
- **网络根治+缓存版本+风险维降级+截断优化**（v0.8.6.5，ISS-047 续）：akshare/baostock 三层线程级硬超时根治冻结；缓存版本校验杜绝"改代码不生效"；风险维新闻缺失改中性50；业务纯度 prompt 限 reasoning≤60字
- **笨总视频理念优化**（v0.8.6.8，来源 51 份视频总结）：宏观流动性状态 `assess_liquidity_state`(0.8/1.3/1.5万亿分档) + 市场宽度 `assess_market_breadth`(通杀/分化/普涨) advisory 展示；自主可控标的强制剑宗(`_mode_from_grade` is_self_reliance 闸门)；板块层 `sector.py`(渗透率30%魔咒+旗手滞涨baostock fetch)；个股层三倍定律(气宗跳过)+股东户数/融资余额激活(日缓存+live门控)；`pos overweight` 超配策略(三铁律)；永不满仓警告+弱势期+高位利好提示；AI标注旗手+渗透率。单股回测 sanity(宁德2020 Δ+68.64pp)未破坏气宗。详见 `docs/2026-08-10_笨总视频理念优化报告.md`+`docs/2026-08-12_未做事项评估.md`

### 待办（优先级排序）

详见 `ISSUES.md`。近期落地：ISS-053 建仓后基本面恶化硬退出（ST+业绩预亏，独立 fundamental_alert 通道）/ ISS-052 激活实控人减持公告 top_signal 子信号（live 生效，回测跳过）/ ISS-055 景气闸门降级+低置信度透明度警告（不动笨总公式）。待办重点：跳法A 阶段5（板块维度信号）；行业景气度维接入客观数据源（治本，撞数据可得性天花板，ISS-055 已评估）；换手率子信号 fetcher（ISS-052 gap）；宏观 10 万亿阈值笨总拍板。

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
| `docs/v0.8.3_里程碑.md` | 里程碑进度（含 v0.8.4 / v0.8.5 收尾） |
| `docs/v0.8.3_迭代规划.md` | v0.8.3 五个优化方向完整设计 |
| `docs/v0.8.5_扩样本验证报告.md` | 30 股沪深 300 回测：9 股非不利样本，是策略哲学问题 |
| `docs/v0.8.5_阶段3_final.md` | v0.8.5 阶段 3 收尾：5 轮调参累计 +1.4pp，核心瓶颈在选股能力 |
| `docs/v0.8.6_调研报告.md` | 笨总超景气价值投机体系融合架构 + 4 阶段路线 + 4 决策点 |
| `docs/笨总6维评分_说明文档.md` | **v0.8.6.3 新增**：笨总 6 维评分器工作原理/数据来源/降级策略/命令用法/输出解读 |
| `docs/选股与持仓规划实战工作流.md` | **v0.8.6.5 新增**：端到端用法主线（选股→评分→建仓→持有→离场），含不同市况用法 |
| `docs/v0.8.6.3_交接.md` | **v0.8.6.3 交接文档**：笨总框架完工度/边际结论/跳法A方向决策（跳法A 阶段1+2 已落地，见 ISSUES ISS-046/047） |
| `docs/实盘操作指南.md` | 回测验证框架、参数调优方法论 |
| `ISSUES.md` | 所有问题追踪（ISS-001 ~ ISS-056） |
| `portfolio.yaml` | 当前持仓记录 |
| `src/scanner/scan_rules.yaml` | 扫描规则定义（healthy_pullback/steady_advance/shrink_pullback/value_pick/theme_members/oversold_watch超跌错杀观察） |
| `configs/settings.yaml` | 全局配置（API key 不在此，见 `configs/settings.local.yaml.example`） |
