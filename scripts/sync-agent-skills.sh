#!/usr/bin/env bash
# 把 skills/ 下的中立主副本同步到各 Agent 工具会自动发现的目录。
#
# 背景：不同 Agent 工具扫描 skill 的目录互不兼容（Claude Code 用 .claude/skills/、
# VS Code Copilot 用 .github/skills/、Cursor 用 .cursor/rules/、Codex 用 .codex/skills/、
# WorkBuddy 用 .workbuddy/skills/）。只放一处会导致其他工具完全看不到。
#
# 用法：  bash scripts/sync-agent-skills.sh
# 校验：  bash scripts/sync-agent-skills.sh --check   （只比对不拷贝，有差异返回非 0）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT/skills"

TARGET_DIRS=(
  "$ROOT/.claude/skills"
  "$ROOT/.github/skills"
  "$ROOT/.cursor/rules"
  "$ROOT/.codex/skills"
  "$ROOT/.workbuddy/skills"
)

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

if [ ! -d "$SRC_DIR" ]; then
  echo "找不到主副本目录: $SRC_DIR" >&2
  exit 1
fi

drift=0

for skill_dir in "$SRC_DIR"/*/; do
  [ -d "$skill_dir" ] || continue
  name="$(basename "$skill_dir")"

  for target in "${TARGET_DIRS[@]}"; do
    dest="$target/$name"
    if [ "$CHECK_ONLY" -eq 1 ]; then
      if [ ! -d "$dest" ]; then
        echo "缺失: $dest"
        drift=1
      elif ! diff -r -q "$skill_dir" "$dest" >/dev/null 2>&1; then
        echo "不同步: $dest"
        drift=1
      fi
    else
      mkdir -p "$dest"
      cp -f "$skill_dir"*.md "$dest"/ 2>/dev/null || true
      echo "已同步: $dest"
    fi
  done
done

if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ "$drift" -eq 0 ]; then
    echo "全部 skill 副本一致"
  else
    echo "" >&2
    echo "存在未同步的副本，请跑: bash scripts/sync-agent-skills.sh" >&2
    exit 1
  fi
fi
