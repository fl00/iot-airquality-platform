/**
 * History chart of one sensor (uPlot), shown on the sensor detail view.
 *
 * The chart displays stored history only, and reloads it every minute. Live values
 * are shown on the badges and cards (stream.js), never appended to the curve: the
 * two have different resolutions, and replayed packets arrive out of order.
 */
(function () {
  const REFRESH_MS = 60000;
  let chart = null;
  let sensorId = null;
  let range = null;

  function container() {
    return document.getElementById("sensor-chart");
  }

  function options(el) {
    const line = (label, scale, stroke, extra) =>
      Object.assign({ label, scale, stroke, width: 2, spanGaps: true }, extra);
    return {
      width: Math.max(300, el.clientWidth),
      height: 360,
      scales: { x: { time: true } },
      axes: [
        { stroke: "#8e9cb2", grid: { stroke: "rgba(255,255,255,0.06)" }, ticks: { stroke: "rgba(255,255,255,0.1)" } },
        { scale: "co2", stroke: "#10b981", label: "CO2 (ppm)", grid: { stroke: "rgba(255,255,255,0.04)" } },
        { scale: "temp", side: 1, stroke: "#06b6d4", label: "Température (°C)", grid: { show: false } },
      ],
      series: [
        {},
        line("CO2 (ppm)", "co2", "#10b981"),
        line("Température (°C)", "temp", "#06b6d4"),
        line("Humidité (%)", "hum", "#f59e0b", { width: 1.5, dash: [4, 4] }),
        line("PM2.5 (µg/m³)", "pm25", "#f43f5e", { width: 1.5 }),
      ],
    };
  }

  function reload() {
    if (!chart) return;
    if (!container()) { // the user navigated away from the detail view
      chart.destroy();
      chart = null;
      return;
    }
    fetch("/api/v1/sensor/" + encodeURIComponent(sensorId) + "/history?range=" + encodeURIComponent(range))
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => { if (chart) chart.setData(data); })
      .catch((err) => console.error("[Chart] History request failed:", err));
  }

  /** Called by the detail view with the history rendered by the server. */
  window.showChart = function (id, rangeCode, data) {
    if (chart) chart.destroy();
    sensorId = id;
    range = rangeCode;
    chart = new window.uPlot(options(container()), data, container());
  };

  /** Called by the range buttons. */
  window.selectRange = function (rangeCode, button) {
    document.querySelectorAll(".btn-tf").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    range = rangeCode;
    reload();
  };

  setInterval(reload, REFRESH_MS);
  window.addEventListener("resize", () => {
    if (chart && container()) chart.setSize({ width: container().clientWidth, height: 360 });
  });
})();
