# ISS-117 唯一执行计划与收口记录

更新：2026-10-14。当前版本v0.8.31；基线HEAD 0b3fadd，改动在现有工作树，未提交或推送。以下表格保留原v0.8.30专项范围；新增尾项和当前指纹见第九轮R15。

**原S2a/S2b/S3已验范围保持接收，本次行动说明/独立止盈抑制尾项已关闭。** 2026-10-14限定P0/P1/P2独立PASS，shadow_v14合同冻结；后续M2有限l/la只读接线已另按R15验收。当前唯一执行入口为现有[第九轮计划](../iteration9/DELIVERY_PLAN.md)。本文件保留原范围记录，不再另派实施路线；当前结论仍见[S2AC验收](S2AC_ACCEPTANCE.md)。已通过的Q规则不重批，capture_only不变。

## 已落实的完整范围

| 范围 | 实际修改 / 用户效果 | 源码与正式回归 |
|---|---|---|
| S2a 动作一致 | 已有仓实际增加一律ADD；HOLD不清策略记忆；未知不伪造数量/权重 | decision_engine/strategy_layer；test_s2a_aggregation |
| S2a 两条账 | 在实际选择处保留独立止损/止盈身份及强度；普通A、意见占比P、覆盖C同源进packet/JSONL/卡片；P仅展示 | models/decision_contract/analysis_service/evidence；test_s2a_aggregation |
| S2b 持有政策 | 删除产业关键词的强制mode覆盖；预览=draft；旧mode/期限/初始止损保持，更新预览后明确接受；聊天确认兼容 | generator/self_reliance/auto_scorer/CLI；test_iss117_completion |
| S2b 完整失效条件 | Guard与Adjuster共用TRUE/FALSE/UNKNOWN；OR/AND完整计算；未知文本/缺指标或阈值不猜；待复核不假称安全、不自动清仓 | trade_plan/conditions、plan_guard、adjuster；test_iss117_completion/test_trade_plan |
| S2b 执行资格 | 实时量比/近涨跌幅只是估计；缺盘口/订单/可卖数量UNKNOWN；核验停牌或零可卖量BLOCKED；意图不消失；回测明示模拟撮合 | execution_layer/analysis_service/action_view/CLI/chat；test_iss117_completion/test_h_block_fixes |
| S3 同源预算 | 同一命题+证据修订+证券/时点跨槽贡献上限=max配置权重，按权重缩放；冲突弃权；无可解析外部证据不充票；不同技术谓词保留独立预算 | decision_engine/SkillSignal；test_iss117_completion |
| S3 授权与影子 | 结构化发现贯穿Guard/packet/JSONL；路径名或布尔值不制造硬证据；两臂共用核定事实及执行资格；辅助分数/增量cap记ID、范围、前后值 | orchestrator/plan_guard/shadow_diff/analysis_service；completion/shadow_diff/n1_canonical_arms/m0_account_terminal |
| S3 输出与待办 | 摘要区分决定依据、辅助研究和缺口；完整保存提醒/追溯；旧字段未记录不补假；最新无操作建议使旧待办过期；分析不写成交、不发起实际冷却 | evidence/CLI/chat/portfolio/proposals；completion/chat_command_bridge/既有账户回归 |
| 同族数值资格 | 坏价格不充普通票；止损/均线/量比阈值须有限正值；调整建议不能写入NaN/Inf或下移止损 | decision_engine/conditions/adjuster/plan_guard；test_iss117_completion |
| 验证隔离 | 默认持仓路径也隔离；脚本数学验证留在审计进程内；图谱/chat产物临时化；仅允许Windows标准库socketpair内部唤醒管道 | tests/conftest/indicator_math_script/industry_data；严格隔离验证 |

## 保持的规则与兼容

- 冻结公式仍是固定分母、A/P/C分开；不加最少票数、覆盖率下限、普通票7天阈值，不改收益参数。
- Q资格规则已关闭；日历/三倍资格、合法止损/ST/期限和已接受计划纪律保持。UNKNOWN不吞独立合法退出。
- 新建计划沿用现有等级/阶段政策；旧接受计划缺新字段不算未接受。用户明确选择的模式保持。止损更新只上移且经确认。
- 回测保持原保守撮合；实时不冒充已成交。确认成交仍是唯一事实写入通道。
- 权限预算协议iss117-s3-v1，影子shadow_v13；旧记录独立桶，不迁移追认。diff同时核评分与权限协议，价格差值照常。
- 笨总维度评分/缓存输入未改，CACHE_VERSION不变；启动/CLI/AGENTS/README统一v0.8.30。

## 结束条件与证据

原范围验证与审批：2026-10-12离线全量1818 passed / 0 failed / 2 skipped / 1 deselected / 1 warning（314.45s）；2026-10-13纯展示告警映射补验72 passed / 0 failed。7项保护哈希及知识库文件集合不变。全量发生在展示映射前，不将72项并入全量计数。此前独立reviewer于2026-10-13只读审阅源码及正式用例，未发现其所审范围内的确定P0/P1阻断，未运行测试；该结论不覆盖后续新增反例。原复核限制及关闭依据见S2AC_ACCEPTANCE.md，尾项当前补修和审批统一见第九轮DELIVERY_PLAN.md及R15_ACCEPTANCE.md。

## 项目边界

本专项完成不代表暮云整体结项：K1 shadow_v14已冻结，后续M2有限l/la只读接线另已过门，capture_only不变；G3与长期融合能力仍按MILESTONES/STATUS原门槛推进。不新增S4/S5，不需要用户删除缓存、修文件或修改真实账户。
