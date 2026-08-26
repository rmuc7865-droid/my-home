#!/usr/bin/env python3
from pathlib import Path

path = Path("docker-compose.yml")
text = path.read_text(encoding="utf-8")

old = '''    volumes:
      - ./server/telegram_notifications.yaml:/app/server/telegram_notifications.yaml
    restart: unless-stopped
'''

new = '''    volumes:
      - ./server/telegram_notifications.yaml:/app/server/telegram_notifications.yaml
      - ./config/tickers.json:/app/config/tickers.json:ro
      - ./config/crypto_tickers.json:/app/config/crypto_tickers.json:ro
      - ./config/international_tickers.json:/app/config/international_tickers.json:ro
    restart: unless-stopped
'''

if old not in text:
    raise SystemExit(
        "ERROR: dashboard volumes block did not match current docker-compose.yml; no changes written."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print("SUCCESS: added dashboard ticker-file mounts to docker-compose.yml")
