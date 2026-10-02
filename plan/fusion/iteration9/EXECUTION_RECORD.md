# 第九轮 P0/P1 实施账本（账户完整性收口；M2 仅 DESIGN_READY 不实施）

> 实施方：Claude Code（实施会话）｜开工：2026-10-02｜基线 HEAD `28c32cf`
> 依据：[R15](R15_ACCEPTANCE.md)（O1/O2 接收关闭；O0 接收已验证范围，Z1/Z2/Z3 补齐）｜任务卡 [DELIVERY_PLAN](DELIVERY_PLAN.md)

## 状态

| 卡 | 内容 | 状态 |
|---|---|---|
| P0 | Z1（PARTIAL+零投影→UNKNOWN）+ Z2（投影枚举失败传播 incomplete）+ 固定验收矩阵 9 行 | 进行中 |
| P1 | Z3①（get_portfolio 全路径共享清单）+ Z3②（l/la 启动提示按 ctx 三态）+ 多端支持范围台账 | 进行中 |
| M2 | 只读研究视图——DESIGN_READY，**本轮不实施**（前置=P0/P1 过门+合同冻结） | 不实施 |

## 病根锚点（实施前复核）

- Z1 `src/data/portfolio.py:488-493`：PARTIAL 保护只在 pos 缺席分支（:467-472）；投影分支 proj_q==0 + `_ledger_partial` 仍 NONE "记录与账本均无持仓"。
- Z2 `src/data/portfolio.py:528-530`：holding_entries 的 except 只打日志，incomplete 不传播（合法 YAML 坏记录 → _corrupted=False → 返回声称完整）。
- Z3① `src/chat/tools.py:523-533`：get_portfolio 只在投影空时查共享清单；投影非空时账本独有被截断。
- Z3② `src/cli/main.py:947-948`：analyze_live 持仓提示按 pos 存在判断——账本独有股同屏"将从FLAT状态开始分析"vs"账本有仓100股"矛盾。

## 版本登记

- 产品版本 v0.8.27 → v0.8.28（五处）。
- Z1 改变账户输入及衍生观察 → **shadow_v11 候选**（任务卡指示；v10 及更早独立桶保留）。
- 表 v4 保持（无周期规则修改）；CACHE_VERSION 不动。

## 执行记录

- [2026-10-02] 交接核对：基线 `28c32cf` 与任务卡一致；R15 反例（iteration9/acceptance_probes.py 4 失败断言）与四处病根全部读毕。
- [2026-10-02] 红灯先行：新建 `tests/core/test_p0_account_exception_matrix.py`（固定矩阵 9 行参数化）+ `tests/chat/test_p1_shared_portfolio_view.py`（Z3①集合断言/Z3② FLAT 禁令/UNKNOWN 正例/RATIO_ONLY 兼容）。红灯确认 9 红（Z1/Z2/Z3①×3/Z3②×3 + row8 positive 构造修正后转锚绿）。
- [2026-10-02] Z1 修复：context_for 投影分支补 `_ledger_partial` → UNKNOWN/None/待对账；Z2 修复：holding_entries `projection_failed` 传播 incomplete；Z3① 修复：get_portfolio 全路径共享清单（投影明细+账本独有分段+incomplete 提示）；Z3② 修复：analyze_live 持仓提示按 ctx 三态 + pos 存在但 ctx UNKNOWN 追加缺口原因行。
- [2026-10-02] shadow_v10→v11 候选（任务卡指示：Z1 账户输入语义变化登记；非映射规则变化不凑版本）；4 个既有测试文件字面同步（sync_version_literals.py）。
- [2026-10-02] 版本五处 v0.8.28；AGENTS/README 状态段+版本历史；STATUS.md 追加交付段；多端台账 ISSUES.md ISS-116；实施方报告落库。
- [2026-10-02] 目标回归 35 文件 **485 passed**（=R15 基准 462+23）；全量 **1544 passed/2 skipped/1 deselected**、7 保护文件哈希与知识库集合不变。M2 未实施（DESIGN_READY，等 P0/P1 过门+合同冻结）。
- [2026-10-02] 全量第一轮暴露既有顺序依赖（非本批引入）：ISS-091 新闻缓存测试依赖类级缓存"未命中初态"，网络窗口下被其他测试的真实抓取污染——一行加固（测试内显式清空 `_stock_news_cache`），教训 LRN-20261002-OBT3。
- [2026-10-02] 第一轮 guard：1×P1（PARTIAL × RATIO_ONLY 零比例 → NONE——同事实两形态结论相反、清单/ctx 矛盾）+ 5×P2。P1 选方案(a) 补 UNKNOWN 分支+row10 锁死；P2 全部落地。第二轮 guard 复核：**可合入**（语义自洽性独立推演确认）。
