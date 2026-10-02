"""One-shot R14 architecture handoff maintenance, no product/config changes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def edit(name, pairs):
    path = ROOT / name
    text = path.read_text(encoding="utf-8-sig")
    for old, new in pairs:
        assert text.count(old) == 1, (name, old[:100], text.count(old))
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


edit("MILESTONES.md", [
    ("更新：2026-10-01", "更新：2026-10-02"),
    ("当前审批见 [R13](plan/fusion/iteration7/R13_ACCEPTANCE.md)，下一执行窗口见 [第七轮](plan/fusion/iteration7/DELIVERY_PLAN.md)。F/R/J/K/L/M/N", "当前审批见 [R14](plan/fusion/iteration8/R14_ACCEPTANCE.md)，下一执行窗口见 [第八轮](plan/fusion/iteration8/DELIVERY_PLAN.md)。F/R/J/K/L/M/N/O"),
    ("当前状态（2026-10-01）", "当前状态（2026-10-02）"),
    ("本文件、R13", "本文件、R14"),
    ("M0最终三态适配、M1原行日期范围接收；独立381目标绿", "N批独立423目标绿、R13的17反例转绿；M2原型25断言通过"),
    ("W1投影存在/W2旧价重标范围通过，X1账户消费与X3时间边界未闭环→N0/N2", "W1/W2及N批明确范围通过；Y1异常账户、Y2持仓集合、Y3影子时间未闭环→O0/O1"),
    ("N0–N2独立过门并冻结修正合同后M2只读接线", "O0–O2独立过门并冻结修正合同后M2只读接线"),
    ("V1/V2历史范围通过；shadow_v8未冻结，X2规范两臂/版本门未过", "N1规范两臂/生成版本门通过；shadow_v9未冻结，Y3时间/Y4 LONG边界待补"),
    ("N0–N2关闭后复验shadow_v9候选，旧v8及更早不追认", "O0–O2关闭后复验shadow_v10候选，旧v9及更早不追认"),
    ("M0已引入请求快照但消费仍待N0", "N0请求快照已接多入口，Y2批量持仓集合仍待O0"),
    ("L系列R12审批不重复；M系列R13分项接收，W1投影存在/W2原行日期范围通过。N0补账户消费者，N1补规范两臂和版本资格，N2补时间边界；联合独立过门再裁定K1冻结。", "L/M历史审批不重复；N系列R14分项接收，R13反例17/17转绿。O0补异常账户与持仓集合，O1统一影子时间资格，O2补LONG未知权重；联合独立过门再裁定K1冻结。"),
    ("R13接收正常只读范围，损坏库视图与失败场景需补；N0–N2过门且修正合同冻结后实施。", "R14接收M2原型25断言（含损坏库/前后哈希/旧方法/重启）；O0–O2过门且修正合同冻结后实施。"),
    ("M0已引入请求上下文；X1策略/无投影chat/异常账本仍不同源", "N0已接策略/无投影chat；Y1异常判空/Y2批量清单仍待补"),
    ("G1，N0统一实际消费者；兼容旧比例账户，不建平行账本", "G1，O0统一异常分支和持仓集合；兼容旧比例账户，不建平行账本"),
    ("N0–N2联合过门、修正合同冻结后接线；先补X4损坏库原型", "O0–O2联合过门、修正合同冻结后接线；X4原型已通过R14"),
    ("M1旧行情日期修复通过；X2两臂/版本门、X3时点校验待补", "N1两臂/生成版本门、N2估值时间通过；Y3影子时间/Y4 LONG待补"),
    ("G6，N1/N2立即，shadow_v8未冻结", "G6，O1/O2立即，shadow_v9未冻结"),
    ("N0补消费一致性；性能收益另测", "O0补持仓集合一致性；性能收益另测"),
])

with (ROOT / "MILESTONES.md").open("a", encoding="utf-8") as f:
    f.write("\n- 2026-10-02 R14：N批分项接收，423目标绿、17旧反例全绿；5新红断言归为Y1–Y4，M2原型25断言接收。K1未冻结/M2生产未放行；O0–O2承接G1/G2/G4/G6与T01/T03，整体未完成。\n")

(ROOT / "plan/fusion/STATUS.md").write_text('''# 融合项目当前实施与审批状态

**更新：2026-10-02（R14独立审批）｜受审HEAD：2b98842｜v0.8.26｜整体未完成。** 长期目标见根[MILESTONES](../../MILESTONES.md)，恢复见[RESUME](RESUME.md)。

## 当前审批

[N系列交付](iteration7/实施方报告_N系列交付.md)已完成[R14独立审批](iteration8/R14_ACCEPTANCE.md)：**分项接收，整卡补齐；K1不冻结，M2/K2b生产不放行，capture_only不变。** R12/R13已通过范围不重开。

独立30文件**423 passed**；R13合同重放**17/17通过**；新增6条检查**5失败/1通过**，归为Y1–Y4（不是5个P1）；M2原型**25项断言通过**。实施方全量1482 passed/2 skipped/1 deselected仅引用，未独立重跑。实施报告462与本次423须按收集清单核对，不算39个失败。7保护文件哈希/知识库集合不变；[证据索引](iteration8/REVIEW_INDEX.json)。

| 块 | 接收范围 | 当前剩余 | 长期里程碑 |
|---|---|---|---|
| K0a/L1/M0/N0账户 | 策略ctx、单股/聊天无投影退出、部分异常分支 | Y1零投影+读失败/PARTIAL无lot仍判空；Y2 la漏账本独有持仓→O0 | G1/G2/G5/T01 |
| K0b事实/命题 | 既有明确范围通过不变 | LONG规则/数据不完整，不整体背书自然语言 | G1/G3/G4 |
| K0c/L0/N1计划 | 规范两臂/生成版本门/旧桶计数、MID行5/6 | Y4 LONG行8未知权重仍ADD→O2；完整MID正例待G3 | G2/G4/G6/T03 |
| K2a/L2公开研究 | 公开引用/checkpoint TRUE通过 | 完整MID评估正例，不以checkpoint替代VALID | G2/G3/T02 |
| K1/M1/N2观察合同 | 估值严格时间/NAV偏移/finite/原fetched_at | Y3 shadow未来naive仍有效→O1；shadow_v9未冻结 | G1/G6/T03 |
| K2b/M2统一l | 原型损坏库/零写/旧方法/重启，25断言通过 | O0–O2联合独立过门+合同冻结前生产不放行 | G2/G7/T02 |
| K3/L3原件与规则 | 6/6原件离线复验历史范围接收；PARTIAL | J1缺URL、更正/人工/PIT/异机获取、B/C/规则未完成 | G4/G5/T07 |
| K4真实观察 | 未验收合格真实样本 | 修正合同冻结后积累；旧v9及更早不追认 | G6 |
| E1b/E5b | NOT_RUN | 前置不齐且未获付费授权 | G6 |
| E2b–E4b | BLOCKED_DATA | 严格历史信息集/覆盖不足 | G6 |
| E6b及其他效果 | NOT_RUN/待证据 | 账户组合资格/场景/指标冻结后回放 | G5/G6 |
| 性能/跨端/必要重构 | PARTIAL | T01–T07持续维护；B2不是性能瓶颈证明 | G7 |

## 当前执行窗口

[第八轮O0–O2任务卡](iteration8/DELIVERY_PLAN.md)已交待实施职责与验收边界：O0异常账户+持仓集合；O1同源时间资格；O2 LONG未知权重。Claude Code负责产品实施，Codex负责复验。建议v0.8.27/表v4/shadow_v10候选，旧观察原件保留。

追认v0.8.26/表v3/shadow_v9命名，不等于冻结或策略晋级。正常账本缺席的RATIO_ONLY兼容通过；decision_rule_version同表达式不另列阻断，不宣传为通用兼容屏障。预取fetched_at及摘要ctx已实现，不再笼统留后续；具体未覆盖项以Y1–Y4为准。

## 数据与协作边界

- 不自动修真实账户/投影，不覆盖历史观察/核定登记；真实库五条旧登记保留。
- R14产物与入口文档本轮仅落盘未Git提交；此前本地持仓/知识库/资料变更保留，iteration7旧探针仍未跟踪。
- 计算成功、事实核实、命题成立、计划接受、有效观察、投资效果分列。零付费AI，非实盘下单，主策略不晋级。

## 历史与恢复

[F](EXECUTION_RECORD.md)→[R](iteration2/EXECUTION_RECORD.md)→[J](iteration3/EXECUTION_RECORD.md)→[K](iteration4/EXECUTION_RECORD.md)→[L](iteration5/EXECUTION_RECORD.md)→[M](iteration6/EXECUTION_RECORD.md)→[N](iteration7/EXECUTION_RECORD.md)→[R13](iteration7/R13_ACCEPTANCE.md)→[R14](iteration8/R14_ACCEPTANCE.md)。

最新增量基准为[iteration8/REVIEW_INDEX](iteration8/REVIEW_INDEX.json)。无新交付不重复已知红例，不越权代写产品；交付/审批同步MILESTONES、STATUS、RESUME。
''', encoding="utf-8")

(ROOT / "plan/fusion/RESUME.md").write_text('''# 融合项目恢复入口与Codex架构师记忆

**更新2026-10-02｜受审HEAD 2b98842｜最新独立审批R14。** 身份以用户指派为准；Codex角色入口[CODEX.md](../../CODEX.md)，Claude Code保持实施职责。

## 恢复顺序

1. AGENTS.md→CODEX.md→根[MILESTONES](../../MILESTONES.md)：固定R01–R09、G0–G8、T01–T07及结项门槛。
2. [STATUS](STATUS.md)→[R14审批](iteration8/R14_ACCEPTANCE.md)→[第八轮O0–O2任务](iteration8/DELIVERY_PLAN.md)。
3. 比较实际HEAD/工作树与[最新指纹](iteration8/REVIEW_INDEX.json)，找最新实施报告；只复核变化组及消费者。R13历史审批不重复，旧探针保留。
4. 简短说明恢复进度和下一动作后继续；常规隔离验收/文档维护已授权，不再请求确认。

## 最新进度与下一动作

- N批产品154b556，文档5c0f51d，HEAD2b98842；v0.8.26。R14分项接收：30文件423绿；R13的17合同反例全绿。实施方称462，需给文件/用例清单核对；全量1482/2skip/1deselect仅引用未独立重跑。
- 新增6检查5红1绿；另M2原型包装测试25断言全绿。Y1 P1异常账本+零数量投影或无幸存lot仍NONE/0；Y2 P1 la先看投影清单，漏账本独有持仓；Y3 P1影子naive时刻只比日期，同日未来可计有效；Y4 P2 LONG行8已持有但权重未知+预算True仍ADD（公开策略接口边界，不夸大当前影子生产可达性）。
- 追认v0.8.26/表v3/shadow_v9标识；**K1未冻结、M2生产未放行、capture_only不变**。R14接收M2原型/X4，不重做旧审批；O0–O2联合独立过门且合同冻结才接生产。
- [O任务](iteration8/DELIVERY_PLAN.md)READY：O0异常状态+批量持仓集合（G1/G2/T01）；O1估值/影子统一精度时区（G6/T03）；O2 LONG行8（G4/T03）。下一版建议v0.8.27、表v4/shadow_v10候选，旧v9及更早不追认当前样本。
- 正常无账本RATIO_ONLY兼容已独立通过；不得扩大成异常账本也可判空的豁免。捕获时规则版本同表达式不另列阻断，真实当前表执行+旧协议分桶有效，不宣传通用兼容屏障。fetched_at/摘要ctx已修，只记录具体剩余。
- 下一动作：有O批新交付即按指纹增量复验；无新交付不重跑已知红例，不自行代写产品或提前启动M2。用户授权的协作由任务卡持续承接。

## 职责与长期目标

Codex负责架构/隔离探针/审批/任务卡/里程碑；未经改派不改产品源码、产品测试、配置、真实账户，不擅自Git提交。保持Python单体、固定所有权、同源终态；中长期独立，一个实际持仓一个主意图。研究查看只读，刷新/接受显式。正确性、可用性、投资效果分开；未达G8不称整体完成。

## 持续未完成项

- L2公开引用/checkpoint TRUE通过，完整MID VALID样板待G3；LONG数据/估值待G4，组合待G5。L3六点原件复算范围保留；J1缺URL、更正/人工/PIT/异机获取/波次B未齐，五条真实旧登记不清理。
- E1b/E5b NOT_RUN（前置/付费授权未齐）；E2b–E4b BLOCKED_DATA；K4无已验收合格真实样本，数据受阻不是完成。真实观察不拿模拟填满。
- B1墙钟+1秒生成research新revision，M2用只读查看解决、不删as_of散列语义。B2微型账本30次约15ms不证明性能瓶颈，G7先测量再优化。
- J2假隔离曾写真实portfolio；R12原生FAISS绕过Python审计已补守卫。使用iteration8 runner：导入前临时HOME，阻网络/子进程/真实.muyun，临时根写守卫、禁可选RAG及原生FAISS写守卫，7保护文件哈希/集合核对。不能只改HOME自称密闭。
- Codex额度守护仅[方案](iteration4/CODEX_QUOTA_RESUME_PLAN.md)，未部署后台自动恢复/监控，不承诺聊天停后持续运行。

## 工作区交接

- R13文档已由5c0f51d落库；`iteration7/acceptance_probes.py`仍未跟踪，必须保留。R14审批/任务/runner/结果/指纹在iteration8，当前入口和learnings同步仅落盘、未提交。
- 原有portfolio.yaml、knowledge/index/embedder_metadata.json、备份、.claude/.zcode、教材及记忆资料均保留。测试7保护文件哈希/集合未变，不将用户dirty当作待清理项。
- 新机器不能只checkout旧HEAD接手，需携带当前未提交架构产物。断网恢复先查成果，避免覆盖历史结果。
''', encoding="utf-8")

for name, marker, replacement in [
    ("README.md", "**长期路线与完成标准：", "**长期路线与完成标准：[MILESTONES.md](MILESTONES.md)**。v0.8.26 N0–N2已完成[R14独立审批](plan/fusion/iteration8/R14_ACCEPTANCE.md)：分项接收，423目标回归与17旧反例通过；异常账户、la持仓集合、影子时间和LONG权重仍须[O0–O2补齐](plan/fusion/iteration8/DELIVERY_PLAN.md)。M2原型25断言通过，生产未放行；K1未冻结、capture_only不变。详见[当前状态](plan/fusion/STATUS.md)，测试历史不等于整体功能或效果验收。"),
    ("AGENTS.md", "当前版本：**v0.8.26**", "当前版本：**v0.8.26**（N0/N1/N2，2026-10-02）。策略/聊天等入口已消费账户ctx，影子两臂取各自规范包，MID行5/6为表v3、协议shadow_v9候选；估值时间严格解析、保留NAV偏移和预取fetched_at，纯K线降级明示。**最新独立审批[R14](plan/fusion/iteration8/R14_ACCEPTANCE.md)：分项接收，整卡补齐**。独立30文件423 passed、R13的17合同反例全绿；新增5红断言归为Y1–Y4：异常账本仍可判空、la漏账本独有持仓、影子同日未来naive可有效、LONG未知权重仍ADD。M2原型25项断言通过；**K1未冻结、M2生产未放行、capture_only不变**。下一窗口[O0–O2](plan/fusion/iteration8/DELIVERY_PLAN.md)，当前状态以[STATUS](plan/fusion/STATUS.md)为准。实施方全量1482 passed/2 skipped/1 deselected本轮仅引用；目标报告462与独立423待清单核对。N批实施账本 `plan/fusion/iteration7/EXECUTION_RECORD.md`，上一轮审批见[R13](plan/fusion/iteration7/R13_ACCEPTANCE.md)。"),
    ("plan/fusion/README.md", "> **当前入口", "> **当前入口（2026-10-02）：[长期里程碑](../../MILESTONES.md) → [当前状态](STATUS.md) → [恢复入口](RESUME.md) → [R14独立审批](iteration8/R14_ACCEPTANCE.md) → [第八轮O0–O2任务](iteration8/DELIVERY_PLAN.md)。N批分项接收、M2原型通过；K1未冻结、M2生产未放行、capture_only不变。**"),
]:
    path = ROOT / name
    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.startswith(marker)]
    assert len(lines) == 1, (name, len(lines))
    edit(name, [(lines[0], replacement)])

with (ROOT / ".learnings/LEARNINGS.md").open("a", encoding="utf-8") as f:
    f.write('''
---

## [LRN-20261002-ARCH11] 通过旧反例后仍须核对分支与集合覆盖

**Logged**: 2026-10-02
**Priority**: high
**Status**: pending
**Area**: architecture

R14：R13的17反例已全绿，但异常账本的零投影/无幸存lot分支仍判空；la先按投影枚举再读ctx，账本独有股根本不进循环；MID新增保护未到LONG；估值时间门严格化未到shadow。实现同一机制不能只验证“循环中的正常对象”。必须核对入口集合、空/错/部分状态、所有周期及所有时间消费者，按同一输入矩阵跑成对断言。行动任务O0–O2，未改产品，本记录不是修复。

**See Also**: LRN-20261001-ARCH10；[R14](../plan/fusion/iteration8/R14_ACCEPTANCE.md)。
**Pattern-Key**: design.context_must_cover_enumeration_and_exception_branches
''')
with (ROOT / ".learnings/ERRORS.md").open("a", encoding="utf-8") as f:
    f.write('''
---

## [ERR-20261002-R14-PROBE] 探针模型导入及必填字段（已纠正）

**Status**: resolved
**Priority**: low

R14新增LONG探针初版误从decision_contract导入HorizonPlan、继而遗漏plan_id/intent。已按decision_policy定义纠正；最终日志5个失败均为业务断言，构造错误不计产品缺陷。新探针先核对真实类定义/必填字段，不能仅靠子Agent给出的概念输入直接构造。
''')

print("R14 handoff documents synchronized")
