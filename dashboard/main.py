"""
IoT Air Quality dashboard (FastHTML + HTMX).

Live values come from the ingestor event stream (stream_bridge.py) and are pushed to
browsers over SSE; history is read from InfluxDB (influx_service.py).
"""

import os
import re
import time
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
from stream_bridge import (
    stream_bridge, sensor_store, sse_subscribers, create_initial_sensor_state,
    status_badge, OFFLINE_AFTER_SECONDS,
)

# Security Whitelists & Limits (CWE-89, CWE-20, CWE-400 Protection)
SENSOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,31}$")
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
        Title("IoT Air Quality"),
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
        Link(rel="icon", type="image/x-icon", href=static_url("favicon.ico")),
        Style(CRITICAL_CSS),
        Script(src=static_url("js/htmx.min.js")),
        Script(f"window.OFFLINE_AFTER_MS = {OFFLINE_AFTER_SECONDS * 1000};"),
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
def render_header():
    return Header(Div(H1("IoT Air Quality", cls="header-title"), cls="header-bar"))

def render_metric_box(label: str, value: str, elem_id: str = None):
    return Div(
        Div(label, cls="metric-label"),
        Div(value, id=elem_id, cls="metric-value") if elem_id else Div(value, cls="metric-value"),
        cls="metric-box"
    )

def render_sensor_card(s: Dict[str, Any]):
    sensor_id = s["device_id"]
    status_label, status_cls = status_badge(s)
    aqi = s["aqi"]

    return A(
        Div(
            Div(
                Div(s["name"], cls="card-title"),
                Div(s["location"], cls="card-loc"),
            ),
            Div(
                Span(f"{aqi['icon']} {aqi['label']}", id=f"badge-aqi-{sensor_id}", cls=f"badge {aqi['css_class']}"),
                Span(f"{s['battery_mv']}mV", id=f"badge-bat-{sensor_id}", cls="badge"),
                Span(f"{s['rssi_dbm']}dBm", id=f"badge-rssi-{sensor_id}", cls="badge"),
                Span(status_label, id=f"badge-status-{sensor_id}", cls=f"badge {status_cls}"),
                cls="badge-group"
            ),
            cls="card-header"
        ),
        Div(
            render_metric_box("CO2", f"{s['co2_ppm']} ppm" if s["co2_ppm"] else "—", f"val-co2-{sensor_id}"),
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

def render_empty_state():
    return Div(
        Div("📡", cls="empty-icon"),
        H2("En attente de télémétrie des capteurs", cls="empty-title"),
        P("Aucun nœud IoT n'a émis de signal sur le bus MQTT. Démarrez un capteur physique ou lancez le simulateur avec `make run-mock`.", cls="empty-text"),
        Div(
            Span("Ingestor IPC: 127.0.0.1:9100", cls="system-pill"),
            Span("MQTT Topic: sensors/+/airquality", cls="system-pill"),
            cls="empty-badges"
        ),
        cls="empty-state-card"
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
# ROUTE: Home View (Responsive Sensor Grid / Dynamic Auto-Discovery)
# ==============================================================================
@app.get("/")
def home(request: Request):
    sensors = sensor_store.get_all()
    if not sensors:
        content = Div(render_empty_state(), id="sensor-grid-container")
    else:
        cards = [render_sensor_card(s) for s in sensors]
        content = Div(*cards, cls="sensor-grid", id="sensor-grid-container")
    return render_page(content, request)

# ==============================================================================
# ROUTE: Sensor Detail View with Interactive uPlot Chart & Timeframe Buttons
# ==============================================================================
@app.get("/sensor/{sensor_id}")
def sensor_detail(sensor_id: str, request: Request):
    # Strict Regex Validation on URL path identifier (CWE-20)
    if not SENSOR_ID_REGEX.match(sensor_id):
        return Response("Invalid sensor identifier format", status_code=400)

    sensor = sensor_store.get(sensor_id) or create_initial_sensor_state(sensor_id)
    aqi = sensor["aqi"]
    status_label, status_cls = status_badge(sensor)

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
            onclick=f"window.fetchTimeframe('{sensor_id}', '{code}', this);"
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
                Span(status_label, id=f"detail-badge-status-{sensor_id}", cls=f"badge {status_cls}"),
                cls="badge-group"
            ),
            cls="detail-toolbar"
        ),
        Div(
            H2(sensor["name"], style="font-size: 1.4rem; font-weight: 700;"),
            P(f"Device: {sensor['device_id']} • {aqi['action_hint']}", style="color: var(--text-muted); font-size: 0.85rem;"),
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
                    event_json = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                    yield f"data: {event_json}\n\n"
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
