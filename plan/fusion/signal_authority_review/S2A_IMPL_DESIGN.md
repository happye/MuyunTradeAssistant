# S2a 实施设计：接口 / 公式 / 调用链 / 矩阵映射（v0.8.29.7 批）

实施负责人 Claude Code；公式 V1 与数值矩阵见 [S2_DESIGN](S2_DESIGN.md)（冻结，不重复）。
本文只定**产品落点**：谁算 E、槽怎么建、A/P/C 存哪、谁消费 A、缓存与展示怎么分桶。

## 1. E/W 固定分母（skill_engine → orchestrator → decision_engine）

- `SkillEngine`（src/core/skill_engine.py）新增：
  - `load_skills(skill_names)` 记录 `self._requested_names = skill_names`（None=全部）。
  - `base_roster(weights: dict[str, float]) -> tuple[dict[str, float], dict[str, str]]`：
    返回 (E, rule_versions)。
    - **E 的口径**：`_requested_names` 显式列表 ∩ type(base)（type 判定沿用
      `skill_types.get(name, yaml_config.get("type","base"))` 同一口径）；None 时
      取 skills 目录全部 yaml stem 中 type(base)——**文件存在即启用**（加载失败/
      执行失败仍占 W），不以本次返回 signals 反推。action/regulator 不进 E。
    - **权重**：来自 DecisionEngine.weights（正有限校验，坏权重显式 ValueError）。
    - **rule_versions**：每个启用 base 技能 = 其 YAML 内容 sha1 前 12 位
      （YAMLBasedSkill 加载时算一次存 `self.rule_version`）。
    - E 为空 / 目录缺失 → 返回空 dict（调用方按 UNKNOWN 降级，不静默换全集）。
- `Orchestrator.analyze/analyze_packet`：调用处组装
  `expected_base, rule_versions = skill_engine.base_roster(decision_engine.weights)`、
  `evaluation_as_of = data.quote_as_of`（bar 日期，独立本次时点；缺省 None→引擎标
  UNKNOWN 时点）传给 make_decision。产品全路径有 E；直调旧路径见 §4 降级。

## 2. 槽身份与一槽一票（models.SkillSignal / decision_engine）

- `SkillSignal` 新增可选字段：`rule_version: Optional[str]`（YAMLBasedSkill.execute
  时自填）、`security/evaluation_as_of: Optional[str]`（技能不自填）。
- `make_decision(..., expected_base=None, rule_versions=None, evaluation_as_of=None)`
  内部为每张 base 票建立槽：
  `slot_key = (skill_name, security, evaluation_as_of, rule_version)`，其中
  security=metadata.get("security") or data.stock_code、
  as_of=metadata.get("evaluation_as_of") or evaluation_as_of、
  rule_version=signal.rule_version or rule_versions.get(skill_name)。
  - 完全相同票（方向+置信度）合一；同槽 rows 冲突/任一 confidence 非有限或越界/
    错股错时点（metadata 与引擎绑定不一致）→ 整槽 UNKNOWN，保留 W，不投票。
  - 不在 E 的 skill_name → 不参与（不占 W）。
  - confidence=0 的合格响应计 C、贡献 0；缺失（槽无票）与 0 分别展示。

## 3. A/P/C 计算（decision_engine，纯函数化）

- `U_d = Σ w_i·c_i·I(dir=d)`（合格槽）；`W = Σ w(E)`；
  `C = Σ(合格已响应槽 w)/W`。
- regulator：沿用现有 impact 幅度（`0.5+(w-1)*0.2`，cap 0.8）得 m_legacy；
  `m_i = 1 + c_i·(m_legacy−1)`；c_i 非有限或 ==0 的 regulator **跳过**（不贡献也
  不稀释）；合法 m_i 按既有几何平均 → M_d；无 → 1。regulator 不创造 U_d=0 的方向。
- `A_d = clip(M_d·U_d/W, 0, 1)`（**不按方向和归一化**）；
  `P_d = M_d·U_d / Σ(M·U)`（分母 0 → 全 0，仅展示）。
- 方向判定 `_determine_final_signal(A, state)`：阈值/分支/优先级全部沿用
  （强 .4/弱 .25/SELL_THRESHOLDS/STRONG_SELL_RATIO）。BUY 判据抽静态方法
  `DecisionEngine.buy_gate(a_buy, a_sell, state) -> bool` 供 Strategy 复用
  （同一门，无第二实现）。
- action 覆盖（止损/止盈）逻辑不变；覆盖发生时 `DecisionResult.action_override_confidence`
  记录触发票置信度（**明确来源**，不反灌进 A）。

## 4. 降级与 UNKNOWN（缺 E/坏注册表）

- `expected_base=None`（直调旧路径）：`aggregation_protocol="legacy-no-roster"`、
  A/P/C=None、普通投票不产方向（决策=WATCH），action 覆盖照常（止损仍 SELL），
  reason 拒因；**绝不按成功票缩分母**。
- E 为空/权重坏：`aggregation_protocol="v1-unknown-roster"`，同上降级；
  独立合格退出（PlanGuard/止损/期限/三倍）不受影响。

## 5. 字段与下游收口

- `DecisionResult` 新增：`action_strength: dict[str,float] | None`（A）、
  `opinion_share: dict[str,float] | None`（P）、`coverage_ratio: float | None`（C）、
  `aggregation_protocol: str`（"s2a-v1"/降级值）、`action_override_confidence: float | None`。
- `DecisionResult.score`：= A[final_signal]（make_decision 初值）；AI/event 调节
  仍叠加其上（既有方向感知逻辑）→ score 兼容语义="调节后综合分"；**普通投票
  资格门一律读 action_strength（纯投票 A），AI/event 不能绕过基础资格**。
- trace "市场状态影响" 的 `final_scores` = A（值改变）并加
  `final_scores_protocol: "s2a-v1"`；strategy_layer 不再靠中文 step 名抓 trace：
  `_calculate_position` 直接读 `decision_result.action_strength`。
- strategy_layer 收口点（全部改读 A）：
  - `_calculate_position`：buy_score/sell_score = A；**HOLD→ADD 需先过同状态
    既有 BUY 门**（`DecisionEngine.buy_gate`），实际仓位不增加时转 HOLD（L1 语义
    保留）；`buy_score > sell_score*1.5` 单独条件不再足以 ADD。
  - `_apply_reverse_cost`（:597）：门分值 = `action_override_confidence`（action
    覆盖时，明确来源）否则 `action_strength[decision]`（D03 止损兜底改走此通道，
    兜底强度不再混入普通 score）。
  - 强止损极端检测：门分值口径与 _gate_score 一致（action_override_confidence
    优先、否则 A["SELL"]）≥0.80——止损一票否决场景 A[SELL]=0 但 override 置信度
    在明确通道；只读 A 会让首日确认门把止损 SELL 降 HOLD 延迟一天（guard P1
    实证，违反冻结合同「独立硬退出不被普通投票覆盖率取消」）。
  - `_needs_confirmation`（:287 调用，raw_score 实参）：改传门分值（同上口径）。
- entry_exit / PlanGuard：不消费普通投票 A（既有硬纪律独立）；无改动。

## 6. 分数消费者与缓存清单（先列后失效）

| 消费点 | 现读 | 改后 |
|---|---|---|
| orchestrator AI/event `_apply_sentiment_to_decision` | decision_result.score | 不变（score=调节后综合分） |
| strategy `_calculate_position` | trace final_scores | action_strength |
| strategy `_apply_reverse_cost`/`_needs_confirmation` | score | 门分值（override 或 A） |
| strategy 强止损检测 | score | action_strength["SELL"] |
| decision_engine 内部 `_calculate_position`/`_determine_final_signal` | 归一化占比 | A |
| cli/evidence.py `rec["score"]` | score | score+新增 action_strength/coverage_ratio/protocol 字段落证据 |
| chat formatter 摘要 | score | 展示 A/覆盖率 C，措辞"动作强度/有效响应覆盖"，不称胜率 |
| analysis diff（v0.8.17）score Δ | score | score 语义变化 → diff 记录带 protocol；旧记录读时标"旧协议"，不补假 A |
| shadow_diff | legacy_trace 字符串 | S2a 不桥接（S3 范围）；本批仅保证新字段不流入旧影子格式 |

**缓存失效**：decision score 无独立磁盘缓存（不进 benzong CACHE_VERSION 管
理范围）；语义分桶靠 `aggregation_protocol` 字段随证据落盘，旧记录按"旧协议"
读取。无观察协议文件需迁移。

## 7. 测试计划（tests/core/test_s2a_aggregation.py）

全部走**产品聚合（make_decision 带 E）→ 真实 StrategyLayer.process → 最终
packet**；数值矩阵 14 行沿用冻结值断言 A/P/C/decision；另覆盖：
- 固定分母：加载失败/执行失败槽占 W；删其他返回票不增强固定 BUY；顺序置换不变。
- regulator：0 置信度加入/移除结果不变；无基础方向票不能被 AI/regulator 造 BUY。
- 门收口：四种市场状态 × 阈值上下边界；HOLD 不绕 ADD；未知权重不伪造新增；
  缺 E 拒新增；C=0 时合法止损仍 EXIT/CLOSE_ALL。
- 同槽重复/冲突/未注册/NaN/1.1；confidence 0 与缺失分开展示。
- 双版本对照：同一输入旧语义（trace final_scores 旧口径构造）vs 新 A 的
  decision/仓位/packet 差异逐项解释。
- score 消费者一致性：evidence/final packet 读同一字段。

## 8. 版本与文档

v0.8.29.7；README/AGENTS/使用手册摘要措辞同步（动作强度/意见占比/覆盖率）；
CACHE_VERSION 不动（benzong 评分输入未变——技能层无评分改动）。
