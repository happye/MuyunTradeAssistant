# 第三轮执行账本（J 系列）

> 开工 2026-09-27｜实施方：J 系列实施 Agent｜基线 HEAD `f53f7ef`（= 架构师复核基线 `427e110` + 交接批）
> 纪律：逐卡五维登记（范围/改动/验证/缺口/版本）；不预填成果；没有真实输出不勾完成。

## J0｜事实和评估唯一真值

状态：完成（代码+测试+监督审查修复全部落地；全量 1266 绿；遗留命题规则结构位归 J3）

- 范围：N1（无关摘录 FACT_CHECKED）/ N2（REFUTES 作正证 + shadow 重判翻转）/ N3（假引用 VALID）；类型化事实核验范围门、AssessmentStore 唯一真值、checkpoint 合法建立渠道、因子绑定合格快照、缓存全量键+防污染。
- 改动：
  - `src/core/claim_extraction.py`：ClaimRecord.verification_scope + TYPED_RULE_VERSION=`j0.typed_scope_v1` + 谓词注册表（order/earnings/exposure）+ 词元边界定位 + `_resolve_verification_scope`（显式声明或按已结构化组件保守推导）；`verify_claim_tiered` 阶梯4 重构——错误/存疑检测器在前（REJECTED/NEEDS_REVIEW 语义与 R2 一致），尾部范围门：类型齐备+来源公开时点已知才 FACT_CHECKED，自由文本/缺组件封顶 EXCERPT_GROUNDED；ClaimVerificationResult.rule_version；DeterministicExtractor 转写数值时声明 scope。
  - `src/core/research.py`：fact_has_resolvable_reference/assess_thesis 加 evidence_resolver（**None=一律不可解析**——旧兼容路径降级）；新增 SupportBinding/CheckpointCondition；ThesisAssertion.support_bindings；ThesisAssessment.security_id。
  - `src/core/research_service.py`：`_claim_supports` 替换为 `_build_support_bindings`（relation=REFUTES/否定→反证侧；单主张至多绑定一个 required 命题；moat 无确定性单主张通道；绑定带 rule_version+justification）；run() 新增 factor_bindings/checkpoints/assessment_store；因子绑定合格快照过滤（未绑定→缺口明示，raw dict 旁路封堵）；checkpoint 求值（经核实材料+用户确认→TRUE；条件不进 facts_observed）；评估落 store、草稿带真实 assessment_id；缓存键含因子全量分量+规则版本+checkpoints；命中/新鲜均返回深副本。
  - `src/core/shadow_diff.py`：`_thesis_status_for` 重写（v4）——带 assessment_id 经 AssessmentStore 核对（security/horizon/snapshot/method/未来时点）消费同一 status，缺失/错配→REVIEW_REQUIRED；旧计划（无引用）降级 UNESTABLISHED（facts+refs 简化判断退役）；capture_shadow 加 assessment_store 注入；SHADOW_DERIVATION_VERSION=`shadow_v4`。
  - `src/data/research_store.py`：新增 AssessmentStore（内容寻址 `asm_<sha16>`、写一次幂等、坏文件隔离、路径注入防御）。
  - 告警三处同步：plain_errors.py + docs/报错速查手册.md（评估加载失败/评估写入失败/原始留样读取失败）。
- 验证：
  - 新增 `tests/core/test_j0_facts_assessment.py` 30 条（N1/三类扩展反例/数值边界/N2 评估一致与待复核/旧计划降级/N3 resolver/checkpoint 渠道正反例/单主张单命题/AssessmentStore 幂等隔离/因子绑定正反例/缓存分量失效+副本防污染/充分 MID 包 VALID 与缺项降级 + 审查修复回归 4 条：声明绕过/异证券检查点/空实体与旧方法版本/旧格式 id）。
  - 更新：test_shadow_diff.py（两个 VALID 预期改评估合法路径；_capture 隔离 assessment store）、test_horizon_policy.py（assess_thesis resolver 语义）、test_research_service.py（fixture 补类型化组件）。
  - 实际命令：`.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`（全量）→ **1262 passed / 2 skipped / 1 deselected**（基线 1222 + 26 条 J0 + 14 条 J1；冻结语料 cv2 15 例期望逐一复核不变）。
- 缺口（对照 J0 验收逐条）：
  - N1 不再 FACT_CHECKED ✅；三类扩展反例（指标错配/其他主体标记/缺公开时点）✅
  - N2 否定订单不进正面支撑 ✅；正式状态草稿/shadow 同或更保守 ✅
  - N3 不存在 ID/无 resolver/实体错配均不 VALID ✅
  - 充分合法 MID 合成包 VALID、缺任一必需命题降级 ✅；LONG 诚实 UNESTABLISHED ✅
  - 缓存全量键（改负债分量→新 run_id）、返回对象防原地污染 ✅
  - 旧 P1–P4 回归保持 ✅
  - **仍缺（登记不掩盖）**：命题级阈值/方向判断（PROPOSITION_RULE_VERSION 结构位）随 J3 因子绑定数据补全；`longterm_invalidation` 的 LONG 侧合法包未构造（估值能力缺，LONG 本就不达 VALID——不伪造）。
- 版本：v0.8.23（J0+J1 合批 bump，见下）。

## J1｜有边界的映射、可复用的原件

状态：完成（代码+测试落地；试点点位 1/6 已核定，其余如实 NOT_RUN）

- 范围：N4（映射非幂等/供应商恢复）；映射合同（mapping_id/observed_scope/effective_period_range/幂等/证据 hash）、原件读回/索引/零重复抓取、报告期选择、原件核对记录；试点按 DATA_DECISION §4 预算执行。
- 改动：
  - `src/data/financial_data.py`：UnitDriftMapping 合同重写（mapping_id/source_schema/observed_scope/effective_period_range=[2024-06-30, 2024-12-31]/verification_level/evidence_hashes；verification_status→verification_level）；`apply_unit_drift_mapping` 重写——幂等（mapping_id 标记 + raw 比对兜底，**两次仍 0.7294**）、范围门（边界前原样不加噪声；范围外证券/报告期→不乘 + SUSPECT 隔离）、多映射冲突→SUSPECT；fin dict 补 security_id；`select_ended_quarters`（不含未结束当季）；`register_mapping_affected`（受影响派生清单登记）；capture_quarterly_evidence 加 skip_archived（零重复抓取）+ 观测索引登记 semantic_status/mapping_id（留样仍=供应商原貌）。
  - `src/data/research_store.py`：load_raw（离线读回+注入防御+坏文件隔离）、query_observations（按证券/指标/报告期/源版本检索，坏行隔离）、has_quarter_evidence、save_original_verification/list_original_verifications（发行人原件核对记录，必需字段校验，幂等，**不自动升核定**）。
  - `src/data/research_snapshot.py`：financial_record 透传 semantic_status（范围外 SUSPECT 可达快照资格隔离）。
- 验证：
  - 新增 `tests/core/test_j1_mapping_boundary.py` 14 条（幂等×2/范围外证券/恢复供应商/边界前无噪声/冲突/不自动升核定/影响清单/load_raw/query_observations/零重复抓取/select_ended_quarters/原件记录往返+缺字段拒收/semantic_status 透传）。
  - 实际命令：同上全量 **1262 passed / 2 skipped / 1 deselected**。
- 试点实况（DATA_DECISION §4 第一批，预算内单点先行）：
  - **已核定 1 个点位**：000002（万科A）2024-06-30 liabilityToAsset——发行人原件《2024 年半年度报告》（巨潮 finalpage/2024-08-31/1221084198.PDF，sha256=3c7e7373…e5462，223 页）合并资产负债表：负债合计 1,037,761,815,611.77 / 资产总计 1,422,819,020,978.85 = **0.729370**，与映射值 0.7294 一致（差<5e-5）；表内自洽（流动+非流动=负债合计；负债和股东权益=资产总计）全过。verification 记录 `orig_6366a5eff46fb65a` 落 ~/.muyun/research/originals/；提取脚本与 JSON artifact 在 tests/artifacts/j1_original_pilot/。
  - **如实登记**：v1 提取脚本曾把「流动负债合计」子串误命中「负债合计」（已修为行锚定+自洽校验——教训入账）；其余 5 份（000002 2024Q1/Q3、600519 2024Q1/Q2/Q3）**NOT_RUN**（会话预算内先验证机制+1 点位，URL 定位与下载待下一批）；映射整体 verification_level 维持 cross_checked_pending_original（单点位不升全范围）。
- 缺口（对照 J1 验收）：
  - N4 两次 0.7294、raw 不变 ✅；恢复/范围外保守 ✅；双源一致不自动升核定 ✅
  - 导入原始财报核对至少一项确定字段 ✅（万科 2024H1 负债率点位）
  - 相同归档离线可读、零重复抓取 ✅（机制+测试；真实多季累积随前瞻采集进行）
  - STATUS 明确已核定/待查字段与时期 → 见 STATUS.md 更新（已核定：000002/2024-06-30/liabilityToAsset；待查：其余 balance 字段与其余点位——DATA_DECISION 波次 A/B/C 排期不变）
- 版本：v0.8.23。

## 附：监督代理对抗审查（J0，2026-09-27）

结论：**需修复后合入 → 已全部修复并回归**。审查实测求证（只读运行复现），非纸面推演。

| 级别 | 发现 | 处置 |
|---|---|---|
| P1 | 显式 `verification_scope` 声明不回验组件齐备——LLM 自报 scope 可让未结构化/语义相反主张拿到 FACT_CHECKED（数值型与否定型两条绕过均实测复现），N1 修复被声明通道重新打开 | 已修：范围门内回验声明与组件一致（数值型须 value+unit+注册表类型；否定型须 negation_flag=True），不符封顶 EXCERPT_GROUNDED；新增 `test_declared_scope_without_components_capped` 回归 |
| P2 | checkpoint 求值不核对 security_id（异证券披露安排可建立本证券命题） | 已修：过滤加实体一致；新增 `test_checkpoint_of_other_security_does_not_establish` |
| P2 | shadow 消费核对过软：评估 security_id 为空放行任意证券；method_version 只查非空不查匹配（旧方法评估被照常消费） | 已修：空实体→待复核；method_version 须等于当前 ASSERTION_METHOD_VERSION（旧评估随 bump 转待复核——保守方向）；新增 `test_shadow_rejects_empty_security_or_wrong_method_version` |
| P2 | 「旧计划降级」对 R3 旧草稿不生效：旧合成 id `MID:snap` 走到 load 失败→待复核而非 UNESTABLISHED（文档语义漂移；方向保守无 N2 风险） | 已修：含 `:` 或非 `asm_` 前缀按旧格式识别→UNESTABLISHED；同步 decision_policy.HorizonPlan.assessment_id 描述；新增 `test_shadow_legacy_format_assessment_id_degrades` |
| P2 | `test_n2_shadow_consumes_same_assessment_not_rejudge` 时钟敏感（固定 as_of 在当日 16:00 前运行必挂） | 已修：该测试改用当下时点 |
| P2 | assessment_id payload 未含 next_checks/extra 字段（潜伏：加字段后同 id 不同内容静默保留首写） | 已修：payload 补 next_checks + docstring 明示「新增消费字段须同步 payload」 |
| P2 | checkpoints 传生成器时被键哈希消费后评估侧丢失 | 已修：run 键构建前 `list()` 定形 |
| 观察 | 生产 `research` 命令未传 assessment_store/factor_bindings——因子全落「未绑定合格快照」缺口、草稿评估未持久化（影子按待复核消费） | **非缺陷**：与 J0「接线前语义门」定位一致，属 J3 接线范围；已在 CLI 实测确认话术如实（缺口 6 项含 3 条未绑定明示） |

审查确认无问题项：N2 反证路径修死（used 集每周期独立）、缓存键无遗漏、冻结语料 15 例真实不变、评估存储幂等/注入防御实测安全、全角字符无语法风险、告警三处同步到位、测试 HOME 隔离完备。

修复后复跑：目标套件 94 passed；全量 **1266 passed / 2 skipped / 1 deselected**（+4 条审查修复回归）。
