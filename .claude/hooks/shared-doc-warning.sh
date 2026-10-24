#!/bin/bash
# PreToolUse (Edit|Write|Bash): distinct warnings for shared planning docs,
# and a hard block on editing the other partner's log.
# Who you are comes from COFOUNDR_USER (set in your .claude/settings.local.json "env").

input=$(cat)
tool=$(jq -r '.tool_name' <<<"$input")
me=$(tr '[:upper:]' '[:lower:]' <<<"${COFOUNDR_USER:-}")
LINE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
PULL="Make sure BOTH Sunil and Ceyda have pulled the latest branch (git pull) before approving."

cap() { echo "$(echo "${1:0:1}" | tr a-z A-Z)${1:1}"; }

emit() { # decision, message
  jq -n --arg d "$1" --arg m "$2" \
    '{systemMessage: $m, hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $m}}'
  exit 0
}

block_other_log() { # owner
  emit deny "$LINE
🚫🔴 BLOCKED: LOG-$1.md belongs to $(cap "$1")
You ($me) must NOT edit your partner's log. Write in LOG-$me.md instead.
$LINE"
}

if [ "$tool" = "Bash" ]; then
  cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
  # Only inspect the "git push ..." segment(s), not commit messages etc.
  pushes=$(grep -oE 'git( -C [^ ]+)? push[^;&|"'\''$]*' <<<"$cmd")
  if [ -n "$pushes" ]; then
    branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null)
    if grep -Eq '[ :+](main|stage)( |$)' <<<"$pushes" || { [[ "$branch" =~ ^(main|stage)$ ]] && ! grep -Eq 'push( -[^ ]+)* [^ -][^ ]* [^ ]+' <<<"$pushes"; }; then
      emit deny "$LINE
🚫🔴 BLOCKED: direct push to main/stage
Push to your own branch, then open a PR.
Flow: sunil / jade → PR → stage → PR (approved) → main
$LINE"
    fi
  fi
  for owner in sunil ceyda; do
    [ "$owner" = "$me" ] && continue
    if grep -q "LOG-$owner\.md" <<<"$cmd" && grep -Eq '(>|sed -i|perl -[a-z]*i|tee|mv |cp |rm |truncate)' <<<"$cmd"; then
      [ -n "$me" ] && block_other_log "$owner"
    fi
  done
  exit 0
fi

f=$(jq -r '.tool_input.file_path // empty' <<<"$input")
case "$(basename "$f")" in
  CLAUDE.md)
    emit ask "$LINE
🟣 SHARED DOC: CLAUDE.md (Claude's instructions)
Changes how Claude behaves for BOTH of you.
⚠️  $PULL
$LINE" ;;
  MVP.md)
    emit ask "$LINE
🟠 SHARED DOC: MVP.md (MVP stage goals)
Changes what BOTH of you are building next.
⚠️  $PULL
$LINE" ;;
  project-plan.md)
    emit ask "$LINE
🔵 SHARED DOC: project-plan.md (scope, stack, timeline)
Changes the overall plan for BOTH of you.
⚠️  $PULL
$LINE" ;;
  winning-criteria.md)
    emit ask "$LINE
🟡 SHARED DOC: winning-criteria.md (judging targets)
Changes the targets BOTH of you are aiming for.
⚠️  $PULL
$LINE" ;;
  LOG-sunil.md|LOG-ceyda.md)
    owner=$(basename "$f" .md); owner=${owner#LOG-}
    if [ -z "$me" ]; then
      emit ask "$LINE
❓ LOG FILE: LOG-$owner.md, but COFOUNDR_USER is not set
Set it in .claude/settings.local.json so Claude knows whose machine this is.
Only approve if you ARE $(cap "$owner").
$LINE"
    elif [ "$owner" != "$me" ]; then
      block_other_log "$owner"
    else
      jq -n --arg m "🟢 Your log: LOG-$me.md" '{systemMessage: $m}'
    fi ;;
esac

# Folder ownership (mirrors CLAUDE.md + CODEOWNERS): Sunil = the middle, Jade/Ceyda = inputs + outputs.
rel=${f#"${CLAUDE_PROJECT_DIR:-$PWD}"/}
case "$rel" in
  backend/db/*|backend/ai/*|backend/pipeline.py|backend/approvals.py|data/*|tests/test_middle.py|Dockerfile|fly.toml|.github/workflows/*) area_owner=sunil ;;
  backend/inputs/*|backend/outputs/*|bridge/*|tests/test_edges.py) area_owner=ceyda ;;
  *) area_owner="" ;;
esac
if [ -n "$area_owner" ] && [ -n "$me" ] && [ "$area_owner" != "$me" ]; then
  who=$([ "$area_owner" = ceyda ] && echo "Jade (Ceyda)" || echo "Sunil")
  emit ask "$LINE
🟠 PARTNER'S AREA: $rel is owned by $who
Editing it can clash with their work. Tell them first, or ask them to make the change.
Only approve if you've agreed it with $who.
$LINE"
fi
exit 0
