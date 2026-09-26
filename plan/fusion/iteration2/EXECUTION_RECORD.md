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

*后续 R 卡按同模板追加。*
