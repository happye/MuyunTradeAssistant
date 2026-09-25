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

### 状态：REVIEW（待 quality-guard + 监督员核对）

---
