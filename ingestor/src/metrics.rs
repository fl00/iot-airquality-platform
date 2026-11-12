//! Counters, AQI level and the local HTTP endpoint (`/metrics`, `/stream`, `/health`).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tracing::{error, info, warn};

#[derive(Default)]
pub struct IngestorMetrics {
    // 1. MQTT Telemetry Ingestion Counters
    pub packets_received_total: AtomicU64,
    pub packets_valid_total: AtomicU64,
    pub packets_dropped_total: AtomicU64,
    pub packets_spoofed_total: AtomicU64,

    // 2. InfluxDB Sink Write Counters & Latencies
    pub influx_batches_total: AtomicU64,
    pub influx_points_written_total: AtomicU64,
    pub influx_errors_total: AtomicU64,
    pub influx_last_batch_latency_ms: AtomicU64,

    // 3. Samples waiting in memory for the next InfluxDB write (grows while InfluxDB is down)
    pub influx_buffered_samples: AtomicU64,

    // 4. Per-Sensor Last Seen Timestamp
    pub sensor_last_seen: RwLock<HashMap<String, u64>>,
}

impl IngestorMetrics {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn record_packet_received(&self) {
        self.packets_received_total.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_packet_valid(&self, device_id: &str, timestamp_sec: u64) {
        self.packets_valid_total.fetch_add(1, Ordering::Relaxed);
        if let Ok(mut map) = self.sensor_last_seen.write() {
            map.insert(device_id.to_string(), timestamp_sec);
        }
    }

    pub fn record_packet_dropped(&self) {
        self.packets_dropped_total.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_packet_spoofed(&self) {
        self.packets_spoofed_total.fetch_add(1, Ordering::Relaxed);
        self.packets_dropped_total.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_batch_result(&self, points_count: u64, latency_ms: u64, is_success: bool) {
        self.influx_batches_total.fetch_add(1, Ordering::Relaxed);
        self.influx_last_batch_latency_ms.store(latency_ms, Ordering::Relaxed);
        if is_success {
            self.influx_points_written_total.fetch_add(points_count, Ordering::Relaxed);
        } else {
            self.influx_errors_total.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Renders all metrics in standard Prometheus text format (version 0.0.4)
    pub fn render_prometheus_text(&self) -> String {
        let mut out = String::with_capacity(2048);

        // Header & Telemetry counters
        out.push_str("# HELP iot_packets_received_total Total number of MQTT telemetry packets received\n");
        out.push_str("# TYPE iot_packets_received_total counter\n");
        out.push_str(&format!("iot_packets_received_total {}\n\n", self.packets_received_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_packets_valid_total Total number of successfully decoded valid packets\n");
        out.push_str("# TYPE iot_packets_valid_total counter\n");
        out.push_str(&format!("iot_packets_valid_total {}\n\n", self.packets_valid_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_packets_dropped_total Total number of malformed or rejected packets\n");
        out.push_str("# TYPE iot_packets_dropped_total counter\n");
        out.push_str(&format!("iot_packets_dropped_total {}\n\n", self.packets_dropped_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_packets_spoofed_total Total packets rejected due to topic vs payload mismatch\n");
        out.push_str("# TYPE iot_packets_spoofed_total counter\n");
        out.push_str(&format!("iot_packets_spoofed_total {}\n\n", self.packets_spoofed_total.load(Ordering::Relaxed)));

        // InfluxDB Sink metrics
        out.push_str("# HELP iot_influx_batches_total Total micro-batches processed towards InfluxDB\n");
        out.push_str("# TYPE iot_influx_batches_total counter\n");
        out.push_str(&format!("iot_influx_batches_total {}\n\n", self.influx_batches_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_influx_points_written_total Total metric points written to InfluxDB\n");
        out.push_str("# TYPE iot_influx_points_written_total counter\n");
        out.push_str(&format!("iot_influx_points_written_total {}\n\n", self.influx_points_written_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_influx_errors_total Total InfluxDB HTTP or transport errors\n");
        out.push_str("# TYPE iot_influx_errors_total counter\n");
        out.push_str(&format!("iot_influx_errors_total {}\n\n", self.influx_errors_total.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_influx_last_batch_latency_ms Duration of the last InfluxDB write batch in ms\n");
        out.push_str("# TYPE iot_influx_last_batch_latency_ms gauge\n");
        out.push_str(&format!("iot_influx_last_batch_latency_ms {}\n\n", self.influx_last_batch_latency_ms.load(Ordering::Relaxed)));

        out.push_str("# HELP iot_influx_buffered_samples Samples held in memory, waiting for the next InfluxDB write\n");
        out.push_str("# TYPE iot_influx_buffered_samples gauge\n");
        out.push_str(&format!("iot_influx_buffered_samples {}\n\n", self.influx_buffered_samples.load(Ordering::Relaxed)));

        // Sensor Last Seen Liveness Probes
        out.push_str("# HELP iot_sensor_last_seen_seconds Unix epoch of last seen packet per sensor\n");
        out.push_str("# TYPE iot_sensor_last_seen_seconds gauge\n");
        if let Ok(map) = self.sensor_last_seen.read() {
            for (dev_id, ts) in map.iter() {
                out.push_str(&format!("iot_sensor_last_seen_seconds{{device_id=\"{}\"}} {}\n", dev_id, ts));
            }
        }

        out
    }
}

use tokio::sync::broadcast;
use crate::sink::MetricSample;

/// AQI level 1 (excellent) to 5 (bad): the worst of the CO2, PM2.5 and VOC sub-levels.
/// Single source of truth for the thresholds (documented in docs/AIR_QUALITY_GUIDE*.md).
pub fn compute_aqi_level(co2_ppm: u32, pm25_ug: f32, tvoc_ppb: f32) -> u8 {
    let sub_co2 = match co2_ppm {
        0..=599 => 1,
        600..=799 => 2,
        800..=999 => 3,
        1000..=1499 => 4,
        _ => 5,
    };
    let sub_pm25 = if pm25_ug < 5.0 {
        1
    } else if pm25_ug < 12.0 {
        2
    } else if pm25_ug < 25.0 {
        3
    } else if pm25_ug < 50.0 {
        4
    } else {
        5
    };
    // BSEC breath-VOC equivalent scale (BME680), in ppb: boundaries at Bosch static IAQ
    // 50 / 100 / 150 / 200.
    let sub_tvoc = if tvoc_ppb < 800.0 {
        1
    } else if tvoc_ppb < 1900.0 {
        2
    } else if tvoc_ppb < 4800.0 {
        3
    } else if tvoc_ppb < 12000.0 {
        4
    } else {
        5
    };
    sub_co2.max(sub_pm25).max(sub_tvoc)
}

/// One live-stream event: the sample plus its AQI level, as a JSON object.
pub fn stream_event_json(sample: &MetricSample) -> String {
    #[derive(serde::Serialize)]
    struct StreamEvent<'a> {
        #[serde(flatten)]
        sample: &'a MetricSample,
        aqi_level: u8,
    }
    let aqi_level = compute_aqi_level(sample.co2_ppm, sample.pm25_ug_m3, sample.tvoc_ppb);
    serde_json::to_string(&StreamEvent { sample, aqi_level }).unwrap_or_default()
}

/// Minimal HTTP/1.1 listener for local consumers: `/metrics` (Prometheus text),
/// `/stream` (Server-Sent Events, one JSON object per sample) and `/health`.
pub async fn run_metrics_server(
    metrics: Arc<IngestorMetrics>,
    stream_tx: broadcast::Sender<Arc<String>>,
    addr: String,
) {
    let listener = match TcpListener::bind(&addr).await {
        Ok(l) => {
            info!("[Prometheus/IPC] Telemetry server listening on http://{}/ (endpoints: /metrics, /stream)", addr);
            l
        }
        Err(e) => {
            error!("[Prometheus/IPC] Failed to bind port {}: {}", addr, e);
            return;
        }
    };

    loop {
        match listener.accept().await {
            Ok((mut socket, _)) => {
                let _ = socket.set_nodelay(true); // Disable Nagle's algorithm for low-latency streaming
                let metrics = metrics.clone();
                let stream_tx = stream_tx.clone();
                tokio::spawn(async move {
                    let mut buf = [0u8; 1024];
                    if let Ok(n) = socket.read(&mut buf).await {
                        if n == 0 {
                            return;
                        }
                        let req_str = String::from_utf8_lossy(&buf[..n]);
                        if req_str.starts_with("GET /stream") {
                            // Server-Sent Events (SSE) stream for Dashboard / Downstream consumers
                            let initial_header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n: connected\n\n";
                            if socket.write_all(initial_header.as_bytes()).await.is_err() {
                                return;
                            }
                            let mut rx = stream_tx.subscribe();
                            loop {
                                tokio::select! {
                                    msg_res = rx.recv() => {
                                        match msg_res {
                                            Ok(event) => {
                                                let frame = format!("data: {}\n\n", event);
                                                if socket.write_all(frame.as_bytes()).await.is_err() {
                                                    break;
                                                }
                                            }
                                            Err(broadcast::error::RecvError::Lagged(_)) => continue,
                                            Err(broadcast::error::RecvError::Closed) => break,
                                        }
                                    }
                                }
                            }
                        } else if req_str.starts_with("GET /metrics") || req_str.starts_with("GET / ") {
                            let body = metrics.render_prometheus_text();
                            let response = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                                body.len(),
                                body
                            );
                            let _ = socket.write_all(response.as_bytes()).await;
                        } else if req_str.starts_with("GET /health") {
                            let body = "{\"status\":\"healthy\",\"service\":\"iot-airquality-ingestor\"}";
                            let response = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                                body.len(),
                                body
                            );
                            let _ = socket.write_all(response.as_bytes()).await;
                        } else {
                            let _ = socket.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").await;
                        }
                    }
                });
            }
            Err(e) => {
                warn!("[Prometheus/IPC] Listener accept error: {}", e);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aqi_level_is_the_worst_sub_level() {
        assert_eq!(compute_aqi_level(450, 3.0, 500.0), 1);
        assert_eq!(compute_aqi_level(599, 4.9, 799.0), 1);
        assert_eq!(compute_aqi_level(600, 3.0, 500.0), 2); // CO2 boundary
        assert_eq!(compute_aqi_level(450, 25.0, 500.0), 4); // PM2.5 boundary
        assert_eq!(compute_aqi_level(450, 3.0, 4800.0), 4); // VOC boundary
        assert_eq!(compute_aqi_level(1500, 3.0, 500.0), 5);
    }

    #[test]
    fn stream_event_has_sample_fields_and_aqi_level() {
        let sample = MetricSample {
            device_id: "kitchen".into(),
            timestamp_ns: 1,
            co2_ppm: 1200,
            temperature_celsius: 21.0,
            humidity_percent: 40.0,
            pm25_ug_m3: 3.0,
            pm10_ug_m3: 4.0,
            tvoc_ppb: 500.0,
            pressure_hpa: 1013.0,
            battery_millivolts: 0,
            rssi_dbm: -60,
            status: 2,
        };
        let v: serde_json::Value = serde_json::from_str(&stream_event_json(&sample)).unwrap();
        assert_eq!(v["device_id"], "kitchen");
        assert_eq!(v["status"], 2);
        assert_eq!(v["aqi_level"], 4);
    }
}
