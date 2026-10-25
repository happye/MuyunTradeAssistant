# 暮云共享进度与跨会话接续

在本仓库使用Grok Build时，遵守根`AGENTS.md`及[共享进度流程](../../plan/fusion/PROGRESS_CONTRACT.md#7-跨会话跨工具恢复与看板更新)。规则只约束项目进度，不改变用户为当前会话指定的角色或权限。

- 新对话先读`plan/fusion/RESUME.md`与进度合同，核Git/工作树，用项目解释器运行`python -B -X utf8 scripts/check_progress.py --check`，再看当前任务段。
- `plan/fusion/PROGRESS.json`是唯一进度事实源；不要另建Grok专属百分比表，也不假定其他模型的聊天或私有memory自动共享。
- 开发/验收改变任务状态或证据后，由本批唯一账本写入者更新JSON/变更记录，再运行`--render`和`--check`。其他Agent只返任务ID与证据增量，避免覆盖并发修改。
- 交付报告列任务ID、行为、证据、前后进度及剩余/阻断；无证据不涨分。中断前同步RESUME。
- 看板是生成快照；生成后刷新浏览器查看。规则不是代码监视器，不因写文件、跑测试或换聊天就自动完成任务。
