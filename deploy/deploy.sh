#!/usr/bin/env bash
# Deploy to artServer by pulling this repo on the server.
#
# The server holds a real clone of git@github.com:artur-hakobyan/ama_bot.git, so a
# deploy is "push, then pull" — the deployed commit is always identifiable, and
# nothing here depends on the state of the laptop's working tree.
#
# Secrets (.env, the Google credential files) and runtime state (amma_bot.db,
# house_rules.json, .venv) are gitignored: they live only on the server and are
# never touched by a deploy.
set -euo pipefail

HOST="${1:-artServer}"     # SSH config alias (capital S)
DEST="/var/www/amma_bot"
BRANCH="${2:-main}"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is dirty — commit before deploying:" >&2
  git status --short >&2
  exit 1
fi

echo "Pushing $BRANCH to origin…"
git push origin "$BRANCH"

# Restarting mid-article loses it: the run dies between two Claude calls and the
# operator sees an API error that was really a deploy. Warn rather than block —
# the check is a heuristic, and an urgent fix must still be deployable.
if ssh "$HOST" "journalctl -u amma-bot --since '3 minutes ago' --no-pager \
     | grep -q 'api.anthropic.com'" 2>/dev/null; then
  echo "⚠️  The bot called Claude in the last 3 minutes — an article may be in progress."
  echo "   Deploying now would kill it. Continue? [y/N]"
  read -r reply
  [ "$reply" = "y" ] || { echo "Aborted."; exit 1; }
fi

ssh "$HOST" "set -euo pipefail
  cd $DEST
  git fetch --quiet origin $BRANCH
  git reset --hard --quiet origin/$BRANCH
  .venv/bin/pip install -q -r requirements.txt
  cp deploy/amma-bot.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl restart amma-bot
  sleep 4
  echo \"Deployed \$(git log --oneline -1)\"
  systemctl is-active amma-bot
  journalctl -u amma-bot -n 5 --no-pager --since '1 minute ago'"
