# Read-only monitoring API

The optional FastAPI service exposes the monitor's existing state and saved telemetry to clients on your LAN or VPN. It is disabled by default. API requests do not contact the inverter, trigger extra polling, or change the database. Historical samples are written by the normal monitor at `CHECK_INTERVAL`.

## Enable the service

Create a separate API token (do not reuse the Telegram bot token). Generate it locally and paste the output into `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set these entries in `.env`:

```ini
API_ENABLED=true
API_TOKEN=paste_the_generated_token_here
API_BIND_ADDRESS=192.168.1.10
API_PORT=8000
```

Replace `192.168.1.10` with the server's reserved LAN address. Keep `API_BIND_ADDRESS=127.0.0.1` if only clients on the server itself should connect. The API listens on port 8000 inside the container; the optional Compose overlay publishes that port only when you explicitly include it:

```bash
docker compose -f docker-compose.yml -f docker-compose.api.yml up -d --build
```

The base Compose file does not publish an API port. Keep the service on a trusted LAN or VPN and do not forward the port from your router to the public internet. For standalone runs, `API_HOST` defaults to `127.0.0.1`; use an appropriate interface address if another LAN host must connect. `API_PORT` defaults to `8000`.

Check that it responds from a machine that can reach the server:

```bash
curl -H "Authorization: Bearer YOUR_API_TOKEN" http://192.168.1.10:8000/api/v1/status
```

The browser documentation at `http://192.168.1.10:8000/docs` asks for HTTP Basic credentials (`api` as the username and the same API token as the password). The API endpoints use the `Authorization: Bearer ...` header. Requests without a valid token receive `401`; non-read methods receive `405`. Credentials in URL parameters are not supported. API tokens must be 32–256 URL-safe characters.

## Endpoints

All API routes are under `/api/v1`. Timestamps use ISO 8601 with a timezone; responses return UTC timestamps. All reads are bounded and read-only.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/status` | Latest inverter measurement, grid state/duration, staleness, charge/discharge power, and battery forecasts. Returns `503` before the first valid measurement. |
| `GET /api/v1/history?start=…&end=…&cursor=…&limit=100` | Raw samples in ascending time order for `[start, end)`. Both range bounds are required and must include a timezone; range is at most 31 days. `cursor` is an exclusive sample timestamp within the range; `limit` is 1–1000. A `next_cursor` means another page is available. |
| `GET /api/v1/history/aggregate?start=…&end=…&interval=hour` | UTC-aligned observed-sample averages/minimums/maximums and grid presence fraction. `interval` is `minute`, `hour`, or `day`; range is at most 31 days and the request may cover at most 2000 buckets. Empty periods are omitted, not filled with invented values. |
| `GET /api/v1/statistics?period=day&completed=false` | Electricity and grid-availability statistics for a local calendar day, Monday-based week, or month in `STATS_TIMEZONE`. Set `completed=true` for the previous fully completed period. Alternatively supply both timezone-aware `start` and `end` for a custom range up to 31 days. |
| `GET /api/v1/events?cursor=0&limit=100&start=…&end=…` | Allowlisted monitoring notification events, ordered by increasing event ID. `start` and `end` are optional timezone-aware filters and may be used independently. Cursor is an exclusive ID and limit is 1–1000. Telegram chats, subscription data, addresses, and report payloads are excluded. |
| `GET /api/v1/schedule` | Cached outage schedule and its next event. It does not make a request to the schedule provider and never returns configured street or house. |
| `GET /api/v1/health` | Database availability and measurement freshness. Returns `200` when healthy and `503` with a degraded response otherwise. |
| `GET /api/v1/config` | Public monitoring intervals, voltage thresholds, battery estimate settings, and timezone. Secrets and private schedule address fields are not returned. |

Errors use a small JSON shape, for example `{"error":{"code":"invalid_request","message":"..."}}`. `503` means the latest reading or database is unavailable. In `/status`, `stale: true` means the last sample is older than the configured freshness window; battery runtime forecasts are omitted for stale readings.

## Measurements and history

Raw samples include `observed_at`, `is_grid_online`, `load_watts`, `battery_soc`, and `total_load_energy_kwh`; the full-telemetry migration adds `grid_voltage`, `grid_connected`, `battery_current`, `battery_capacity_ah`, `shutdown_soc`, `battery_power_watts`, `battery_voltage`, `battery_temperature_c`, `inverter_dc_temperature_c`, `inverter_radiator_temperature_c`, `runtime_eta_seconds`, `charge_eta_seconds`, and `battery_cutoff_soc`. Measurements not available in a sample are `null`. Battery current and power are signed: positive means discharge and negative means charge. `battery_temperature_c` is the common reported battery temperature, not an individual reading for each battery pack.

Previous history is retained. Values that were not recorded before this migration stay `null`; they cannot be reconstructed. The API reads the stored samples and the monitor's shared latest snapshot. It neither reads the inverter itself nor initiates writes. History has no automatic retention cleanup, so PostgreSQL storage grows with monitor uptime.

Statistics include energy, grid online/offline/unknown durations, coverage, outage count and longest outage, average/peak load, and minimum/maximum battery SOC. `grid_energy_kwh` and `battery_mode_energy_kwh` split load consumption by grid presence; they are not measurements of grid import or DC battery discharge. Missing load statistics remain `null`. Schedule data is only the provider's in-memory cache, so it can be unavailable after startup until the next configured refresh.

## Home Assistant example

The REST integration can poll the same JSON status response for multiple entities. Store the token in Home Assistant's `secrets.yaml`:

```yaml
deye_api_authorization: "Bearer paste_the_generated_token_here"
```

Add this to `configuration.yaml` (use the API host address reachable by Home Assistant). Availability templates mark stale readings and missing values as unavailable rather than turning them into zero. The 30-second polling interval avoids requesting data faster than the default 10-second inverter monitor loop.

```yaml
rest:
  - resource: http://192.168.1.10:8000/api/v1/status
    method: GET
    headers:
      Authorization: !secret deye_api_authorization
    scan_interval: 30
    timeout: 5
    sensor:
      - name: Deye Grid Voltage
        availability: "{{ value_json is defined and not value_json.get('stale', true) and value_json.get('grid_voltage') is not none }}"
        value_template: "{{ value_json.grid_voltage }}"
        unit_of_measurement: V
        device_class: voltage
        state_class: measurement
      - name: Deye Load
        availability: "{{ value_json is defined and not value_json.get('stale', true) and value_json.get('load_watts') is not none }}"
        value_template: "{{ value_json.load_watts }}"
        unit_of_measurement: W
        device_class: power
        state_class: measurement
      - name: Deye Battery
        availability: "{{ value_json is defined and not value_json.get('stale', true) and value_json.get('battery_soc') is not none }}"
        value_template: "{{ value_json.battery_soc }}"
        unit_of_measurement: "%"
        device_class: battery
        state_class: measurement
      - name: Deye Battery Charge Power
        availability: "{{ value_json is defined and not value_json.get('stale', true) and value_json.get('charge_power_watts') is not none }}"
        value_template: "{{ value_json.charge_power_watts }}"
        unit_of_measurement: W
        device_class: power
        state_class: measurement
      - name: Deye Battery Temperature
        availability: "{{ value_json is defined and not value_json.get('stale', true) and value_json.get('battery_temperature_c') is not none }}"
        value_template: "{{ value_json.battery_temperature_c }}"
        unit_of_measurement: °C
        device_class: temperature
        state_class: measurement
      - name: Deye Grid State
        availability: "{{ value_json is defined and not value_json.get('stale', true) }}"
        value_template: "{{ value_json.grid_voltage_state }}"
```

Validate and reload Home Assistant's YAML configuration after adding the integration. The `/status` response exposes all of its fields, so additional sensors can use documented keys such as `battery_voltage`, `battery_current`, `discharge_power_watts`, `grid_state_duration_seconds`, `runtime_eta_seconds`, `charge_eta_seconds`, `inverter_dc_temperature_c`, and `inverter_radiator_temperature_c`. A `null` field means the inverter or settings did not provide that value; keep it unknown instead of substituting zero.

See Home Assistant's [RESTful integration documentation](https://www.home-assistant.io/integrations/rest/) and [REST sensor availability templates](https://www.home-assistant.io/integrations/sensor.rest/) for the configuration options. The polling interval may be changed, but polling more often than the notifier's `CHECK_INTERVAL` cannot produce newer inverter readings.
