#!/usr/bin/env bash
# Pull latest changes before work starts or before editing files.
# Throttled to once every 60s so it does not run on every single edit.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0

stamp="$(git rev-parse --git-dir)/.claude-last-pull"
now=$(date +%s)
last=$(cat "$stamp" 2>/dev/null || echo 0)
[ $((now - last)) -lt 60 ] && exit 0

if ! out=$(git pull --rebase --autostash 2>&1); then
  echo "git pull failed — resolve before editing:" >&2
  echo "$out" >&2
  exit 2
fi
# Only record a successful pull, so a failure is retried on the next edit.
echo "$now" > "$stamp"
exit 0
