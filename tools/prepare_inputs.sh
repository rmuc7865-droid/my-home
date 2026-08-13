#!/usr/bin/env bash
set -euo pipefail

cd /opt/home-monitor

echo "Merging user watchlists..."
python3 server/merge_watchlists.py

echo "Generating collector inputs..."
python3 tools/generate_provider_inputs.py
