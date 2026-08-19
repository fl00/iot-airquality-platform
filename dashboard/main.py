"""
Zero-Bloat Bare-Metal IoT Air Quality Platform - FastHTML Dashboard
Architecture: Single-process async FastHTML + HTMX DOM orchestration + Native JS SSE uPlot canvas.
"""

import os
import re
import time
import struct
import base64
import hashlib
import asyncio
from typing import AsyncGenerator, Dict, Any, List

import orjson
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse, FileResponse
from starlette.requests import Request
from starlette.staticfiles import StaticFiles
from fasthtml.common import (
    FastHTML, serve, Title, Meta, Link, Script, Style, Div, H1, H2, H3, P, Span,
    Button, A, Header, Main, Body, Head
)

from influx_service import influx_service
from stream_bridge import stream_bridge, sensor_store, sse_subscribers, create_default_sensor_state
from aqi_engine import compute_aqi

# Security Whitelists & Limits (CWE-89, CWE-20, CWE-400 Protection)
SENSOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
ALLOWED_TIMEFRAME_RANGES = {"6h", "24h", "7j", "30j", "1an"}
MAX_SSE_SUBSCRIBERS = 64

# ==============================================================================
# Automatic Static Asset Content-Hashing (Zero Manual Versioning / Cache Busting)
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_HASH_CACHE: Dict[str, str] = {}

def static_url(relative_path: str) -> str:
    """
    Returns '/static/{relative_path}?v={md5_hash}' for automatic 1-year browser cache busting.
    Computes an 8-char content hash cached in-memory. Zero manual file renaming needed.
    """
    if relative_path not in _STATIC_HASH_CACHE:
        full_path = os.path.join(BASE_DIR, "static", relative_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "rb") as f:
                    _STATIC_HASH_CACHE[relative_path] = hashlib.md5(f.read()).hexdigest()[:8]
            except Exception:
                _STATIC_HASH_CACHE[relative_path] = "1"
        else:
            _STATIC_HASH_CACHE[relative_path] = "1"
    return f"/static/{relative_path}?v={_STATIC_HASH_CACHE[relative_path]}"

# Inlined Critical CSS for 0-RTT First Contentful Paint (< 14KB TCP CWND)
CSS_PATH = os.path.join(BASE_DIR, "static", "css", "main.css")
CRITICAL_CSS = ""
if os.path.exists(CSS_PATH):
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        CRITICAL_CSS = f.read()

# ==============================================================================
# Ultra-Compact 16-Byte SSE Binary Protocol (Zero-GC / Minimal Wire Overhead)
# ==============================================================================
# Rationale & Design Trade-offs:
# 1. Wire Overhead: Standard JSON payloads {"co2_ppm":540,...} weigh 140-180 bytes.
#    This binary frame is strictly 16 bytes (packed via struct, encoded as 24 Base64 chars).
# 2. V8 Garbage Collection: Parsing JSON objects continuously in JavaScript triggers
#    frequent GC sweeps. Decoding an ArrayBuffer with DataView performs in-place scalar
#    extraction with ZERO temporary object allocations.
# 3. Deterministic Throughput: Exact fixed 16-byte boundary allows sub-millisecond dispatch.
#
# Binary Frame Memory Layout (Big-Endian / Network Order):
# ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬───────────────────────────────┐
# │ Bytes 0 - 3  │ Bytes 4 - 5  │ Bytes 6 - 7  │ Bytes 8 - 9  │ Bytes 10 - 11│ Bytes 12 - 13│   Byte 14    │            Byte 15            │
# ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼───────────────┬───────────────┤
# │  Timestamp   │     CO2      │ Temperature  │   Humidity   │    PM2.5     │   Battery    │     RSSI     │   AQI Level   │  Sensor Index │
# │ uint32 (sec) │ uint16 (ppm) │ int16 (.01°C)│uint16 (.01%) │uint16(.01µg) │ uint16 (mV)  │  int8 (dBm)  │  High Nibble  │  Low Nibble   │
# │  [0..2^32-1] │  [0..65535]  │[-32768..32767│  [0..65535]  │  [0..65535]  │  [0..65535]  │ [-128..127]  │ 4 bits (1..5) │ 4 bits (0..15)│
# └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴───────────────┴───────────────┘
# Total Size: Exactly 16 Bytes (128 bits).

# Sensor ID mapping for 1-byte compact binary streaming (up to 16 sensors per index map)
SENSOR_INDEX_MAP: Dict[str, int] = {
    "sensor-esp32-01": 0,
    "sensor-esp32-02": 1,
    "sensor-esp32-03": 2,
}

def pack_telemetry_binary(telemetry: Dict[str, Any]) -> str:
    """
    Packs a telemetry dictionary into an ultra-compact 16-byte big-endian binary struct,
    then encodes it as Base64 for Server-Sent Events (SSE) wire transport.
    """
    ts = int(telemetry.get("last_seen", telemetry.get("timestamp", time.time()))) & 0xFFFFFFFF
    co2 = max(0, min(65535, int(telemetry.get("co2_ppm", 0))))
    temp_centi = max(-32768, min(32767, int(round(telemetry.get("temperature", 0.0) * 100))))
    hum_centi = max(0, min(65535, int(round(telemetry.get("humidity", 0.0) * 100))))
    pm25_centi = max(0, min(65535, int(round(telemetry.get("pm25", 0.0) * 100))))
    bat = max(0, min(65535, int(telemetry.get("battery_mv", telemetry.get("battery_millivolts", 0)))))
    rssi = max(-128, min(127, int(telemetry.get("rssi_dbm", 0))))
    sensor_id = telemetry.get("device_id", "sensor-esp32-01")
    sensor_idx = SENSOR_INDEX_MAP.get(sensor_id, 0) & 0x0F
    
    # Bitmask packing: AQI (1..5) in bits 7-4, sensor_idx (0..15) in bits 3-0
    aqi_level = max(1, min(5, int(telemetry.get("aqi_level", telemetry.get("aqi", {}).get("level", 1))))) & 0x0F
    combined_byte = (aqi_level << 4) | sensor_idx

    # 16-byte fixed struct format: >IHhHHHbB (Big-Endian Network Byte Order)
    packed = struct.pack(">IHhHHHbB", ts, co2, temp_centi, hum_centi, pm25_centi, bat, rssi, combined_byte)
    return base64.b64encode(packed).decode("ascii")

# ==============================================================================
# Native Security Headers Middleware (Defense-in-Depth)
# ==============================================================================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects modern HTTP security headers into all responses."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"
            )
        return response

# ==============================================================================
# Application Lifespan: Local Rust Ingestor IPC Stream Supervision
# ==============================================================================
async def lifespan(app):
    stream_bridge.start()
    print("[FastHTML] Application started. Background Rust Ingestor IPC stream initialized.")
    yield
    print("[FastHTML] Application shutting down.")
    stream_bridge.stop()

# ==============================================================================
# Static Asset Serving with 1-Year Immutable Browser Caching
# (Dynamic compression is handled on-the-fly by Caddy / GZipMiddleware)
# ==============================================================================
class ImmutableStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

# ==============================================================================
# FastHTML Core App Initialization
# Global headers: only essential HTMX + lightweight 1.2KB SSE stream bridge
# ==============================================================================
app = FastHTML(
    default_hdrs=False,
    lifespan=lifespan,
    middleware=[
        Middleware(SecurityHeadersMiddleware),
        Middleware(GZipMiddleware, minimum_size=500),
    ],
    hdrs=(
        Title("Bare-Metal IoT Air Quality Platform"),
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
        Link(rel="icon", type="image/x-icon", href=static_url("favicon.ico")),
        Style(CRITICAL_CSS),
        Script(src=static_url("js/htmx.min.js")),
        Script(src=static_url("js/stream.js")),
    )
)

# Root Favicon endpoint
@app.get("/favicon.ico")
def get_favicon():
    fav_path = os.path.join(BASE_DIR, "static", "favicon.ico")
    return FileResponse(
        fav_path,
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )

# Mount static asset directory
app.mount("/static", ImmutableStaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# ==============================================================================
# Reusable UI Components
# ==============================================================================
def render_header(subtitle: str = "Zero-Bloat Bare-Metal Telemetry"):
    return Header(
        Div(
            H1("💨 IoT Air Quality Hub", cls="header-title"),
            Span("45MB Bare-Metal Architecture", cls="system-pill"),
            cls="header-bar"
        )
    )

def render_metric_box(label: str, value: str, elem_id: str = None):
    return Div(
        Div(label, cls="metric-label"),
        Div(value, id=elem_id, cls="metric-value") if elem_id else Div(value, cls="metric-value"),
        cls="metric-box"
    )

def render_sensor_card(s: Dict[str, Any]):
    sensor_id = s["device_id"]
    status_cls = "online" if s["status"] == "Online" else "warn"
    aqi = s.get("aqi") or compute_aqi(int(s.get("co2_ppm", 450)), float(s.get("pm25", 4.0)))

    return A(
        Div(
            Div(
                Div(s.get("name", sensor_id), cls="card-title"),
                Div(s.get("location", "Facility"), cls="card-loc"),
            ),
            Div(
                Span(f"{aqi['icon']} {aqi['label']}", id=f"badge-aqi-{sensor_id}", cls=f"badge {aqi['css_class']}"),
                Span(f"{s['battery_mv']}mV", id=f"badge-bat-{sensor_id}", cls="badge"),
                Span(f"{s['rssi_dbm']}dBm", id=f"badge-rssi-{sensor_id}", cls="badge"),
                Span(s["status"], id=f"badge-status-{sensor_id}", cls=f"badge {status_cls}"),
                cls="badge-group"
            ),
            cls="card-header"
        ),
        Div(
            render_metric_box("CO2", f"{s['co2_ppm']} ppm", f"val-co2-{sensor_id}"),
            render_metric_box("Temp", f"{s['temperature']:.1f} °C", f"val-temp-{sensor_id}"),
            render_metric_box("Humidity", f"{s['humidity']:.1f} %", f"val-hum-{sensor_id}"),
            render_metric_box("PM 2.5", f"{s['pm25']:.1f} µg", f"val-pm25-{sensor_id}"),
            cls="metrics-row"
        ),
        cls="sensor-card",
        hx_get=f"/sensor/{sensor_id}",
        hx_push_url="true",
        hx_target="#main-content",
        hx_swap="innerHTML"
    )

# ==============================================================================
# Persistent App Shell Wrapper
# ==============================================================================
def render_page(inner_content, request: Request):
    if request.headers.get("HX-Request"):
        return inner_content
    return Div(
        render_header(),
        Div(inner_content, id="main-content"),
        cls="app-container"
    )

# ==============================================================================
# ROUTE: Home View (Responsive Sensor Grid)
# ==============================================================================
@app.get("/")
def home(request: Request):
    cards = [render_sensor_card(s) for s in sensor_store.get_all()]
    grid = Div(*cards, cls="sensor-grid", id="sensor-grid-container")
    return render_page(grid, request)

# ==============================================================================
# ROUTE: Sensor Detail View with Interactive uPlot Chart & Timeframe Buttons
# ==============================================================================
@app.get("/sensor/{sensor_id}")
def sensor_detail(sensor_id: str, request: Request):
    # Strict Regex Validation on URL path identifier (CWE-20)
    if not SENSOR_ID_REGEX.match(sensor_id):
        return Response("Invalid sensor identifier format", status_code=400)

    sensor = sensor_store.get(sensor_id) or create_default_sensor_state(sensor_id)
    aqi = sensor.get("aqi") or compute_aqi(int(sensor.get("co2_ppm", 500)), float(sensor.get("pm25", 5.0)))

    # Fetch initial 6h downsampled history in columnar format
    initial_history_raw = influx_service.fetch_sensor_history_columnar(sensor_id, "6h")
    initial_history_str = initial_history_raw.decode("utf-8")

    timeframes = [
        ("6h", "6h", "active"),
        ("24h", "24h (Jour)", ""),
        ("7j", "7j (Semaine)", ""),
        ("30j", "30j (Mois)", ""),
        ("1an", "1an (Année)", ""),
    ]

    tf_buttons = [
        Button(
            label,
            cls=f"btn-tf {active}",
            hx_get=f"/api/v1/sensor/{sensor_id}/history?range={code}",
            hx_swap="none",
            onclick="document.querySelectorAll('.btn-tf').forEach(b=>b.classList.remove('active')); this.classList.add('active');",
            hx_on__after_request="window.updateChartData(event.detail.xhr.response);"
        )
        for code, label, active in timeframes
    ]

    detail_content = Div(
        Div(
            A("← Back to Overview", cls="btn-back", hx_get="/", hx_push_url="true", hx_target="#main-content", hx_swap="innerHTML"),
            Div(
                Span(f"{aqi['icon']} AQI {aqi['label']}", id=f"detail-badge-aqi-{sensor_id}", cls=f"badge {aqi['css_class']}"),
                Span(f"Battery: {sensor['battery_mv']}mV", cls="badge"),
                Span(f"RSSI: {sensor['rssi_dbm']}dBm", cls="badge"),
                Span(sensor["status"], cls="badge online"),
                cls="badge-group"
            ),
            cls="detail-toolbar"
        ),
        Div(
            H2(sensor["name"], style="font-size: 1.4rem; font-weight: 700;"),
            P(f"Device: {sensor['device_id']} • Location: {sensor['location']} • {aqi['action_hint']}", style="color: var(--text-muted); font-size: 0.85rem;"),
        ),
        Div(
            Div(*tf_buttons, cls="timeframe-group"),
            cls="detail-toolbar"
        ),
        # uPlot Canvas Chart Container
        Div(id="sensor-chart", cls="chart-wrapper"),
        # Sequential On-Demand Dependency Loader (guarantees uPlot.js executes before chart.js init)
        Script(f"window.loadChartDependencies('{static_url('js/chart.js')}', function() {{ window.initOrUpdateChart('{sensor_id}', {initial_history_str}); }});"),
        cls="detail-view"
    )

    return render_page(detail_content, request)

# ==============================================================================
# ROUTE: Columnar History API (orjson high-speed JSON array)
# ==============================================================================
@app.get("/api/v1/sensor/{sensor_id}/history")
def get_sensor_history(sensor_id: str, range: str = "6h"):
    # Parameter Whitelist Validation (CWE-89 / CWE-20)
    if not SENSOR_ID_REGEX.match(sensor_id) or range.lower() not in ALLOWED_TIMEFRAME_RANGES:
        return Response(
            orjson.dumps({"error": "Invalid query parameters: sensor_id or timeframe range rejected"}),
            status_code=400,
            media_type="application/json"
        )

    payload_bytes = influx_service.fetch_sensor_history_columnar(sensor_id, range)
    return Response(content=payload_bytes, media_type="application/json")

# ==============================================================================
# ROUTE: Server-Sent Events (SSE) Live Telemetry Stream
# ==============================================================================
@app.get("/api/v1/stream")
async def sse_stream(request: Request):
    # Anti-DoS Connection Cap Protection (CWE-400)
    if len(sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
        return Response(
            "Service Unavailable: Maximum concurrent SSE live subscribers reached",
            status_code=503,
            headers={"Retry-After": "15"}
        )

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    sse_subscribers.add(client_queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    telemetry = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                    b64_frame = telemetry if isinstance(telemetry, str) else pack_telemetry_binary(telemetry)
                    yield f"data: b64:{b64_frame}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_subscribers.discard(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# ==============================================================================
# ROUTE: Health Probe
# ==============================================================================
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "engine": "FastHTML + uvloop",
        "sensors_active": sensor_store.count(),
        "timestamp": int(time.time())
    }

if __name__ == "__main__":
    serve(host="0.0.0.0", port=8000, reload=False)
