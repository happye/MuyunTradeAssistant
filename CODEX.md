# CODEX.md — 暮云思辨投资助手 Codex 专用配置

> **单一信源是 [AGENTS.md](AGENTS.md)**——Codex CLI 会自动读取仓库根目录的 AGENTS.md，
> 本文件不重复其内容（架构/命令/数据源/踩坑警示以 AGENTS.md 为准），只补充 Codex 特有的操作说明。
> 多工具协同：Claude Code → `CLAUDE.md`，VS Code Copilot → `.github/copilot-instructions.md`。

## Codex 特有注意点

1. **Shell 环境**：Codex 默认 shell 下执行 git 前确认中文路径编码；遇到 git 因路径报错时，
   用完整 POSIX 路径（`/g/Tools/...`）而非反斜杠 Windows 路径。
2. **Python 入口**：统一用 `./.venv/Scripts/python.exe`（Windows 路径）或
   `uv run python`；不要用系统 Python。测试是脚本式直跑，不是 pytest 断言式：
   `PYTHONUTF8=1 PYTHONPATH=. ./.venv/Scripts/python.exe tests/<目录>/<测试名>.py`
3. **无交互机制**：Codex 没有 Claude Code 的 plan mode / TaskCreate / preview 工具。
   非平凡改动按 AGENTS.md「Deep Analysis Requirements」在回复里给出分析→计划→等用户确认，
   不要跳过确认直接改。
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
