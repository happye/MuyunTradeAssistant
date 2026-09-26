# 融合迭代执行记录（F 批次）

> 本文件是 plan/fusion/ F0–F9 的实施账本（TASKS.md「执行记录模板」），与上级 plan/README.md 的 M/C 旧账本**分开记**（交接文档 §四：F 编号不与 M/C 混记）。
> 每批一个记录段，状态只有 TODO / IN_PROGRESS / REVIEW / VERIFIED 四种；未完成条款保持 TODO 不自动删除。

---

## F0 — 契约、基线与参数登记

- **任务**：F0 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员（全程）+ code-quality-guard（每批）
- **基线提交**：`649c4b7`（fusion 8 份设计文档入库；其上为 b07073b/bdebf13 = v0.8.17）
- **基线全量**：811 passed / 2 skipped / 1 deselected（监督员实测 66.5s）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/decision_contract.py` | 新增：DecisionPacket/EvidenceRef/Blocker/InvalidationRule/NextCheck/LegacyTrace + 7 枚举 + §2.3 不变量构造期校验 + 四份官方样例 |
| `tests/core/test_decision_contract.py` | 新增：41 测试（四样例/非法组合×22/UNKNOWN-MISSING 语义/JSON 往返/未知字段保留/ID 规范化/不可变锁） |
| `plan/fusion/EXECUTION_RECORD.md` | 新增：本账本 + F0 基线报告（语义登记/调用矩阵/冻结 hash） |

### 已完成条款（对照 TASKS.md F0）

| 条款 | 落点 | 证据 |
|---|---|---|
| 实现 DecisionPacket/EvidenceRef/ResearchStatus 等最小类型 | `decision_contract.py`（7 枚举：ResearchStatus/ThesisStatus/DesiredAction/ExecutionStatus/Horizon/MarketPhase/FactStatus + TruthValue） | 36/36 绿 |
| 明确百分比单位、target 与 delta、时区与证券ID | 权重全部以账户净资产为分母 0-1（delta -1..1）；`as_of` 强制带时区（naive 拒收）；`security_id` 纯数字规范化 zfill(6)，exchange 单独字段（SH/SZ/BJ 按代码段推导） | `test_naive_datetime_rejected` / `test_security_id_normalized` / `test_bad_security_id_rejected` |
| 非法动作/仓位组合有明确校验 | `_check_action_weight_invariants`：EXIT 目标必须 0；REDUCE 须降且需持仓>0；ADD 须升且需持仓>0；OPEN 须空仓；HOLD 不得改比例；WAIT/REVIEW 不得带目标；缺组合信息禁精确目标（**ADD/REDUCE/OPEN/HOLD 全覆盖**，EXIT 例外目标恒 0）；delta=target-confirmed 矛盾拒收（confirmed 未知时 delta 必须 None，F2 批补）；BLOCKED 必带 blockers 且 executable=None；CONDITIONAL 必带 blockers 且 executable=None；ELIGIBLE 禁 blockers 且 executable=desired；INVALID 逻辑禁 OPEN/ADD | 26 个拒绝测试全绿 |
| UNKNOWN 与 MISSING 不被转换为 0 | Optional 字段无任何 None→0 补全路径；`FactStatus.MISSING`/`TruthValue.UNKNOWN` 枚举原样保留（无自动 FALSE）；契约无 `score`/`win_probability` 字段 | `test_none_weights_stay_none_after_roundtrip` / `test_missing_semantics_survive` / `test_contract_has_no_ambiguous_score_field` |
| 决策记录不可变（ADR-F02）+ 防绕过 | `frozen=True`（构造后赋值即报错）；`model_construct` 重写为抛 NotImplementedError（防绕过全部不变量）；security_id 拒收全角数字 | `test_packet_is_frozen_after_construction` / `test_model_construct_bypass_rejected` / `test_fullwidth_digit_security_id_rejected` |
| JSON 往返稳定 | `model_dump_json`→`model_validate_json` 全字段相等且二次序列化字节一致；`extra="allow"` 未知字段保留（ADR-F09） | `test_json_roundtrip_stable[×4]` / `test_extra_fields_preserved_roundtrip` |
| BUY/WAIT/HOLD/EXIT受阻 四份样例 | `sample_buy_packet`（OPEN+ELIGIBLE+精确目标）/ `sample_wait_packet`（INCOMPLETE+WAIT+无目标）/ `sample_hold_packet`（HOLD+无比例变化）/ `sample_exit_blocked_packet`（EXIT+BLOCKED+跌停阻塞+退出意图保留） | 4 个样例测试 + 参数化往返测试 |
| 冻结风险不变量 | DESIGN §2.3 全部机器化为构造期校验（上两行），F5 策略层只能在其上收紧不能放松 | 本表 |
| 记录每个现有分数字段的语义与来源，不改变其历史含义 | 见下「分数字段语义登记」表 | — |
| 相关调用矩阵（consumer 清单） | 见下「consumer 调用矩阵」表 | — |
| 配置 hash 冻结 | 见下「冻结 hash」 | — |
| 对 RESEARCH 可疑调用链复核最新 HEAD（持仓回写和摘要） | 见下「可疑调用链复核」 | — |
| 暂不改策略参数 | 本批零改动任何现有计算/策略代码（`git show --stat` 可证） | — |

### 未完成条款（保持 TODO）

- 冻结 legacy 回放集：本轮只登记**指针**（见下），正式冻结清单（样本/参数/manifest）随 F8 回放基建落
  （TASKS.md F0 未把回放集清单列为 F0 单独验收产物，F8「已有DataFeeder等价基准不得删除」为硬门）
- `analyze_packet` 适配出口：属 F2 范围（TASKS.md F2 文件清单），F0 只冻结契约
- `bz_normalized_score` 具名诊断字段：F0 最小集未落契约（extra="allow" 兜底透传）；
  F2 适配 orchestrator 结果时随 legacy_trace 一并落位（DESIGN §2.2"保留 bz_normalized_score 等有名称的诊断字段"）

### 对抗审查记录（code-quality-guard，2026-09-25）

- 🔴-1 缺组合信息守卫只覆盖 ADD/REDUCE（HOLD/OPEN 带精确目标静默放行）→ **已修**（守卫扩为四动作 + 2 测试）
- 🔴-2 CONDITIONAL + executable_action 不校验（与字段描述矛盾）→ **已修**（+1 测试）
- 🔴-3 契约可变违反 ADR-F02 不可变要求 → **已修**（frozen=True + 派生重构为 mode="before" + model_construct 禁用 + 2 测试）
- ⚠️-1 model_construct 旁路 → **已修**（结构化锁死）；⚠️-2 全角数字 security_id → **已修**（isascii+isdigit + 1 测试）
- ⚠️-3 账本两处坐标不准（#3 confidence / #10 score_one）→ **已更正**；⚠️-4 LegacyTrace 称谓漂移 → **已统一**；⚠️-5 bz_normalized_score 未登记 → **已补进未完成条款**

### 分数字段语义登记（F0 冻结：只登记，不改任何历史含义）

| # | 字段 | 语义 | 来源/坐标 | 禁止的误用 |
|---|---|---|---|---|
| 1 | `DecisionResult.score` | **选定动作桶的强度** 0-1（SELL 0.9=强卖出意愿，不是买入价值/上涨概率）；AI/事件层 adjustment 直接加在该值上 | `decision_engine.make_decision`；AI 合并 `orchestrator.py:233/270/294`（三分支，按实际路径执行） | 当综合买入价值用；跨动作比较排序（探针 C/D 实证） |
| 2 | `AIModifierResult.score_adjustment` | 情绪调节量；bearish 统一产生负值，**无方向输入**（作用于 SELL 会削减卖出强度） | `ai_modifier.py:351 _apply_modification` | 视为方向性观点；F2 需 direction/strength 拆分 |
| 3 | `AIModifierResult.confidence` | 模型自报置信度 0-1，未校准 | 赋值 `ai_modifier.py:445-446`（字段定义 models.py:426） | 进仓位计算；对外展示「上涨概率」 |
| 4 | `DecisionResult.position_ratio` | 聚合层建议仓位 0-1（AI cap 应用处） | `decision_engine` + `orchestrator.py:242` | 当已确认仓位；跨股加总直接当组合 |
| 5 | `StrategyDecision.position_ratio` | 策略层建议目标仓位 0-1（HOLD_POSITION 路径保持原仓） | `strategy_layer.process` | 同上；F1 前被 `update_from_strategy_decision` 直接当事实仓位写盘（待拆） |
| 6 | `StrategyDecision.position_action` | 仓位动作枚举 OPEN/ADD/REDUCE/CLOSE_ALL/HOLD_POSITION/STAY_OUT；execution_layer **只读此字段** | `strategy_layer` / `plan_guard` / `orchestrator` 安全网 | 只设 decision 不设 position_action（安全网静默失效，AGENTS §五） |
| 7 | `StrategyDecision.sell_path` | 卖出路径标签（weak_sell/trend_exit/stop_loss_*/take_profit_trim/top_signal/fundamental_alert/flat_sell） | `strategy_layer._infer_sell_path` 等 | 当成交记录 |
| 8 | 排名技术分 | `score×100` 动作强度映射（SELL .9→90、BUY .6→60），**非买入吸引力** | `ranking_layer.py:180 _calc_technical_score` | 当买入排序依据（探针 D） |
| 9 | `DimensionScore.score` | 排名四维 technical/sentiment/liquidity/volatility 0-100 | `ranking_layer` | 当独立证据（MA/RSI 同源价格序列） |
| 10 | benzong `total_score` | 保真公式分（乘流动性系数 L），`grade()` 依据；试金石锁死 | `benzong/scorer.py:219 score_one`（:114 为 total_score 字段定义） | 改公式；用 `grade()` 做展示/决策 |
| 11 | benzong `normalized_score()` | ÷(正权和×L) 归一化 0-100 展示分（B16 后满分恒 100） | `scorer.py` | 与 total_score 混用口径 |
| 12 | `effective_grade()` | 展示/决策等级（风险否决 F/景气≤0 F/≤30 C/≤50 B） | `scorer.py` | 用 `grade()` 替代（AGENTS §五） |
| 13 | `TradeRecord.signal_score` | 回测成交时点的决策 score 快照 | `backtest_engine` | 事前信号统计当事后收益证据 |
| 14 | `evidence.jsonl .score` | C1 证据快照 = DecisionResult.score（中间态）；与 position_action（末端）不同阶段 | `cli/evidence.py:26 record_evidence` | diff 输出当终态比较（F2 统一契约时同步消费方） |

### consumer 调用矩阵（F0 复核 HEAD `bdebf13`，F2 改造范围依据）

| 生产者字段 | 消费方 | F2 触点 |
|---|---|---|
| `DecisionResult.score/decision/position_ratio` | `cli/main.py:1571 _print_plain_summary`（摘要，**读中间态**）、`ranking_layer`（技术分）、`cli/evidence.py record_evidence`（JSONL）、`backtest_engine`（signal_score）、四端展示 | 摘要改读终态；evidence 补终态字段 |
| `StrategyDecision.position_action/sell_path` | `execution_layer.evaluate`（只读 position_action）、**F1 起四入口消费终态**：chat/tools.py（观察量+建议）、web/app.py、tui/app.py、cli/main.py l/la（观察量+建议）；`_print_plain_summary`（**未接 execution_eval**，探针 A/B，F2 修）、`watch` 入池钩子、evidence | 终态统一出口 |
| `ExecutionEvaluation.blocked/block_reason` | `cli/main.py` 展示（摘要未消费）、四端 | blockers 进 DecisionPacket |
| `AIModifierResult.*` | `orchestrator.analyze` 三合并分支 | direction/strength 语义修复（独立小批 A/B） |
| bz `total/normalized/effective_grade` | CLI 展示、`_mode_from_grade`（定 mode）、`backtest_engine._resolve_benzong_mode` | 不动（legacy 保真） |
| `DecisionPacket`（新契约） | **F0 无生产消费方**（仅样例+测试）；F2 起由 `analysis_service` 产出 | — |

### 冻结 hash（F0 基线，sha256）

| 文件 | hash |
|---|---|
| `configs/settings.yaml` | `fa2356ec8f049a8d99b6f07ef44e2d5cde20d50b6f7d2e4500f38feaf0656b0c` |
| `src/scanner/scan_rules.yaml` | `2a9098c2327b056db15b0f32aec277e82889a2b1b9db2c50d1110c7f17291525` |
| `src/core/strategy_layer.py` | `8a8231d717378dfa04a5341bd59cec688d664a1b447baa09de484ac815931ca3` |

### legacy 回放集指针（正式冻结随 F8 manifest）

- `tests/backtest/test_jumpA_backtest_5year.py`（5 年回测载体）、`tests/backtest/` issue_033/041 验证
- `tests/backtest/test_datafeeder_vectorized_equiv.py`（DataFeeder 等价性参照基准，**不得删除**，AGENTS §五）
- 人工标注 mode 案例集：ISSUES.md ISS-046/065 记录（注意：人工标注对 mode 层 bug 免疫，见记忆 project_ma_stage_divergence）
- 基线数字叙事：v0.8.17 = 811 passed / 2 skipped / 1 deselected（L05 口径，本批起每批更新）

### 可疑调用链复核（最新 HEAD，与 RESEARCH §3.1 对照）

| RESEARCH 坐标 | HEAD 实测 | 结论 |
|---|---|---|
| `portfolio.py:532` update_from_strategy_decision | 实为 `:533`；OPEN/ADD/REDUCE/CLOSE_ALL 路径把 `strategy_decision.position_ratio`/`new_state` 直接写持仓（CLOSE_ALL 清 entry_date/price）；HOLD_POSITION 保持原仓；M5 指纹保护 + bool 传播完好 | F1 拆分靶点确认；M5 保护不可回退 |
| 回写调用方 | **chat/tools.py（分析后观察量+建议，带回写前重读+防外部删仓）、web/app.py、tui/app.py；CLI main.py l/la（F1 新增观察量+建议落盘，此前仅内存更新 high_since_entry）** | F1 接线范围 = 4 入口 |
| `cli/main.py:1568 _print_plain_summary` | 实为 `:1571`；读 raw DecisionResult + `strategy_decision.entry_exit`，未接 execution_eval | 探针 A/B 终态相反文案病根在；F2 修 |
| `orchestrator.py:233/270/294` AI 加分 | 三分支实存（AI 分支/合并块/事件层独立分支），按实际路径执行 | F2 按分支分别测试 |
| `evidence.py:26 record_evidence` | decision/score/position_ratio 取中间态 DecisionResult；position_action/sell_path 取末端 StrategyDecision | F2 统一契约时同步 `diff` 消费方 |

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_decision_contract.py -q
# 初版 6 failed（pydantic v2 默认不校验默认值 → exchange 派生 validator 缺省不跑）→
# 修：派生移入 model_validator（构造期必跑；F0 审查 🔴-3 frozen 重构后定稿为 mode="before"）→ 36 passed
# 对抗审查 3 阻断修复后（守卫扩展/CONDITIONAL/frozen+model_construct禁用）→ 41 passed
.\.venv\Scripts\python.exe -m pytest -q
# 852 passed, 2 skipped, 1 deselected（基线 811 + 新增 41，零回归）
```

### 同类调用点扫描

- `grep -rn "DecisionPacket" src/ tests/` → 仅本契约+测试（F0 无生产消费方，符合"不接入"边界）
- 全库无其他模块定义同名契约类型；`score` 字段登记表已覆盖 14 个分数语义点，无遗漏新维度

### 用户可见变化

无（F0 纯契约层，`start.py` 行为零变化；TASKS.md F0 明示"尚无行为切换，删除新入口即可回滚"）。
用户可感知的终态变化从 F1 起（"这是建议，尚未记为成交"提示 + 确认后变化）。

### 数据/配置/模型版本

不涉及（零配置/模型改动；配置 hash 见上）。

### 迁移/回滚验证

- 回滚 = 删除 `src/core/decision_contract.py` + `tests/core/test_decision_contract.py` 两个文件即回到 `649c4b7` 行为（无任何既有文件被改动，历史文件不变——TASKS.md F0 回滚条款满足）
- 迁移：无（无旧数据消费新契约）

### 状态：VERIFIED（2026-09-25：code-quality-guard 3 阻断修复后通过 + 进度对齐监督员 7 项清单全过无阻断；监督窄缝"confirmed 未知时 delta 必须 None"已随 F2 批补 + 回归测试）

---
## F1 — 已确认持仓与分析建议分离（最高优先）

- **任务**：F1 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`e99b280`（F0 契约冻结）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/data/proposals.py` | 新增：Proposal/FillRecord/ProposalStore（~/.muyun/proposals.json，原子写+内容指纹冲突拒绝+惰性过期 TTL 7 天+同股同日幂等合并） |
| `src/data/portfolio.py` | 删除 `update_from_strategy_decision`（建议整包当事实回写的病根）；新增 `record_analysis_observation`（观察量白名单原地更新）/`record_proposal`/`confirm_fill`/`pending_proposals`/`reject_pending_proposal`；PositionRecord 加 `holding_verification`（LEGACY_UNVERIFIED 迁移+一次提示）；M5 保护原样保留 |
| `src/chat/tools.py` / `src/web/app.py` / `src/tui/app.py` | 回写→观察量+建议（chat 保留重读+防外部删仓门） |
| `src/cli/main.py` | l/la 观察量+建议落盘（la 的 high_since_entry 此前仅内存更新，现持久化）；manage_positions 加 confirm 动作 + pos list 待确认建议区 + LEGACY 记录核对提示；ratio 签名 float\|None（add 兜底 0.20 内移） |
| `start.py` | pos confirm 解析/run_cli/帮助菜单 |
| `src/cli/plain_errors.py` + `docs/报错速查手册.md` | 新告警人话映射 5 条（另有 2 条由既有通用映射命中；三处同步纪律） |
| `tests/core/test_portfolio_observation.py` / `test_confirmed_fill.py` | 新增 8+13 测试；`test_iss078_portfolio_safety.py` 4 测试迁移新方法（语义锁保留） |
| `AGENTS.md` §五 / `docs/chat模块架构文档.md` | update_from_strategy_decision 表述 → 三拆语义（P1-7 文档同步） |

### 已完成条款（对照 TASKS.md F1）

| 条款 | 落点 | 证据 |
|---|---|---|
| 临时持仓20%→建议EXIT且受阻，记录仍20%且成本/日期不变 | 观察量白名单+原地更新 | `test_exit_proposal_does_not_touch_confirmed_position`（四要素断言，旧实现下必红） |
| 用户确认部分成交才减仓，重复确认幂等 | confirm_fill + fill_id 账本去重 | `test_partial_sell_then_full_sell_with_proposal` / `test_duplicate_fill_id_idempotent` |
| 双实例冲突不吃更新 | portfolio.yaml 走 M5 _save；proposals.json 加内容指纹冲突拒绝（P1-2）+ refresh 读-改-写合并 | `test_confirm_fill_conflict_does_not_eat_update` / `test_ledger_conflict_rejects_stale_store` |
| 所有分析入口都不能伪造持仓 | 数据层双闸（观察 rec None→False；建议需真实持仓）+ 四入口全查 | `test_observation_never_fabricates_position` / `test_proposal_requires_real_position`；全库 grep 无漏网（backtest/scanner 无持仓写入） |
| 同日重复分析不重复推进交易日计数 | 绝对值写入+无变化不落盘+ISS-086 日期去重 | `test_same_day_reanalysis_does_not_double_advance` |
| 用户可见"这是建议，尚未记为成交"与确认后变化 | chat 结果注记 + pos list 待确认区 + pos confirm 回显 | 三处文案如实（含"尚未记为成交"原文） |
| 旧记录 LEGACY_UNVERIFIED 迁移，保留未知字段 | from_dict 缺标记→LEGACY_UNVERIFIED（数值原样）+观察量原地更新不重建 | `test_legacy_record_tagged_unverified_values_preserved`；ISS-078 4 锁迁移全绿 |
| 持久化需串行写锁+版本冲突拒绝 | 初版口径=单进程+原子替换+指纹冲突拒绝（DESIGN ADR-F03）；账本补齐 P1-2 | 见上冲突测试 |
| 状态机 PROPOSED→CONFIRMED/PARTIAL/REJECTED/EXPIRED | 全链路（含 ADD 腿 P1-3 修复、恰达目标 P1-4 修复） | `test_add_proposal_confirmation_completes` / `test_reduce_exact_target_confirms_not_partial` |
| 迁移 dry-run 列旧记录解释，不自动修正 | LEGACY_UNVERIFIED 仅加标记不改数值+一次核对提示 | 同上迁移测试 |

### 未完成条款（保持 TODO）

- REJECTED 状态无独立 REPL 命令（内部 API 可达：pos confirm 持仓已删时自动 REJECTED；用户主动拒绝=忽略至 TTL 过期）
- FLAT+0仓+冷却0 的 legacy 僵尸记录不自动清理（审查 P2-5：旧管线同样不清，防误删用户记录；靠 LEGACY 一次提示引导 `pos rm`）——登记不修
- 同股分仓台账（DESIGN §3：首版单 active 意图）按设计留后续立项

### 对抗审查记录（code-quality-guard，2026-09-25，7 阻断+6 建议）

- 🔴 P1-1 strategy_state:null 崩溃（已复现）→ **已修**：两处 null 归一 + l/la 异常路径 debug→warning + 回归锁
- 🔴 P1-2 账本零版本冲突保护（违反任务卡明文）→ **已修**：ProposalStore M5 同款指纹冲突拒绝+refresh+回归锁
- 🔴 P1-3 ADD 确认后永不转 CONFIRMED → **已修**：BUY 分支同样匹配建议+回归锁
- 🔴 P1-4 REDUCE 恰达目标误标 PARTIAL → **已修**：partial 按"是否达建议目标"判+回归锁
- 🔴 P1-5 pos rm 后 pos confirm 崩溃 → **已修**：先查持仓记录，None 时作废建议（REJECTED）+人话提示
- 🔴 P1-6 新 WARNING 零映射 → **已修**（审查运行期间先行补齐）：plain_errors 5 条新映射（另 2 条由既有通用条命中）+手册新节
- 🔴 P1-7 AGENTS.md/chat架构文档仍教调已删方法 → **已修**：三处改写三拆语义
- ⚠️ P2-1 价格校验双标 → **已修**（两分支统一 _validate_price→FillResult）；⚠️ P2-2 冷却期记录加仓不重置 → **已修**（空仓再建仓重置 lifecycle+state+回归锁）；⚠️ P2-3 观察量测试未注入账本路径 → **已修**（_pm 注入，脚本直跑安全）；⚠️ P2-4 成交日冷却多扣一天 → **已修**（写 last_tick_date=成交日+回归锁）；⚠️ P2-5 僵尸记录 → 登记不修（见未完成条款）；⚠️ P2-6 账本坐标 → 已随本记录更新

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_confirmed_fill.py tests/core/test_portfolio_observation.py tests/core/test_iss078_portfolio_safety.py -q
# 34 passed（修复后含 8 个审查回归锁）
.\.venv\Scripts\python.exe -m pytest -q
# 873 passed, 2 skipped, 1 deselected（F0 后 852 + F1 净增 21：obs 8 + fill 13；ISS-078 4 测试迁移净 0）
```

### 同类调用点扫描

- `grep -rn "update_from_strategy_decision" src/ tests/` → 仅 docstring/注释的历史指称，零活调用
- `grep -rn "record_analysis_observation\|record_proposal\|confirm_fill" src/` → 四入口 + REPL + 数据层内部，无第六处写入路径

### 用户可见变化（v0.8.18）

1. 分析不再把建议当持仓写盘——`l/la/chat` 分析持仓股后，持仓数量/成本/日期永不被建议改动；建议出现在 `pos` 列表「📌 待确认建议」区（"这是建议，尚未记为成交"）
2. 新命令 `pos confirm <代码> [变化] [价]`：确认实际成交（部分成交→剩余待办保留；重复 fill_id 幂等）
3. 旧持仓记录自动标 LEGACY_UNVERIFIED 并提示核对一次

### 收益实验

不适用（F1 为正确性修复，无策略参数改动；DESIGN 明示"修复安全缺陷后可能降低历史收益，这是修正隐含风险敞口"）。

### 迁移/回滚验证

- 迁移：旧 portfolio.yaml 零改动加载（缺标记→LEGACY_UNVERIFIED 只在下次保存时写入新字段）；proposals.json 首次使用自动创建
- 回滚：回到 e99b280 可恢复旧建议流（回写调用方在旧 commit 自洽）；**不能恢复"分析自动当成交"**（TASKS F1 回滚条款原文满足）

### 状态：VERIFIED（2026-09-25 监督员 7 项清单全过无阻断：四要素断言/M5 零回退/不伪造持仓双闸/幂等/四端接线全实证；发现 A-E 全闭环——A/C 账本更正、B pos 列表 LEGACY 提示、D 版本五处同步、E entry_price 置 None（回归测试留 F3 顺手补））

---
## F2 — 最终裁决与所有输出一致

- **任务**：F2 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`83698f2`（F1 持仓事实分离）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/analysis_service.py` | 新增：`build_decision_packet`——末端 Strategy+Execution → 唯一 DecisionPacket（防御钳制：旧七层非法组合转合法包+reason_code 留痕，绝不丢弃退出意图） |
| `src/cli/action_view.py` | 新增：`terminal_verdict`（终态行动判定，七 bucket）+ `render_action_card`（人话行动卡） |
| `src/core/orchestrator.py` | ① `analyze_packet` 适配出口（ADR-F01：旧 analyze 协议保留）；② AI/事件调节方向感知 helper `_apply_sentiment_to_decision` + 三处合并点接线（AI 分支/事件合并增量/事件独立）+ `ai.direction_aware_adjustment` 开关 |
| `src/core/ranking_layer.py` | `_calc_technical_score`：SELL 强度记 0 分不入买入候选排名（探针 D 病根；原始强度留 detail 诊断） |
| `src/cli/main.py` | `_print_plain_summary` 终态化：第一节由 `terminal_verdict` 驱动（execution_eval 新参数）；l/la 构造 packet 传证据；原始卖点被压制时仅作诊断注解 |
| `src/chat/tools.py` | 构造 packet 传证据（chat 层） |
| `src/cli/evidence.py` | `record_evidence` 追加终态字段（decision_id/desired_action/target_weight/execution_status/research_status/policy_id，缺字段=当时未记录）；`diff_evidence` 同步消费 desired_action/execution_status/target_weight |
| `configs/settings.yaml` | `ai.direction_aware_adjustment: true`（false=回 legacy 统一加减口径，A/B 对照开关，满足 TASKS"改变行为的要 A/B 不混同等价适配"） |
| `src/core/decision_contract.py` | 契约窄缝补（监督员 F0 报告）：confirmed 未知时 delta 必须 None + 回归测试 |
| `tests/core/test_action_view.py` / `test_analysis_service.py` / `test_ai_direction.py` / `test_decision_contract.py` | 新增 8+16+8+1 测试 |

### 已完成条款（对照 TASKS.md F2）

| 条款 | 落点 | 证据 |
|---|---|---|
| PROBES A/B 转真正的 PlanGuard/Execution 接线回归 | terminal_verdict 用真实 StrategyDecision/ExecutionEvaluation 对象；摘要级全链测试 | `test_probe_a_suppressed_sell_shows_hold`（压制 HOLD 不得播报卖出）/ `test_probe_b_forced_exit_shows_exit`（强制退出不得播报无事）/ `test_plain_summary_probe_a/b`（经 _print_plain_summary 全链） |
| 所有强制退出路径与压制路径覆盖 | verdict 映射表全覆盖 6 position_action × blocked；摘要三分支（EXIT/BLOCKED/注解） | `test_action_mapping_table` / `test_exit_blocked_keeps_exit_intent` / `test_plain_summary_exit_blocked` |
| 行情同快照各入口输出相同终态 | build_decision_packet 唯一构造 + terminal_verdict 唯一判定（REPL/chat 同源）；packet 构造期不变量锁非法包 | `test_analysis_service` 13 测试 + action_view 8 测试 |
| SELL 强度不再被当买入吸引力 | ranking 技术分 SELL→0 + 接线哨兵测试（三合并点必须走 helper、无裸加法残留） | `test_ranking_sell_strength_not_buy_attractiveness` / `test_orchestrator_merge_sites_use_direction_aware_helper` |
| AI score 语义独立小批：明确 direction/strength，禁止看空降低卖出强度 | `_apply_sentiment_to_decision`：SELL+负调节→跳过分数调节（仓位上限/强制状态照常）；开关可回 legacy | `test_bearish_does_not_weaken_sell` / `test_black_swan_does_not_weaken_sell` / `test_legacy_switch_restores_old_behavior` |
| 原 buy/sell 路径原因仍可查，legacy 安全网不退化 | legacy_trace 投影（decision/action_strength/position_action/position_ratio/sell_path 全保留）+ 摘要"未采纳"诊断注解；安全网代码零改动（FORCE_EXIT_SCORE/PlanGuard/force_exit 原样） | `test_policy_id_is_legacy_and_trace_projection`；diff 审查可证零裁决逻辑改动 |
| 证据卡/diff/排名消费终态 | evidence 终态字段追加 + diff 消费 + ranking SELL→0 + 摘要终态化 | `tests/core/test_evidence.py` 既有锁全绿（追加式不破坏） |
| BUY机会榜与持仓风险榜分开 | ranking 层只服务买入候选（scan 唯一消费方），SELL 不再入榜排序 | ranking_layer 改动 + 测试 |
| 回滚条款：统一输出与事实修复保留；新 AI 调节语义可单独关闭 | direction_aware_adjustment=false 即回 legacy 口径；摘要/证据/排名统一为正确性修复保留 | `test_legacy_switch_restores_old_behavior` |

### 未完成条款（保持 TODO）

- chat/Web/TUI 直接消费 DecisionPacket 渲染：chat formatter 已展示终态 position_action（双列并存：原始决策+终态动作），行动卡（action_view.render_action_card）进 chat/Web/TUI 视图留给 F7 统一行动工作台（TASKS F7 文件清单）
- **display_result 详版（l 全版/a/json 入口）未终态化**：展示原始 result.decision/position_action（任务卡允许"原始信号在详版报告"，但缺"原始信号非终态"标注）——F7 统一行动卡时补标注
- watch 准入消费研究资格（ADR-F08 自动 watch 准入定义）：现维持 v0.8.12 语义，F7 随 today 服务重定义
- **confirmed_ratio 三入口口径统一**（F2 审查 P2）：现 l/chat"无持仓记录=0.0"、la 同——portfolio 读取异常时应传 None（组合未知）而非 0.0（钳制基于错误事实）；随 F3 数据资格层贯通
- **reason_codes 码化**（F2 审查 P2）：当前混入中文人话钳制说明（如"适配钳制: 已持仓时 OPEN 转 ADD"），F7 机器消费前改结构化码+人话字典
- "AI 已结合技术背景再调节=证据重复计权"（RESEARCH §3.1）：prompt 层重构属 F6 范围，F2 只修方向语义——登记不修

### 用户可见变化（v0.8.18 同批）

1. `l` 人话摘要不再自相矛盾：PlanGuard 压制后说"继续持有"（附原始卖点诊断注解），强制退出救回后说"按纪律卖出"——不再出现"卖出信号"与"继续持有"同时出现的打架文案（PROBES A/B 实证的两个病根）
2. 退出受阻（跌停/停牌）时摘要明说"现在卖不出，持仓记录不变，下个时段再检查"——不再误读为"继续看好"
3. `diff <代码>` 新增终态对比（建议动作/执行状态/目标仓位变化）
4. 看空新闻不再软化卖出信号（方向感知调节，可在 settings.yaml `ai.direction_aware_adjustment: false` 回旧口径对照）
5. scan 排名不再把强卖出信号当高技术分买入候选

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_action_view.py tests/core/test_analysis_service.py tests/core/test_ai_direction.py -q
# 32 passed
.\.venv\Scripts\python.exe -m pytest -q
# 906 passed, 2 skipped, 1 deselected（F1 后 873 + F2 净增 33：action_view 8（含摘要全链 3）+ analysis 16 + ai_direction 8 + contract 1）
```

### 同类调用点扫描

- `grep -rn "decision_result.score +" src/` → 仅 `_apply_sentiment_to_decision` 内部一处（三合并点全部接线，哨兵测试锁死）
- `grep -rn "score \* 100" src/core/ranking_layer.py` → 仅 BUY 路径；SELL 提前返回 0
- 摘要旧口径完整保留在降级分支（strategy_decision=None 时）——watch 池文案测试未破坏

### 迁移/回滚验证

- evidence.jsonl 旧记录零迁移（终态字段追加，缺字段=当时未记录，diff 不误报）
- 回滚：ai.direction_aware_adjustment=false 回 AI legacy 口径；摘要/证据/排名/统一终态为正确性修复不随回滚撤销（TASKS F2 回滚条款）

### 状态：VERIFIED（2026-09-25 监督员终核全过无阻断：PROBES A/B 真接线/安全网逐行零退化/哨兵测试/P0 断链真修复均实证；906 passed 监督员独立复跑一致）

---
## F3 — 证据快照与时点数据资格

- **任务**：F3 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`a04aea3`（F2 统一终态）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/data/research_snapshot.py` | 新增：EvidenceRecord（时点三分离+口径+revision）/EvidenceSnapshot（strict PIT 闸门+内容 hash）/qualify 资格判定/窄适配 record 构造（market/announcement/forecast/financial/user_asserted/rag）/live 惰性抓取 helper |
| `tests/core/test_research_snapshot.py` | 新增：20 测试（含审查修复回归锁：重述非冲突/多期非冲突/单位归一/live 视图 None 安全） |
| `plan/fusion/DATA_COVERAGE.md` | 新增：数据契约与可得性覆盖报告（诚实矩阵：可PIT/live-only/待现场探查三类分明） |

### 已完成条款（对照 TASKS.md F3）

| 条款 | 落点 | 证据 |
|---|---|---|
| 未来公告和后发重述不进入旧快照 | strict PIT 闸门（available_at 缺失或>as_of 必拒）+ revision_id 分版 | `test_future_announcement_excluded_from_old_snapshot` / `test_late_restatement_does_not_enter_old_snapshot` / `test_market_bar_pit_natural` |
| 元/万元及累计/单季口径可验证 | normalize_amount（未登记单位拒猜）+ financial_record period_kind 必填 | `test_unit_conversion_explicit` / `test_period_kind_must_be_explicit_for_financial` / `test_qualify_respects_period_kind` |
| 删必需字段后资格变 INCOMPLETE | qualify 显式命名缺失/过期/冲突 | `test_missing_required_evidence_incomplete_named` / `test_stale_evidence_flagged` / `test_conflicting_evidence_reported` |
| RAG 方法文本不当公司事实 | qualify 默认排除 source_kind=rag | `test_rag_text_not_company_fact` |
| 同快照重放稳定 | snapshot_id 内容 hash（evidence_id/fetched_at/入序无关） | `test_snapshot_id_stable_across_replay_and_order` |
| 回测路径零实时网络 | 模块层禁网络 import（AST 源码守卫）+ live helper 函数体内惰性 import | `test_module_import_is_network_free` |
| 先支持行情/官方公告/财务三类，不重建数据层 | 窄适配 record 构造函数消费现有 provider dict；live helper 复用 get_recent_announcements/get_latest_forecast | DATA_COVERAGE.md 矩阵 |
| 建立关键财务可得性矩阵，不足则缩小首发范围 | DATA_COVERAGE.md：严格PIT 可用=行情+业绩预告+官方公告；财务三表待现场探查；分部营收走人工通道；长期财务包在验证前不启动 | DATA_COVERAGE.md §2 |
| 证据异常/过期/未知分开 | quality_status 七态 + 时效判定 + CONFLICTED 显式 | `test_conflicting_evidence_reported` 等 |

### 未完成条款（保持 TODO）

- 财务三表（baostock query_profit_data 等）接线：公布日字段可得性需 external 现场探查
  （tests/data_sources/test_all_api.py 扩展，opt-in）——探查前不进严格快照、长期财务包不启动
- 分部营收自动取得：无稳定接口，USER_ASSERTED 人工通道已备（DESIGN 允许）
- ThesisRecord（DESIGN §5.2 投资逻辑记录）：属 F5 PlanV2 范围（thesis 与计划绑定落地），
  F3 只交付证据层

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_research_snapshot.py -q
# 初版 5 failed（snapshot_id 混入 evidence_id 重放不稳 / helper 用真实 now 对固定 as_of
# 时序脆弱 / 口径提示英文）→ 修：_content_hash 用 content_fingerprint / rag+user_asserted
# 显式 available_at 参数 / 口径中文提示 / bar 时区测试修正 → 16 passed
# 对抗审查 3 阻断修复后 → 20 passed（重述语义重写 + live 视图 None 安全 + 巨潮源标）
.\.venv\Scripts\python.exe -m pytest -q
# 926 passed, 2 skipped, 1 deselected（F2 后 906 + 净增 20；F4 预置文件 stash 隔离后实测）
```

### 同类调用点扫描

- `grep -rn "EvidenceSnapshot\|research_snapshot" src/` → 仅本模块+测试（F3 无生产消费方，
  F4/F5 接线）；现有 provider 调用方零改动（窄适配旁路可回滚）

### 用户可见变化

无（F3 数据层基建，`start.py` 行为零变化；证据层消费方 F4/F5 落地时才可感知——
TASKS F3 定位为研究可信基建）。

### 迁移/回滚验证

- 回滚 = 删除 research_snapshot.py + 测试 + DATA_COVERAGE.md 即回 a04aea3（零既有文件改动）
- 缺字段不补假数据：financial_record 无 period_kind 拒构造 / 无公布日 available=None 不进严格快照

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：3 阻断修复带回归锁、越权/数字/告警纪律全过；财务三表接线随 external 探查已登记）

---
## F4 — 三路候选与最小因子包

- **任务**：F4 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`5d14727`（F3 证据快照）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/candidate_pool.py` | 新增：CandidateRoute 三路 / RouteContribution 谱系 / Candidate 并集去重 / CandidateSet（配额确定序截断+excluded 可追溯+fingerprint 排列无关）/ scan·bz 窄适配构造（量比缺失→volume_ratio_missing） |
| `src/core/factor_registry.py` | 新增：FactorSpec 登记模板（DESIGN §5.3 全字段）+ 首批 8 因子登记 + validate_registry 自检（needs_data_probe 标记上游未验证因子） |
| `tests/core/test_candidate_lineage.py` | 新增：17 测试（含审查修复回归组：法C形态/空码留痕/rank配额/来源截断原因/同路多规则谱系/dict序不变/filter序不变） |

### 已完成条款（对照 TASKS.md F4）

| 条款 | 落点 | 证据 |
|---|---|---|
| 多渠道同股只计算一次 | build_candidate_set 并集去重（同股一个 Candidate，contributions 记全部路） | `test_union_dedup_same_stock_once` |
| 候选来源配额不会因输入顺序变 | 路内 (code, rule, rank, filters) 确定序截断 + fingerprint 排列无关 | `test_quota_stable_under_input_permutation` |
| 长期候选不受当天回调必要条件误杀 | LONG_QUALITY 路独立准入+独立配额 | `test_long_quality_admitted_without_technical_conditions` / `test_quota_per_route_independent` |
| 快照缺量比的候选标待验证，不宣称缩量 | scan 适配 missing_filters=volume_ratio_missing（missing≠applied） | `test_missing_volume_ratio_marked_not_claimed` / `test_scan_adapter_marks_missing_amount_too` |
| 不新增龙头偏向，不把小盘按论文一刀切删除 | 候选池层只有配额无规模歧视 | `test_no_size_or_leader_discrimination` |
| 保留 source/rule_version/applied/missing filters/rank completeness/**来源截断原因+配额截断记录** | RouteContribution.source_truncation_reason + excluded_by_quota（F4 审查 P1 断链修复：适配器收下→构造时透传） | `test_source_truncation_reason_preserved` / `test_quota_stable_under_input_permutation` |
| 新因子登记文件 | factor_registry 8 因子全字段登记（definition/inputs/unit/missing_policy/sector_scope/availability_rule/economic_hypothesis/experiment_id/needs_data_probe） | `test_factor_registry_complete_and_valid` |
| 避免加一个含糊综合分 | 登记表无综合分；缺失政策全部显式 UNKNOWN/NOT_APPLICABLE | `test_no_composite_score_in_specs` |
| 旧六维按原口径显示 | 零改动 bz scorer（`git show --stat` 可证） | — |

### 未完成条款（保持 TODO）

- **同研究预算比较召回效果**（TASKS F4 验收条款）：实验比对类——挂 E4 实验执行/F7
  接线后可测；F4 交付池本体（fingerprint 可作实验对账键）。审查 P1 指出本条款原记录
  无声缺失，已补登记
- 候选池生产接线（today 命令消费 CandidateSet）：属 F7 统一行动工作台（TASKS F7 文件
  清单）；F4 交付池本体+窄适配构造函数，旧 scan/bz 命令零改动（回滚=关新路线，满足回滚条款）
- 质量路自动准入数据源（长期质量筛选依赖财务因子）：needs_data_probe 因子在 F3 矩阵
  探查前只登记不计算——质量路现以显式输入（如观察池/用户名单）为源
- 相对趋势/执行容量两因子（行情天然 PIT，needs_data_probe=False）的计算函数：随 F5
  策略层/组合预算接线时实现（F4 只登记定义）

### 对抗审查记录（code-quality-guard，2026-09-25，1 P0+4 P1+8 P2）

- 🔴 P0 from_theme_results 与 bz 法C 真实形态（{"code","name",...}）不兼容——INDUSTRY 路静默清零 → **已修**（双键回退 stock_code/code + name 回退 + 空码 skipped_invalid 计数留痕）+ 回归锁
- 🔴 P1 截断原因断链（适配器收下 _truncation_reason 构造时丢弃，账本宣称不实）→ **已修**（RouteContribution.source_truncation_reason 透传 + 账本行改如实）+ 回归锁
- 🔴 P1 配额按代码字典序截断=板块系统性偏置（与自述病根矛盾）→ **已修**（拍板：rank_in_source 优先，缺失排最后；同 rank 按 rule/code 定序）+ 回归锁
- 🔴 P1 登记表与 DATA_COVERAGE 三处不一致（demand_change 幻影类别「行业数据」/relative_trend 行业指数未接线却 probe=False/trading_capacity 停牌状态未接线）→ **已修**（矩阵补行业数据/行业指数/停牌状态三行如实标注；relative_trend 降 probe=True；trading_capacity 停牌分量 UNKNOWN 说明）
- 🔴 P1 测试恒真断言（or True）→ **已修**（改为真锁：登记模块仅 get_spec/validate_registry 两函数=「只登记不计算」机器化；弱兜底收紧为 UNKNOWN/NOT_APPLICABLE）
- ⚠️ P2 同路多规则谱系保留（只计算一次≠只记录一次）/fingerprint filter 排序/空码静默/死导入/names 回填/子串级校验注明/dict 序测试——**全部已修**；账本隔离脚注 → 已补

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_candidate_lineage.py -q
# 首版 10 passed → 审查修复后 17 passed
.\.venv\Scripts\python.exe -m pytest -q
# 943 passed, 2 skipped, 1 deselected（F3 后 926 + 净增 17；F5 预置文件 stash 隔离后实测提交树口径）
```

### 同类调用点扫描

- `grep -rn "candidate_pool\|factor_registry" src/ tests/` → 仅本模块+测试（无生产消费方）
- bz/scorer/scan_rules 零改动（旧口径保真；CACHE_VERSION 红线不适用）

### 用户可见变化

无（候选池为 F7 today 命令的基建；旧命令行为零变化）。

### 迁移/回滚验证

- 回滚 = 删除两个新模块+测试即回 5d14727；旧 scan/bz 命令可用性不受影响（零接线）

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：P0 法C 修复+4 P1 全修带回归锁；召回比较随 E4 已登记）

---
## F5 — 中期与长期 PlanV2 和纯策略

- **任务**：F5 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`ab65900`（F4 候选池）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/decision_policy.py` | 新增：HorizonPlan（PlanV2 侧挂对象，horizon 限 MID/LONG）/HorizonFacts/evaluate_horizon（DESIGN §4.2 决策表逐行机器化，行0-10 从上到下首条决定性条件优先） |
| `src/core/research.py` | 新增：ThesisRecord（DESIGN §5.2 七要素）/assess_thesis（证据驱动状态转移，UNKNOWN 不自动变 FALSE）/evaluate_invalidation_rules 三值求值 |
| `tests/core/test_horizon_policy.py` | 新增：20 测试（决策表逐行+验收条款回归） |

### 已完成条款（对照 TASKS.md F5）

| 条款 | 落点 | 证据 |
|---|---|---|
| 同股票同证据不同周期结果可解释 | 同事实组 MID→行5 REDUCE / LONG→行7 HOLD，reason 指回各自决策表行 | `test_same_facts_different_horizons_explainable` |
| 中期逻辑失效不能自动延长期限 | **决策表端结构化推导**（审查 P1-1 修复：evaluate_horizon 读 plan.invalidate_if 三值求值，TRUE→行3 EXIT / UNKNOWN→行4b REVIEW，不依赖调用方自觉） | `test_mid_invalidation_true_structurally_forces_exit` / `test_invalidation_unknown_reviews_not_add` / `test_assess_thesis_driven_by_evidence` |
| 长期短期技术噪声不触发未约定退出 | 行7：short_term_noise_only → HOLD 留痕（technical_exit 标志也不升级成退出） | `test_long_short_term_noise_does_not_exit` |
| 长期缺财务不发质量合格结论 | 行4 先于行8：research INCOMPLETE 时 quality_valuation_ok 也不 OPEN/ADD | `test_long_missing_financial_never_quality_pass` |
| 新计划需确认后才激活 | 行0 激活门：accepted_at 空 → 只 REVIEW | `test_unaccepted_plan_only_reviews` |
| 未支持行业明确限制 | unsupported_industry → research 强制 NOT_APPLICABLE → 不填通用数字 | `test_unsupported_industry_limited` |
| 复核日只触发 REVIEW 不是强制卖 | review_due 标记不改 VALID 持有结论（行9） | `test_review_due_is_not_forced_sell` |
| 结构化失效条件；UNKNOWN 与 FALSE 分明 | InvalidationRule + evaluate_invalidation_rules 三值 | `test_invalidation_three_value_semantics` |
| legacy 气宗180自然日/阶段双实现/Chandelier 保持原行为 | 零改动（strategy_layer/plan_guard/entry_exit 零文件触碰） | `git show --stat` 可证 |
| 新增策略ID，不用旧 mode 代替 horizon | fusion_mid_v1/fusion_long_v1；HorizonPlan.horizon 限 MID/LONG（LEGACY 拒收） | `test_policy_ids_are_new_not_legacy_mode` |
| 旧计划零写入即可继续读取 | 侧挂对象独立，不 import TradePlan（命名空间断言） | `test_plan_v2_is_sidecar_no_tradeplan_write` |
| UNKNOWN 与 FALSE 分明（组合未知不给精确目标） | budget_available=None → HOLD/OPEN 有条件方向 target=None | `test_unknown_budget_no_precise_add` |

### 未完成条款（保持 TODO）

- PlanV2 持久化存储与用户确认流程（`pos plan` 家族扩展）：属 F7 today 工作台接线
  （accepted_at 的用户确认动作需要入口）；F5 交付纯策略层+侧挂对象
- MID 转 LONG 的用户确认流程入口：语义锚已立（mid_to_long_requires_new_assessment），
  流程入口随 F7
- 决策表与 legacy 七层的并行运行开关（shadow/opt_in 接线）：属 F8/F9 实验开关
- 行5 的 REDUCE 目标权重计算（依"已接受退出策略"细化）：需 plan.exit_policy_id 具体
  化后落地（F7 计划编辑流程）

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_horizon_policy.py -q
# 初版 3 failed（assess_thesis 无规则时误判 REVIEW_REQUIRED / 耦合断言查 docstring /
# model_copy 不校验）→ 修：状态转移顺序调整（None=未给规则按事实评估）+ hasattr 断言
# + 直接构造校验 + PlanV2 horizon 限 MID/LONG → 20 passed
.\.venv\Scripts\python.exe -m pytest -q
# 963 passed, 2 skipped, 1 deselected（F4 后 943 + 净增 20）
```

### 同类调用点扫描

- `grep -rn "evaluate_horizon\|HorizonPlan" src/ tests/` → 仅本模块+测试（F5 无生产消费方）
- strategy_layer/plan_guard/entry_exit/benzong 零改动（legacy 保真）

### 用户可见变化

无（纯策略层基建；消费方 F7 today 工作台）。

### 迁移/回滚验证

- 回滚 = 删除两个新模块+测试即回 ab65900；shadow/opt_in 可关闭（接入由调用方开关控制）
- 版本对象保留，不反写旧计划、不删除历史（侧挂设计保证）

### 对抗审查记录（code-quality-guard，2026-09-25，4 P1+8 P2）

- 🔴 P1-1 invalidate TRUE→INVALID→EXIT 无结构性保障（调用方漏接 assess_thesis 时失效计划可无限 ADD；原测试名夸大）→ **已修**（决策表端三值求值结构化推导 + 真断言回归锁 + 账本证据行更正）
- 🔴 P1-2 行0 激活门掩盖已核实硬退出（违反 DESIGN §2.2）→ **已修**（行1 提前到激活门之前）+ 回归锁
- 🔴 P1-3 行6 未持有列漏预算门（budget=False 仍 OPEN + 反事实 reason）→ **已修**（落行9 WAIT）+ 回归锁
- 🔴 P1-4 行7 未持有列错给 HOLD（违反 ADR-F08）→ **已修**（未持有落行8/9 评估）+ 回归锁
- ⚠️ P2 review_due 静默吞掉 → **已修**（next_check 落包）；⚠️ 行4 未持有对齐 DESIGN 两列 REVIEW → **已修**（永真式断言收紧）；⚠️ 行8/行10 覆盖缺口 → **已补测试**；⚠️ §4.1 字段缺口（entry/exit_policy_id/risk_profile_id/accepted_risk_limits）→ **已补字段**；⚠️ 行2 死分支 exec_status → **已修**；⚠️ accepted_at 空串激活 → **已修**；⚠️ research.py 小项（FactStatus 死导入已修/horizon 校验与 LONG→MID 锚登记）；⚠️ 提交边界（F6 预置混入风险）→ 按 stash 纪律只 add F5 文件

### 未完成条款补充（F5 审查 P2 登记）

- plan.review_triggers / max_hold_until 消费逻辑（财报/事件/日期复核的具体判定）——F7 接线
- ThesisRecord.horizon 枚举校验、LONG→MID 显式记录锚——随 F7 计划编辑流程
- DESIGN §4.1 accepted_risk_limits 的结构化（现为 str 列表）——F7 组合预算输入时细化

### 红灯→绿灯证据与命令（更正后）

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_horizon_policy.py -q
# 初版 20 passed（3 处断言夸大被审查抓出）→ 4 P1 修复后 29 passed
.\.venv\Scripts\python.exe -m pytest -q
# 972 passed, 2 skipped, 1 deselected（F4 后 943 + 净增 29；
# F6 预置文件 stash 隔离后实测提交树口径——复现口径依赖 stash 预置，见交接说明）
```

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：4 P1 修复带回归锁；legacy 零触碰实证；表格测试数措辞=函数数口径随手统一备注）

---
## F6 — 证据型 AI：事实提取、反证与解释

- **任务**：F6 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`ccfcd3b`（F5 周期决策表）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/claim_extraction.py` | 新增：ClaimRecord（结构化主张+引用+revision）/derive_event_id（事件谱系去重键）/verify_claim（规则核验：无引用/引用不存在/**实体归属不符**/未来日期/单位不合法 拒收）/ClaimExtractor 协议（同输入不重复付费缓存）+ DeterministicExtractor（零 AI 确定性通道）/MockLLMExtractor（评测替身）/dedup_claims |
| `tests/ai_eval/claim_annotations.json` | 新增：冻结人工标注集 8 用例（错误实体/旧闻重炒/财报更正/上下游影响相反/缺证据/注入文本/未来日期/单位不合法） |
| `tests/core/test_claim_extraction.py` | 新增：15 测试（含标注集全量跑） |

### 已完成条款（对照 TASKS.md F6）

| 条款 | 落点 | 证据 |
|---|---|---|
| 不凭 LLM 自报 confidence 加仓 | model_confidence 仅诊断字段；核验结论与 confidence 值无关（0.9/0.1 同结论）；F2 方向感知调节已断 score 直改路径 | `test_confidence_is_diagnostic_only` |
| 非法引用拒收 | verify_claim 五规则（无引用/引用不存在/**实体归属不符**/未来日期/单位不合法） | `test_no_citation_rejected` / `test_unknown_citation_rejected` / `test_future_date_rejected` / `test_bad_unit_rejected` |
| 重复新闻不重复计票 | 事件谱系（derive_event_id 源ID→正文hash→主体/类型/时间/数值聚类）+ dedup_claims；更正公告 revision 区分不被吞 | `test_duplicate_news_deduped` / `test_correction_not_deduped` / `test_old_news_resubmission_dedup` |
| 无 AI 时模板行动卡可用 | DeterministicExtractor 零 AI 确定性通道；F2 行动卡独立存在（无 AI 依赖） | `test_deterministic_extractor_offline_usable` |
| 同输入不重复付费 | extract 缓存（输入内容 hash 键）+ calls 计数 | `test_same_input_not_double_charged` |
| 冻结小型人工标注集（六类场景） | tests/ai_eval/claim_annotations.json 冻结；确定性核验器全量跑通；LLM 提取器接入后按同集评测（正确率/漏检/捏造/费用） | `test_frozen_annotation_set_all_cases` |
| 分清 OBSERVED 与 MODEL_INFERRED | VerifiedClaim.fact_status=MODEL_INFERRED（不冒充）；user_asserted 通道独立（F3） | `test_valid_claim_passes_all_checks` |
| 注入文本免疫 | 正文指令性文字按数据处理——核验只依赖结构化字段与引用；statement 不含注入指令 | `test_frozen_annotation_set_all_cases` AN-06 |
| 上下游影响相反 | AN-04：关系默认 NEUTRAL（上游利好可能是下游成本压力），禁止自动 SUPPORTS | `test_frozen_annotation_set_all_cases` AN-04 |

### 未完成条款（保持 TODO）

- 真实 LLM 提取器接入与评测（提取正确率/重大风险漏检/事实捏造/费用与耗时对比）：
  协议+标注集+替身已备，**真实调用属 AI opt-in 评测**（external 入口，不混默认 pytest）
- 多维提取调用合并评测（六维共享一次结构化提取）：随真实提取器落地后按 ADR-F06
  "批量合并须验证各维质量没有下降"评测
- ai_modifier/event_layer/benzong 调用边界改写（现有 AI 流量改产 claim）：涉及决策
  路径行为变更——按 TASKS F2 口径需 A/B 开关，挂 F8 影子运行阶段（F2 已做方向感知
  防护，直接改写留待影子验证后）
- expect/events 共用 event_id 接线、fear 进环境字段：随 F7/F8 接线
- RAG 检索块 ID/内容 hash/版本贯通（解释引用可追到块）：F3 rag_method_record 已备
  骨架，回答级引用归属沿 F2 登记不修项推进

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_claim_extraction.py -q
# 15 passed（标注集首跑即绿；verify_claim 实体归属核验在标注集驱动下补强）
.\.venv\Scripts\python.exe -m pytest -q
# 987 passed, 2 skipped, 1 deselected（F5 后 972 + 净增 15）
```

### 同类调用点扫描

- `grep -rn "claim_extraction\|ClaimRecord" src/ tests/` → 仅本模块+测试+标注集（无生产消费方）
- ai_modifier/event_layer/benzong 零改动（边界改写挂 F8 影子阶段，见未完成条款）

### 用户可见变化

无（提取层基建；消费方 F7/F8；真实 LLM 提取器为 AI opt-in 评测项）。

### 迁移/回滚验证

- 回滚 = 删除两个新文件+标注集即回 ccfcd3b；无 AI 时确定性通道与 F2 模板行动卡均可用
  （不填"中性看好"掩盖失败——确定性通道只转写已有公告字段，无法结构化则空列表）

### 对抗审查记录（code-quality-guard，2026-09-25，5 P1+8 P2）

- 🔴 P1-1 跨来源事件聚类未按 ADR-F06 两阶段实现（改写转载分裂 event_id）→ **已修**（dedup_claims 第二阶段 event_key=主体/类型/发生日/归一值聚类；revision!=1 是更正版本跳过聚类保留可审计；值 None 不入键防误并）+ 回归锁
- 🔴 P1-2 错误实体拒收是测试池构造假象（uri 来源级弱验证可穿透）→ **已修（诚实边界方案）**：docstring 写明弱验证/强验证分界（文档级 hash+归属=强验证）；弱池穿透用例锁定为已知缺口登记；AN-01 保留强池拒收断言
- 🔴 P1-3 naive datetime 触发 TypeError 崩溃而非拒收 → **已修**（ClaimRecord tz-aware 构造期校验，EvidenceRef 同款）
- 🔴 P1-4 单位白名单伪 token"股-万股-亿股"（万股被拒/垃圾入库）→ **已修**（拆分为 股/万股/亿股）
- 🔴 P1-5 DeterministicExtractor 畸形输入崩溃违反自身降级契约 → **已修**（日期解析失败→None/relation 归一映射非法降 NEUTRAL/value 非数值→None/构造失败→空列表如实降级）
- ⚠️ P2 聚类键值归一（5亿元==50000万元）/occurred future 检查/缓存键加提取器标识/更正 lineage 登记/revision 测试锁死/永真断言修复/AN-02·03 runner 期望/LLM 通道注入防护登记/账本数字重测——**全部处理**（修或登记，见未完成条款）

### 未完成条款补充（F6 审查 P2 登记）

- **LLM 通道注入防护机制**：确定性通道的注入免疫是结构性的（不读正文语义）；LLM
  接入后 statement/relation/value 全来自模型输出，verify_claim 不审 statement 语义——
  防护机制（结构化输出 schema 约束+引用强制）随真实提取器设计（F8 影子阶段前置）
- occurred_at 未来值检查（事实 claim vs 预期事件语义区分）——随 expect/events 接线（F7/F8）
- 文档级证据池（每份公告独立 uri/hash+归属方）——强验证引用的前提，随 F3 矩阵
  「财务三表/官方公告」现场探查接线
- 更正公告自动回填 lineage_ids 指回原事件——F7/F8 接线

### 红灯→绿灯证据与命令（更正后）

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_claim_extraction.py -q
# 初版 15 passed → 5 P1 修复后 24 passed（含 9 个审查回归锁）
.\.venv\Scripts\python.exe -m pytest -q
# 996 passed, 2 skipped, 1 deselected（F5 后 972 + 净增 24：claim 15 + 审查回归 9；
# F7 预置文件 stash 隔离后实测提交树口径；此前 987 为推算值已更正——审查指正）
```

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：5 P1 修复带回归锁；表格测试数措辞=函数数口径随手统一备注；LLM 通道注入防护边界已登记）

---
## F7 — 组合预算与统一行动工作台

- **任务**：F7 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`69c39f7`（F6 证据型 AI 基建）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/portfolio_policy.py` | 新增：solve_budget 确定性约束分配（计划目标/单股/周期/行业聚合/现金/可交易额/损失预算取 min；压力损失率未知不给精确额度；sell_eligible_nav 只计可成交卖出；稳定排序+排列不变 fingerprint） |
| `src/cli/today_service.py` | 新增：build_today_view 三组分类（需要处理/继续持有/等待条件）+ render_today（LEGACY 核对提示全量可见） |
| `src/cli/main.py` | 新增 today_command 接线 |
| `start.py` | today/今日 解析 + run_cli 派发 + 帮助菜单"每日入口"组 + 命令说明字典 |
| `tests/core/test_portfolio_policy.py` / `test_today_service.py` | 新增：10+6 测试 |
| `start.py`/`main.py`/`AGENTS.md`/`README.md` | 版本 v0.8.19 五处同步 |

### 已完成条款（对照 TASKS.md F7）

| 条款 | 落点 | 证据 |
|---|---|---|
| 同快照批量分配风险预算 | solve_budget 单次求解（现金/单股/行业/周期/损失预算约束） | `test_basic_allocation_meets_target` 等 |
| 资金不足时只给可行计划 | 超限提案 rejected 带明确原因（受限约束名） | `test_cash_constraint_trims_and_rejects` |
| 卖不出不得提前花卖出现金 | sell_eligible_nav 参数语义（受阻卖出不计入） | `test_sell_cash_not_counted_unless_eligible` |
| 同主题风险合并 | industries 多标签聚合暴露（最紧约束） | `test_industry_cap_aggregates_multiple_tags` |
| 排列不变性 | 确定序排序 + fingerprint | `test_permutation_invariance` |
| 持仓版本变更使缓存失效 | F1 指纹冲突拒绝已覆盖 portfolio 侧；预算求解无缓存（每次现算——无失效问题） | 设计保证 |
| today 优先持仓风险，硬风险全部可见 | needs_action 全量展示（不截断）+ LEGACY 提示 | `test_grouping_needs_action_and_holding` / `test_legacy_notice_all_vs_partial` |
| l/la/chat 复用同一包 | build_today_view 服务函数可注入任意 PM（chat/Web 接线留 TODO） | 服务函数签名 |
| 推荐与实际成交分离，未知资金不发精确股数 | F1 语义 + 压力损失率未知不给精确额度 | `test_unknown_pressure_loss_rate_no_precise_budget` |
| 风险更高候选不自动增总预算 | 损失预算封顶与候选数无关 | `test_higher_risk_candidate_does_not_grow_budget` |

### 未完成条款（保持 TODO）

- **VALIDATION 八类用户任务走查**（含"受阻退出不能看成继续看好"误解记录）：用户验收类
  ——需用户参与，登记待用户走查（本轮以 test_today_service 文案断言为前置保障）
- chat/Web 复用 build_today_view 的展示接线：服务函数已可注入，UI 壳随各端迭代
- 用户风险档（BudgetConstraints）的持久化与设置入口：现由调用方构造传入，设置命令
  随 F9 收尾评估是否立项
- today 的 DecisionPacket 深度消费（当前消费 F1 建议+持仓事实；packet 级卡片随
  analyze_packet 进入常规分析路径后接）

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_portfolio_policy.py tests/core/test_today_service.py -q
# 初版 3 failed（损失预算单位换算错误：预算/损失率=可承载权重；排序测试现金预算写错）
# → 修：单位换算 + 测试预算更正 → 16 passed
.\.venv\Scripts\python.exe -m pytest -q
# 1012 passed, 2 skipped, 1 deselected（F6 后 996 + 净增 16）
```

### 同类调用点扫描

- `grep -rn "solve_budget\|build_today_view" src/ tests/` → today_command 消费 today_service；
  solve_budget 无生产消费方（组合预算随计划/候选接入后启用——F8 影子）
- `pos`/`diff`/`watch` 命令行为零变化（today 为新增入口）

### 对抗审查记录（code-quality-guard，2026-09-25，2 P1+11 P2）

- 🔴 P1-1 tradeable_nav 容量约束不随分配递减（N 只候选可成交 N×容量）→ **已修**（remaining_tradeable 逐笔扣减 + 回归锁）
- 🔴 P1-2 cash_nav=None 被当 0 现金（拒绝理由假称"可用现金不足"——DESIGN：没有可用配置不假装知道承受能力）→ **已修**（None 跳过现金约束）+ 回归锁
- ⚠️ P2-1 八项公式缺"个股损失预算/压力损失率"单股维度未登记 → **已补登记**（docstring 脚注 + 本段）
- ⚠️ P2-2 exits_first docstring 承诺与签名不符 → **已修**（改"返回后自行填充，不参与 fingerprint"）
- ⚠️ P2-3 trimmed_to 并列约束只显示第一个 → **已修**（并列拼接）
- ⚠️ P2-4 __none__ 哨兵泄漏进公开行业暴露 → **已修**（哨兵不入账）+ 回归锁
- ⚠️ P2-5 BudgetConstraints extra=allow 约束拼错静默放松 → **已修**（extra=forbid）+ 回归锁
- ⚠️ P2-6 score 未禁 NaN（破坏排列不变性）→ **已修**（allow_inf_nan=False）+ 回归锁
- ⚠️ P2-7 观察池读取失败静默 → **已修**（notices 显式告知）
- ⚠️ P2-8 过期计数私有双跳+文案不实 → **已修**（ProposalStore.count_recently_expired 公开 API + 近 7 天口径 + 文案更正）
- ⚠️ P2-9 跨批交接项 dropped（F2 的 watch 准入 TODO 未接续；action_view 收敛未登记）→ **已补登记**（见下）
- ⚠️ P2-10 today 接线测试非密闭（DEFAULT_PORTFOLIO_PATH 按 __file__ 推导不受 HOME 隔离，读到真实持仓）→ **已修**（monkeypatch 注入临时文件）
- ⚠️ P2-11 持仓损坏时 today 静默空态 → **已修**（notices 显式提示"显示的不是真实持仓"）

### 未完成条款补充（F7 审查 P2 登记）

- **action_view 渲染收敛**：F2 的 render_action_card 与 F7 的 render_today 为两套
  渲染语义（近似并行）——包收敛随 chat/Web 接线迭代（跨批 TODO 接续登记）
- **F2 登记的"watch 准入消费研究资格"**：F7 未落地，接续登记（随 F8 影子/候选池
  准入定义）
- chat/Web 复用 build_today_view 展示壳
- 用户风险档持久化（BudgetConstraints 设置入口）

### 用户可见变化（v0.8.19）

🆕 **`today` / `今日` 命令**：每天打开 REPL 先跑 today——三组视图（需要处理的待确认
建议[含"建议≠成交"提示与确认指引]/继续持有/等待条件[观察池]）+ 旧记录核对提示全量
可见；帮助菜单新增"每日入口"组与典型流程更新。

### 迁移/回滚验证

- 回滚 = 删除两个新模块+测试+revert 接线 commit 即回 69c39f7；today 为纯读取命令
  （零写入，不影响持仓/建议数据）

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：2 P1 修复带回归锁；八类用户走查待用户已登记）

---
## F8 — 严格回放、制度模型与消融报告（基建批）

- **任务**：F8 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：F7 批次提交（见 git log）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/experiment.py` | 新增：E0–E7 注册（信息集标签强制）/ExperimentManifest（VALIDATION §2 字段缺一不可，样本集空拒跑）/PortfolioReplay（固定组合资金纯模型）/ReplayChecks（T+1/整手/一字板/未来数据探针） |
| `tests/core/test_experiment_replay.py` | 新增：10 测试 |
| `plan/fusion/EXPERIMENTS.md` | 新增：E0–E7 计划 + 场景覆盖清单 + 统计硬门摘要 |

### 已完成条款（对照 TASKS.md F8——离线可验证部分）

| 条款 | 落点 | 证据 |
|---|---|---|
| 离线重放可复现 | PortfolioReplay 确定性（同输入同 fingerprint） | `test_replay_deterministic_fingerprint` |
| 未来数据探针红灯 | validate_no_future_bars（回放前强制调用语义） | `test_future_bar_probe_red_light` |
| 成本加大不会无解释提高净收益 | 成本单调性性质测试 | `test_cost_increase_never_improves_return` |
| 制度模型核对（T+1/数量单位/一字板） | ReplayChecks 纯函数 | `test_t_plus_1_enforced` / `test_lot_size_rounding` / `test_limit_board_blocked` |
| 固定组合资金回放≠独立满仓均值 | 共享现金池语义锁死 | `test_replay_portfolio_semantics_shared_cash` |
| E0–E7 完成VALIDATION | **基建完成、真实执行未发生**（E1/E6 runnable，其余 blocked_on_data 如实标注）——真实跑批为 external opt-in 操作 | EXPERIMENTS.md + E_SPEC 注册 |
| 严格区分信息集 | InfoSetTag 三标签强制（E5=ai_lookahead 显式） | `test_e_matrix_registered_with_source_tags` |
| 已有DataFeeder等价基准不得删除 | backtest_engine/data_feeder 零文件改动 | `git show --stat` 可证 |

### 未完成条款（保持 TODO）

- **E0–E7 真实执行**：需外源数据建库（DATA_COVERAGE 矩阵探查先行）+ external 入口
  manifest 落盘——逐实验解锁，不混默认 pytest
- 除权/分红/IPO/停牌/退市场景的真实数据跑批：检查函数骨架已备（场景表见
  EXPERIMENTS.md §3），DataFeeder 复权口径核对随 external 阶段
- backtest_engine/execution_layer 的窄适配改写：本轮零改动（独立模块消费其输出概念；
  改写随 E0 基线设计定稿后进行）

### 对抗审查记录（F7-F9 合并审查，2026-09-25，2 P1+7 P2）

- 🔴 P1-A 买入先取整后加费 → 费差边缘满仓输入崩溃 → **已修**（份额换算先扣费 `budget/(1+fee)/price` 再取整）+ 回归锁
- 🔴 P1-B T+1 基准为单标量 buy_date（G11 分批场景整仓误锁）——批次份额模型随 E0 基线设计 → **账本口径已更正**（已完成行改"T+1 仓位级粒度"）+ 未完成条款登记
- ⚠️ P2 check_lot_size 恒等函数 → **已实现真语义**（BUY 整手拒/SELL 尾股允许）+ 回归锁；⚠️ 一字板检查未接回放路径 → **登记**；⚠️ 超卖静默截断 → **已修**（拒绝）；⚠️ manifest code_commit 空串兜底 → **已修**；⚠️ E1 runnable 混标 → **已降级 blocked_on_data**；⚠️ 末日估值/精确限价无着落 → **登记**
- 说明：本段曾在 stash 循环中丢失（anchor 未命中静默 no-op——正是账本漂移同族模式），2026-09-25 用户问询时发现并恢复

### 未完成条款补充（F8 合并审查登记 + MARKET_EVIDENCE §4 补登）

- **T+1 批次份额模型**（G11 正确答案：可卖量按交易批次）——随 E0 基线设计
- 精确限价 / 一字板接入回放路径 / 末日估值政策（VALIDATION §2 成交行）——随 external 回放阶段
- **MARKET_EVIDENCE §4 补证清单**（架构师明示"本次没有穷尽制度核对，F8 必须逐市场
  核验原文"）：规则有效区间元数据（上交所 2026 版规则生效日/风险警示 5%→10% 于
  2026-07-06 生效/深交所条款）、过户费与佣金历史变化、除权日限价基准与数量单位、
  科创板/北交所特殊规则——属制度元数据 external 核对工作；当前回放的价格检查函数
  不声称完整制度适配

### 用户可见变化

无（实验基建；external 跑批入口随数据资格解锁）。

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：基建子集口径诚实标注；E0-E7 执行随数据解锁）

---
## F9 — 影子运行、验收与简化（收口批）

- **任务**：F9 / 实现者：Claude Code（Opus 5.1）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：F8 批次提交

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `plan/fusion/ROLLOUT.md` | 新增：四级启用阶梯（legacy_only→capture_only→shadow→opt_in→default）+ 各能力当前开关位置 + 停止/回滚条件 + 影子观察清单 + 源码简化纪律 |
| `使用手册.md` | 新增「今日工作台（today）」章节 |
| `AGENTS.md`/`README.md`/`start.py`/`main.py` | 版本 v0.8.19 同步（F7 批已起） |

### 已完成条款（对照 TASKS F9）

| 条款 | 落点 | 状态 |
|---|---|---|
| 实验开关 | 阶梯与开关位置登记（ROLLOUT.md §2）；ai.direction_aware_adjustment（F2）唯一运行时开关 | ✅ 登记完整 |
| capture_only→shadow→opt_in→default 按门槛推进 | ROLLOUT.md §1 门槛表：当前 capture_only（F1 记录 live），shadow 门槛=E0 基线+差异报告 | ✅ 框架就位，未晋级（诚实） |
| 先收集同日数据完整性与动作差异 | F1 建议/观察量记录即 capture 层数据；差异报告随 shadow 接线 | 部分（登记） |
| 达到正确性但未达投资证据门槛时只发布相应能力 | ROLLOUT.md：当前发布=正确性（F1/F2）+可用性（today）+基建；无收益声称 | ✅ |
| 用户手册 | 使用手册.md today 章节 | ✅ |
| **所有入口覆盖**（TASKS F9 验收首句） | today 当前仅 REPL+chat 命令桥（chat 经 parse_input/run_cli 实证可跑）；**web/tui 未接** | ⚠️ 缺口登记（随各端迭代） |
| 回滚说明 | ROLLOUT.md §3 停止/回滚条件（四触发器）+ 事实修复独立保留声明 | ✅ |
| 源码简化（删重复裁决须消费者扫描+回放等价证据） | 扫描完成：无满足三条件（空消费者/回放等价/登记）的删除对象——本批零删除 | ✅（零删除即合规） |
| 风险事件无静默吞掉 | F1-F7 各批告警映射三处同步 + F2 受阻退出文案 + F5 硬退出先于激活门 | ✅（各批已锁） |
| 性能基线实测 | **未做**（today/l/la p50-p95 与重复 AI 费用实测需 live 环境） | ❌ 登记待测 |

### 未完成条款（保持 TODO——诚实收口）

- **shadow/opt_in 阶段推进**：门槛未达（E0 基线需数据资格解锁）——TASKS F9 的
  "长期跟踪净值、未成交、AI 费用与人工覆盖"为**运行期活动**，非代码交付；框架
  （ROLLOUT.md 观察清单）已备
- 性能基线实测（p50/p95/AI 费用）：需 live 环境多次运行采样——登记待测
- VALIDATION 八类用户任务走查（F7 段已登记）：需用户参与
- 源码简化：零删除（无满足删除证据的对象）；后续删除须按 ROLLOUT.md §5 三条件

### 用户可见变化（F9 收口批次）

- 使用手册新增 today 章节（命令文档化）
- 无其他行为变化（零删除零开关翻转）

### 迁移/回滚验证

- 全部新能力按 ROLLOUT.md 阶梯就位：legacy 默认行为除安全性修复外不变
- 回滚说明落 ROLLOUT.md §3（四停止触发器 + 事实修复独立保留）

### 状态：VERIFIED（2026-09-25 监督员终核无阻断：五阶段阶梯+停止触发器+零删除纪律；所有入口覆盖缺口已登记——today 当前仅 REPL+chat 桥）

---
## 影子阶段前置批 — 影子差异捕获 + E0 正确性基线 + 数据资格探查

- **任务**：ROLLOUT §1 shadow 门槛两要素（E0 基线 + 差异报告机制）+ DATA_COVERAGE 三行探查 / 实现者：Claude Code（Opus 5.1 1M）/ 监督者：进度对齐监督员 + code-quality-guard
- **基线提交**：`1914812`（F9 收口后；本批前先行收口遗留文档指针 `e317e5a`）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/core/shadow_diff.py` | 新增：影子对照捕获（facts 映射 v1 + 模拟 HorizonPlan 派生 + evaluate_horizon 双周期 + 差异原因五标签 + JSONL 追加账本 + 分原因报告） |
| `src/cli/main.py` / `src/chat/tools.py` | l/la/chat 三入口 packet 构建后接 capture_shadow（失败 warning 不影响主流程）；新增 `shadow_command` |
| `start.py` | shadow/影子 解析 + run_cli 派发 + 帮助菜单「每日入口」+ 命令说明 |
| `configs/settings.yaml` | 新增 `fusion.shadow_capture: true`（capture 语义默认开；false 零写入） |
| `src/cli/plain_errors.py` + `docs/报错速查手册.md` | 新告警 2 条人话映射（N+2 节；三处同步纪律） |
| `src/core/backtest_engine.py` | `t_plus_1_lot_mode` 开关参数（默认 False=现行为不变）：T+1 按批次份额（G11）——可卖=持仓−当日新买；减仓路径份额封顶；末日强平只清当日以前批次（快照重算与 legacy 数值一致）；`_t1_lot_blocked_count` 计数 |
| `tests/backtest/e0_correctness_baseline.py` | 新增：E0 双臂 external runner（复用五年 CASES 人工标注 mode；manifest 落盘；results.jsonl 增量续跑；报告生成——结论判定沿用项目噪声阈 LRN-20260619-001） |
| `tests/data_sources/probe_fin_pubdate.py` | 新增：财务季频 pubDate/行业指数/停牌状态现场探查（external opt-in） |
| `plan/fusion/DATA_COVERAGE.md` | 三行「待现场探查」→ 实测结论回写（探查产物 tests/artifacts/probe_fin_pubdate.log） |
| `src/core/factor_registry.py` | 探查通过的 5 因子 needs_data_probe→False（earnings_quality/capital_return/balance_risk/valuation_range/relative_trend；business_exposure/demand_change 仍 True——上游未取得） |
| `plan/fusion/E0_BASELINE_REPORT.md` | 新增：E0 逐案对照 + 汇总 + 诚实 caveat |
| `plan/fusion/ROLLOUT.md` / `EXPERIMENTS.md` | shadow 门槛状态更新；E0 状态更新 |
| `使用手册.md` | 新增「影子对照报告（shadow）」章节 |
| 版本 v0.8.20 五处同步 | start.py/main.py/AGENTS.md/README.md/使用手册 |

### 已完成条款

| 条款 | 落点 | 证据 |
|---|---|---|
| 差异报告机制（shadow 门槛之二）| shadow_diff 全链 + `shadow` 命令 | `test_shadow_diff.py` 18 项 |
| shadow 不改主结论、不回写持仓（DESIGN）| 捕获纯读+追加 JSONL；主结论零触碰 | 开关关闭/无持仓零写入测试 |
| 差异标记原因，不只比总收益（DESIGN 硬要求）| 五标签分类 + 分原因聚合报告 | `test_report_aggregates_reasons_no_returns`（断言无"收益"字样） |
| 不把 legacy mode 映射成 horizon（F5 硬约束）| 双周期各出一包；legacy_mode 仅对照字段 | `test_both_horizons_evaluated_policy_ids` |
| 行1 硬退出不被影子流程吞（G02 对照本体）| hard_exit 先于激活门直达 EXIT | `test_hard_exit_maps_exit_in_both_horizons`（legacy HOLD vs fusion EXIT） |
| thesis 恒 UNESTABLISHED（不拿评分冒充逻辑）| 无 benzong 代理——差异主因如实登记 thesis_unestablished | 映射表 v1 docstring + 测试 |
| T+1 批次份额（G11，F8 登记「随 E0 基线设计定稿」）| backtest_engine `t_plus_1_lot_mode`（默认关） | `test_backtest_t1_lot_mode.py` 10 项；默认关=基线安全 |
| E0 manifest 缺一不可（VALIDATION §2）| ExperimentManifest.build 落盘（样本集/费用规则/信息集标签/冻结时间） | tests/artifacts/e0_baseline/manifest_*.json |
| E0 真实执行（EXPERIMENTS.md E0 blocked_on_data 解锁）| 21 案例双臂跑通，results.jsonl 42 行 | `plan/fusion/E0_BASELINE_REPORT.md` |
| 失败试验也保留（VALIDATION §3）| results.jsonl 含超时/失败行不删；坏行隔离 | runner 代码 + G14 口径 |
| DATA_COVERAGE 三行探查（E2/E4 前置）| 财务季频五接口 pubDate 官方公布日实证（600519 2023Q4→2024-04-03，同期查询一致）；申万 index_hist_sw 6463 行日线；tradestatus 字段可用（1 正常/0 停牌；附带 isST 日频） | probe_fin_pubdate.py + tests/artifacts/probe_fin_pubdate.log |

### E0 执行结果（首个真实执行的 E 实验）

- 21 有效配对；收益差异 ≥0.01pp 仅 3 例；Δpp 均值 −0.0414，最大绝对值 1.11pp（斯菱智驱）
- T+1 批次硬拦截 0 次（差异来自减仓路径份额封顶与残仓不足 5% 收口语义）
- **结论**：全部差异在项目噪声阈内（单股<2pp、整体<1pp，LRN-20260619-001）——**旧回测基线可视为与修正后基线等价**；后续 E3/E4/E6 沿用既有基线，臂 B 作敏感性参照
- 臂 A 精确复现 ISS-046 参照数字（宁德 2020 = 79.00%），harness 等价性佐证

### 未完成条款（保持 TODO）

- **影子数据积累与 opt_in 推进**：shadow 捕获已 live（默认开），数据随日常分析积累；
  opt_in 晋级条件（差异报告连续稳定 + 正确性门槛）按 ROLLOUT §1 定期复核
- **八类用户任务走查**：首轮完成（2026-09-26 用户走查，结果登记 ISS-109）——4 张卡通过、
  3 张发现可用性问题（ISS-104/105/106 登记）、diff 可对比清单已当场修（`diff` 无参数
  列表 + 测试）；退出受阻/周期冲突/预算不足三张卡界面未建，随后续批补走查
- **用户风险档已拍板**（ISS-108：单股 2 成/行业 5 成/单笔压力亏损 40%）——已落
  settings.yaml `fusion.risk_profile`，生产消费（today/组合建议）后续批接线
- **E5 AI 消融实验用户已授权**（ISS-107）——前置=真实 LLM claim 提取器（F6 登记项），
  独立批立项
- **chat/Web/TUI 的 today/影子展示接线**：报告命令当前 REPL 入口（chat 经命令桥可达）
- **E1–E7 逐实验执行**：E1 历史市场快照未备；E2/E4 财务因子接线批（探查已过，登记表已翻转）；
  E3 持有纪律双臂（以 E0 结论=既有基线为参照）；E5 AI opt-in 评测；E6 动作集宜取自真实分析日；
  E7 待 E1–E6
- 财务季频接口接线进证据层（research_snapshot 窄适配）+ 全量覆盖验证（抽样只证可得性）
- 行业归属历史回溯 caveat：baostock query_stock_industry 为当前值（updateDate 语义），
  历史行业变化不可回溯——历史重放用行业归属须另行解决（已登记 DATA_COVERAGE）

### 红灯→绿灯证据与命令

```text
.\.venv\Scripts\python.exe -m pytest tests/core/test_shadow_diff.py -q
# 18 passed（初版 4 failed：evaluate_horizon 漏传 confirmed_ratio 致 held 判定错 +
# 测试助手 None 哨兵缺陷 + store 变量清理误删——修复后全绿）
.\.venv\Scripts\python.exe -m pytest tests/core/test_backtest_t1_lot_mode.py tests/core/test_v0895_correctness_batch.py -q
# 20 passed（lot 模式 10 项 + 引擎既有回归 10 项）
.\.venv\Scripts\python.exe -m pytest tests/core/test_candidate_lineage.py -q
# 17 passed（登记表探查翻转 + 测试同步实证状态；business_exposure/demand_change 保持 True）
```

### 用户可见变化（v0.8.20）

1. 新命令 `shadow`/`影子`：影子对照报告（差异率分原因 + 最近记录；披露"shadow 模拟计划"前置）
2. 持仓股分析（l/la/chat）后台多写一条影子对照记录（`~/.muyun/shadow_diff.jsonl`）——分析输出零变化
3. `configs/settings.yaml` 新增 `fusion.shadow_capture` 开关（默认开）

### 迁移/回滚验证

- 迁移：shadow_diff.jsonl 首次使用自动创建；backtest_engine 新参数默认 False=全部既有调用零变化
- 回滚：`fusion.shadow_capture: false` 即停捕获（账本保留不删）；lot 模式仅 E0 runner 显式开启；
  E0 产物为纯新增文件，删除即回滚；探查脚本与 DATA_COVERAGE 回写不影响任何运行时行为

### 对抗审查记录（code-quality-guard 两轮，2026-09-26）

- 🔴 P1 tradestatus 文档结论引用失败首跑日志（成功复跑只到 stdout 未落盘）→ **已修**：probe 脚本补 error_code/空结果显式打印（根因盲区消除）+ 重跑落盘产物，四处文档措辞与日志核对一致
- ⚠️ P2 Monte Carlo 临时引擎漏传 t_plus_1_lot_mode（MC 臂静默降级 legacy 口径）→ **已修** + 注释
- ⚠️ P2 runner 非超时异常中止整批不记账 / 超时行阻塞续跑 → **已修**（try/except 记 error 行继续；timeout/error 行不算 done，重试可达；报告跳过 error 配对）
- ⚠️ P2 lot 部分卖出冒充 CLOSE_ALL → **已修**（CLOSE_ALL/信号SELL 主分支 + REDUCE 残仓 <5% 子支均按「sell_shares==持仓」条件化；复核残留项闭环）
- ⚠️ P2 弱断言 → **已修**（双 fixture 断言）
- ⚠️ 账本两处测试计数失实（9 项→实为 10 项；拆分 9+11→实为 10+10）→ **已订正**
- 复核结论（第二轮）：五项修复闭环 + 账本抽查属实 + 1059 全量独立复跑一致 → **可转 VERIFIED**

### 状态：VERIFIED（2026-09-26 code-quality-guard 两轮审查 + 修复闭环复核通过；遗留登记：lot 模式 REDUCE 子支标签修正无独立回归锁——该路径需 mock feeder 驱动 run()，随 E6/E3 扩展补）

---
## 通宵批 — 财务季频接线 + E6 组合实验 + 性能基线（2026-09-26 夜，用户授权自主推进）

- **任务**：影子前置批登记的下一步中可自主执行项 / 实现者：Claude Code（Opus 5.1 1M）/ 监督者：code-quality-guard（影子批两轮已覆盖同批代码面；本批新增面待下轮审查）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/data/financial_data.py` | 新增：baostock 季频五接口 → fin dict 窄适配（pubDate→available_at；单位实测锚定；缺失不补 0；30s 硬超时 + 单接口异常不拖垮其余） |
| `tests/core/test_financial_data.py` | 新增 8 测试（mock 零网络：映射/缺失/降级/登录失败提前返回/PIT 闸门拒未来） |
| `src/cli/plain_errors.py` + `docs/报错速查手册.md` | 财务季频新告警 4 条人话映射（N+3 节；三处同步） |
| `tests/backtest/e0_correctness_baseline.py` | `--dump-trades`（强制重跑双臂导出逐笔交易；非超时异常记账续跑——影子批审查 P2 闭环） |
| `tests/backtest/e6_portfolio_budget.py` | 新增：E6 双臂 runner（naive 到达序 vs solve_budget 统一预算；盯市前复权收盘；逐笔拒绝原因落报告） |
| `plan/fusion/E6_REPORT.md` | 新增：E6 v1 报告（口径 caveat + 收益差异防误读声明） |
| `plan/fusion/EXPERIMENTS.md` / `DATA_COVERAGE.md` | E6 状态 runnable→已执行（v1）；财务三表现状 未接线→已接 |

### 已完成条款

| 条款 | 落点 | 证据 |
|---|---|---|
| 财务季频进证据层（DATA_COVERAGE「财务三表」接线）| financial_data.get_financial_quarterly → financial_record | live 冒烟 600519 2023Q4：32 条证据，netProfit 775.2亿元，available_at=2024-04-03T23:59+08:00 |
| 单位语义不猜（G15）| 实测锚定：比例字段=小数比值（roeAvg 0.3618=36.18%，文档标 % 实测非——登记「倍」）；货币=元；缺失空串→None | test_financial_data 映射断言 + 模块 docstring |
| 严格 PIT 闸门消费财务证据 | EvidenceSnapshot strict：公布前 as_of 拒收（dropped_pit=全量）| `test_evidence_integration_and_pit_gate` |
| E6 同一单股动作集合（E_SPEC）| E0 臂 A 逐笔交易 --dump-trades 重跑导出（42 文件）| tests/artifacts/e0_baseline/trades/（gitignored 本地） |
| G09 拒绝原因明确 | naive「现金不足」vs 统一臂 solve_budget 拒绝/截断原因逐笔落报告 | E6_REPORT.md 拒绝明细 |
| E6 主指标（不可实现仓位/集中风险）| 最大单股权重 78.5%→30.0%；同日最大合计需求 213% NAV 不可全实现被预算协调 | E6_REPORT.md |
| 收益差异不冒充策略优劣（VALIDATION §1）| 报告显式声明 naive 收益来自先到先得路径与高集中持仓 | E6_REPORT.md 结论段 |
| 性能基线（VALIDATION §7 零写入部分）| today p50=12.6ms/p95=12.9ms；shadow p50=0.3ms；doctor p50=308ms/p95=471ms（n=7 进程内）| 本段登记；30 秒用户验收线达标 |

### 未完成条款（保持 TODO）

- E6 v1 口径升级：行业暴露约束（行业映射未接线）/ 批次份额 T+1 回放（F8 P1-B）/ 费率对齐回测口径 / per_stock_max 等用户风险档持久化
- l/la 冷热缓存与 AI 费用 p50-p95 实测：触真实持仓状态（观察量/建议写入）+ AI 费用——待用户会话
- E1/E2/E4/E5/E7 执行：E1 历史市场快照未备；E2/E4 财务因子计算接线（fetcher 已备）；E5 AI opt-in；E7 待 E1-E6
- 八类用户任务走查：需用户参与

### 用户可见变化

无（研究基建批；`start.py` 行为零变化——如实登记，符合 F3/F4/F8 基建批先例）。

### 迁移/回滚验证

- financial_data.py 纯新增模块，无生产消费方（回滚=删文件+测试）；E6 runner/报告纯新增；E6_REPORT.md 为文档产物
- 回放/探查脚本均为 external opt-in，不进 pytest 默认收集

### 对抗审查记录（code-quality-guard，2026-09-26 通宵批，2 P1+7 P2）

- 🔴 P1-1 E6 结论行「减少不可实现仓位」无数据支撑且与表格反方向（统一臂拒绝 94>56）+ 恒真死条件 → **已修**：结论只写集中度断言（78.5%→30.0%）；拒绝拆分「求解拒绝 vs 执行拒绝」分计（25/69）；删恒真条件
- 🔴 P1-2 get_close_series error_code≠0 静默返回空序列（盯市失真进报告零告警）→ **已修**（显式 print 告警，与超时路径同格式）
- ⚠️ P2 dump-trades 重复追加账本行 → **已修**（dump 模式跳过 _append_result）
- ⚠️ P2 E6 内联 bs_code 前缀推导与 _normalize_stock_code 不一致（920 北交所隐患）→ **已修**（复用 financial_data._to_baostock_code 单一事实源）
- ⚠️ P2 登记表字段漂移静默产 None 证据 / 登录失败仍发 5 请求 → **已修**（字段缺失显式告警跳过；登录失败提前返回 + 测试锁）
- ⚠️ P2 压力损失率「过闸」措辞与机制不符（估计值只过「未知不给额度」门不参与额度计算）→ **已修**（caveat 重写）
- ⚠️ P2 末日强平 exits-first 重排未登记 / 报告头硬编码 / max_same_day_demand 只报 naive → **已修**（caveat 补登记；头动态化；两臂分报）
- ⚠️ P2 北交所季频财务不支持未登记 → **已修**（DATA_COVERAGE 范围限制补笔）
- 审查确认：决策路径零侵入、CACHE_VERSION 红线不适用、告警三处同步命中、_FakeRS 契约正确、两臂公平性、报告-log-账本数字三方一致

### 状态：VERIFIED（2026-09-26 通宵批对抗审查 2 P1+7 P2 全部修复；全量 1118 passed, 2 skipped, 1 deselected）

---
## 收尾批 — 用户走查驱动修补 + RAG 硬件保护 + plan2 + 因子计算 + LLM 提取器（2026-09-26 日间至夜间，用户全程授权）

- **任务**：走查修补 + 用户拍板落地 + 剩余可自主任务 / 实现者：Claude Code（Opus 5.1 1M）/ 监督者：code-quality-guard（plan2 批 1P0+3P1 修复闭环；factor/LLM/verify 批 2P1+7P2 修复闭环）

### 文件所有权与实际改动

| 文件 | 动作 |
|---|---|
| `src/rag/embedding.py` | RAG 加载线程上限保险丝（MUYUN_RAG_MAX_THREADS——用户 14700 缩肛硬件叮嘱；默认零变化）+ AGENTS §五硬件节 |
| `src/cli/today_service.py` / `main.py` | today 等待条件逐只明细（ISS-104）+ 单股额度显示（风险档生产消费，ISS-108） |
| `src/core/entry_exit/exit_rules.py` | 摘要术语源头中文化（Chandelier Exit→吊灯止损，ISS-105） |
| `src/core/shadow_diff.py` | v2：报告中文+定位说明（ISS-106）；plan2 真计划消费（facts 逐周期作用，泄漏修复双侧断言）；plan_source 三态；归因 plan_source 感知（新标签 plan_draft_not_activated） |
| `src/data/horizon_plans.py` + `main.py plan2_command` + `start.py` | **plan2 计划V2**：每股每周期一份（mid/long 正交）；M5 指纹判据=加载态快照（审查修正）；候选副本模式（失败不污染内存）；双序兼容（审查 P0）；损坏保护拒绝写 |
| `src/cli/main.py` / `evidence.py` | diff 无参数可对比清单（走查需求）；diff --ai 可选解读层（ISS-110：事实层机械，AI ≤3 句只基于事实） |
| `src/data/portfolio.py` / `main.py` / `start.py` | pos verify（ISS-111：旧记录核对确认；CONFIRMED_FILL 溯源不覆盖；保存失败回滚——审查 P1） |
| `src/core/factor_compute.py` | 因子计算层 v1（6 因子纯函数；登记口径漂移登记 ISS-112） |
| `src/core/claim_llm_extractor.py` + `tests/ai_eval/run_llm_extractor_eval.py` | **真实 LLM claim 提取器**（引用强制归属/注入三层防御/容错降级）+ 冻结标注集评测 **8/8**（费用 8 次/4276 tokens；llm_expect f6.v2 校准） |
| `tests/core/test_batch_quotes.py` | 时钟冻结修复（17:30 后必挂的预先存在时间依赖缺陷，stash 二分确认） |
| `ISSUES.md` | ISS-104~113 登记/闭环 |
| `使用手册.md` / `README.md` / `AGENTS.md` | shadow/plan2/diff 章节 + 版本描述 + 术语表两行 + 硬件节 |

### 用户可见变化（v0.8.20 累计）

today 等待条件明细+单股额度 / `shadow` 中文报告 / `plan2`（立计划→影子真判断）/ `diff` 列表+--ai / `pos verify` / 摘要术语中文 / 全量测试 1031→1118。

### 未完成条款（保持 TODO——结构性后续批）

- **E1 候选召回**：需历史市场快照建库（全市场逐日 bar 拉取，数小时 external 跑批）+ 三路召回 harness——独立批
- **E5 本体三臂消融**：提取器已就绪并过冻结评测；决策路径 A/B 接线（须 shadow 期验证）+ 预算口径确认——独立批；前置=ISS-113 告警映射
- **E7 完整系统**：待 E1–E6
- **影子数据积累 → opt_in**：时间积累；用户 plan2 立计划后影子出真判断
- **三张卡补走查**（退出受阻/周期冲突/预算不足）：周期冲突卡现可经 plan2 双计划触发——待用户补走查
- **E6 v2 口径升级**：行业映射（industry_max 消费前置）/批次 T+1 回放/费率对齐

### 状态：VERIFIED（两轮对抗审查修复闭环；全量 1118 passed；LLM 评测 8/8 真实跑；plan2/pos verify 生命周期实跑）

---

---
## 最终批 — E 矩阵收口：E6v2/E1/E5/E7 v1 真实执行 + ISS-112/113 闭环（2026-09-26 夜）

- **任务**：剩余可自主任务全部完成 / 实现者：Claude Code（Opus 5.1 1M）/ 监督者：code-quality-guard（plan2 批与收尾批尾部各一轮，共 3 P1 修复闭环）
- **提交**：`3c79552`（E6v2+ISS-112/113）、`2fd167a`（E1）、`920454a`（E5/E7）、`d9a77f0`（矩阵声明同步）

### 完成条款

| 项 | 落点 | 证据 |
|---|---|---|
| E6 v2（批次T+1/行业约束/费率对齐） | PortfolioReplay lots 批次（F8 P1-B 收口）+ E6 runner 行业映射（baostock 18/18）+ 用户档 50% 行业约束 | 统一预算最大单股权重 78.5%→46.9%；测试 3 项（G11 三态） |
| ISS-112 登记口径对齐 | FactorSpec 四因子定义对齐 v1 实际口径 + version bump 1→2 + 修订注记 | test_candidate_lineage 全绿 |
| ISS-113 LLM 告警映射 | plain_errors 6 条 + 速查手册 N+5 节 | 三处同步 |
| E1 候选召回 v1 | live 快照 5568 只三路召回 → 并集配额 | 跨路重叠 0（互补实证）；报告 E1_REPORT.md |
| ISS-114（新发现） | baostock balance 字段跨期单位不一致（万科 0.7322→0.0073） | E1 实测；质量路改用已验证语义季规避 |
| E5 证据层三臂 v1 | 同批真实公告 6 份：无AI 6 / LLM 11 / 核验+反证（通过 6+REFUTES 5） | 报告 E5_REPORT.md；费用 6 次调用 |
| E7 漏斗烟测 v1 | 候选 35→研究完备 5→资格 5→决策表→预算 端到端 | 报告 E7_REPORT.md |
| EXPERIMENTS.md | E0/E1/E5(证据层)/E6/E7(烟测) 已执行登记；头部声明更新 | 本文件 |

### 未完成条款（结构性后续批——前置条件均已明确登记）

- E2/E3/E4：需 plan2 计划流（已建）+ 历史 PIT 数据 + 用户计划参与——前置就绪后立项
- E1 历史版/E5 本体 A-B/E7 完整版：历史快照建库（数小时跑批）+ 影子期验证——独立批
- 影子数据积累 → opt_in 晋级：时间积累（用户 plan2 立计划即出真判断）
- 三张卡补走查：周期冲突卡现可经 plan2 双计划触发——待用户

### 状态：VERIFIED（全量 1121 passed；E1/E5/E6/E7 真实跑批产物落盘；两轮对抗审查闭环）

### 使用期回看清单（用户进入实际使用留痕期——回看时看这些）

1. **影子账本**（`shadow` 命令 / ~/.muyun/shadow_diff.jsonl）：条数、差异率（分原因）、
   `plan_source=user_plan_accepted` 真判断占比——占比上升=plan2 采纳在转化
2. **plan2 采纳**：立计划数/激活数（`plan2` 列表）；真判断动作是否与用户实际操作一致
3. **硬风险零误判**：影子记录中 hard_exit_divergence 用例的复核（G02 对照质量）
4. **三张补测卡**：周期冲突卡（同一只股立 mid+long 双计划触发）/退出受阻/预算不足
   ——遇到即记录走查结果（ISS-109 延续）
5. **告警卫生**：使用期新增 WARNING 是否被人话映射命中（plain_errors 覆盖面检验）
6. **致架构师汇报**：[ARCHITECT_REPORT.md](ARCHITECT_REPORT.md)——数据积累到位后，
   opt_in 晋级决策与架构师下一轮设计迭代同时启动
