//! Prometheus Exposition Text Exporter (Standard 0.0.4)
//! Zero-allocation atomic counters and asynchronous HTTP /metrics endpoint.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, AtomicU32, Ordering};
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

    // 3. Circuit Breaker State (0 = Closed/OK, 1 = HalfOpen, 2 = Open/Tripped)
    pub circuit_breaker_state: AtomicU32,

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

    pub fn record_batch_result(&self, points_count: u64, latency_ms: u64, is_success: bool, cb_state: u32) {
        self.influx_batches_total.fetch_add(1, Ordering::Relaxed);
        self.influx_last_batch_latency_ms.store(latency_ms, Ordering::Relaxed);
        self.circuit_breaker_state.store(cb_state, Ordering::Relaxed);
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

        out.push_str("# HELP iot_circuit_breaker_state Circuit breaker state (0=Closed/OK, 1=HalfOpen, 2=Open)\n");
        out.push_str("# TYPE iot_circuit_breaker_state gauge\n");
        out.push_str(&format!("iot_circuit_breaker_state {}\n\n", self.circuit_breaker_state.load(Ordering::Relaxed)));

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

/// Runs an ultra-lightweight asynchronous HTTP 1.1 listener serving `/metrics` and live `/stream` SSE (0 External Crates)
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
                let metrics = metrics.clone();
                let stream_tx = stream_tx.clone();
                tokio::spawn(async move {
                    let mut buf = [0u8; 1024];
                    if let Ok(n) = socket.read(&mut buf).await {
                        if n == 0 {
                            return;
                        }
                        let req_str = String::from_utf8_lossy(&buf[..n]);
                        if req_str.starts_with("GET /stream") || req_str.starts_with("GET /internal/stream") {
                            // Server-Sent Events (SSE) stream for Dashboard / Downstream consumers
                            let initial_header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\nAccess-Control-Allow-Origin: *\r\n\r\n: connected\n\n";
                            if socket.write_all(initial_header.as_bytes()).await.is_err() {
                                return;
                            }
                            let mut rx = stream_tx.subscribe();
                            loop {
                                tokio::select! {
                                    msg_res = rx.recv() => {
                                        match msg_res {
                                            Ok(payload) => {
                                                let frame = format!("data: {}\n\n", payload);
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
