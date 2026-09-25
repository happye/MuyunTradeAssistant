# E0–E7 实验矩阵与场景覆盖计划（plan/fusion F8 交付）

2026-09-25。VALIDATION.md §2/§3 的落地文件。**诚实声明：本文件是实验基建的冻结计划，
E0–E7 的真实执行未发生**（需外源数据/长时回测，单独入口 opt-in）；基建的离线可验证
部分（组合回放纯模型/制度检查/未来数据探针/manifest 机制）已落地并有 10 测试锁
（`src/core/experiment.py` + `tests/core/test_experiment_replay.py`）。

## 1. 实验注册（src/core/experiment.py E_SPEC，机器化 VALIDATION §3）

| ID | 名称 | 固定 | 改变 | 信息集标签 | 当前状态 |
|---|---|---|---|---|---|
| E0 | 正确性基线 | 同一历史快照、旧策略参数 | 仅终态/事实持仓/执行正确性修正 | rule_bz_proxy | **已执行（2026-09-26，首个真实执行）**：21 案例双臂（legacy vs T+1 批次份额），差异全部在项目噪声阈内——旧基线与修正后基线等价；报告 E0_BASELINE_REPORT.md + manifest tests/artifacts/e0_baseline/ |
| E1 | 候选召回 | 同时点市场全集、研究预算 | 技术/产业/质量单路 vs 并集配额 | rule_bz_proxy | blocked_on_data（机制可跑：候选池 fingerprint 对账键；**数据未备**——同时点市场全集需历史快照） |
| E2 | 投资逻辑资格 | 同一候选集合、相同入场/退出 | 无资格过滤/bz旧分/证据资格 | rule_bz_proxy | blocked_on_data（2026-09-26 探查后财务 pubDate 已实证可 PIT——财务因子接线批是前置） |
| E3 | 持有纪律 | 同一事前候选与入场日 | legacy/fusion_mid/fusion_long 分别报告 | rule_bz_proxy | blocked_on_data |
| E4 | 技术择时 | 同一投资逻辑和预算 | 固定周期分批/技术触发/混合门控 | rule_bz_proxy | blocked_on_data |
| E5 | AI | 同一证据、计划、预算 | 无AI/结构化提取/提取+反证（旧 modifier 独立对照） | **ai_lookahead** | blocked_on_data |
| E6 | 组合 | 同一单股动作集合 | 逐股建议 vs 统一资金与集中度约束 | rule_bz_proxy | **runnable**（solve_budget 已可离线对比） |
| E7 | 完整系统 | 同一PIT全集、成本、研究资源预算 | 修正后基线 vs 完整融合漏斗 | rule_bz_proxy | blocked_on_data |

信息集标签强制：rule_bz_proxy / ai_lookahead / human_mode 三类不混记（信息集来源
在 E_SPEC 注册时锁定，报告输出必须带标签）。

## 2. 开跑前置（VALIDATION §2 manifest 缺一不可）

每次执行前必须落 ExperimentManifest（`ExperimentManifest.build`）：
代码提交 / 配置 hash / 数据源版本 / as_of / 交易日历 / 费用规则 / 样本集与纳入排除
原因 / 缺失比例 / AI 模型与 prompt 版本 / 试验编号（失败试验也保留）/ 冻结时间。
**样本集为空不许开跑**（构造期即拒）。

## 3. 场景覆盖清单（TASKS F8：IPO/停牌/一字板/除权/分红/退市/财务修订）

| 场景 | 检查函数（ReplayChecks，离线单测已锁） | 真实数据跑批 |
|---|---|---|
| T+1 | check_t_plus_1（当日买不可当日卖） | external 回放 |
| 数量单位 | check_lot_size / round_lot_buy（买入整手向下） | external 回放 |
| 一字板 | check_limit_board（涨停禁买/跌停禁卖） | external 回放 |
| 现金不足 | PortfolioReplay.buy（不足一手/现金不足拒绝） | 离线已测 |
| 除权/分红 | **待接线**（价格序列须用不含分红复权口径做成交、另计股息——VALIDATION §2 复权纪律；DataFeeder 现口径核对随 external 阶段） | external |
| IPO/停牌/退市 | **待接线**（上市日过滤/停牌 bar 缺失跳过/退市强平估值——依赖 DATA_COVERAGE「停牌状态」行接线） | external |
| 财务修订 | F3 证据层 revision 语义已锁（后发重述不进旧快照） | 已离线覆盖 |
| 成本单调性 | 性质测试（成本加大净收益不升） | 离线已测 |
| 未来数据 | validate_no_future_bars 探针 | 离线已测 |

## 4. 统计要求（VALIDATION §4 摘要——执行时的硬门）

- 训练/验证/测试按时间前推；阈值/分位只在训练窗拟合
- 收益分布不正态不报普通 t；试验登记与多重选择修正参考 DSR
- 报告维度：收益/超额/最大回撤/回撤持续/下行/换手/持有期/成本/现金暴露/行业集中/
  未成交率/拒绝覆盖率
- 基线至少含：同候选买入持有、简单可解释策略、宽基总收益、行业/风格参照
  （小盘科技策略仅超沪深300不足以证明选股能力）
