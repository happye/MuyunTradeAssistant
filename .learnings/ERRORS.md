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