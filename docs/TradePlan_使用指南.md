# TradePlan 使用指南（v0.8.5）

> 解决"前景良好的股票被日常波动卖飞"的问题。建仓时一次性定下交易计划，之后按计划执行。

---

## 一句话理解

**之前**：每天根据当下技术面/AI 重新决定要不要持有 → 一只好股票遇到一天负面消息就被卖飞。  
**现在**：建仓时定下计划（止损/止盈/失效条件/最长持有），之后只检查"计划条件是否触发"，不被日常波动牵着走。

---

## 三步上手

### 第一步：建仓时让 AI 帮你生成 plan

```
暮云> pos add 600519 贵州茅台 1500.0 0.20
✓ 已添加持仓: 贵州茅台 (600519)
  仓位: 20%  开仓价: 1500.00

🤖 正在为 600519 贵州茅台 生成交易计划草稿...
   ✓ 读取技术面（ATR=35.2, MA20=1480, MA60=1450）

📋 计划草稿
  why_buy: Weinstein S2 上升阶段（MA20>MA60 且价在 MA20 上方）；MA20(1480.00)>MA60(1450.00) 多头排列；MACD 多头；基本面前景判定：bullish
  when_buy: 建仓价 ¥1500.00，S2 阶段，价格在 MA20(1480.00)上方
  how_much: 20% 仓位
  止盈分档: ¥1650.00 / ¥1800.00
  失效条件:
    - 跌破初始止损价（致命）
    - 跌破 MA60(¥1450.00) + 成交量异常放大
    - MA20 死叉 MA60（上升趋势结构破坏）
    - 出现重大利空（如政策转向、财务造假、行业系统性风险）
  locked_initial_stop: ¥1430.00     ← 这是建仓时锁死的初始止损（entry - 2×ATR）
  max_hold_days: 135 天               ← 牛市档自动延长（90 × 1.5）
  fundamental_outlook: bullish

是否采用此计划草稿？[Y/n]:  Y
✓ 交易计划已保存到 portfolio.yaml
```

**何时跑**：每次给某只股票建仓后，**自动**会跳出。
**怎么改**：选 `n` 跳过 AI 草稿后，可以直接编辑 `portfolio.yaml` 的 `trade_plan:` 段。

### 第二步：跑 scan 看计划状态 + 调整建议

```
暮云> scan
分析 600519 贵州茅台...
  当前价: ¥1700  涨跌幅: +1.2%
  决策: HOLD  ← 即使大盘当天看空，PlanGuard 因计划未失效压制了 weak_sell

  💡 计划调整建议（2 条）
    📋 [auto_trailing] 持仓最高 ¥1720.00（盈利 14.7%），按 2×ATR(14)=35.20 上移止损至 ¥1649.60（原 ¥1430.00）
    ⚠ [target_reached] 价格 ¥1700 已达止盈分档 T1 (¥1650.00)，建议按计划分批落袋
```

**何时跑**：每天开盘前或收盘后。
**重点看的字段**：
- 📋 标记 = 数学/规则建议（如 trailing 止损上移）— 跟着系统操作
- ⚠ 标记 = 提醒类（如止盈到了、时间快到了、失效条件触发）— 自己判断

### 第三步：查完整计划

```
暮云> pos plan 600519

📋 600519 贵州茅台 — 交易计划
  plan_id: 600519_2026-06-19
  opened_at: 2026-06-19
  why_buy: Weinstein S2 上升阶段...
  when_buy: 建仓价 ¥1500.00，S2 阶段...
  how_much: 20% 仓位
  止盈分档: ¥1650.00 / ¥1800.00
  失效条件:
    - 跌破初始止损价（致命）
    - 跌破 MA60(¥1450.00) + 成交量异常放大
    ...
  locked_initial_stop: ¥1430.00
  current_stop: ¥1430.00 (单向上移)
  max_hold_days: 135 天
  fundamental_outlook: bullish

  调整审计（暂无）
```

---

## PlanGuard 是怎么"持得住"的

PlanGuard 在 Strategy Layer 之后、Execution Layer 之前评估，按 4 条规则操作：

| 规则 | 触发 | 操作 |
|------|------|------|
| 1. 压制 weak_sell | sell_path=weak_sell + outlook≠bearish + 失效条件未触发 | SELL → HOLD（标"PlanGuard: 计划未失效，压制弱卖出"） |
| 2. 安全网保留 | take_profit_trim / stop_loss_* / trend_exit | **不动** — 这些是真该卖的信号 |
| 3. 时间止损 | 已持有 ≥ max_hold_days | 强制 SELL（不管你 plan 怎么写，时间到了就要重评估） |
| 4. 致命止损 | price ≤ current_stop | 强制 SELL（绝对不可压制——保本金第一） |

> **设计原则**：PlanGuard 只压制 weak_sell（信号边际转空），不削弱任何风控。致命止损/趋势退出/止盈分批永远按现行逻辑触发。

---

## TradePlan 七要素（参考策略库望周知大纲 ch40-41）

| 要素 | 字段 | 说明 |
|------|------|------|
| 买什么 | stock_code (隐含) | 股票代码 |
| 为什么买 | why_buy | thesis 文本（AI 生成草稿，可改） |
| 什么时候买 | when_buy | 进场触发条件描述 |
| 买多少 | how_much | 目标仓位比例 |
| 什么时候止盈 | when_sell_targets | 分档目标价（绝对价） |
| 什么时候止损 | locked_initial_stop / current_stop | 初始止损（建仓基线）+ 当前止损（trailing 后单调上移） |
| 失效条件 | when_sell_invalidate | 文本列表，规则版能判定 MA60/MA20 类，AI 增强后可判定新闻类 |
| 最长持有 | max_hold_days | 时间止损 |

附加：
- `fundamental_outlook` (bullish/neutral/bearish) — 影响 PlanGuard 是否压制
- `thesis_sources` — AI 引用的策略库章节/新闻锚点
- `adjustments` — 调整审计（每条带 source: user/ai_suggested/auto_trailing）

---

## 动态调整 4 类触发器（adjuster）

| 触发器 | 何时输出 | 数据依据 |
|--------|---------|---------|
| `auto_trailing` | 浮盈 ≥5% 且新止损 > current_stop | high - 2×ATR(14)，策略库 ch48 |
| `target_reached` | price ≥ when_sell_targets[i] | 计划目标价 |
| `max_hold_warning` | 剩余 ≤ 7 天 | opened_at + max_hold_days |
| `invalidate_triggered` | 跌破 MA60 / MA20 死叉 | 失效条件文本 + 实时技术面 |

每条建议必带 `reason`（人话）+ `source`（数据锚点）。**所有调整必须用户 Y 确认才会写入 plan.adjustments**——AI 不会替你下单。

---

## FAQ

### Q：建仓时不想让 AI 生成 plan，怎么办？
A：在 `pos add` 提示"是否采用？"时输入 `n`，会跳过保存。后续可手动编辑 `portfolio.yaml` 的 `trade_plan:` 段，或重新建仓。

### Q：旧持仓没有 plan 怎么办？
A：`pos rm <代码>` 删除后再 `pos add` 重新录入即可（系统会自动生成 plan 草稿）。或者直接编辑 `portfolio.yaml` 手动加 `trade_plan:` 段（参考 `portfolio.yaml.template`）。

### Q：为什么 PlanGuard 不压制 take_profit_trim？
A：take_profit_trim 是按计划止盈分批落袋——这本身就是计划的一部分。PlanGuard 只压制"信号边际转空"的 weak_sell。

### Q：止损价能下移吗？
A：**不能**。adjuster 的 trailing 规则是单向上移：只有 `new > current` 时才输出建议。这是策略库 ch48 铁律：止损只上移不下移，保护已有利润不被吐出。

### Q：max_hold_days 到了一定要卖吗？
A：是的——这是时间止损。设计上要求你"到点重评估"，避免无限期套牢。如果你判断这只股票仍值得持有，可以编辑 plan 的 max_hold_days 延长（记得记录到 adjustments 审计）。

### Q：plan 里的 thesis 是 AI 真的看了基本面吗？
A：**当前阶段（v0.8.5）只看技术面 + 策略库**，不接真实财报数据。thesis 是 AI 根据 Weinstein 阶段 + MA/MACD 关系 + RAG 策略章节推断的"判断"。所以 thesis 准确性取决于：① 你建仓时的技术面是否清晰；② 后续是否手动维护。真实基本面接入留到后续版本。

---

## 相关文件锚点

- `src/data/models.py:TradePlan` — Pydantic 模型定义
- `src/data/portfolio.py:PositionRecord.trade_plan` — 持久化字段
- `src/core/trade_plan/generator.py` — 计划生成器
- `src/core/trade_plan/adjuster.py` — 动态调整器
- `src/core/plan_guard.py` — PlanGuard 守卫层
- `src/core/orchestrator.py:294-308` — 接入位置（Strategy → PlanGuard → Execution）
- `src/cli/main.py:_try_attach_trade_plan` — pos add 引导
- `tests/test_trade_plan.py` — 22 条单测全 PASS
- `portfolio.yaml.template` — YAML 示例
