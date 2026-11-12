"""
History queries against InfluxDB (InfluxQL over HTTP, standard library only).
Results are columnar JSON for uPlot: [[ts], [co2], [temp], [hum], [pm25]].
"""

import http.client
import os
import re
from urllib.parse import quote, urlparse

import orjson

# Same rule as the wire contract. The id is interpolated into queries and URLs.
SENSOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,31}$")

# range code (API and buttons) -> (label, InfluxQL duration, bucket size)
TIMEFRAMES = {
    "6h": ("6 h", "6h", "5m"),
    "24h": ("24 h", "24h", "10m"),
    "7j": ("7 jours", "7d", "30m"),
    "30j": ("30 jours", "30d", "2h"),
    "1an": ("1 an", "365d", "1d"),
}

INFLUX_URL = urlparse(os.getenv("INFLUXDB_URL", "http://127.0.0.1:8086"))
INFLUX_DATABASE = os.getenv("INFLUXDB_BUCKET", "airquality")
# No default on purpose: a missing token must fail at start-up (see .env.example).
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN is not set (see .env.example)")

EMPTY_HISTORY = b"[[],[],[],[],[]]"


def _query(influxql: str) -> dict:
    """Runs an InfluxQL query (epoch=s: integer Unix timestamps). Raises on failure."""
    is_ssl = INFLUX_URL.scheme == "https"
    conn_cls = http.client.HTTPSConnection if is_ssl else http.client.HTTPConnection
    # One short-lived connection per query: handlers run in a thread pool and
    # http.client connections are not thread-safe.
    conn = conn_cls(INFLUX_URL.hostname, INFLUX_URL.port or (443 if is_ssl else 8086), timeout=10.0)
    try:
        path = f"{INFLUX_URL.path.rstrip('/')}/query?db={INFLUX_DATABASE}&epoch=s&q={quote(influxql)}"
        conn.request("GET", path, headers={"Authorization": f"Token {INFLUX_TOKEN}"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"InfluxDB answered HTTP {resp.status}")
        return orjson.loads(body)
    finally:
        conn.close()


def fetch_history(sensor_id: str, range_code: str) -> bytes:
    """
    Mean values per time bucket for one sensor, as columnar JSON bytes.
    `sensor_id` and `range_code` must have been validated by the caller.
    An empty or failed query gives an empty history, never invented values.
    """
    _, duration, bucket = TIMEFRAMES[range_code]
    influxql = (
        "SELECT mean(co2), mean(temperature), mean(humidity), mean(pm25) FROM air_quality "
        f"WHERE device_id = '{sensor_id}' AND time >= now() - {duration} "
        f"GROUP BY time({bucket}) fill(none)"
    )
    try:
        series = _query(influxql)["results"][0].get("series")
    except Exception as ex:
        print(f"[History] Query failed: {ex}")
        return EMPTY_HISTORY
    if not series:
        return EMPTY_HISTORY

    # rows of [ts, co2, temp, hum, pm25] -> one list per column, 2 decimals
    columns = zip(*series[0]["values"])
    timestamps = list(next(columns))
    rounded = [[None if v is None else round(v, 2) for v in column] for column in columns]
    return orjson.dumps([timestamps, *rounded])
