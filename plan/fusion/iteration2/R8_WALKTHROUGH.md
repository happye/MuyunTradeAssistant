# R8 八类用户走查记录（plan/fusion iteration2，RESEARCH_LOOP §6 产品合同）

> 2026-09-27｜R8 验收的走查证据记录。**区分合成走查与真实用户试用**：
> 本表全部为合成/离线走查（隔离 HOME + 测试代码 600519），**真实用户试用尚未发生**
> （R9 发布门：opt_in 初次运营观察提议下限=20 交易日/30 证券-计划版本/10 次有新证据复评）。
> 走查命令与实际输出可复现（命令逐条给出；输出为实测摘录）。

## 走查矩阵（八类）

| # | 场景 | 走查路径（实际执行命令） | 用户能回答"现在做什么/为什么/何时再看" | 结果 |
|---|---|---|---|---|
| ① | 新候选自动草稿 | `research 600519`（离线）/ `research <代码> --capture`（采集财务） | MID/LONG 各一句资格结论；草稿未激活；缺口逐条点名（"缺能力/证据: valuation_range_v1"）；下次看：来源文档或季度披露 | ✅ 合成走查通过（输出摘录见下） |
| ② | 正常 MID 持仓 | plan2 立+accept 激活 → `la`/`l`（live 分析）→ shadow 真判断 | 依赖 live 分析网络路径——**本轮未跑真实 l（成本/时间），命令序列已记录** | ⏳ 待真实试用 |
| ③ | 正常 LONG 持仓与复核到期 | plan2 long → l → today | 同上（live 依赖） | ⏳ 待真实试用 |
| ④ | 同股周期冲突 | 仅 MID 接受 → shadow 报告分周期计数（R0 验收2 的 render 测试实测） | "MID=真判断｜LONG=模拟计划"分列；冲突时提示查看两计划差异 | ✅ 合成走查通过（test_accepted_plan_long_side_stays_simulated + 渲染实测） |
| ⑤ | 已知风险退出受阻 | E0b 场景1/3（同日买入退出被拒/末日估值）+ E7b EXIT+BLOCKED | "退出意图保持，本次数量没变，下一交易时点复查可卖数量" | ✅ 合成走查通过（e0b_directed_report.json） |
| ⑥ | 研究合格但预算不足 | E7b 预算案例②③④（最低申报/现金 0/未确认卖出不释放） | "符合研究条件暂不新增；现有现金不足最低申报；不占用预算" | ✅ 合成走查通过（e7b_controlled_report.json） |
| ⑦ | 数据缺失/来源冲突/AI 失败 | research 离线（无证据→具体缺口）；snapshot qualify 冲突/缺失话术；LLM 失败计数 | 缺什么点名（不笼统报错）；冲突 → CONFLICTED→REVIEW | ✅ 合成走查通过（测试+实测） |
| ⑧ | 部分成交与重复确认后再分析 | 事件账本幂等/重放测试（R5 验收3）+ proposals fill_id 幂等（F1 既有） | 重复确认不重复入账；重放一致 | ✅ 合成走查通过 |

## 实测输出摘录（隔离 HOME，2026-09-27）

**① research 离线**（真实 REPL 输出）：
```
🔬 单股研究工作台 600519
  MID ○ 逻辑待建立
    · 公司哪个业务真实受益、暴露依据可查证（仅概念标签不达标）
    ▸ [MID] 待核验事实——自动研究需来源文档或人工通道
  LONG ○ 逻辑待建立
    · …共 5 项缺口
  系统草稿（未激活——用户选择意图后才出行动建议）
    MID: [系统草稿 UNESTABLISHED] 命题评估见 assessment；缺口 3 项
  缺口 6 项：
    · [MID] 缺能力/证据: relative_return_v2（命题无法建立——不强行 VALID）
    · [LONG] 缺能力/证据: valuation_range_v1 …
  来源 0 份文档；已核验主张 0 条；run run_5d762aeaa12e0738
```

**④ plan2 生命周期 + R0 资格门提示**（真实 REPL 输出）：
```
✅ 600519 [MID] 计划草稿已保存（未激活）
   已发生事实: 6月订单环比增长30%
 ℹ 事实未挂证据引用——影子对照按「逻辑未立」处理；资格门要求事实可定位到原文证据
✅ 600519 计划已激活（2026-09-27T03:15:00）——影子对照/周期决策表开始出真判断
  600519 [MID] 已激活 r2 ｜ 锂电需求回暖
```

**--capture 全链**（真实网络，600519；**监督员 P0 修正后重录**——首次记录把「缺口行减少」
误读为「命题建立」并写错摘录顺序，实为因子接线形状错配（metric vs metric_or_claim）下的
键存在性效应；修复重跑后如实记录）：
```
  能力 cash_conversion_v1: status=OK value=1.5356倍
  能力 roe_observed_v1: status=OK value=0.1795倍
  能力 balance_risk_v1: status=OK value=None各分量原生单位（倍）
  MID ○ 逻辑待建立
  LONG ○ 逻辑待建立
    · …共 3 项缺口（cash_sustainability/capital_constraint 命题已建立——因子 OK；
      moat/valuation_assumption/longterm_invalidation 仍无依据）
  严格快照拒收: latest_only_unverifiable×32；suspect_semantic×2
    （latest-only 财务不入严格历史判定——live 研究口径不受影响；2 条疑似异常被筛查隔离）
  来源 0 份文档；已核验主张 0 条；未达核验 0 条；run run_00178ba6c22b479b
```
留样落 ~/.muyun/research/raw（写一次幂等）。

## 性能基线（R8 验收6）

- `research` 核心链路（ResearchService.run 离线）：**亚毫秒级**（独立复测 p50 0.008–0.125ms、
  p95 0.135–0.300ms，n=20，混合冷热实例——口径以复测区间记，R9 审查校正原精确数字）
  ——决策表/命题评估为纯函数量级；live 瓶颈在网络抓取（与本基线正交）。
- today/l 渲染基线沿用 F 系列口径（本批未改动其渲染路径）。

## 性质声明

- 八类中 ①④⑤⑥⑦⑧ 共 6 类有合成走查证据；②③（正常持仓 live 分析）依赖真实网络分析
  与真实用户持仓，留待 R9 前真实试用（命令序列已给，不写"可运行"充数）。
- chat/Web/TUI 三端消费同输入一致性：**本批仅 CLI 接线**（chat/Web/TUI 适配随 R9 前接线批）——
  验收3 如实 PARTIAL。
