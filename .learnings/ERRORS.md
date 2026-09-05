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

## [ERR-20260904-001] bz scan 启动期误报 8 条「未知操作符 starts_with/contains」

**Logged**: 2026-09-04T00:30:00+08:00
**Severity**: low（纯告警误报，运行时行为正确）
**Status**: resolved

### Symptom
`bz scan shrink_pullback` 启动期连出 8 条 WARNING：
```
规则'global_exclude'过滤器校验失败: 过滤器#1: 未知操作符 'starts_with'，可用操作符: ['between', 'eq', ...]
```
（4 条 starts_with + 1 条 contains，×2 轮：`run_benzong_scan` 先建一个 ScannerEngine 解析规则名再建一个真扫描。）

### Root Cause
ISS-078（v0.8.8.2）给 `_load_rules` 接的加载期校验，对**所有 section** 用 `validate_filters`（普通 filters 的 `VALID_OPS`），而 `global_exclude` 段的执行器是 `apply_global_exclude`——合法支持 starts_with/contains/is_nan。校验器操作符集与执行器不匹配 → 合法配置误报；告警文案「该规则筛选变宽松」是假的（运行时排除一直正常）。

### Fix
`ScannerFilter.validate_excludes`（EXCLUDE_VALID_OPS 专属操作符集）校验排除段；`validate_filters` 只管规则 filters 段。回归测试 `tests/core/test_scan_rules_validation.py` 4 项：真实 YAML 零误报 + 真拼错操作符（规则段和排除段各一处）仍告警。v0.8.8.3。

### Prevention
校验器与执行器成对同步（见 LRN-20260904-001）；配置校验的回归测试必须用线上真实配置文件做断言。

### Metadata
- Reproducible: yes（v0.8.8.2 上跑任意 bz scan / --scan 即可复现）
- Related Files: src/scanner/scanner_engine.py, src/scanner/scanner_filter.py, src/scanner/scan_rules.yaml
- Tags: scanner, yaml-validation, false-alarm

---

## [ERR-20260905-001] expect 命令财报披露 Length mismatch 英文告警

**Date**: 2026-09-05
**Symptom**: `expect 60` 输出 `data_provider.calendar.disclosure 失败: ValueError:
Length mismatch: Expected axis has 0 elements, new values have 10 elements`，
每次执行都重复出现（不缓存），英文原文未命中人话映射直接透传吓用户。

### Root Cause
巨潮预约披露接口对**尚未发布预约表的报告期**返回空列表（`prbookinfos: []`），
akshare `stock_report_disclosure`（stock_yjyg_cninfo.py）对空数据直接
`temp_df.columns=[10个列名]` → 0 列 DataFrame 赋 10 列名必崩。触发窗口=每年
「预约表未发布的强制披露期」（实测 2026-09 的 2026三季；5-6 月半年报表同理）。
次要问题：`get_stock_disclosure` 的 `df.empty → return []` 早退绕过函数尾部的
缓存写入——空结果从不缓存，每次 expect 重复打接口。

### Fix
`calendar_client._fetch` 仅对 Length mismatch 签名 catch 转 `pd.DataFrame()`
（"该期间无预约表"是合法业务空态非故障；其余异常原样上抛 `_safe_call` 告警，
防 fail-open 吞真故障）+ `empty/None` 分离：空态入缓存、真故障（None）不缓存
保持重试语义。v0.8.8.4。回归 `tests/core/test_calendar_empty_period.py`：
空态三断言（[]/无 WARNING/二次命中缓存）+ 真故障守卫（超时仍告警）。

### Prevention
上游 akshare 对"合法业务空态"常以崩溃表达（0 列赋列名是惯犯模式）——对接
akshare 的包装层要把"接口返回空"翻译成业务空态而非告警；翻译必须**按异常签名
收窄**并保留真故障告警路径，不许裸 except。缓存语义要区分"空结果"（可缓存）
与"失败"（不缓存）。

### Metadata
- Reproducible: yes（v0.8.8.3 上跑 `expect 60`，9 月-三季表未发布窗口内必现）
- Related Files: src/data/calendar_client.py, .venv/.../akshare/stock_feature/stock_yjyg_cninfo.py
- Tags: akshare-upstream, empty-state, cache-semantics, fail-open-guard
