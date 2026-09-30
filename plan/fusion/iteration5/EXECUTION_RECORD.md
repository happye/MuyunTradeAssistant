# 第五轮 L 系列执行账本（实施方 Claude Code）

> 接手基线：`42ecd2f`（= `7dbe57d` 受审指纹 + 两笔 docs 提交；39/39 源码指纹匹配
> iteration5/REVIEW_INDEX.json）。架构师=Codex（不提交 Git，新交付由实施方代提交）。
> 纪律：每个反例先写独立测试红灯 → 修转绿 → 扫同类点 → 落 `.learnings/`；每卡 commit
> 前起 code-quality-guard 监督审查（用户指示），P1/P2 处置后才提交；告警三处同步；
> 隔离断言全路径；探针期望不得改写；`portfolio.yaml` 永不提交。

## 执行顺序（DELIVERY_PLAN §执行原则与顺序）

L0 观察合同 → L1 账户读模型 → L2 公开检查点 → L3 原件证据重建。
L0 与 L1 业务模块可分开处理，共享 CLI/计划接线串行整合。

## L0｜观察协议真正按合同消费（G6，T03）

**状态：实施完成，待架构师复验（2026-10-01）**

### 反例与红灯

| 反例 | 内容 | 红灯 | 状态 |
|---|---|---|---|
| V1 | 缺行情时点仍进有效分母（quote_cutoff=None 而 eligible=true） | tests/core/test_l0_shadow_contract.py 10 例全红（TypeError 起步：资格函数/参数/字段均未实现） | ✅ 转绿 |
| V2 | 权重变化被去重吞掉（_output_fingerprint 不消费 binding 语义） | 同上 test_v2_* 两例 | ✅ 转绿 |

### 实施（commit 后补哈希）

| # | 内容 | 文件 |
|---|---|---|
| L0-T1 | 真实规则版本常量 DECISION_TABLE_VERSION="v1"（行语义变更必须 bump；binding 的 decision_rule_version=policy_id@表版本，不再复制 policy_id） | src/core/decision_policy.py |
| L0-T2 | StockData.quote_as_of 行情时点字段（实时价=抓取时刻；无实时价=最近K线日期）+ 三处构造填充 | src/data/models.py、src/data/akshare_client.py |
| L0-T3 | shadow_v7 合同：统一资格函数 `_binding_eligibility`（accepted_ref/assessment/snapshot/account_version/policy_version/method/规则版本/证据截止/行情时点/两臂完整；时点不可解析或在未来→降级）+ `_target_state`（KNOWN/UNKNOWN/NOT_APPLICABLE）；绑定自含 account_version | src/core/shadow_diff.py |
| L0-T4 | 字段真实来源：quote_cutoff←调用方注入 StockData.quote_as_of（不可得如实 None+阻塞资格）；evidence_cutoff←被消费评估 evaluated_as_of（不再用捕获墙钟冒充） | src/core/shadow_diff.py |
| L0-T5 | 两臂完整输出：binding.arms={legacy,fusion}×{action,target,target_state,blockers,execution}——补 legacy 目标权重与分别执行约束/阻塞 | src/core/shadow_diff.py |
| L0-T6 | 输出指纹重写：完整语义投影（model_dump 去非语义字段）——目标/阻塞/时点/版本变化全留痕，仅捕获时点变化可折叠，含 derivation_version 跨协议不互判重复；WRITE 成功才回执 saved | src/core/shadow_diff.py |
| L0-T7 | 报告协议分列：v7_*/protocol_version（当期分母）、legacy_records（旧记录只诊断）、v6_*（按记录原样仅供过渡观察）、effective_observations 只数当期协议；UI 缺口人话映射扩充 | src/core/shadow_diff.py |
| L0-T8 | 调用点接线：l/la/chat 传 quote_as_of | src/cli/main.py、src/chat/tools.py |
| L0-T9 | 旧预期按新合同更新（同等严格度：K1 四例/J4 两例/K2a 一例加 policy_version+行情时点种子；K1/J4 补账本隔离 fixture） | tests/core/test_k1_shadow_v6.py、test_j4_mode_observation.py、test_k2a_research_loop.py |

### 验证

- 红灯→绿灯：test_l0_shadow_contract.py 10/10 passed
- **探针（未改写）**：`.\.venv\Scripts\python.exe -B plan/fusion/iteration5/acceptance_probes.py`
  → **V1/V2 observed_defect=false**（quote_cutoff=null、eligible=false、drop_reasons=[no_policy_version_mid, no_quote_cutoff_mid]、mid_count=0、evidence_cutoff_is_capture_time=false；V2 fingerprints_equal=false、second_saved=true）；**V3/V3b/V4 observed_defect=true 保持 OPEN（L1/L2 范围，未越界修复）**；exit=0 无 probe_error
- 目标测试：shadow 相关 6 文件 67 passed
- **全量**：pytest -q → **1370 passed, 2 skipped, 1 deselected**（60s）
- 同类点扫描：shadow_v6 硬编码残留（仅注释与报告 v6 历史计数，符合设计）；decision_rule_version/quote_cutoff/mid_effective 消费点全部收敛于 shadow_diff；被删散键 target_weight/blockers 无遗漏消费方（全库 grep）
- code-quality-guard 监督审查（用户指示）：**无 P1，可合入**；P2×2 已处置——
  P2-1 V2 测试改变异幽灵散键 target_weight → 改真实字段 arms.fusion.target（防指纹排除项精确回归时测试网失效）；
  P2-2 test_k1/test_j4 缺账本隔离 fixture（真实账本存在的机器上 account_version="" 被回填→假失败）→ 补 autouse fixture；
  P3×2 顺手修（_cutoff_in_future docstring 失真、_capture 死参数），P3×1 留架构师（见未完成项）

### 未完成项 / 留架构师裁决

1. **P3（guard 提出，属架构师裁决）**：policy_version 资格门只验存在不验现行——旧 RESEARCH_SERVICE_VERSION 盖章的历史接受计划仍可合格。已核实生产无既有可用路径被误伤（系统草稿盖章；plan2 手写路径本就无 assessment_id 在 v6 下也不合格）。是否需要现行值比对（及其现行值定义）请架构师裁决。
2. V3（数量投影进决策读取）→ L1；V4（公开 checkpoint 绑定证据）→ L2。探针保持红灯。
3. 旧行级 observation_kind 语义变更：行级 effective 现在走统一资格结果（任一周期合格）——与 v6 的「revision>0+评估引用+账户版本」口径相比更严格（新增行情/策略版本/两臂门槛），真实已产生记录均为 v6 只诊断，无追认问题。
4. 真 机 shadow 账本（~/.muyun/shadow_diff.jsonl）存在 v6 历史记录：新协议从 v7 起算，不追认（报告已分列显示）。

## L1｜数量账户的读取资格贯穿消费者（G1/G5，T01）

**状态：实施完成，待架构师复验（2026-10-01）**

### 反例与红灯

| 反例 | 内容 | 红灯 | 状态 |
|---|---|---|---|
| V3 | 数量投影标记（ratio_stale/quantity_fact）无实际消费者——过期比例/旧比例0继续进策略与组合 | tests/core/test_l1_account_reading.py 10红+2既有守卫（TypeError 起步） | ✅ 转绿 |

### 实施

| # | 内容 | 文件 |
|---|---|---|
| L1-T1 | 权重未知进类型系统：StrategyState.current_position_ratio / StrategyDecision.position_ratio / DecisionResult.position_ratio → Optional[float]（None=有仓但权重未知；RATIO_ONLY float 路径零变化——guard 逐分支核对） | src/data/models.py |
| L1-T2 | 读取适配：PositionRecord.quantity_held/weight_unknown；to_strategy_state 估值参数（价格×NAV 同日窗保守相等才锁定权重=数量×价格/NAV，否则 None——不自行放宽容差；派生只读不回写账）；strategy_state_for 装配便捷入口（行情时点+账户NAV自动接入）；account_nav()（惰性读账本快照）；get_total_position_ratio→Optional（含未知权重→None） | src/data/portfolio.py |
| L1-T3 | 策略层 None 合同：未知权重→不 flat、BUY 阻精确新增→HOLD_POSITION（持仓事实保留）、REDUCE 意图保留但目标 None（不拿过期比例×keep 伪造）、退出/硬风险路径不变；_infer_sell_path 不推 flat_sell；EXIT 分支 None 守卫（guard P1-2） | src/core/strategy_layer.py、src/core/decision_engine.py |
| L1-T4 | 消费者贯穿：orchestrator AI/事件仓位上限 None 守卫×4+默认状态构造（None→HOLD）；l/la/chat/scanner/TUI/Web 六入口 has_position 数量感知+strategy_state_for 接线（无持仓兜底 0.0——guard P1-1：None 只允许来自「有仓未知」，防误伤建仓）；观察池/播报 has_pos 数量感知 | src/core/orchestrator.py、src/cli/main.py、src/chat/tools.py、src/scanner/scanner_engine.py、src/tui/app.py、src/web/app.py |
| L1-T5 | 建议与 today：record_proposal 非持仓判定数量感知（重建仓不漏建议）+None 安全目标；pos confirm 缺省路径遇权重待重估建议拒绝并要求 --qty/--ratio（guard P2-3：防 None 目标被当全比例卖出）；pos add 80% 底牌警告 None 如实显示（guard P2-4）；today 权重待重估显示+不用过期比例做超限误报 | src/data/portfolio.py、src/cli/main.py、src/cli/today_service.py |

### 验证

- 红灯→绿灯：test_l1_account_reading.py 11/11 passed
- 探针（未改写）：V3/V3b observed_defect=false（strategy_ratio=None、account_total_ratio=None——过期比例不再进策略/组合）；V1/V2 保持绿；V4 仍 OPEN（L2 范围）
- 全量：pytest -q → 1381 passed, 2 skipped（含新增 11 例）
- 同类点扫描：全库 grep `current_ratio > 0` 有仓判定→六入口+观察池+播报全部数量感知；`position_ratio > 0` 算术比较全部 None 安全；回测路径核实不产生 None（DataFeeder float 路径）
- code-quality-guard 监督审查：P1×2 已修复——P1-1 四装配点「无持仓」兜底误传 None（orchestrator 误读为有仓→非持仓建仓建议被吞，实测复现 cli OPEN vs chat HOLD 分叉）→ 兜底改回 0.0+补对照回归；P1-2 EXIT 分支 None<=0 TypeError → 守卫。P2×2 已修复（pos confirm 缺省路径拒 None 目标伪造全卖；80% 警告 None 如实显示）。P3×3 顺手修（orchestrator 长行/tui 死变量/docstring 措辞）。LRN-20261001-L010 落库

### 未完成项 / 留后续

1. P3（guard 提出，性能）：account_nav() 每调用重放账本——la 批量 N 持仓×全量重放为乘法开销。正确性无损；按 T05 纪律先量基线再优化（分析周期内快照复用为候选方案）。
2. 投影失败重启的 D3 恢复路径已被 K0a 用例覆盖（崩溃恢复补做）；本轮竖向用例覆盖「确认→重启→读取→today」主链。
3. V4 → L2。

## L2｜检查点从公开入口可建立、可复核（G2/G3，T02）

**状态：实施完成，待架构师复验（2026-10-01）**

### 反例与红灯

| 反例 | 内容 | 红灯 | 状态 |
|---|---|---|---|
| V4 | CLI 检查点 evidence_refs 固定 []——公开入口无法绑定证据，checkpoint 命题 UNKNOWN | tests/core/test_l2_public_checkpoint.py 8 红（稳定ID/绑定通道/全链均未实现） | ✅ 转绿 |

### 实施

| # | 内容 | 文件 |
|---|---|---|
| L2-T1 | 稳定证据ID：主张文件 claim_id 显式给则尊重、缺省按 sha256(主体\|主张\|来源URI)[:12] 确定性生成（同命令重放不换引用）；批内 ID 重复显式拒绝（guard P3） | src/cli/main.py |
| L2-T2 | --ref 绑定通道：可重复参数（consumed 索引解析——值恰似代码不误吞）；需与 --checkpoint 成对；CheckpointCondition.evidence_refs 只收显式 --ref（**不自动绑定任何主张**——DELIVERY_PLAN 红线） | src/cli/main.py |
| L2-T3 | 入口预校验：伪引用（不在本批）拒绝并列可用ID；错主体拒绝；--ref 无 --claims 显式拒绝（guard P2-1：防跑后误导诊断） | src/cli/main.py |
| L2-T4 | 跑后引导：未给引用→列可选证据ID与缺口；批内未核验→「补原文后重跑」明确下一步；渲染层显示已核验证据ID；--json 经 verified_claim_ids 暴露（提示不污染 JSON stdout） | src/cli/main.py |
| L2-T5 | 帮助/使用文档：start.py 两处 research 帮助 + 使用手册 --claims/--checkpoint/--ref 段落 | start.py、使用手册.md |

### 验证

- 红灯→绿灯：test_l2_public_checkpoint.py 9/9 passed——真实 parse_input→run_cli 全链：
  research --ref → 检查点命题 TRUE → plan2 accept --primary → 隔离 l（行情替身=数据隔离，
  研究/接受/捕获全真）→ 影子 v7_mid_effective=1；命题真值/计划状态/报告分母**单独断言**
- 覆盖面：正例全链＋缺引用（列可选证据）＋伪引用拒绝＋错主体拒绝＋缺原件（批内未核验→
  明确下一步）＋未确认风险（保持 UNKNOWN）＋重启恢复＋变更新候选未接受（双槽）
- 全量：pytest -q → 1390 passed, 2 skipped
- 同类点扫描：CheckpointCondition 生产构造点全库唯一（CLI 已修）；claim_id 缺省 uuid 生成
  仅剩程序化路径（非用户绑定面）
- 探针：V4 场景命令为固定无 --ref 的历史形态——修复后该场景按合同仍 UNKNOWN（未给引用
  不自动绑定），probe 无 probe_error（exit=0）；V1/V2/V3/V3b 保持 ok
- code-quality-guard 监督审查：P2×2 已修复（--ref 无 --claims 入口缺口；缺原件+--ref 分支
  补真实批内 ID 断言——原 or 断言掩盖实际分支）；P3×2 已修复（批内 ID 重复检测；手册版本
  标注去除——产品版本号未 bump，留待收口统一）

### 未完成项 / 留后续

1. 产品版本号（start.py/cli --version/AGENTS.md 三处）第五轮全程未 bump——收口时按架构师裁决统一处理。
2. K2a 旧测试 test_k2a_full_loop_fixture_to_active_ref 仍为直调服务形态（R11 批评点）——本卡以 test_l2_public_checkpoint.py 全链测试补齐真实入口验收；旧测试保留作服务层回归。
3. L3（原件证据交付可重建）待实施。

## L3｜原件证据交付可重建（G4/G6，T07）

**状态：实施完成，待架构师复验（2026-10-01）**

### 红灯

tests/core/test_l3_evidence_pack.py 4红+3error 起步（raw/mapped 未分列、漂移检测/清单/离线复验入口不存在）→ 8/8 绿。

### 实施

| # | 内容 | 文件 |
|---|---|---|
| L3-T1 | 登记语义修复：供应商 **raw/mapped 分列**（旧脚本把映射值写进 baostock_raw 键——R11/L3 点名缺陷，勘误 88ddbc6 披露过）；更正检索状态 correction_search_status=not_performed 如实明示（替换固定「首发版」注记） | tests/evidence/k3_original_pilot/verify_and_register.py |
| L3-T2 | 可注入重放：--store 临时研究库（缺省真实库=运维用法）；--supplier-cache 供应商缓存（离线重放零触网）；旧记录**漂移检测**四态 REGISTERED/IDEMPOTENT/EXISTING_DIVERGENT/SKIPPED——已有记录 vs 本轮 entry 逐字段比较，store 幂等不覆盖（登记语义漂移交人工核对，参照 L008） | 同上 |
| L3-T3 | 可携带清单：k3_evidence_manifest_v1 六点位（URL/hash/证券/期间/字段/单位/合并范围/页/行/原值/公式/版本/更正检索状态）；000002_2024Q2 的 source_url=null 如实标注 not_recorded（J1 批次未记录，不编造）；供应商值 recorded_only；**原件数值核定与 PIT 可得性分别出状态**（pit_availability=not_assessed——核定不自动取得历史 PIT 资格） | tests/evidence/k3_original_pilot/build_evidence_manifest.py + evidence_manifest.json |
| L3-T4 | 离线复验入口：复用 extract_reports.extract_one 同一提取链路——MISSING/HASH_MISMATCH/EXTRACT_FAILED/SELF_CHECK_FAILED/VALUE_MISMATCH/PASS 六态，失败不伪补；**真实交付清单 6/6 PASS** | tests/evidence/k3_original_pilot/verify_manifest_offline.py + offline_verification_results.json |
| L3-T5 | 交付通道（guard P1-1）：脚本/清单从 gitignored tests/artifacts 迁至**随交付入库**的 tests/evidence/k3_original_pilot/——PDF 单独存储留 artifacts（卡面「PDF 可单独存储」），获取方式=清单 source_url+download_reports.py；tests/README.md 目录定位同步 | 目录迁移 |

### 验证

- 红灯→绿灯：test_l3_evidence_pack.py 8/8 passed（含交付清单本体回归——手工篡改会被拦）
- 真实离线复验：6/6 PASS exit=0（PDF hash/重提取/算式复算全一致）
- 全量：pytest -q → 1398 passed, 2 skipped（零网络纪律恢复——guard P1-2 修复后测试不再触 baostock）
- code-quality-guard 监督审查：P1×2 已修复（交付通道 gitignore 吞没→迁 tests/evidence；测试真实触网→全 key 合成缓存+诚实 docstring）；P2×1 已修复（交付清单本体纳入回归）；P3×5 已修复（PASS 分支复核自洽、坏文件防护、防御取值、产物相对路径、raw_note 分点措辞）

### 未完成项 / 留架构师

1. 波次B（currentRatio/quickRatio/assetToEquity 逐字段核对）：按 DELIVERY_PLAN 由架构师依 G4/G5 消费方确定优先级后细化，本轮只交框架（清单 schema/复验入口已可扩展）。
2. 真实库旧记录（旧键语义）重登：漂移检测会显式报 EXISTING_DIVERGENT——是否清理重登由用户/架构师裁决（参照 L008）。
3. 映射 v2 未申请未实施（须限定已核定范围、追加版本、验证 v1 可重放——前置未到）。
