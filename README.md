# 暮云思辨投资助手

AI驱动的A股交易行为约束系统 - 基于规则引擎的投资策略系统

> 🧭 **AI Agent 入口**：所有 AI 编码工具（Claude Code / Copilot / Codex 等）打开本仓库时，请先阅读 `AGENTS.md` 为单一事实源（架构、约束、踩坑、当前状态）。本文件偏向人类读者的项目概述。

## 项目状态

**v0.8.22 第二轮R7/R8已实施** ✅ (E矩阵manifest v2 + E0b/E7b受控场景 + research单股研究工作台；版本历史见下)

> 📖 **里程碑与阶段详情请参阅 [历史里程碑](docs/archive/v0.8.3_里程碑.md)（已归档，现行状态见 ISSUES.md）**
> 📖 **案例验证报告请参阅 [C4 案例验证](docs/archive/v0.8.3_C4_案例验证报告.md)（已归档）**

> 📖 **使用方法请参阅 [使用手册.md](使用手册.md)**
> 📖 **AI系统详解请参阅 [AI系统说明.md](docs/AI系统说明.md)** — AI的角色、影响范围和可控性
> 本文档仅包含架构设计、技术实现与版本历史。

## v0.8.x 技术边界

v0.8.x 的职责拆分如下：

- `README.md`：说明架构、模块关系、技术边界、导出结构与版本口径。
- `使用手册.md`：说明怎么运行、怎么看输出、哪些行为会直接影响日常使用。
- 历史版本文档已移入 `docs/archive/`（索引见 `docs/archive/README.md`）；现行状态以 `ISSUES.md` 为准。

Phase 5 在开发侧新增了三类核心逻辑：

- 持仓窗口显式化：策略状态不再借生命周期间接表达限制，而是独立维护 `min_hold_remaining` 与 `add_protection_remaining`。
- 卖出语义显式化：策略层把减仓、止损、趋势退出、弱卖出拆成独立 `sell_path`，并统一映射到 `action_semantic`。
- 导出链路保真化：回测记录、CLI 展示、组合持久化、报告归因优先使用显式语义，而不是依赖事后字符串回推。

Phase 6 在开发侧完成了真实多时间框架接入：

- 周线/月线数据真正进入 `StockData.weekly` 与 `StockData.monthly`，不再只停留在模型占位。
- `multi_timeframe` 技能不再用日线 `MA5/MA20/MA60` 近似月周，而是按“月线方向过滤、周线位置确认、日线触发执行”工作。
- 实时分析与回测重放共享这套时间框架摘要，避免实盘分析和历史回测在多周期逻辑上出现两套口径。

Scanner 近期改成了统一主题词扫描：

- `quick_scan` 现在保留一个主入口：规则名优先匹配；若未命中规则，则把输入当成主题词，自动匹配相关行业和概念。
- 主题词支持单个和多个，多个主题必须使用英文逗号 `,` 分隔，例如 `AI,半导体,机器人`。
- 行业与概念不再要求用户手动区分，匹配到的板块成分股按并集筛选；若 AI 可用，会在本地模糊匹配之后再做一次名称纠偏。

买卖点显示口径在 v0.8.3 之后统一为：

- 单股分析（`--live/-l`）显示完整买卖点区块（触发或无触发）。
- 持仓扫描（`--portfolio/-p`）显示每只股票的触发行摘要（触发才显示）。
- 持仓场景下卖点计算依赖 `has_position` 与 `entry_price` 上下文，缺失时将只能执行入场侧判断。

---

## 项目结构

```
暮云思辨投资助手/
├── src/
│   ├── core/                    # 核心引擎
│   │   ├── skill_engine.py      # YAML规则执行器 + 条件注册表
│   │   ├── decision_engine.py   # 信号聚合器（投票→调节→覆盖+追溯）
│   │   ├── event_layer.py       # 事件驱动层（关键词匹配+AI分类+持仓扫描）⭐v0.8.0
│   │   ├── entry_exit/             # 买卖点精确触发模块 ⭐v0.8.3 (Chandelier/ATR/海龟)
│   │   ├── ranking_layer.py     # 排名引擎（四维评分+权重归一化+TOP3推荐）⭐v0.8.0
│   │   ├── position_tier.py       # 金字塔仓位管理 ⭐v0.8.3 (试探→基础→重仓)
│   │   ├── ai_modifier.py       # AI调节层（新闻→情绪→信号调节）⭐v0.8.0
│   │   ├── strategy_layer.py    # 策略层（交易生命周期+持有窗口+卖出语义拆分）⭐v0.8.2
│   │   ├── execution_layer.py   # 执行层（波动率滑点+流动性+涨跌停+冲击成本）⭐v0.7.2
│   │   ├── backtest_engine.py   # 回测引擎（模拟账户+统计指标+Monte Carlo+稳定性）
│   │   └── orchestrator.py      # 编排层（七层架构：Signal→Decision→Event→AI→Strategy→Execution）
│   ├── chat/                    # 对话式智能助手 ⭐v0.8.0
│   │   ├── agent.py             # ChatAgent核心（function calling+REPL主循环）
│   │   ├── tools.py             # 11个工具函数（引擎映射+命令桥）+ TOOL_REGISTRY
│   │   ├── formatter.py         # 结构化数据→纯文本格式化器
│   │   ├── prompts.py           # 系统提示词 + 11个工具JSON Schema定义
│   │   └── __init__.py          # 包初始化
│   ├── scanner/                 # 全市场扫描模块 ⭐v0.8.0
│   │   ├── market_cache.py      # 全市场行情缓存（新浪/efinance/过期缓存3层降级）
│   │   ├── scanner_engine.py    # 扫描引擎（初筛quick_scan + 深度分析deep_analyze）
│   │   ├── scanner_filter.py    # 声明式过滤器（field/op/value三元组，YAML可配置）
│   │   ├── scan_rules.yaml      # 扫描规则配置（6条规则+全局排除）
│   │   └── event_rules.yaml     # 事件触发规则（9条：政策/地缘/财报/黑天鹅/暴跌）⭐v0.8.0
│   ├── data/
│   │   ├── models.py            # Pydantic数据模型（含回测+策略层+执行层+AI调节层+事件层+排名层模型）
│   │   ├── akshare_client.py    # 多数据源客户端（Baostock+AKShare）
│   │   ├── news_client.py       # 新闻数据客户端（个股+宏观，内存缓存）⭐v0.8.0
│   │   ├── portfolio.py         # 持仓管理（portfolio.yaml持久化+Y/N交互）⭐v0.7.2
│   │   └── data_feeder.py       # 历史数据回放器（回测专用）
│   ├── skills/                  # 策略技能 (YAML定义)
│   │   ├── ma_trend.yaml        # 均线趋势
│   │   ├── volume_price.yaml    # 量价验证
│   │   ├── support_resistance.yaml  # 支撑阻力
│   │   ├── stop_loss.yaml       # 止损管理（第48/54章）
│   │   ├── multi_timeframe.yaml # 多周期共振
│   │   ├── macd.yaml            # MACD趋势
│   │   ├── rsi.yaml             # RSI动量
│   │   ├── boll.yaml            # 布林带
│   │   ├── kdj.yaml             # KDJ随机指标
│   │   ├── position_context.yaml  # 位置上下文（第41章）
│   │   ├── market_context.yaml    # 大盘环境（第42/44章）
│   │   ├── smart_money.yaml       # 主力行为（第46/47章）
│   │   └── take_profit.yaml       # 止盈管理（第49章）
│   └── cli/
│       └── main.py              # CLI入口（含回测+策略层+执行层+Chat模式展示）
├── configs/
│   └── settings.yaml            # 全局配置（含技能分类）
├── tests/
│   ├── test_conditions.py       # 参数化条件单元测试
│   ├── test_index_data.py       # 大盘数据可行性测试
│   ├── test_tech_context_e2e.py # 技术面摘要端到端测试 ⭐v0.8.3
│   ├── test_entry_exit/         # 买卖点模块测试套件 ⭐v0.8.3
│   └── rag_eval/                # RAG检索质量评估 ⭐v0.8.3
├── requirements.txt
├── sample_data.json             # 示例数据（含完整技术指标）
├── ISSUES.md                    # 问题追踪与待办
└── 使用手册.md                   # 详细使用手册
```

---

## 数据源架构

本项目采用多数据源降级策略，确保数据获取的稳定性：

| 数据类型 | 主数据源 | 备用数据源 | 说明 |
|---------|---------|-----------|------|
| 全市场行情(Scanner) | **新浪财经API** | efinance（东方财富） | 新浪~5秒获取5511只A股，efinance因IP封禁常失败 ⭐v0.8.0 |
| 单股实时行情 | **Baostock** | 东方财富/新浪 | Baostock稳定可用，东方财富IP可能被封 |
| 历史K线 | **Baostock** | 东方财富(AKShare) | Baostock直接取交易所数据 |
| 大盘指数 | **Baostock** | - | 沪深300(sh.000300) MA20/MA60趋势 |
| 个股新闻 | **AKShare** | - | stock_news_em()，同会话缓存 ⭐v0.8.0 |
| 宏观快讯 | **AKShare** | - | stock_info_global_em()，1小时缓存 ⭐v0.8.0 |
| 技术指标 | 本地pandas计算 | - | MA/MACD/RSI/布林带/KDJ |

> ⚠️ **注意**：Baostock实时行情有30秒-1分钟延迟，非盘中实时报价。

---

## 13个技能体系

### 技能分类（ISS-001 信号去重）

技能分为三种类型，避免信号重复计数：

| 类型 | 说明 | 技能 |
|------|------|------|
| **base** | 独立信号源，产出BUY/SELL/HOLD | 8个技术指标技能 |
| **regulator** | 上下文调节器，修改base信号权重 | 3个环境/位置/主力技能 |
| **action** | 动作信号，拥有优先级覆盖能力 | 止损+止盈 |

### Phase 1 - 基础技能（base）

| 技能 | 功能 | 信号 |
|------|------|------|
| ma_trend | 均线多空排列判断 | BUY/HOLD/SELL |
| volume_price | 量价关系验证 | BUY/SELL/HOLD |
| support_resistance | 支撑阻力位识别 | BUY/SELL/WATCH |
| multi_timeframe | 多周期共振 | BUY/SELL/WATCH |

### Phase 2 - 技术指标（base）

| 技能 | 功能 | 信号 |
|------|------|------|
| macd | MACD金叉死叉判断 | BUY/SELL/HOLD |
| rsi | RSI超买超卖分析 | BUY/SELL/HOLD |
| boll | 布林带位置分析 | BUY/SELL/HOLD |
| kdj | KDJ随机指标 | BUY/SELL/HOLD |

### Phase 3 - 上下文与风控

| 技能 | 类型 | 来源章节 | 功能 | 信号 |
|------|------|---------|------|------|
| position_context | regulator | 第41章 | 位置比形态重要，高位降权低位加权 | BUY/SELL/WATCH/HOLD |
| market_context | regulator | 第42+44章 | 牛市突破可信，熊市诱多，情绪亢奋 | BUY/SELL/WATCH |
| smart_money | regulator | 第46+47章 | 洗盘vs出货，暗度陈仓识别 | BUY/SELL/WATCH/HOLD |
| stop_loss | action | 第48+54章 | 均线止损/形态止损/希望陷阱 | SELL/HOLD/WATCH |
| take_profit | action | 第49章 | 移动止盈/前高止盈/量能止盈 | SELL/WATCH/HOLD |

---

## 决策引擎架构（v0.8.0）

### 七层架构：信号→决策→事件→AI调节→策略→执行

> 📖 **AI在各层中的角色和影响范围，详见 [AI系统说明.md](docs/AI系统说明.md)**

```
┌─────────────────────────────────────────────────────────────────┐
│ Signal Layer（信号层）                                            │
│ 13个YAML技能独立产出信号                                          │
│ 8个base投票 + 3个regulator调节 + 2个action覆盖                     │
├─────────────────────────────────────────────────────────────────┤
│ Decision Layer（决策层/信号聚合器）                                │
│ 基础投票 → 调节器修正 → SELL分层门槛 → 动作信号覆盖                  │
│ 输出：DecisionResult（聚合信号，非最终交易决策）                     │
├─────────────────────────────────────────────────────────────────┤
│ Event Layer（事件驱动层）⭐v0.8.0新增                              │
│ 关键词匹配(秒级) + AI二次分类 + 市场规则检测 + 持仓新闻扫描          │
│ 9条事件规则：市场暴跌/政策/地缘/财报/黑天鹅等                       │
│ 事件→AIModifierResult转换，复用三层调节机制                        │
│ 优雅降级：AI不可用时关键词匹配仍可检测                               │
├─────────────────────────────────────────────────────────────────┤
│ AI Modifier Layer（AI调节层）⭐v0.8.0新增                         │
│ 新闻抓取 → AI分析情绪/风险/事件 → 三层信号调节                       │
│ Layer 1: 信号调节 — bearish压制buy_score, bullish轻微增强          │
│ Layer 2: 仓位调节 — high risk→仓位×0.7, medium→×0.85              │
│ Layer 3: 状态干预 — black_swan→强制PANIC                          │
│ ⚠️ AI只负责信息理解/情绪判断，不负责决策输出/交易执行                │
│ 📖 详见 docs/AI系统说明.md                                        │
├─────────────────────────────────────────────────────────────────┤
│ Strategy Layer（策略层/行为约束）⭐v0.7.2核心                      │
│ 1. 信号稳定性评估 → 不稳定信号降权                                  │
│ 2. 决策惯性 → 反向决策需持续3天以上才允许                           │
│ 3. 信号确认 → 单日弱信号需连续2天确认                               │
│ 4. 冷却机制 → 清仓后5天/减仓后10天不允许反向操作                    │
│ 5. 反转成本 → 每次方向反转需支付0.3%基础+0.2%×累计次数              │
│ 6. 交易生命周期 → FLAT→OPEN→HOLD→EXIT→COOLDOWN→FLAT              │
│ 7. 持有窗口 → 显式维护 min_hold / add_protection，阻断过早战术卖出 │
│ 8. 卖出语义 → TRIM / EXIT / STOP 与 sell_path 明确分离            │
│ 9. 仓位管理 → 建仓20%+加仓20%+总仓上限60%(RISK_ON)                │
│10. 极端行情感知 → 自动缩短惯性/跳过止损确认/缩短冷却期 ⭐v0.7.2    │
├─────────────────────────────────────────────────────────────────┤
│ Execution Layer（执行层/现实约束）⭐v0.7.2核心                      │
│ 1. 波动率滑点 → 滑点与ATR正相关，高波动=大滑点                     │
│ 2. 流动性过滤 → 量比<0.3禁止交易                                   │
│ 3. 涨跌停限制 → 涨停不买/跌停不卖（A股特性）                        │
│ 4. 冲击成本 → 大额交易影响价格，与成交量比例相关                     │
├─────────────────────────────────────────────────────────────────┤
│ Ranking Layer（排名层）⭐v0.8.0新增 [Scanner模式]                  │
│ 四维评分：技术面(40%) + AI情绪(20%) + 流动性(20%) + 波动性(20%)    │
│ AI禁用时权重自动重分配：{50%, 0%, 25%, 25%}                        │
│ TOP3推荐 + 综合分排序 + 颜色区分                                   │
│ AI情绪维度仅复用AI调节层结果，不独立调用AI                           │
├─────────────────────────────────────────────────────────────────┤
│ Chat Agent Mode（对话模式）⭐v0.8.0新增（v0.8.8 全命令桥）         │
│ 自然语言 → AI(function calling) → 11个工具 → 底层引擎/REPL命令     │
│ 绕过CLI层，直接调Orchestrator/ScannerEngine/PortfolioManager等      │
│ 纯文本输出（不使用Rich Console），对话历史滑动窗口(20条)            │
│ 工具：9个分析类 + run_command(执行任意REPL命令)                   │
│       + manage_portfolio(持仓修改，confirm硬门)                   │
│ 循环保护(3轮) + 结果截断(4000字符) + reset重置对话                 │
└─────────────────────────────────────────────────────────────────┘
```

### 交易生命周期

```
FLAT(空仓) → OPEN(新开仓) → HOLD(持仓) → EXIT(退出过程) → COOLDOWN(冷却期) → FLAT
```

核心原则：
- **信号可以变，决策必须稳定**
- **方向改变必须付出成本**
- **优先避免错误交易，而非追求机会最大化**

Phase 5 后，生命周期只负责描述持仓阶段，不再拿来偷代“刚建仓不能卖”或“刚加仓不能减”的限制。相关限制改由显式窗口字段承担：

- `min_hold_remaining`：建仓后的最短持有窗口，阻断 `weak_sell`、`take_profit_trim`、`stop_loss_trim` 这类战术性卖出。
- `add_protection_remaining`：加仓后的保护窗口，阻断刚加仓就立刻被短噪音打回去。
- `trend_exit`：只在新的趋势破坏事实出现时触发，不再因为生命周期已进入 `EXIT` 就自动放大为清仓依据。

### 市场状态

| 状态 | 含义 | 判定依据（A股行业标准） |
|------|------|----------------------|
| **RISK_ON** | 牛市环境 | 沪深300 > 年线(250日均线) 且 回撤 < 15% |
| **RISK_OFF** | 熊市环境 | 沪深300 < 年线 且 回撤 > 20% |
| **TRANSITION** | 震荡/过渡 | 年线附近(±3%) 或 回撤15%~20% |
| **PANIC** | 恐慌状态 | 当日跌幅>5% 或 跌破MA60+放量暴跌 |

### SELL分层门槛（v0.6.1新增）

| 市场状态 | SELL门槛 | 逻辑 |
|---------|---------|------|
| PANIC | 0.20 | 极端行情，先跑再说 |
| RISK_OFF | 0.25 | 熊市灵敏卖出，该跑就跑 |
| TRANSITION | 0.30 | 震荡市中等门槛，不急卖 |
| RISK_ON | 0.35 | 牛市保守，避免震荡洗出 |

> SELL信号还需明显强于BUY(>1.2倍)才触发。止损/止盈覆盖不受门槛影响。

### 决策追溯

每次决策记录6步追溯路径，CLI展示完整决策过程：

```
决策追溯:
  1. 基础投票 → BUY (score=0.62)
  2. 调节器修正 → BUY (系数=0.85)
  3. 状态判定 → RISK_ON
  4. 状态修正 → BUY (偏多)
  5. 动作覆盖 → HOLD (止盈压制)
  6. 最终决策 → HOLD
```

---

## 回测框架

### 用途

用历史数据验证策略表现，回答关键问题：**这套策略在过去的A股市场到底行不行？**

### 回测流程

```
DataFeeder加载历史数据（Baostock）
  → 一次性拉取个股K线 + 沪深300指数
  → 向前多取320天确保MA250等长周期指标有效
        ↓
逐日回测（每个交易日）：
  1. 截止当日 → 重建完整StockData（均线/RSI/MACD/布林带/KDJ/大盘趋势）
  2. Orchestrator.analyze → 获取决策信号
  3. 策略层状态机约束 → 惯性/确认/冷却/反转成本统一生效
  4. 执行层评估并成交 → 涨跌停/流动性/滑点/冲击成本
  5. 记录每日快照
        ↓
回测结束 → 强制清仓 → 计算统计指标
```

回测固定为纯历史模式：默认禁用 AI 调节层与事件层，避免实时新闻引入时序偏差。

### 评估指标

| 指标 | 说明 |
|------|------|
| 总收益率 | 策略实际收益 vs 初始资金 |
| 年化收益率 | 折算为年化收益 |
| 最大回撤 | 峰值到谷值的最大跌幅（越小越好） |
| 夏普比率 | 风险调整后收益（>1优秀，<0亏损） |
| 胜率 | 盈利交易占比 |
| 盈亏比 | 平均盈利 / 平均亏损 |
| 基准收益 | 买入持有策略的收益（对比基准） |
| 决策稳定性 | 方向反转次数/总决策数（越高越稳定）⭐v0.7.2 |
| 回撤稳定性 | 回撤标准差（越小越稳定）⭐v0.7.2 |
| 最差收益 | Monte Carlo最差路径收益⭐v0.7.2 |
| 结果方差 | Monte Carlo多次模拟的收益方差⭐v0.7.2 |

### 交易规则

- **信号生成**：Day N-1收盘后分析，生成BUY/SELL信号 + 仓位动作
- **执行时机**：Day N开盘价执行（消除前视偏差）
- **T+1硬限制**：买入后至少下一交易日才能卖出（A股基本规则）
- **回测模式切换**：
  - `framework_strict`（默认）→ 仅保留T+1硬限制，行为约束由Strategy Layer统一处理
  - `legacy_compatible` → 额外保留旧版最少持有天数/冷却期门控，便于历史结果对照
- **仓位管理**：
  - 建仓(OPEN)：试探性建仓，默认20%（第53章：先买两成）
  - 加仓(ADD)：趋势确认后追加到40%，总仓位上限60%（第17章）
  - 减仓(REDUCE)：分批止盈或弱卖出信号，减仓留65%底仓（第49章）
  - 清仓(CLOSE_ALL)：深度止损(≥0.85)触发，全部清仓（第48章铁律）；仓位<5%直接清仓
  - 仓位上限随市场状态调整：RISK_ON=60%, TRANSITION=30%, RISK_OFF=15%, PANIC=0%
- **最少持有5天/冷却期5天** → 仅 legacy_compatible 模式启用
- **佣金0.025%（最低5元）+ 印花税0.05%（卖出）+ 过户费0.001%** → A股完整成本模型
- **波动率滑点** → 滑点与ATR正相关（高波动=大滑点），替代固定0.1% ⭐v0.7.2
- **涨停不买/跌停不卖** → A股涨跌停封板约束 ⭐v0.7.2
- **流动性过滤** → 量比<0.3禁止交易 ⭐v0.7.2
- **冲击成本** → 与成交量比例相关 ⭐v0.7.2
- **回测结束强制清仓** → 按最后一天开盘价卖出

### 回测归因导出与持久化

- 回测运行时结果先聚合到 `BacktestResult`：`trades`、`daily_snapshots`、`diagnostics`、`layer_mode` 都在进程内可用。
- 只有显式传入 `--export-analysis-json PATH` 或 `--export-analysis-txt PATH` 时，归因结果才会持久化落盘到指定路径。
- JSON/TXT 导出由 `src/core/backtest_reporter.py` 负责组装，不改变回测统计口径，只做结构化分析输出。
- 当前 JSON 载荷已包含 `layer_breakdown`、`action_source_table`、`hold_break_table`、`diagnostics`，其中动作/持仓破坏归因会额外标出 `trigger_layer` / `broken_layer`；Phase 4-5 已把旧动作收紧映射为 `action_semantic`（ENTRY/ADD/HOLD/TRIM/EXIT/STOP），并补齐 `sell_path`（`flat_sell / stop_loss_trim / stop_loss_exit / take_profit_trim / trend_exit / weak_sell`）。
- `TradeRecord` 会优先持久化策略层给出的 `action_semantic` 与 `sell_path`，报告层仅在缺失时才做 fallback 推断，避免回测后处理把不同卖出原因重新混成一类。
- 若传入 `--export-layer-comparison-json <PATH>`，系统会顺序运行 `decision_only / decision_strategy / decision_strategy_execution` 三层，并导出单个对照 JSON。
- 若传入 `--export-layer-comparison-txt <PATH>`，系统会导出三层对照的统一文本分析，便于直接给 AI 或人工复盘。
- 若传入 `--export-validation-json <PATH>` / `--export-validation-txt <PATH>`，系统会执行基础研究验证：lookahead 时序检查、回测/准实时重放一致性检查（含信号日到执行日的日志传播校验）、样本内/样本外拆分、walk-forward 窗口验证，以及趋势 / 震荡 / 极端行情分桶评估。
- 若传入 `--batch-backtest-codes <CODE1,CODE2,...>` 并配合 `--export-batch-validation-json <PATH>` / `--export-batch-validation-txt <PATH>`，系统会批量执行多标的验证并输出统一汇总。
- 若不传导出参数，终端展示结束后结果不会自动写入仓库固定目录。

### 条件注册表（ISS-002 参数化条件）

支持在YAML中为条件传递参数，实现精确阈值控制：

```yaml
# 旧格式（向后兼容）
change_negative:
  reason: "价格下跌"

# 新格式（参数化）
change_range:
  min: -1.0
  max: 2.0
  reason: "涨跌幅在-1%到2%之间，放量不涨=暗度陈仓"

volume_ratio:
  above: 1.5
  reason: "成交量超过均量1.5倍"
```

可用参数化条件：`change_range`, `volume_ratio`, `ma_distance`, `price_above_ma`, `ma_cross`, `rsi_above`, `kdj_k_above`, `index_bullish`, `index_bearish`, `index_neutral` 等。

---

## 版本历史

### v0.8.22 (R7/R8——E矩阵受控场景 + research 研究工作台) - 2026-09-27

plan/fusion iteration2 R7+R8（账本 plan/fusion/iteration2/EXECUTION_RECORD.md）。

- 🧪 **E 矩阵 manifest v2**：temporal_eligibility 从输入资格**派生**（STRICT/CONTEMPORARY/NON_STRICT/BLOCKED_DATA——不按实验编号硬赋）；selection/mode/thesis/extraction 分源登记；E0b–E7b 重定级注册
- 🧪 **E0b 定向成交场景**：同日买入退出被拒/老仓可卖/末日残余持仓按最后价格估值（不虚构强平收益）/除权分红恒等/印花税切换边界（财政部2023年第39号公告，来源登记）
- 🧪 **E7b 受控场景**：七类动作（OPEN/ADD/HOLD/REDUCE/EXIT/WAIT/REVIEW）+ EXIT+BLOCKED + 四预算案例全命中（合成输入 NON_STRICT 如实标注）
- 🚧 **E2b/E3b/E4b 诚实阻断**：无严格历史输入 → BLOCKED_DATA 拒绝（R1 latest-only 暂停点保持，不结构性收口）；E1b 资源同价检查机器化
- 🆕 **`research <代码>` 单股研究工作台**：一次出 MID/LONG 资格结论、系统草稿（未激活）、逐条缺口；`--capture` 采集财务（真实网络，留样+语义筛查）；不带 --facts 解锁资格——缺什么如实点名
- 📋 **八类走查**：6/8 类合成走查实测通过（记录 plan/fusion/iteration2/R8_WALKTHROUGH.md）；②③ live 持仓路径待真实用户试用（如实 PARTIAL）
- 质量：测试 1204→**1220**（+16）；监督代理对抗审查通过

### v0.8.21 (第二轮R0资格止血) - 2026-09-26

plan/fusion iteration2 R0（账本 plan/fusion/iteration2/EXECUTION_RECORD.md）。架构师第二轮验收裁决落地：先防旁路输出被误用为生产资格，再进大功能。

- 🔒 **事实证据资格门（A03）**：投资逻辑 VALID 需要事实带**可解析证据引用**——「一条 facts 文本即 VALID」不再成立（用户确认的是目标与风险意愿，不是把一句话变成客观事实）；空白/无引用文本一律 UNESTABLISHED；带引用事实的真判断路径保留（R3 自动研究正式化）
- 🔒 **latest-only 历史快照门（A01）**：今天抓的 latest-only 财务证据（旧 pubDate、无版本）不再进入历史严格 PIT 快照——「版本不可追溯」≠「当时已发布」；带版本/当时抓取/行情公告不受累；资格缺口显示具体「版本不可追溯」原因
- 🔧 **影子对照逐周期标来源（A05）**：同股仅 MID 接受时 LONG 显式模拟、不计真计划样本；报告「记录来源」按周期计数；新差异原因「已激活计划的事实未挂证据引用」；映射版本 shadow_v3
- 📝 **文档扩大措辞修正**：E0「等价」→ 案例集内差异较小、E1「互补实证」→ 当前截面重叠低（原始并集130/配额后35 分清）、E7「全路径」→ 全 WAIT 烟测口径；原始报告与数值保留仅加裁决注记
- 质量：测试 1121→**1129**（+8 反例回归）；资格门为安全门，不随策略回滚撤销

### v0.8.20 (影子差异捕获 + E0 正确性基线) - 2026-09-26

plan/fusion 影子阶段前置批（账本 plan/fusion/EXECUTION_RECORD.md）。

- 🆕 **`shadow` 影子对照报告**：l/la/chat 分析持仓时自动用 fusion 中期/长期决策表出对照包（shadow 模拟计划，非用户确认），差异按原因分组落 `~/.muyun/shadow_diff.jsonl`——不改任何主结论，开关 `fusion.shadow_capture`
- 🔧 **E0 正确性基线真实执行**：21 案例双臂回测（legacy vs T+1 批次份额口径），差异全部在项目噪声阈内（单股<2pp、整体<1pp）——本人工案例集内差异较小（v0.8.21 修正原「可视为等价」表述：不能统计证明等价）；报告 `plan/fusion/E0_BASELINE_REPORT.md`
- 🔧 **数据资格探查落地**：baostock 财务季频五接口 pubDate 官方公布日实证可得（财务证据带公布日；v0.8.21 起 latest-only 财务不入历史严格快照，严格历史实验仍阻断）；申万指数日线/停牌状态 tradestatus 可用（`plan/fusion/DATA_COVERAGE.md` 已回写，因子登记表 5 项翻转）
- 🔧 **用户走查首轮修补**：`diff` 无参数列可对比股票；today「等待条件」展开观察池逐只明细；摘要术语中文化（Chandelier Exit→吊灯止损）；shadow 报告中文化+定位说明；用户风险档落配置（单股 2 成/行业 5 成/单笔压力亏损 4 成）；today 单股额度显示
- 🆕 **`plan2` 计划V2 + 影子真判断**：给持仓立中期/长期投资计划（`plan2 <代码> mid|long <意图> --facts 事实`），`plan2 accept` 激活后影子对照用真计划裁决（硬退出仍最先）；`diff <代码> --ai` 可选 AI 综合解读（事实层保持机械）
- 🔧 **RAG 加载峰值保护**：`MUYUN_RAG_MAX_THREADS=N` 限制嵌入模型加载线程（用户 14700 缩肛硬件叮嘱）
- 质量：测试 1031→**1089**（+58）

### v0.8.19 (融合架构基建 F3-F7) - 2026-09-25

plan/fusion 融合架构第二批（F3–F7 基建批次；账本 plan/fusion/EXECUTION_RECORD.md）。

- 🆕 **`today` 今日工作台**：每天从这里开始——需要处理（待确认建议，"这是建议，尚未记为成交"）/继续持有/等待条件三组，旧记录核对提示全量可见；无操作是合法结果
- 🔧 **证据快照与时点资格（F3）**：证据带公布/可用/抓取三时点，未来公告与后发重述不进历史快照；元/万元与累计/单季口径显式；RAG 方法文本不当公司事实；数据可得性矩阵如实标注（`plan/fusion/DATA_COVERAGE.md`）
- 🔧 **三路候选池+因子登记（F4）**：技术/产业/长期质量并集去重，配额按来源排名截断（排列不变），谱系完整可追溯；首批 8 因子登记（未验证数据源只登记不计算）
- 🔧 **周期决策表（F5）**：中期/长期各自独立的决策逻辑（fusion_mid/long_v1），失效条件结构化推导（UNKNOWN 不自动变 FALSE），复核≠强制卖，长期技术噪声不触发退出
- 🔧 **claim 提取与事件谱系（F6）**：AI 产出必须带引用且过五规则核验（引用/实体归属/未来日期/单位），同一事件转载十次只计一票，冻结标注集锁定错误实体/旧闻重炒/财报更正/注入文本等场景
- 🔧 **组合预算求解（F7）**：现金/单股/行业/周期/损失预算约束取 min，卖不出的钱不能提前花，排列不变，拒绝原因明确
- 质量：对抗审查 11 P1 修复（每批回归锁）；测试 906→**1012**（+106）

### v0.8.18 (持仓事实分离 + 统一终态输出) - 2026-09-25

plan/fusion 融合架构 F0+F1+F2 批次（架构师设计 8 文档见 `plan/fusion/`）。

- 🔴 **分析不再改持仓——事实/建议分离（F1）**：旧逻辑把"建议清仓"整包当真实持仓写盘（用户没卖，系统却记成仓位 0+进冷却，EXIT 意图还被吞 5 天）。现拆三层：观察量（持仓期最高价/信号历史）、建议（`~/.muyun/proposals.json` 待确认账本）、成交确认（唯一改持仓入口）。旧持仓自动标 LEGACY_UNVERIFIED（数值原样）并提示核对一次
- 🆕 **`pos confirm <代码> [变化] [价]`**：确认实际成交——部分成交转 PARTIAL 保留剩余待办，重复确认幂等（fill_id 去重），清仓按建议进冷却；`pos` 列表新增「📌 待确认建议」区（"这是建议，尚未记为成交"）
- 🆕 **人话摘要不再自相矛盾（F2）**：`l` 摘要以末端动作+执行可行性为准——PlanGuard 压制后的 HOLD 不再播报"卖出信号"、强制退出救回不再播报"什么都不用做"（探针实证的两个病根）；退出受阻（跌停/停牌）明说"卖不出、待办保留"不误读为继续看好
- 🆕 **AI 看空不再软化卖出（F2）**：方向感知调节——SELL 决策的负向情绪调节不再削低卖出强度（仓位上限/强制状态照常）；`ai.direction_aware_adjustment: false` 可回旧口径 A/B
- 🆕 **scan 排名修复（F2）**：强卖出信号不再折算成高"技术分"被当买入候选排序（探针 D 病根）
- 🆕 **证据卡携带终态**：分析证据追加 decision_id/建议动作/执行状态/目标仓位字段，`diff` 对比同步消费
- 🆕 **统一决策契约（F0）**：DecisionPacket 不可变决策记录（非法动作/仓位组合构造期即报错，UNKNOWN/MISSING 不转 0）+ `analysis_service` 唯一终态构造 + `Orchestrator.analyze_packet` 适配出口
- 质量：对抗审查累计修复 10 阻断缺陷 + 监督员全程账本核对；测试 811→**906**（+95）

### v0.8.17 (分析证据层 + diff 对比 + 批量任务账本) - 2026-09-25

- 🆕 **分析证据自动落盘**：l/la/l all/chat 每次深分析自动追加 `~/.muyun/analysis_evidence.jsonl`（决策/评分/信号/仓位动作/数据缺口）+ 人话证据卡 `分析报告/analysis/{时间}_{代码}_{名字}.md`——满足「分析输出落盘方便回看」
- 🆕 **`diff <代码>` 分析对比**：同股最近两次深分析只列变化项——价格/评分 Δ、决策与仓位动作转向、技能信号转向/新增/消失、新增提示；「较上次为何变化」一眼可读
- 🆕 **`tasks` 批量任务账本**：l all/la/ba/l 多代码 的进度与失败项可见，中断后续跑有据；成功项由当日缓存复用、失败项自动重试（语义不变）
- 🆕 **`scan review` 按扫描方法分组**：汇总尾部新增分组表——每种扫描方式（规则/主题）的只次/胜率/平均涨跌/平均超额/无行情数，口径全透明（同股多次按只次独立计、小样本只看方向）
- 🆕 **bz 输出透出行业景气数据来源**：商品锚/需求端/新闻等来源一目了然（v0.8.8.7 三级桥接成果可视化；无来源不显示假行）
- 🆕 **ADR-02 装配统一**：`src/core/runtime.py` 工厂收敛 6 处手写 Orchestrator 装配（漏传 entry_exit/rag 的 H2/ISS-032 同族事故病根）；配置加载迁 `src/config.py`（路径不依赖 cwd）；回测装配独立（守卫测试）
- 🆕 **ADR-06/07 收口**：超时资源测量工具实跑基线（防冻结无卡死/孤儿线程自然消亡/退出不等待）；维度元数据注册表集中（与 scorer 权重键一致性有测试锁）
- 测试 +30（按版本累计：runtime 6 + evidence 7 + batch 6 + doctor C3 1 + C4 metrics 4 + ba 时序锁 1 + C6 3 + registry 2）；全量 **806 passed / 0 failed**

### v0.8.16 (运行诊断+安装事实源统一) - 2026-09-25

- 🆕 **doctor 运行诊断命令**：REPL 输入 `doctor` 一键体检环境——解释器/核心依赖版本/配置存在性/状态缓存/RAG 知识库/报告目录；只读、零 AI、零网络，缺失项如实标注；`settings.local.yaml` 只报存在性，**内容（API key）绝不回显**
- 🆕 **安装事实源统一**：实测 `uv sync` 因无 pyproject.toml 报错（新环境装不起来）——全部文档统一到 `requirements.txt`（钉版本的已验证清单）：`uv venv .venv` + `uv pip install -r requirements.txt`；chat 依赖缺失提示同步更正
- 🆕 **Agent 入口瘦身**：AGENTS.md ~15KB 历史版本叙事迁入 docs/archive/ 归档（保留指针），两条跨版本硬约束补进 §五
- 测试 +4

### v0.8.15 (持仓并发写保护) - 2026-09-24

- 🔴 **冲突拒绝**：保存持仓前比对磁盘内容指纹（sha256），文件被其他会话/编辑器改过 → 拒绝写入 + 内存恢复为磁盘版本——双开窗口不再静默互相覆盖（原实现只告警仍覆盖）
- 🔴 **不报假成功**：加仓/删仓/字段修改/计划附加/策略回写全部如实返回保存成败；REPL 红字提示、Web 错误片段、chat/TUI 告警
- 🆕 内容指纹比 mtime 更准：外部只改时间戳没改内容（编辑器 touch）不再误报冲突
- 测试 +6；修复 test_tui 的类替换污染 main 命名空间的隔离脆弱性

### v0.8.14 (扫描报告防覆盖+导入幂等) - 2026-09-24

- 🆕 **报告防同分钟覆盖**：扫描报告文件名升级秒级精度 + 独占创建（同秒冲突 `_2/_3` 递增）——同分钟同主题连续扫描不再互相覆盖，每份都保留
- 🆕 **导入复合键幂等**：`scan review import` 已知记录改 `(时间, 来源)` 复合键集合（原 dict 同分钟多来源互相覆盖，重复执行会产生重复行）；成功追加才入集合，同批重复文件不重入、追加失败可重试
- 🆕 **新格式精确导入**：v0.8.14+ 报告按正文精确时间导入；旧格式保留分钟语义，已导入历史不受影响
- 🆕 **时间戳同源**：扫描历史、快照、报告文件三者时间戳精确一致（消除跨秒边界错位）
- 测试 +9（含固定旧格式文本 fixture 重写）

### v0.8.13 (会话状态容错) - 2026-09-24

- 🆕 **JSON 快照原子写**：`last_scan.json` / `deep_analyzed.json` 改走"先序列化 → 同目录唯一临时文件 → `os.replace`"——崩溃不再留半个文件，失败自动清理临时文件并保留旧快照
- 🆕 **坏快照整体拒绝**：`#N` 接续的快照读取带结构校验（根/时间戳/items/每项 code），坏快照告警并按"无扫描"处理——绝不过滤坏条目后重排序号（`#N` 不会错位指向别的股票）；修复坏快照会让 `#N` 直接 AttributeError 崩溃的问题
- 🆕 **历史逐行隔离**：扫描历史/观察池 JSONL 按物理行逐行解码，坏编码/截断尾行跳过并计数告警（不再因单行坏字节拖垮全部历史），原文件绝不动；时间戳混排不再排序崩溃
- 🆕 **部分成功如实上报**：`save_last_scan` 分别报告快照/历史成败，快照挂了历史仍独立记入
- 新告警 6 条人话化三处同步；测试 +13

### v0.8.12.1 (对抗审查修复批) - 2026-09-23

- 🐛 **P0-1 scan review import 数据静默丢失**：`append_scan_history` 原每次写入
  全量重写并 prune >90 天旧行，import 逐文件调用它 → 导入 >90 天旧报告会被同批/
  后续 append 静默删除（实测：导入 140/120/100 天 3 条最终只剩 1 条）。修法=append
  改纯追加（open("a")），prune 整体移除——它与「导入旧历史」的产品目的结构性冲突；
  体量 ~百行/年有界，展示窗口由查询侧（days 参数）控制。
- 🐛 **P0-2 test_session_state 隔离修复入库**：此前该修复只在工作区未提交，HEAD 上
  全量 pytest 仍会污染真实 `~/.muyun/scan_history.jsonl`（文档已声称已修=失实）。
- 🐛 **P1-1 观察池持仓误入池**：`_watch_pool_touch` 未查持仓，持仓股 HOLD 分析会被
  入池；签名加 `pos` + `current_ratio>0` 短路，CLI/chat 两路传参。
- 🟠 成本行按 60只/请求向上取整如实报轮数；`min(..., default=)` 防全无 timestamp 崩溃；
  NaN 防御（`_sparkline`/`_review_path`）；in_pool 文案入池价缺失不再显示「当时 None」；
  chat 钩子补 debug 日志；test_scan_review 隔离补 `_WATCH_FILE`。
- 测试 +1（旧记录存活回归；持仓不入池断言并入既有用例）；全量 755 passed / 2 skipped
  （5 失败为既有失败）。

### v0.8.12 (观察池: watch 命令) - 2026-09-23

- 🆕 **观察池落地**：`~/.muyun/watchlist.jsonl` 事件流（add/remove，重放得在池）。
  `l`/`la`/`l all`/chat 分析出 **WATCH 且无持仓时自动入池**（`_watch_pool_touch` 钩子，
  入池价=分析时现价）——人话摘要「放进观察池」措辞从此与事实一致（已放进/已在观察池
  两态，入池失败不承诺）。
- 🆕 **`watch` 命令**：复用 scan review 引擎（路径/走势/基准/统计全共享，曲线聚合抽为
  `_offset_curve`）看每只在池股「入池价→现价」涨跌 + 逐日走势 + 沪深300 对比 +
  整体走势曲线，报告存 `分析报告/scan/watch_*.md`。`watch add <代码|#N>` 手动入池
  （缺省拉实时价，已在池不重复）；`watch rm <代码>` 唯一出池通道（移出后再分析
  WATCH 会重新入池=新锚点价）。
- 🆕 **交叉标注**：`scan review` 表格加「池」列，在池股打 ✓——复盘与观察池互相看见。
- 🐛 **scan review 空窗口修复**：默认 7 天窗口内无可评估记录但历史有更早的 → 自动扩大
  到全部历史并明说（用户实测导入 26 条旧记录后 review 显示空的病根）；记录标题标注
  「落盘时间 + 距今天数」；整体走势显示实际日期范围。
- 测试 +19（`tests/core/test_watch_pool.py` 16：事件重放/钩子两态/摘要措辞/watch 渲染/
  K线兜底；test_scan_review.py +3：自动扩大/落盘标注/空态引导）；
  全量 738 passed / 2 skipped（5 失败为既有失败）。

### v0.8.11 (扫描复盘: scan review 命令) - 2026-09-22

- 🆕 **扫描历史落盘**：`save_last_scan` 收口（scan market / bz scan / chat 三路共用）
  同步追加 `~/.muyun/scan_history.jsonl`（JSONL 一行一条；v0.8.12.1 起全量保留不
  prune，损坏行读取时跳过）——历史记录跨会话保留，此前只存最近一次（last_scan.json 逐次覆盖）。
- 🆕 **`scan review [天数]` 命令**（别名 `scan 复盘`，默认 7 天，clamp 1-90）：核对近 N 天
  扫描历史每只票「扫描价→现价」累计涨跌 + **逐日走势**（表格迷你曲线列 ▁▂▃▄▅▆▇，
  超 10 点均匀降采样保端点，报告内存逐日全量数值）+ **沪深300 同窗基准超额** +
  涨跌胜率汇总 + 「扫描组 vs 沪深300」整体走势曲线（各票按扫描日后第 k 个交易日
  对齐取均值），报告落盘 `分析报告/scan/review_*.md`。
  当天扫描不进统计（待满 1 个交易日）；停牌/退市股显式标「无行情」剔除；快照价缺失
  （bz scan 系）自动用扫描日 K 线收盘兜底（≤扫描日最后一根 bar，周末自动落上一交易日）。
- 🆕 **`scan review import`**：一次性导入 v0.8.7~v0.8.10 的旧扫描报告
  （`分析报告/scan/*.md` → 解析标题来源 + 文件名时间戳 + 明细段字段；同 (时间,来源)
  幂等跳过）——历史扫描即刻可复盘。
- **反爬预算**（零 AI 调用）：批量实时行情 1 轮（60只/请求，全部票合并去重）+
  逐票日线各 1 次拉全窗口（A股历史无批量接口，逐票一次即请求下限；
  `~/.muyun/kline_cache/` 当日磁盘缓存，同日重复复盘零请求）+
  沪深300 日线 1 次（`~/.muyun/scan_review_index.json` 当日缓存，复用 fear 的
  `_bs_index_kline` 节流+硬超时纪律）。口径统一不复权（快照价与 baostock 日线同口径）。
- chat 经命令桥自动可用（`scan review 7`，零 AI 不进 confirm 硬门）；新告警 1 条
  人话化三处同步（append_scan_history 失败）。
- 测试 +25（`tests/core/test_scan_review.py`：parse 分流 / 历史层往返+prune / 收口行为
  锁死 / 已知输入精确断言 / 缺行情剔除 / 今日记录排除 / 指数当日缓存零请求 / 基准降级 /
  迷你曲线与降采样 / 旧报告导入幂等）。
- 教训两条（详见 AGENTS.md）：宽 except 会把 datetime 别名笔误静默吞成 None（靠精确
  数字断言抓出）；给写文件的既有函数加副作用必须扫测试隔离同类点（test_session_state
  漏重定向新历史文件，曾向真实 ~/.muyun 写入测试垃圾行，已修+清除）。

### v0.8.10 (市场恐慌指数: fear 命令 + chat 工具) - 2026-09-13

- 🆕 **市场恐慌指数 `src/core/fear_index/`**（纯客观计算，**AI 调用次数=0**）：0-100 越高越恐慌，
  五档（极度贪婪/贪婪/中性/恐慌/极度恐慌）。7 成分等权聚合，缺失成分**剔除权重显式标注**
  （绝不补假数据），每个成分带原始值/数据源/状态/口径说明（成分级溯源）：
  涨跌广度（全市场快照）/打板情绪（东财涨停池跌停+炸板率）/市场波动（沪深300 20日波动率分位）/
  市场动量（MA125 乖离）/成交热度（两市成交额分位）/杠杆情绪（融资余额5日变化分位）/
  股债性价比（自算日频盈利收益率−10Y国债，乐咕 PE 采样点反推盈利阶梯 point-in-time）。
- 🆕 **`fear` 命令**：一键总览（成分级溯源面板）+ `fear history` 多周期回顾
  （近5日/10日/1个月/3个月摘要+趋势判断+人话总结+**matplotlib 走势图存
  `~/.muyun/fear_index/charts/`**，中文字体降级链，绘图失败文字功能独立可用）+
  `fear backfill [天数]` 涨停池历史回填（约5-8分钟，断点续传）。
- 🆕 **chat 工具 `get_fear_index`**：三件套注册，问"市场情绪/恐慌/过热"自动调用，
  返回成分明细+多周期摘要+图表路径，MISSING 维度严禁脑补（提示词硬约束）。
- 🆕 **数据底座**：`~/.muyun/fear_index/` 每日快照自动落盘 + 250 日历史回填
  （baostock 指数/成交额、东财宏观两融、乐咕PE+10Y）+ point-in-time 滚动分位
  （pandas rolling rank，历史回放无未来函数）。分位样本不足自动降级固定阈值口径并标注。
- 探针修正：乐咕 PE/中证估值历史均为稀疏采样（近3年仅38-40点）→ ERP 改自算日频；
  东财指数日线 ConnectionError → 成交额历史改 baostock；`macro_china_market_margin_sh/sz`
  一次拉整段两融历史；乐咕活跃度接口被反爬弃源。
- 新增告警 7 条人话化三处同步；`bz --check` 加涨停池探针；依赖 +matplotlib。
- 测试 +33（`tests/core/test_fear_index_engine.py` 口径锁死 20 +
  `test_fear_index_history.py` 隔离 tmp 的历史层/组装 13）；
  设计文档 `docs/2026-09-13_市场恐慌指数_评估与实现方案.md`。
- **对抗审查批（86b6161）**：逐模块攻击面+故障注入修复 5 处（合成序列单边成交额静默
  当双边→交集语义 / syn_history 缓存陈旧致回填成分永远缺失→删缓存 / 收盘后-17:30
  窗口 vol·mom MISSING→回退 STALE / 序列文件乱序取错末日→sort_index 防御 / chat
  history 参数 cap+backfill 新→旧遍历）；+12 故障注入用例（`test_fear_index_adversarial.py`，
  fear 单测 45 全过）。三源交叉验证：成交额新浪 vs baostock 偏差 0.76%（≈北交所口径差），
  收盘价/涨跌停双源自洽。
- 板块/概念/个股下钻（scope 引擎已就绪）留待 P2/P3，当前返回明确未上线提示。

### v0.8.9.5 (全模块彻查修复批: 数据口径/超时/同口径/向量化) - 2026-09-13

- 🔴 **成交量单位归一（P1-1）**：东财系接口（`stock_zh_a_spot_em`/`fund_etf_spot_em`）成交量
  单位是"手"，新浪/baostock 是"股"——降级链混源时 EM 源 volume 比 20 日均量小 100 倍，
  量比类技能条件失效、执行层流动性过滤把所有交易误判为"流动性不足"。新增
  `AKShareClient._em_volume_to_shares` 归一全部 3 处 EM 分支；顺带修 `000001.SZ`
  后缀式代码解析（此前 K线/行情全链路查不到）与批量预取键不匹配。
- 🔴 **baostock 读取超时收尾（P1-2）**：全库最后 8 处未包线程级硬超时的 `rs.next()`
  读取点补齐（回测 DataFeeder K线×2 是回测唯一网络入口，此前 socket 挂起=整个回测
  冻结）；同类点清零，新增 4 条人话告警（速查手册 F2 节）。
- 🔴 **Monte Carlo/重放一致性检查器同口径（P1-3/A-1）**：MC 临时引擎补传
  signal_weights/skill_types/enabled_skills/entry_exit_config（原漏传=MC 分布与基础
  回测不可比）；重放一致性检查补 entry_exit_config+has_position 还原。
- 🟠 **DataFeeder 向量化 31×提速（P2-1）**：逐 bar 全量重算指标的 O(n²) 实现改为
  全序列预计算+按行取值，5 年回测数据构建 18.8s→0.6s；数值等价性由
  `tests/backtest/test_datafeeder_vectorized_equiv.py` 逐 bar 锁死（4 种子×
  Wilder/legacy 双口径×1193 bar 全字段 0 分歧），回测行为零变化。
- 清扫批：trail_pct 配置单位归一（>1 视为百分数，旧口径不变）/event_layer sentiment
  白名单+kimi temperature 守卫/死代码×4 处/TQDM_DISABLE 泄漏×3/scanner 深析持仓
  透传/web·TUI 持仓回写（与 chat·CLI 行为平价）/web 引擎初始化加锁/Pydantic
  ConfigDict 迁移。
- 测试 +18；明细见 ISSUES.md ISS-093。

### v0.8.9.4 (chat 轮次 20 轮 + 上下文护栏 + 模型系判定修复) - 2026-09-11

- **工具轮次上限 10 → 20 轮**：`chat.max_tool_rounds: 5 → 10`（实际硬上限 = 该值 ×2），
  并把 ×2 系数收敛成 `ChatAgent.hard_tool_round_limit` 单一出口；启动横幅与每轮进度
  都显示「第 X/N 轮」，用户可感知。
- **上下文护栏**（新增）：每轮请求前估算输入 token（system + tools + 全部消息），
  超「provider 官方上下文窗口 × 0.85」即折叠最早的 tool 结果（保留最近 6 条，只改
  content 不删消息以保持配对）；折叠后仍超窗口则停止调工具、要求模型直接作答。
  窗口取官方值：DeepSeek `deepseek-flash` 1,000,000 / Kimi `kimi-k2.6` 262,144。
- 🔴 **模型系判定修复（本次改名的隐性回归）**：DeepSeek 2026-09-10 发布 V4.1 Flash，
  API 名改为 `deepseek-flash`。项目改名后，全库 **10 处** `startswith("deepseek-v4")`
  判定全部失配 → 不再传 `thinking.type=disabled` → **全项目静默退回思考模式**
  （官方默认 effort=high：更慢、更贵，且 `temperature` 不生效）。
  修复：新增 `src/core/ai_model.py` 作单一事实源，判定改为「deepseek 前缀」而非版本号，
  10 处调用点统一改走 `thinking_disabled_body(model)`；顺带清理 4 处过期模型名兜底
  （含已下线的 `deepseek-chat`）。实测验证：修复后真实 API 响应不再返回 `reasoning_content`。
- 新增回归：`tests/core/test_ai_model_family.py`（13 项，含「settings 里实际配置的模型名
  必须被识别」「src 内不得再出现版本号式硬编码判定」两条防复发测试）、
  `tests/chat/test_chat_context_guard.py`（8 项）、`tests/chat/test_chat_round_limit.py`（5 项）。

### v0.8.9.3 (chat 会话中断恢复) - 2026-09-11

- **会话持久化（ISS-092）**：chat 对话逐消息原子落盘
  `~/.muyun/chat_sessions/current.json`——q 退出/进程崩溃/发送失败（额度墙）
  中断后，下次启动 chat 检测到未归档会话提示恢复（y/N）；拒绝则归档为
  `session_时间戳.json` 留档（绝不静默删除/覆盖）；`reset` 重置同样先归档
- **`sessions` 命令**：列出全部会话文件（条数/更新时间/当前标记）；
  归档找回归档文件改名为 current.json 重启即可
- **恢复语义**：恢复时 system 提示词换当前版本、按当前 max_history 裁剪
  （tool/tool_calls 配对保护防 API 400）；上次最后一条提问未获回复时提示
  重新提问；管道 EOF/Ctrl+C 不恢复也不归档
- **防覆盖**：并发双开 chat 或未走恢复路径时，首次保存前盘上旧会话先归档
  （并发双开最后写者胜为已知局限）；损坏会话文件隔离 `corrupt_` 前缀留档不删
- **本地文件读写工具（ISS-092）**：chat 新增 `read_file`/`write_file`/`list_files`
  三工具——AI 可读仓库内任意文本文件（越界/密钥文件 `configs/settings.local.yaml`
  拒绝、二进制拒绝），可写**仅限 `AI笔记/` 专属目录**（新目录，可建子目录；
  "把当前对话总结成精华存下来"类需求的载体），可列目录找文件（.git/.venv 隐藏）
- **配置**：`chat.session_persist`（默认 true，settings.yaml）
- 回归：test_chat_session_store 14 项 + test_chat_session_resume 11 项 +
  test_chat_file_tools 15 项；全量 **619 passed / 4 skipped**（含 2 项
  data_sources 外源间歇抖动，非代码问题）；code-quality-guard 对抗审查
  2×P1（恢复裁剪清空覆盖丢数据/测试重定向编码）+8×P2 全部修复

### v0.8.9.2 (盘中实时价 + CLI 接 RAG + 杂项加固) - 2026-09-07

- **盘中实时价（ISS-089）**：单只 `l` 行情链路改为新浪实时优先——此前 baostock
  优先导致盘中分析的"现价"实为昨收，技术面/AI 情绪全基于过期价格；新浪失败
  自动落回 baostock/EM 既有降级链
- **CLI 分析路径接 RAG（ISS-090，ISS-085 拍板落地）**：`-l`/`l`/`la`/scan 深析
  的 AI Modifier/EventLayer 现在带策略知识增强；`MUYUN_CLI_RAG=0` 可关闭；
  首次启用打印可见提示；get_rag_service 失败记忆（一次失败会话内不重试加载）
- **杂项加固（ISS-091）**：benzong 评分缓存 90 天自动清理、exit_signal 日缓存
  30 天清理、add_position 无开仓价显式告警、plan_guard 日期解析失败告警、
  个股新闻当日磁盘缓存 ~/.muyun/news_cache/
- **测试账实同步**：AGENTS tests 表修正（tests/ui 已不存在；web 实验性 UI 零
  测试为已知缺口）
- 回归：test_iss089_realtime_quote 2 项 + test_iss090_cli_rag 4 项 + test_iss091_robustness 5 项；全量 **581 passed / 2 skipped**

### v0.8.9.1 (网络防护层：节流+熔断+批量行情+K线磁盘缓存) - 2026-09-07

- **四件套反爬落地（ISS-088）**：① 新增 `src/data/net_guard.py`——按源节流
  （同源最小间隔±20%抖动）+ 失败熔断（连续3次失败熔断120s直接换源，冷却后
  half-open 试探）；② 新浪 list 批量行情源（原生多参，1请求60只）+ 批量原语
  `get_realtime_quotes`（新浪批量→东财全市场→ETF专用→逐只旧链路，逐层降级
  部分失败合并）；③ `_QUOTE_PREFETCH` 预取映射——`l` 多代码开头预取一次，
  逐只分析的行情请求压成 1 次批量请求；④ 历史K线当日磁盘缓存
  `~/.muyun/kline_cache/`（同日重复拉取零请求，17:30/18:00 盘后新鲜度规则；
  仅 live 路径，回测 DataFeeder 独立路径零影响）
- 降级适配（用户关注点）：支持批量的源一次拉全量；不支持批量的源（baostock）
  在批量原语内自动降级为逐只旧链路；每个源独立熔断，挂掉的源直接跳过
- 回归：tests/core/test_net_guard.py 6 项 + test_batch_quotes.py 10 项（全 mock
  零网络）；全量 **570 passed / 2 skipped**

### v0.8.9.0 (RAG 知识库扩充+检索质量批+对抗审查修复) - 2026-09-06

- **知识库 712→837 块**：公众号教程 73 篇入库（认知革命 43 + 金融战争 30）；
  系列隔离 doc_id 前缀（rz/jrz/qp/ol/ot）修 91 个跨系列 ID 冲突（store 字典
  覆盖致 182 块被静默屏蔽）；层级过滤接通（get_context 按 Chat/Decision/
  AI_Modifier 过滤，金融战争纪实小说不再进决策链路）
- **分块质量**：超长段落按句硬切杜绝 bge 512 token 截断 + 50 字块间重叠 +
  短尾合并，全部块 ≤550 字；jieba 金融黑话词典 100 词（打板/低吸/气宗/剑宗
  不再被切碎）；同章节多样性截断（top_k 单章最多 2 席，30/30 查询章节覆盖 ≥3）
- **静默降级根治**：嵌入模型命中本地 HF 缓存快照即零联网加载（断网不再走
  hub→失败→多源重试风暴，蓝屏事故嫌疑机制封死）；重排器代码保留默认关闭
  （同体系 A/B 实测负收益 ON 0.433/0.232/0.286 vs OFF 0.633/0.553/0.568）
- **对抗审查修复（ISS-085）**：① 知识文件变化触发的重建改从空 store 开始，
  根治已删除/改名文件的僵尸块永久残留；② store.add 批内 doc_id 去重 + 摄入层
  跨文件撞号告警；③ settings.yaml 不再硬编码本机模型绝对路径（动态解析快照，
  换机器不断链）；④ chat search_knowledge 双检索并一次
- **底层挖掘修复（ISS-086）**：strategy_layer 日频推进（冷却/保护期递减+信号
  入史）按交易日去重——live 同一天重复分析不再烧穿 5 天禁买/10 天减仓保护
  （此前每次分析烧一天）；回测每 bar 一次恰好不变，行为矩阵逐调用点核实零回归
- **多代码批量支持（ISS-087）**：`l`/`bz` 支持 `l 600519,000001` 一次性批量
  （中英文逗号/顿号/分号/空格分隔、#N 引用可混用、非法 token 告警、单只失败
  不拖垮批次）；bz 多代码复用 ba 批量管道（AI client 建一次/整批共享成交额/
  缓存优先）；chat analyze_stock 原生多代码+confirm 硬门，系统提示明示能力；
  静默丢弃（第二只代码无声消失）改为显式告警
- **RAG 消费方现状备忘**：仅 chat（search_knowledge+chat 内分析）与 TradePlan
  （pos add/pos plan）接 RAG；CLI/REPL 分析路径（-l/l/la/scan 深析）自 v0.8.1
  起未接线，接线影响评估待拍板（见 ISS-085）
- 评估：Recall@5 0.633 / MRR 0.553 / NDCG 0.568（自一致性口径，见 ISS-085 说明）；
  回归 tests/rag 35 项（新增 index_freshness 3 项）；全量 **524 passed / 2 skipped**

### v0.8.8.7 (景气度接线方案A：客观数据注入 industry_prosperity prompt) - 2026-09-05

- **笨总景气度维从「AI 看新闻猜」升级为「客观数据主导」**（ISS-083）：新增
  `get_industry_metrics` 三级桥接（L1 手写链代表公司代码直配 > L2 链名/别名 >
  L3 COMMODITY_MAP 13 类商品关键词，自举链不参与），拉取商品价格 90/250 日
  区间分位/基差/仓单趋势 + NEV 渗透率/乘用车/用电量 + 宏观 PMI/PPI，注入
  industry_prosperity prompt（SYSTEM_PROMPT 增「客观证据优先、矛盾以客观为准、
  [数据缺失]不得脑补」）
- **兜底变严**：行业名+新闻+客观行业数据三者全空才给兜底 50（原双空即兜底）
- **回测零影响（结构性隔离）**：回测显式构造 data_summary 不经过
  get_data_summary；冒烟 600354/2021 stash 前后 ret/trades/mdd 逐格一致；
  rule_scorer 不动（无 point-in-time 数据源，接入即前瞻）
- **CACHE_VERSION → v0.8.8.7**（景气度旧缓存全部失效重算）；scorer.py 公式
  与闸门零改动（评分系统不重设计，病根是输入不是公式）
- live 验收：6 命中股 sources 全带客观标签、reasoning 引用具体价格分位
  （融捷股份 ip 70→30，客观证据修正纯新闻情绪误判）；未命中股行为不变。
  证据 tests/artifacts/iss083_wiring_round1/；探针命中率 56%（L1 主力）
- 回归：test_industry_prosperity_wiring 15 项（含监督审查强化）；全量 **490 passed / 2 skipped**

### v0.8.8.6 (第三轮审查小项清扫：PlanGuard 冷却对齐 + ba 未评出显示等) - 2026-09-05

- **PlanGuard 规则 4.5/P1/3 强制清仓补写 COOLDOWN**（ISS-082）：此前仅规则4 有
  冷却（ISS-068），其余三条强制清仓次日无冷却可立即买回；现四条口径一致
  （同款 5 天）。回测行为变更：涉及 fundamental_alert/top_signal/time_stop
  退出场景的 A/B 对比需注意
- **ba 白话点评 conf=0 维度显示"未评出"**：风险维缺新闻降级中性 50 不再
  形似真实打分（batch_scorer 行新增 dim_confidences）
- **manage_portfolio 代码规范化 / bz flag 大小写不敏感 / settings.yaml 死键
  清理+显式补 ai.request_timeout/max_retries / 军工链补 created_at /
  删 suggest_update 死代码（62 行）+ 修两处失真注释 / chat 系统提示加工具
  输出防火墙条款**
- **新立 ISS-081**：EntryExit TRIM 类 force_exit 不在 P1a 重断言范围
  （trend_break 短期 TRIM 真实可达，待 A/B 立项）
- 回归：PlanGuard 冷却 3 项（stash 红灯验证）+ 清扫 4 项；全量 **475 passed / 2 skipped**

### v0.8.8.5 (静态事件表自动化：过期自动归档 + 周期事件 recur 滚动) - 2026-09-05

- **`expect` 不再逐条刷「静态事件表可能过时…请更新」催办**（ISS-080）：过期
  一次性事件（>7 天宽限）自动归档，无需手动标 `landed`（landed 仍可用于主动
  标记复盘）；逐条催办改为至多一条「已自动归档 N 条」汇总（防静默空表）
- **新增 recur 周期事件**：`recur: monthly|quarter` + `day` + `window_days`，
  占位符 `{m}`=数据所属月份（公布月-1）、`{q}`=数据所属季度——CPI/PPI、季度
  GDP 自动滚动到下一档期，不再每月手动加条目；static_events.yaml 已迁移，
  年维护量从 ~20 次手动编辑降为每年初更新一次美联储 8 次议息日期
- 实测 `akshare news_economic_baidu`（百度经济日历）存在但本环境被
  BAIDUID cookies 挡住，接入留待办（ISS-080）
- 回归测试 7 项（自动归档/汇总可见/宽限期/月季滚动/{m}{q}解析/旧 schema 兼容
  +landed/周期永不过时）；全量测试 **468 passed / 2 skipped**

### v0.8.8.4 (expect 财报披露空态修复：未发布期间不再刷告警+空态入缓存) - 2026-09-05

- **`expect` 命令的 `Length mismatch: Expected axis has 0 elements, new values
  have 10 elements` 英文告警根治**（ISS-079）：巨潮预约披露接口对尚未发布预约表
  的报告期返回空列表，akshare `stock_report_disclosure` 对空数据直接
  `temp_df.columns=[10列]` 崩 Length mismatch（实测 2026-09 的 2026三季；
  每年预约表未发布的强制披露期同窗口复发）
- **修法**：`calendar_client._fetch` 仅对 Length mismatch 签名转空 DataFrame
  （合法业务空态，非故障）+ 空态结果入缓存（原 `df.empty` 早退绕过缓存写入，
  每次 expect 重复打接口）；其余异常原样上抛 `_safe_call` 告警，防 fail-open
- 回归测试 2 项：空态→[]+无 WARNING+二次调用命中缓存；真故障（超时）仍告警
- 同类点扫描：src 内无其它无防护列赋值/轴操作（崩点全在 akshare 上游）
- 全量测试 **461 passed / 2 skipped**（455 + v0.8.8.3 批 4 项 + 本批 2 项）

### v0.8.8.3 (扫描校验误报修复：bz scan 启动告警消除) - 2026-09-04

- **`bz scan` 启动期 8 条「未知操作符」误告警消除**（ISS-078 残留）：ISS-078 给
  `ScannerEngine._load_rules` 接的加载期校验，把普通 filters 的 `VALID_OPS` 错套到
  `global_exclude` 段——而该段由 `apply_global_exclude` 执行，本就合法支持
  `starts_with`/`contains`/`is_nan`（排除北交所 8/920/430 开头代码与退市股一直在
  正常工作，告警文案「筛选变宽松」是假的）。现新增 `ScannerFilter.validate_excludes`
  （`EXCLUDE_VALID_OPS` 专属操作符集）校验排除段，`validate_filters` 只管规则段
- **回归测试双侧锁死**（tests/core/test_scan_rules_validation.py）：真实
  scan_rules.yaml 加载零误报 + 真拼错操作符仍告警（防矫枉过正）

### v0.8.8.2 (持仓数据安全批：trade_plan 回写保留 + confirm 门补齐 + 值校验) - 2026-09-03

- **[P0] chat 回写不再抹掉 TradePlan**（ISS-078）：`update_from_strategy_decision`
  重建 PositionRecord 时漏传 trade_plan，chat 每深析一次持仓股就把该股交易计划
  从 portfolio.yaml 静默抹掉（实测 HEAD 7 份计划→0），PlanGuard 压制/追踪止损/
  超配「只出手一次」标志随之失效。现从 existing 全量携带；被抹的 6 份计划已从
  git 历史恢复（快照 portfolio.yaml.pre_plan_restore_20260903）
- **Chandelier 强制清仓不再被 PlanGuard 静默吞掉**：force_exit 残留的
  CLOSE_ALL+weak_sell 组合（strategy_layer REDUCE 分支推断残留）在剑宗也会被
  规则1 压成 HOLD（实测复现）；现 PlanGuard 规则1 加守卫 + orchestrator P1a
  覆写 weak_sell→trend_exit，双保险
- **chat confirm 硬门补齐 la/lall、scan、events**：三者均为批量 AI 费用操作
  （逐持仓 ai_enabled=True / 事件层 AI 分类），此前不在门内，AI 可无确认烧费
- **持仓值校验**：add_position/update_position_fields 统一 isfinite+0-1 区间
  （负数/NaN/Inf 仓位此前实测可落盘并污染总仓位），chat manage_portfolio 入参
  前置拦截；残缺 trade_plan 加载不崩且原始数据原样保留
- **会话外修改检测**：_save mtime 比对发人话告警 + chat 回写前重读并确认持仓仍在
- **P2 批**：kimi 兜底模型 moonshot-v1-auto 已下线→kimi-k2.6（实测探测）；
  自举产业链 cycle_anchors/analysis_notes 字符串归一（端侧AI链「端；侧；A；I」
  碎裂修复）；backtest_validator 补 is_backtest=True；ISS-077 缓存条件补周月线
  （半降级不入缓存）；**M-G 修复**：sort_by 列缺失且候选≤50 时先富集 60d 再排序
  再截断（healthy_pullback「按60日涨幅Top30」首次真实生效）；web/tui/扫描排除
  持仓 fail-open 与 rule_scorer 风险检测留痕（人话映射三处同步）；auto_scorer
  密钥撤出进程 env；scan_rules 启动期 validate_filters 接线；test_all_api 假绿
  元组经 conftest 转 skip/断言；遗留清单 L01（5 处版本标签）/L02/L03/L05 清偿
- 全量测试 **455 passed / 2 skipped**

### v0.8.8.1 (个股数据 120s 缓存，防 chat 高频反爬) - 2026-09-02

- **`calculate_indicators` 加类级 120 秒短 TTL 缓存**（ISS-077）：chat 对话里 AI
  反复分析同一只股票（追问、失败重试轮）此前每次都现爬 1 次实时行情+3 次 K 线
  （日/周/月），高频打接口易被反爬限流；REPL 同股连跑（`l 600519` 后紧接
  `la`）同样受益——2 分钟内同股秒回。**只缓存完整结果**（有技术指标才缓存），
  失败/降级结果不缓存，"重跑一次就好"的恢复机会保持原样；120 秒自动过期，
  实时性口径参考全市场快照盘中 5 分钟 TTL 的既有先例（更保守）。回测不受影响
  （走 `DataFeeder._build_stock_data` 独立路径）。模式同既有的
  `_index_trend_cache`，模块级别名 `get_stock_data` 自动覆盖全部调用方
  （CLI/chat/tui/web/scanner）
- 测试：tests/core/test_stock_data_cache.py 6 项（命中/跨股隔离/失败不缓存/
  降级不缓存/TTL 过期/键规范化）+ 公告接线测试补类级缓存清理（全量 390 passed）

### v0.8.8 (chat 全命令桥 + 持仓文件修改) - 2026-09-02

- **chat 连通全部 REPL 命令（run_command 工具）**：chat 里用自然语言执行任意命令——
  回测 `b`/`bb`、事件 `events`/`expect`、笨总评分 `bz <代码>`/`bz scan <主题>`、
  批量分析 `la`/`ba`/`l all`、扫描规则 `rules`、板块 `industries`/`concepts`、
  产业链 `chains`、`#N` 最近扫描引用等。复用 start.py 的 parse_input+run_cli
  **同一份调度代码**（行为平价由构造保证，REPL 新增命令 chat 自动可用），
  命令输出实时回显终端（用户看进度）+ 捕获喂给 AI
- **chat 修改持仓文件（manage_portfolio 工具）**：建仓（add，带价格自动生成
  TradePlan 草稿）/清仓（remove）/字段级修改（update：仓位/开仓价/名称，
  REPL 没有的新能力）/交易计划（plan）/超配（overweight）。复用 CLI
  manage_positions 全部副作用（超配铁律检查、总仓位>80% 警告），每次写盘自动
  留 portfolio.yaml.bak 滚动备份
- **confirm 硬门（AI 费用与写盘安全）**：批量AI费用操作（`l all`/`ba`/`bz scan`/
  `scan market deep`/`pos plan --update`/`bz --refresh`）和持仓写操作必须先在
  对话中征得用户明确同意、带 confirm=true 才执行，不带会被工具直接拒绝；
  REPL 的 y/N 交互确认语义完整映射到 chat 对话
- **一致性配套**：chat 的 scan_market 结果同步写 session_state（chat 内扫描后
  `#N`/`l all`/`ba` 跨命令接续可用）；命令执行后无条件重读持仓（防陈旧快照
  回滚刚做的修改）；RAG 单例对齐（防 CLI TradePlan 路径在 chat 进程内二次
  加载 torch）；命令输出的降级告警经 plain_errors 人话汇总追加喂给 AI
- 测试：tests/chat/test_chat_command_bridge.py 18 项（块列表/confirm 硬门/
  parse+dispatch 桥接/input 补丁语义/持仓往返/#N/重读/SystemExit 存活）

### v0.8.7.9 (l all 批量深分析 + bz scan 接口缓存) - 2026-08-30

- 新增 `l all`：对最近一次 `bz scan` / `scan market` 结果批量深度分析，每只一张 compact
  简明卡（同 `la` 模式），执行前 y/N 确认（含耗时预估）、扫描超 30 分钟自动提示、
  单只失败跳过不中断并汇总——补齐 `ba`（批量评分）/`la`（批量持仓）之间的空白，
  「扫描 → ba 评分排名 → l all 深分析 → pos add 建仓」全链路不再需要逐只手敲
- **`l all` 当天去重**：成功深分析的股票当日记账（`~/.muyun/deep_analyzed.json`，只存
  当天自动过期），重复 `l all` / 重跑 `bz scan` 后再 `l all` 默认只分析新面孔，
  已析股列表+时间展示；`l all -f` 强制全部重析；失败股不记账、下次自动重试
- **bz scan 第三方接口缓存（防反爬重爬）**：① 全市场快照（新浪73页分页）与行业/概念
  成分股（THS HTML 翻页，全库最贵端点）在原有内存缓存（快照盘中5min/成分股30min、
  盘后长缓存）之上加**磁盘 L2**（`~/.muyun/market_cache/`），REPL 重启后仍在 TTL 内
  直接命中，双源全失败时磁盘过期快照兜底；② 法C AI 报股（theme_locator）加同主题词
  **当日文件缓存**（AI+Baostock 验证全省），`bz scan --refresh` 旁路强刷，命中/复用
  处均有 `♻` 提示行；笨总六维评分当日缓存原已具备（`bz scan` 重复跑不重复计费）
- start.py 帮助文本补齐遗漏用法：`#N` 快捷引用（l #1 / bz #1 / pos add #1）、
  `bz scan ... --backtest`、示例区新增主题选股/批量/expect 等 6 条；`l all` 在帮助中
  紧跟 `l` 本体标注为子命令，与一级命令 `la`/`ba` 明确区分

### v0.8.7.8 (对抗审查四批裁决修复) - 2026-08-30

- 六轮对抗性审查（A 42项/B 27项/C 7项/D 6组/E 3项/G 6项/H 5项，累计 96 项发现）全部裁决闭环，
  本版落地最后四批 19 项：D03🔴 止损一票否决 score=0 被降级反手 ADD（FORCE_EXIT_SCORE 兜底）、
  H01🔴 涨跌停按板块分板（创业板/科创板 ±12% 不再误封）、G01🔴 持仓文件损坏不再被静默清空
  （原子写+.bak+快照）、G03 技能聚合加权表决、G02 PANIC 文档对齐（方向A）等
- 基线影响声明：回测账面收益较旧口径下降（大盘组 +12.32→+4.10pp），下降部分=止损失效/不复权
  假象的隐性风险承担挤出；随机 60 股大样本期望 ≈0 不变。旧数字作废，引用以 ISS-074 为准
- 新增回归 45 项（D/E 重放 26 + G 8 + H 11），全量 340 passed；矩阵 v4 大样本终审落账。
  详见 ISS-067~075 与 docs/ 各轮裁决报告

### v0.8.7.7 (第三轮审查·指标公式数学正确性) - 2026-08-29

- RSI/ATR 切 Wilder·通达信口径（MUYUN_INDICATOR_LEGACY=1 可回旧口径）；live 指标 notna 防护；
  高低点窗口守卫对齐；BOLL 显式 ddof=1；C06 一字板复核为假阳性不改。详见 ISS-071

### v0.8.7.6 (第二轮审查·技能引擎与笨总域) - 2026-08-29

- 技能引擎 majority 阈值 off-by-one 修复+9 个 YAML 条件补注册；一票否决强制 F（红线股不再
  进气宗）；坏缓存容错+原子写；事件层 B 系列修复（持仓暴跌/跌停死功能复活等）。详见 ISS-069

### v0.8.7.5 (第一轮审查·42 项裁决) - 2026-08-29

- live ATR 补齐（实盘止损首次真正生效）、回测建仓 bar 时点化防前瞻、前复权对齐实盘、
  信号确认机制修复、market_cache 快照校验/缓存过期/胜率配对等口径纠正。详见 ISS-067

### v0.8.7.4 / v0.8.7.3 / v0.8.7.2 之前

- v0.8.7.4：bz scan 规则+主题组合支持；v0.8.7.3：告警人话化三处同步铁律（plain_errors）；
  v0.8.7.2 见下。各版详情见 ISSUES.md 对应 ISS 条目

### v0.8.7.2 (网络共享性审计修复) - 2026-08-25

- 治"l 分析次数过多后全部命令失效"复发问题（同款问题第二次发作，根因=上次只修了调用点没修病根）
- MarketCache 全部五组缓存类级共享+失败冷却；指数趋势 600s 缓存；融资余额全市场表进程级 memo；
  线程超时包装器改 shutdown(wait=False) 真防冻结。详见 ISS-066

### v0.8.7.1 (用户可感知三件套 + 批量/简明) - 2026-08-24

- `l` 命令顶部新增「📖 人话摘要」面板：白话结论（该做什么/为什么）+ 趋势阶段白话 + 当日笨总评分 + 术语小词典，详细报告保留不变
- `la` 一键分析所有持仓改简明模式（每只一张摘要卡）；新增 `ba` 对最近扫描结果批量笨总评分排名
- 宏观层回测门控（ISS-063）、缩量加速连续性增强、五年回测安全网复跑（牛股组 Δ+12.32pp 8/8 正向）
- 经三路对抗审查修复：la 单股失败不再杀死整个会话等九项（commit c06e83d）

### v0.8.7 (预期事件日历) - 2026-08-20

- `expect` 命令：未来 N 天事件日历 + 预期透支度环境温度计（利率/估值/短期动能三维，事前视角，与 events 事后互补）
- 文档体系精简重组：历史版本文档归档至 docs/archive/；新增 chat 模块架构文档、回测到实盘对齐指南

### v0.8.6.8 (笨总视频理念优化) - 2026-08-12

**来源：笨总 51 份视频总结提炼，对照工具现有能力提取可编码规则。详见 `docs/2026-08-10_笨总视频理念优化报告.md`**

阈值/语义修正（第一档）：
- 宏观流动性状态 `assess_liquidity_state`（笨总实操 0.8/1.3/1.5 万亿分档，非永不触发的10万亿）advisory 展示
- 事件真实性<30 信号路径一票否决（`to_ai_modifier_result` neutral）+ 展示层提示核实
- 自主可控标的强制剑宗（`_mode_from_grade` is_self_reliance 闸门，治"气宗死扛"）

板块/个股层补全（第二档）：
- 板块层 `sector.py`：渗透率30%魔咒 + 旗手滞涨（baostock fetch）
- 个股层：三倍定律（price/low_60d≥3+破MA5，气宗跳过）+ 股东户数/融资余额激活（日缓存+live门控）
- `pos overweight` 超配策略（三铁律：涨幅3x禁入/只出手一次/1.5月到期）
- 永不满仓警告 + 弱势期提醒 + 高位利好出货提示
- AI标注旗手+渗透率；市场宽度通杀/分化/普涨

验证：单股回测 sanity（宁德2020 Δ+68.64pp，未破坏气宗）；bz --check 15源全通；tier1/2/3+live_gating 测试全绿

### v0.8.6.3 (笨总体系融入 scan/events/回测) - 2026-06-24

**笨总 6 维 AI 评分 + 主题精准选股：**
- `bz <code>` 6 维评分（行业景气/业务纯度/历史估值/细分龙头/辨识度/风险），不依赖 RAG
- `bz scan <主题>` 法C 精准定位细分赛道标的（AI 报股 + Baostock 验证，绕过板块匹配粒度天花板）
- `bz --check` 数据源连通性一键体检

**事件四要素：** events 命令 AI 输出真实性/传播性/规模性/时效性，真实性低降级+提示核实

**气宗持有模式（ISS-033）：** PlanGuard 气宗压 trend_exit/take_profit_trim 拿住牛股。验证显示边际收益（+2.46pp），指向"增量调参已达边际，建议跳出现有技术架构思维"

**TradePlan 增强：** `pos plan <code|all> [--update]` 单只/批量生成/更新计划

**基础设施：** requirements.txt 钉版本；SSL 证书路径修复（中文路径致 curl 77）；拼音修正 benzhong→benzong；AI 评分 JSON 截断容错+重试

### v0.8.2 (回测归因 + 交易语义 + 中线持有 + 多时间框架集成) - 2026-05-12

### v0.8.4 (选股体系升级) - 2026-06-10

**扫描规则重构：**
- 删除追涨规则（放量突破/强势动量/超跌反弹）
- 新增健康回调（默认规则）：缩量小跌+低振幅+流动性好
- 新增温和上涨：温和放量上涨(0~5%)，不追涨停
- 保留缩量回调 + 低估值筛选（共4条规则）

**大盘环境前置开关：**
- scan market 启动时自动检测沪深300趋势（MA20/MA60）
- 多头/空头/中性三级提示，空头时红色警告建议轻仓

**Weinstein四阶段分类：**
- 深度分析自动标注S1(筑底)/S2(上升)/S3(顶部)/S4(下跌)
- 趋势判断由深度分析完成，弥补初筛无多日数据的局限

### v0.8.3 (AI增强 + 买卖点精确触发 + 金字塔仓位管理) - 2026-05-17

- **Phase A**: MEMORY.md中长期路线清理 ✅
- **Phase B**: AI输入增强 — tech_context.py技术面摘要注入AI ✅
- **Phase C**: 买点/卖点精确触发 — ATR+EntryExit模块（Chandelier/海龟/CANSLIM）+Orchestrator/回测集成 ✅
- **Phase D**: 金字塔仓位管理 — PositionTier三档体系+升级降级+倒金字塔减仓 ✅
- 新增模块: `src/core/entry_exit/`(6文件), `src/core/position_tier.py`
- 新增配置: `src/core/entry_exit/config.yaml`, `configs/position_tiers.yaml`
- StockData新增atr_14字段，StrategyState新增position_tier/unrealized_profit_pct/days_held
- 架构升级: AI Modifier之后插入EntryExitCalculator（Layer 3.75），最高优先级覆盖

**本轮版本目标：从“可解释的中线系统”继续收口到“真实多时间框架的中线系统”**

核心完成项：

- **Phase 1-3：回测研究链路完成**
  - 已支持结构化归因导出、三层对照回测、基础偏差检查与批量验证。
- **Phase 4：交易语义重构完成**
  - `action_semantic` 与 `sell_path` 已贯通策略层、回测记录、报告导出、CLI 展示与组合持久化。
- **Phase 5：中线持有机制完成**
  - 已引入 `min_hold_remaining`、`add_protection_remaining`，战术减仓与趋势退出彻底拆开。
- **Phase 6：多时间框架集成完成**
  - `StockData` 现在会携带真实周线/月线摘要。
  - `multi_timeframe` 技能已改为“月线定方向、周线定位置、日线找触发”。
  - 实时分析与回测 `DataFeeder` 均接入同一套周/月框架摘要逻辑。

验证结果：

- `tests/test_multi_timeframe_phase6.py` 通过：确认真实月线/周线条件已驱动 `multi_timeframe` 技能。
- `tests/test_conditions.py` 通过：确认条件注册表在新增时间框架条件后无回归。

### v0.8.0 (AI调节层 + 全市场扫描 + 事件驱动 + 四维排名 + 对话模式) - 2026-05-07

**Phase 1: AI调节层 — 从"看K线"到"理解市场"，五层→六层架构**

核心问题：系统只看技术面，无法感知新闻/政策/黑天鹅等现实事件对市场的影响。

**新增模块：**
- **AI Modifier Layer** (`ai_modifier.py`)：Decision→Strategy之间插入AI调节层
  - 三层调节机制：信号调节(bearish压制BUY) + 仓位调节(高风险×0.7) + 状态干预(黑天鹅→PANIC)
  - 支持DeepSeek/Kimi双提供商（均兼容OpenAI SDK）
  - 优雅降级：未配置API Key时仅输出WARNING，不中断分析
- **NewsClient** (`news_client.py`)：新闻数据获取客户端
  - 个股新闻：AKShare stock_news_em()，同会话缓存
  - 宏观快讯：AKShare stock_info_global_em()，1小时缓存
  - 自动格式化为AI可读文本（截断200字/条）

**Phase 2: 全市场扫描（Scanner） — 从"逐只分析"到"全市场筛选"**

核心问题：5500+只A股逐只分析不现实，需要快速缩小范围。

**新增模块：**
- **Scanner** (`src/scanner/`)：两步走全市场扫描
  - Step 1 初筛(quick_scan)：纯规则驱动，5秒内从5511只筛出≤30只候选
  - Step 2 深度分析(deep_analyze)：逐只走Orchestrator六层架构+AI
- **MarketCache** (`market_cache.py`)：3层数据源降级（新浪→efinance→过期缓存）
  - 新浪API：10线程并行分页，0.3~5秒获取5511只A股
  - efinance：备用（东方财富IP封禁风险）
  - 过期缓存：最后兜底
- **ScannerFilter** (`scanner_filter.py`)：声明式过滤器
  - field/op/value三元组，YAML可配置
  - 自动跳过缺失字段（如新浪无量比）
- **6条初筛规则** (`scan_rules.yaml`)：
  - 健康回调(healthy_pullback) / 温和上涨(steady_advance) / 缩量回调(shrink_pullback) / 低估值筛选(value_pick) / 主题成分股(theme_members) / 超跌错杀观察(oversold_watch,6维AND到底确认)
  - 全局排除：停牌/北交所/退市（ST保留给用户判断）

**Phase 3: 事件驱动层（Event Layer） — 从"只看价格"到"感知事件"，六层→七层架构**

核心问题：系统无法感知降息/战争/暴雷/崩盘等重大事件对市场的即时冲击。

**新增模块：**
- **EventLayer** (`event_layer.py`)：Decision→AI Modifier之间插入事件驱动层
  - 关键词匹配：秒级检测宏观新闻中的重大事件（降息/加息/战争/暴跌等）
  - 市场规则：检测沪深300跌幅>3%/1.5%、持仓股跌幅>5%/跌停
  - 持仓扫描：批量扫描持仓股新闻，AI判断影响（events命令时触发）
  - AI二次分类：对关键词命中事件做AI确认（独立AI客户端，不复用AIModifier）
  - 事件→AIModifierResult转换：复用三层调节机制（信号调节+仓位调节+状态干预）
  - 优雅降级：AI不可用时关键词匹配+市场规则检测仍可正常工作
- **9条事件规则** (`event_rules.yaml`)：
  - 市场暴跌(impact=5) / 市场下跌(3) / 个股暴跌(4) / 跌停(5)
  - 政策利好(3) / 政策利空(3) / 地缘风险(4) / 财报意外(3) / 黑天鹅关键词(5)
- **MarketEvent** 模型 (`models.py`)：event_type/sentiment/impact_level(1-5)/scope/detection_method等

**Phase 4: 排名层（Ranking Layer） — 从"逐只分析"到"横向比较"**

核心问题：深度分析10只股票后，缺少统一的评分排序标准，难以快速识别最优标的。

**新增模块：**
- **RankingLayer** (`ranking_layer.py`)：Scanner深度分析后执行四维评分排名
  - 技术面(40%)：映射DecisionResult.score
  - AI情绪(20%)：bullish=70+置信度×20, bearish=30-置信度×20, 风险惩罚
  - 流动性(20%)：成交额对数映射(60%)+换手率倒U形(40%)
  - 波动性(20%)：振幅倒U形(2-5%最优)
  - AI禁用时：情绪权重归零，剩余三维按比例重分配(50%/25%/25%)
  - 缺失数据默认50分（中性），不惩罚信息不全的股票
- **DimensionScore + RankingResult** 模型 (`models.py`)：四维分数+排名+决策建议
- **CLI排名展示**：排名表+TOP3高亮+颜色区分(≥70绿/50-70黄/<50灰)+推荐行

**Phase 5: 对话式智能助手（Chat Agent Mode） — 从"命令行"到"自然语言"**

核心问题：CLI命令行交互门槛高，需要记忆大量参数和命令格式。

**新增模块：**
- **ChatAgent** (`src/chat/agent.py`)：对话式REPL主循环，OpenAI function calling协议
  - 用户自然语言 → AI(DeepSeek/Kimi)解析意图 → function calling → 调用底层引擎
  - 绕过CLI层（Rich Console无返回值），直接调底层引擎获取结构化数据
  - 对话历史管理（滑动窗口20条，reset重置）
  - 循环保护（单次最多3轮工具调用），结果截断（4000字符防token爆炸）
- **5个工具函数** (`src/chat/tools.py`)：到引擎的映射层
  - `search_stocks_by_sector(keyword)` → ScannerEngine.get_industry_list()
  - `analyze_stock(stock_code)` → Orchestrator.analyze()
  - `scan_market(rule_name, industry)` → ScannerEngine.quick_scan()
  - `get_portfolio()` → PortfolioManager.list_positions()
  - `get_news(stock_code, max_count)` → NewsClient.gather_news_for_analysis()
- **纯文本格式化器** (`src/chat/formatter.py`)：结构化数据→AI可读纯文本
- **工具Schema定义** (`src/chat/prompts.py`)：5个OpenAI function JSON Schema

**CLI新增参数：**
- `--no-ai`：禁用AI调节层，仅使用技术面分析
- `--ai-provider deepseek/kimi`：临时切换AI提供商
- `--backtest-mode framework_strict/legacy_compatible`：回测执行模式（默认strict）
- `--layer-mode decision_only/decision_strategy/decision_strategy_execution`：回测分层模式（默认完整链路）
- `--export-analysis-json <PATH>`：导出归因 JSON，按你提供的路径持久化保存
- `--export-analysis-txt <PATH>`：导出归因文本摘要，按你提供的路径持久化保存
- `--export-layer-comparison-json <PATH>`：单次命令导出三层对照 JSON，便于直接做分层归因
- `--export-layer-comparison-txt <PATH>`：单次命令导出三层对照文本分析
- `--export-validation-json <PATH>`：导出基础偏差检查与研究验证 JSON
- `--export-validation-txt <PATH>`：导出基础偏差检查与研究验证文本
- `--batch-backtest-codes <CODE1,CODE2,...>`：批量执行多标的回测验证
- `--export-batch-validation-json <PATH>`：导出多标的批量验证 JSON 汇总
- `--export-batch-validation-txt <PATH>`：导出多标的批量验证文本汇总
- `--scan`：全市场扫描
- `--rule <规则ID>`：指定初筛规则
- `--events`：事件扫描（宏观新闻+持仓新闻+市场规则检测）⭐v0.8.0
- `--chat`：进入对话模式（自然语言交互）⭐v0.8.0

**配置新增（settings.yaml）：**
- `ai`节：AI调节层配置（双提供商+modifier参数）
- `event`节：事件驱动层配置（规则路径+AI分类+持仓扫描开关）⭐v0.8.0
- `chat`节：对话模式配置（历史消息数+工具调用轮次+结果长度）⭐v0.8.0

**设计原则：AI只负责信息理解/情绪判断/事件解析，不负责决策输出/交易执行**
📖 详见 [docs/AI系统说明.md](docs/AI系统说明.md)

**依赖新增：** `openai>=1.0`

### v0.7.3 (Bug修复+持仓扫描) - 2026-04-28

**Bug修复**：空仓分析显示"加仓+0%+FLAT→FLAT"三bug联动
- `_calculate_position`：空仓时HOLD信号返回OPEN（建仓20%），不再返回ADD
- `_update_lifecycle`：FLAT下所有建仓动作正确转换lifecycle+同步position_ratio
- `portfolio.py`：持仓展示/存储统一用strategy_decision.position_ratio

**新增功能**：一键扫描持仓股
- CLI: `--portfolio` / `-p`，交互模式: `scan` / `s`
- 遍历持仓逐个分析 → Y/N确认 → 汇总表格+操作建议统计

### v0.7.2 (交易行为约束系统) - 2026-04-25

**架构升级：从"技术分析工具"升级为"交易行为约束系统"**

核心问题不是策略错误，而是：
1. 决策无状态 → 每日独立判断，行为不连续
2. 信号驱动过强 → 信号变化≈决策变化，噪声交易
3. 缺乏交易语义 → 系统未定义"什么是一笔交易"

**新增模块：**
- **Strategy Layer** (`strategy_layer.py`)：交易生命周期状态机 + 5大约束机制
  - 交易生命周期：FLAT→OPEN→HOLD→EXIT→COOLDOWN→FLAT
  - 决策惯性：反向决策需持续3天以上才允许变化
  - 信号确认：单日弱信号需连续2天确认
  - 冷却机制：清仓后5天/减仓后10天不允许反向操作
  - 反转成本：每次方向反转需支付0.3%基础+0.2%×累计次数
  - 信号稳定性评估：频繁变化的信号权重下降
- **Execution Layer** (`execution_layer.py`)：4大现实约束
  - 波动率滑点：滑点与ATR正相关，高波动=大滑点
  - 流动性过滤：量比<0.3禁止交易
  - 涨跌停限制：涨停不买/跌停不卖
  - 冲击成本：大额交易与成交量比例相关

**架构变更：**
- Decision Layer → 信号聚合器（保留信号整合和覆盖，移除仓位管理）
- 仓位管理迁移到 Strategy Layer（感知交易生命周期）
- Orchestrator → 五层架构：Signal→Decision→Strategy→Execution

**回测增强：**
- Monte Carlo 随机化回测（交易执行日随机偏移±1天）
- 稳定性指标：决策稳定性、回撤稳定性、最差收益、结果方差
- 执行约束统计：涨停阻止买入次数、跌停阻止卖出次数、流动性不足次数

**验证结果：** 002192一年回测：收益+21.35%，夏普1.21，决策稳定性78%

### v0.6.1 (SELL分层门槛) - 2026-04-19

**ISS-016方案C实现：**
- SELL分层门槛：PANIC=0.20/RISK_OFF=0.25/TRANSITION=0.30/RISK_ON=0.35
- 强SELL判定：SELL需>BUY×1.2才触发，弱看空+regulator放大不再直接变SELL
- 牛市特有降级：SELL达标但不够强 → 降为WATCH
- 止损/止盈action覆盖不受门槛影响

### v0.6.0 (决策架构重构) - 2026-04-19

**ISS-016方案A+D+B实现：**
- 年线牛熊判断：沪深300年线(250日均线)+20%回撤，替代5维度日线评分
- 止损仓位感知：空仓时止损降级为WATCH，不否决BUY
- 动态减仓保护期：浅层止损15天/止盈10天/强卖出5天

### v0.5.3 (收益率增强) - 2026-04-19

**收益率展示增强：**
- 新增投入资金收益率（盈利/实际投入成本，反映选股能力）
- 交易统计新增总投入成本/总卖出回款/买卖盈利展示

### v0.5.0 (仓位管理) - 2026-04-18

**仓位管理系统（ISS-014 根本解决）：**
- 新增 PositionAction 枚举：OPEN(建仓)/ADD(加仓)/REDUCE(减仓)/CLOSE_ALL(清仓)
- DecisionResult 新增 position_action + position_ratio 字段
- 决策引擎根据信号强度+市场状态计算仓位动作和比例
- 建仓比例：试探20%（第53章），加仓10%（第17章），总仓上限60%
- 减仓逻辑：SELL+非止损→减仓50%，仓位<10%清仓
- 止损清仓：止损SELL→全部清仓（第48章铁律）
- SimulatedAccount 支持 target_position_ratio 和 reduce_ratio
- CLI 交易记录和决策面板增加仓位信息

### v0.4.0 (回测修正) - 2026-04-18

**回测逻辑修正（ISS-012/013/015）：**
- 前视偏差修复：改为"前日信号 + 次日开盘价执行"，信号和执行分离
- T+1硬限制：买入后至少下一交易日才能卖出（A股基本规则），与min_hold_days分离
- 完整A股成本模型：佣金0.025%(最低5元) + 印花税0.05%(卖出) + 过户费0.001%
- DataFeeder新增 get_open_price()、get_next_trading_day() 方法
- 回测结束清仓使用开盘价而非收盘价

**已知问题（ISS-011 待调优）：**
- 策略频繁交易+止损信号过于活跃，回测仍跑输基准
- 需要策略调优（止损阈值/KDJ降权/买入过滤/信号冷却期）

### v0.3.1 (Phase 3.5) - 2026-04-18

**新增回测框架：**
- DataFeeder 历史数据回放器：逐日重建完整StockData（含技术指标+大盘趋势）
- BacktestEngine 回测引擎：模拟账户+策略层状态机+执行层评估+统计指标
- CLI `--backtest` / `-b` 命令：支持自定义区间/资金
- 7项统计指标：总收益率/年化/最大回撤/夏普比率/胜率/盈亏比/基准对比

**引擎增强：**
- TRANSITION状态：多空分歧判定（0.3 < ratio < 0.6）
- 决策追溯（DecisionTrace）：6步决策路径记录，CLI展示完整决策过程
- 大盘趋势权重×2、PANIC前置判断

### v0.3.0 (Phase 3 完成) - 2026-04-18

**引擎重构：**
- 条件注册表模式（ISS-002）：YAML条件支持参数，替代硬编码lambda
- 信号去重机制（ISS-001）：技能分组 base/regulator/action，regulator不产出独立信号
- 层级决策（ISS-004）：止损一票否决 + 止盈压制买入
- 大盘数据集成（ISS-003）：沪深300 MA20/MA60趋势判断

**新增4个技能：**
- 位置上下文（第41章）：高位骗线/低位机会
- 大盘环境（第42+44章）：牛市突破可信/熊市诱多/情绪亢奋
- 主力行为（第46+47章）：洗盘vs出货/暗度陈仓
- 止盈管理（第49章）：移动止盈/量能止盈/分批止盈

**重构1个技能：**
- 止损管理（第48+54章）：融入四种止损方法+希望陷阱识别

**数据源变更：**
- Baostock提升为主数据源（历史K线+实时行情+沪深300指数）
- 东方财富/新浪降为备用（IP被封风险）

**风险提示增强（心态篇融入）：**
- 第51章：概率思维提醒
- 第52章：贪婪警告（不加杠杆）
- 第53章：恐惧警告（不在恐慌中卖出）
- 第54章：希望陷阱（止损线到就走）

### v0.2.0 (Phase 2) - 2026-04-06
- AKShare金融数据库集成
- 4个新技术指标技能（MACD/RSI/布林带/KDJ）
- 支持实时行情查询（`--live`参数）
- 完整技术指标自动计算

### v0.1.0 (Phase 1) - 2026-04-05
- 5个基础技能
- 手工JSON数据输入
- CLI交互模式

---

## 开发进度

- [x] Phase 1: 项目脚手架 + 5个技能 + 手工数据模式
- [x] Phase 2: AKShare接入 + 4个新技术指标
- [x] Phase 3: 策略融合(第41-55章) + 引擎重构 + 大盘数据
- [x] Phase 3.5: 决策追溯 + TRANSITION状态 + 回测框架
- [x] Phase 4: 仓位管理系统（分批建仓/减仓/清仓）
- [x] Phase 4.5: 决策架构修复（年线牛熊+止损感知+动态保护期+SELL门槛）
- [x] Phase 5: 交易行为约束系统（Strategy Layer+Execution Layer+稳定性指标）
- [x] Phase 5.5: Bug修复（空仓分析错误）+ 持仓扫描功能
- [x] Phase 6: AI调节层（新闻→情绪→三层信号调节）⭐v0.8.0
- [x] Phase 7: 全市场扫描（Scanner两步走：初筛→深度分析）⭐v0.8.0
- [x] Phase 8: 事件驱动层（Event Layer：关键词+AI分类+9条规则+持仓扫描）⭐v0.8.0
- [x] Phase 9: 四维排名层（Ranking Layer：技术面+AI情绪+流动性+波动性）⭐v0.8.0
- [x] Phase 10: 对话式智能助手（Chat Agent：function calling+5工具+REPL）⭐v0.8.0
- ~~Web界面 / API服务~~ (已取消，CLI更适合投资分析场景)

---

## 技术栈

- Python 3.11+
- uv (环境管理)
- PyYAML (技能规则)
- Pydantic (数据模型)
- Rich (CLI美化)
- AKShare v1.18.51 (金融数据，备用 + 新闻数据源)
- Baostock (金融数据，主数据源)
- NumPy (回测统计计算)
- OpenAI SDK (AI API调用，兼容DeepSeek/Kimi) ⭐v0.8.0
