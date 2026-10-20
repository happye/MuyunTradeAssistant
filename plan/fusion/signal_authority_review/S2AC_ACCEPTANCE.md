# ISS-117 当前验收

更新2026-10-14，原专项/P尾项在v0.8.31收口；后续产品版本与M2证据见第九轮R15/STATUS。基线 HEAD `0b3fadd5b604df60f29f13a0149b4fa5806eb18d`。交付在工作树，未提交或推送。

**原S2a/S2b/S3保持接收；本次行动说明/独立止盈抑制尾项随P0/P1/P2补修过门，局部重开已关闭。** 父Agent隔离全量1962 passed/0 failed，定向131 passed含原19探针全绿；独立Sol/high审阅当前源/正式用例/实际日志并核38项指纹后PASS，未另跑测试。最新审批见[R15当前记录](../iteration9/R15_ACCEPTANCE.md)，统一实施入口为[第九轮计划](../iteration9/DELIVERY_PLAN.md)。原专项范围见[计划及关闭记录](S2AC_FINAL_CLOSEOUT_TASK.md)，行为映射见[实施报告](IMPLEMENTATION_REPORT.md)，合同见[S2设计](S2_DESIGN.md)。

当前独立复验与统一补修由[原第九轮R15续接](../iteration9/R15_ACCEPTANCE.md)和[唯一实施计划](../iteration9/DELIVERY_PLAN.md)承接；不重批已通过日期/身份/预算合同，不回滚正确UNKNOWN或隐藏完整提醒。下列1818及72属于此前验证范围，不能替代新增失败场景验收。

## 验证证据

| 执行者与范围 | 结果 | 原始日志 |
|---|---|---|
| 实现方，2026-10-12严格离线全量 | 1818 passed、0 failed、2 skipped、1 deselected、1 warning，314.45s | [最终全量段](QR4_FULL_OFFLINE_RESULTS.txt) |
| 实现方，旧docstring warning修复后单文件复验 | 22 passed、0 warning，0.17s | [增量日志](S2AC_INDEPENDENT_TARGETED_RESULTS.txt) |
| 父Agent，2026-10-13纯warning map与completion隔离补验 | 72 passed、0 failed，3.88s | 同一增量日志 |

全量早于纯warning map补验，72不并入1818。completion正式用例66项；S2a正式用例65项。上述原始段均保留7项保护哈希和知识库文件集合未变的运行校验；当前源码/测试及保护指纹见[S2AC_REVIEW_INDEX.json](S2AC_REVIEW_INDEX.json)。此前 `knowledge/index/embedder_metadata.json` 的外部状态漂移不归因于本次整理，也未恢复该文件。

## 接收范围与限制

接收固定分母与A/P/C分离、严格时点及身份资格、合法独立退出、有仓ADD与HOLD保留未知/已知仓位、选中来源贯穿记录、持有政策与完整三态、真实/模拟执行资格、同源预算及结构化授权、影子与提示追溯。旧错误技能名夹具不作为真实账户事故证据；当前正式回归使用真实技能身份。

真实券商执行快照、外部resolver生产全链、投资效果未验证。默认external resolver未装配，缺证据fail-closed；隔离自检不等于独立测试执行。未查询真实行情/AI/账户，未迁移账户。

原范围及本次尾项均已接收；**K1 shadow_v14观察合同冻结，后续M2有限生产l/la只读接线已按R15过门，capture_only不变**。当前G3及长期融合验收仍按根MILESTONES和plan/fusion/STATUS执行。下列历史1818/72与P批1962/131分别核算；后续M2的1997/68另有实际运行及独立核指纹，不能倒归原专项。
