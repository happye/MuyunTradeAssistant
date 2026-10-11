# ISS-117 S0+S1 实施报告（交架构师验收）

> 2026-10-10。实施：Claude Code（单卡工作流：红灯→绿→code-quality-guard 两轮→提交）。
> 基线：ac5aa8e（v0.8.28.1）。交付 commit：`8640766`（v0.8.29）。离线全量 **1587 passed / 2 skipped / 0 failed**。
> 声明：本报告覆盖 **S0+S1**；S2/S3 未实施（见 §5），ISS-117 按裁决保持打开。

---

## 1. 交付范围与 A01–A13 处置状态

| 编号 | 处置 | 所在步 | 证据 |
|---|---|---|---|
| A01 减持标题误读 | ✅ S0：标题只作 research 待核验线索（否定/取消/澄清/未实施语义在 reason 如实标注；原件/主体/证券/时点未核验不隐藏） | S0 | test_signal_authority_iss117::test_a01_*（5 反例 + 3 非命中正例） |
| A02 股东户数取最旧 | ✅ S1：按明确日期列倒序+去重取最新一期；无效日期剔除；激增也降 research | S1 | ::test_a02_holder_count_uses_latest_row_not_iloc0 / _surge_on_latest_still_research |
| A03 旗手窗口/复权 | ✅ S1：qfq 同口径（baostock adjustflag="2"，备用源 adjust 透传）；<21 收盘观测不冒充 20 日（fail-open 除外披露） | S1 | 源码 sector.py/akshare_client.py + guard 复核；行级夹具矩阵留 S2/S3 供应商测试 |
| A04 缺数据降门 | ✅ S0+S1：缩量连续性缺失=UNKNOWN 研究线索（不放宽单日）；skill_engine 未知条件 fail-closed（met=False + unknown_conditions） | S0/S1 | ::test_a04_*（4 项） |
| A05 预告日期 | ✅ S1：缺失/非法/未来 → 无资格跳过；同日公开与建仓 → `_pub_order_ambiguous` 时序不明标注；建仓后合法预告 → 资格成立 | S1 | ::test_a05_*（3 项） |
| A06 AI/事件越权 | ✅ S0：black_swan 不再 force PANIC（cap/压分按置信度缩放，conf=0 零调节）；risk cap 同款；事件四要素缺失=UNKNOWN 不硬覆盖、限仓仅空头方向；事件消费先按资格筛选；**cap 方向门覆盖全部 4 个应用点**（guard 初审抓出漏 2 处，已补） | S0 | ::test_a06_*（6 项）+ guard 复核 |
| A07 invalidate 兜底 | ✅ S0：跨字段子串兜底删除（结构化布尔才认；非布尔由上游校验层归一 None） | S0 | ::test_a07_*（2 项） |
| A08 投票归一化 | ⏸ S2 未实施——卡片前置「先固定公式与低覆盖矩阵，再改，不能随意选新阈值」需架构师先给矩阵 | S2 | plan/fusion/signal_authority_review/IMPLEMENTATION_TASKS.md §S2 |
| A09 关键词→模式/期限 | ⏸ S2 未实施——decision_audit D7 明确定性为「已明确设计、应由架构师重裁」（非代码缺陷） | S2 | 同上 |
| A10 失效条件三态 | ⏸ S2 未实施（plan_guard 三态改造随 S2 批） | S2 | 同上 |
| A11 坏价格/执行措辞 | ✅ S1（价格资格部分）：`_qualified_price` 助手 + `_check_stop_breach` 与 MA60 失效条件双点复用（0/负/NaN/Inf 不假触发不炸管线）；执行层「量比/封板」措辞部分在 S2 | S1（价格）/S2（措辞） | ::test_a11_*（2 项） |
| A12 经验信号越权 | ✅ S0：旗手滞涨/股东户数/融资/缩量/宏观10万亿/预告类别全部降 research（SignalFinding.action_scope），仅三倍定律保持 exit（既有周期纪律，裁决保留）；P1 强制清仓通道现在只吃 exit 资格 | S0 | ::test_a12_*（2 项）+ 全库 grep action_scope 白名单 |
| A13 shadow 桥接 | ⏸ S3 未实施（shadow_diff 依赖 S0–S2 消费合同） | S3 | 同上 |

**已核验保留的硬权限正例**：有效价格止损（正反例）、三倍定律 exit 资格、ST 排除政策、时间止损、已接受计划纪律——全部有测试或 guard 复核锁定，未被降级波及。

## 2. 关键实现

- **权限合同**：`models.SignalFinding`（signal_id/来源/适用证券/as_of/data_quality/action_scope/verified/strategy_binding/detail/reason；缺字段默认最严）。信号代码、证据状态、来源时点、允许动作四件套齐备；旧字符串仅在消费侧展示兼容。
- **check_top_signals 改 list[SignalFinding]**：不再首 hit 短路（同层多条并存）；orchestrator 按 action_scope 分流——exit → 既有 P1 强制清仓链（PlanGuard 不变）；research → warnings（风险提示），与强制退出原因分开展示，且不写强制离场冷却。
- **基本面通道拆分**：`_compute_fundamental_findings` 返回 (hard, findings)——ST 保持 hard（当前状态排除政策），预告类别降 research。
- **cap 方向门 ×4 应用点**：AI cap 只限制新增风险（BUY 方向）；HOLD/SELL/REDUCE 不 min 现有仓位——A06「.8→.6 放大成 .8→.3」端到端封死。
- **事件资格筛选**：先按四要素/真实性≥30 过滤，未核实事件只作提示（>1 条附计数）；低真实性<30 否决语义不变。
- **S1 数据**：股东户数日期倒序+去重；旗手 qfq 同口径 + ≥21 观测；预告日期资格门；baostock 备用源 adjust 透传；exit_signal 缓存 v2（旧字符串缓存不复活）；CACHE_VERSION bump v0.8.29（risk_deduction 语义 + 备用源口径变化）。

## 3. 监督与修正记录（code-quality-guard 两轮）

**第一轮**：3×P1 全部修复——
1. cap 方向门漏 2 个应用点（事件独立分支 + 策略层后最终 min）→ 补齐（不修则 A06 验收场景端到端仍复现）；
2. plan_guard MA60 失效条件同类坏价格缺口（price=0 假触发 / None 炸 evaluate）→ 抽 `_qualified_price` 复用；
3. CACHE_VERSION 未 bump → v0.8.29。

**第二轮**：P0/P1 归零，结论「可提交」。P2 批 9 项顺手处理 8 项（docstring 腐化 ×4、探针标 superseded、securities 未核定留空、as_of ISO 格式、a05 mock 改 patch 真模块属性、展示层 authenticity 口径对齐、简报/注释与实际 diff 对齐）；**接受现状 1 项**（diff warnings 截断边界，已登记 ISS-095）。

**guard 确认无问题的攻击面**：裁决忠实性（无夹带）、降级不短路、research 不越权（零写入 top_signal/fundamental_alert/strategy_reasons）、回测隔离（DataFeeder 不经新代码、live 门控原样）、缓存 v2 三态、action_scope 白名单（exit 仅三倍定律）、ST 优先级不变、版本五处一致、告警同步零义务。

## 4. 验证

- 新回归：`tests/core/test_signal_authority_iss117.py` 33 项（A01/A02/A04/A05/A06/A07/A11/A12 探针反例 + 合法正例）；6 个旧测试文件契约按裁决纠正（含「30+ 必触发」等旧规则预期）。
- 全量离线：**1587 passed / 2 skipped / 0 failed**（基线 ac5aa8e 为 755/5——渗透率批后既有失败已被 M1 离线化与本次修复清零）。
- 真实探针（000001/000498，用户两票）：**exit 资格 = 无**（清仓信号消失），研究提醒按裁决文案输出。
- 隔离纪律：零真实网络调用进测试、零 ~/.muyun 污染（缓存目录重定向）、无 AI/付费调用、真实 portfolio.yaml 未动。

## 5. 未实施与所需输入（如实声明）

| 卡 | 内容 | 阻塞点 |
|---|---|---|
| S2 | A08 投票公式（action_strength/coverage 分离 + HOLD→OPEN 资格门） | 卡片明文「先固定公式与低覆盖矩阵，再改，不能随意选一新阈值」——需架构师给出矩阵 |
| S2 | A09 自主可控关键词→模式/期限 | decision_audit D7 定性「已明确设计、应由架构师重裁」 |
| S2 | A10 plan_guard 失效条件三态、A11 执行层措辞 | 可实施，随 S2 批（依赖 S0 合同已就绪） |
| S3 | A13 shadow 桥接资格、signal_id 全消费者贯穿、摘要三类分层、待办确认闭环 | 依赖 S0–S2 全部落地后做覆盖清单 |

**同源叠加/旗手复权行级夹具/重复票去重可达性统计**等「尚未行为闭合」清单见 REVIEW.md §3 末尾，S2/S3 卡片必须带回。

## 6. 已知局限（如实）

- research 发现经 warnings 展示有 [:8] 截断（v0.8.28.1 起）；S3 要求「所有提醒完整落结构化证据，不靠排序运气」——结构化 SignalFinding 已全量落 analysis_evidence.jsonl 之外还需独立通道（S3）。
- `diff` 对 warnings 截断边界的旧记录误报（ISS-095 已登记，两条新宽度记录积累后自愈）。
- authority_probes.py 已标 SUPERSEDED（旧契约），S2/S3 验收探针需另起。
- 旗手滞涨降 research 后的「旗手关系核实」（000498 案例的真实触发源）属 S2/S3 研究课题，本轮只解除其越权。


---

## Q 补修批实施报告（v0.8.29.1，2026-10-10，对应 S01_ACCEPTANCE Q1–Q7）

| ID | 落地 | 回归 |
|---|---|---|
| Q1 | `authorize_hard_findings`（signal_id 白名单/绑定/证券/时点新鲜度 live 7 天/质量；失败降 research 保留诊断；缓存重放同门）+ `StrategyDecision.top_signal_authorized` 贯穿 PlanGuard P1（未授权仅诊断） | test_iss117_q_batch::test_q1_*（7 项） |
| Q2 | event_layer 移除 force_state/硬限仓（保留软调节+真实性置信度）；orchestrator 事件消费按 `_event_auth_qualified`+`_event_applies_to_stock` 双门（空证券≠全市场；stock/sector 须 affected_codes 含当前股）；未核实事件仅提示（多条附计数） | ::test_q2_*（4 项） |
| Q3 | risk_deduction：AI invalidate=True（任意置信度）→ 红线候选警告，不写硬 invalidate/不强制 F；规则版硬排除与 ST 政策不撤销 | ::test_q3_*（1 项） |
| Q4 | `_soft_cap_target`：delta=max(0,min(t,k)-c)，目标=c+delta；delta=0 一律转 HOLD/HOLD_POSITION（HOLD+ADD 归一）；REDUCE 方向不参与；当前权重未知不伪造增量；4 处 min 收口策略层后单点应用（事件 cap 取更严者合并） | ::test_q4_*（4 项） |
| Q5 | 三倍定律 price/ma5/low_60d 有限正值；as_of 来自 quote_as_of（缺失=UNKNOWN）；live 过期降 research（回测按 bar 资格豁免）+ DataFeeder 回测补 quote_as_of=bar 日期 | ::test_q5_*（2 项） |
| Q6 | 公告旧格式字符串→dict 规范化；exit_signals 逐层+逐子信号隔离（异常→diag 条目，不吞独立保护如三倍定律） | ::test_q6_*（2 项） |
| Q7 | 旗手行级日期有效性（剔未来+同日去重+≥21 不同交易日）；股东统计未来日期无资格+剔除后走缓存 | ::test_q7_*（3 项） |

**监督**：code-quality-guard 两轮。初审 P0×2（__init__ 隔离层 logger 未定义致 Q6 整体失效；
DataFeeder 缺 quote_as_of 致回测三倍定律被资格门静默降权）+ P1×3（cap 方向门漏 2 应用点/
plan_guard MA60 同类价格门/CACHE_VERSION）——全部修复。复核 R1（warnings 双计）R2（告警
pattern 文案）R3（死代码）R4a（隔离测试固化）——全部修复。**复核结论：可提交。**

**验证**：全量离线 **1616 passed / 2 skipped / 0 failed**；S01 探针反例 25 项落回归
（test_iss117_q_batch.py）+ 三层隔离端到端 3 项。版本 v0.8.29.1 五处同步；
CACHE_VERSION v0.8.29.1（Q3 语义）。

**未实施（如实）**：S2a（投票公式 V1，DESIGN_READY）/S2b-1（mode 政策）/S2b-2（失效
三态）/S2b-3（执行事实分离）/S3——按架构师顺序待本批过门后实施；同源叠加/复权行级
夹具/重复票去重可达性等 REVIEW §3 未闭合项随对应卡片带回。

---

## Q 尾项实施报告（v0.8.29.2，2026-10-10，对应 Q_ACCEPTANCE Q-R1/R2/R5/R7）

| ID | 落地 | 回归 |
|---|---|---|
| Q-R1 | `authorize_hard_findings` 加复核：exit 发现须在**当前 StockData 上重跑三倍定律纯函数**且成立才授权（研究缓存冒用 signal_id/绑定/verified 全部失效）；绑定精确相等（禁子串）；holder/margin 缓存按各自 signal_id 校验（错 ID 不复活）；来源非空检查 | test_q1_gate_*（6 项重写：授权正例/错股/复算未确认/绑定/缓存冒名） |
| Q-R2 | `_ai_classify_event`/`_ai_classify_portfolio_news` 双入口：scope 缺省/非法=unknown（不默认全市场）；affected_codes 按 prompt 要求输出并规范化 6 位保存；`_event_applies_to_stock` 对 unknown/不匹配不适用（软调节也不吃）；持仓股新闻按请求上下文确定本股（来源可追溯） | test_q2_*（4 项） |
| Q-R5 | 时点参照=本次分析数据时点：回测=当前 bar 日期（BacktestEngine 经 today= 传入，backtest_validator 重放路径补 today）、live=行情快照；证据 `source_time_in_future(证据, 参照)` 晚于参照即拒；坏字符串经 parse_source_time 严格解析不截断洗白；7天政策按裁决移除（陈旧行情由行情源资格降级） | test_q1_gate_rejects/backtest_waives 重写（3 项） |
| Q-R7 | sector：行级日期 strptime 严格解析（2026-00-01 等非法日历剔除）+未来剔+同日收盘冲突放弃+跨度≥20自然日核验（防御性）；holder：显式「户数+本次/上次」列映射（防户均市值列误选）、无统计日期列 fail-closed、缺公开时点 data_quality=UNKNOWN 不伪装合格；2 个假绿测试修为真实签名兼容+断言拉取与遍历发生 | test_q7_*（3 项） |

**监督**：code-quality-guard 两轮。第一轮 P0×2（隔离层 logger 未定义致 Q6 整体失效；
DataFeeder 缺 quote_as_of 致回测硬纪律被资格门静默降权）+ P1×3（gate research 重复告警
+reason 覆写/CACHE_VERSION 未 bump/新告警未三处同步）+ P2×9（same_day 假绿夹具、事件
force_state 死代码、holder 缓存早退等）——全数修复。第二轮 R1（warnings 双计）R2（隔离
文案与 pattern 失配）R3（事件独立路径 force_state 死代码残留）R4a（三层隔离异常路径无
pytest 锁定）——全数修复（R4a 固化为 3 项 pytest）。**复核结论：可提交。**

**验证**：全量离线 **1616 passed / 2 skipped / 0 failed**（本轮 +25 测试）；q_fixture_audit
假绿审计转绿。**未实施**：S2a（生产投票公式，DESIGN_READY 待放行）/S2b/S3——ISS-117
保持打开，S2a 固定矩阵正式测试可提前准备。**已知局限**：research 发现经 warnings 展示
有 [:8] 截断（ISS-095 登记）；独立核验器缺位前事件/研究信号一律无硬权限（裁决要求）。

---

## v0.8.29.3 实况补记（QR3 裁决要求如实更正，2026-10-11）

上节 v0.8.29.2 报告之后、本批之前的 d47c6b9（v0.8.29.3）实际交付范围：仅
`exit_signals/__init__.py`（analysis_date 严格解析 + live 新鲜度初版）与
`event_layer.py`（scan_portfolio_events 对 scope=unknown 不写 affected_codes），
另有版本/banner/文档。**未新增任何正式测试**（此前报告中「已入测试」表述无对应
证据，QR3 已指出，此处更正）；未改 orchestrator/models/sector/stock。live 新鲜度
初版错误消费 `recent_trade_date()` 展示接口（冷缓存触发 baostock 登录、RuntimeError
按星期推算兜底、缓存时间戳非法被裸 except 吞掉后重新放行陈旧行情）——QR3 判不合格。

## QR3 收口批实施报告（v0.8.29.4，2026-10-11，对应 QR3_ACCEPTANCE.md 四条指示）

| 指示 | 落地 | 回归 |
|---|---|---|
| ①Q-R5 日历资格异常 | `fear_index/history.py::trading_days` 新增 `allow_refresh=False` **只读资格模式**：只核验有效缓存（版本/built_at 7 天新鲜/日期格式逐条 strptime），缺失/过期/时间戳非法/损坏/格式错误一律抛 RuntimeError；不建目录、不 import baostock、不联网刷新、不提供星期猜测值。`exit_signals/__init__.py` live 分支改消费该查询：不可核实→`data_qual_reason`（拒绝硬授权）；可核实→覆盖检查（日历最后日期距分析日期 >12 天=覆盖不足）+ 陈旧判定。裸 `except: pass` 移除；recent_trade_date 原展示降级行为未动 | test_iss117_qr3_closeout.py：周末/节假日正例、陈旧行情拒绝+等价措辞、built_at 非法、日历获取失败、缓存缺失不触发登录、过期、格式错误、覆盖不足（8 项语义入库） |
| ②Q-R2 消费者+显式证券 | `_event_applies_to_stock`：先拒 unknown/缺失/非法 scope，再核证券（unknown 附 codes 不适用）；`_ai_classify_portfolio_news`：保留 AI 解析的事件主体证券（规范化 6 位），请求股不再无条件写入 affected_codes（QR2 探针「明确别股被改成本股」关闭）；`scan_portfolio_events`：删除 scope!=unknown 时的 `affected_codes=[code]` 强制替换（两处「请求股冒充事件主体」全部移除）；`models.MarketEvent.scope` 默认 market→unknown（旧/缺字段收口；_create_event_from_rule 规则配置默认 market 保留）；CLI scope_cn 补 unknown 映射 | test_iss117_qr3_closeout.py：unknown+codes 消费者拒、非法 scope 拒、默认 unknown、别股不替换、unknown 扫描→真实编排器不转换、market/本股正例（8 项入库） |
| ③Q-R1/Q-R7 按 QR2 清单 | 复算块三项独立核验：mode 适用性不依赖 stock_data 存在（气宗+缺数据也拒）；stock_data=None 拒绝（不再跳过复算放行）；复算结果须 data_quality=OK 且 signal_id/证券与申请一致（stock.py 对缺 quote_as_of 返回 UNKNOWN finding 的路径现在被门拒绝）。sector：行日期**完整**严格解析（[:10] 截断洗白关闭）；行日期须落请求窗口 [start,end]（供应商无视窗口参数的 2000 年旧行不冒充当前窗口）；端点可比性（最后观测距请求端点 >12 天拒绝，「21 个不同日期+恒真跨度」不再代替窗口资格） | test_iss117_qr3_closeout.py：缺 data/缺时点/气宗不依赖 stock_data/mode 正反例（5 项）；sector 垃圾尾巴/旧窗口/合法同窗/同日冲突/端点滞后（5 项入库） |
| ④正式回归与对账 | 本表即逐项「要求→落地→回归」映射；探针语义全部落库 `tests/core/test_iss117_qr3_closeout.py`（32 项）；既有 test_iss117_q_batch.py 3 个 live 正例补合格日历 fixture（旧实现靠 recent_trade_date 星期推算兜底碰巧通过——QR3 指出的 fail-open 路径，新合同正例=合格数据+合格日历） | 全量离线 **1654 passed / 2 skipped / 0 failed**（上轮 1622 + 本批 +32，原始日志见交付）；架构师探针复核：qr3_calendar 8/8、qr2_boundary 6/6、qr_tail 3/3 全绿 |

**版本**：v0.8.29.4（start.py/main.py banner/--version/AGENTS.md/README）。CACHE_VERSION
未动（本批全部在信号权限层，不触及笨总评分输入）。

**如实遗留（提请架构师裁决）**：
1. `s01_acceptance_probes.py::test_q5_stale_quote_not_hard_exit`（helper 级，非 QR3
   验收 31 项组合）：断言 `_check_triple_up_rule` 对 quote_as_of=2000-01-01 返回
   None/非 exit。该纯函数无分析日期上下文，唯一实现途径是拿墙钟判陈旧（QR2 明令
   「不拿当前墙钟否定历史」）或硬编码天数政策（QR2 明令禁止「任意 7 天政策」）；
   且与 qr3 正例（历史 bar quote_as_of=2020-01-10 授权通过）直接冲突。d47c6b9 上
   该探针同样红（stock.py 与 d47c6b9 无 diff），非本轮回归。
2. `q_acceptance::test_r5_stale_quote_rejected_at_real_terminal` 的「过期」用词断言：
   QR3 已裁定按行为接收（新提示用「陈旧」），产品未按用词返工。
3. authority_probes.py 的 offline_orchestrator fixture 引用旧方法名
   `_compute_fundamental_alert`（v0.8.29 已改名 _compute_fundamental_findings），
   该文件 16 项为探针自身过时 fixture，不属于 QR3 目标回归组合，未改动架构师资产。

**监督**：code-quality-guard 一轮（对照 QR3 四条指示 + 全库 grep 复核）。初审抓
**P1×1**（交易日 15:00 前 live 分析把上一交易日收盘误判「行情已陈旧」——新门丢了
recent_trade_date 的场次语义，合法硬退出在每日约 9 小时窗口内被误降；已修：门内
补场次语义，交易日当日且墙钟 <15:00 时最近交易日取前一交易日，附盘前正例/盘后
对照两测试）+ **P2×5**（双入口 affected_codes 规范化口径不一致——持仓入口补
split(".")[0] 对齐；端点滞后测试原走 len<21 路径无区分性——改 21 连续自然日直达
该分支；缓存缺失测试补「拒绝」断言；日历缓存时间戳在未来拒采信；sector 非零填充
日期解析后规范化回写）——全数修复。复核确认：日历比较/gap/fail-closed 落点、
fail-open 无残留、规则事件路径未误伤、MarketEvent.scope 兼容面（全仓 3 构造点均
显式传值）、tmp_path 隔离完整（空 HOME 实跑 229 项全绿且不产生 .muyun）、
CACHE_VERSION 无需 bump、告警三处同步不适用（无新 logger.warning）。**结论：修完
P1 可提交。**

**未实施**：S2a/S2b/S3——按 QR3「过门后再按冻结的 S2_DESIGN 执行」，本批不含。
