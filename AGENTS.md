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

核心能力：全市场扫描、深度分析（含 Weinstein 阶段）、买卖点精确触发、金字塔仓位、回测框架、RAG 策略知识检索、**TradePlan 持久化交易计划**（v0.8.5）、**笨总 6 维 AI 自动评分 + 主题精准选股**（v0.8.6）、**气宗持有模式 + 事件四要素**（v0.8.6.3）。

当前版本：**v0.8.6.5**（ISS-047 续修：网络超时三层根治 + 风险维降级(新闻缺失中性50) + 缓存版本校验(改维度逻辑bump版本自动失效) + 业务纯度截断优化 + 新闻多源(巨潮备用)）。

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
configs/            # 配置文件（settings.yaml, position_tiers.yaml）
tests/              # 测试（含 rag_eval/ 评估模块）
docs/               # 详细文档
投资策略（持续更新）/ # 65+ 策略 txt/md → RAG 文档块（含笨总教学 .md）
```

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
- **Chandelier force_exit 不设 sell_path**（v0.8.6.3 修复）：orchestrator 里 Chandelier/trend_break 的 force_exit 经 strategy_layer 后 sell_path 会落空，已在 orchestrator 明确标 `trend_exit` 让 PlanGuard 气宗能匹配压制
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
- 事件驱动预警 + **现象级事件四要素**（v0.8.6.3，真实性低降级）
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

### 待办（优先级排序）

详见 `ISSUES.md`。重点：跳法A 阶段3（客观基本面恶化监测）/阶段4（2020-2024 五年回测验证 LRN-20260619-003）/阶段5（板块维度信号）；行业景气度维接入更多客观数据源（治本，撞数据可得性，ISS-047 已分析）。

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
| `ISSUES.md` | 所有问题追踪（ISS-001 ~ ISS-047） |
| `portfolio.yaml` | 当前持仓记录 |
| `src/scanner/scan_rules.yaml` | 扫描规则定义（healthy_pullback/steady_advance/shrink_pullback/value_pick/theme_members） |
| `configs/settings.yaml` | 全局配置（API key 不在此，见 `configs/settings.local.yaml.example`） |
