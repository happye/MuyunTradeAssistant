# 暮云思辨投资助手

AI驱动的A股交易行为约束系统 - 基于规则引擎的投资策略系统

## 项目状态

**v0.8.4 选股体系升级** ✅ (趋势选股 + 大盘风控 + Weinstein阶段分类)

> 📖 **里程碑与阶段详情请参阅 [v0.8.3 里程碑](docs/v0.8.3_里程碑.md)**
> 📖 **案例验证报告请参阅 [v0.8.3 C4 案例验证](docs/v0.8.3_C4_案例验证报告.md)**

> 📖 **使用方法请参阅 [使用手册.md](使用手册.md)**
> 📖 **AI系统详解请参阅 [AI系统说明.md](docs/AI系统说明.md)** — AI的角色、影响范围和可控性
> 本文档仅包含架构设计、技术实现与版本历史。

## v0.8.x 技术边界

v0.8.x 的职责拆分如下：

- `README.md`：说明架构、模块关系、技术边界、导出结构与版本口径。
- `使用手册.md`：说明怎么运行、怎么看输出、哪些行为会直接影响日常使用。
- `docs/v0.8.3_里程碑.md`：说明每个 Phase 的交付状态、验收结果与下一阶段主线。

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
│   │   ├── tools.py             # 5个工具函数（到引擎的映射层）+ TOOL_REGISTRY
│   │   ├── formatter.py         # 结构化数据→纯文本格式化器
│   │   ├── prompts.py           # 系统提示词 + 5个工具JSON Schema定义
│   │   └── __init__.py          # 包初始化
│   ├── scanner/                 # 全市场扫描模块 ⭐v0.8.0
│   │   ├── market_cache.py      # 全市场行情缓存（新浪/efinance/过期缓存3层降级）
│   │   ├── scanner_engine.py    # 扫描引擎（初筛quick_scan + 深度分析deep_analyze）
│   │   ├── scanner_filter.py    # 声明式过滤器（field/op/value三元组，YAML可配置）
│   │   ├── scan_rules.yaml      # 扫描规则配置（5条规则+全局排除）
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
│ Chat Agent Mode（对话模式）⭐v0.8.0新增                            │
│ 自然语言 → AI(function calling) → 5个工具函数 → 底层引擎           │
│ 绕过CLI层，直接调Orchestrator/ScannerEngine/PortfolioManager等      │
│ 纯文本输出（不使用Rich Console），对话历史滑动窗口(20条)            │
│ 工具：search_stocks_by_sector / analyze_stock / scan_market       │
│       / get_portfolio / get_news                                  │
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
- **5条初筛规则** (`scan_rules.yaml`)：
  - 放量突破 / 缩量回调 / 强势动量 / 低估值筛选 / 超跌反弹
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
