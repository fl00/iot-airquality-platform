/**
 * Zero-Bloat Bare-Metal IoT Air Quality Chart Engine (v1.2.0)
 * Dedicated uPlot 2D Canvas Integration (Loaded only on Sensor History views)
 * Features:
 *   1. Zero-Allocation TypedArray Ring-Buffers (Float64Array & Float32Array)
 *   2. Zero V8 Garbage Collection churn in active live streaming loop
 *   3. Dynamic resolution and auto-resize handling
 */

(function () {
  let activeChart = null;
  let activeSensorId = null;
  const MAX_RING_POINTS = 3600;

  // Zero-GC Pre-Allocated Contiguous TypedArray Buffers
  const tsRing = new Float64Array(MAX_RING_POINTS);
  const co2Ring = new Float32Array(MAX_RING_POINTS);
  const tempRing = new Float32Array(MAX_RING_POINTS);
  const humRing = new Float32Array(MAX_RING_POINTS);
  const pm25Ring = new Float32Array(MAX_RING_POINTS);
  let ringCount = 0;

  // Pre-allocated dataset tuple for uPlot (eliminates Array literal allocations in 60 FPS loop)
  const staticDataTuple = [tsRing, co2Ring, tempRing, humRing, pm25Ring];

  // Chart configuration builder
  function buildPlotOptions(containerEl) {
    const rect = containerEl.getBoundingClientRect();
    const width = Math.max(300, rect.width || 800);
    const height = Math.max(200, rect.height || 360);

    return {
      width: width,
      height: height,
      tzDate: (ts) => new Date(ts * 1000),
      cursor: {
        sync: { key: "airquality" },
        drag: { x: true, y: true, uni: 50 },
      },
      scales: {
        x: { time: true },
        co2: { auto: true },
        temp: { auto: true },
        hum: { auto: true },
        pm25: { auto: true },
      },
      axes: [
        {
          scale: "x",
          stroke: "#8e9cb2",
          grid: { stroke: "rgba(255,255,255,0.06)", width: 1 },
          ticks: { stroke: "rgba(255,255,255,0.1)" },
        },
        {
          scale: "co2",
          stroke: "#10b981",
          label: "CO2 (ppm)",
          grid: { stroke: "rgba(255,255,255,0.04)" },
        },
        {
          scale: "temp",
          side: 1,
          stroke: "#06b6d4",
          label: "Temp (°C)",
          grid: { show: false },
        },
      ],
      series: [
        {}, // 0: Timestamps
        {
          label: "CO2 (ppm)",
          scale: "co2",
          stroke: "#10b981",
          width: 2,
          spanGaps: true,
        },
        {
          label: "Temp (°C)",
          scale: "temp",
          stroke: "#06b6d4",
          width: 2,
          spanGaps: true,
        },
        {
          label: "Humidity (%)",
          scale: "hum",
          stroke: "#f59e0b",
          width: 1.5,
          dash: [4, 4],
          spanGaps: true,
        },
        {
          label: "PM2.5 (µg/m³)",
          scale: "pm25",
          stroke: "#f43f5e",
          width: 1.5,
          spanGaps: true,
        },
      ],
    };
  }

  // Populate TypedArrays from columnar history dataset
  function loadColumnarIntoTypedArrays(columnarData) {
    const srcTs = columnarData[0] || [];
    const srcCo2 = columnarData[1] || [];
    const srcTemp = columnarData[2] || [];
    const srcHum = columnarData[3] || [];
    const srcPm25 = columnarData[4] || [];
    const len = Math.min(srcTs.length, MAX_RING_POINTS);

    for (let i = 0; i < len; i++) {
      tsRing[i] = srcTs[i] || 0;
      co2Ring[i] = srcCo2[i] !== null ? srcCo2[i] : 0;
      tempRing[i] = srcTemp[i] !== null ? srcTemp[i] : 0;
      humRing[i] = srcHum[i] !== null ? srcHum[i] : 0;
      pm25Ring[i] = srcPm25[i] !== null ? srcPm25[i] : 0;
    }
    ringCount = len;

    return [
      tsRing.subarray(0, ringCount),
      co2Ring.subarray(0, ringCount),
      tempRing.subarray(0, ringCount),
      humRing.subarray(0, ringCount),
      pm25Ring.subarray(0, ringCount),
    ];
  }

  // Robust initialization with uPlot readiness check
  window.initOrUpdateChart = function (sensorId, columnarData) {
    activeSensorId = sensorId;
    const container = document.getElementById("sensor-chart");
    if (!container) return;

    function renderPlot() {
      if (!window.uPlot) {
        setTimeout(renderPlot, 25);
        return;
      }

      if (activeChart) {
        activeChart.destroy();
        activeChart = null;
      }

      container.innerHTML = "";
      const typedData = loadColumnarIntoTypedArrays(columnarData);
      const opts = buildPlotOptions(container);
      activeChart = new window.uPlot(opts, typedData, container);

      // Window resize observer
      if (!window.__uplotResizeBound) {
        window.addEventListener("resize", () => {
          if (activeChart && container) {
            const w = container.getBoundingClientRect().width;
            activeChart.setSize({ width: w, height: 360 });
          }
        });
        window.__uplotResizeBound = true;
      }
    }

    renderPlot();
  };

  // Called when timeframe buttons fetch new downsampled history
  window.updateChartData = function (rawResponse) {
    if (!activeChart) return;
    try {
      const data = typeof rawResponse === "string" ? JSON.parse(rawResponse) : rawResponse;
      if (Array.isArray(data) && data.length >= 5) {
        const typedData = loadColumnarIntoTypedArrays(data);
        activeChart.setData(typedData);
      }
    } catch (e) {
      console.error("Failed to parse history data payload", e);
    }
  };

  // Zero-Allocation live point push using Float32Array in-place shift
  function pushLiveSample(ts, co2, temp, hum, pm25) {
    if (!activeChart) return;

    if (ringCount < MAX_RING_POINTS) {
      tsRing[ringCount] = ts;
      co2Ring[ringCount] = co2;
      tempRing[ringCount] = temp;
      humRing[ringCount] = hum;
      pm25Ring[ringCount] = pm25;
      ringCount++;
    } else {
      // In-place memory shift (Zero heap allocations)
      tsRing.copyWithin(0, 1);
      co2Ring.copyWithin(0, 1);
      tempRing.copyWithin(0, 1);
      humRing.copyWithin(0, 1);
      pm25Ring.copyWithin(0, 1);

      const lastIdx = MAX_RING_POINTS - 1;
      tsRing[lastIdx] = ts;
      co2Ring[lastIdx] = co2;
      tempRing[lastIdx] = temp;
      humRing[lastIdx] = hum;
      pm25Ring[lastIdx] = pm25;
    }

    if (ringCount < MAX_RING_POINTS) {
      staticDataTuple[0] = tsRing.subarray(0, ringCount);
      staticDataTuple[1] = co2Ring.subarray(0, ringCount);
      staticDataTuple[2] = tempRing.subarray(0, ringCount);
      staticDataTuple[3] = humRing.subarray(0, ringCount);
      staticDataTuple[4] = pm25Ring.subarray(0, ringCount);
    } else {
      staticDataTuple[0] = tsRing;
      staticDataTuple[1] = co2Ring;
      staticDataTuple[2] = tempRing;
      staticDataTuple[3] = humRing;
      staticDataTuple[4] = pm25Ring;
    }

    activeChart.setData(staticDataTuple);
  }

  // Hook for live telemetry dispatcher from global SSE stream
  window.onLiveTelemetrySample = function (sensorId, ts, co2, temp, hum, pm25) {
    if (activeChart && activeSensorId === sensorId) {
      pushLiveSample(ts, co2, temp, hum, pm25);
    }
  };
})();
