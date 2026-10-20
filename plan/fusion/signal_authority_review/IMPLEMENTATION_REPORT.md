# ISS-117 最终实施报告

2026-10-13，v0.8.30；基线HEAD `0b3fadd5b604df60f29f13a0149b4fa5806eb18d`。原S0/S1/Q/S2a/S2b/S3范围已完成，改动在工作树，未提交或推送。原范围记录为[S2AC_FINAL_CLOSEOUT_TASK.md](S2AC_FINAL_CLOSEOUT_TASK.md)，当前专项验收为[S2AC_ACCEPTANCE.md](S2AC_ACCEPTANCE.md)；2026-10-13用户反馈使行动说明/止盈抑制尾项局部重开，统一实施看[原第九轮计划](../iteration9/DELIVERY_PLAN.md)。

原问题根因：AI把行业成熟误标为30+，字符串标签越权触发硬清仓。当前30+仅作研究提醒，缺证据或未知不得取得强动作权限，合法止损、ST与期限等硬纪律独立保留。

## A01–A13最终行为映射

| 项 | 当前行为 | 主要源码 / 正式回归 |
|---|---|---|
| A01 减持标题 | 标题及否定、取消、澄清、未实施仅研究线索，未核主体/时点/原件不授权退出 | exit_signals/stock.py；test_signal_authority_iss117、test_top_signal_announcements |
| A02 股东户数 | 明确统计日期倒序去重；本次/上次显式映射；缺公开时点UNKNOWN，激增不硬清仓 | exit_signals/stock.py；test_signal_authority_iss117、test_iss117_q_batch |
| A03 旗手窗口/复权 | qfq同口径；窗口、未来行、同日冲突、21收盘观测、跨度与末观测资格核验；研究权限 | exit_signals/sector.py、akshare_client.py；test_iss117_q_batch |
| A04 缺数据降门 | 缩量缺连续证据UNKNOWN；未知技能条件fail-closed，失败槽不缩分母 | stock.py、skill_engine.py；test_shrink_acceleration_consecutive、test_signal_authority_iss117 |
| A05 预告日期 | 缺失/非法/未来跳过；同日时序不明披露；回测不拉当前预告 | fundamental.py；test_fundamental_alert、test_fundamental_alert_backtest_guard |
| A06 AI/事件越权 | black_swan不强制PANIC；软调节按真实性置信度缩放；stock主体显式核验，未知四要素不硬覆盖，软cap仅空头 | ai_modifier.py、event_layer.py、orchestrator.py；test_signal_authority_iss117、test_iss117_completion |
| A07 invalidate兜底 | 删除跨字段子串红线兜底；AI红线候选不制造硬invalidate或强制F；规则硬排除保持 | benzong/auto_scorer.py、dimensions/risk_deduction.py；test_signal_authority_iss117 |
| A08 投票归一化 | E固定分母；严格注册身份/版本/独立时点；A授权、P展示、C独立；同源预算；有仓真增ADD，HOLD保仓位，独立退出不受弱普通票吞掉 | decision_engine.py、strategy_layer.py；test_s2a_aggregation、test_iss117_completion |
| A09 关键词模式 | 产业关键词不强制mode；预览=draft；旧mode/期限/初始止损保留，更新预览与明确接受 | benzong/self_reliance.py、trade_plan/generator.py；test_iss117_completion |
| A10 失效三态 | Guard/Adjuster共用完整OR/AND及TRUE/FALSE/UNKNOWN；缺指标/阈值/过期/未知文本待复核，合法独立退出保持 | trade_plan/conditions.py、adjuster.py、plan_guard.py；test_iss117_completion、test_trade_plan |
| A11 价格/执行 | 非有限非正价格不能触发授权；实时缺盘口/订单/可卖量UNKNOWN，核验事实才BLOCKED/ELIGIBLE；退出意图保留，回测明示模拟 | execution_layer.py、analysis_service.py、plan_guard.py；test_iss117_completion |
| A12 经验信号越权 | 经验信号与30+标签仅研究；硬消费核结构化发现、白名单、证券、时点及质量；三倍资格日历只读严格完整覆盖 | exit_signals/、plan_guard.py、calendar_client.py；test_iss117_qr3_closeout、test_iss117_qr4_coverage、test_iss117_qr5_integrity |
| A13 影子桥接 | 同源发现/权限/执行资格贯穿Guard、packet、JSONL与shadow_v13；路径或bool不制造硬事实；旧分桶；完整提醒/追溯与待办过期 | shadow_diff.py、decision_contract.py、evidence.py、portfolio.py；test_iss117_completion、test_n1_canonical_arms、test_m0_account_terminal |

## 验证与边界

2026-10-12严格离线全量1818 passed、0 failed、2 skipped、1 deselected、1 warning（314.45s）；随后旧docstring复验22 passed、0 warning，2026-10-13纯warning map与completion补验72 passed、0 failed。计数分别报告，原始完整最终运行段和最新指纹见当前验收及索引。

此前独立reviewer只读源码与正式用例，未运行测试，未发现其所审范围内的确定P0/P1阻断；该结论不覆盖10-13后续独立运行发现的行动说明及止盈抑制缺陷，最新审批见[R15当前记录](../iteration9/R15_ACCEPTANCE.md)。真实券商快照、外部resolver生产全链及投资效果未验证；默认external resolver fail-closed。未改笨总评分输入与CACHE_VERSION；权限预算协议iss117-s3-v1，影子shadow_v13。分析仅记录观察/建议，实际成交仍经确认写入。

7项保护哈希及知识库集合在保留的隔离运行段中不变；本次整理仅更新源码/测试里的失效文档指针及说明，执行语法树不变，未改产品逻辑、配置、真实账户或知识库。此前metadata外部漂移不归因本批。原已验范围不等于融合整体验收，2026-10-13行动说明/止盈抑制尾项局部重开，集中按第九轮计划补修；K1未冻结、M2未放行、capture_only不变，P批另行独立审批。
