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
