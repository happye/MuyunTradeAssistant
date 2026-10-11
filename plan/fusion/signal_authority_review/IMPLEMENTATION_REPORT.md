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
| ④正式回归与对账 | 本表即逐项「要求→落地→回归」映射；探针语义全部落库 `tests/core/test_iss117_qr3_closeout.py`（32 项）；既有 test_iss117_q_batch.py 3 个 live 正例补合格日历 fixture（旧实现靠 recent_trade_date 星期推算兜底碰巧通过——QR3 指出的 fail-open 路径，新合同正例=合格数据+合格日历） | 全量离线 **1656 passed / 2 skipped / 0 failed**（上轮 1622 + 本批净增 34；原始日志见 QR3_CLAUDE_FULLTEST_RESULTS.txt。注：本段初稿误记 1654/32 项，QR4 指出后更正——guard 盘前/盘后对照 2 项补入后正式文件实际收集 34 项）；架构师探针复核：qr3_calendar 8/8、qr2_boundary 6/6、qr_tail 3/3 全绿 |

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

---

## QR4 覆盖合同批实施报告（v0.8.29.5，2026-10-11，对应 QR4_ACCEPTANCE.md 两尾项）

### 逐项对照（要求→落地→回归）

**①Q-R5 日历覆盖合同（P1，QR4 实证两场景）**

| 要求 | 落地 | 回归（test_iss117_qr4_coverage.py） |
|---|---|---|
| 缓存增加独立 schema/来源/查询边界/已确认覆盖边界与**完整自然日日历记录**（每日期明确交易/非交易），不写无法复核的 complete=True | history.trading_days 生产路径：供应商 query_trade_dates 返回的**每一天**（date, flag）全部保留（不再只挑交易日）；缓存新增 `coverage: {schema:1, start, end, days:{date:0/1}}`，原 `dates` 兼容字段保留（fear 展示） | test_matrix4_production_writes_confirmed_coverage（生产→保存→读取→授权→packet 全链） |
| 生产核验：供应商原始日期/交易标志、唯一性、冲突、查询正常结束；缺一天/异常条目不得盖合格章；0/1 来自供应商不按星期补假 | 生产端五重核验：error_code==0、日期严格解析、唯一、flag∈{0,1}、**返回集合==查询区间自然日全集**——任何一关失败抛 RuntimeError 且不写缓存 | test_matrix4_supplier_missing_row_not_stamped / error_not_stamped / duplicate_not_stamped / bad_flag_not_stamped |
| strict 只读消费复核新 schema 与覆盖记录；分析日必须在已确认覆盖内；删除 12 天猜测覆盖与靠未来交易日作证明的路径 | strict 消费：复核 coverage schema/start-end 合法、days 逐条格式、**days 键集合==声称边界自然日全集**（消费端重算自然日序列，不信任生产者声明）；门里 `not (start ≤ 参照日 ≤ end)` → 覆盖待确认；12 天 gap 检查与 max(dates) 未来证明路径已删除（exit_signals/__init__.py live 分支重写） | test_matrix3_cache_ends_friday_monday_analysis_rejected / test_matrix3_future_row_cannot_prove_coverage；qr4 探针 4a/4b 转绿 |
| 缺元数据的旧日历继续服务 fear 展示，硬授权标 UNKNOWN，不自填范围追认 | strict 遇旧 schema 返回 `{"qualified": False, "legacy_dates": [...]}`（诊断用已知交易日），门一律拒绝；fear 展示（allow_refresh=True 列表语义）逐行未动；7 天 TTL 自然过期后由 fear 路径重建新 schema，无手工清缓存 | test_matrix4_fear_display_still_served_by_legacy_cache；矩阵行 2 五种变形（missing/expired/corrupt_built_at/unknown_version/no_coverage_meta）全部 UNKNOWN 且 attempts==[] |
| 盘前/盘中、15点后、周末/节假日用同份明确日历及场次；合格历史回测用独立 bar 上下文 | 门场次语义保留（交易日当日 15:00 前参照日前移一天）；新 schema 下假日授权依赖 days 明确 flag=0 记录；live=False 不走日历分支（独立 bar 上下文） | test_matrix1 四参数化（premarket/postmarket/weekend/explicit_holiday）+ test_matrix6 两场景（2000bar/2000证据授权；2026live/2000行情拒绝） |
| 门不联网/不自动刷新；缺日历提示可见；不取消独立保护 | strict 只读不变；诊断 reason 统一「覆盖待确认，三倍纪律暂为提醒」；test_matrix7 坏日历+价格 7<止损 8 → packet 仍 CLOSE_ALL | test_matrix7_bad_calendar_price_stop_still_closes |

**②Q-R1 行情主体证券独立核对（P2）**

| 要求 | 落地 | 回归 |
|---|---|---|
| 复算前核行情对象实际 stock_code 与请求证券一致（沿现有规范化口径）；缺失/非法/不一致拒授权并保留诊断；不改行情 stock_code、不只改复算 securities | 复算块前置核对：`stock_data.stock_code` 与请求 code 按 `split(".")[0]` 规范化比对（与 _event_applies_to_stock 同口径）；缺失/空/不一致 → reasons 显式诊断；**复算改传行情自身代码** `_check_triple_up_rule(stock_data, _data_code)`——复算 securities 独立来自数据主体，自证链路消除 | test_matrix5_unqualified_current_data_rejected_with_reason[wrong_stock] / test_matrix5_data_stock_code_missing_rejected / test_matrix5_suffixed_code_normalized_positive（带后缀正例）；qr4 探针 6 转绿 |
| 合格同股与合法历史正例保留 | 同股（含后缀规范化一致）授权不受影响；2000 bar/2000 证据回放授权保留 | test_matrix5_suffixed_code_normalized_positive / test_matrix6_historical_bar_replay_authorized |

### 验收矩阵 7 行覆盖状态

1. 合格完整日历×场次 → test_matrix1（4 例）✅；2. 缺/过期/坏/未知版本/缺元数据 → test_matrix2（5 例，无新网络）✅；3. 覆盖边界外/稀疏行 → test_matrix3（2 例）✅；4. 供应商缺行/错误/重复/坏标志 → test_matrix4 生产四例 + fear 展示兼容 ✅；5. 错股/无主体/缺时点 → test_matrix5 ✅；6. 历史回放合法 vs live 陈旧拒绝 → test_matrix6 ✅；7. 坏日历+有效止损 → test_matrix7 ✅。

### 探针状态（如实）

- qr4_context_probes 6 项：**5 绿**（4a 覆盖边界外、4b 稀疏行、6 证券核对本批转绿；1/2 历史回放与 live 陈旧保持绿）。**1 红（预期）**：test_qualified_live_calendar_still_authorizes_real_terminal——其 fixture（复用 qr3 cached_calendar）写旧 schema 缓存，按 QR4 矩阵「缺覆盖元数据→UNKNOWN」拒绝。依据 QR4「不以所有历史探针机械全绿为门槛」+ 指示 1「缺元数据的旧日历硬授权标 UNKNOWN」，该红为**新合同下的预期行为**，正例语义由本批正式回归 test_matrix1（新 schema 合格日历授权）承接；fixture 升级（写 coverage/days 字段，断言不动）待架构师复验时定夺，实施方未擅改架构师资产。
- qr3_calendar_probes 8 项：6 绿（探针 2 陈旧措辞在旧 schema 降级诊断中保留；探针 3-8 拒绝语义全部保持）；**2 红（预期，同因）**：探针 1 weekend/holiday 正例参数——旧 schema fixture 无法在新合同下授权（正例语义由 test_matrix1 weekend/explicit_holiday 承接）。
- q_acceptance「过期」用词项：按 QR4 裁决保持（旧 schema 降级路径的陈旧预检诊断含「陈旧」，等价语义）。
- 上轮遗留提请裁决的 s01 helper 断言：QR4 已裁决「该断言被新上下文合同替代」，无待办。

### 验证与监督

- 全量离线：**1681 passed / 2 skipped / 0 failed**（上轮 1656 + 本批净增 25：qr4_coverage 24 项 + qr3_closeout 适配净增 1）
- 架构师探针复核日志：QR4_CLAUDE_PROBE_RESULTS.txt；全量日志：QR4_CLAUDE_FULLTEST_RESULTS.txt
- code-quality-guard：一轮（对照 QR4 指示+验收矩阵+三条红线全复核）。结论「代码本体扎实，修占位符即可提交」。抓 P1×1（报告 {GUARD_RECORD} 占位符漏填——即本条，已填）+ P2×4（strict 消费端归一化重复键静默覆盖——已补与生产端对称的重复核验+布尔宽容收紧；ISSUES.md 既有重复拼接片段——已删；README/AGENTS「自然重建」表述弱化触发依赖——已按「跑一次 fear 即恢复」改准；跨年日历窗口——已登记 ISSUES 非缺陷）。确认项：决策路径零侵入/生产五重核验双层兜底/strict 契约清晰/场次×覆盖交互 fail-closed/矩阵 7 行断言真实（变异推演可区分新旧）/fear 展示兼容/无新告警面/诚实交付四红如实。
- 版本 v0.8.29.5（start.py/banner/--version/AGENTS.md/README/ISSUES/对齐指南）；CACHE_VERSION 未动（信号权限层）

**未实施**：S2a/S2b/S3——按 QR4「随后按 S2_DESIGN 执行 S2a→S2b→S3」，本批不含。全量隔离缺口（test_iss091 等写真实 HOME 路径）按 QR4 归 G7/T07 登记，不在本批范围。

---

## QR5 完整性收口批实施报告（v0.8.29.6，2026-10-11，对应 QR5_ACCEPTANCE.md 指示 1-4）

| 指示 | 落地 | 回归（test_iss117_qr5_integrity.py） |
|---|---|---|
| ①生产与 strict 都核对**精确集合** | 抽共用纯校验助手 `_verify_calendar_days(days_raw, start, end)`：逐键规范化+标志合法（布尔拒）+归一化重复键拒绝+**键集合与 [start,end] 自然日全集精确相等**——缺行、**范围外多行**均拒且差异写清（多行诊断「孤立旧日期不能证明覆盖、不得参与最近交易日判定」）。生产端行级核验后调用助手（供应商 431+1 行 → 抛错不写缓存）；strict 消费端以同助手替换原「只查 missing」逻辑 | test_producer_refuses_extra_out_of_range_row（查询参数+432 行遍历确实发生+不落盘）；test_verify_days_rejects_missing_and_extra_with_clear_diff（缺/多/重复/布尔/精确相等五态） |
| ②原始 JSON 解析阶段识别重复键 | 新增 `_pairs_without_duplicates`（json object_pairs_hook，任意层级 dict 生成前拦截）+ `_load_json_strict`（strict 专用：重复键/语法错误一律 RuntimeError fail-closed 带诊断）；**仅 strict 资格消费路径使用**，fear 展示宽容加载（_load_json）逐行未动 | test_raw_duplicate_json_key_rejected_before_dict_merge（原始字符串手写同键两次→解析期拒绝→真实末端 packet 不 EXIT/未授权/原因可见）；test_strict_load_rejects_duplicate_key_at_any_depth（顶层/嵌套/合法/缺失四态） |
| ③消费者只从已核定区间选最近已完成交易日 | 门内交易日选取加下界：`_cal["start"] ≤ d ≤ 参照日`（上游核验已保证精确集合，此处防御纵深）；场次截止/独立分析时点/历史 bar 语义不变，无墙钟否决历史、无 7 天行情政策、无 12 天猜测 | test_gate_selects_trading_day_within_confirmed_range（核定区间内正常授权不受防护影响） |
| ④三红反例落正式回归 | 上表三例全部入库，packet 级断言不 EXIT/未授权/拒绝原因可见；生产反例断言查询参数与遍历次数。完整生产全链合法正例、缺行/中途错/重复行/坏标志、错股、盘前/盘后、周末/假日、历史 bar、独立价格止损正例由 test_iss117_qr4_coverage.py（24 项）与 test_iss117_qr3_closeout.py（35 项）承接，本批全绿未动 | 本文件合计 6 项新增；全量 **1687 passed / 2 skipped / 0 failed**（上轮 1681 + 本批净增 6，原始日志 QR5_CLAUDE_FULLTEST_RESULTS.txt） |

**两项披露裁决落实**：年底供应商窗口保持保守边界（ISSUES 已登记非缺陷）；日历升级
过渡期表述全面改准（README/AGENTS/对齐指南）——仍新鲜的旧格式缓存由 fear 路径直接
复用（TTL 内不重建），仅过期/缺失后才重建，不承诺「跑一次 fear 立即恢复」，不要求
用户手动删除/编辑缓存。

**版本**：v0.8.29.6（start.py/banner/--version/AGENTS.md/README/ISSUES/对齐指南）。
CACHE_VERSION 未动。

**监督**：code-quality-guard 一轮（对照 QR5 指示 1-4+三红反例语义+文档裁决落实）。确认项：_verify_calendar_days 边界（缺/多同时存在 fail-closed、单日区间、430 天性能）、_load_json_strict 异常分流（OSError 原样上抛由门兜转 UNKNOWN，端到端 fail-closed）、strict/宽容路径分流正确且 legacy 行为不变、门下界与场次前移交互 fail-closed、三反例与架构师探针语义逐一对齐且非假红、文档三处裁决表述落实。抓 P2×3（报告占位符——提交前已填；AGENTS/README 版本段未随批 bump——已新开 v0.8.29.6 段；cov 边界非补零字典序比较可扩大授权面——已归一化回写 2 行）。全数修复后合入。

---

## S2a 实施报告（v0.8.29.7，2026-10-11，S2A_IMPLEMENTATION_TASK + S2A_IMPL_DESIGN.md）

设计稿先行落盘（S2A_IMPL_DESIGN.md：接口/公式/调用链/矩阵映射/缓存清单），同批实现。

### 要求→入口→落地→正式用例

| 冻结要求 | 入口 | 落地 | 正式用例（tests/core/test_s2a_aggregation.py，30 项全走产品聚合→真实 Strategy→packet） |
|---|---|---|---|
| §1 固定分母 E | skill_engine.load_skills/base_roster；orchestrator 接线 | load_skills 记录目标名单（None=目录全部）+目录发现集；base_roster 返回 (E, rule_versions)：显式名单∩type(base)（与 _load_skill 同口径）、**文件存在即启用（失败/缺失槽占 W）**、权重正有限校验（坏值 ValueError→orchestrator 捕获降级 v1-unknown-roster）、rule_version=YAML sha1 指纹 | frozen_matrix[one_of_four_to_.2]（4 槽 1 票 →A=.2 非 1）、failed_slot_still_occupies、missing/empty_roster 降级 |
| §2 一槽一票 | decision_engine._aggregate_votes | 槽=(skill_name, security, evaluation_as_of, rule_version)；同槽同票合一、冲突/坏值整槽 UNKNOWN 保留 W；错股错时点（票自带身份与上下文不一致）→UNKNOWN；不在 E 不参与；c=0 计覆盖贡献 0 | frozen_matrix[same_slot_duplicate_merged/same_slot_conflict_unknown/unregistered_slot_ignored]、removing_other_votes_does_not_strengthen、permutation_invariant |
| 坏 confidence 不 clamp | models.SkillSignal | confidence 构造期校验（NaN/Inf/越界 ValidationError），引擎另含 isfinite 防线 | invalid_confidence_rejected_at_model（4 参数化） |
| §3-§5 A/P/C | decision_engine | U_d=Σw·c；C=Σ合格槽w/W；m_i=1+c(m_legacy−1)（c≤0/坏跳过——不贡献不稀释）；M_d 几何平均；A_d=clip(M·U/W)（不按方向和归一化）；P_d=M·U/Σ(M·U)（分母0全0，仅展示） | frozen_matrix 全 14 行数值断言、zero_confidence_regulator_no_effect、confident_regulator_scales_not_creates（.01×1.5=.015）、regulator_cannot_create_direction |
| 方向门槛沿用 | _determine_final_signal | 结构/阈值/分支优先级不动（输入换 A 绝对值）；BUY 判据抽 buy_gate 静态方法（唯一实现） | frozen_matrix[RISK_ON/TRANSITION 两列逐行] |
| §6 门收口 | strategy_layer | 仓位/强卖读 action_strength（trace 中文 step 抓法退役）；HOLD→ADD 先过 buy_gate（实际不增仓转 HOLD）；反转成本/确认门读 _gate_score（action 覆盖→override_confidence 明确通道，否则 A——**AI/事件加分不绕基础资格**）；强止损极端检测读 A | packet_one_weak_vote_no_new_position（旧版 ADD 场景→HOLD）、packet_ai_bonus_cannot_bypass_gate（score 被抬、A 不变、仓位不动）、c0_valid_stop_still_exits |
| action 覆盖强度明确来源 | DecisionResult.action_override_confidence | 覆盖触发票置信度单独记录，不反灌普通投票 A（D03 兜底改走此通道） | c0_valid_stop（override 0.9 过确认门→EXIT/CLOSE_ALL 保持） |
| §4 降级协议 | make_decision(expected_base=None/空) | legacy-no-roster / v1-unknown-roster：A/P/C=None、决策=门槛函数对全 0 输出（不按成功票缩分母）、action 覆盖照常、拒因落字段 | missing_roster_degrades、empty_roster_degrades |
| §5 字段/证据/展示 | models + evidence + cli | DecisionResult 新增 5 具名字段；trace final_scores=A+protocol 标记；evidence 落 protocol/A/C 三字段（旧记录按 pre-s2a 读取不补假 A）；人话摘要加「信号口径」行（动作强度+覆盖率，不称胜率） | trace_final_scores_is_a_with_protocol；evidence/CLI 为展示层（人工验收项见下） |
| 缓存清单 | 见设计稿 §6 | decision score 无磁盘缓存；语义分桶靠 aggregation_protocol 随证据落盘；旧影子/旧 diff 不追认 | trace 协议断言 |

### 已知边界（如实）

- 跨槽同源去重按 S2_DESIGN §7 留 S3 证据 ID 矩阵，本批只做同槽去重——不冒称完成所有相关性。
- 影子（shadow_diff）本批仅协议隔离（新字段不流入旧影子格式），S3 桥接未实施。
- AI 调节作用于 score（调节后综合分）的既有行为保留；strategy 资格/仓位门已全部改读 A，
  AI/事件不再能影响普通票资格。
- CLI 人话摘要「信号口径」行与 chat formatter 的措辞同步为展示层，plan_guard/entry_exit
  不消费普通投票 A（无改动）。
- 全量：**1719 passed / 2 skipped / 0 failed**（上轮 1687 + S2a 净增 32——含 guard P2 双版本对照/evidence 一致性两测试；guard P1-1 修复后的 override 语义收口：base 与 action 同向不构成覆盖，门分值走投票 A，override 通道仅方向翻转时携带触发票置信度；全量偶发的 d04 断言随语义收口修正，复跑 0 失败；原始日志 S2A_CLAUDE_FULLTEST_RESULTS.txt）。回归适配 12 项如实分类：10 项测试构造按新合同适配（sell_split×5/l1×1/g_block 全量偶发×2 复跑绿/d04×2 合同替代——veto 强度从 score 反灌改为 action_override_confidence 明确通道，由 strategy _gate_score 消费，原 D03 病根不复发）；test_conditions（YAMLBasedSkill 无 path 构造）为产品健壮性修复（rule_version 容错 adhoc）。- guard：一轮（对照冻结公式/实施单/设计稿+全库 score 消费 grep+探针实证）。确认项：冻结矩阵算术逐行对上、A/P/C 公式与原型一致、槽逻辑边界、降级协议、buy_gate 等价性、门收口 grep 无残留、CACHE_VERSION 红线、版本四处同步。抓 **P1×2**（①is_extreme 强止损条件只读 A[SELL]——止损覆盖场景 A=0，首日确认门把止损 SELL 降 HOLD 延迟一天，违反「独立硬退出不被覆盖率取消」——已改 _gate_score 口径（override 优先）+设计稿同步+c0 测试改真覆盖 strategy 止损路径（无 PlanGuard 救回场景）；②scan 路径 enabled_skills=[] 默认——load 侧 falsy 加载全集、roster 侧当空集，同配置两入口行为分叉且静默降级——base_roster falsy 语义与 load_skills 对齐+main.py:2881 默认 None）。**P2×7** 全部顺手收口（make_decision 入口坏注册表校验防 ZeroDivision；override_conf 覆盖两条压制路径如实记录；action_strength 存原值单源化；降级注释措辞；chat formatter 信号口径行；双版本对照+evidence 一致性两测试补齐；_resolve_type falsy 显式值口径）。全数修复后合入。

---

## S2a 四组补修报告（v0.8.29.8，2026-10-11，S2A_ACCEPTANCE 四组 + S2A_CLOSEOUT_TASK 复验矩阵）

### 要求→实际源码 diff→正式测试 ID→终态（tests/core/test_s2a_aggregation.py 补修矩阵 16 项，全走产品聚合→真实 Strategy→packet）

| 组 | 源码 diff | 正式测试 ID | 终态断言 |
|---|---|---|---|
| **B/P1 止损不被同向取消**（先修） | decision_engine：override_conf **触发即携带**（同向不清——「没有翻转方向」≠「没有独立保护」，撤回上轮错误收口）；strategy_layer._gate_score 改**双通道取较大者**（止损同向不被弱普通 A 拖延，强普通票不被较弱 action 拉低——两担心一个 max 同时解决） | test_closeout_b_stop_survives_weak_same_direction_base_votes（主反例：A_SELL=.40 同向 → 当日 EXIT/CLOSE_ALL）、…_strong_same_direction…（A_SELL=.95 不被 0.85 拉低）、…_b_gate_score_takes_max…；既有 c0（C=0/方向翻转）保持 | EXIT/CLOSE_ALL 同日到达；A 不混硬强度；确认/极端保护同源 |
| **A/P1 固定预算身份核验** | skill_engine：抽 _typed_roster + regulator_roster（regulator 不再免检）；decision_engine._aggregate_votes：证券/时点/规则版本由**注册上下文**建立——评估时点与版本**缺失不得由票自报补齐**（缺→槽 UNKNOWN 保留 W 拒因可见），票自带身份与注册不一致（错股/错时点/**错版本**）→ 整槽 UNKNOWN 不投不计覆盖；UNKNOWN 槽隔离记录不污染合法槽组；_apply_regulators 重写为**槽化资格**（未注册/缺上下文/错股/错时点/错版本/非法权重（负/NaN）全部弃权留拒因，同槽重复合一/冲突弃权，合法正权重幅度公式与几何平均保留） | test_closeout_a_same_slot_wrong_version_copy…（主反例：副本不增预算 A=.20/C=.25）、…_single_wrong_version…、…_missing_asof_degrades_all_slots（quote_as_of=None→全槽 UNKNOWN）、…_regulator_duplicate_10x_same_as_single、…_regulator_wrong_security_version_abstain、…_bad_regulator_weight_cannot_flip_direction（−10/NaN）、…_zero_regulator_on_u0_not_clipped（U=0 不洗成方向票） | 配置预算不多发；错版不授权；重复/错股/坏权重调节不扩大资格；独立退出保留 |
| **C/P1 新增出口完整门** | strategy_layer：BUY 分支**按当前 state 重过 buy_gate**（AI/事件改 state 后旧 BUY 不得落地，过不了→HOLD）；BUY/HOLD 全部新增出口补「目标≤已知当前→HOLD_POSITION」（不保留 ADD 假动作）；analysis_service：ADD 目标未高于当前 → 终态**改 HOLD**（不靠删目标掩盖） | test_closeout_c_state_change_revalidates_buy_gate（force_state 改 TRANSITION 后不 OPEN）、…_target_not_increasing_is_hold（.60 当前 + 目标 .60 → HOLD）、…_c_unknown_weight_no_precise_add（L1 保留） | 全部新增出口同一当前门；目标未增→HOLD；未知权重不造精确增量 |
| **D/P1 记录口径** | evidence.py：diff_evidence 抽 _diff_records 纯函数，score 跨协议 Δ **不给数值**（标「口径不同，数值不可比」+双方协议；价格 Δ 照常；同协议 Δ 保留）；decision_contract.LegacyTrace 补 aggregation_protocol/voting_action_strength/opinion_share/coverage_ratio 四字段（旧记录 None=旧协议不补假 A）；analysis_service 构造同步；shadow_diff **SHADOW_DERIVATION_VERSION 升 shadow_v12**（S2a 聚合身份变化→旧 v11 整体降 older_versions 只诊断不进当前分母，既有分桶机制复用，旧原件不重写） | test_closeout_d_cross_protocol_score_delta_guarded、…_d_legacy_trace_carries_protocol_fields、…_d_shadow_version_buckets_old_v11_out | 跨协议不比数；新 A/P/C/协议同源落盘；旧 v11 不进当前分母 |

### 版本与全量

- v0.8.29.8（start.py/banner/--version/AGENTS/README）；CACHE_VERSION 不动。
- 全量离线：**1735 passed / 2 skipped / 0 failed**（上轮 1719 + 补修矩阵净增 16）（shadow 版本升级连带 4 文件 12 处版本硬编码断言同步 v12——历次升级同款做法，旧记录字符串不动）。
- guard：{GUARD_S2AC}
- 原报告更正（保留历史记录）：S2a 批报告「所有测试真实链」应为「数值表主要到 make_decision，部分到 packet」（S2A_ACCEPTANCE 已指出，本批补齐 16 项真实链）；「影子本批已协议隔离」「旧记录读时标旧协议」当时无源码闭环，本批完成。
