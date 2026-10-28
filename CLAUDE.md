# CLAUDE.md — Muyun Investment Assistant (暮云思辨投资助手)

> This is the Claude Code project config. **General rules — architecture, tech stack, data sources, pitfalls, current status, and the docs index — live in [AGENTS.md](AGENTS.md) as the single source of truth.** This file does not repeat them; it adds Claude Code-specific configuration plus the universal agent operating principles that govern how work is done here.
>
> Multi-tool coordination: Codex → `CODEX.md`, VS Code Copilot → `.github/copilot-instructions.md`, shared standard → [AGENTS.md](AGENTS.md). Each tool file cites AGENTS.md as the single source and adds only tool-specific config.
>
> Note: AGENTS.md and most project docs are written in Chinese. Read them as-is.

## Persistent project progress across conversations

At each new conversation and batch handoff, follow [the shared progress lifecycle](plan/fusion/PROGRESS_CONTRACT.md#7-跨会话跨工具恢复与看板更新). Read `plan/fusion/RESUME.md`, check Git and run the project interpreter's `scripts/check_progress.py --check`, then read the active task. The shared `PROGRESS.json` owns progress; Claude auto-memory and chat history are not separate authorities. After status/evidence changes, the designated ledger owner updates JSON and runs `--render` then `--check` before delivery. Other agents return task IDs and evidence deltas; do not overwrite each other's ledger edits.

**Collaboration process:** [plan/fusion/WORKFLOW.md](plan/fusion/WORKFLOW.md) is the single source for how work is done here (card selection and caps, contract template, verification layering, lightweight receipts, the 4 must-update docs, quota/interruption rules, multi-machine rules). The current plan and task cards live in [plan/fusion/iteration11/PLAN.md](plan/fusion/iteration11/PLAN.md). Where this file and WORKFLOW disagree on process, WORKFLOW wins; product red lines stay in AGENTS.md. On this machine Claude Code acts as controller without DSH (WORKFLOW §9): subagents implement/review, the main agent owns contracts, acceptance, Git and the ledger.

## Before any non-trivial work: read the discipline skill

**Read `skills/muyun-dev-discipline/SKILL.md` first.** It is the tool-neutral master copy of this project's engineering discipline gates (fix-in-four-steps, same-class scanning, docs≠fix, verification-by-running, tuning red lines, pre-commit checklist). A synced copy also lives in `.claude/skills/muyun-dev-discipline/`.

Why this is called out explicitly: skill auto-discovery directories are **not** compatible across agent tools (Claude Code `.claude/skills/`, Copilot `.github/skills/`, Cursor `.cursor/rules/`, Codex `.codex/skills/`). This project already lost a mandatory rule that way — the `self-improvement` skill sat only in `.github/skills/`, so the rule "consult self-improvement after a failure" was never executed. Do not rely on directory auto-discovery alone; if you edit the master copy, run `bash scripts/sync-agent-skills.sh`.

## Role

You are Claude Code working inside a long-lived research/backtest codebase for A-share trading strategy (non-live-trading; research + decision support). Your job is to deliver correct, maintainable changes by moving in small, verifiable steps.

These operating rules apply to all **non-trivial** work — any change that touches more than one logical unit (function, class, or file), could break existing behavior, or requires design judgment. Single-character fixes, renaming a local variable, or adding a missing import are trivial and may be made directly without a stated hypothesis.

## Primary Principles

- **Think before coding.** State assumptions explicitly, surface tradeoffs, and ask when something is unclear.
- **Simplicity first.** Use the minimum code that solves today's problem; avoid speculative abstractions, configurability, or extra features.
- **Surgical changes.** Touch only what the request requires; do not refactor or reformat unrelated code. Behavior-preserving changes are preferred over refactors.
- **Goal-driven execution.** Define success criteria and turn the task into verifiable steps.
- **Anchor on concrete evidence.** A file, symbol, failing test, error message, or user report — never a guess.
- **Preserve existing behavior, style, and architecture** unless the user explicitly asks for a broader change. Do not add new architecture layers; tighten the existing Decision/Strategy/Execution/Scanner boundaries first.
- **When multiple interpretations exist, name them and ask** which one to use instead of guessing.

## Working Style

1. Gather only the minimum context needed to state a specific, testable cause — name the exact file, symbol, or condition to verify, and describe what passing or failing that check would mean.
2. State the likely cause or change target before editing when the task is ambiguous or risky.
3. Make the smallest plausible edit that tests that hypothesis.
4. Validate immediately after the first substantive edit with the cheapest useful check.
5. Iterate only when the result changes your understanding or exposes a local defect.
6. For multi-step work, write a brief plan with each step paired to a specific verification check.

For non-trivial changes, enter plan mode first (Shift+Tab) and confirm the approach before writing code. Store plan-mode outputs in `.claude/plans/` so they survive across sessions. Break large tasks into subtasks — one at a time — and track them with `TaskCreate`. Make a git checkpoint before major changes.

## Deep Analysis Requirements

Before changing code, you MUST perform deep, evidence-driven analysis:

1. Thoroughly read the relevant code and surrounding modules to understand responsibilities, contracts, and data flows.
2. Build an explicit call/dependency map (call graph, module dependency chain, hierarchy) to locate where behavior originates.
3. Trace execution and data flow to identify root causes — prefer fixing the root cause over superficial patches.
4. Before any edit, document the target component's logical architecture, assumptions, and boundary conditions, and list all necessary changes.
5. Produce a concise implementation plan and risk checklist; obtain confirmation before modifying code (plan mode for non-trivial work).

Avoid speculative edits — if uncertain, state assumptions clearly and enumerate follow-up investigations.

## Running & Verification

| Scenario | Command |
|----------|---------|
| Interactive REPL (daily use) | `uv run python start.py` |
| CLI single-stock analysis | `uv run python -m src.cli.main -l 600519` |
| Benzong scoring | `bz 600519` / `bz scan 氮化镓,钽电容` / `bz --check` |
| Expectation event calendar | REPL `expect` / `expect 60` (pre-event expectation overdraft + environment thermometer, v0.8.7) |
| Fear index | REPL `fear` / `fear history` / `fear backfill` (objective 0-100 panic gauge, zero AI, v0.8.10) |
| Scan review (validate scan picks) | REPL `scan review [days]` / `scan review import` (per-stock change vs CSI300 + sparkline path, zero AI, v0.8.11) |
| Watch pool | REPL `watch` / `watch add <code|#N>` / `watch rm` (auto-collects WATCH picks on analysis; review-style tracking since entry, v0.8.12) |
| Run diagnostics | REPL `doctor` (read-only env check: interpreter/deps/configs/state caches/RAG; zero AI, zero network, never echoes secrets, v0.8.16) |
| Analysis diff | REPL `diff <code>` (key-evidence changes across the last two deep analyses: price/score Δ, decision/signal flips; evidence auto-recorded per analysis, v0.8.17) |
| Batch task ledger | REPL `tasks` (progress & failed items of l all/la/ba batches; resume = rerun same command, v0.8.17) |
| Web UI preview | `preview_start` → "web" (`uv run python -m src.web.app`, :5000, autoPort) |
| Tests (pytest, offline by default since M1) | `pytest -q` (see tests/README.md for external/AI opt-in); script direct-run: `.\.venv\Scripts\python.exe tests\core\test_xxx.py` |
| Install dependencies | `uv venv .venv` + `uv pip install -r requirements.txt` (requirements.txt is the pinned source of truth; `uv sync` unavailable — no pyproject.toml) |

**Verification discipline:**

- After editing, run the most targeted validation available for the touched area. Prefer a narrow test, lint, build, or type check over a broad repo-wide check.
- UI changes must be verified with the `preview_*` tools (console/logs/snapshot/inspect) — never ask the user to check manually.
- If no targeted check exists, use the lightest practical validation and explain the gap. If no validation is available, state this explicitly before delivering, list the manual checks the user should run, and do not assert correctness.
- Do not claim success without evidence from a check, a compile, a test, or a clear file diff. If the user explicitly asks to skip verification, acknowledge the risk, proceed, and note in your response which checks were not run.
- CLI/TUI changes must be run for real; Web changes verified with `preview_*`. Skipping verification = not done.

**Environment caveats:**

- Windows + Chinese paths: prefix git operations with `cmd /c`; PowerShell executing git directly is error-prone.
- Before pushing, check the proxy: the global `.gitconfig` uses a URL-scoped proxy `http.https://github.com.proxy=127.0.0.1:7890`, so bypass it with `git -c http.https://github.com.proxy= push`. If the error says 'via 127.0.0.1 ... Could not connect', the proxy client is off (and direct access is usually blocked too) — start it first.

## Context Management & Memory

- **Reuse the existing handoff.** Before a context reset, update `plan/fusion/RESUME.md`; do not create another per-session handoff or duplicate archive.
- **Read order at session start:** [AGENTS.md](AGENTS.md) → [RESUME](plan/fusion/RESUME.md) current section → `scripts/check_progress.py --check` → [current plan](plan/fusion/iteration11/PLAN.md) (queue + active card contract) → Git worktree. Read [WORKFLOW](plan/fusion/WORKFLOW.md) before process work; read [MILESTONES.md](MILESTONES.md)/[STATUS](plan/fusion/STATUS.md) only when G/T definitions or status are needed. `ISSUES.md` provides issue detail; old handoffs and iteration 2–10 plans are historical evidence only.
- Auto-memory (`~/.claude/projects/.../memory/`) is injected automatically; consult `MEMORY.md` for user preferences and project direction — no need to read memory files manually.
- When a lesson, constraint, or decision matters for future work, persist it as a memory file (one fact per file, with frontmatter) and add a one-line pointer in `MEMORY.md`. Record the root cause, the fix, and the guardrail that prevents recurrence — do not repeat the same mistake.
- Revisit the current plan before making changes so prior decisions are not lost. Use the `neat-freak` skill to sync docs and memory at phase transitions or session end, preventing knowledge rot.

## Subagent Usage

- The user requests proactive delegation: before substantive work, identify bounded independent tasks that reduce the parent context or provide necessary verification, and dispatch the minimum useful set without waiting for another reminder. Keep short/dependent steps in the parent.
- Follow [AGENTS.md](AGENTS.md) and [agent-routing](docs/agent-routing.md), using roles and model IDs supported by this client; Codex model IDs are not Claude aliases. Assign file ownership, preserve others' work and verify actual model metadata rather than an agent's self-report. No recursive delegation or duplicate reviewers.

### Claude Code role routing (mandatory — never spawn a bare/default agent)

Every `Agent` call MUST name a project role via `subagent_type` (definitions in `.claude/agents/`, each pins its model). A call without a role inherits the main model (the most expensive) — that is a token-waste bug. If you must use a built-in type (`Explore`, `general-purpose`), pass an explicit `model` matching the table.

| Work | Role (`subagent_type`) | Model |
|---|---|---|
| Read docs, locate code, summarize progress/ISSUES/milestones | `muyun-explorer` | haiku |
| Run tests / commands / env checks, condense logs | `muyun-runner` | haiku |
| Track a long background job | `task-progress-monitor` | haiku |
| Write/edit code and tests per work order | `muyun-coder` | sonnet |
| Write/update docs (plan, STATUS/RESUME, ISSUES, README, manual) | `muyun-doc-writer` | sonnet |
| Routine diff review before commit | `muyun-reviewer` | sonnet |
| Split an approved card into work orders | `muyun-commander` | sonnet |
| Design decision / acceptance-contract design (rare) | `muyun-architect` | opus |
| High-risk review: concurrency, persistence, data identity, cost | `code-quality-guard` | opus |

Rules: opus roles only when the decision genuinely needs it, one at a time. Main agent = architect/integrator: owns plans, git commit/push, and final verification claims. Workers never commit. Each work order states goal, allowed files, reuse targets, acceptance command, and the three guard rules from `muyun-commander`.

## Quality Bar

- Aim for correctness first, then clarity, then completeness.
- Prefer existing abstractions, helpers, and conventions over new ones.
- Keep changes focused; avoid unrelated refactors.
- Remove dead code, duplication, and accidental complexity only when **all** are true: it has no callers in the codebase (verified by search), its removal requires changing only the single file already being edited, and no tests reference it.
- Add or update tests when behavior changes or when a regression could reasonably be introduced.
- A good diff contains only lines that trace directly to the user request.

## Decision Rules

- If the task is underspecified, ask the minimum number of clarifying questions needed to proceed safely.
- If multiple solutions are possible, choose the one easiest to verify and easiest to undo.
- If a requested change would touch several unrelated areas, split it into smaller steps.
- If a file or command fails, inspect the local cause before retrying with a different approach.
- Before fixing a bug, consider whether the fix could damage already-working logic. A market-level signal applied to a stock-level problem will always roll over — see [AGENTS.md §5](AGENTS.md) pitfalls.

## Communication

- Be concise and factual.
- Report what changed, why it changed, and how it was verified.
- Call out assumptions, risks, and remaining follow-up items explicitly.
- When blocked, say exactly what is missing and what the next actionable step is.
- **Language:** Conversational replies to the user are in Chinese (per user preference). Commit messages are in Chinese and detailed (per [AGENTS.md §6](AGENTS.md)). Code comments match the density and idiom of the surrounding code. This config file is in English per explicit request.

## Progressive Extension Triggers

- Same spec gotten wrong twice → add it to this file or AGENTS.md.
- Same prompt typed repeatedly → save it as a Skill.
- Same flow pasted three times → encapsulate as a Skill.
- Data not visible → connect an MCP server.
- Side tasks drowning the output → delegate to a subagent.
- Something happening automatically every time → write a Hook (`.claude/settings.json`).

## Project Hard Constraints

- **User-perceivable:** Every change must produce a measurable difference when running `start.py`. Invisible changes = not done. Ask yourself before every commit.
- **Invalidate cache when changing logic:** After changing scoring/calculation logic you MUST bump `CACHE_VERSION` (`src/core/benzong/cache.py`), otherwise the change won't take effect.
- **Anti-overfix:** Before fixing a bug, think about whether it could hurt already-working logic. Market-level signals for stock-level problems always roll over.
- **Honest delivery:** No "invisible changes." Honestly note limitations; do not pretend coverage you don't have.
- **Push authorized tasks directly:** After completing + verifying an authorized task, commit + push directly. Do not ask "should I push?"
- **No future-info injection:** Never fill recent announcements / current fundamentals into the backtest `DataFeeder` for "completeness" — it injects the future into the past.
- **Account reads and sizing:** `RequestAccountFacts.estimate_nav` defaults to `False`; pure `today`/`pos list` queries must not fetch valuation quotes. Analysis entry points opt in explicitly. `scripts/pipeline.py` reports research target ratios only: never derive available cash, share quantities, or order sizes from a target ratio and estimated NAV; keep them `UNKNOWN`.
- Pitfalls (full-width punctuation / YAML key names / RAGDocument fields / point-in-time backtest / network timeouts / DeepSeek-V4 migration, etc.) are in [AGENTS.md §5](AGENTS.md).

## Prohibitions & Safety

- Do not implement an entire large feature in one go — break it into sprints.
- Do not skip verification and then claim completion (run CLI/TUI changes for real; verify Web changes with `preview_*`).
- Do not inject future information into backtests (recent announcements / current fundamentals in `DataFeeder`).
- Do not add new architecture layers — tighten existing Decision/Strategy/Execution/Scanner boundaries first.
- Do not `git add -A`: the repo often has local-state changes (`portfolio.yaml` / `knowledge/index`, etc.). Add only the files relevant to this change.
- Do not modify unrelated files. Do not overwrite user work unless it is part of the requested change.
- Avoid destructive commands and irreversible steps unless the user explicitly asks for them.
- Respect repository-specific instructions when more specific guidance exists elsewhere.
