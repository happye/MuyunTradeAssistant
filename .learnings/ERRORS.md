# Errors

Command failures and integration errors.

---

## [ERR-20260718-001] uv_run_pytest

**Logged**: 2026-07-18T00:15:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
`uv run pytest` 走系统 python（.venv 无 pytest），导致 import 第三方包（textual）失败。要用 `uv run python -m pytest` 走 .venv python。

### Error
```
ModuleNotFoundError: No module named 'textual'
```

### Context
- .venv 装了 textual（uv pip install textual），但没装 pytest（历史测试用系统 pytest）
- `uv run pytest` 找到系统 PATH 的 pytest.exe（系统 python，无 textual）
- `uv run python -c "import textual"` 成功（用 .venv python）
- 已补装 pytest==9.1.1 + pytest-asyncio==1.4.0 到 .venv（requirements.txt 已加）

### Suggested Fix
测试统一用 `uv run python -m pytest tests/...`（强制 .venv python + pytest 模块），不用 `uv run pytest`。

### Metadata
- Reproducible: yes
- Related Files: tests/test_tui.py, requirements.txt
- Tags: uv, pytest, venv

---

## [ERR-20260515-001] run_in_terminal

**Logged**: 2026-05-15T15:40:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
VS Code 终端内已有交互式 CLI 占用时，run_in_terminal 的一次性诊断命令会被当前程序吞掉并误解析为业务输入。

### Error
```text
所有股票深度分析均失败
输入代码深度分析(如: 600546 002192) 或 all(全选) 或 q(跳过)
> import requests, json
```

### Context
- Attempted to run a one-shot Python probe to inspect the Sina market API response.
- The persistent shell had an interactive scanner/deep-analysis session still attached.
- Probe text was consumed as CLI input and triggered unrelated Baostock and scanner errors.

### Suggested Fix
在存在交互式终端残留时，优先使用代码静态检查、单文件诊断，或显式切换到干净终端再做运行验证。

### Metadata
- Reproducible: yes
- Related Files: src/scanner/market_cache.py

---

## [ERR-20260519-001] git commit

**Logged**: 2026-05-19T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell 下多段中文 commit message 首次执行时引号拼接失败，触发 pathspec 错误。

### Error
```text
error: pathspec '看不到买卖点' did not match any file(s) known to git
```

### Context
- Attempted to run git commit with multiple -m arguments containing Chinese punctuation via terminal wrapper.
- First invocation had malformed quoting.
- Retried with corrected quoting and commit succeeded.

### Suggested Fix
PowerShell 中提交复杂多段消息时，优先使用单引号包裹每个 -m 内容；失败后立即重试并保留最小可复现错误。

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md
- See Also: ERR-20260515-001

---

## [ERR-20260521-001] main.py syntax regression

**Logged**: 2026-05-21T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: tests

### Summary
The sell-point display edit accidentally truncated the `pm.suggest_update(...)` call in `src/cli/main.py`, leaving an unmatched closing parenthesis.

### Error
```text
SyntaxError in main.py at line 565: unmatched ')'.
```

### Context
- Attempted to revise portfolio sell-point text to show清仓/减仓 meaning.
- The surrounding `pm.suggest_update(...)` block was partially overwritten.
- Syntax check on `src/cli/main.py` failed immediately after the edit.

### Suggested Fix
Restore the full `pm.suggest_update(pos.stock_code, stock_data.stock_name, strategy_decision, stock_data, console)` call and rerun syntax validation.

### Metadata
- Reproducible: yes
- Related Files: src/cli/main.py

---

## [ERR-20260525-001] run_in_terminal powershell exe invocation

**Logged**: 2026-05-25T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
通过 `run_in_terminal` 在 PowerShell 中直接执行绝对路径的 `python.exe` 时，未加调用运算符或未加引号会触发命令解析错误。

### Error
```text
The term 'c:\Project\Muyun\.venv\Scripts\python.exe' is not recognized as the name of a cmdlet...
The ampersand (&) character is not allowed...
```

### Context
- Attempted to run focused Python validation commands against the project virtualenv.
- Plain absolute executable path failed in PowerShell parsing under the terminal wrapper.
- Using `& "c:\Project\Muyun\.venv\Scripts\python.exe" ...` succeeded and produced the expected validation output.

### Suggested Fix
在 PowerShell 的 `run_in_terminal` 命令里，执行绝对路径 exe 时统一使用带引号的调用形式：`& "...\python.exe" script_or_args`。

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md
- See Also: ERR-20260515-001

---

## [ERR-20260526-001] rag evaluator offline model init

**Logged**: 2026-05-26T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
运行 RAG 评估脚本时，脚本先尝试初始化在线嵌入模型，离线环境会在指标计算前直接失败。

### Error
```text
RAG服务初始化失败: 嵌入模型加载失败: We couldn't connect to 'https://hf-mirror.com' to load the files, and couldn't find them in the cached files.
错误: RAG初始化失败
```

### Context
- Attempted to validate updated labels with `.\.venv\Scripts\python.exe tests\rag_eval\evaluator.py`.
- The script tried to initialize the full RAG service before reading existing retrieval results and labels.
- Current environment had no cached model files and could not reach the Hugging Face mirror.

### Suggested Fix
给评估脚本增加离线模式或纯指标模式：在已有检索结果和标签文件存在时，跳过 RAG 初始化，仅执行指标计算与报告生成。

### Metadata
- Reproducible: yes
- Related Files: tests/rag_eval/evaluator.py
- See Also: ERR-20260525-001

---

## [ERR-20260724-001] powershell_nested_command

**Logged**: 2026-07-24T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell parsed an embedded Python `-c` command instead of passing it through to `cmd /c` because nested quoting was escaped for the wrong shell.

### Error
```text
The 'from' keyword is not supported in this version of the language.
```

### Context
- A validation command used nested PowerShell, `cmd`, and Python quoting.
- The Python source was interpreted by PowerShell before `cmd` received it.

### Suggested Fix
Use separate short commands or a temporary script when validating Python expressions from PowerShell; avoid nested escaped quotes.

### Metadata
- Reproducible: yes
- Related Files: src/rag/embedding.py
- Tags: powershell, quoting, validation

---
## [ERR-20260830-001] git stash 中断导致 .git 引用层损坏

**Logged**: 2026-08-30T17:05:00+08:00
**Severity**: critical
**Status**: resolved

### Symptom
`git stash push` 执行中被中断（SIGTERM）后：`git status` → `fatal: bad object HEAD`；
`git fsck` → 全部 ref `invalid sha1 pointer`；`git cat-file -t <近期commit>` → `could not get object info`。

### Root Cause
- stash 的对象写入/引用更新非原子，进程中断留下半写状态；
- 2026-05 之后的 loose commit 对象损坏丢失（实测 650 对象仅 7 个 commit 可解析：
  2026-05-12 前历史 + 1 个 2026-07-16 WIP stash 快照 f27577f）。

### Rescue（无损修复，零文件删除）
1. 工作区文件核实完好（grep/测试逐项验证）
2. 旧 .git 整体归档 `.git.corrupted_20260830/`（另留 /tmp/d04_rescue/git_backup_dotgit）
3. `git init -b main` + 配回 origin + 全工作区快照 commit `0e9471c`（461 文件）
4. `git fsck --full` 零错误
5. 远端历史嫁接待用户凭据：`git fetch origin && git reset --soft origin/main && git commit && git push`

### Prevention
- agent 长任务链内禁裸跑 stash/checkout/reset/rebase；A/B 对照用文件级原地切换
  （备份→改→跑→还原→md5 校验，见 LRN-20260830-001）
- 仓库损坏先跑 `git cat-file --batch-all-objects --batch-check` 评估对象库存活——refs 坏 ≠ 对象丢，
  49 个 commit 对象在但仅 7 个可解析，说明逐个 parse 验证才能给出真实损失面

### Metadata
- Reproducible: yes（中断 stash 即可复现）
- Related Files: .git
- Tags: git, stash, corruption, rescue

---
