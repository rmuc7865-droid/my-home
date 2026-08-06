# Home Monitor

A two-node monitoring system:

- **Raspberry Pi 4:** collects data every 15 minutes, normalizes it, stores it in a durable local outbox, and uploads it over HTTPS.
- **IONOS VM:** validates and deduplicates uploads, stores measurements in SQLite, evaluates YAML rules, sends Telegram alerts, and serves a Streamlit dashboard.

## 1. Project structure

```text
shared/models.py                 Common JSON/Pydantic schema
raspberry/main.py                Scheduler, collection and upload loop
raspberry/collectors/            Pluggable collectors
raspberry/outbox.py              Offline SQLite upload queue
server/app.py                    FastAPI API and rule processing
server/database.py               SQLAlchemy data model
server/rules.yaml                Configurable conditions
dashboard/streamlit_app.py       Dashboard
docker-compose.yml               IONOS deployment
deploy/systemd/                  Raspberry Pi service
```

## 2. IONOS VM installation

Install Docker and the Docker Compose plugin, copy this project to the VM, then:

```bash
cp .env.example .env
nano .env
```

Set at least a strong `MONITOR_API_KEY`. For Telegram, create a bot with BotFather, message the bot, determine the destination chat ID, and set `MONITOR_TELEGRAM_BOT_TOKEN` and `MONITOR_TELEGRAM_CHAT_ID`.

Start the services:

```bash
docker compose up -d --build
curl http://localhost:8501
curl http://localhost:8000/health
```

The sample Compose file exposes Streamlit on port 8501. In production, put Caddy, Traefik, or Nginx in front of both services and expose only HTTPS. Route a private API hostname/path to port 8000 and the dashboard hostname to port 8501. Do not expose port 8000 without TLS.

## 3. Raspberry Pi installation

```bash
sudo mkdir -p /opt/home-monitor
sudo chown "$USER":"$USER" /opt/home-monitor
# Copy the repository contents into /opt/home-monitor
cd /opt/home-monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp raspberry/config.example.yaml raspberry/config.yaml
nano raspberry/config.yaml
```

Set the HTTPS upload URL and use the same API key as the VM. Test one cycle:

```bash
PYTHONPATH=. .venv/bin/python -m raspberry.main --config raspberry/config.yaml --once
```

Install the service (change `User=pi` when your account is different):

```bash
sudo cp deploy/systemd/home-monitor-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-monitor-collector
journalctl -u home-monitor-collector -f
```

## 4. Collector configuration

`mock` produces fixed example data. `json_http` performs an HTTP GET and extracts nested values using dot paths. Array indexes are supported, such as `results.0.c`.

```yaml
- type: json_http
  enabled: true
  system: energomonitor
  url: http://192.168.1.50/api/measurements
  headers:
    Authorization: Bearer secret
  mapping:
    power_w: power.current
    energy_kwh: totals.energy
```

For systems requiring login flows, signatures, MQTT, Modbus, or vendor SDKs, add a collector class implementing `Collector.collect()` and register it in `collectors/factory.py`.

## 5. Rules

A rule describes the **healthy condition**. An alert is created when the condition is not satisfied.

```yaml
- name: Battery Low
  system: x1
  field: battery_soc
  operator: ">="
  value: 20
  severity: critical
  message: "X1 battery state of charge is below 20%."
```

Supported operators: `>`, `>=`, `<`, `<=`, `==`, `!=`, `exists`, and `not_exists`.
Repeated alerts for the same rule and system are suppressed for `MONITOR_ALERT_COOLDOWN_MINUTES`.

## 6. Payload

```json
{
  "device": "raspberrypi4",
  "records": [
    {
      "record_id": "3b538a7d-6ac8-4ada-9bbc-504fe095791c",
      "system": "x1",
      "timestamp": "2026-08-05T12:30:00Z",
      "measurements": {
        "battery_soc": 83,
        "pv_power": 4250,
        "grid_power": -1800
      },
      "metadata": {"location": "home"}
    }
  ]
}
```

`record_id` makes retries idempotent: the VM ignores a duplicate instead of storing it twice.

## 7. Security and operations

- Use HTTPS and a long random API key.
- Restrict VM firewall rules to ports 22, 80, and 443 after adding a reverse proxy.
- Keep secrets in `.env` and `raspberry/config.yaml`; do not commit them.
- Back up the Docker volume holding `/data/monitor.db`.
- SQLite is suitable for this workload. Move to PostgreSQL if write concurrency, retention, or analytics grow substantially.
- Add database retention/aggregation when raw history becomes large.

## 8. Tests

```bash
pip install pytest
PYTHONPATH=. pytest -q
```

## 9. Energomonitor collector

Energomonitor exposes measurements through its HTTPS cloud API. Data belongs to a
**feed**, and each measured or calculated quantity is a separate **stream**. API
requests require a bearer access token.

### Values you must obtain

1. In the Energomonitor application/API settings, create an access token with read
   permission for the required feed.
2. Copy the token into `access_token` in `raspberry/config.yaml`.
3. Copy the feed ID into `feed_id`.
4. Discover the feed's streams:

```bash
PYTHONPATH=. .venv/bin/python -m raspberry.collectors.energomonitor_discover \
  --config raspberry/config.yaml
```

Example output:

```text
stream_id  type       channel combined index medium       unit title
embnos     processed  4       False          power        W    Electricity - Main
embmoh     processed  4       False          power        kWh  Electricity - Main
```

Copy the appropriate IDs into the `streams` section:

```yaml
- type: energomonitor
  enabled: true
  system: energomonitor
  base_url: https://api.energomonitor.com/v1
  access_token: YOUR_TOKEN
  feed_id: "200242"
  lookback_seconds: 3600
  stale_after_seconds: 1800
  fail_on_stale: true
  streams:
    power_w:
      stream_id: embnos
      unit: W
      decimals: 1
    energy_kwh:
      stream_id: embmoh
      unit: kWh
      decimals: 3
```

Test only one collection/upload cycle:

```bash
PYTHONPATH=. .venv/bin/python -m raspberry.main \
  --config raspberry/config.yaml --once
```

The normalized record contains values in `measurements` and diagnostics in
`metadata`:

```json
{
  "system": "energomonitor",
  "timestamp": "2026-08-05T13:15:00Z",
  "measurements": {
    "power_w": 721.5,
    "energy_kwh": 12.346
  },
  "metadata": {
    "feed_id": "200242",
    "stream_ids": {
      "power_w": "embnos",
      "energy_kwh": "embmoh"
    },
    "units": {
      "power_w": "W",
      "energy_kwh": "kWh"
    },
    "point_timestamps": {
      "power_w": "2026-08-05T13:15:00+00:00",
      "energy_kwh": "2026-08-05T13:15:00+00:00"
    }
  }
}
```

### Optional stream selectors

Instead of `stream_id`, a stream can temporarily be selected by `title`, `medium`,
`unit`, `type`, `channel`, `combined`, or `index`. Use stable stream IDs after
initial discovery because display titles may be edited.

```yaml
room_temperature_c:
  title: Living room temperature
  medium: temperature
  unit: °C
  decimals: 1
```

### Transforming values

Each mapping supports `multiplier`, `offset`, and `decimals`:

```yaml
power_kw:
  stream_id: embnos
  multiplier: 0.001
  decimals: 3
```

### Freshness handling

- `lookback_seconds` controls the interval searched for the latest point.
- `stale_after_seconds` defines when a point is considered stale.
- With `fail_on_stale: true`, stale data causes the collector cycle to fail and no
  misleading new record is created.
- Energomonitor processed streams can have a different sampling interval from raw
  streams, so choose the stale threshold comfortably above the expected interval.

Never commit a real access token. Restrict the token to read access and only the
feed required by this Raspberry Pi.
