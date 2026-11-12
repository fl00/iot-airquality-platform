"""
InfluxDB High-Speed Vectorized SQL Query Service for FastHTML Dashboard.
Executes downsampled SQL / InfluxQL aggregations over a persistent HTTP Keep-Alive connection.
Serializes directly to columnar JSON format [[ts], [co2], [temp], [hum], [pm25]] for uPlot.
"""

import os
import re
import http.client
from urllib.parse import urlparse, quote
from typing import Tuple
import orjson

# Strict regex whitelist for sensor hardware IDs (prevents injection attacks)
SENSOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

INFLUX_HOST = os.getenv("INFLUXDB_URL", "http://127.0.0.1:8086")
# No default on purpose: a missing token must fail at start-up (see .env.example).
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN is not set (see .env.example)")
INFLUX_DATABASE = os.getenv("INFLUXDB_DATABASE", os.getenv("INFLUXDB_BUCKET", "airquality"))
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "baremetal-iot")

# Columnar history with no points: [[ts], [co2], [temp], [hum], [pm25]]
EMPTY_HISTORY = b"[[],[],[],[],[]]"

class InfluxService:
    def __init__(self):
        # Persistent standard library HTTP Keep-Alive connection (Zero external dependency)
        self._parsed_url = urlparse(INFLUX_HOST)
        self._http_conn = None
        self._init_http_connection()

    def _init_http_connection(self):
        """
        Initializes or resets a persistent HTTP Keep-Alive socket connection.
        Avoids repeated TCP handshakes on consecutive downsampled history queries.
        """
        try:
            if self._http_conn:
                try:
                    self._http_conn.close()
                except Exception:
                    pass
            is_ssl = self._parsed_url.scheme == "https"
            conn_cls = http.client.HTTPSConnection if is_ssl else http.client.HTTPConnection
            host = self._parsed_url.hostname or "127.0.0.1"
            port = self._parsed_url.port or (443 if is_ssl else 8086)
            self._http_conn = conn_cls(host, port, timeout=2.0)
        except Exception as ex:
            print(f"[InfluxService] HTTP socket initialization warning: {ex}")
            self._http_conn = None

    def _execute_http_sql_query(self, sql_query: str) -> dict:
        """
        Executes a SQL/InfluxQL query over the persistent Keep-Alive socket with epoch=s (Unix integer timestamps).
        """
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Accept": "application/json",
            "Connection": "keep-alive"
        }
        encoded_query = quote(sql_query)
        path = f"{self._parsed_url.path.rstrip('/')}/query?db={INFLUX_DATABASE}&epoch=s&q={encoded_query}"

        for attempt in range(2):
            try:
                if self._http_conn is None:
                    self._init_http_connection()
                if self._http_conn is None:
                    break
                self._http_conn.request("GET", path, headers=headers)
                resp = self._http_conn.getresponse()
                if resp.status == 200:
                    raw_bytes = resp.read()
                    return orjson.loads(raw_bytes)
                else:
                    resp.read() # Drain response body
                    break
            except (http.client.HTTPException, ConnectionError, OSError):
                # Socket timed out or closed by server; reset and retry once
                self._init_http_connection()
        return {}

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
