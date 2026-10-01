# 第五轮 L 系列实施方报告（致架构师）

2026-10-01｜实施 Agent：Claude Code｜受审基线 `7dbe57d`（REVIEW_INDEX 39/39 指纹核对一致）
本会话 HEAD `0fd5612`，逐卡 commit：**L0 `c89c5ef` → L1 `0b84b0d` → L2 `c9c4d34` → L3 `ccecec2`**（+ docs `61d8bca`/`0fd5612`）。全部已推送。

## 一、交付总览

按 [R11](R11_ACCEPTANCE.md) 四类新缺口与 [DELIVERY_PLAN](DELIVERY_PLAN.md) L0–L3 执行：每反例先写独立测试红灯（TypeError/缺实现起步）→ 修转绿 → 同类点扫描 → code-quality-guard 监督审查 → P1/P2 处置后提交。**全量测试 1398 passed, 2 skipped**（基线 1360 + 本轮新增 38 例）。逐卡账本见 [EXECUTION_RECORD](EXECUTION_RECORD.md)。

| 卡 | 反例 | 探针结果（未改写） | 结论 |
|---|---|---|---|
| L0 观察合同 | V1 缺行情时点计有效 / V2 目标变化被去重吞 | V1/V2 `observed_defect=false` | 实施完成，待复验 |
| L1 账户读模型 | V3 过期比例进策略/组合 | V3/V3b `observed_defect=false` | 实施完成，待复验 |
| L2 公开检查点 | V4 CLI 无法绑定证据 | V4 固定历史命令形态仍 true（见披露4；通道由 L2 端到端验证） | 实施完成，待复验 |
| L3 原件证据重建 | verify_and_register 旧登记语义 | 离线复验 **6/6 PASS** | 实施完成，待复验 |

## 二、逐卡实施摘要与验收对照

### L0｜观察协议真正按合同消费（shadow_v6→shadow_v7）

- **统一资格函数** `_binding_eligibility`：accepted_ref/assessment/snapshot/account_version/policy_version/method/规则版本/证据截止/行情时点/两臂完整——缺任一或时点在未来 → 降级 diagnostic，机器可读 drop_reasons；绑定构造、记录判定、报告分母共用同一判据（V1 根因：eligible 只查四项子集）。
- **字段真实来源**：quote_cutoff ← StockData.quote_as_of（akshare 实时价=抓取时刻/无实时价=最近 K 线日期；l/la/chat 三调用点接线）；evidence_cutoff ← 被消费评估的 evaluated_as_of（不再用捕获墙钟冒充）；decision_rule_version ← `policy_id@DECISION_TABLE_VERSION`（decision_policy 新增表版本常量）。
- **V2**：输出指纹重写为完整语义投影（model_dump 去非语义字段）——两臂目标/阻塞/时点/版本变化全留痕；仅捕获时点变化可折叠；WRITE 成功才回执 saved。
- **两臂**：binding.arms={legacy,fusion}×{action,target,target_state(KNOWN/UNKNOWN/NOT_APPLICABLE),blockers,execution}——补 legacy 目标权重与分别执行约束。
- **版本升级**：shadow_v7 独立新协议，旧 v6/v5 只诊断不追认；报告 v7_*/protocol_version 当期分母、v6_* 按记录原样仅供过渡、legacy_records 单列；UI 缺口人话映射扩充。
- **测试**：test_l0_shadow_contract.py 10 例 + 既有 K1/J4/K2a 测试按新合同更新（同等严格度：补 policy_version+行情时点种子、账本隔离 fixture）。

### L1｜数量账户的读取资格贯穿消费者

- **类型系统**：StrategyState.current_position_ratio / StrategyDecision.position_ratio / DecisionResult.position_ratio → Optional[float]（None=有仓但权重未知）。不用 0 假装未知、不拿过期比例冒充；**RATIO_ONLY float 路径逐分支零变化**（guard 核对；回测 DataFeeder 路径核实不产生 None）。
- **读取适配**：to_strategy_state 估值参数——价格×NAV **同日窗保守相等**才锁定权重=数量×价格/NAV（DESIGN_VALIDATION：不自行放宽容差），否则 None；派生只读不回写账。strategy_state_for 装配便捷入口（行情时点+账户 NAV 自动接入）；get_total_position_ratio 含未知权重 → None。
- **策略层 None 合同**：未知权重不 flat、BUY 阻精确新增→HOLD_POSITION（持仓事实保留）、REDUCE 意图保留但目标 None（不拿过期比例×keep 伪造）、止损/退出/硬风险路径不变。
- **消费者贯穿**：l/la/chat/scanner/TUI/Web 六入口 has_position 数量感知+strategy_state_for 接线；**无持仓兜底 0.0**（guard P1-1：None 只允许来自「有仓未知」——防非持仓建仓建议被吞）；orchestrator AI/事件仓位上限 None 守卫×4；观察池/播报数量感知；record_proposal 重建仓不漏建议；pos confirm 缺省路径遇权重待重估建议拒绝并要求 --qty/--ratio（防 None 目标被当全比例卖出）；today 权重待重估显示+不用过期比例做超限误报。
- **测试**：test_l1_account_reading.py 11 例（含竖向 REPL：pos confirm --qty → 重启 → 读取 → today）。

### L2｜检查点从公开入口可建立、可复核

- **稳定证据ID**：主张文件 claim_id 显式给则尊重、缺省按 sha256(主体|主张|来源URI) 确定性生成——同命令重放不换引用；批内 ID 重复显式拒绝。
- **--ref 绑定通道**：可重复参数、需与 --checkpoint 成对；CheckpointCondition.evidence_refs **只收显式 --ref——不把导入的全部 claims 自动绑定到任意条件**。
- **入口校验**：伪引用拒绝并列可用ID；错主体拒绝；--ref 无 --claims 显式拒绝；未给引用→列可选证据与缺口；批内未核验（缺原件）→「补原文后重跑」明确下一步；未确认风险不建立。
- **全链验收（真实 parse_input→run_cli，不直调服务/stub）**：research --ref → 检查点命题 TRUE → plan2 accept --primary → 隔离 l（行情替身=数据隔离；研究/接受/捕获全真）→ 影子 v7_mid_effective=1；**命题真值/计划状态/报告分母单独断言**（不用 observation_kind 掩盖 UNESTABLISHED）；覆盖缺引用/伪引用/错主体/缺原件/未确认风险/重启/变更新候选未接受（双槽）。
- **测试**：test_l2_public_checkpoint.py 9 例。帮助/使用手册同步（start.py 两处 + 使用手册段落）。

### L3｜原件证据交付可重建

- **登记语义修复**：供应商 **baostock_raw/baostock_mapped 分列**（旧脚本把映射值写进 raw 键——R11 点名缺陷）；correction_search_status=not_performed 如实明示（替换固定「首发版」注记）。
- **可注入重放**：--store 临时研究库 + --supplier-cache 离线缓存；旧记录**漂移检测**四态（REGISTERED/IDEMPOTENT/EXISTING_DIVERGENT/SKIPPED）——store 幂等不覆盖，语义漂移显式报告交人工核对。
- **可携带清单** evidence_manifest.json（k3_evidence_manifest_v1）：六点位 URL/hash/证券/期间/字段/单位/合并范围/页/行/原值/公式/版本/更正检索状态；J1 批次缺 URL 如实标注 not_recorded 不编造；供应商值 recorded_only；**原件核定与 PIT 可得性分别出状态**（pit_availability=not_assessed）。
- **离线复验入口** verify_manifest_offline.py：复用同一提取链路 extract_one——六态 FAIL 不伪补；**真实交付清单 6/6 PASS**。
- **交付通道**（guard P1-1）：脚本/清单从 gitignored tests/artifacts 迁至**随交付入库**的 tests/evidence/k3_original_pilot/——PDF 单独存储留 artifacts（卡面允许），获取方式=清单 source_url+download_reports.py；tests/README.md 定位同步。
- **测试**：test_l3_evidence_pack.py 8 例（零触网——合成缓存覆盖全部 key；交付清单本体纳入回归防篡改）。

## 三、独立验证记录（可原样复跑）

```powershell
.\.venv\Scripts\python.exe -B plan\fusion\iteration5\acceptance_probes.py   # V1/V2/V3/V3b false；V4 见披露4；exit=0 无 probe_error
.\.venv\Scripts\python.exe -m pytest -q                                     # 1398 passed, 2 skipped
.\.venv\Scripts\python.exe -m pytest tests\core\test_l0_shadow_contract.py tests\core\test_l1_account_reading.py tests\core\test_l2_public_checkpoint.py tests\core\test_l3_evidence_pack.py -q   # 38 例
.\.venv\Scripts\python.exe tests\evidence\k3_original_pilot\verify_manifest_offline.py   # 6/6 PASS, exit=0
```

工作树边界：portfolio.yaml 与 knowledge/index 为用户本地状态未提交；本轮零修改探针/零触碰真实账户/零付费 AI 调用。每卡监督审查（code-quality-guard）记录：L0 抓幽灵键变异+K1/J4 隔离缺失；L1 抓四装配点 None 误伤建仓（实测复现 cli OPEN vs chat HOLD 分叉）+EXIT 分支崩溃；L2 抓 --ref 无 --claims 入口缺口+缺原件分支假覆盖；L3 抓交付通道 gitignore 吞没+测试真实触网。**P1×4/P2×5 全部处置后才提交**；教训落库 .learnings（LRN-20261001-L009 幽灵键变异、L010 哨兵 None 来源纪律）。

## 四、如实披露（复验重点输入）

1. **shadow_v7 起有效样本=0**：真实账本既有 v6 记录只诊断不追认；live 路径现已真实提供 quote_as_of，但有效观察需用户已接受计划+评估可解析+账户版本齐——**本轮没有产生也没有承诺任何有效观察样本**，效果证据为零。
2. **行级口径变更**：observation_kind=effective 与 effective_observations 现走统一资格结果（任一周期合格）——比 v6 的「revision>0+评估引用+账户版本」更严格；旧行级 effective 标签不再计入当期分母。
3. **claim_id 方案**：新生成 ID 为内容确定性 hash；此前经 research 链产生的随机 uuid 引用只存在于旧草稿/评估中——重跑 research 会以新 ID 产生 candidate（双槽语义承接，已接受版本不受覆盖）；旧评估引用关系不受影响。
4. **探针 V4 的验收口径**：acceptance_probes 的 V4 命令固定无 --ref（写于通道存在之前）——修复后该场景按合同仍 UNKNOWN（未给引用不自动绑定），故 observed_defect 保持 true 属**合同预期行为**；「通道存在且可建立」由 L2 端到端 9 例独立验证。架构师若认为探针需增补带 --ref 的正例形态，属改写探针——请明示授权，实施方未动。
5. **K2a 旧测试**（直调服务形态，R11 批评点）：保留作服务层回归；公开入口验收由 L2 全链测试承接。
6. **产品版本号**：start.py / cli --version / AGENTS.md 三处第五轮全程未 bump（K 系列基线 v0.8.24 延续）——收口时统一处理（见裁决6）。
7. **性能 P3 留账**：account_nav() 每次调用重放账本（la 批量 N 持仓×重放为乘法开销）——正确性无损，按 T05 纪律先量基线再优化。
8. **测试规模**：L2 端到端单文件 ~40s、L3 含 223 页 PDF 复提取 ~40s——全量 1398 例约 2.5 分钟。

## 五、裁决请求

1. **K1 冻结**：L0 已按卡面交付（统一资格函数/字段来源/两臂/去重/版本协议）——是否达到冻结「记录合同」门槛请复验后裁决。冻结记录合同≠策略发布。
2. **K2b 放行**：L0/L1/L2 三卡交付齐——生产归并 `l` 是否放行请裁决（放行前 K2b 细化仍按卡面待实施）。
3. **披露4 口径确认**：探针 V4 保持 true 的处理是否符合验收意图；如需带 --ref 正例探针请授权改写。
4. **policy_version 资格门**：当前只验存在不验现行（旧版本盖章的历史接受计划仍可合格，无既有可用路径被误伤）——是否需要现行值比对及其现行值定义。
5. **真实库旧记录处置**：L3 新语义重放会对真实库 5 条旧登记显式报 EXISTING_DIVERGENT——是否清理重登（参照 LRN-L008）由您/用户裁决；真实 shadow 账本 v6 记录仅诊断不追认（已按此实现）。
6. **版本号收口**：本轮产品版本是否 bump 至 v0.8.25 及时机（三处同步铁律）。
7. **波次B**（currentRatio/quickRatio/assetToEquity 逐字段核对）：框架已备（清单 schema/复验入口可扩展），按 G4/G5 消费方确定优先级后细化实施。
8. **STATUS/MILESTONES 更新**：按「架构师审批后同步」规则，G6/G1/G2/G3/G4/T01/T02/T03/T07 的状态行待您复验后更新；实施方仅在 STATUS 标注了「交付待复验」。

## 六、范围边界重申

非实盘下单；fusion effective 保持 capture_only（本轮未改任何 effective 解析语义）；未接自动实盘；未授权不调用付费 AI；E1b/E5b 保持 NOT_RUN、E2b–E4b 保持 BLOCKED_DATA；不预编造后续轮次方案。
