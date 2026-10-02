# 第八轮 O 批实施账本（O0–O2，Y1–Y4 补齐）

> 实施方：Claude Code（实施会话）｜开工：2026-10-02｜基线 HEAD `4eb5c1b`（产品主提交 `154b556`）
> 角色铁律：Claude Code=实施方，Codex=独立验收；本账本只记实施过程，不代审批。

## 状态

| 卡 | 内容 | 状态 |
|---|---|---|
| O0 | Y1 异常账户判定 + Y2 批量持仓集合（portfolio 读侧 / start.py live_all / cli analyze_portfolio） | 进行中 |
| O1 | Y3 估值与影子时点资格一致（source_time 纯函数收敛 + shadow_v10 候选） | 进行中 |
| O2 | Y4 LONG 行8 未知权重冻结（决策表 v4） | 进行中 |

## 执行记录

- [2026-10-02] 交接核对：HEAD `4eb5c1b` 与交接文档一致；工作树脏文件=用户本地状态+架构师资产（不回滚不覆盖）。R14/DELIVERY_PLAN/m0 测试基建/病根锚点全部读毕。
- [2026-10-02] 红灯先行：新建 `tests/core/test_o0_exceptional_account.py`（Y1×2+正例保留矩阵+Y2 真REPL la/analyze_portfolio+snapshot 计数）、`tests/data_sources/test_o1_time_gate_matrix.py`（估值/影子两侧同输入矩阵，as_of 参数化）、`tests/core/test_o2_long_unknown_weight.py`（行8 矩阵+硬退出优先+版本登记）。红灯确认：O0 7红/4锚绿、O1 9红/1绿（as_of TypeError 形态）、O2 3红/12绿。
- [2026-10-02] O0 修复：portfolio.py 两病根分支→UNKNOWN + HoldingEntry/_normalize_stock_code/holding_entries；start.py live_all 清单前置；cli/main.py analyze_portfolio 同步+汇总表 current_ratio None 防护。O0 12 绿。
- [2026-10-02] O1 修复：新 src/core/source_time.py（parse_source_time/source_time_in_future）；portfolio/shadow_diff 接线（_parse_cutoff/_cutoff_in_future 薄包装、_qualified_weight as_of 参数化）；SHADOW_DERIVATION_VERSION→shadow_v10。O1 10 绿。
- [2026-10-02] O2 修复：行8 not known 分支→HOLD；DECISION_TABLE_VERSION→v4。O2 15 绿（参数化展开）。
- [2026-10-02] 版本字面同步：test_m1_quote_time/test_k1_shadow_v6/test_l0_shadow_contract/test_n1_canonical_arms 的 shadow_v9→v10、v3→v4（合同升级登记；sync_version_literals.py 留档）。
- [2026-10-02] 同类扫描与最小接线：三处扫描排除集（start.py auto_exclude_holdings、cli/main.py scanner、chat/tools.py 扫描）+ chat get_portfolio 账本独有提示已接线；today（账户事实区已兜住）/pos list/event 计数/analyze_industry/TUI-Web 登记不改（清单见实施方报告 §1）。
- [2026-10-02] 口径核对：同 HEAD 复跑架构师 30 文件=423 passed 精确复现；本轮 33 文件（30+O批3）=460 passed（423+37 参数化展开项）；462 vs 423=收集文件集不同（N批 34 文件集 vs 架构师 30 文件集）。结果 TARGETED_TEST_RESULTS_O.txt。
- [2026-10-02] 全量重跑（修复 chat 测试单例顺序依赖后）：见 FULL_TEST_RESULTS.txt；7 保护文件哈希与知识库集合不变。
- [2026-10-02] 版本五处 v0.8.27；README 版本历史/AGENTS 状态段更新；实施方报告落库。
- [2026-10-02] 第一轮 guard：1×P0（live_all handler `as e` 撞循环变量→单只异常整批中止）+2×P1（HoldingEntry frozen 撞观察量内存更新；incomplete 漏 _ledger_partial→PARTIAL 下 la 仍报"当前无持仓"）+3×P2（新告警三处同步缺失；账本独有股误导性观察告警；存量 4 元组解包）。全部修复+2 个新回归测试（O0 14 项）；旧全量证据留档 R1_pre_guard_fix。
- [2026-10-02] guard 复核：可合入（六处逐一确认，docstring nit 已补）。最终树：目标回归 33 文件 **462 passed**（423+39）、全量 **1521 passed/2 skipped/1 deselected**、7 保护文件哈希与知识库集合不变。
- [2026-10-02] 教训落库 .learnings：模块级单例消费测试须显式注入（LRN-20261002-OBT1）、审计钩子 runner 结果走 stdout 重定向（LRN-20261002-OBT2）。

## 病根锚点（实施前复核）

- Y1-病根1 `src/data/portfolio.py:441-442`：`_read_failed` 分支中投影 quantity_fact=0 → NONE/0（旧零投影被当空仓证明）。
- Y1-病根2 `src/data/portfolio.py:445-452`：pos None + `_ledger_quantity`==0 + `_ledger_partial` → NONE "无持仓记录"（PARTIAL 无幸存 lot 无法证明空仓）。
- Y2-病根 `start.py:1702-1716`（live_all）与 `src/cli/main.py:408-416`（analyze_portfolio）：清单只来自 `pm.list_positions()`，在读快照之前判空返回。
- Y3-病根 `src/core/shadow_diff.py:318-327` `_cutoff_in_future`：naive 带时刻串只比日期，当天未来 naive 可过资格。
- Y4-病根 `src/core/decision_policy.py:292-304` 行8：LONG+HELD+weight=None+VALID+质量/买区合格+budget=True → ADD（行6 MID 已有 v3 保护，行8 缺）。

## 版本登记

- 产品版本 v0.8.26 → v0.8.27（五处：start.py / cli/main.py banner+--version / README 状态段+版本历史 / AGENTS 当前版本段）。
- 决策表 v3 → v4（受影响行=行8；position_state=None 路径旧行为零变化）。
- 影子协议 shadow_v9 → shadow_v10 候选（资格语义变更；v9 及更早独立桶保留不追认）。
- CACHE_VERSION 不动（评分域零改动）。

## 纪律对照

- 修复四步：红灯测试（本批 3 文件）→ 修转绿 → 同类扫描（today/摘要/建议清单判据；ThreadPool 清单不适用）→ .learnings 落库。
- 每卡 commit 前 code-quality-guard；告警三处同步（若新增 logger.warning）；不 `git add -A`。
