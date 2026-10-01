# R12：L 系列独立审批

2026-10-01｜Codex 架构师｜受审 HEAD `679afa6`｜产品版本仍 v0.8.24。

## 结论

**分项接收，L0/L1 退回补齐；K1 记录合同暂不冻结，K2b 生产归并暂不放行。** 已关闭的反例不重新打开：V1 缺行情字段、V2 去重、V3 策略读数、V4 公开引用均有进展。新的阻断来自跨层消费和行情来源，而非测试数量不足。

| 交付 | 审批范围 | 未完成部分 |
|---|---|---|
| L0 | 统一字段资格函数、版本分桶、真实 arms 目标变化指纹通过 | W2 来源日期丢失，旧收盘被标成当前行情；两臂仍须消费同一合格账户/终态语义 |
| L1 | 数量持仓的策略读数、未知权重守卫、总权重未知、today 提示接收 | W1 最终行动包与周期策略仍用旧比例判断有无持仓；完整消费者闭环未通过 |
| L2 | 公开 `--ref` 通道、缺引用拒绝、引用正例及现有端到端回归通过 | 当前正例证明检查点 TRUE，不证明完整 MID VALID；完整中期样板仍属 G3 待办 |
| L3 | 可跟踪清单、raw/mapped 分列、非覆盖登记、六点离线重算通过 | 缺失 J1 URL、更正检索、人工表格核对、异机原件获取和 PIT 资格仍未验收 |

## 独立证据与边界

- [目标测试](TARGETED_TEST_RESULTS.txt)：21 个测试文件，**281 passed，59.30秒**；最终运行7个受保护的持仓/知识库文件哈希和知识库文件集合均未变。初次运行281 passed/124.60秒，但原生RAG写入绕过Python守卫，见下面的隔离披露；修正隔离后重新验证。实施方报告的 **1398 passed / 2 skipped** 全量本轮未独立重跑。
- [独立探针](ACCEPTANCE_PROBE_RESULTS.json)：历史 V1/V2/V3/V3b 不复现；另测当前 `arms.fusion.target` 的真实字段，变化不被吞。旧 V2 在 binding 外层写 `target_weight` 是旧模式字段，不能单独充当当前 schema 的验收证据。
- 旧 V4 无 `--ref` → UNKNOWN，保留原始 `observed_defect=true` 历史标签；新的显式 `--ref order1` → 引用实存、检查点 TRUE。**该历史布尔值现在描述负例行为，不再证明公开通道不可达。** 完整评估仍 UNESTABLISHED，未伪称整个中期逻辑成立。
- [原件复验](EVIDENCE_RECHECK.json)：6/6 PASS。仅本地 hash、提取、自洽与比值复算；本轮没有联网重取、发行人更正检索、视觉审表或投资效果验证。
- 未改产品源码/测试/配置或真实账户；未调用付费 AI；未 Git 提交。原有工作树改动保留。探针退出码表示执行是否完整，缺陷是否存在须看各结果，不以退出 0 冒充审批通过。

### 本轮测试隔离披露

首次L2公开链测试进入可选RAG初始化，`faiss.write_index`的原生IO绕过CPython审计，14:43:14在仓库生成`knowledge/index/index.faiss.tmp`（16,740,045字节）。后续Python原子替换被守卫阻断；portfolio/bak哈希未变，Git未显示知识库已有文件新增变更。原始日志保留[INITIAL_TEST_RESULTS](INITIAL_TEST_RESULTS.txt)。没有事前全知识库哈希，因此不宣称凭首次守卫证明全部文件字节未变。

临时文件初始工作树不存在，时间/调用路径与本轮吻合；记录SHA256 `d9b7764dd39dabb350f5d3855a9c8c7f6a716b41fdfc18bf08981f51276bc359`后，仅按精确路径及哈希条件清理该文件。修正runner：可选RAG方法检索替身为None（不替换待验的research/accept/策略/捕获），另对faiss原生写边界校验临时根；加入知识库现有文件哈希与文件集合前后检查并重跑目标集。网络/子进程/真实`.muyun`/Python越界写守卫继续保留。此为架构师验证工具的遗漏，不归责L系列产品实现。

## W1｜P1：有股但旧比例为零，清仓被最终适配器吞掉

实测 `quantity=100，ratio_stale=true，current_ratio=0`：策略读数为 None；输入终态 `CLOSE_ALL/fundamental_alert`，`build_decision_packet` 输出 WAIT，`capture_shadow` 的 MID 也输出 WAIT。

根因链：`src/cli/main.py:929`（la 同类点 543）、`src/chat/tools.py:342` 仍传 `pos.current_ratio`；`src/core/analysis_service.py` 以 `confirmed_ratio > 0` 判持仓；`src/core/shadow_diff.py:551` 再取旧比例传给 `evaluate_horizon`。只把调用参数改成 None 仍不够：独立对照证明适配器对未知权重 REDUCE 也给 WAIT。

修复必须显式区分**有无持仓**与**权重资格**，贯穿最终包、影子、摘要、diff/证据、建议，不仅是 orchestrator 的入参。未知权重不应吞退出/减仓方向。观察两臂应取各自规范化的终态；不要把 StrategyDecision 的 HOLD 占位比例 0 直接解释成目标清仓。

范围限制：W1 是真实生产适配函数的组合反例，调用参数与当前 l/la/chat 一致，未宣称现场触发了用户账户清仓。另一个 W1b 技术减仓反例**未**产生伪造数值目标（fusion target=None）；这项负结果保留，不作为第三项缺陷。

## W2｜P1：降级收盘行情被重新标为当前时点

实测供应商行日期 `2026-09-29`，走实际 `_fetch_baostock_realtime` 提取和 `_calculate_indicators_uncached`：`quote_as_of` 变成 `2026-10-01` 抓取时刻。数量 100 × 价格 10 / 当天 NAV 10000，继而锁出权重 10%，本应因价格与 NAV 日期不一致保持未知。

根因：Baostock 返回字典丢弃查询结果的 `date`（`akshare_client.py:1058` 附近）；上层对任何非空 quote 均填 `_quote_fetched_at`（1222/1261/1322）。预取命中也可能重新打时点。已有新浪/日线降级和缓存必须整体追踪。

**价格有效时点、抓取时点、缓存复用时点必须分开。** 缺少源时点可保留抓取信息用于诊断，不能靠抓取墙钟取得合格估值/有效比较资格。旧日行情可按真实日期用于明确的历史观察，但不能与当天 NAV 拼出当前权重。M1 验证后升级协议，旧 v7 不追认成修正后的样本。

## 八项裁决

1. **K1 冻结：暂不批准**。M0/M1 关闭 W1/W2，完成来源/账户/规范终态的组合复验后再冻结。冻结记录合同不授权策略晋级。
2. **K2b：生产接线暂不放行，M2 限定设计与原型接收**。允许先做临时根内原型/回归准备；M0/M1 经独立复验后再进入产品实施。不能在 l 内每次无条件 `ResearchApplicationService.run()`。
3. **V4：认可缺引用负例行为**。本轮已增加独立显式引用正例；保留历史脚本与结果，不改历史事实。L2 入口范围关闭，G3 全 MID 样板不随之关闭。
4. **policy_version：暂不要求等于最新版本**。它描述计划生成来源，应原样保存；当前决策表版本和研究方法另行绑定。历史来源不等于不能执行，也不等于自动兼容。冻结/实验分组须按版本明确分层；未知/不兼容版本不可混入当前比较组。首轮冻结仅覆盖经回归验证的生成版本×研究方法×决策表组合，其余保留诊断；不把任意字符串存在当版本兼容证明。后续只为真实多版本消费建立必要兼容清单，不先造版本框架。
5. **真实库五条旧登记：不清理重登**。保留原记录，未来采用追加 supersedes/修订记录及 dry-run 差异；先在临时副本验证幂等与恢复。真实库更改需要明确数据范围，当前架构审批不授权删改用户历史。
6. **版本：下一次已通过回归的修复交付统一升 v0.8.25**，写清 L 与 M0/M1 的范围和 capture_only。同步实际 banner/--version/start.py、README 和共享版本说明；`src.__version__=0.1.0` 的包版本历史差异先核定用途，勿按“固定三处”盲改。
7. **波次 B：暂不扩采**。先由首个非金融 LONG 样板声明消费者和指标定义，再选择 currentRatio/quickRatio/assetToEquity；已有六点负债率不证明其它字段。G4/G5 保留待办，不以搬迁清单当长期研究完成。
8. **里程碑：本轮同步根 MILESTONES、STATUS、RESUME**。G1/G2/G3/G6 有范围明确的推进，G4 证据便携推进；整体仍未完成。E1b/E5b NOT_RUN、E2b–E4b BLOCKED_DATA 不变。

下一窗口与设计证据见 [DELIVERY_PLAN](DELIVERY_PLAN.md)、[DESIGN_VALIDATION](DESIGN_VALIDATION.md)。
