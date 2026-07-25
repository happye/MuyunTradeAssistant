# CLAUDE.md - 暮云思辨投资助手

> 本文件是 Claude Code 的项目专属配置。**通用规则（架构/技术栈/踩坑警示/数据源/当前状态/文档索引）见 [AGENTS.md](AGENTS.md)**，本文件不重复，只补 Claude Code 特有内容。
>
> 多工具协同：Codex 见 `CODEX.md`（待补）、VS Code Copilot 见 `.github/copilot-instructions.md`、通用标准见 [AGENTS.md](AGENTS.md)。各工具文件引用 AGENTS.md 作为单一信源，只加工具特有配置。

## 工作流

- 非平凡改动先进 plan mode（Shift+Tab），方案确认后再写代码。
- 大任务拆子任务，每次只做一个，用 TaskCreate 跟踪进度。
- `.claude/plans/` 存放 plan mode 产出的实施方案，跨会话可续。
- 重大变更前用 git checkpoint；改动窄而准，behavior-preserving 优先于重构。

## 运行与验证

| 场景 | 命令 |
|------|------|
| 交互式 REPL（日常） | `uv run python start.py` |
| CLI 单股分析 | `uv run python -m src.cli.main -l 600519` |
| 笨总评分 | `bz 600519` / `bz scan 氮化镓,钽电容` / `bz --check` |
| Web UI preview | preview_start 选 "web"（`uv run python -m src.web.app`，:5000，autoPort） |
| 测试（脚本式，非 pytest） | `.\.venv\Scripts\python.exe tests\test_xxx.py` |
| 依赖安装 | `uv sync` |

- Windows + 中文路径：git 操作用 `cmd /c` 前缀，PowerShell 直接执行 git 易出错。
- 推送前检查代理：`git -c http.proxy= push`（系统代理 127.0.0.1:7890 会 reset GitHub）。
- UI 改动用 preview_* 工具验证（console/logs/snapshot/inspect），不要让用户手动检查。

## 上下文管理

- **上下文重置 > 压缩**：长任务接近上限时，写交接文档 + 启新会话，而非在同一会话压缩。
- 会话开始先读：[AGENTS.md](AGENTS.md) -> [ISSUES.md](ISSUES.md) -> 最新 `docs/*_交接.md` -> `git log --oneline -10`。
- memory（`~/.claude/projects/.../memory/`）自动注入，无需手动读；用户偏好/项目方向看 MEMORY.md 索引。
- 阶段切换或会话收尾用 neat-freak skill 同步文档与记忆，避免知识腐烂。

## subagent 使用

- 代码定位/广度搜索：用 Explore（read-only，快）。
- 实现新功能/重构落地：用 code-developer。
- 把关 PR/找问题：用 code-reviewer（只读）。
- 架构方案设计：用 code-architect 或 Plan。
- 独立子任务优先 subagent，保护主上下文窗口；并行无依赖的搜索用单条消息多 Agent 调用。

## 渐进式扩展触发规则

- 两次搞错同一规范 -> 加入本文件或 AGENTS.md
- 反复输入同一 prompt -> 存为 Skill
- 三次粘贴同一流程 -> 封装为 Skill
- 数据不可见 -> 连接 MCP server
- 侧任务淹没输出 -> subagent 处理
- 每次自动发生 -> 写 Hook（`.claude/settings.json`）

## 项目硬约束（Claude Code 视角）

- **用户可感知**：任何改动必须让跑 `start.py` 时实测得到差异。看不见的改动 = 没做。commit 前自问。
- **改逻辑须失效缓存**：改评分/计算逻辑后必须 bump `CACHE_VERSION`（`src/core/benzong/cache.py`），否则改了不生效。
- **防矫枉过正**：修 bug 前先想会不会误伤已跑通逻辑；大盘级信号解个股级问题必翻车。
- **诚实交付**：不许"看不见的改动"，诚实标注局限，不假装覆盖。
- **已授权任务直接推送**：完成 + 验证后直接 commit+push，不问"要不要推送"。
- 踩坑警示（全角标点 / YAML 键名 / RAGDocument 字段 / 回测 point-in-time / 网络超时 / DeepSeek-V4 迁移等）见 [AGENTS.md 第五节](AGENTS.md)。

## 禁止事项

- 不要一次性实现整个大功能，拆 sprint。
- 不要跳过验证就声称完成（CLI/TUI 改动要实跑，Web 改动用 preview_* 验证）。
- 不要为"完整性"给回测 DataFeeder 填近期公告 / 当前基本面（注入未来信息）。
- 不要新增架构层，先收紧现有 Decision/Strategy/Execution/Scanner 边界。
- 不要 `git add -A`：仓库常有 portfolio.yaml / knowledge/index 等本地状态改动，只 add 本次相关文件。
