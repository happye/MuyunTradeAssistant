# 实施方报告：P0/P1 交付（Z1–Z3 补齐，账户完整性收口）

> 2026-10-02｜Claude Code 实施会话｜基线 HEAD `28c32cf`｜v0.8.27
> 角色：Claude Code=实施方；本报告不代审批，待 Codex 架构师独立验收。
> 交付版本 **v0.8.28**；表 v4 保持；**shadow_v11 候选**（Z1 账户输入语义变化）。

## 0. 交付摘要

| 卡 | 反例 | 修法落点 | 状态 |
|---|---|---|---|
| P0/Z1 | PARTIAL 无幸存 lot + 旧零投影 → ctx 仍 NONE/0"记录与账本均无持仓" | context_for 投影分支补 `_ledger_partial` 保护 → UNKNOWN/None/待对账（与 pos 缺席分支同口径） | ✅ 红→绿 |
| P0/Z2 | 合法 YAML 但记录解码失败 → holding_entries 返回空清单 incomplete=False，真 la 说"当前无持仓记录" | 投影枚举异常传播 `projection_failed` → incomplete=True；可独立列出的账本持仓保留 | ✅ 红→绿 |
| P1/Z3① | 投影有 000001+账本独有 600519 → chat.get_portfolio 只列 000001 | 所有路径读同一共享清单（holding_entries）：投影明细保留+账本独有分段标注缺口+incomplete 显式提示 | ✅ 红→绿 |
| P1/Z3② | 账本独有 600519 的 la 同屏"将从FLAT状态开始分析"vs"账本有仓100股" | analyze_live 持仓提示按 ctx 三态（pos 仅提供元数据不再决定有无仓）；pos 存在但 ctx 未对账追加缺口原因行 | ✅ 红→绿 |

## 1. P0：固定验收矩阵（9 行，参数化落正式回归）

[tests/core/test_p0_account_exception_matrix.py](../../../tests/core/test_p0_account_exception_matrix.py)——数量状态与权重分别断言（`_assert_ctx` 助手），清单与 ctx 同请求不矛盾（同一 incomplete 语义）：

| 行 | 场景 | 断言（状态/数量/权重/清单） | 红绿 |
|---|---|---|---|
| 1 | 正常无账本+无投影 | NONE/0/0.0；空清单 incomplete=False | 锚绿 |
| 2 | 正常无账本+合法旧比例>0 | HELD/None/.1；清单含该股 incomplete=False | 锚绿 |
| 3 | 健康账本数量0 + 无投影/**合法数量0投影**（两参数） | NONE/0/0.0；incomplete=False（不报假异常） | 锚绿 |
| 4 | 健康账本正数量+无投影 | HELD/100/None（缺 NAV）；清单含该股、from_ledger、成本/比例不伪造 | 锚绿 |
| 5 | 健康账本正数量+不一致数量投影 | HELD/100/None+对账原因 | 锚绿 |
| 6 | PARTIAL 有幸存正 lot + 有/无投影（两参数） | HELD/100/None+待对账；incomplete=True | 锚绿 |
| 7 | PARTIAL 无幸存 lot + 无投影 | UNKNOWN/None/None；incomplete=True；坏行不改原账本 | 锚绿（O批已修） |
| 7b | **PARTIAL 无幸存 lot + 旧零投影（Z1 主反例）** | UNKNOWN/None/None；incomplete=True | **红灯→绿** |
| 8 | 快照读取失败 × 无投影/数量0/正数量投影（三参数） | 全 UNKNOWN/None；正数量投影给提示性 quantity=100 但状态 UNKNOWN（不冒充核对通过）；incomplete=True | 锚绿（O批已修；构造修正后 3/3 绿） |
| 9 | **合法 YAML 记录解码失败 + 无账本/有独立账本持仓（两参数）** | incomplete=True；真 la 不说"当前无持仓"（P1 测试覆盖）；独立账本持仓（600519 账本侧）在清单保留 | **红灯→绿** |

- 真实文件损坏/条目类型异常不用整体 mock：行 9 写真实坏 YAML（`'600519': 42`），断言 `_corrupted=False`（文件级语法合法）且 `pm.list_positions()` 真实抛错；行 7/7b 写真实截断账本行。
- 读取前后字节不变（行 7/7b 断言）；同请求快照次数不增加（holding_entries 不再读快照——O批合同保持；下一请求刷新由 O批 `test_y2_next_request_reads_new_version` 回归覆盖）。
- 相邻旧比例/零比例异常分支实际结果（逐 cell 列明，guard 审查后修正）：
  - 快照读取失败 × RATIO_ONLY 比例 0 → NONE（投影比例语义不依赖账本——N0 既有决策保持）
  - **账本 PARTIAL × RATIO_ONLY 零比例 → UNKNOWN（guard 第一轮 P1 修正）**：真实可达形态=全部卖出+冷却记录恰为 ratio=0 且 quantity_fact 已弹出，账本坏行可能是隔离掉的一笔新买入——旧比例 0 不足以证明空仓；与数量投影形态（row7b）同判，同请求清单 incomplete 与 ctx 不再矛盾
  - 纯 RATIO_ONLY 无账本用户零变化（无账本时 _ledger_partial=False，正常兼容 NONE/HELD 保持——row1/2 锚定）
  - 原则：无法证明独立空仓（PARTIAL/读失败/解码失败）一律 UNKNOWN，不借"无账本兼容"放行异常状态

## 2. P1：共享清单和 ctx 贯穿查仓与启动提示

[tests/chat/test_p1_shared_portfolio_view.py](../../../tests/chat/test_p1_shared_portfolio_view.py) 9 例：

1. **Z3①混合集合**（红灯）：投影独有 000001+账本独有 600519 → 输出含两者+账本段标注"成本/比例未知待对账"。
2. **集合相等**：投影独有 A/重叠 B/账本独有 C → 三只代码各出现且不漏；投影细节保留（名称照旧展示）——不漏不重，不只 assert"不是空仓"。
3. **空与不完整分开**：明确空 → 恰为"当前无持仓记录"；Z2 同款解码失败 → 提示"待对账/未对账"不谎报空仓。
4. **Z3② la**（红灯）：账本独有 100 股 → 输出不含"将从FLAT状态开始分析"、含"账本有仓 100 股"。
5. **Z3② l 单股**：同口径。
6. **UNKNOWN 提示正例**：读取失败+正数量投影 → 不宣称 FLAT，显示投影明细+"账本读取失败且存在数量投影——未对账"原因行。
7. **正常 RATIO_ONLY 展示兼容**：既有"持仓记录"区与比例显示保持。
8. **批次不中止回归**：单只失败后账本独有股仍被分析（O批 P0 回归在新树保持绿）。
9. 硬退出方向一致性：既有回归覆盖（r13 replay 的 chat EXIT、m0 竖向 EXIT——本轮未触碰策略/适配层，回归全绿）。

**多端支持范围台账**（验收4）：已转录 [ISSUES.md ISS-116](../../../ISSUES.md)——已支持（l/la/chat get_portfolio/chat analyze_stock/三处扫描排除集/today 账户区）与未支持（today 持仓卡并集、analyze_industry、pos 管理命令投影视图语义、TUI/Web）逐项列明，不以文档登记冒充实现，T01 不宣称整体关闭。

## 3. 测试统计与证据

- 红灯先行：P0/P1 两个新文件先红（9 红矩阵/探针断言）后修转绿；R15 四条探针断言全部对应转正式回归。
- **目标回归：35 文件 = 485 passed**（=R15 架构师 33 文件 462 基准 + P 批新增 23：P0 矩阵 14（含 guard 补的 row10）+ P1 9）。精确命令与收集清单见 [TARGETED_TEST_RESULTS_P.txt](TARGETED_TEST_RESULTS_P.txt)（COLLECTED_FILES=35）。
- **全量**：见 [FULL_TEST_RESULTS.txt](FULL_TEST_RESULTS.txt)（隔离离线，HOME 重定向+保护哈希；实施方 runner 无网络阻断，如实表述；runner 中外源调用尝试被拒绝后降级的性质与架构师 runner 相同——"未成功联网"不写成"无外联尝试"）。7 保护文件哈希与知识库集合不变。
- **全量暴露一处既有顺序依赖（非本批引入，一行加固）**：第一轮全量 test_news_disk_cache_roundtrip（ISS-091，v0.8.9.2 引入的测试）失败——代理开启的网络窗口下，全量顺序中其他测试（chat/web 真实链）真实抓取新闻并写入 `NewsClient._stock_news_cache` 类级缓存，该测试依赖"缓存未命中"初态（单跑绿、O批全量绿，网络窗口变化后红）。加固：测试内 `monkeypatch.setattr(NewsClient, "_stock_news_cache", {})` 显式隔离初态（与该测试已 patch `_news_disk_path` 同主题）；教训已录 .learnings（LRN-20261002-OBT3，与 OBT1 模块级单例同族）。第一轮全量留档于本轮 runner 重跑覆盖前如实记录于此：1 failed/1542 passed → 加固后重跑。
- shadow 协议字面：4 个既有测试文件 shadow_v10→v11 同步登记（账户输入语义升位，非改测试凑绿）。

## 4. 版本与文档同步

- 版本五处 → **v0.8.28**（start.py VERSION / cli banner / cli --version / README 状态段+版本历史 / AGENTS 当前版本段）。
- **shadow_v11 候选**（SHADOW_DERIVATION_VERSION + docstring v11 段）：Z1 改变账户输入及衍生观察——按任务卡指示登记；v10 及更早独立桶保留不追认。表 v4 保持（无周期规则修改，不为凑版本改规则）；CACHE_VERSION 不动。
- 无新增 logger.warning 文案（Z2 沿用既有"投影持仓清单读取失败"warning——已在 O批三处同步；本轮无新文案）。
- 实施账本：[EXECUTION_RECORD.md](EXECUTION_RECORD.md)。多端台账：ISSUES.md ISS-116。
- 不覆盖 R15 审批探针结果（acceptance_probes/ACCEPTANCE_PROBE_RESULTS 原样）；实施方结果另存 TARGETED_TEST_RESULTS_P/FULL_TEST_RESULTS。
- 历史口径按 R15 裁定更正：N 批"34文件462"历史解释**未获追认**——现有工作树已无旧树对应清单，按裁定改为"旧报告统计未核实"（本报告 §3 仅主张本轮 33文件462=R15 实证基准、35文件485=本轮口径）。

## 4c. guard 复核结论

第一轮 guard：1×P1（PARTIAL × RATIO_ONLY 零比例 → NONE——同事实两形态结论相反、违反清单/ctx 不矛盾）+ 5×P2（断言补强×2、runner 死变量、docstring 零付费前提、iss091 漂移核对）。全部修复：P1 选方案(a) 补 UNKNOWN 分支+row10 锁死；P2 各项落地。第二轮 guard 复核：**可合入**（7 项逐一核验，语义自洽性独立推演：读失败×比例0 保持 NONE 与 PARTIAL×比例0 → UNKNOWN 的不对称有原则——PARTIAL 的隔离事件能与陈旧投影矛盾，读失败无事件证据可矛盾）。

## 5. 红线自查与剩余披露

- 未触碰事件确认/修复协议（confirm_fill/opening_import 零改动）；未扩供应商、未重写 main、未修真实账户；不自动补投影、不自行恢复丢失成交。
- 工作树架构师 R15 资产（iteration9 审批文档/runner/指纹）未覆盖；commit 只含本批相关文件（架构师资产按 iteration8 先例归档落库并注明归属）。
- 零付费 AI、零网络（行情替身/规则 fallback）。
- M2：仅 DESIGN_READY——本轮未实施任何 M2 生产接线（无 get_daily_view、无 l/la 研究段），按任务卡等 P0/P1 过门+合同冻结。
- 剩余未完成项：ISS-116 台账所列多端归并（G2/G7/T01 排期）；N 批 462 历史清单无原始记录（已按裁定标注未核实）。
