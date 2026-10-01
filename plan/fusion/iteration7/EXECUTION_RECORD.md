# 第七轮 N 系列实施账本（N0–N2 + M2 原型修订）

实施 Agent：Claude Code｜基线 `73ba005`（R13 审批落库前工作区）｜交付版本 **v0.8.26**。

每卡流程：R13 反例转产品回归红灯 → 修转绿 → 同类点扫描 → code-quality-guard 监督审查 → P1/P2 处置 → 联合同一 HEAD 回归。任务卡见 [DELIVERY_PLAN](DELIVERY_PLAN.md)；实施方报告见 [实施方报告_N系列交付](实施方报告_N系列交付.md)。

## N0｜让已装配账户事实成为所有消费者的事实来源（修 X1）

**红灯**：`tests/core/test_n0_ctx_source_of_truth.py` 初版 10 失败（策略层仍读投影旧比例 .1 / 无投影 FLAT+0 / chat 无投影硬退出 WAIT 且无账户版本 / 读取失败 NONE+0 / 隔离事件不冻结 / 影子以 pos 存在为前置 / 摘要以 pos 判据 / la 读取次数无真断言）。

**修复**：
- `src/data/portfolio.py`：`RequestAccountFacts` 增 `_read_failed`（读取失败→UNKNOWN 待对账，不当作空仓；数量投影在账本读不到同样保守未对账；RATIO_ONLY 投影比例独立成立）与 `_ledger_partial`（isolated_events 或 data_completeness==PARTIAL → 权重一律冻结+待对账原因）；`to_strategy_state` 增 `account_context` 参数——ctx 是持仓/权重唯一事实源（投影旧比例不再二次判读），账本有仓无投影→重建仓 OPEN/成本不伪造，已知空仓照旧 FLAT/0.0；`account_nav` NAV 保留偏移（isoformat）。
- `src/chat/tools.py`：ctx 装配**不以遍历到投影为前置**（先建 facts 再匹配投影；无投影也装配）；strategy_state 经 ctx 装配；has_position 全由 ctx 三态判定。
- `src/cli/main.py`：l/la strategy_state 经 ctx；`analyze_live` 增 `account_facts` 参数；摘要 `_print_plain_summary` 增 `account_context`——`_account_fact_note()` 统一产出退出/减仓方向保留注记（ctx 判据：无投影有仓/待对账/权重待重估三形态）、合格估值显示派生权重及来源、无投影有仓独立诊断行。
- `start.py`：la / l all / 多代码三个批量循环一次装配 `account_facts` 传入（B2 同批次同版本）；`analyze_live` 缺省自建（单股下一命令刷新）。
- 影子：`capture_shadow` 无投影但 ctx HELD/UNKNOWN 仍捕获（退出方向保留；建议仍不伪造可确认项——pos confirm 依赖投影，缺口由摘要诊断承载）。
- **同类点（guard P1-3）**：scanner_engine/web/tui 三入口同款 ctx 装配+三态 has_position（账本有仓无投影不按空仓分析）。
- 告警三处同步：新 warning「请求级账户事实读取失败」→ plain_errors.py + docs/报错速查手册.md。

## N1｜按规范包记录两臂并落实有效版本资格（修 X2）

**红灯**：`tests/core/test_n1_canonical_arms.py` 初版 11 失败（HOLD 正比例 .1 进 legacy 臂 KNOWN / execution 字段是动作名 / UNSUPPORTED_FUTURE_VERSION 计有效 / 行5 UNKNOWN REDUCE / 行6 预算 True 掩盖权重缺口 / 旧桶只计 MID / v9 缺失）。

**修复**：
- `src/core/shadow_diff.py` **shadow_v9**：`_build_binding` legacy 臂消费最终 legacy DecisionPacket（action/target/blockers/execution_status 同枚举同语义；原 HOLD 占位守卫被规范包消费取代而删除）；`_binding_eligibility` 增版本资格门（policy_version==RESEARCH_SERVICE_VERSION、method_version==ASSERTION_METHOD_VERSION、decision_rule_version==policy_id@DECISION_TABLE_VERSION，否则 `unsupported_*` drop——版本字符串存在≠兼容；不改写历史计划原值）；报告旧桶 MID/LONG 独立计数（diagnostic=两者皆无）。
- `src/core/decision_policy.py` **v2→v3**（受影响行=行5、行6）：行5 UNKNOWN 技术退出 REDUCE→REVIEW（规模待对账）；行6 HELD+未知权重即使 budget=True 也 HOLD（预算已知不掩盖权重缺口）。其余行零变化；position_state=None 推导路径随 v3 明确迁移（confirmed=None→行5 REVIEW），迁移锚 `test_no_position_state_migration_is_explicit`。

## N2｜时间资格严格解析与同日未来检查（修 X3）

**红灯**：`tests/data_sources/test_n2_time_gate.py` 初版 9 失败（X3 四条全过估值门 / NAV 偏移被 strftime 丢 / NaN/Inf 混入 / 预取无 fetched_at / kline_only 缺标注）。

**修复**：
- `src/data/portfolio.py`：`_quote_day` 重写为 `_parse_source_time` 严格解析——非法日历（2026-02-30）/非法时刻（99:99:99）/垃圾尾巴 → None（不截取救回）；naive 显式按 Asia/Shanghai；aware 归一；日精度按交易所日期判未来、时刻精度**先比实际时刻再取日**（当天未来时刻拒绝）；`_qualified_weight` finite 检查。
- `src/data/akshare_client.py`：sina/baostock 提取与 EM/ETF 四个 dict 携带 `fetched_at`（预取复用不打新时点；`_calculate_indicators_uncached` 优先消费 quote 携带的原抓取时刻）；纯 K 线回退 `price_source="kline_only"`。
- l/la/chat 降级提示扩展 kline_only（「K 线收盘（实时行情未取得）（非实时）——当前权重待重估」）。

## M2 原型修订（X4，条件卡——仍限计划目录）

`plan/fusion/iteration6/m2_readonly_view_prototype.py` 按 R13 X4 修订：损坏库返回 `store_status="corrupted"`（不装空库、不给「建立研究」误导）、每次视图调用**真前后哈希**（废除自比较恒真）、旧方法（非当期已验证生成版本）场景标注、重启读取一致、评估状态只消费产品核对规则（错配/未来时点/缺引用——不发明期限、不用字符串「过期」）。**25/25 场景通过**，结果 [m2_readonly_view_results.json](../iteration6/m2_readonly_view_results.json)。M2 生产前置不变（N0–N2 联合过门+合同冻结）。

## guard 审查处置（一轮全量）

- **P1×3 全部修复**：(1) start.py live_multi/l all 分支缺 `PortfolioManager` 局部 import——批量入口 UnboundLocalError 全灭（4 个既有测试红实证）→ 两分支补 import；(2) n2 午夜边界测试硬编码日历日跨午夜永久红 → 动态未来时刻；(3) scanner/TUI/Web 同类点未扫（X1 同型缺陷）→ 三入口 ctx 接线（见 N0）。
- **P2 落实**：la/chat kline_only 提示补齐；l0/m1 去重测试午夜窗口 flake 修复（同日安全推进）；`_ledger_partial` 纳入 data_completeness==PARTIAL；测试替身签名补 `account_facts` 形参（live_scan_all/multi_code）。
- **披露（不改）**：`decision_rule_version` 资格检查在捕获时与绑定构造同表达式（恒真）——跨版本隔离实效靠 derivation_version 分桶，不宣称该子项为防旧表混入的实效屏障；账本文件不存在（≠读取失败）仍按明确空仓处理（RATIO_ONLY 用户无账本属正常形态——登记为已知边界）；l/chat 竖向测试存在笨总 margin 表既有外联尝试（失败降级，非本批引入）。

## 版本与全量

- 版本统一 **v0.8.26**（start.py/banner/--version/README/AGENTS.md；README/AGENTS/STATUS 分层更新在工作树，与架构师 R13 文档同步汇合提交）。
- 联合同一 HEAD 目标回归 **30 文件 462 passed**（R13 的 27 文件集 + N0/N1/N2）；隔离离线全量见 [FULL_TEST_RESULTS](FULL_TEST_RESULTS.txt)（runner 输出路径改至 iteration7，不覆写 iteration6 的 M 批受审证据）。
- CACHE_VERSION 未 bump（评分域零改动）；capture_only 不变；K1 未冻结、M2/K2b 生产未放行。
