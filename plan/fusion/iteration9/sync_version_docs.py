# -*- coding: utf-8 -*-
"""第九轮版本五处同步（v0.8.28）：AGENTS/README 状态段更新（保留架构师 R15 事实）。"""
import re
from pathlib import Path

# AGENTS.md 当前版本段
p = Path('AGENTS.md')
t = p.read_text(encoding='utf-8')
m = re.search(r'当前版本：\*\*v0\.8\.27\*\*（O批，2026-10-02）。.*?恢复以\[STATUS\]\(plan/fusion/STATUS\.md\)/\[RESUME\]\(plan/fusion/RESUME\.md\)为准。', t, re.S)
assert m, 'AGENTS 版本段未匹配'
new_agents = ('当前版本：**v0.8.28**（P0/P1，2026-10-02）。R15裁决O1/O2接收关闭、O0部分接收后，本轮补齐三组缺口：'
  '**Z1**账本PARTIAL保护贯穿投影分支（旧零投影不再证明空仓→UNKNOWN/None/待对账）、'
  '**Z2**holding_entries投影枚举失败传播incomplete（合法YAML坏记录不再谎报完整，账本独立持仓保留）、'
  '**Z3**chat get_portfolio全路径读共享清单（账本独有条目标注缺口）+ l/la启动提示按ctx三态'
  '（账本有仓不再同屏宣称FLAT起点；pos存在但ctx未对账时追加缺口原因）。表v4保持；**shadow_v11候选**'
  '（账户输入语义变化，v10及更早独立桶不追认）。固定验收矩阵9行参数化落回归（tests/core/test_p0_account_exception_matrix.py、'
  'tests/chat/test_p1_shared_portfolio_view.py）。多端支持范围已转录[ISSUES.md](ISSUES.md)持久台账（G2/G7/T01）。'
  '**K1未冻结、M2生产未放行（DESIGN_READY）、capture_only不变**。实施账本 `plan/fusion/iteration9/EXECUTION_RECORD.md`。')
t = t[:m.start()] + new_agents + t[m.end():]
p.write_text(t, encoding='utf-8', newline='\n')
print('AGENTS.md updated')

# README 状态段 + 版本历史新段
p = Path('README.md')
t = p.read_text(encoding='utf-8')
m = re.search(r'\*\*长期路线与完成标准：\[MILESTONES\.md\]\(MILESTONES\.md\)\*\*。v0\.8\.27 O批已完成.*?区分实现、审批和效果。', t, re.S)
assert m, 'README 状态段未匹配'
new_readme = ('**长期路线与完成标准：[MILESTONES.md](MILESTONES.md)**。v0.8.28 P0/P1已交付（R15 Z1/Z2/Z3补齐）：异常账户保护贯穿全部投影分支、'
  '清单读取失败如实传播、chat查仓与l/la启动提示按共享清单与ctx三态显示；shadow_v11候选、表v4保持；待架构师独立验收。'
  'M2生产未放行（DESIGN_READY）；K1未冻结、capture_only不变。正常RATIO_ONLY兼容保留；[当前状态](plan/fusion/STATUS.md)区分实现、审批和效果。')
t = t[:m.start()] + new_readme + t[m.end():]

history_anchor = '## 版本历史\n\n'
new_history = history_anchor + (
    '### v0.8.28 (P0/P1——账户完整性收口：异常贯穿状态与清单 + 查仓与启动提示同源) - 2026-10-02\n\n'
    'plan/fusion iteration9 P0/P1（修 R15 Z1–Z3；账本 plan/fusion/iteration9/EXECUTION_RECORD.md；M2 仅 DESIGN_READY 不实施）。\n\n'
    '- 🆕 **异常保护贯穿投影分支（P0/Z1）**：账本 PARTIAL（隔离事件）时旧零数量投影不再可证明空仓——'
    '与 pos 缺席分支同口径转 UNKNOWN/None/待对账（旧实现 NONE/0「记录与账本均无持仓」）；不反推股数\n'
    '- 🆕 **清单读取失败如实传播（P0/Z2）**：holding_entries 投影枚举异常（合法 YAML 但记录解码失败）'
    '→ incomplete=True——不再「日志说待对账、返回值声称完整」；可独立列出的账本持仓保留，la 不得输出「当前无持仓」\n'
    '- 🆕 **查仓与启动提示同源（P1/Z3）**：chat get_portfolio 所有路径读同一共享清单（投影细节保留、'
    '账本独有条目标注元数据缺口、清单不完整显式提示）；l/la 启动提示按 ctx 三态——账本有仓不再同屏宣称'
    '「将从FLAT状态开始分析」（改示「账本有仓 N股」），pos 存在但 ctx 未对账时追加缺口原因行\n'
    '- 🔧 固定验收矩阵 9 行参数化落正式回归（账本/记录状态 × 投影或独立事实交叉，数量状态与权重分别断言）；'
    'shadow 协议升 **shadow_v11 候选**（账户输入语义变化；v10 及更早独立桶保留不追认）、表 v4 保持\n'
    '- 测试 1521→见 [FULL_TEST_RESULTS](plan/fusion/iteration9/FULL_TEST_RESULTS.txt)（P0 矩阵 13 例 + P1 9 例新增；'
    '目标回归 35 文件 484 passed = R15 基准 462 + 22）\n\n')
assert history_anchor in t
t = t.replace(history_anchor, new_history, 1)
p.write_text(t, encoding='utf-8', newline='\n')
print('README.md updated')
