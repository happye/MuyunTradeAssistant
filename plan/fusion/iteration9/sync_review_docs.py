"""One-shot R15 handoff update; only architecture documentation is changed."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def update_lines(name, changes):
    path = ROOT / name
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    for prefix, new in changes:
        found = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        assert len(found) == 1, (name, prefix, found)
        lines[found[0]] = new
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")


update_lines("MILESTONES.md", [
    ("本文件回答", "本文件回答“最终做成什么、现在在哪、还差什么、什么时候算完成”。每批实现与审批状态见 [STATUS](plan/fusion/STATUS.md)，当前审批见 [R15](plan/fusion/iteration9/R15_ACCEPTANCE.md)，下一执行窗口见 [第九轮](plan/fusion/iteration9/DELIVERY_PLAN.md)。F/R/J/K/L/M/N/O/P 是实施批次，下面 G0–G8 是稳定的产品里程碑，两者不可混用。"),
    ("| G0 |", "| G0 | 目标、基线和长期路线可持续恢复 | 框架已建立；随批维护 | 早期ADR、验证规范、本文件、R15 | 需求都有里程碑和验证；每次审批同步状态、证据和下阶段；读入口无冲突 |"),
    ("| G1 |", "| G1 | 事实与账户可信 | 部分完成 | O批独立33文件462绿；R14六检查全绿；O1/O2验收 | O0异常交叉和返回完整性仍有Z1/Z2→P0；不是所有异常均已闭合。数量/现金/成本在所有消费者一致仍待全链完成 |"),
    ("| G2 |", "| G2 | 统一的日常工作流 | 部分完成 | today/diff/tasks、L2公开引用、O0批量账本清单 | Z3聊天混合清单/启动提示→P1；M2只读接口设计已细化，P0/P1过门+合同冻结才接生产；today持仓卡并集未做 |"),
    ("| G6 |", "| G6 | 可比较的观察与效果晋级 | 证据受阻 | R15接收O1时间资格和O2 LONG边界，捕获/落盘/分母正反例通过 | shadow_v10未冻结，账户残余须先补齐；候选修正后旧协议不追认。真实观察、分周期独立留出实验和付费/数据前置仍未齐 |"),
    ("| G7 |", "| G7 | 必要重构、性能、诊断与多入口一致 | 部分完成 | 请求级快照、B1/B2基线、O批批量入口修复 | P1展示一致；today/analyze_industry/TUI/Web清单剩余未整体关闭；M2只读研究待接线。性能仍先测量，不把B2微型样本说成瓶颈 |"),
    ("1. **当前窗口", "1. **当前窗口：可信事实贯穿最终输出（G1/G2/G6）**。R15接收O1/O2，O0部分接收；P0补异常交叉/清单完整性，P1补聊天混合清单/同屏提示。固定矩阵和公开入口同次回执过门再裁定K1冻结，不重做已通过时间/LONG设计。"),
    ("2. **下一窗口", "2. **下一窗口：日常统一与首个完整中期样板（G2/G3/G5）**。M2原型25断言已由R14接收，第九轮已细化只读查询接口/共享渲染/八类验收；P0/P1独立过门且合同冻结后才接生产。正常RATIO_ONLY也可查看研究，不要求先建数量账本；完整MID VALID样板随后验证。"),
    ("| T01 ", "| T01 账户事实统一读出口 | O0批量数量清单通过，Z1/Z2异常及Z3展示残余 | G1/G2/G7，P0/P1收口；today卡/analyze_industry/TUI/Web剩余持久登记 | 同版本账户→所有消费者一致，未知不吞退出、不漏清单；当前未整体关闭 |"),
    ("| T02 ", "| T02 研究与计划命令归并 | B1问题明确，M2原型已接收 | G2/G3，R15方案DESIGN_READY；P0/P1过门且合同冻结后生产接线 | 查询不写研究；候选不替换接受态；跨入口同回执；不把run(capture=False)当只读 |"),
    ("| T03 ", "| T03 终态/观察共用字段语义 | 两臂规范包/版本门、O1时点与O2 LONG通过 | G6，账户残余阻断K1冻结；表v4保持，修正观察候选按语义登记 | 捕获/存储/分母一致已验；所有输入资格和主流程输出收口才整体关闭 |"),
])
with (ROOT / "MILESTONES.md").open("a", encoding="utf-8") as f:
    f.write("\n- 2026-10-02 R15：O1/O2接收，O0部分接收；33文件462绿，13合同项9绿4红（三组Z1–Z3）。K1未冻结/M2未放行；P0/P1 READY，M2只读接口DESIGN_READY。正常RATIO_ONLY无行为变化可接收；不强制迁移真实账户。\n")

(ROOT / "plan/fusion/STATUS.md").write_text('''# 融合项目当前实施与审批状态

**更新：2026-10-02（R15独立审批）｜受审HEAD：28c32cf｜v0.8.27｜整体未完成。** 长期路线见[MILESTONES](../../MILESTONES.md)，恢复见[RESUME](RESUME.md)。

## 当前裁决

[O批交付](iteration8/实施方报告_O系列交付.md)已完成[R15](iteration9/R15_ACCEPTANCE.md)：**O1/O2接收，Y3/Y4关闭；O0部分接收，Z1–Z3待补。K1未冻结，M2/K2b生产未放行，capture_only不变。** R12–R14已通过范围不重复审批，M2原型仍按R14接收。

独立**33文件462 passed**；合同13项**9 passed/4 failed**（R14六项全绿；新增7项3绿4红，分三组发现）。O1真实捕获→存储→报告的未来naive/过去naive正反例通过。实施方全量1521 passed/2 skipped/1 deselected仅引用未独立重跑。测试7保护文件哈希/知识库集合均未变。[指纹与证据](iteration9/REVIEW_INDEX.json)。

| 块 | 已接收 | 剩余/下一动作 | 里程碑 |
|---|---|---|---|
| O0账户/批量 | R14具体异常反例、账本独有股进la/CLI、混合去重/一次快照、单股失败续跑 | Z1 PARTIAL+零投影仍判空；Z2记录解码失败清单却完整→P0 | G1/G2/G5/T01 |
| O0展示 | 单股最终包和摘要消费ctx范围通过 | Z3 chat混合清单漏股、l/la启动提示仍FLAT→P1 | G2/G7/T01 |
| O1时间 | 共用解析，naive时刻/偏移/日精度矩阵，捕获落盘分母一致 | Y3关闭；无变化不重开设计 | G1/G6/T03 |
| O2 LONG规则 | 行8未知权重HOLD、已知权重ADD、硬退出优先，表v4 | Y4关闭；LONG完整数据/研究仍未完成 | G4/G6/T03 |
| K1观察合同 | shadow_v10候选、旧协议独立桶 | 账户残余阻断冻结；不晋级策略 | G6 |
| M2日常研究 | 原型25断言R14接收；接口/入口/验收方案已细化 | DESIGN_READY，P0/P1过门+合同冻结前不接生产 | G2/G3/G7/T02 |
| L2公开研究 | 公开引用/checkpoint TRUE | 完整MID VALID证据样板未完成 | G3 |
| L3原件 | 六点核定范围通过 | 缺URL、更正/人工/PIT/异机获取及LONG波次B待办 | G4/G5/T07 |
| K4/效果实验 | 无已验收合格真实样本 | E1b/E5b NOT_RUN；E2b–E4b BLOCKED_DATA；其余前置未齐 | G6 |
| 多端与工程 | 请求级事实、部分清单已接线 | today卡并集/analyze_industry/TUI/Web清单尚未归并；T01–T07未整体关闭 | G2/G7 |

## 当前任务与披露

[P0/P1任务与M2设计](iteration9/DELIVERY_PLAN.md)：P0固定账户状态组合矩阵和异常返回；P1共享清单/ctx进入查仓和启动提示。Claude Code实施、Codex独立复验。建议v0.8.28、表v4不改，账户输入语义变化随shadow_v11候选登记；旧观察不改写、不追认。

正常无数量账本RATIO_ONLY兼容接收，不强制用户迁移；正常la无差异不是拒收理由。但投影解码失败同样影响旧比例模式，不能笼统说全部异常合同只在数量账户生效。历史N批462解释仍缺旧清单，不背书；本轮33文件462已实证。

## 协作与恢复

R14文档已随28c32cf落库；R15资产和当前入口同步仅落盘未Git提交。iteration7旧探针未跟踪、本地持仓/知识库/备份/.claude/.zcode/教材均保留。禁止清理真实状态、自动补投影或覆盖历史证据。

[R13](iteration7/R13_ACCEPTANCE.md)→[R14](iteration8/R14_ACCEPTANCE.md)→[O实施账本](iteration8/EXECUTION_RECORD.md)→[R15](iteration9/R15_ACCEPTANCE.md)。新交付按最新指纹增量复验；无新交付不重复已知红例、不代写产品。付费实验未授权，非实盘下单，整体未完成。
''', encoding="utf-8")

(ROOT / "plan/fusion/RESUME.md").write_text('''# 融合项目恢复入口与Codex架构师记忆

**更新2026-10-02｜受审HEAD 28c32cf｜最新独立审批R15。** 身份以用户指派为准，角色规则见[CODEX.md](../../CODEX.md)。

## 恢复顺序

1. AGENTS.md→CODEX.md→根[MILESTONES](../../MILESTONES.md)：R01–R09/G0–G8/T01–T07固定，不另起路线。
2. [STATUS](STATUS.md)→[R15审批](iteration9/R15_ACCEPTANCE.md)→[第九轮P0/P1与M2设计](iteration9/DELIVERY_PLAN.md)。
3. 比较实际HEAD/工作树与[iteration9指纹](iteration9/REVIEW_INDEX.json)，查最新实施报告，按变化组复核消费者。R12–R14已通过范围和M2原型不重复审批。
4. 简短说明恢复进度后继续，常规隔离验收/文档维护已授权，不重复问确认。

## 最新进度

- O批产品0de6d38，文档28c32cf；v0.8.27。R15独立33文件462绿；13合同项9绿4红（R14六项全绿、新增七项三绿四红）。实施方全量1521/2skip/1deselect仅引用未独立重跑。
- **O1/O2接收，Y3/Y4关闭**：source_time共用解析；真正capture→存储→分母未来naive为0/过去为1；LONG未知权重HOLD、已知权重ADD、硬退出优先。表v4/shadow_v10候选追认。
- **O0部分接收，三组残余**：Z1 P1 PARTIAL+旧零数量投影仍NONE/0；Z2 P1合法YAML但记录无法解码时holding_entries返回空且incomplete=False，真la说无持仓；Z3 P2聊天混合清单漏账本股，以及账本有仓的l/la启动提示仍说FLAT（实际策略ctx已正确，不夸大成策略回退）。
- **K1未冻结，M2生产未放行，capture_only不变**。P0账户状态组合矩阵+异常返回、P1共享清单/ctx展示READY。M2原型已接收；第九轮已细化只读查询接口、入口装配、八类验收为DESIGN_READY，前置通过才接生产，不重做原型。
- 下一版建议v0.8.28、表v4保持，账户输入语义修正随shadow_v11候选登记；旧协议原件保留、只诊断不追认。新交付时按指纹增量复验；无交付不重跑已知红例。
- 用户补充真实账户RATIO_ONLY、正常la零变化：接收为兼容说明，不强制建数量账本；但投影解码异常也影响旧模式。当前33文件462实证，历史N批34文件462说法未附旧清单，不背书且不作代码阻断。

## 职责与保护

Codex负责架构/只读审查/隔离探针/审批/任务卡/里程碑，Claude Code实施产品；未经改派不改产品源码、产品测试、配置、真实账户，不擅自Git提交。一个实际持仓一个主意图，中长期独立；查看研究只读，刷新/接受显式。正确性、可用性与效果分列，未达G8不称整体完成。

测试用iteration9 runner：导入前临时HOME，阻网络/子进程/真实.muyun，限制临时根写入、禁可选RAG、原生FAISS写守卫；7保护文件哈希和集合核对。外源尝试被阻断≠没有尝试。历史J2假隔离和R12原生写绕过事件不再重犯。

## 未完成长期项

- G3完整MID VALID样板未做，checkpoint TRUE不能代替；G4 LONG财务/质量/估值与G5组合仍部分完成。L3六点原件历史范围保留；缺URL、更正/人工/PIT/异机获取/波次B待办，五条旧登记不清理。
- today持仓卡并集、chat analyze_industry和TUI/Web清单仍未归并，登记G2/G7/T01；不因本轮暂不作为冻结前置而算关闭。管理命令投影视图语义可保留。
- E1b/E5b NOT_RUN（前置/付费授权未齐），E2b–E4b BLOCKED_DATA；K4无已验收合格真实样本。冻结不是opt_in/默认策略晋级，不用模拟填真实分母。
- B1墙钟变化生成research revision，M2查询不得调用run(capture=False)；B2微型账本30次约15ms不是性能瓶颈证明。G7优化先测量。
- Codex额度守护仅[方案](iteration4/CODEX_QUOTA_RESUME_PLAN.md)，后台恢复/监控未部署，不承诺聊天停止后持续运行。

## 工作区与断网恢复

R14资产已由28c32cf落库；iteration7/acceptance_probes.py仍未跟踪，必须保留。R15文档/探针/runner/结果/指纹位于iteration9，当前入口同步只落盘未提交。原有portfolio.yaml、knowledge/index/embedder_metadata.json、备份/.claude/.zcode/教材/记忆资料均保留；本轮保护检查全不变。

断网先查本地结果和HEAD，勿覆盖旧日志或重跑已完成检查。跨机器接手须携带未提交架构产物，不只checkout旧HEAD。详细合同和回执以R15及任务卡为准。
''', encoding="utf-8")

update_lines("README.md", [("**长期路线与完成标准：", "**长期路线与完成标准：[MILESTONES.md](MILESTONES.md)**。v0.8.27 O批已完成[R15审批](plan/fusion/iteration9/R15_ACCEPTANCE.md)：O1时间资格、O2 LONG边界接收；O0仍有异常交叉与展示遗漏，按[P0/P1任务](plan/fusion/iteration9/DELIVERY_PLAN.md)补齐。独立33文件462 passed；K1未冻结、M2生产未放行、capture_only不变。正常RATIO_ONLY兼容保留，不强制建数量账本；[当前状态](plan/fusion/STATUS.md)区分实现、审批和效果。")])
update_lines("AGENTS.md", [("当前版本：**v0.8.27**", "当前版本：**v0.8.27**（O批，2026-10-02）。最新独立审批[R15](plan/fusion/iteration9/R15_ACCEPTANCE.md)：**O1/O2接收，O0部分接收**；表v4/shadow_v10候选。独立33文件462 passed，R14六项全绿；剩余Z1异常账本与零投影交叉、Z2清单读取失败未传播、Z3聊天混合清单与l/la启动提示，由[第九轮P0/P1](plan/fusion/iteration9/DELIVERY_PLAN.md)补齐。**K1未冻结、M2生产未放行、capture_only不变**；M2只读接口方案已细化，正常RATIO_ONLY不要求迁移。实施账本 `plan/fusion/iteration8/EXECUTION_RECORD.md`，恢复以[STATUS](plan/fusion/STATUS.md)/[RESUME](plan/fusion/RESUME.md)为准。")])
update_lines("plan/fusion/README.md", [("> **当前入口", "> **当前入口（2026-10-02）：[长期里程碑](../../MILESTONES.md) → [状态](STATUS.md) → [恢复](RESUME.md) → [R15审批](iteration9/R15_ACCEPTANCE.md) → [第九轮P0/P1与M2设计](iteration9/DELIVERY_PLAN.md)。O1/O2接收、O0部分接收；K1未冻结，M2生产未放行。**")])

with (ROOT / ".learnings/LEARNINGS.md").open("a", encoding="utf-8") as f:
    f.write('''
---

## [LRN-20261002-ARCH12] 异常维度需交叉，日志不能代替返回状态

**Logged**: 2026-10-02
**Priority**: high
**Status**: pending
**Area**: architecture

R15：O批33文件462绿、旧六反例全绿；PARTIAL+旧零投影组合仍判NONE。holding_entries捕获真实记录解码失败，logger已说待对账，但返回incomplete=False，la继续说无持仓。chat仅在投影空时补账本，混合清单仍漏；l/la启动提示与末端ctx矛盾。

执行改进：P0固定正常/异常/组合矩阵，断言返回标志及公开消费结果；P1以混合来源集合和同次完整输出测试，不能只assert末端摘要。ARCH11中入口集合/分支覆盖教训仍pending，不以该批红例转绿提前resolved。[R15](../plan/fusion/iteration9/R15_ACCEPTANCE.md)与[P任务](../plan/fusion/iteration9/DELIVERY_PLAN.md)给出具体关闭条件。本记录不是产品修复。

**Pattern-Key**: design.exception_state_must_reach_consumers
''')
print("R15 current handoff documents synchronized")
