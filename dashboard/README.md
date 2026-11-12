# Dashboard

FastHTML web application: one card per sensor with live values, and a history chart per sensor.

| File | Role |
| :--- | :--- |
| `main.py` | Pages, history API, live stream to browsers (`/api/v1/stream`) |
| `stream_bridge.py` | Reads the ingestor event stream, keeps the latest state of each sensor |
| `influx_service.py` | History queries (InfluxQL) |
| `aqi_engine.py` | Label, colour and advice for each AQI level |
| `static/js/stream.js` | Applies live events to the page |
| `static/js/chart.js` | uPlot chart |

The dashboard has no MQTT or Protobuf dependency: it consumes the ingestor stream specified in the [root README](../README.md#data-contracts).

## Behaviour

- **Sensor list.** A sensor appears when its first event arrives. Display names can be set in `SENSOR_METADATA` (`stream_bridge.py`); otherwise the device id is shown.
- **Status badge.** "Online", "Warming up", "Degraded" or "Fault" as reported by the node; "Offline" after 30 s without an event.
- **AQI badge.** The level comes from the ingestor; the dashboard only maps it to a label.
- **History.** Mean values per time bucket: 6 h (5 min buckets), 24 h (10 min), 7 days (30 min), 30 days (2 h), 1 year (1 day). An empty or failed query gives an empty chart.
- **Missing CO2 sensor.** A CO2 value of 0 is displayed as "—".

## HTTP routes

| Route | Content |
| :--- | :--- |
| `/` | Sensor cards |
| `/sensor/{id}` | Detail and chart |
| `/api/v1/sensor/{id}/history?range=6h\|24h\|7j\|30j\|1an` | Columnar JSON `[[ts],[co2],[temp],[hum],[pm25]]` |
| `/api/v1/stream` | Server-Sent Events, one JSON object per sample (at most 64 clients) |
| `/health` | Liveness and number of sensors seen |

## Run

```bash
pip install -r requirements.txt
INFLUXDB_TOKEN=... uvicorn main:app --host 127.0.0.1 --port 8000    # or `make run-dashboard` with a .env file
```

Static files are served with a one-year cache and a content hash in the URL, so an edited file is picked up on the next page load.
