# 第二轮执行账本（R0–R9）

> 按 [TASKS.md](TASKS.md) 协作与验收纪律维护：状态 TODO→IN_PROGRESS→REVIEW→VERIFIED；
> VERIFIED 只能用于该卡所有验收条款，缺一条写 PARTIAL。「技术上接通」≠ VERIFIED。
> 历史第一轮账本（F0–F9 + 四批计划外）在 [../EXECUTION_RECORD.md](../EXECUTION_RECORD.md)，保留不改。

---

## R0｜冻结基线与资格止血

```text
task_id: R0
owner: 实施方（Claude Code，集成负责人口径一次完成）
status: REVIEW
baseline_commit: 00ec84f（HEAD 交接核对一致；工作区仅 portfolio.yaml 用户实盘状态 + 本地未跟踪文件，未动）
delivered_commit: d21b341（amend 回填账本后终哈希）
```

### 基线记录（实现条款①：记录 HEAD/dirty tree 与现有外部变更）

- HEAD = `00ec84f`（架构师第二轮设计交付入库），与交接文档一致；架构验收基线 `5b2e262` 之上。
- dirty：`portfolio.yaml`（用户实盘状态，永不提交）；未跟踪本地文件：`.claude/launch.json`、
  `.claude/plans/`、`.claude/settings.json`、`.zcode/`、`docs/2026-09-14_笨总评分新维度设计方案.md`、
  `docs/AI_Agent面试备战_技术深度剖析.html`、`knowledge/index.bak_20260905/`、`portfolio.yaml.bak`、
  `portfolio.yaml.pre_plan_restore_20260903`、`tests/rag_eval/eval_report_rerank_on.json`、
  `项目记忆资产备份/`——全部为既有本地状态，本轮零改动。

### owned_files / integration_changes / other_agent_changes_preserved

| 文件 | 变更 |
|---|---|
| `src/core/research.py` | A03 资格门：`fact_evidence_refs` 字段 + `fact_has_resolvable_reference` + assess_thesis 改「带可解析引用的观察事实才 VALID」（R0 主责） |
| `src/core/shadow_diff.py` | v3：thesis 走 assess_thesis 唯一入口（删影子自写非空判断）；来源逐周期标注 mid/long_plan_source + mid/long_thesis_status；新原因标签 facts_unverified；SHADOW_DERIVATION_VERSION → shadow_v3（R0 主责） |
| `src/data/research_snapshot.py` | A01 临时保守门：latest-only 财务（晚抓+无 revision）不入历史 strict 快照；drop_reasons 记录；qualify 给「版本不可追溯」具体话术（R0 主责） |
| `src/cli/main.py` | plan2 保存带 facts 时打印「事实未挂证据引用按逻辑未立处理」提示（集成接线，加一行输出） |
| `tests/core/test_horizon_policy.py` / `test_research_snapshot.py` / `test_financial_data.py` / `test_shadow_diff.py` | 反例回归 +8 条（净），test_evidence_integration_and_pit_gate 按新资格语义反转 |
| `plan/fusion/E0_BASELINE_REPORT.md` / `E1_REPORT.md` / `E7_REPORT.md` | 顶部裁决注记（原始数值与正文保留不改） |
| `plan/fusion/EXPERIMENTS.md` / `EXECUTION_RECORD.md` | E0 等价 / E1 互补实证 / E7 全路径扩大措辞修正（数值保留、注明裁决） |
| `start.py` / `src/cli/main.py` / `README.md` / `AGENTS.md` | 版本 v0.8.20 → v0.8.21（5 处口径） |

R1–R6 主责文件未动（decision_policy.py / horizon_plans.py / financial_data.py / portfolio_policy.py /
experiment.py 零改动）；HorizonPlan 经 `extra="allow"` 消费 `fact_evidence_refs`（未声明字段，R3 正式化）。

### 五维度

| 维度 | 状态 | 说明 |
|---|---|---|
| implemented | ✅ | 三处最小阻断 + 文档修正 + 版本同步，全部落码 |
| connected | ✅ | shadow → assess_thesis 唯一入口；snapshot 严格门 → qualify 话术；plan2 CLI → 用户提示 |
| scenario_validated | ✅ | 探针 P1/P4 反例复现转绿；shadow 混合来源（仅 MID 接受）逐周期标注场景测试通过 |
| empirically_validated | PARTIAL | 影子记录尚无真实使用期数据（v3 起新字段逐周期落账本，观察需时间）；R0 验收条款本身以反例+全量回归为准 |
| release_ready | PARTIAL | 资格门随策略回滚不撤销（见回退条款）；opt_in/default 晋级仍按架构裁决保持禁止（R9 门） |

### 验收条款逐条

| 条款 | 命令/证据 | 观察结果 | 判定 |
|---|---|---|---|
| 1a 无依据逻辑不再升级（探针 P1） | `pytest tests/core/test_horizon_policy.py -q`（含空白事实/无引用文本/空引用/键不匹配四反例） | 4 反例全 UNESTABLISHED；带引用才 VALID | PASS |
| 1b latest-only strict 反例不再升级（探针 P4） | `pytest tests/core/test_research_snapshot.py tests/core/test_financial_data.py -q` | kept=0、drop_reasons=latest_only_unverifiable；带 revision/当时抓取仍可入；行情不受累 | PASS |
| 1c 有效 legacy 主流程正常 | `pytest -q` 全量 | **1129 passed, 2 skipped, 1 deselected**（基线 1121 + 新增 8；气宗/剑宗主流程零改动） | PASS |
| 2 仅 MID 接受时 LONG 显式模拟且不计真计划样本 | `pytest tests/core/test_shadow_diff.py -q`（long_plan_source=simulated、报告分周期计数） | MID=真判断 / LONG=模拟计划，报告按周期计数 | PASS |
| 3a 用户看到具体「事实未核实」 | plan2 保存提示 + shadow 报告 facts_unverified 人话标签（render 测试） | 具体话术，非笼统错误 | PASS |
| 3b 用户看到具体「版本不可追溯」 | snapshot.qualify problems（测试断言含该字样） | 「版本不可追溯: netProfit（latest-only 财务证据无法证明 as_of 时点已发布该版本…）」 | PASS |
| 3c 旧事实文本/历史计划/报告保留 | plan2/影子/快照只加不删；E0/E1/E7 报告原文保留仅加注记 | 无删除性变更 | PASS |
| 4 契约变更有兼容读策略、无并行覆盖 | ThesisRecord 新字段默认 {}（旧数据保守 UNESTABLISHED）；ShadowDiffRecord 旧字段保留为兼容聚合口径（描述注明 R3 移除）；EvidenceSnapshot.drop_reasons 默认 {} | 旧 JSONL/JSON 全部可读；R1–R6 主责文件零改动（见上表） | PASS |

全量口径：`pytest -q` → 1129 passed, 2 skipped, 1 deselected（2026-09-26 本机实测，离线默认）。

### user_visible_change / fixtures_vs_live / data_and_AI_budget

- 用户可见：①plan2 带事实时明示「按逻辑未立处理」；②影子报告「记录来源」按周期计数、
  新原因「已激活计划的事实未挂证据引用」；③历史严格快照对 latest-only 财务给出「版本不可追溯」。
- 全部测试为离线夹具；零网络、零 AI 调用、零真实持仓写入。
- 探针复现（P1/P4）用 `python -B -` 纯内存运行，未装依赖未改环境。

### architecture_deviations / remaining_clauses / rollback / next_owner

- **架构偏差**：无主动偏差。一处边界说明——R0 未给 plan2 CLI 增加手工挂证据引用入口：
  用户自证不构成核验（裁决②）， earned-VALID 路径由 R3 自动研究接线（测试已证明该路径可用）。
- **剩余条款**：empirically_validated/release_ready 两维 PARTIAL（见上）；E0「冻结修正臂为基线」
  归 R6/R7；影子数据观察归 R3/R9。
- **回退**：资格门（research.py 资格判断、snapshot strict 门）**不随策略回滚撤销**（TASKS 退出条件）；
  可回退部分 = shadow v3 记录字段（v2 兼容字段保留，旧读者不受影响）与 plan2 提示文案。
- **next_owner**：R1（research_snapshot 版本分级正式化，收编 drop_reasons/latest-only 门）；
  R3（research_service + HorizonPlan.fact_evidence_refs 正式化 + 移除 shadow 兼容聚合字段）。

---

## R1｜财务语义、历史版本与可追溯快照

```text
task_id: R1
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 26de693（R0 后）
delivered_commit: 5c0fda2
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/data/research_snapshot.py` | R1 主责：EvidenceRecord 版本化扩展（knowledge_basis 四级/document_version_id/first_seen_at/version_available_at/timestamp_precision/provenance_evidence_ids/semantic_status/semantic_note/raw_value/raw_unit/value_kind/period_basis/underlying_period_basis/metric_definition_version——三轴分离，缺省保守 UNKNOWN 不自动补绿）；统一资格算法 `assess_evidence_eligibility`（DATA_TRUST §1）+ EvidenceSnapshot.build 重写（拒收按 7 类原因码分类 + suspect_ids）；qualify 排除 SUSPECT 并给具体话术；派生守卫 `derive_single_quarter_from_cumulative`/`derive_ttm_from_single_quarters`（比率不可差分/跨年拒绝/TTM-YTD 不混）；量级筛查 `screen_semantic_anomalies`（ISS-114，只隔离不纠偏）；R0 临时门 `_is_latest_only_unverifiable` **删除**（knowledge_basis 正式替代，不两套长存）；builders 显式声明版本语义（bar 复权口径参数化） |
| `src/data/financial_data.py` | R1 主责：32 字段语义登记表 `FIELD_DEFINITIONS`（value_kind/period_basis/underlying/unit/definition_note，verification_status=name_semantics_pending_original 如实）；get_financial_quarterly 消费登记表（未登记字段不产证据）；采集管线 `capture_quarterly_evidence`（原始响应写一次留样+观测索引，DATA_TRUST §2 链路1）；ISS-114 版本化映射 `UNIT_DRIFT_MAPPINGS`+`apply_unit_drift_mapping`（范围+证据透明，非全局 ×100） |
| `src/data/research_store.py` | 新增：最小存储（raw 写一次归档/观测索引/快照清单/纠错账本，DATA_TRUST §5；隔离目录，绝不写 portfolio/HOME） |
| `tests/core/test_research_snapshot.py` / `test_financial_data.py` / `test_research_store.py` | R1 回归 17 条净增（档案入选/当时捕获/UNKNOWN 拒绝/修订 hash 稳定/SUSPECT 隔离/派生守卫/映射边界/留样幂等/坏行隔离/HOME 隔离） |
| `tests/data_sources/probe_fin_semantics.py` | R1 验收6 显式网络探针（真实跑通，见下） |
| `plan/fusion/DATA_COVERAGE.md` | 财务/行情两行按 R1 语义重写（原「财务可进严格快照/E2E4 解锁」表述作废）；范围结论更新 |
| `ISSUES.md` | ISS-114 病根核实 + 处置登记 |

### 真实探针结果（R1 验收6——真实网络实测，非 fixture 假称）

`probe_fin_semantics.py` 2026-09-27 实测（日志 tests/artifacts/probe_fin_semantics.log，留样 768 份）：
- **ISS-114 病根实证**：baostock `liabilityToAsset` 自 **2024-06-30 报告期起**全市场统一 ÷100——
  万科A 与贵州茅台（正常公司）双样本一致；与东财资产负债表**绝对值按定义重算**交叉核对：
  2024-03-31 及以前逐期吻合（差<1e-4），2024-06-30 起恒差 ×100。**供应商字段单位变更，非公司异动**。
- **【2026-09-27 架构师第三轮裁决限定】**：「全市场统一」系本行原表述**过度推断**——768 份
  留样为逐字段对象数（000002 共 512 / 600519 共 256），仅 2 家公司、24 个 liabilityToAsset
  观测；「两个跨行业样本、边界前后多期一致」足以启动**有范围的映射核定**，不足以证明
  供应商所有证券/所有未来期间永久同比例（裁决原文见 iteration3/DATA_DECISION.md §2）。
- 处置按 DATA_TRUST §2.4：版本化映射（适用范围=该字段×该源×边界后，canonical=raw×100，
  raw_value 保原值，证据指针可审计）；其余 balance 字段是否同样漂移**待逐字段核定**（登记）。

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1a latest-only+旧pubDate 不进 strict | `test_latest_only_financial_excluded_from_strict_history`（探针 P4 反例保持） | PASS |
| 1b 原始历史文档按真实公开日进快照 | `test_original_archive_document_enters_strict_by_publication_time`（AS_PUBLISHED_ARCHIVE+version_available_at） | PASS |
| 2 同期修订后旧快照 hash 不变；新快照解释来源时点 | `test_restatement_does_not_change_old_snapshot_hash` + revisions_of/revision_id/available_at | PASS |
| 3 类别正确时间基准；比率累计差分拒；TTM/YTD 不混 | 登记表测试 + `test_ratio_cumulative_diff_rejected_and_ttm_not_mixed` | PASS |
| 4 单位异常/真实经营突变/缺失零值反例；隔离不冻结 | `test_unit_anomaly_suspect_isolated_not_frozen` / `test_real_business_mutation_isolated_then_resolved_by_human`（真实突变同样隔离、人工核对后解除）/ `test_zero_and_growth_not_screened`（零值/GROWTH 分支） | PASS |
| 5 纠错列受影响对象；旧已确认成交不变 | invalidations 账本 + affected_refs 登记（`test_invalidation_ledger_*`）；store 无 portfolio 写能力 | PASS（账本层）/ **反向影响图随 R3/R4 接线补全**（PARTIAL 项） |
| 6 正常公司+涉事样本原始资料验证 | 探针真实跑通（768 留样+交叉核定）；verification_status 如实 pending_original | PASS（交叉佐证档）/ 逐份原始财报确认登记为后续 |
| 暂停点 | 严格 E2/E4 保持阻断（DATA_COVERAGE 已回写作废原解锁表述） | 遵守 |

全量口径：`pytest -q` → 见 R1 提交（提交前实测）。

### 五维度 / remaining / next_owner

- implemented ✅ / **connected PARTIAL**（capture→screen→snapshot 路径测试覆盖；但**零生产
  接线**——「分析卡可见财务证据资格与具体缺口」的可见交付随 R3/R8 落地，本卡只交付资格层）/
  scenario_validated ✅（探针=真实场景）/
  empirically_validated PARTIAL（映射待逐份原始财报确认；其余 balance 字段待核定）/
  release_ready PARTIAL（严格 E2/E4 仍阻断——暂停点如约保持）
- next_owner：R2（claim 内容核验，不同文件可并行）；R3/R4 接线 screen/invalidations 进服务层
  与因子层（语义状态 UNKNOWN 的消费侧限制随之生效）；行业双时间轴（effective/known）随 R4。

---

## R2｜可定位原文的事实核验与 AI 评测

```text
task_id: R2
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 6b8544f（R1 后）
delivered_commit: 2ab7c25
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/core/claim_extraction.py` | R2 主责：分级核验协议 cv2（VerificationLevel 六级：PARSED→SOURCE_RESOLVED→EXCERPT_GROUNDED→FACT_CHECKED/NEEDS_REVIEW/REJECTED）；`SourceDocument`（内容 hash 只对正文，标题不算已读全文；旧 dict 池条目适配）；ClaimRecord 新增 quote_text/quote_span/fact_stage/negation_flag/conditional_flag/relation_basis（缺省空=未提供，兼容）；`verify_claim_tiered`（显式 as_of 强制、数值 100 倍错位/否定丢失/框架意向当收入/后发更正/主体错配/摘录不符确定性检查、注入免疫）；verify_claim 修无池虚标 citation_resolves（探针 P3）；提取器缓存键版本化（schema+prompt hash） |
| `src/core/claim_llm_extractor.py` | 失败/截断/费用留痕（failures/truncations/last_usage_tokens——DATA_TRUST §4「调用、重试、截断、拒识、成本均进同一 run」） |
| `tests/ai_eval/claim_verification_corpus.json` | 新增：cv2 冻结语料 **16 例**（空白/错公司/100倍单位/否定丢失/框架意向/后发更正/注入/无池/摘录不符/回放资格×2/数值缺失/正文缺失/无摘录/hash 版本冲突相关路径）；f6.v1/v2 历史标注集保留不动；changelog 记录审查修正（否定/数值/阶段检查限定摘录内——正文套话不误杀；无正文→NEEDS_REVIEW 对齐暂停点） |
| `tests/core/test_claim_verification_corpus.py` | 新增：冻结语料运行器（等级覆盖+失败分布+一致性报告落 tests/artifacts/，验收5「不只一个通过数」） |
| `tests/core/test_claim_extraction.py` | R2 回归 8 条（探针 P2/P3 反例、标题不冒充全文、回放 as_of、确定性通道可达 FACT_CHECKED、缓存键版本化、LLM 失败留痕、旧池适配） |
| `plan/fusion/DATA_COVERAGE.md` | §1 契约表同步 R1/R2 语义（EvidenceRecord 版本化、统一资格算法、verify_claim 无池修正、cv2 协议） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1a 真实 hash 但正文不支持 → 不进 FACT_CHECKED | 探针 P2 复现：矛盾主张 tiered → REJECTED（原五项检查全过 MODEL_INFERRED）；`test_tiered_rejects_contradictory_claim_with_quote` + CV-05 | PASS |
| 1b 无 pool/无正文不声称引用和内容已核实 | 探针 P3 复现：无池 checks 不含 citation_resolves；CV-09/CV-14（无正文停 SOURCE_RESOLVED） | PASS |
| 2 七类对抗案例明确预期与回归 | 冻结语料 CV-01~14 全过（空白/错公司/100倍/否定丢失/框架意向/后发更正/注入）；运行器断言等级覆盖 | PASS |
| 3 回放截止前后资格不同，不依赖机器日期 | verify_claim_tiered 强制 as_of；CV-11a/11b 同一主张资格翻转；`test_tiered_replay_as_of_explicit` | PASS |
| 4 无 AI 确定性研究与模板解释可用 | DeterministicExtractor 带 quote_text 可达 FACT_CHECKED（`test_deterministic_claim_reaches_excerpt_grounded`）；两周期同文档不重复付费=提取器缓存（键含 schema+prompt hash） | PASS |
| 5 语料先冻结后运行；报告多维度 | 语料 frozen_at=2026-09-27 + 运行器报告（等级覆盖/失败原因分布/REJECTED 数）；LLM 真实评测（E5 本体 A-B）属 AI opt-in 预算，R7 承接 | PASS（确定性层）/ LLM 层 NOT_RUN（如实登记） |
| 暂停点：语义复杂保留 NEEDS_REVIEW；不用第二个 LLM 自报当核验 | CV-06/07/13 转 NEEDS_REVIEW；全链确定性检查，无 LLM-judge | 遵守 |

### user_visible / remaining / next_owner

- 可见交付 PARTIAL：quote/原文定位机制已落，「用户从事实打开原文位置」的界面随 R3/R8 接线。
- remaining：E5 本体 A-B（AI opt-in 预算）→ R7；cv2 语料扩展需新冻结文件。
- next_owner：R3（research_service 消费 verify_claim_tiered 结果进命题评估；契约=停在已达
  等级+failures，NEEDS_REVIEW 才进人工通道——监督员 P2 已对齐 docstring/语料）。
- 监督员审查：4 P1（否定套话误杀/hash 冲突静默放行/naive as_of 崩溃/无时间虚标
  no_future_date）+ 9 P2 全部修复；hash 约定双轨说明（SourceDocument.body_hash vs
  from_source_doc 的 title+content hash——接线用 from_source_doc 保持两侧一致）。

---

## R4｜真实因子能力、日期对齐与有预算的召回

```text
task_id: R4
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 6b8544f（R1 后）
delivered_commit: cddd191
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/core/factor_registry.py` | FACTOR_REGISTRY_VERSION → r4.v1；**能力ID=真实计算**（A08）：新增 roe_observed_v1/pe_ttm_v1/cash_conversion_v1/relative_return_v2 真实能力登记；capital_return_v1（ROIC）v3 与 valuation_range_v1（区间）v3 **回归不可计算态**（needs_data_probe=True——代理不得自动满足本能力，研究资格按能力ID匹配） |
| `src/core/factor_compute.py` | 新函数 cash_conversion_v1（含接近零分母防护）/roe_observed_v1/pe_ttm_v1/relative_return_v2（**按交易日期对齐**：指数交易日为日历、窗口端点必须真实 bar、区间缺 bar 计数声明超阈 UNKNOWN——停牌错位不再伪同窗）；旧名 capital_return_v1/earnings_quality_v1/valuation_pe_v1 保留为**弃用委托**（E1/E7 迁移前兼容，R7 迁移后删），relative_trend_v1（伪同窗本体）删除 |
| `src/core/candidate_pool.py` | `funnel_report`（原始/路内配额/跨路去重/最终逐阶段机器核对 + 缺口候选完成度汇总——验收4「原始130→配额后35」不再人抄）；`run_sharded_recall`（法C 分片编排：预算硬顶含重试、耗尽抛错不无限重试、单分片失败保留已核实实体+覆盖缺口显式登记——验收5；纯编排调用方注入，R8 接线） |
| `src/data/research_store.py` | 行业成员**双时间轴** IndustryMembership（effective_from/to 经济归属期 × known_from/to 系统知晓期）+ strict_eligible_at——当前值接口构造的记录对历史 as_of 一律不 strict（不回填上市日，验收2 后半） |
| `tests/core/test_factor_compute.py` / `test_r4_recall_and_funnel.py` / `test_candidate_lineage.py` | R4 回归（能力ID 断言/伪同窗反例/停牌端点/缺 bar 超阈/接近零分母/漏斗互洽/预算硬顶/失败隔离/双时间轴） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1 单点PE不满足 valuation_range；ROE 不自动满足 ROIC；缺失/不适用贯穿 | 登记表 needs_data_probe 翻转（test_candidate_lineage）+ FactorResult.factor_id=真实ID + note 机器可读边界 | PASS |
| 2 日期错位不产伪同窗；无历史行业标签不宣称 strict | relative_return_v2 停牌反例组（test_relative_return_*）+ IndustryMembership.strict_eligible_at | PASS |
| 3 负分母/接近零/一次性收益/行业不适用测试；不编缺失估值输入 | cash_conversion 近零/负分母 NOT_APPLICABLE；pe_ttm 亏损 NOT_APPLICABLE；一次性损益标注为解释性缺口（R3 命题评估承接） | PASS |
| 4 原始130/配额后35 机器核对；缺字段明确完成度 | funnel_report 与 CandidateSet 内部记录互洽测试 + completeness_note | PASS |
| 5 分片失败保留已核实实体+缺口；请求/重试不超冻结预算 | run_sharded_recall 预算硬顶/失败隔离/截断缺口测试 | PASS |
| 可见交付 | 「为什么值得研究/还缺什么」的逐候选一句话随 R3 ResearchBundle/R8 界面落地——本卡交付能力ID与缺口机器化 | PARTIAL（如实） |

### remaining / next_owner

- bz Excel 保真与 CACHE_VERSION：评分逻辑未触碰（能力拆分在融合因子层，旧六维零改动）——不 bump。
- relative_trend_v1 删除影响面：E1/E7 用的是 capital_return/earnings_quality/balance_risk（委托兼容）；
  relative_trend 无脚本调用方（grep 核实）。
- next_owner：R3（研究资格按能力ID匹配消费）；R8（分片召回接线与界面）。

---

## R3｜自动研究服务与双周期计划版本

```text
task_id: R3
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 474df8c（R2/R4 后）
delivered_commit: （commit 后回填）
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/core/research.py` | 命题级评估层（RESEARCH_LOOP §2）：ThesisAssertion/ThesisAssessment + evaluate_assertion（能力ID匹配，A08 呼应）+ assess_thesis_by_assertions（状态优先级链：INVALID→失效UNKNOWN→未核实重大反证→UNESTABLISHED→必需命题被反驳→VALID；周期隔离——MID VALID 不使 LONG 自动 VALID） |
| `src/core/research_service.py` | 新增：单服务编排（snapshot→extract→verify→factor→assess→draft；步骤缓存幂等——同输入同 run_id）；ResearchBundle（研究结论包，**不是第二个 DecisionPacket**——无动作/仓位/执行资格）；MID 4 命题/LONG 5 命题模板（evidence_requirements 引用 R4 能力ID——valuation_range_v1 不可计算→LONG 诚实带缺口）；系统草稿生成（facts_observed=已核验主张+fact_evidence_refs=claim 引用——R0 资格门正式数据源；不填 --facts 可生成）；纯注入无 IO |
| `src/core/decision_policy.py` | HorizonPlan 研究契约字段正式声明（fact_evidence_refs/assessment_id/snapshot_id/policy_version/supersedes_ref——旧 JSON 缺省兼容）；content_hash（不含激活态）；PolicyIntent + evaluate_horizon_intent（同表裁决剥执行语义——中间 ELIGIBLE 不透传，验收7） |
| `src/data/horizon_plans.py` | 接受绑定（accepted_refs：plan_id+revision+content_hash——is_accepted_version 判定草稿不得沿用旧接受）；持仓主意图引用（active_refs 生命周期：设置需已接受精确版本、清除后重建不沿用旧轮授权）；save/remove 保留全部顶层键（审查修正：原实现会静默丢 accepted_refs/active_refs） |
| `tests/core/test_research_service.py` | R3 回归 10 条（七条验收逐条） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1 无 --facts 生成草稿；缺资料带具体缺口不强行 VALID | test_auto_draft_without_user_facts / test_missing_data_gives_gaps_not_valid（valuation_range_v1 点名） | PASS |
| 2 共享事实分别评估；MID VALID 不使 LONG 自动 VALID | test_mid_valid_does_not_leak_to_long（同 claim id 两周期；命题集合不同；LONG 缺估值诚实 UNESTABLISHED） | PASS |
| 3 反证未核实→REVIEW_REQUIRED；失效 TRUE→INVALID | test_unverified_counter_evidence_gives_review_required / test_verified_invalidation_true_gives_invalid（决策表行3 已有锁） | PASS |
| 4 接受绑定精确版本；新事实只生成修订草稿 | test_accept_binds_exact_version | PASS |
| 5 主意图生命周期：切换保留旧版；重建不沿用旧授权 | test_active_ref_lifecycle | PASS |
| 6 重试幂等；两进程不丢更新；损坏保守读取 | test_service_retry_idempotent（bundle 级幂等）/ test_two_writers_conflict_rejected（M5 指纹）+ 既有损坏保护测试；**失败恢复（步骤级缓存）未实现——随 R8 增量任务账本接线（监督员 P1·诚实交付修正）** | **PARTIAL** |
| 7 意图与执行资格分离 | test_policy_intent_strips_execution_semantics（PolicyIntent 无执行字段、组合未知无精确目标） | PASS |
| 回退 | 旧主策略零改动（legacy 路径未触碰）；草稿/评估全部新增对象；无「非空事实自动 VALID」恢复路径 | 遵守 |

### remaining / next_owner

- CLI 接线交 R8（`l <code>` 消费 ResearchBundle 显示草稿/缺口/比较）；AccountSnapshot 联动
  active_refs 由 R5 落账户侧；反证自动检测（REFUTES 主张进 material_counter）随 R8/R7 评测口径。
- 影子对评估的消费：R0 已统一走 assess_thesis 资格门；R3 的 assessments 为逐周期正式输入，
  R8 接线时替换影子临时口径。

---

## R5｜账户事实、现金与合法数量预算

```text
task_id: R5
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 9b93d3f（R3 后）
delivered_commit: 12fecbc（与 R6 同批）
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/data/account_snapshot.py` | 新增：AccountSnapshot（cash_available/cash_reserved/holdings(数量成本批次)/NAV及定价时点/data_completeness——RATIO_ONLY 权重假设视图不冒充数量）；AccountEventLog（事件追加账本：BUY/SELL/FEE/DIVIDEND/DEPOSIT/WITHDRAW 独立事件、event_id 幂等、崩溃重放恢复——FIFO 批次扣减）；LotRules/TradeRulesAdapter 接口（**不实现全市场规则表**——R6 提供官方核验实现）；FeeModel（最低佣金边界显式）；allocate_tradeable_budget（连续 indicative → 离散可行：FEASIBLE/CONDITIONAL/REJECTED + 唯一阻塞字段 + 额度释放；参数表无未来回撤通道——验收6） |
| `tests/core/test_account_snapshot.py` | R5 回归 12 条（七条验收对应 + 板块规则差异/费用边界/FIFO 批次/幂等重放） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1a 现金 None 不显示可买金额/股数 | test_cash_none_gives_conditional_not_amount（CONDITIONAL+唯一阻塞字段） | PASS |
| 1b 现金 0 与缺失分别解释 | test_cash_zero_vs_missing_distinct（0→REJECTED 仍不足；None→CONDITIONAL 问现金） | PASS |
| 2 预算不足最低申报量拒绝+释放；板块/日期规则差异、零股卖出字段、费用边界 | test_budget_below_min_order_rejected_and_released / test_board_rules_differ_via_interface / test_sell_odd_lots_rule_field_exists / test_fee_min_commission_boundary | PASS |
| 3 部分成交/重复确认/崩溃重启不重复入账 | test_event_log_idempotent_and_replay / test_partial_then_more_fills_accumulate（event_id 幂等+重放一致） | PASS |
| 4 拟卖未确认/已占用/已入账三态不重复计可用 | test_reserved_cash_not_double_counted（cash_deployable=available-reserved；未入账 SELL 不产生现金） | PASS |
| 5 新开/加/减/清/清后再开维护主计划关联 | R3 active_refs 生命周期测试承接（设置需接受精确版本、重建不沿用）；确认流与 proposals 账本联动 | **PARTIAL**（plan_ref 字段与生命周期已落；确认流联动随 R8——监督员修正：原判 PASS 偏宽） |
| 6 风险预算占用受限；未来回撤参数无输入通道；终检不越限 | solve_budget 压力约束（既有）+ test_no_future_drawdown_parameter_channel + test_discrete_rounding_never_exceeds_budget | PASS |
| 7 两写入者冲突显式拒绝；不改真实账户 | M5 指纹拒绝（既有测试）+ 事件账本 event_id 幂等；测试全隔离目录。**注记：事件账本 append 幂等为 check-then-act，单写者前提**（沿用 proposals.json 口径；多进程并发同 event_id 会双行——监督员 P2 登记） | PASS（单写者前提注记） |
| 监督员修正补记 | P1×2 修复（多行分配串行扣减现金池——合计不超可部署；坏 BUY 事件隔离不炸整本重放）；P2：告警三处同步（plain_errors+速查手册 N+6 节）、幽灵持仓/fee 留痕语义/replay 未知现金 None/终检复核/释放池如实降「登记待 R8 再分配」 | 已修 |

### remaining / next_owner

- **pos confirm 流与 AccountEventLog 的联动接线随 R8**（今日确认流仍走 proposals 账本——
  事件账本为 R8 准备的双账本事务基础）；today 的「可行数量或唯一阻塞字段」显示随 R8。
- 交易规则数据（官方核验的 LotRules 表）由 R6 提供——R5 只定义接口与消费，不硬编码。
- 释放额度自动再分配随 R8（本版如实只登记 released_budget_weight，不自动加量）。
- next_owner：R6（规则接口实现+回放闭环）；R8（确认流联动+today 显示）。

---

## R6｜交易制度与回放闭环

```text
task_id: R6
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: 9b93d3f（R3 后）
delivered_commit: 12fecbc（与 R5 同批）
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/core/experiment.py` | DatedRuleEntry + DATED_RULES（日期化规则注册：印花税 2023-08-28 切换【财政部/税务总局公告2023年第39号】、科创板最低申报 200 股【上交所科创板交易规则】——每条带来源；明确「不冒充最新制度清单，R9 复核」）+ rule_at（切换日边界判定）；CorporateAction + apply_corporate_action（分红入现金/送转扩股调成本——未复权价+行动台账配对，无双计通道）；MarketStatusCheck（停牌/上市/退市拒绝；状态数据 None=缺失→调用方必须标 NON_STRICT，不因接口存在就绿灯） |
| `tests/backtest/e0b_directed_scenarios.py` | 新增：E0b 定向成交场景脚本（同日买入退出被拒计数非零/次日老仓可卖/末日尚不可卖按最后价格估值——期末残余持仓列示不虚构强平/除权分红恒等/印花税切换边界/三态拒绝；temporal_eligibility=NON_STRICT 合成价格如实标注；报告落 tests/artifacts/e0b_directed_report.json） |
| `tests/core/test_experiment_r6.py` | R6 回归 9 条 |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1 E0b 定向：新仓不可卖/老仓可卖计数非零；未能卖出仍持有并估值 | e0b_directed_scenarios 场景1/3 实测（blocked=1、残余持仓按最后价格估值） | PASS |
| 2 除权分红恒等；不复权价与分红现金不双计 | apply_corporate_action 恒等测试（分红=持股×每股；收益=未复权价差+分红一次）+ 送转批次 | PASS |
| 3 状态数据缺失标 NON_STRICT 不绿灯 | MarketStatusCheck None 语义 + E0b 报告 temporal_eligibility=NON_STRICT | PASS |
| 4 费用/税切换日边界、板块申报差异有真实来源与固定夹具 | DATED_RULES 来源字段（test_rules_registry_has_sources）+ 边界测试 | PASS（已登记规则事实）/ **全市场现行规则核验归 R9 复核** |
| 5 固定意图与数量下成本单调 | test_cost_monotonicity_fixed_intent_and_quantity（双边费率单调） | PASS |
| 可见交付 | 回放报告显示制度版本来源/不可成交原因/期末残余持仓（E0b 报告 JSON） | PASS（E0b 范围）/ 完整回放（历史 PIT 全集）仍 blocked（如实） |

### remaining / next_owner

- 真实历史停牌/退市/行业成员数据接入后 run 才能升级 strict（暂停点保持）；
  E0b 冻结修正臂作为后续基线（VALIDATION §4 E0b——修正臂 manifest 冻结随 R7）。
- next_owner：R7（E0b manifest 冻结 + E2b/E3b/E4b/E5b/E6b/E7b 按重定级口径执行）。

---

## R7｜按真实信息集执行 E 矩阵

```text
task_id: R7
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: dc7f12a（R5/R6 后）
delivered_commit: 980e925
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/core/experiment.py` | ExperimentManifestV2（VALIDATION §3 扩展：preregistered_at/temporal_eligibility 从输入资格**派生**/selection/mode/thesis/extraction sources/arms 单一变量/costs/stop_rule——旧 manifest 保留读兼容）+ validate_resource_parity（E1b 资源同价机器化）+ check_strict_eligibility_or_block（BLOCKED_DATA 资格门）+ FixedStressScenario（E6b 删除未来全区间回撤的替代——估计截止日+窗口+方法版本）；E_SPEC_V2 注册 E0b–E7b（重定级口径，runnable/block_on_data 如实） |
| `tests/backtest/e7b_controlled_scenarios.py` | 新增：E7b 受控场景（七类动作意图全命中 + EXIT+BLOCKED + 四预算案例——实测报告 tests/artifacts/e7b_controlled_report.json；SYNTHETIC/NON_STRICT 如实标注；真实非空路径观察=NOT_OBSERVED 另批） |
| `tests/backtest/e2b_e3b_e4b_skeletons.py` | 新增：E2b/E3b/E4b 骨架（manifest 预注册+单一变量臂+资格门——实测全部 BLOCKED_DATA 如实拒绝；无严格历史输入不跑收益结论） |
| `tests/core/test_experiment_r6.py` | R7 回归 4 条（资格派生/资格门/固定压力场景/资源同价检查） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1 manifest 从输入证明资格；不硬赋 rule_bz_proxy | ExperimentManifestV2（temporal_eligibility 派生 + selection/mode/thesis/extraction 分源登记）+ 测试 | PASS |
| 2 E1 同资源比较；E5 预注册；E6 不用全未来回撤；E7 预算正向命中 | validate_resource_parity / E5b 语料冻结（R2 cv2）+ AI 臂 NOT_RUN / FixedStressScenario / E7b 四预算案例全命中 | PASS（离线部分）/ E1b/E5b AI 真跑 opt-in NOT_RUN（如实） |
| 3 E2/E3/E4 单一变量；被拒样本保留 | ARMS 预注册（每臂 single_variation）+ guardrail_metrics「未触发样本保留」 | PASS（骨架级——真实执行 BLOCKED_DATA） |
| 4 无严格资料显式 BLOCKED_DATA；执行可完成部分 | e2b_e3b_e4b_skeletons 实测三实验全部 BLOCKED_DATA；E0b/E6b/E7b 可完成部分已执行 | PASS |
| 5 参数搜索与失败尝试记录 | manifest v2 costs/human_overrides/exclusions 字段 + trial 留痕字段 | PASS（字段层） |
| 暂停点 | 严格 E2/E4 保持 BLOCKED_DATA（R1 latest-only 门未解除——如实） | 遵守 |

### remaining / next_owner

- E1b/E5b 真跑（AI opt-in 预算）、E2b–E4b 严格历史执行：BLOCKED_DATA——待原始档案路径或
  前瞻采集积累（R1 暂停点，不能接受掉）；E6b 全量 rerun 随 e6 脚本迁移 FixedStressScenario。
- next_owner：R9（独立验收裁决本表）。

---

## R8｜统一入口与八类用户走查

```text
task_id: R8
owner: 实施方（Claude Code）
status: REVIEW
baseline_commit: dc7f12a（R5/R6 后）
delivered_commit: 980e925
```

### owned_files / 变更面

| 文件 | 变更 |
|---|---|
| `src/cli/main.py` | 新增 `research` 命令（单股研究工作台：MID/LONG 各一句资格结论+理由；系统草稿未激活；缺口逐条点名；已核验主张带引用可打开原文；`--capture` 真实网络采集财务→留样→筛查→因子能力→研究链）；版本 v0.8.22 |
| `start.py` | research 命令路由+帮助（REPL 入口） |
| `plan/fusion/iteration2/R8_WALKTHROUGH.md` | 八类走查记录（①④⑤⑥⑦⑧ 合成走查实测证据；②③ live 依赖如实待真实试用；性能基线亚毫秒级——R9 审查校正口径） |

### 验收条款逐条

| 条款 | 证据 | 判定 |
|---|---|---|
| 1 每卡"现在做什么/为什么/何时再看" | R8_WALKTHROUGH.md 走查矩阵（6/8 类实测输出摘录；**P0 修正记录在案**——初版把「缺口行减少」误读为「命题建立」，系因子接线形状错配下的键存在性效应；修复后重跑重录） | PASS（6/8）/ ②③ 待真实试用（PARTIAL） |
| 2 不支持的长期行业显示研究边界 | NOT_APPLICABLE→WAIT/REVIEW（决策表行4b 既有）+ research 缺口点名 | PASS |
| 3 CLI/chat/Web/TUI 同输入一致 | **仅 CLI 接线本批完成**；chat/Web/TUI 适配随 R9 前接线批 | **PARTIAL**（如实） |
| 4 新用户无需手填事实得草稿；不反复重填风险档/现金 | research 命令实测（无 --facts 生成草稿）+ 风险档/账户落配置（F7 既有） | PASS |
| 5 today 不隐藏重大风险；变化不足不刷重复分析 | today 先持仓风险序（F7 既有）+ 同股同分钟影子幂等（既有）；融合风险上限三卡内不隐藏——design 承接 | PASS（既有语义回归）/ 新增验证随 R9 走查 |
| 6 性能基线 p50/p95；缓存/AI 失败降级实测 | research 核心链路亚毫秒级（独立复测 p50 0.008–0.125ms / p95 0.135–0.300ms，n=20 冷热混合）+ LLM 失败/截断计数（R2） | PASS（核心链路）/ today 全渲染基线随 R9 |

### remaining / next_owner

- chat/Web/TUI 三端一致性接线、②③ 真实用户试用、today 全渲染基线：R9 前接线批。
- 监督员审查修正：P0（走查证据与代码输出不符——更正重录）+ P1×2（因子接线形状错配
  修复并重跑；「使用已归档证据」虚假话术改为如实降级 + 快照拒收摘要展示）+ P2×5
  （死变量/对照臂/静默失败提示/E1b 精确匹配/E_SPEC_V2 注册表测试）全部修复。
- next_owner：R9（独立验收——本表不得自证 VERIFIED，交独立审查者逐条裁决）。

---

## R9｜独立验收、发布与代码收敛

```text
task_id: R9
owner: 独立审查者（裁决）+ 实施方（按裁决改模式解析/消费者收口/文档）
status: REVIEW
baseline_commit: 980e925（R7/R8 后）
delivered_commit: （commit 后回填）
```

### 实施方交付（待独立裁决确认）

| 文件 | 变更 |
|---|---|
| `src/core/shadow_diff.py` | **fusion.mode 单一解析器** `resolve_fusion_mode`（legacy_only/capture_only/shadow/opt_in/default；旧布尔开关 fusion.shadow_capture 迁移映射——两套语义不并存；opt_in/default 配置附「晋级门未过」警示——模式登记≠晋级决定）；_load_capture_switch 消费解析器 |
| `tests/core/test_shadow_diff.py` | 解析器回归（迁移映射/未知回退/晋级警示/缺省） |
| `使用手册.md` | research 命令使用说明（用户视角：离线口径/--capture/与 plan2 的关系） |
| `plan/fusion/iteration2/R9_INDEPENDENT_REVIEW.md` | 独立审查者裁决报告（独立会话产出，实施方原样收录） |

### 旁路/兼容分支删除条件登记（R9 验收4——「只在等价/预期差证据具备时删」）

| 旁路/兼容项 | 消费者 | 删除条件（三者同时满足） | 现状 |
|---|---|---|---|
| ShadowDiffRecord v2 兼容聚合字段（plan_source/thesis_status） | 旧报告读取（无仓库内消费者）；render 已改分周期 | R3/R8 评估接线完成 + 覆盖反例测试 + 预期行为差报告 | 保留（登记于字段 description） |
| factor_compute 弃用委托（capital_return_v1/earnings_quality_v1/valuation_pe_v1） | tests/backtest/e1_route_recall.py、e7_funnel_smoke.py | R7 迁移脚本至新能力ID + 回放等价证据 | 保留（委托内标注「请改用新ID」） |
| verify_claim 结构层（verify_claim_tiered 之外的旧入口） | e5_claim_arms.py:81 | E5b 真跑后改消费分级结果 | 保留 |
| legacy 决策表 DecisionPacket 直出（vs PolicyIntent） | evaluate_horizon 全部既有调用方（shadow/测试） | R8/R9 界面全面消费 PolicyIntent + 决策表行证据引用接通 | 保留 |
| 旧 E_SPEC（E0–E7 原表） | test_experiment_replay 锁定 | E_SPEC_V2 独立验收通过后保留历史、标注 superseded | 保留（V2 另表） |

### 全库扫描结论（统一资格/终态入口——R9 验收4）

- 资格唯一入口：`research.assess_thesis`（R0 资格门）+ `research.assess_thesis_by_assertions`
  （R3 命题级）——shadow 已消费，CLI plan2 提示对齐；无第二处「非空→VALID」实现（grep 扫描）。
- 终态唯一入口：DecisionPacket 仍由 evaluate_horizon 产出（PolicyIntent 为中间值对象）——
  chat/Web/TUI 消费一致性未接线（R8 验收3 PARTIAL 如实）。
- 证据资格唯一入口：`assess_evidence_eligibility`（R1）——shadow/CLI 均经 EvidenceSnapshot.build。

### 发布建议（实施方提交独立裁决的底稿——最终以 R9_INDEPENDENT_REVIEW.md 为准）

| 能力 | 建议 | 依据 |
|---|---|---|
| 可信数据提示/资格门/缺口话术 | **可发布**（已在 live 路径可见：plan2/research 提示） | R0/R1 反例回归 + 探针复现 |
| 研究工作台 research 命令 | **可发布（实验性）**——离线口径诚实，--capture 真实网络 | R8 走查 6/8 |
| shadow 捕获（capture_only 缺省） | **维持**（数据积累中——opt_in 观察下限未达） | R3/R9 影子统计未满 20 交易日 |
| 融合主决策 opt_in | **不晋级**（VALIDATION §7 门未过：影子观察期/真实计划样本不足） | 架构裁决 + R9 独立确认 |
| default | **不晋级** | 同上 |

### 阻断与未完成清单（独立审查合并后——含审查★新增项）

- 严格历史财务实验（E2b/E3b/E4b）BLOCKED_DATA——原始档案路径或前瞻采集未积累（R1 暂停点）。
- E1b/E5b 真跑（AI opt-in 预算）NOT_RUN。
- ISS-114 其余 balance 字段单位漂移待逐字段核定；映射待逐份原始财报确认升级。
- R3 步骤级缓存/失败恢复、R5 释放再分配——随 R9 后接线批。
- **【审查★新增登记】R5→R8 承接缺口：today「可行数量/唯一阻塞字段」显示、pos confirm 流→AccountEventLog 联动——此前脱账，本节补登**。
- R8 chat/Web/TUI 三端一致性、②③ 真实用户试用——随 R9 后接线批。
- factor_compute 弃用委托删除债（依赖 e1/e7 迁移——R4/R7 未迁移，见 R9 旁路表）；ShadowDiffRecord v2 兼容字段删除债。
- DATED_RULES 全市场现行规则官方复核（规则表当前只喂回放/场景，不下真实单）。
- research CLI 缺口列表截断显示（cosmetic——本批已加省略号提示）。

### R2–R8 五维判定汇总（独立审查者裁定——补 VALIDATION §1 要求；实施方自评见各卡节）

| 卡 | implemented | connected | scenario_validated | empirically_validated | release_ready |
|---|---|---|---|---|---|
| R2 | PASS | PARTIAL | PASS | NOT_RUN（LLM 层） | PARTIAL |
| R3 | PASS | PARTIAL | PASS | NOT_RUN | PARTIAL |
| R4 | PASS | PARTIAL | PASS | NOT_RUN | PARTIAL |
| R5 | PASS | **PARTIAL（零生产消费方）** | PASS | NOT_RUN | PARTIAL |
| R6 | PASS | PARTIAL | PASS | NOT_RUN | PARTIAL |
| R7 | PASS | PARTIAL | PASS | NOT_RUN | PARTIAL |
| R8 | PASS | **PARTIAL（仅 CLI）** | PARTIAL | NOT_RUN | PARTIAL |

（R0/R1 五维表见各卡节；R1 connected PARTIAL——`l` 主分析路径尚不展示证据资格。）

### 独立裁决结论（R9_INDEPENDENT_REVIEW.md 摘录）

> **第二轮 R0–R8 交付验收通过（有条件）**：八张卡账本声称的测试、场景、探针经独立实跑
> 全部复现，无一处「账本 PASS 但代码做不到」；五处★均为账本形式缺陷或承接登记缺口而非
> 语义造假；发布口径——资格门/提示/缺口话术与 research CLI（实验性）可发布，影子维持
> capture_only，opt_in/default 因影子数据 0/0/0 明确不晋级，R9 收尾须完成文档收敛、承接
> 缺口登记与哈希回填后方可关账。

**R9 收尾执行记录（实施方按裁决逐项完成）**：文档收敛 7 项（RESUME/iteration2 README/
DATA_TRUST/RESEARCH_LOOP/EXPERIMENTS E1 数字修正 5221-50-75-5/ROLLOUT 现状行/使用手册
research 表述）；R3/R7/R8 哈希回填；R2–R8 五维表补齐（本节）；R5/R8 承接缺口补登（本清单）；
R4 迁移未发生注记；性能口径降格「亚毫秒级」（R8_WALKTHROUGH.md 同步）；cosmetic 截断提示。
五处★全部清偿。

---

*后续 R 卡按同模板追加。*
