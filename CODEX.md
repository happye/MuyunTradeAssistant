# CODEX.md — 暮云思辨投资助手 Codex 专用配置

当前进度与下一步见 [RESUME](plan/fusion/RESUME.md)；当前计划与任务卡见 [第十一轮计划](plan/fusion/iteration11/PLAN.md)；**协作流程以 [WORKFLOW](plan/fusion/WORKFLOW.md) 为准**（2026-10-28 起，本文件与之冲突的流程约定以 WORKFLOW 为准）。DSH 工具层问题与需求见 [工具需求清单](plan/fusion/CODEX_DSH_TOOL_REQUESTS_20261028.md)。

> **单一信源是 [AGENTS.md](AGENTS.md)**——Codex CLI 会自动读取仓库根目录的 AGENTS.md，
> 本文件不重复其内容（架构/命令/数据源/踩坑警示以 AGENTS.md 为准），只补充 Codex 特有的操作说明。
> 多工具协同：Claude Code → `CLAUDE.md`，VS Code Copilot → `.github/copilot-instructions.md`。

## Codex 特有注意点

新对话和交付时必须执行[跨会话进度流程](plan/fusion/PROGRESS_CONTRACT.md#7-跨会话跨工具恢复与看板更新)：从共享JSON/RESUME恢复当前任务，运行进度校验；状态/证据改变后更新账本并重生成看板。不要依赖本聊天隐含记忆或复制一个私人进度表。

**当前指派（2026-10-25；流程 2026-10-28 按 WORKFLOW 修订）**：用户明确恢复Codex与DeepSeek Harness协作。Codex负责合同、审批、独立验收和Git，原生DSH负责实施；按需使用Codex Subagent核架构和审查。每张卡在仓库外的 detached 专用代码工作树中绑定新的 DSH task（WORKFLOW 第 8 节），不重投未知请求；测试分层与卡终全量一次按 WORKFLOW 第 5 节，主控复核有效证据，不复制未失效全量。任务与下一卡以[第十一轮计划](plan/fusion/iteration11/PLAN.md)与共享账本为准；main、精确暂存与保护用户状态的纪律保持。2026-10-24至本次改派前的“仅Codex”记录只作历史。另一台机器上由用户指派的 Claude Code 主控按同一 WORKFLOW 推进，同一时间只有一个主控推进同一张卡。

### 本项目融合架构任务的 Codex 持久化职责

适用范围：用户当前指派 Codex 承担暮云融合项目总架构师的会话；不约束 Claude Code 或其他实施 Agent，用户后续明确改派任务时以新指派为准。

- 职责：深入代码审查、隔离探针、独立验收、架构裁决与可执行任务卡。用户已于2026-10-17指定由DSH实施、Codex管理审批；实施方以用户最新指派为准。未经用户改派，不直接修改产品代码、测试、配置和真实账户。
- 当前DSH协作入口为个人`codex-dsh-collab` Skill及`G:/Tools/ChatGPTRepo/codex-dsh-双端联通/bin/codex-dsh.mjs`；先读个人Skill同目录`local-settings.json`及工具`docs/manuals/codex-guide.md`，所有命令复用配置中的共享绝对`--state-dir`，不因换工作区重新询问或另建账本。当前Codex聊天管理审批，原生DSH执行；旧`G:/Tools/DeepseekHarness/codex-dsh/`仅作历史证据，不是当前入口。接续身份与验收见`plan/fusion/iteration9/DELIVERY_PLAN.md`末节；未知提交先resume，不盲目重发，DSH报告不能自证通过。工具不提供文件沙箱、事务合入、费用硬上限或聊天结束后的常驻主控；未获迭代指派不启动G7/v43。工具源码仓库为 https://github.com/happye/codex-dsh 。按 WORKFLOW 第 8 节使用：执行目录为专用代码工作树；测试用工具自带 testPlan/run/receipt 并加 `--output-budget-bytes 16384`，仓库只记 runId 与结论；不用 stop 做额度交接。
- 恢复顺序：`AGENTS.md` → `plan/fusion/RESUME.md` 当前段 → `plan/fusion/iteration11/PLAN.md` → `plan/fusion/PROGRESS.md` → 当前卡合同与最近回执；每次先查 HEAD/工作树，按源码指纹复核受影响模块。`MILESTONES.md`/`STATUS.md` 只在需要 G/T 定义或状态时读。
- 持续维护长期里程碑：用户要求先固定目标/架构/完成标准，再按依赖验证小步设计；每批任务须映射G/T，审批后按 WORKFLOW 6.3 同步（每卡必改 4 处，MILESTONES/STATUS 只在 G/T 状态变化时改）。必要迭代/优化/重构未完成前不宣称整体结项，不一次铺开未经验证的远期细节。
- `AGENTS.md` 是共同规范，只能放工具中立规则与指针；Codex 身份和续接指令写在本文件，跨会话进度写入明确标注范围的 RESUME。
- 新会话交接固定复用 `plan/fusion/RESUME.md`，不另建重复的逐会话交接文档。读取后先用简短摘要核对职责、最新独立审批、当前阻断和下一动作，再继续任务；以磁盘现状及用户最新指派为准，不假定上个聊天的上下文已自动继承。
- DSH按受委派的实施subagent协作：Codex下发目标、文件所有权、基线branch/HEAD、验收条件与禁止范围，DSH交付实际改动/验证/限制，Codex独立审批和Git集成。此为职责分工，不宣称DSH已变成原生嵌套子Agent；默认不递归派发。
- DSH task/session：一卡一 task（工具合同绑定执行目录 HEAD，提交即断链——见工具需求清单 K0）。新 Codex 聊天先用 recover/resume 核对上一卡 task 的终态与副作用，未知请求不重投；SUBMISSION_UNKNOWN/UNKNOWN 意图只读核实后作废该 task，下一卡新 task。task/session/request id、stopAttempt 等运行态只记在本机（DSH state-dir 或本机笔记），不写入仓库 RESUME（WORKFLOW 7.5）。用户指定session时先核目录、活动和已有task映射；当前CLI附加旧session不核cwd，不能靠换填`--cwd`切目录；须独立核原生header。实际写任务前补读与范围相关的AGENTS约束。
- 收尾维护：按 WORKFLOW 6.3 更新回执、台账、DELIVERY 验收段与 RESUME 当前段；保留历史受审结果。新会话若已有实施方新交付，先做增量验收；若无新交付，不重复跑已知反例或擅自代替实施方改产品。

### 环境与操作

1. **Shell 环境**：使用当前PowerShell和正确引用的绝对路径；中文输出显式UTF8。不要为已正常的Git调用强制叠加cmd或改写POSIX路径。
2. **Python 入口**：统一用 `./.venv/Scripts/python.exe`（Windows 路径）或
   `uv run python`；不要用系统 Python。离线全量以 `pytest.ini` 和 `tests/README.md` 为准：
   `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`；脚本式场景按各文件头说明单独执行。
3. **任务范围**：按用户当前指派自主完成可逆工作；不要用过期的工具能力描述或不存在的确认流程阻塞已经授权的审查/设计。产品代码实施仍遵守上面的 Codex 架构师分工。
4. **网络纪律**：金融数据接口必须带超时与降级（见 AGENTS.md §五）。GitHub路由按实际连通性判断，URL级代理优先于通用`http.proxy`；2026-10-20收尾直连失败、现有`127.0.0.1:7890`代理可达，Git命令同时临时指定两项代理后成功。不要据旧结论固定禁用代理，也不改用户全局配置；金融数据的绕代理规则另按AGENTS执行。
5. **提交纪律**：commit message 用中文详细描述（AGENTS.md §六）；**禁止 `git add -A`**——
   `portfolio.yaml`、`knowledge/index/*`、`.claude/`、`项目记忆资产备份/` 是本地状态文件，
   只 add 与本次改动相关的文件。
   用户明确本项目单人维护，默认直接在`main`完成开发、验收与推送，不自动创建功能分支或PR，不把合并留给用户。重大改动验收后集中commit+push，由Codex负责Git，DSH合同明确git_owner。远端领先时先核差异并处理冲突，可用临时detached检出验证，DSH 执行目录可用仓库外的 detached 专用工作树（WORKFLOW 第 8 节），但不另建命名分支；不强推、不覆盖未提交账户/知识库。协作工具代码只交付其独立仓库。

## 最高优先级红线（速查，详见 AGENTS.md）

- 改评分/计算逻辑必须 bump `CACHE_VERSION`（src/core/benzong/cache.py），否则改动不生效
- 回测绝不注入未来信息（公告/当前基本面不进 DataFeeder；live 门控模式见 ISS-052/ISS-063）
- 改动必须让用户跑 `start.py` 时可感知（AGENTS.md §二·五）
- 不新增架构层；先收紧既有 Decision/Strategy/Execution/Scanner 边界
