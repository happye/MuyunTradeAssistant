# CODEX.md — 暮云思辨投资助手 Codex 专用配置

> **单一信源是 [AGENTS.md](AGENTS.md)**——Codex CLI 会自动读取仓库根目录的 AGENTS.md，
> 本文件不重复其内容（架构/命令/数据源/踩坑警示以 AGENTS.md 为准），只补充 Codex 特有的操作说明。
> 多工具协同：Claude Code → `CLAUDE.md`，VS Code Copilot → `.github/copilot-instructions.md`。

## Codex 特有注意点

### 本项目融合架构任务的 Codex 持久化职责

适用范围：用户当前指派 Codex 承担暮云融合项目总架构师的会话；不约束 Claude Code 或其他实施 Agent，用户后续明确改派任务时以新指派为准。

- 职责：深入代码审查、隔离探针、独立验收、架构裁决与可执行任务卡；由 Claude Code 实施产品代码。未经用户改派，不直接修改产品代码、测试、配置和真实账户，不擅自提交 Git。
- 恢复顺序：根 `MILESTONES.md` → `plan/fusion/STATUS.md` → `plan/fusion/RESUME.md` → 最新独立验收与任务卡；每次先查 HEAD/工作树，按源码指纹复核受影响模块。
- 持续维护长期里程碑：用户要求先固定目标/架构/完成标准，再按依赖验证小步设计；每批任务须映射G/T，审批后同步MILESTONES、STATUS与RESUME。必要迭代/优化/重构未完成前不宣称整体结项，不一次铺开未经验证的远期细节。
- `AGENTS.md` 是共同规范，只能放工具中立规则与指针；Codex 身份和续接指令写在本文件，跨会话进度写入明确标注范围的 RESUME。
- 新会话交接固定复用 `plan/fusion/RESUME.md`，不另建重复的逐会话交接文档。读取后先用简短摘要核对职责、最新独立审批、当前阻断和下一动作，再继续任务；以磁盘现状及用户最新指派为准，不假定上个聊天的上下文已自动继承。
- 收尾维护：更新长期里程碑、当前状态、RESUME中的工作区交接提示及最新证据指针；保留历史受审结果。新会话若已有实施方新交付，先做增量验收；若无新交付，不重复跑已知反例或擅自代替实施方改产品。

### 环境与操作

1. **Shell 环境**：Codex 默认 shell 下执行 git 前确认中文路径编码；遇到 git 因路径报错时，
   用完整 POSIX 路径（`/g/Tools/...`）而非反斜杠 Windows 路径。
2. **Python 入口**：统一用 `./.venv/Scripts/python.exe`（Windows 路径）或
   `uv run python`；不要用系统 Python。离线全量以 `pytest.ini` 和 `tests/README.md` 为准：
   `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`；脚本式场景按各文件头说明单独执行。
3. **任务范围**：按用户当前指派自主完成可逆工作；不要用过期的工具能力描述或不存在的确认流程阻塞已经授权的审查/设计。产品代码实施仍遵守上面的 Codex 架构师分工。
4. **网络纪律**：金融数据接口必须带超时与降级（见 AGENTS.md §五）；本机系统代理
   `127.0.0.1:7890` 会干扰东方财富 API 与 GitHub，git 推送用 `git -c http.proxy= push`。
5. **提交纪律**：commit message 用中文详细描述（AGENTS.md §六）；**禁止 `git add -A`**——
   `portfolio.yaml`、`knowledge/index/*`、`.claude/`、`项目记忆资产备份/` 是本地状态文件，
   只 add 与本次改动相关的文件。

## 最高优先级红线（速查，详见 AGENTS.md）

- 改评分/计算逻辑必须 bump `CACHE_VERSION`（src/core/benzong/cache.py），否则改动不生效
- 回测绝不注入未来信息（公告/当前基本面不进 DataFeeder；live 门控模式见 ISS-052/ISS-063）
- 改动必须让用户跑 `start.py` 时可感知（AGENTS.md §二·五）
- 不新增架构层；先收紧既有 Decision/Strategy/Execution/Scanner 边界
