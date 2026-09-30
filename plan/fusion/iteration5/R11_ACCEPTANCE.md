# R11：K系列交付独立审批

2026-10-01｜受审报告：[K系列实施方报告](../iteration4/实施方报告_K系列交付.md)｜代码HEAD `7dbe57d`，产品基线v0.8.24。Codex按用户架构师分工审批；未修改产品代码/测试/配置/真实账户，未提交Git。

## 一、审批结论

**分项接收，整批退回补齐验收。K1暂不冻结，K2b生产归并暂不放行，融合effective保持capture_only。**

| 交付 | 结论 | 依据与范围 |
|---|---|---|
| K0a | 原D2/D3/D4/D7修复通过；完整卡PARTIAL | 原探针关闭，写入与投影有改善；V3证明过期比例仍进入策略/组合消费者，未满足原K0a统一读取合同 |
| K0b | **带明确限制通过** | 原D1四例/D5/D6关闭，相关回归通过；仅验收已支持范围的资格门，不证明一般自然语言语义或LONG研究能力完整 |
| K0c | 原A1–A4所覆盖场景修复接收；完整去重合同PARTIAL | 双槽/精确引用/原动作变化回归通过；V2目标权重变化仍被吞。墙钟导致候选频繁新增已披露，需G2接线时收敛 |
| K2a | 通道实现接收；公开闭环PARTIAL | 原文导入与应用服务可用；V4从REPL证实checkpoint证据引用无法绑定；实施方“纵向全链”测试实际直调服务/影子stub |
| K1 | **退回修改，不冻结** | V1缺行情时点仍计有效，V2语义变化去重丢失；字段存在与完整可比较不是同一门槛 |
| K3 | 六点本地数值证据**带限制接收**；整体K3 PARTIAL | 六份PDF哈希/页内数值/比值复算通过；更正公告检索、表格视觉/人工复核、供应商当前重取及规则证据本轮未做，不能升级为严格历史全覆盖 |

原始报告保留，不改写历史测试成绩。新发现不是把已修的具体反例重新说成未修；它们揭示的是卡内尚未覆盖的消费者和完整合同。

## 二、独立验证记录

```powershell
.\.venv\Scripts\python.exe -B plan/fusion/iteration4/deep_review_probes.py
.\.venv\Scripts\python.exe -B plan/fusion/iteration5/review_test_runner.py
.\.venv\Scripts\python.exe -B plan/fusion/iteration5/acceptance_probes.py
.\.venv\Scripts\python.exe -B plan/fusion/iteration5/k3_local_evidence_check.py
```

- [原探针重查](K_COUNTEREXAMPLE_RECHECK.json)：10/10 observed_defect=false，无probe_error。原输出内review_baseline仍写def45d1，这是未修改的历史探针常量；实际本轮HEAD为7dbe57d，源码hash已随输出记录，不能把标签误作执行版本。
- [目标测试](TARGETED_TEST_RESULTS.txt)：**243 passed in 8.29s**，覆盖原10文件和新增7个K文件。读写门禁、禁网络、临时产物、portfolio及bak前后哈希一致。未独立重跑实施方1360全量；没有将“243”冒充全量。
- [新边界探针](ACCEPTANCE_PROBE_RESULTS.json)：5个场景、4类问题V1–V4均复现。全部合成，持久化限临时根；V4穿过start.parse_input→start.run_cli→research_command，只替换应用服务存储装配，生产求值逻辑未替换。
- [K3只读复验](K3_LOCAL_EVIDENCE_RESULTS.json)：6/6原件哈希一致，登记页内原数字存在，Decimal复算与记录一致。没有运行会联网及写真实研究库的verify_and_register脚本；没有把文本数值核对说成表格布局人工核定。
- 本轮不调用付费AI、不回放真实账户、不改映射注册表。静态结构盘点见[ARCHITECTURE_INVENTORY](ARCHITECTURE_INVENTORY.json)；本轮合同原型验证见[DESIGN_VALIDATION](DESIGN_VALIDATION.md)。

## 三、阻断发现

### V1：缺行情时点仍进入有效分母（P1，G6/L0）

`src/core/shadow_diff.py:519–521`把quote_cutoff固定None，但eligible只检查accepted/ref/aid/account_version。隔离接受计划和可解析评估下输出：quote_cutoff=null、eligible=true、drop_reasons=[]，报告MID有效数=1。与报告“缺时点不计eligible”直接不符。

同处evidence_cutoff来自捕获墙钟as_of，未绑定所消费证据快照截止；decision_rule_version直接复制policy_id。两臂完整输出还缺legacy目标权重、分别的执行约束。需要真实来源字段与统一资格函数；缺字段先diagnostic，不能为凑有效样本伪造时点/版本。已产生v6记录保留诊断，修正协议单独版本，不能事后追认。

### V2：权重变化仍被去重吞掉（P1，G6/L0）

`src/core/shadow_diff.py:597`的_output_fingerprint未消费binding.target_weight/blockers；输入相同、同分钟，把mid_binding.target_weight从0.1改0.2，输出指纹相同，第二次_append_record=false。这是记录边界反例，不宣称某条真实行情已触发此变更；合同要求的变化明确不可丢。

修复应规范化完整语义输入/两臂输出后去重，而非继续列几个拼接字段。capture时间不构成有意义变化，行情/证据截止、规则、目标、阻塞等构成；资格判定、指纹与报告消费同一合同。

### V3：数量投影标记未进入实际决策读取（P1，G1/L1）

`src/data/portfolio.py:1101/1121`写ratio_stale及quantity_fact，但`:434`的get_total_position_ratio和`:466`的to_strategy_state仍读取current_ratio原值。全src扫描中，ratio_stale/quantity_fact的引用集中于PortfolioManager存取，CLI/chat主分析未消费这项资格。

探针1：10%旧比例、部分卖后剩900股、ratio_stale=true，策略比例与组合合计仍0.1。探针2：旧比例0、数量买入后持有100股，策略比例/合计仍0。旧比例在特定价格下偶然等于新比例不能证明其合格；关键是未知事实没有传给消费者。`record_proposal`还有按旧比例≤0判断非持仓的分支（静态旁证）。

不能简单把float改None后让旧链崩溃，也不能用0作兼容替身。须在现有应用装配边界提供账户资格，quantity>0决定有仓；无合格估值/NAV时权重未知并抑制精确新增，硬风险/退出意图仍保留。RATIO_ONLY旧账户与数量账户分开适配，迁移不反造历史。

### V4：公开checkpoint无法绑定证据（P1，G2/G3/L2）

`src/cli/main.py:5433`固定构造evidence_refs=[]；应用层按原样传递，ResearchService要求非空已核引用。REPL输入本地合法订单原文，--checkpoint与--confirm-risk均给出，实际verified_claims=1、user_confirmed=true，但window_and_refutation=UNKNOWN。正向对照为同一应用服务、同一内容/时点，显式传证据引用后该命题TRUE。

这里只验证checkpoint，不声称整条MID已VALID。CLI没有表达这项引用的通道；用户点击接受计划也不能修补。应提供可见稳定证据ID选择与引用验证，缺/错引用给明确下一步；不能把所有导入事实自动绑定任何检查条件。正文来源真实也不意味着用户填写的任意复核条件被该正文支持。

实施方test_k2a_full_loop_fixture_to_active_ref直接app.run、store.accept、构造stub→capture_shadow，未经过REPL研究/接受/l分析全链，不能支撑报告所称完整纵向验证。后续验收须实走入口并独立核对逻辑状态与报告资格。

## 四、六项裁决请求

1. **K1冻结**：退回L0；补齐字段来源、资格、去重、两臂输出与公开纵向验证后再审批。冻结“记录合同”不代表策略发布。
2. **K2b放行**：设计和依赖盘点可准备；生产归并须L0/L1/L2过门。避免把当前账户读取和checkpoint缺口扩散到l/la。
3. **波次B**：允许做字段定义/原件可得性准备；按G4/G5消费方确定优先级，不要求本轮全做。currentRatio/quickRatio/assetToEquity逐字段核同报告期/合并范围/公式/行业适用性，不借一次提取宣称全部能力就绪。
4. **映射升级**：不覆盖v1，不作全证券全期间升级。先由L3形成可重建证据包、补更正/原件复核与登记脚本语义，再追加限定field×security×period×source版本的核定记录；核定数值不自动取得历史PIT资格。报告“umd_iss14”为命名笔误，实际ID为umd_iss114_liability_v1。
5. **E1b/E5b**：继续NOT_RUN；请求/token上限不等于付费授权。候选全集/名额/费用和独立留出集冻结后再提出具体授权请求。
6. **K4观察**：诊断可保留；晋级所需有效样本须新协议冻结后从零累计。不能用合成effective测试、旧v6标签或重复捕获代替真实独立观察窗。不能因缺付费授权停止无需付费的工程和数据资格工作。

## 五、后续范围

按[根里程碑](../../../MILESTONES.md)推进，当前只细化[第五轮L0–L3](DELIVERY_PLAN.md)。长期估值、组合、效果、性能/重构已有固定G/T承接，不因本次没有全部展开而消失。新原件、研究或调参需先作最小前提验证；不预先编造后几轮必定有效的方案。
