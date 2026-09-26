# E0–E7 实验矩阵与场景覆盖计划（plan/fusion F8 交付）

> **2026-09-26 架构验收补充**：已执行不等于原始实验目标全部验证。E0为人工案例敏感性、E1为当前截面、E5为提取层、E6存在非严格信息、E7为全WAIT烟测；具体分级及下一版设计见 [iteration2/VALIDATION.md](iteration2/VALIDATION.md)。旧产物不改写；R7按真实信息集和未完成条款继续实施。

2026-09-25 交付；2026-09-26 更新：**E0/E1/E5(证据层)/E6/E7(烟测) 已真实执行**（v1/v2
口径见各行状态与对应 REPORT）；E2/E3/E4 及 E1 历史版/E5 本体/E7 完整版登记后续批
（前置条件见各行）。基建的离线可验证部分（组合回放纯模型/制度检查/未来数据探针/
manifest 机制）有 10+ 测试锁（`src/core/experiment.py` + `tests/core/test_experiment_replay.py`）。

## 1. 实验注册（src/core/experiment.py E_SPEC，机器化 VALIDATION §3）

| ID | 名称 | 固定 | 改变 | 信息集标签 | 当前状态 |
|---|---|---|---|---|---|
| E0 | 正确性基线 | 同一历史快照、旧策略参数 | 仅终态/事实持仓/执行正确性修正 | rule_bz_proxy | **已执行（2026-09-26，首个真实执行）**：21 案例双臂（legacy vs T+1 批次份额），差异全部在项目噪声阈内——本人工案例集内差异较小（不能统计证明等价，2026-09-26 裁决）；报告 E0_BASELINE_REPORT.md + manifest tests/artifacts/e0_baseline/ |
| E1 | 候选召回 | 同时点市场全集、研究预算 | 技术/产业/质量单路 vs 并集配额 | rule_bz_proxy | **已执行（2026-09-26，v1）**：live 快照 5568 只三路召回——技术 50/产业 100/质量 5，跨路重叠 0（当前截面重叠低；35 为配额后并集，经济互补待 E1b，2026-09-26 裁决）；报告 E1_REPORT.md；历史 PIT 版登记后续批（ISS-114 balance 字段漂移一并处理） |
| E2 | 投资逻辑资格 | 同一候选集合、相同入场/退出 | 无资格过滤/bz旧分/证据资格 | rule_bz_proxy | blocked_on_data（2026-09-26 探查后财务 pubDate 已实证可 PIT——财务因子接线批是前置） |
| E3 | 持有纪律 | 同一事前候选与入场日 | legacy/fusion_mid/fusion_long 分别报告 | rule_bz_proxy | blocked_on_data |
| E4 | 技术择时 | 同一投资逻辑和预算 | 固定周期分批/技术触发/混合门控 | rule_bz_proxy | blocked_on_data |
| E5 | AI | 同一证据、计划、预算 | 无AI/结构化提取/提取+反证（旧 modifier 独立对照） | **ai_lookahead** | **证据层 v1 已执行（2026-09-26）**：真实 LLM 提取器冻结评测 8/8 + 三臂消融（无AI 6/LLM 11/反证标记 5）；报告 E5_REPORT.md；决策路径 A/B（本体）须影子期验证——后续批 |
| E6 | 组合 | 同一单股动作集合 | 逐股建议 vs 统一资金与集中度约束 | rule_bz_proxy | **已执行（2026-09-26，v2）**：E0 臂 A 动作集 420万两臂——批次 T+1 回放+行业约束（50% 用户档）+费率对齐；统一预算最大单股权重 78.5%→46.9%、求解拒绝 38/执行拒绝 37 分计；报告 E6_REPORT.md |
| E7 | 完整系统 | 同一PIT全集、成本、研究资源预算 | 修正后基线 vs 完整融合漏斗 | rule_bz_proxy | **漏斗烟测 v1 已执行（2026-09-26）**：候选 35→研究完备 5→资格 5→决策表→预算全链端到端可跑、逐级存活可计量；报告 E7_REPORT.md；真正 E7（历史 PIT 全集+收益对照）待 E1 历史版 |

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
