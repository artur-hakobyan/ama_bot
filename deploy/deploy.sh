#!/usr/bin/env bash
# Deploy to arturserver. Excludes secrets, venv, and the database.
set -euo pipefail

HOST="${1:-arturserver}"
DEST="/opt/amma_bot"

rsync -az --delete \
  --exclude '.env' --exclude '*.db' --exclude '.venv' \
  --exclude '.git' --exclude '__pycache__' --exclude '.pytest_cache' \
  ./ "$HOST:$DEST/"

ssh "$HOST" "cd $DEST \
  && [ -d .venv ] || python3 -m venv .venv \
  && .venv/bin/pip install -q -r requirements.txt \
  && sudo cp deploy/amma-bot.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now amma-bot \
  && sudo systemctl restart amma-bot \
  && systemctl --no-pager status amma-bot | head -5"

echo "Deployed. Remember: $DEST/.env must exist on the server (copy .env.example and fill it)."
