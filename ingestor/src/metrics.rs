//! Counters, AQI level and the local HTTP endpoint (`/stream`, `/metrics`).

use std::collections::HashMap;
use std::fmt::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast;
use tracing::warn;

use crate::sink::MetricSample;

#[derive(Default)]
pub struct IngestorMetrics {
    // MQTT packets
    pub packets_received_total: AtomicU64,
    pub packets_valid_total: AtomicU64,
    pub packets_dropped_total: AtomicU64,
    pub packets_spoofed_total: AtomicU64,

    // InfluxDB writes
    pub influx_batches_total: AtomicU64,
    pub influx_points_written_total: AtomicU64,
    pub influx_errors_total: AtomicU64,
    pub influx_last_batch_latency_ms: AtomicU64,

    // Samples waiting in memory for the next InfluxDB write (grows while InfluxDB is down)
    pub influx_buffered_samples: AtomicU64,

    // Unix time of the last accepted packet, per device id
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

    /// Prometheus text exposition format.
    pub fn render_prometheus_text(&self) -> String {
        let load = |v: &AtomicU64| v.load(Ordering::Relaxed);
        let rows = [
            ("iot_packets_received_total", "counter", "MQTT messages received", load(&self.packets_received_total)),
            ("iot_packets_valid_total", "counter", "Packets accepted", load(&self.packets_valid_total)),
            ("iot_packets_dropped_total", "counter", "Packets rejected, all causes", load(&self.packets_dropped_total)),
            ("iot_packets_spoofed_total", "counter", "Packets rejected: topic id differs from payload id", load(&self.packets_spoofed_total)),
            ("iot_influx_batches_total", "counter", "InfluxDB write attempts", load(&self.influx_batches_total)),
            ("iot_influx_points_written_total", "counter", "Samples written to InfluxDB", load(&self.influx_points_written_total)),
            ("iot_influx_errors_total", "counter", "Failed InfluxDB write attempts", load(&self.influx_errors_total)),
            ("iot_influx_last_batch_latency_ms", "gauge", "Duration of the last InfluxDB write attempt", load(&self.influx_last_batch_latency_ms)),
            ("iot_influx_buffered_samples", "gauge", "Samples waiting in memory for the next InfluxDB write", load(&self.influx_buffered_samples)),
        ];

        let mut out = String::with_capacity(2048);
        for (name, kind, help, value) in rows {
            let _ = write!(out, "# HELP {name} {help}\n# TYPE {name} {kind}\n{name} {value}\n\n");
        }
        out.push_str("# HELP iot_sensor_last_seen_seconds Unix time of the last accepted packet per node\n");
        out.push_str("# TYPE iot_sensor_last_seen_seconds gauge\n");
        if let Ok(map) = self.sensor_last_seen.read() {
            for (device_id, ts) in map.iter() {
                let _ = writeln!(out, "iot_sensor_last_seen_seconds{{device_id=\"{device_id}\"}} {ts}");
            }
        }
        out
    }
}

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

/// Minimal HTTP/1.1 server for local consumers: `/stream` (Server-Sent Events, one
/// JSON object per sample) and `/metrics` (Prometheus text).
pub async fn run_http_server(
    listener: TcpListener,
    metrics: Arc<IngestorMetrics>,
    stream_tx: broadcast::Sender<Arc<String>>,
) {
    loop {
        match listener.accept().await {
            Ok((socket, _)) => {
                tokio::spawn(handle_connection(socket, metrics.clone(), stream_tx.subscribe()));
            }
            Err(e) => warn!("[HTTP] Accept error: {}", e),
        }
    }
}

async fn handle_connection(
    mut socket: TcpStream,
    metrics: Arc<IngestorMetrics>,
    events: broadcast::Receiver<Arc<String>>,
) {
    // Only the request line matters; local clients send it in the first segment.
    let mut buf = [0u8; 1024];
    let n = match socket.read(&mut buf).await {
        Ok(n) if n > 0 => n,
        _ => return,
    };
    let request = String::from_utf8_lossy(&buf[..n]);

    if request.starts_with("GET /stream") {
        serve_stream(socket, events).await;
    } else if request.starts_with("GET /metrics") {
        let body = metrics.render_prometheus_text();
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let _ = socket.write_all(response.as_bytes()).await;
    } else {
        let _ = socket.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").await;
    }
}

/// Sends every event to one subscriber until it disconnects.
async fn serve_stream(mut socket: TcpStream, mut events: broadcast::Receiver<Arc<String>>) {
    let _ = socket.set_nodelay(true);
    let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\r\n: connected\n\n";
    if socket.write_all(header.as_bytes()).await.is_err() {
        return;
    }
    loop {
        match events.recv().await {
            Ok(event) => {
                if socket.write_all(format!("data: {event}\n\n").as_bytes()).await.is_err() {
                    return;
                }
            }
            // A slow subscriber skips the events it missed.
            Err(broadcast::error::RecvError::Lagged(_)) => continue,
            Err(broadcast::error::RecvError::Closed) => return,
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
