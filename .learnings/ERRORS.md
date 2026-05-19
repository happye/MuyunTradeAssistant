# Errors

Command failures and integration errors.

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