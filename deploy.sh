#!/usr/bin/env bash
set -euo pipefail

cd /opt/home-monitor

echo "Pulling latest code..."
git pull --ff-only

echo "Merging watchlists..."
python3 server/merge_watchlists.py

echo "Validating Python..."
python3 -m py_compile \
    server/telegram_notifier.py \
    server/merge_watchlists.py \
    server/app.py \
    shared/models.py

echo "Rebuilding services..."
docker compose up -d --build

echo "Checking status..."
docker compose ps

echo "Deployment complete."
