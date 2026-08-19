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
use crate::sink::MetricSample;

#[derive(Clone, Debug)]
pub struct TelemetryBroadcast {
    pub binary_b64: Arc<String>,
    pub json: Arc<String>,
}

const B64_TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Fast, zero-allocation base64 encoder for exact 16-byte buffer (produces exact 24-char ASCII string)
pub fn encode_base64_16(src: &[u8; 16]) -> String {
    let mut out = [0u8; 24];
    let mut s = 0;
    let mut d = 0;
    for _ in 0..5 {
        let b0 = src[s] as usize;
        let b1 = src[s + 1] as usize;
        let b2 = src[s + 2] as usize;
        out[d] = B64_TABLE[(b0 >> 2) & 0x3F];
        out[d + 1] = B64_TABLE[((b0 & 0x03) << 4) | ((b1 >> 4) & 0x0F)];
        out[d + 2] = B64_TABLE[((b1 & 0x0F) << 2) | ((b2 >> 6) & 0x03)];
        out[d + 3] = B64_TABLE[b2 & 0x3F];
        s += 3;
        d += 4;
    }
    let b0 = src[15] as usize;
    out[d] = B64_TABLE[(b0 >> 2) & 0x3F];
    out[d + 1] = B64_TABLE[(b0 & 0x03) << 4];
    out[d + 2] = b'=';
    out[d + 3] = b'=';

    String::from_utf8(out.to_vec()).unwrap_or_default()
}

/// Computes normalized AQI level (1..5) using worst-case limiting factor
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
    let sub_tvoc = if tvoc_ppb < 65.0 {
        1
    } else if tvoc_ppb < 220.0 {
        2
    } else if tvoc_ppb < 660.0 {
        3
    } else if tvoc_ppb < 2200.0 {
        4
    } else {
        5
    };
    sub_co2.max(sub_pm25).max(sub_tvoc)
}

/// Maps device_id to 4-bit sensor index (0..15) for 1-byte packing in Byte 15
pub fn sensor_id_to_index(device_id: &str) -> u8 {
    match device_id {
        "sensor-esp32-01" => 0,
        "sensor-esp32-02" => 1,
        "sensor-esp32-03" => 2,
        "sensor-esp32-04" => 3,
        "sensor-esp32-05" => 4,
        "sensor-esp32-06" => 5,
        "sensor-esp32-07" => 6,
        "sensor-esp32-08" => 7,
        _ => {
            if let Some(num_str) = device_id.rsplit('-').next() {
                if let Ok(num) = num_str.parse::<u8>() {
                    return (num.saturating_sub(1)) & 0x0F;
                }
            }
            0
        }
    }
}

/// Packs MetricSample into ultra-compact 16-byte big-endian binary struct and Base64-encodes it
pub fn pack_binary_frame_b64(sample: &MetricSample) -> String {
    let ts = (sample.timestamp_ns / 1_000_000_000) as u32;
    let co2 = sample.co2_ppm.min(65535) as u16;
    let temp_centi = (sample.temperature_celsius * 100.0).round().clamp(-32768.0, 32767.0) as i16;
    let hum_centi = (sample.humidity_percent * 100.0).round().clamp(0.0, 65535.0) as u16;
    let pm25_centi = (sample.pm25_ug_m3 * 100.0).round().clamp(0.0, 65535.0) as u16;
    let bat = sample.battery_millivolts.min(65535) as u16;
    let rssi = sample.rssi_dbm.clamp(-128, 127) as i8;

    let aqi_level = compute_aqi_level(sample.co2_ppm, sample.pm25_ug_m3, sample.tvoc_ppb);
    let sensor_idx = sensor_id_to_index(&sample.device_id);
    let combined_byte = ((aqi_level & 0x0F) << 4) | (sensor_idx & 0x0F);

    let mut buf = [0u8; 16];
    buf[0..4].copy_from_slice(&ts.to_be_bytes());
    buf[4..6].copy_from_slice(&co2.to_be_bytes());
    buf[6..8].copy_from_slice(&temp_centi.to_be_bytes());
    buf[8..10].copy_from_slice(&hum_centi.to_be_bytes());
    buf[10..12].copy_from_slice(&pm25_centi.to_be_bytes());
    buf[12..14].copy_from_slice(&bat.to_be_bytes());
    buf[14] = rssi as u8;
    buf[15] = combined_byte;

    encode_base64_16(&buf)
}

/// Runs an ultra-lightweight asynchronous HTTP 1.1 listener serving `/metrics` and live `/stream` SSE (0 External Crates)
pub async fn run_metrics_server(
    metrics: Arc<IngestorMetrics>,
    stream_tx: broadcast::Sender<TelemetryBroadcast>,
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
                        if req_str.starts_with("GET /stream") || req_str.starts_with("GET /internal/stream") {
                            // Check if subscriber requested human-readable JSON debug format
                            let is_json_requested = req_str.contains("format=json");

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
                                            Ok(event) => {
                                                let payload = if is_json_requested {
                                                    &event.json
                                                } else {
                                                    &event.binary_b64
                                                };
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
