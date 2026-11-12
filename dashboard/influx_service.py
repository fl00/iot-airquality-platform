"""
History queries against InfluxDB (InfluxQL over HTTP, standard library only).
Results are columnar JSON for uPlot: [[ts], [co2], [temp], [hum], [pm25]].
"""

import os
import re
import http.client
from urllib.parse import urlparse, quote
from typing import Tuple
import orjson

# Strict regex whitelist for sensor hardware IDs (prevents injection attacks)
SENSOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,31}$")

INFLUX_HOST = os.getenv("INFLUXDB_URL", "http://127.0.0.1:8086")
# No default on purpose: a missing token must fail at start-up (see .env.example).
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN is not set (see .env.example)")
INFLUX_DATABASE = os.getenv("INFLUXDB_DATABASE", os.getenv("INFLUXDB_BUCKET", "airquality"))

# Columnar history with no points: [[ts], [co2], [temp], [hum], [pm25]]
EMPTY_HISTORY = b"[[],[],[],[],[]]"

class InfluxService:
    def __init__(self):
        self._parsed_url = urlparse(INFLUX_HOST)

    def _execute_http_sql_query(self, query: str) -> dict:
        """Runs an InfluxQL query (epoch=s: integer Unix timestamps). Raises on failure."""
        is_ssl = self._parsed_url.scheme == "https"
        conn_cls = http.client.HTTPSConnection if is_ssl else http.client.HTTPConnection
        # One short-lived connection per query: handlers run in a thread pool and
        # http.client connections are not thread-safe.
        conn = conn_cls(
            self._parsed_url.hostname or "127.0.0.1",
            self._parsed_url.port or (443 if is_ssl else 8086),
            timeout=10.0,
        )
        try:
            path = f"{self._parsed_url.path.rstrip('/')}/query?db={INFLUX_DATABASE}&epoch=s&q={quote(query)}"
            conn.request("GET", path, headers={"Authorization": f"Token {INFLUX_TOKEN}", "Accept": "application/json"})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"InfluxDB answered HTTP {resp.status}")
            return orjson.loads(body)
        finally:
            conn.close()

    def get_timeframe_params(self, range_code: str) -> Tuple[str, str, int]:
        """
        Maps UI timeframe code to interval range and downsample window.
        Codes: 6h, 24h (Jour), 7j (Semaine), 30j (Mois), 1an (Année)
        """
        mapping = {
            "6h": ("6h", "5m", 72),
            "24h": ("24h", "10m", 144),
            "7j": ("7d", "30m", 336),
            "30j": ("30d", "2h", 360),
            "1an": ("365d", "1d", 365),
        }
        return mapping.get(range_code.lower(), ("6h", "5m", 72))

    def fetch_sensor_history_columnar(self, sensor_id: str, range_code: str = "6h") -> bytes:
        """
        Queries InfluxDB via SQL and returns high-speed columnar JSON bytes:
        [[timestamp_unix_seconds], [co2_values], [temp_values], [hum_values], [pm25_values]]
        """
        # Defensive input validation: the id is interpolated into the query below.
        if not sensor_id or not SENSOR_ID_REGEX.match(sensor_id):
            return EMPTY_HISTORY

        range_offset, window_interval, _ = self.get_timeframe_params(range_code)

        try:
            sql_query = (
                f"SELECT mean(co2) AS co2, mean(temperature) AS temp, "
                f"mean(humidity) AS hum, mean(pm25) AS pm25 "
                f"FROM air_quality "
                f"WHERE device_id = '{sensor_id}' AND time >= now() - {range_offset} "
                f"GROUP BY time({window_interval}) fill(none)"
            )
            result = self._execute_http_sql_query(sql_query)
            series = result.get("results", [{}])[0].get("series", [])
            if series and series[0].get("values"):
                values = series[0]["values"]
                timestamps = [row[0] for row in values]
                co2_list = [round(float(row[1]), 1) if row[1] is not None else None for row in values]
                temp_list = [round(float(row[2]), 2) if row[2] is not None else None for row in values]
                hum_list = [round(float(row[3]), 1) if row[3] is not None else None for row in values]
                pm25_list = [round(float(row[4]), 2) if row[4] is not None else None for row in values]
                return orjson.dumps([timestamps, co2_list, temp_list, hum_list, pm25_list])
        except Exception as ex:
            print(f"[InfluxService] History query failed: {ex}")

        # No data, or InfluxDB unreachable: an empty chart, never invented values.
        return EMPTY_HISTORY

influx_service = InfluxService()
