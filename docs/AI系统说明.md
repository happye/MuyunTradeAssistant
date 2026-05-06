# AI系统说明 — 暮云思辨投资助手

> 版本：v0.8.0 | 创建：2026-05-07
> 本文档详细说明AI在本系统中的角色、影响范围和用户可控性

---

## 核心原则

**AI只负责信息理解和情绪判断，不负责决策输出和交易执行。**

```
AI的职责边界：
  ✅ 信息理解：从新闻文本中提取关键事件、判断情绪方向
  ✅ 风险评估：判断新闻事件的风险等级和影响范围
  ✅ 信号微调：在规则引擎的框架内，对技术面信号做有限度的调节
  ❌ 决策输出：BUY/SELL/HOLD由Decision Layer（规则引擎）决定
  ❌ 交易执行：仓位计算和执行约束由Strategy/Execution Layer决定
  ❌ 自主行动：AI不会主动触发任何操作，所有输出需人工确认
```

> 💡 **一句话总结**：AI是"信息翻译官"，不是"交易决策者"。它把新闻翻译成可量化的参数，然后交给规则引擎处理。

---

## 目录

1. [AI参与的系统层级](#1-ai参与的系统层级)
2. [三层调节机制详解](#2-三层调节机制详解)
3. [AI事件分类](#3-ai事件分类)
4. [AI情绪评分（排名层）](#4-ai情绪评分排名层)
5. [数据流与AI干预点](#5-数据流与ai干预点)
6. [用户可控范围](#6-用户可控范围)
7. [降级策略](#7-降级策略)
8. [AI的局限与风险](#8-ai的局限与风险)
9. [配置速查表](#9-配置速查表)

---

## 1. AI参与的系统层级

AI在系统中参与了**3个层级**，各有明确的职责和边界：

### 1.1 AI Modifier Layer（AI调节层）— 架构第4层

| 项目 | 说明 |
|------|------|
| **代码位置** | `src/core/ai_modifier.py` |
| **架构位置** | Signal → Decision → Event → **AI Modifier** → Strategy → Execution |
| **触发时机** | 每次个股分析（analyze）时自动调用 |
| **输入** | 个股新闻 + 宏观快讯 |
| **输出** | AIModifierResult（情绪/置信度/风险等级/事件类型/调节参数） |
| **AI做什么** | 阅读新闻，输出结构化JSON：sentiment + confidence + risk_level + event_type |
| **AI不做什么** | 不输出BUY/SELL/HOLD，不直接修改仓位，不触发交易 |

**工作流程**：
1. `NewsClient` 抓取个股新闻 + 宏观快讯（AKShare API）
2. 新闻文本格式化（截断200字/条）→ 拼接为AI输入
3. 调用DeepSeek/Kimi API → 输出JSON格式的分析结果
4. 解析JSON → 生成 AIModifierResult
5. 应用三层调节（见第2节）

### 1.2 Event Layer AI分类（事件层AI二次确认）— 架构第3层

| 项目 | 说明 |
|------|------|
| **代码位置** | `src/core/event_layer.py` |
| **架构位置** | Signal → Decision → **Event** → AI Modifier → Strategy → Execution |
| **触发时机** | ①关键词命中重大事件后 ②持仓股新闻扫描时 |
| **输入** | 新闻标题 + 摘要 |
| **输出** | MarketEvent（事件类型/情绪/影响等级/范围/持续时间） |
| **AI做什么** | 对关键词命中的事件做"二次确认"，判断是否真的重大 |
| **AI不做什么** | 不主动检测事件（检测由关键词规则完成），不直接调节信号 |

**工作流程**：
1. 关键词规则首先扫描新闻（秒级，无需AI）
2. 关键词命中后，如果impact_level ≥ 3，才调用AI确认
3. AI确认可能**降级**事件（认为不重大）或**保持/升级**事件
4. 事件最终通过 `to_ai_modifier_result()` 转换，复用三层调节机制

> 💡 **设计意图**：关键词是"火警探头"（快但粗糙），AI是"消防指挥官"（慢但精确）。关键词先报警，AI再判断是否真着火了。

### 1.3 Ranking Layer AI情绪维度（排名层情绪评分）— 展示层

| 项目 | 说明 |
|------|------|
| **代码位置** | `src/core/ranking_layer.py` |
| **架构位置** | Scanner.deep_analyze() → **RankingLayer** → CLI展示 |
| **触发时机** | 全市场扫描深度分析后，排名计算时 |
| **输入** | AIModifierResult（来自AI调节层） |
| **输出** | 情绪维度评分（0-100分，占排名权重20%） |
| **AI做什么** | 将AI情绪结果量化为评分（bullish→70+置信度×20，bearish→30-置信度×20） |
| **AI不做什么** | 不独立调用AI API，仅复用AI调节层已有结果 |

**排名四维权重**：

| 维度 | 默认权重 | AI启用 | AI禁用 |
|------|---------|--------|--------|
| 技术面 | 40% | 40% | **50%** |
| AI情绪 | 20% | 20% | **0%** |
| 流动性 | 20% | 20% | **25%** |
| 波动性 | 20% | 20% | **25%** |

> AI禁用时，情绪权重自动归零，剩余三维按比例重分配（40:20:20 → 50:25:25）。

---

## 2. 三层调节机制详解

AI调节层和事件层都通过**三层调节机制**影响系统，这是AI对信号的唯一干预方式：

### Layer 1：信号调节（Signal Adjustment）

**作用**：调节买入信号的强度（buy_score）

| 情况 | 公式 | 效果 | 最大影响 |
|------|------|------|---------|
| bearish + confidence>0.3 | `buy_score *= (1 - confidence × sentiment_weight)` | 压制买入信号 | score最多削弱30% |
| bullish + confidence>0.5 | `buy_score *= (1 + confidence × sentiment_weight × 0.3)` | 轻微增强买入 | score最多增强9% |
| neutral | 不调节 | 无影响 | 0% |

**关键设计**：
- **bearish的压制力度是bullish增强的3倍以上**（0.3 vs 0.09系数），体现"宁可错过，不可做错"的保守原则
- **bullish需要更高置信度(>0.5)才触发**，bearish只需>0.3，防止AI过度乐观
- 叙事转变(narrative_shift)会额外放大1.5倍调节力度

### Layer 2：仓位调节（Position Cap）

**作用**：限制最大仓位比例

| 风险等级 | 仓位上限 | 效果 |
|---------|---------|------|
| low | 100%（不限制） | 无影响 |
| medium | 85% | 轻微限制 |
| high | 70% | 显著限制 |

**关键设计**：
- 仓位调节**只降不升**，AI永远不会建议加大仓位
- 叙事转变时仓位上限再打85折（high risk → 70% × 85% ≈ 60%）

### Layer 3：状态干预（State Override）

**作用**：在极端情况下强制改变市场状态

| 触发条件 | 强制状态 | 仓位上限 | 信号压制 |
|---------|---------|---------|---------|
| black_swan事件 | PANIC | 30% | -0.5（强力压制买入） |

**关键设计**：
- 状态干预**仅限黑天鹅事件**，是AI最激进的干预手段
- PANIC状态下：SELL门槛降至0.20，仓位上限0%，极端行情"先跑再说"
- 即使是黑天鹅，也只是改变市场状态，**不直接执行卖出**——最终的交易执行仍需人工确认

### 三层调节的累积效果示例

| 场景 | 信号调节 | 仓位调节 | 状态干预 | 综合效果 |
|------|---------|---------|---------|---------|
| 利空消息（bearish, conf=0.6, medium risk） | score×0.82 | cap=85% | 无 | 温和偏空 |
| 地缘风险（bearish, conf=0.8, high risk） | score×0.76 | cap=70% | 无 | 显著偏空 |
| 黑天鹅（black_swan, conf=0.9） | score×0.50 | cap=30% | →PANIC | 极端防御 |
| 利好消息（bullish, conf=0.7, low risk） | score×1.06 | cap=100% | 无 | 温和偏多 |

---

## 3. AI事件分类

Event Layer中的AI分类是一个"二次确认"机制，其工作流程如下：

```
新闻进入系统
    │
    ▼
关键词匹配（规则驱动，秒级）
    │
    ├── 未命中 → 忽略（无AI调用）
    │
    └── 命中 + impact ≥ 3 → AI二次确认
            │
            ├── AI判断"不重大"(is_significant=false) → 事件降级/忽略
            │
            └── AI判断"重大" → 使用AI的分类结果
                    │
                    ├── 可能修正event_type（如从macro改为black_swan）
                    ├── 可能修正impact_level（升级或降级）
                    └── 最终 → to_ai_modifier_result() → 三层调节
```

**AI分类的输出字段**：

| 字段 | 说明 | 可选值 |
|------|------|--------|
| is_significant | 是否值得关注 | true/false |
| event_type | 事件类型 | policy/war/earnings/macro/black_swan/none |
| sentiment | 情绪方向 | bullish/bearish/neutral |
| impact_level | 影响等级 | 1-5（1=可忽略，5=极端） |
| scope | 影响范围 | market/sector/stock |
| duration | 预期持续时间 | short/medium/long |

**AI分类的约束**：
- 系统提示词明确要求"宁可低估影响也不要夸大"
- 只有impact_level ≥ 3的关键词命中才触发AI确认（避免API滥用）
- AI分类失败时，回退到关键词匹配的结果（降级不中断）
- 持仓股新闻扫描时，也用同一套AI分类逻辑

---

## 4. AI情绪评分（排名层）

Ranking Layer将AI调节层的输出量化为0-100分的情绪维度评分：

### 评分公式

```
基础分：bullish=70, neutral=50, bearish=30

置信度偏移：
  bullish: 基础分 + confidence × 20  （最高90分）
  bearish:  基础分 - confidence × 20  （最低10分）
  neutral:  基础分 + score_adjustment × 50

风险惩罚：
  low risk: 0分惩罚
  medium: -10分
  high: -20分

最终: clip(0, 100)
```

### 评分示例

| AI判断 | 置信度 | 风险 | 基础分 | 偏移 | 惩罚 | 最终分 |
|--------|--------|------|--------|------|------|--------|
| bullish | 0.8 | low | 70 | +16 | 0 | **86** |
| bullish | 0.5 | medium | 70 | +10 | -10 | **70** |
| neutral | 0.3 | low | 50 | 0 | 0 | **50** |
| bearish | 0.6 | medium | 30 | -12 | -10 | **8** |
| bearish | 0.9 | high | 30 | -18 | -20 | **0**（最低） |

### 权重影响范围

AI情绪维度在排名中占**20%权重**（默认），即：

```
AI对排名的最大影响范围：
  最高场景(bullish, 高置信, 低风险): 90分 × 20% = 贡献18分
  最低场景(bearish, 高置信, 高风险): 0分 × 20% = 贡献0分
  差值: 18分（占满分100的18%）

  → AI情绪最多让排名分波动18分，不会颠覆排名结果
```

---

## 5. 数据流与AI干预点

完整的七层架构数据流，标注AI干预位置：

```
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. Signal Layer（信号层）                                                  │
│    13个YAML技能 → 独立信号                                                  │
│    ✅ 纯规则驱动，AI不参与                                                  │
├────────────────────────────────────────────────────────────────────────────┤
│ 2. Decision Layer（决策层）                                                │
│    信号投票 → 调节器修正 → SELL门槛 → 动作覆盖                              │
│    ✅ 纯规则驱动，AI不参与                                                  │
│    输出: DecisionResult(score, decision, position_action)                   │
├────────────────────────────────────────────────────────────────────────────┤
│ 3. Event Layer（事件驱动层）                                               │
│    关键词匹配 → [🤖AI二次分类] → 市场规则检测 → 持仓扫描                    │
│    ⚠️ AI参与：仅做事件确认（可禁用，降级为纯关键词）                          │
│    输出: MarketEvent → 转换为 AIModifierResult                              │
├────────────────────────────────────────────────────────────────────────────┤
│ 4. AI Modifier Layer（AI调节层）                                           │
│    新闻抓取 → [🤖AI分析情绪/风险] → 三层调节                                │
│    ⚠️ AI参与：新闻理解 + 情绪判断 + 风险评估（可禁用，降级为不调节）          │
│    输出: AIModifierResult(sentiment, confidence, risk_level, adjustments)   │
├────────────────────────────────────────────────────────────────────────────┤
│ 5. Strategy Layer（策略层）                                                │
│    交易生命周期 + 惯性 + 确认 + 冷却 + 反转成本 + 仓位管理                   │
│    ✅ 纯规则驱动，AI不参与                                                  │
│    ⚠️ 接受AI的调节参数（score_adjustment/position_cap/force_state）          │
├────────────────────────────────────────────────────────────────────────────┤
│ 6. Execution Layer（执行层）                                               │
│    波动率滑点 + 流动性 + 涨跌停 + 冲击成本                                  │
│    ✅ 纯规则驱动，AI不参与                                                  │
├────────────────────────────────────────────────────────────────────────────┤
│ 7. Ranking Layer（排名层）[Scanner模式]                                    │
│    四维评分 → 排序 → TOP3推荐                                               │
│    ⚠️ AI参与：情绪维度评分（复用AI调节层结果，不独立调用AI）                   │
│    输出: RankingResult(排名 + 四维分数 + 决策建议)                           │
├────────────────────────────────────────────────────────────────────────────┤
│ 👤 用户确认                                                                │
│    所有交易操作（持仓更新）需人工Y/N确认                                     │
│    ✅ 最终控制权始终在用户手中                                               │
└────────────────────────────────────────────────────────────────────────────┘
```

**总结**：
- 6个层级中，AI仅参与**2个层级**的主动计算（Event AI分类 + AI Modifier）
- 1个层级被动引用AI结果（Ranking情绪维度）
- 3个核心层级完全不受AI影响（Signal + Decision + Strategy/Execution）
- **最终交易执行需人工确认**

---

## 6. 用户可控范围

### 6.1 一键禁用AI

| 方式 | 操作 | 效果 |
|------|------|------|
| CLI参数 | `--no-ai` | 本次运行完全禁用AI（调节层+事件AI分类） |
| 配置文件 | `ai.enabled: false` | 全局禁用AI调节层 |
| 配置文件 | `event.ai_classify: false` | 仅禁用事件AI分类，保留AI调节层 |
| 配置文件 | `event.keyword_scan: true` | 关键词检测不受AI禁用影响，始终可用 |
| 清空API Key | 删除api_key值 | 系统自动降级（输出WARNING，不报错） |

### 6.2 AI行为参数调节

| 参数 | 配置位置 | 默认值 | 说明 |
|------|---------|--------|------|
| `ai.modifier.sentiment_weight` | settings.yaml | 0.3 | 情绪调节力度（越大→AI对信号影响越大） |
| `ai.modifier.risk_position_cap` | settings.yaml | 0.7 | 高风险时仓位上限（0.7=打7折） |
| `ai.modifier.max_news_per_stock` | settings.yaml | 10 | 每只股票最多抓取新闻条数 |
| `ai.modifier.cache_ttl` | settings.yaml | 3600 | 新闻缓存时间（秒） |
| `ranking.weights.sentiment` | settings.yaml | 0.20 | 排名中AI情绪维度权重 |

**参数调节建议**：
- 如果觉得AI影响过大：降低 `sentiment_weight`（如0.1或0.2）
- 如果觉得AI影响不足：提高 `sentiment_weight`（如0.4或0.5，但不超过0.5）
- 如果不想AI限制仓位：设 `risk_position_cap: 1.0`
- 如果排名不需要AI维度：设 `ranking.weights.sentiment: 0`

### 6.3 AI提供商切换

| 方式 | 操作 | 说明 |
|------|------|------|
| CLI参数 | `--ai-provider deepseek/kimi` | 临时切换（本次运行有效） |
| 配置文件 | `ai.provider: "kimi"` | 全局切换 |

| 提供商 | 默认模型 | 优点 | 缺点 |
|--------|---------|------|------|
| DeepSeek | deepseek-chat | 稳定JSON输出，快+便宜 | 中文理解略弱 |
| Kimi | moonshot-v1-auto | 中文理解强 | kimi-k2.6偶尔空响应 |

### 6.4 AI Debug模式

用于查看AI的完整输入输出，判断AI分析是否合理：

```powershell
# 方式1: CLI参数
python -m src.cli.main -l 600519 --debug

# 方式2: 交互模式
暮云> debug          # 开启debug模式
暮云> 600519         # 分析时打印AI交互详情

# 方式3: 配置文件
# settings.yaml → ai.debug: true

# 方式4: 一键启动
start_debug.bat       # 双击直接启动debug模式
```

Debug输出包含：
- AI Provider + Model 信息
- System Prompt（截断500字）
- User Prompt（截断1000字）
- AI Response Content（截断2000字）
- Reasoning Content（如有，截断1000字）

---

## 7. 降级策略

系统设计了多层降级机制，确保AI不可用时系统仍正常运行：

### 7.1 降级层级

```
AI完全可用
  │
  ├─ AI Modifier: 新闻→AI分析→三层调节
  ├─ Event AI分类: 关键词命中→AI二次确认
  └─ Ranking情绪维度: AI结果→20%权重评分
  │
  ▼ AI API不可用/超时
  │
  ├─ AI Modifier: 返回neutral结果（不调节任何信号）
  ├─ Event AI分类: 回退到关键词匹配结果（纯规则）
  └─ Ranking情绪维度: 默认50分（中性），权重自动重分配
  │
  ▼ 未配置API Key
  │
  ├─ AI Modifier: 自动禁用（输出WARNING日志）
  ├─ Event AI分类: 自动禁用
  └─ Ranking情绪维度: 0%权重，其余三维{50%,25%,25%}
  │
  ▼ --no-ai 参数
  │
  ├─ AI Modifier: 完全跳过
  ├─ Event AI分类: 完全跳过（关键词检测仍可用）
  └─ Ranking情绪维度: 0%权重
```

### 7.2 降级对输出的影响

| 场景 | 信号 | 仓位 | 状态 | 排名 |
|------|------|------|------|------|
| AI正常 | 可能被bearish削弱 | 可能被high risk限制 | 黑天鹅→PANIC | 4维评分 |
| AI降级 | 不受影响（纯技术面） | 不受限制 | 不被AI干预 | 3维评分（情绪=50） |
| AI禁用 | 同上 | 同上 | 同上 | 3维评分（情绪=0权重） |

> 💡 **关键理解**：AI降级/禁用 = 回退到v0.7.3的纯技术面系统。所有v0.7.3及之前的功能不受影响。

---

## 8. AI的局限与风险

### 8.1 AI分析的局限性

| 局限 | 说明 | 应对 |
|------|------|------|
| **新闻延迟** | 新闻抓取有缓存（个股同会话、宏观1小时），突发事件可能延迟 | 关注CLI中新闻数量和时间戳 |
| **情绪误判** | AI可能将中性新闻判为bearish（或反之） | 调低sentiment_weight或用--no-ai对比 |
| **置信度偏差** | AI可能对不确定事件给出过高置信度 | 系统对bullish需更高置信度(>0.5)才触发 |
| **黑天鹅误报** | AI可能将正常波动标记为black_swan | 黑天鹅需同时满足关键词+AI+impact=5，三重确认 |
| **信息不完整** | AI仅看新闻文本，不理解技术面（指标/形态/量价） | TD-5已知技术债，后续版本改进 |
| **API不稳定** | DeepSeek/Kimi API偶尔超时或返回空 | 自动降级为neutral，不中断分析 |

### 8.2 用户应注意的风险

1. **AI不是"更聪明"的分析师**：AI只是在新闻理解上比规则更灵活，但它的判断可能出错
2. **不要因为AI说bearish就恐慌卖出**：AI的影响被限制在±30%信号调节范围内，且需要你手动确认
3. **AI对利好反应温和**：bullish增强最多9%，而bearish削弱最多30%，这是故意的设计——宁可错过机会，不可做错方向
4. **AI分析的是新闻，不是K线**：AI完全不知道均线、MACD、支撑位等技术信息
5. **成本考虑**：每次AI分析消耗约0.001-0.01元API费用（DeepSeek）或0.01-0.05元（Kimi），深度分析10只股票约0.01-0.5元

### 8.3 安全边界（系统设计保证）

| 安全保证 | 实现方式 |
|---------|---------|
| AI不会自动交易 | 所有持仓更新需Y/N人工确认 |
| AI不会颠覆决策 | bearish最多削弱30%，bullish最多增强9% |
| AI不会放大仓位 | 仓位调节只降不升 |
| AI不会无限调用 | 新闻有缓存，API有超时，失败自动降级 |
| AI可以被完全关闭 | `--no-ai` 或 `ai.enabled: false` |
| 黑天鹅需多重确认 | 关键词命中 + AI确认 + impact=5，缺一不可 |
| 回测不受AI干扰 | 回测模式AI始终禁用，避免历史数据混入实时新闻 |

---

## 9. 配置速查表

### 完整AI相关配置（settings.yaml）

```yaml
# ===== AI调节层 =====
ai:
  enabled: true                    # 全局开关
  debug: false                     # Debug模式
  provider: "deepseek"             # 默认提供商
  deepseek:
    api_key: "sk-xxx"              # API Key（或环境变量DEEPSEEK_API_KEY）
    base_url: "https://api.deepseek.com"
    model: "deepseek-chat"         # 日常分析模型
    model_pro: "deepseek-chat"     # 深度分析模型
  kimi:
    api_key: "sk-xxx"
    base_url: "https://api.moonshot.cn/v1"
    model: "moonshot-v1-auto"
    model_pro: "kimi-k2.6"
  modifier:
    sentiment_weight: 0.3          # 情绪调节力度 (0-1, 推荐0.1-0.5)
    risk_position_cap: 0.7         # 高风险仓位上限 (0-1)
    max_news_per_stock: 10         # 每股最多新闻数
    cache_ttl: 3600                # 新闻缓存(秒)

# ===== 事件驱动层 =====
event:
  enabled: true
  keyword_scan: true               # 关键词扫描(无需AI)
  ai_classify: true                # AI二次分类(需AI)
  portfolio_scan: true             # 持仓股新闻扫描

# ===== 排名层 =====
ranking:
  enabled: true
  weights:
    technical: 0.40                # 技术面权重
    sentiment: 0.20                # AI情绪权重(AI禁用自动归零)
    liquidity: 0.20                # 流动性权重
    volatility: 0.20               # 波动性权重
```

### CLI参数速查

| 参数 | 说明 | 示例 |
|------|------|------|
| `--no-ai` | 禁用所有AI功能 | `python -m src.cli.main -l 600519 --no-ai` |
| `--ai-provider <p>` | 切换AI提供商 | `--ai-provider kimi` |
| `--debug` | 查看AI完整输入输出 | `python -m src.cli.main -l 600519 --debug` |
| `--events` | 事件扫描(含AI分类) | `python -m src.cli.main --events` |
| `--scan` | 全市场扫描(含排名) | `python -m src.cli.main --scan` |

---

## 附录：AI Prompt概览

系统使用了两套AI Prompt，确保输出结构化和可控：

### A.1 AI调节层Prompt（ai_modifier.py → SYSTEM_PROMPT）

**目标**：从新闻中提取情绪、风险、事件信息
**输出格式**：JSON（sentiment/confidence/risk_level/narrative_shift/event_type/summary/key_events）
**关键约束**："宁可低估影响也不要夸大"

### A.2 事件分类Prompt（event_layer.py → EVENT_CLASSIFY_PROMPT）

**目标**：判断新闻事件是否值得关注
**输出格式**：JSON（is_significant/event_type/sentiment/impact_level/scope/duration/summary）
**关键约束**："只有真正重大的事件才标记为4-5级"

> 两套Prompt都设置了 `temperature=0.1`（低随机性），确保输出稳定可复现。

---

*本文档随系统版本更新。如有疑问，请参考源码中的注释或使用 `--debug` 模式查看AI完整交互。*
