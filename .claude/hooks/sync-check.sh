#!/bin/bash
# SessionStart: warn if you're about to work on main/stage or on a branch that's behind stage.
# Starting from an out-of-date branch is the #1 cause of merge conflicts.

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
timeout 10 git fetch -q origin 2>/dev/null || git fetch -q origin 2>/dev/null

branch=$(git branch --show-current)
behind=$(git rev-list --count HEAD..origin/stage 2>/dev/null || echo 0)
dirty=$(git status --porcelain 2>/dev/null | grep -vc '^??')
msgs=()

if [[ "$branch" =~ ^(main|stage)$ ]]; then
  msgs+=("🔴 You're on '$branch'. Switch to your own branch first: git switch sunil  (or: git switch jade)")
fi
if [ "${behind:-0}" -gt 0 ]; then
  msgs+=("⬇️  '$branch' is $behind commit(s) behind stage. Before working: git pull origin stage")
fi
if [ "${dirty:-0}" -gt 0 ]; then
  msgs+=("✏️  $dirty uncommitted change(s) on '$branch'. Commit or stash before pulling.")
fi

if [ ${#msgs[@]} -eq 0 ]; then
  jq -n --arg b "$branch" '{systemMessage: ("🟢 In sync: \($b) is up to date with stage.")}'
else
  text=$(printf '%s\n' "${msgs[@]}")
  jq -n --arg m "━━ Sync check ━━
$text" '{systemMessage: $m, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: ("Git sync check at session start:\n" + $m + "\nRemind the user to sync before editing files.")}}'
fi
exit 0
