# Muyun Repository Instructions

This repository is a Windows-first Python CLI project for an AI-assisted A-share trading workflow. Prefer repository-specific guidance over generic Python habits when they conflict.

## Core working mode

- Treat non-obvious failures as a workflow event, not just a bug to patch. This is mandatory, not optional.
- Before continuing after a meaningful failure, first consult the `self-improvement` skill and follow that workflow.
- Before major tasks, repeated bug classes, or after user workflow corrections, review the relevant entries in `.learnings/LEARNINGS.md` and `.learnings/ERRORS.md` if they exist.
- After resolving a non-obvious issue, record a structured learning in `.learnings/` using the self-improvement skill format, link related entries with `See Also`, and surface the prompt `Should I log this as a learning?` in chat.
- Do not rely on short-term conversation memory alone. Re-state and re-check this workflow when the task changes significantly or after long debugging sessions.

## Repo shape

- Main code lives in `src/`.
- `src/core/` contains the layered trading pipeline: signal, decision, event, AI modifier, strategy, execution, and backtest/orchestration.
- `src/scanner/` contains the market scan path: `market_cache.py`, `scanner_engine.py`, `scanner_filter.py`, and YAML rule files.
- `src/data/` contains external data clients, models, portfolio persistence, and replay feeders.
- `src/chat/` contains the chat agent, tool wiring, prompt schemas, and text formatting.
- `src/skills/` contains YAML strategy skill definitions. Prefer existing skills and layer boundaries over adding new top-level abstractions.
- `tests/` mainly uses direct script-style test files run with Python, not a heavy pytest workflow.

## Development rules for this repo

- Do not add new architectural layers unless absolutely necessary. Tighten existing `Decision`, `Strategy`, `Execution`, `Scanner`, and data-source boundaries first.
- Prefer root-cause fixes over cosmetic fallbacks, but keep changes narrow and behavior-preserving.
- AI features in this repo should remain research, interpretation, explanation, and ranking aids. Do not silently expand AI into the primary trading trigger path.
- Preserve explicit trading semantics such as `ENTRY`, `ADD`, `HOLD`, `TRIM`, `EXIT`, and `STOP`.
- For scanner work, preserve the unified theme-query UX: rule names match first; otherwise input is treated as one or more theme terms split by English commas.
- When external data sources fail, prefer graceful degradation and explicit user-facing fallback messages over hard failure.

## Validation rules

- Use the local virtual environment explicitly on Windows: `.\.venv\Scripts\python.exe`.
- Prefer focused script validation for the touched slice before wider checks.
- Common validated test style in this repo is:
	- `.\.venv\Scripts\python.exe tests\test_scanner_concept_filter.py`
	- `.\.venv\Scripts\python.exe tests\test_strategy_layer_sell_split.py`
	- `.\.venv\Scripts\python.exe tests\test_backtest_reporter_sell_path_fallback.py`
- For CLI validation, prefer direct module execution such as `.\.venv\Scripts\python.exe -m src.cli.main ...`.
- `start.bat` and `start.py` are user-facing entry paths and should stay aligned with CLI behavior.

## Data-source and environment notes

- This repo already uses multiple free market-data sources with fallback behavior. External failures are common and should be treated as upstream instability first, not immediate proof of a local logic bug.
- `MarketCache` currently handles all-market scan data separately from board and constituent lookups. Board-list endpoints can fail independently from realtime quote and historical K-line paths.
- Windows PowerShell quoting is a common source of false-negative diagnostics. Prefer simple commands, temporary files, or PowerShell-safe quoting when probing runtime behavior.

## File and secret hygiene

- Do not commit secrets from `configs/settings.yaml`.
- Treat `.learnings/` as shareable project knowledge when entries are concise, sanitized, and useful across sessions or machines. Do not treat ad-hoc `.txt`, temporary `.json`, or local debug artifacts as normal source files unless the task explicitly requires them.
- Keep documentation responsibilities split: `README.md` for architecture and technical boundaries, `使用手册.md` for user-facing operation, and milestone docs for delivery status.

## Search discipline

- Trust these instructions first and only widen search when they are incomplete or contradicted by the current code.
- Start from the nearest owning file or test, make the smallest grounded edit, then validate immediately.