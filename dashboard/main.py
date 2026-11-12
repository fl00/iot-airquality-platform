"""
IoT Air Quality dashboard (FastHTML + HTMX).

Live values come from the ingestor event stream (stream_bridge.py) and are pushed to
browsers over Server-Sent Events; history is read from InfluxDB (influx_service.py).
"""

import asyncio
import os
import time
from typing import Any, AsyncGenerator, Dict

from fasthtml.common import A, Button, Div, FastHTML, H1, H2, Header, Link, Meta, P, Script, Span, Title
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from influx_service import SENSOR_ID_REGEX, TIMEFRAMES, fetch_history
from stream_bridge import (
    OFFLINE_AFTER_SECONDS, OFFLINE_LABEL, initial_sensor_state, run_bridge,
    sensor_store, sse_subscribers, status_badge,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RANGE = "6h"
MAX_SSE_SUBSCRIBERS = 64  # bounds memory if browsers pile up


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # 'unsafe-inline': the pages carry two small inline scripts (see below)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"
        )
        return response


async def lifespan(app):
    bridge = asyncio.create_task(run_bridge())
    yield
    bridge.cancel()


app = FastHTML(
    default_hdrs=False,
    lifespan=lifespan,
    middleware=[Middleware(SecurityHeadersMiddleware)],
    hdrs=(
        Title("IoT Air Quality"),
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
        Link(rel="icon", type="image/x-icon", href="/static/favicon.ico"),
        Link(rel="stylesheet", href="/static/css/main.css"),
        Link(rel="stylesheet", href="/static/css/uPlot.min.css"),
        Script(src="/static/js/htmx.min.js"),
        Script(src="/static/js/uPlot.iife.min.js"),
        Script(f"window.OFFLINE_AFTER_MS = {OFFLINE_AFTER_SECONDS * 1000}; window.OFFLINE_LABEL = '{OFFLINE_LABEL}';"),
        Script(src="/static/js/chart.js"),
        Script(src="/static/js/stream.js"),
    ),
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ---- Page fragments ------------------------------------------------------------

def metric_box(label: str, value: str, elem_id: str):
    return Div(Div(label, cls="metric-label"), Div(value, id=elem_id, cls="metric-value"), cls="metric-box")


def co2_text(co2_ppm: int) -> str:
    return f"{co2_ppm} ppm" if co2_ppm else "—"  # 0 = CO2 sensor missing


def sensor_card(s: Dict[str, Any]):
    sensor_id = s["device_id"]
    status_label, status_cls = status_badge(s)
    aqi = s["aqi"]
    return A(
        Div(
            Div(Div(s["name"], cls="card-title"), Div(s["location"], cls="card-loc")),
            Div(
                Span(f"{aqi['icon']} {aqi['label']}", id=f"badge-aqi-{sensor_id}", cls=f"badge {aqi['css_class']}"),
                Span(f"{s['rssi_dbm']} dBm", id=f"badge-rssi-{sensor_id}", cls="badge"),
                Span(status_label, id=f"badge-status-{sensor_id}", cls=f"badge {status_cls}"),
                cls="badge-group",
            ),
            cls="card-header",
        ),
        Div(
            metric_box("CO2", co2_text(s["co2_ppm"]), f"val-co2-{sensor_id}"),
            metric_box("Température", f"{s['temperature']:.1f} °C", f"val-temp-{sensor_id}"),
            metric_box("Humidité", f"{s['humidity']:.1f} %", f"val-hum-{sensor_id}"),
            metric_box("PM 2.5", f"{s['pm25']:.1f} µg/m³", f"val-pm25-{sensor_id}"),
            cls="metrics-row",
        ),
        cls="sensor-card",
        hx_get=f"/sensor/{sensor_id}", hx_push_url="true", hx_target="#main-content", hx_swap="innerHTML",
    )


def empty_state():
    return Div(
        H2("En attente de mesures", cls="empty-title"),
        P("Aucun capteur n'a encore publié. Démarrez un capteur ou lancez le simulateur avec `make run-mock`.",
          cls="empty-text"),
        cls="empty-state-card",
    )


def page(content, request: Request):
    """Full page on a normal request, inner fragment on an HTMX navigation."""
    if request.headers.get("HX-Request"):
        return content
    return Div(
        Header(Div(H1("IoT Air Quality", cls="header-title"), cls="header-bar")),
        Div(content, id="main-content"),
        cls="app-container",
    )


# ---- Routes --------------------------------------------------------------------

@app.get("/")
def home(request: Request):
    sensors = sensor_store.get_all()
    if not sensors:
        return page(empty_state(), request)
    return page(Div(*[sensor_card(s) for s in sensors], cls="sensor-grid"), request)


@app.get("/sensor/{sensor_id}")
def sensor_detail(sensor_id: str, request: Request):
    if not SENSOR_ID_REGEX.match(sensor_id):
        return Response("Invalid sensor id", status_code=400)

    sensor = sensor_store.get(sensor_id) or initial_sensor_state(sensor_id)
    aqi = sensor["aqi"]
    status_label, status_cls = status_badge(sensor)
    history = fetch_history(sensor_id, DEFAULT_RANGE).decode()

    range_buttons = [
        Button(label, cls="btn-tf active" if code == DEFAULT_RANGE else "btn-tf",
               onclick=f"window.selectRange('{code}', this);")
        for code, (label, _, _) in TIMEFRAMES.items()
    ]
    return page(Div(
        Div(
            A("← Retour", cls="btn-back", hx_get="/", hx_push_url="true", hx_target="#main-content",
              hx_swap="innerHTML"),
            Div(
                Span(f"{aqi['icon']} {aqi['label']}", id=f"badge-aqi-{sensor_id}", cls=f"badge {aqi['css_class']}"),
                Span(f"{sensor['rssi_dbm']} dBm", id=f"badge-rssi-{sensor_id}", cls="badge"),
                Span(status_label, id=f"badge-status-{sensor_id}", cls=f"badge {status_cls}"),
                cls="badge-group",
            ),
            cls="detail-toolbar",
        ),
        Div(
            H2(sensor["name"], style="font-size: 1.4rem; font-weight: 700;"),
            P(f"{sensor_id} • {aqi['action_hint']}", style="color: var(--text-muted); font-size: 0.85rem;"),
        ),
        Div(Div(*range_buttons, cls="timeframe-group"), cls="detail-toolbar"),
        Div(id="sensor-chart", cls="chart-wrapper"),
        Script(f"window.showChart('{sensor_id}', '{DEFAULT_RANGE}', {history});"),
        cls="detail-view",
    ), request)


@app.get("/api/v1/sensor/{sensor_id}/history")
def sensor_history(sensor_id: str, range: str = DEFAULT_RANGE):
    if not SENSOR_ID_REGEX.match(sensor_id) or range not in TIMEFRAMES:
        return Response('{"error":"invalid sensor id or range"}', status_code=400, media_type="application/json")
    return Response(fetch_history(sensor_id, range), media_type="application/json")


@app.get("/api/v1/stream")
async def sse_stream(request: Request):
    """Live events for one browser (Server-Sent Events, one JSON object per sample)."""
    if len(sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
        return Response("Too many live connections", status_code=503, headers={"Retry-After": "15"})

    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    sse_subscribers.add(queue)

    async def events() -> AsyncGenerator[str, None]:
        try:
            while not await request.is_disconnected():
                try:
                    yield f"data: {await asyncio.wait_for(queue.get(), timeout=15.0)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps the connection open through proxies
        finally:
            sse_subscribers.discard(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"status": "healthy", "sensors_active": len(sensor_store.get_all()), "timestamp": int(time.time())}
