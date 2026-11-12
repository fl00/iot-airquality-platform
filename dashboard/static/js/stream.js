/**
 * Live values: reads JSON events from /api/v1/stream (Server-Sent Events) and
 * updates the sensor cards and badges in place.
 */
(function () {
  const lastSeenMs = new Map(); // device id -> time of its last event

  function setText(id, text, className) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (className) el.className = className;
  }

  function onEvent(event) {
    const s = JSON.parse(event.data);
    const id = s.device_id;
    lastSeenMs.set(id, Date.now());

    // First sensor ever seen: replace the "waiting" page by the sensor grid
    if (document.querySelector(".empty-state-card")) {
      window.htmx.ajax("GET", "/", { target: "#main-content", swap: "innerHTML" });
      return;
    }

    setText(`val-co2-${id}`, s.co2_ppm ? `${s.co2_ppm} ppm` : "—"); // 0 = CO2 sensor missing
    setText(`val-temp-${id}`, `${s.temperature.toFixed(1)} °C`);
    setText(`val-hum-${id}`, `${s.humidity.toFixed(1)} %`);
    setText(`val-pm25-${id}`, `${s.pm25.toFixed(1)} µg/m³`);
    setText(`badge-rssi-${id}`, `${s.rssi_dbm} dBm`);
    setText(`badge-aqi-${id}`, `${s.aqi.icon} ${s.aqi.label}`, `badge ${s.aqi.css_class}`);
    setText(`badge-status-${id}`, s.status_label, `badge ${s.status_class}`);
  }

  // A sensor that stops publishing must not stay "online" on an open page.
  setInterval(function () {
    const now = Date.now();
    lastSeenMs.forEach(function (seen, id) {
      if (now - seen > window.OFFLINE_AFTER_MS) setText(`badge-status-${id}`, window.OFFLINE_LABEL, "badge warn");
    });
  }, 5000);

  // EventSource reconnects by itself after a network error.
  new EventSource("/api/v1/stream").onmessage = function (event) {
    try {
      onEvent(event);
    } catch (err) {
      console.error("[Stream] Event ignored:", err);
    }
  };
})();
