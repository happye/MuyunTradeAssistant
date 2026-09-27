# R10 独立验收：J0–J5 交付与观察资格

2026-09-27｜架构师独立复验｜基线 `def45d1` / v0.8.24｜只读生产代码与隔离临时目录；未改产品、测试、配置、真实账户，未提交 Git。

> **同日深审更新**：[DEEP_ARCHITECTURE_REVIEW](DEEP_ARCHITECTURE_REVIEW.md) 新增 D1–D7 / 10 个反例，目标测试扩大为180绿。下表 J2 的 PASS 只指当时目标回归；当前 J2 场景资格改为 PARTIAL，末尾“账户确认可继续使用”的笼统许可撤回。J0事实语义门也重新打开。本文保留初验过程与 A1–A4；最新裁决以深审和 STATUS 为准。

## 验证范围与结论

命令：`.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/core/test_j0_facts_assessment.py tests/core/test_j1_mapping_boundary.py tests/core/test_j2_account_service.py tests/core/test_j3_research_application.py tests/core/test_j4_mode_observation.py` → **78 passed in 12.16s**。另用 `TemporaryDirectory` 四组独立反例实跑，均无真实 HOME/持仓写入。实施方全量 `1297 passed / 2 skipped / 1 deselected` 和 E0b/E7b `exit=0` 是已提交证据，本次未独立重跑全量或付费/外源场景。

**裁决：J0–J5 的实现交付可接收，发布/观察资格不能整批关账。** N1–N5 原反例目标测试仍通过；J0 的评估快照核对、J3 的已接受计划生命周期、J4 的观察去重出现新反例。J3/J4 场景验收记 **PARTIAL**，有效观察协议暂不冻结，`opt_in/default` 继续按 `capture_only`。不把原反例关闭改写成“语义全面安全”。

| 卡 | 实现 | 生产接线 | 场景复验 | 实证/发布边界 |
|---|---|---|---|---|
| J0 | PASS | PARTIAL | PARTIAL：A2 | 自由文本/引用原门保留；快照缺失评估可被消费，须补门 |
| J1 | PASS | PASS（research 路） | PASS（本轮目标回归） | 原件核定仍仅 1/6 点位，余量 NOT_RUN |
| J2 | PASS | PASS（CLI/today） | PASS（本轮目标回归；实施方 e2e 另有证据） | 仅单用户本地账本；本轮未跑可能写真实持仓的 e2e |
| J3 | PASS | PARTIAL（`l` 未归并） | **FAIL：A1** | 重跑可撤销用户已接受计划，先修再观察 |
| J4 | PASS | PASS（l/la/chat 捕获） | **FAIL：A3/A4** | 观察丢失且同输入定义不全，不冻结/不晋级 |
| J5 | PASS（有限收口） | PARTIAL | E0b/E7b 依实施方证据 | E1b/E5b NOT_RUN、E2b–E4b BLOCKED_DATA、E6b NOT_RUN |

## 新反例（均在临时目录复现）

### A1｜同输入重跑 research 撤销已接受计划（P1，J3）

`ResearchApplicationService.run` 每次都调用 `_save_draft`；`src/core/research_application.py` 的已有计划分支无内容相等判断，强制 `accepted_at=None`，`HorizonPlanStore.save` 再加 revision。固定 `as_of`、零归档和同一个 run_id：首跑 MID rev1 → `accept` 后 active=True → 原输入重跑仍同 run_id，MID rev3 且 active=False。旧接受引用仍在 `accepted_refs`，但当前计划被覆盖，真实影子计划失活。这不是“新事实要求重新确认”，因为输入未变。缺少重复研究幂等测试。

**修复合同**：相同研究内容重跑不写计划、不增 revision、不改变 accepted 状态；新材料生成候选草稿时保留当前已接受版本与用户主意图引用，待用户显式接受新版本后原子切换。评估变旧/冲突时可标 REVIEW_REQUIRED，不能悄悄把已接受计划改成普通草稿。覆盖同输入、材料变化、接受前后、重启和影子消费。

### A2｜计划快照有值、评估快照为空仍消费 VALID（P1，J0/J4）

`src/core/shadow_diff.py` 的 `_load_verified_assessment` 仅在 `plan_snap and asm.snapshot_id` 同时为真时比较；`ThesisAssessment.snapshot_id` 默认为空。临时 AssessmentStore 保存同证券/MID/当前方法版本、`snapshot_id=""` 的 VALID 评估；计划 `snapshot_id="snap-expected"` 引用它。实跑 `loaded_as_verified=True`、`_thesis_status_for(...)=VALID`。这突破“评估与计划绑定同一快照”的消费资格，并可进入 effective 计数。

**修复合同**：新协议要求两侧非空且完全相等；旧记录缺快照按待复核/diagnostic，不能用空值作通配。新增正反例同时测 thesis 状态和 observation_kind；版本变更保留旧记录，不追认。

### A3/A4｜影子去重丢有效变更（P1，J4）

`src/core/shadow_diff.py` `_append_record` 先按 `(security_id, as_of[:16])` 去重，与指纹无关：同一分钟账户/计划版本从 v1 改 v2，第二条 `saved=False`，文件仅 1 行。另 `_input_fingerprint` 仅含证券、两周期计划 content_hash 和账户版本；相隔 2 分钟，终态从 WAIT/EXECUTABLE 改 EXIT/BLOCKED，指纹不变，第二条仍 `saved=False`。现有测试特意跨分钟验证指纹，未覆盖这两个反例。

**修复合同**：去重只折叠同一完整输入与同一输出的重复触发；纳入证据快照/行情截止、评估版本、决策表与规则版本、双方动作/目标/阻塞及执行状态。不同计划、账户或决策结果即使同分钟也必须留痕；同计划账户下价格/证据变化产生新观察。捕获回执须区分已保存与被去重，报告分母仅数已落盘且达到完整资格的记录。补同分钟变更、跨分钟动作变更、真正重复三组回归。

## 验收与发布动作

1. A1/A2/A3/A4 进入第四轮最高优先级修复；修复前不把新影子记录计为晋级所需的合格样本。历史 `shadow_v5` 记录保留诊断，不改写、不按新 schema 追认。
2. 用户目前可继续用 legacy 主分析与账户确认；`research` 仍属实验入口。**已有已接受 plan2 计划时，重复执行 `research` 可能使其失活**，实施方需优先给可见诊断/修复；架构师不改产品代码。
3. 修复后须有独立复验：上述反例先红后绿、受影响消费者目标测试、隔离真实入口场景、再跑全量。e2e 必须在导入/构造前断言所有持久化路径均在临时目录，不能只改 HOME。
