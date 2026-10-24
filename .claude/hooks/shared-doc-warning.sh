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
exit 0
