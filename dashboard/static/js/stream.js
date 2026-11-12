/**
 * Live telemetry stream: reads JSON events from /api/v1/stream (SSE), updates the
 * sensor cards and feeds the chart. Also lazy-loads the chart dependencies.
 */
(function () {
  let eventSource = null;
  const lastSeenMs = new Map(); // device id -> time of its last event

  function setStatus(sensorId, label, cls) {
    for (const prefix of ["badge-status-", "detail-badge-status-"]) {
      const el = document.getElementById(prefix + sensorId);
      if (el) {
        el.textContent = label;
        el.className = `badge ${cls}`;
      }
    }
  }

  // A sensor that stops publishing must not stay "Online" on an open page.
  setInterval(function () {
    const now = Date.now();
    lastSeenMs.forEach(function (seen, sensorId) {
      if (now - seen > window.OFFLINE_AFTER_MS) setStatus(sensorId, "Offline", "warn");
    });
  }, 5000);
  function startSseStream() {
    if (eventSource) return;
    eventSource = new EventSource("/api/v1/stream");

    eventSource.onmessage = function (event) {
      if (!event.data) return;
      try {
        const payload = JSON.parse(event.data);
        const sensorId = payload.device_id;
        const ts = payload.timestamp || Math.floor(Date.now() / 1000);
        const co2 = payload.co2_ppm;
        const temp = payload.temperature;
        const hum = payload.humidity;
        const pm25 = payload.pm25;
        const battery = payload.battery_mv;
        const rssi = payload.rssi_dbm;
        lastSeenMs.set(sensorId, Date.now());
        setStatus(sensorId, payload.status_label, payload.status_class);

        // Auto-transition from empty state to live grid when first telemetry packet arrives
        const emptyState = document.querySelector(".empty-state-card");
        if (emptyState) {
          if (window.htmx) {
            window.htmx.ajax("GET", "/", { target: "#main-content", swap: "innerHTML" });
          } else {
            window.location.reload();
          }
          return;
        }

        // 1. Dispatch to active chart if chart engine is currently active
        if (typeof window.onLiveTelemetrySample === "function") {
          window.onLiveTelemetrySample(sensorId, ts, co2, temp, hum, pm25);
        }

        // 2. Live update sensor card numbers on home grid or detail badges
        const co2El = document.getElementById(`val-co2-${sensorId}`);
        if (co2El) co2El.textContent = co2 ? `${co2} ppm` : "—"; // 0 = CO2 sensor missing
        const tempEl = document.getElementById(`val-temp-${sensorId}`);
        if (tempEl) tempEl.textContent = `${temp.toFixed(1)} °C`;
        const humEl = document.getElementById(`val-hum-${sensorId}`);
        if (humEl) humEl.textContent = `${hum.toFixed(1)} %`;
        const pm25El = document.getElementById(`val-pm25-${sensorId}`);
        if (pm25El) pm25El.textContent = `${pm25.toFixed(1)} µg`;
        const batEl = document.getElementById(`badge-bat-${sensorId}`);
        if (batEl && battery) batEl.textContent = `${battery}mV`;
        const rssiEl = document.getElementById(`badge-rssi-${sensorId}`);
        if (rssiEl && rssi) rssiEl.textContent = `${rssi}dBm`;

        // 3. AQI badges (level, label and style are computed server-side)
        const aqi = payload.aqi;
        const aqiEl = document.getElementById(`badge-aqi-${sensorId}`);
        if (aqiEl) {
          aqiEl.textContent = `${aqi.icon} ${aqi.label}`;
          aqiEl.className = `badge ${aqi.css_class}`;
        }
        const detailAqiEl = document.getElementById(`detail-badge-aqi-${sensorId}`);
        if (detailAqiEl) {
          detailAqiEl.textContent = `${aqi.icon} AQI ${aqi.label}`;
          detailAqiEl.className = `badge ${aqi.css_class}`;
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    };

    eventSource.onerror = function () {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      setTimeout(startSseStream, 3000);
    };
  }

  // On-demand lazy script loader with guaranteed sequential execution
  window.loadChartDependencies = function (chartScriptUrl, callback) {
    if (window.uPlot && typeof window.initOrUpdateChart === "function") {
      callback();
      return;
    }

    // 1. Inject uPlot stylesheet if not already present
    if (!document.getElementById("uplot-css")) {
      const link = document.createElement("link");
      link.id = "uplot-css";
      link.rel = "stylesheet";
      link.href = "/static/css/uPlot.min.css";
      document.head.appendChild(link);
    }

    function injectScript(src, onLoad) {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing) {
        if (existing.getAttribute("data-loaded") === "true") {
          onLoad();
        } else {
          existing.addEventListener("load", onLoad);
        }
        return;
      }
      const s = document.createElement("script");
      s.src = src;
      s.onload = function () {
        s.setAttribute("data-loaded", "true");
        onLoad();
      };
      document.head.appendChild(s);
    }

    // 2. Sequentially load uPlot.js followed by chart.js
    if (!window.uPlot) {
      injectScript("/static/js/uPlot.iife.min.js", function () {
        injectScript(chartScriptUrl || "/static/js/chart.js", callback);
      });
    } else if (typeof window.initOrUpdateChart !== "function") {
      injectScript(chartScriptUrl || "/static/js/chart.js", callback);
    } else {
      callback();
    }
  };

  // Start stream when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startSseStream);
  } else {
    startSseStream();
  }
})();
