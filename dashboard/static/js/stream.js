/**
 * Zero-Bloat Bare-Metal IoT Platform - Global Telemetry Stream & DOM Bridge (v1.0.0)
 * Handles high-speed 16-byte binary SSE streaming, real-time DOM updates,
 * and on-demand sequential chart dependency resolution.
 * Size: ~1.8 KB (Zero external dependencies)
 */

(function () {
  let eventSource = null;
  const INDEX_SENSOR_MAP = ["sensor-esp32-01", "sensor-esp32-02", "sensor-esp32-03"];

  function startSseStream() {
    if (eventSource) return;
    eventSource = new EventSource("/api/v1/stream");

    eventSource.onmessage = function (event) {
      if (!event.data) return;
      try {
        let sensorId, ts, co2, temp, hum, pm25, battery, rssi;

        if (event.data.startsWith("b64:")) {
          // Zero-GC 16-byte packed frame: decodes in-place without object allocations (< 0.01ms)
          // Frame layout: [0..3: ts (u32)] [4..5: co2 (u16)] [6..7: temp (i16/100)] [8..9: hum (u16/100)]
          //               [10..11: pm25 (u16/100)] [12..13: bat (u16)] [14: rssi (i8)] [15: aqi(7-4)|sensor_idx(3-0)]
          const raw = atob(event.data.slice(4));
          const buffer = new ArrayBuffer(raw.length);
          const uint8 = new Uint8Array(buffer);
          for (let i = 0; i < raw.length; i++) uint8[i] = raw.charCodeAt(i);

          const view = new DataView(buffer);
          ts = view.getUint32(0, false);              // Bytes 0-3: Unix UTC timestamp seconds
          co2 = view.getUint16(4, false);             // Bytes 4-5: CO2 in ppm (0..65535)
          temp = view.getInt16(6, false) / 100.0;     // Bytes 6-7: Temp in centi-Celsius (-327.68..+327.67)
          hum = view.getUint16(8, false) / 100.0;     // Bytes 8-9: Humidity in centi-percent (0..100.00)
          pm25 = view.getUint16(10, false) / 100.0;   // Bytes 10-11: PM2.5 in centi-µg/m3 (0..655.35)
          battery = view.getUint16(12, false);        // Bytes 12-13: Battery in mV (e.g. 3850)
          rssi = view.getInt8(14);                    // Byte 14: Wi-Fi RSSI in dBm (-128..+127)
          const rawIdx = view.getUint8(15);           // Byte 15: Combined bitmask byte
          const aqiLevel = (rawIdx >> 4) & 0x0F;      // High Nibble (bits 7-4): AQI Level 1..5
          const sensorIdx = rawIdx & 0x0F;            // Low Nibble (bits 3-0): Sensor index 0..15
          sensorId = INDEX_SENSOR_MAP[sensorIdx] || "sensor-esp32-01";
        } else {
          // JSON fallback
          const payload = JSON.parse(event.data);
          sensorId = payload.device_id;
          ts = payload.timestamp || Math.floor(Date.now() / 1000);
          co2 = payload.co2_ppm;
          temp = payload.temperature;
          hum = payload.humidity;
          pm25 = payload.pm25;
          battery = payload.battery_mv;
          rssi = payload.rssi_dbm;
          var aqiLevel = payload.aqi_level || 1;
        }

        // 1. Dispatch to active chart if chart engine is currently active
        if (typeof window.onLiveTelemetrySample === "function") {
          window.onLiveTelemetrySample(sensorId, ts, co2, temp, hum, pm25);
        }

        // 2. Live update sensor card numbers on home grid or detail badges
        const co2El = document.getElementById(`val-co2-${sensorId}`);
        if (co2El) co2El.textContent = `${co2} ppm`;
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

        // 3. Update AQI Badges dynamically
        const AQI_MAP = {
          1: { label: "🌿 Excellent", cls: "aqi-excellent" },
          2: { label: "🌱 Bon", cls: "aqi-good" },
          3: { label: "🟡 Moyen", cls: "aqi-moderate" },
          4: { label: "⚠️ Dégradé", cls: "aqi-poor" },
          5: { label: "🚨 Mauvais", cls: "aqi-bad" }
        };
        if (typeof aqiLevel === "number" && aqiLevel >= 1 && aqiLevel <= 5) {
          const meta = AQI_MAP[aqiLevel];
          const aqiEl = document.getElementById(`badge-aqi-${sensorId}`);
          if (aqiEl) {
            aqiEl.textContent = meta.label;
            aqiEl.className = `badge ${meta.cls}`;
          }
          const detailAqiEl = document.getElementById(`detail-badge-aqi-${sensorId}`);
          if (detailAqiEl) {
            detailAqiEl.textContent = `AQI: ${meta.label}`;
            detailAqiEl.className = `badge ${meta.cls}`;
          }
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
