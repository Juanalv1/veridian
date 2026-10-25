#!/usr/bin/env bash
# On Stop: require Claude to commit with a descriptive message and a Status
# trailer, then pull --rebase and push.
input=$(cat)
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0

# Never auto-commit or auto-push on main: every push to main redeploys Render.
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$branch" = "main" ] && exit 0

active=false
echo "$input" | grep -q '"stop_hook_active": *true' && active=true

# Block once so Claude can react; on a repeated Stop, warn and let it stop
# instead of looping forever.
fail() {
  echo "$1" >&2
  [ -n "$2" ] && echo "$2" >&2
  if $active; then
    echo "Sync NOT completed — fix it manually." >&2
    exit 0
  fi
  exit 2
}

if [ -n "$(git status --porcelain)" ]; then
  # First Stop with a dirty tree: block and ask Claude to write the commit.
  if ! $active; then
    cat >&2 <<'MSG'
Uncommitted changes. Commit them now (Conventional Commits, no AI attribution):
  <type>(<scope>): <what changed>

  <why / details>

  Status: pending | in-progress | done
Use "done" only if the task is finished; "in-progress" if partially done; "pending" if only scaffolding/notes.
MSG
    exit 2
  fi
  # Second attempt still dirty: fallback commit so nothing is lost.
  git add -A
  if ! out=$(git commit -m "chore: sync uncommitted changes from $(hostname)" \
                        -m "Status: in-progress" 2>&1); then
    fail "fallback commit failed (check git user.name/user.email):" "$out"
  fi
fi

[ -z "$(git log @{u}..HEAD 2>/dev/null)" ] && git rev-parse @{u} >/dev/null 2>&1 && exit 0

if ! out=$(git pull --rebase --autostash 2>&1); then
  fail "git pull before push failed (conflict?) — resolve manually:" "$out"
fi
if ! out=$(git push -u origin HEAD 2>&1); then
  fail "git push failed:" "$out"
fi
exit 0
