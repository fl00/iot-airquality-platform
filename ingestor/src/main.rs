//! IoT Air Quality ingestor.
//!
//! MQTT (Protobuf) -> validation -> InfluxDB (batched line protocol)
//!                               -> live JSON stream for the dashboard (`/stream`)

mod config;
mod metrics;
mod proto;
mod sink;

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use prost::Message;
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use tokio::signal::unix::{signal, SignalKind};
use tokio::sync::{broadcast, mpsc};
use tokio::time::{interval, MissedTickBehavior};
use tracing::{error, info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::config::AppConfig;
use crate::metrics::IngestorMetrics;
use crate::proto::{AirQualityPacket, FullSample};
use crate::sink::{InfluxDbSink, MetricSample, WriteError};

/// Samples kept in memory while InfluxDB is unreachable (about 7 h for one sensor at 5 s).
const MAX_BUFFERED_SAMPLES: usize = 5000;

/// Timestamps below this value (2001-09-09) mean "device clock not synchronised".
const MIN_VALID_UNIX_SECS: u64 = 1_000_000_000;

/// Device ids are used as an InfluxDB tag and in dashboard URLs.
/// Same rule as the wire contract: `[A-Za-z0-9_-]{1,31}`.
fn is_valid_device_id(id: &str) -> bool {
    (1..=31).contains(&id.len())
        && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn is_finite_sample(s: &FullSample) -> bool {
    [s.temperature_celsius, s.humidity_percent, s.pm25_ug_m3, s.pm10_ug_m3, s.tvoc_ppb, s.pressure_hpa]
        .iter()
        .all(|v| v.is_finite())
}

/// Decodes and validates one MQTT message. `Err` carries the reason for the drop.
fn parse_packet(topic: &str, payload: &[u8], metrics: &IngestorMetrics) -> Result<MetricSample, String> {
    let packet = AirQualityPacket::decode(payload).map_err(|e| {
        metrics.record_packet_dropped();
        format!("Protobuf decode failed: {e}")
    })?;

    if !is_valid_device_id(&packet.device_id) {
        metrics.record_packet_dropped();
        return Err("invalid device id".into());
    }
    // Anti-spoofing: the topic sensors/<id>/airquality must carry the payload id.
    if topic.split('/').nth(1) != Some(packet.device_id.as_str()) {
        metrics.record_packet_spoofed();
        return Err(format!("topic does not match payload id '{}'", packet.device_id));
    }
    let Some(full) = packet.full_sample.filter(is_finite_sample) else {
        metrics.record_packet_dropped();
        return Err(format!("missing or non-finite sample from '{}'", packet.device_id));
    };

    // A device without a synchronised clock sends 0: use the reception time instead.
    let timestamp_sec = if packet.base_timestamp_sec >= MIN_VALID_UNIX_SECS {
        packet.base_timestamp_sec
    } else {
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
    };
    metrics.record_packet_valid(&packet.device_id, timestamp_sec);

    Ok(MetricSample {
        device_id: packet.device_id,
        timestamp_ns: timestamp_sec * 1_000_000_000,
        co2_ppm: full.co2_ppm,
        temperature_celsius: full.temperature_celsius,
        humidity_percent: full.humidity_percent,
        pm25_ug_m3: full.pm25_ug_m3,
        pm10_ug_m3: full.pm10_ug_m3,
        tvoc_ppb: full.tvoc_ppb,
        pressure_hpa: full.pressure_hpa,
        battery_millivolts: packet.battery_millivolts,
        rssi_dbm: packet.rssi_dbm,
        status: packet.status,
    })
}

/// Writes the buffer to InfluxDB. Returns false when the samples were kept for a later retry.
async fn flush(sink: &InfluxDbSink, metrics: &IngestorMetrics, buffer: &mut Vec<MetricSample>) -> bool {
    if buffer.is_empty() {
        return true;
    }
    let start = Instant::now();
    let res = sink.write_batch(buffer).await;
    metrics.record_batch_result(buffer.len() as u64, start.elapsed().as_millis() as u64, res.is_ok());
    let written = match res {
        Ok(()) => true,
        Err(WriteError::Rejected(detail)) => {
            error!("[Influx] {} samples rejected and dropped: {}", buffer.len(), detail);
            true
        }
        Err(WriteError::Retryable(detail)) => {
            warn!("[Influx] Write failed, {} samples kept for retry: {}", buffer.len(), detail);
            false
        }
    };
    if written {
        buffer.clear();
    }
    metrics.influx_buffered_samples.store(buffer.len() as u64, Ordering::Relaxed);
    written
}

/// Batches samples and writes them to InfluxDB when `batch_size` is reached or every
/// `flush_secs`. After a failed write, retries happen on the timer only.
async fn run_batch_writer(
    mut rx: mpsc::Receiver<MetricSample>,
    sink: InfluxDbSink,
    metrics: Arc<IngestorMetrics>,
    batch_size: usize,
    flush_secs: u64,
) {
    let mut buffer: Vec<MetricSample> = Vec::with_capacity(batch_size);
    let mut ticker = interval(Duration::from_secs(flush_secs));
    ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let mut write_failed = false;

    loop {
        tokio::select! {
            sample = rx.recv() => {
                let Some(sample) = sample else {
                    // Channel closed: shutdown. One last attempt to write what is pending.
                    flush(&sink, &metrics, &mut buffer).await;
                    return;
                };
                buffer.push(sample);
                if buffer.len() > MAX_BUFFERED_SAMPLES {
                    let excess = buffer.len() - MAX_BUFFERED_SAMPLES;
                    buffer.drain(..excess);
                    warn!("[Influx] Retry buffer full: dropped {} oldest samples.", excess);
                }
                if buffer.len() >= batch_size && !write_failed {
                    write_failed = !flush(&sink, &metrics, &mut buffer).await;
                }
            }
            _ = ticker.tick() => {
                write_failed = !flush(&sink, &metrics, &mut buffer).await;
            }
        }
    }
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .with(tracing_subscriber::fmt::layer().compact())
        .init();

    let config = AppConfig::from_env();
    info!(
        "Starting: MQTT={}:{} topic='{}' InfluxDB={} http=127.0.0.1:{}",
        config.mqtt_host, config.mqtt_port, config.mqtt_topic, config.influx_url, config.metrics_port
    );

    let metrics = IngestorMetrics::new();
    let (stream_tx, _) = broadcast::channel::<Arc<String>>(512);
    if config.metrics_enabled {
        // Local consumers only: the dashboard runs on the same host.
        let addr = format!("127.0.0.1:{}", config.metrics_port);
        tokio::spawn(metrics::run_metrics_server(metrics.clone(), stream_tx.clone(), addr));
    }

    // The channel decouples MQTT reception from InfluxDB I/O.
    let (sample_tx, sample_rx) = mpsc::channel::<MetricSample>(1024);
    let writer = tokio::spawn(run_batch_writer(
        sample_rx,
        InfluxDbSink::new(&config),
        metrics.clone(),
        config.batch_size,
        config.flush_interval_secs,
    ));

    let mut mqtt_options = MqttOptions::new(&config.mqtt_client_id, &config.mqtt_host, config.mqtt_port);
    mqtt_options.set_keep_alive(Duration::from_secs(15));
    mqtt_options.set_clean_session(true);
    if let (Some(user), Some(password)) = (&config.mqtt_user, &config.mqtt_password) {
        mqtt_options.set_credentials(user, password);
    }
    let (mqtt_client, mut eventloop) = AsyncClient::new(mqtt_options, 100);

    // systemd stops services with SIGTERM; Ctrl-C sends SIGINT. Both flush the pending batch.
    let mut sigterm = signal(SignalKind::terminate())?;
    let mut shutdown = std::pin::pin!(async {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = sigterm.recv() => {}
        }
    });

    loop {
        tokio::select! {
            _ = &mut shutdown => {
                info!("Termination signal received, flushing pending samples.");
                break;
            }
            event = eventloop.poll() => match event {
                Ok(Event::Incoming(Packet::ConnAck(_))) => {
                    // Sessions are clean: the subscription must be renewed after every reconnect.
                    match mqtt_client.subscribe(&config.mqtt_topic, QoS::AtLeastOnce).await {
                        Ok(()) => info!("[MQTT] Connected; subscribed to '{}'.", config.mqtt_topic),
                        Err(e) => error!("[MQTT] Subscribe failed: {}", e),
                    }
                }
                Ok(Event::Incoming(Packet::Publish(publish))) => {
                    metrics.record_packet_received();
                    match parse_packet(&publish.topic, &publish.payload, &metrics) {
                        Ok(sample) => {
                            let _ = stream_tx.send(Arc::new(metrics::stream_event_json(&sample)));
                            if sample_tx.send(sample).await.is_err() {
                                error!("Batch writer stopped unexpectedly.");
                                break;
                            }
                        }
                        Err(reason) => warn!("[MQTT] Dropped packet on '{}': {}", publish.topic, reason),
                    }
                }
                Ok(_) => {}
                Err(e) => {
                    warn!("[MQTT] Connection error: {}. Retrying in 1 s.", e);
                    tokio::time::sleep(Duration::from_secs(1)).await;
                }
            }
        }
    }

    drop(sample_tx); // closes the channel: the writer flushes and exits
    let _ = writer.await;
    info!("Stopped cleanly.");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode(device_id: &str, pressure: f32) -> Vec<u8> {
        AirQualityPacket {
            device_id: device_id.into(),
            base_timestamp_sec: 1_794_000_000,
            full_sample: Some(FullSample { co2_ppm: 600, pressure_hpa: pressure, ..Default::default() }),
            ..Default::default()
        }
        .encode_to_vec()
    }

    #[test]
    fn device_id_charset() {
        assert!(is_valid_device_id("sensor-esp32-01"));
        assert!(is_valid_device_id("kitchen_2"));
        assert!(!is_valid_device_id(""));
        assert!(!is_valid_device_id("a b"));
        assert!(!is_valid_device_id("a,x=1"));
        assert!(is_valid_device_id(&"a".repeat(31)));
        assert!(!is_valid_device_id(&"a".repeat(32)));
    }

    #[test]
    fn valid_packet_is_accepted_with_device_time() {
        let m = IngestorMetrics::new();
        let s = parse_packet("sensors/kitchen/airquality", &encode("kitchen", 1013.0), &m).unwrap();
        assert_eq!((s.device_id.as_str(), s.co2_ppm, s.timestamp_ns), ("kitchen", 600, 1_794_000_000_000_000_000));
    }

    #[test]
    fn invalid_packets_are_rejected() {
        let m = IngestorMetrics::new();
        assert!(parse_packet("sensors/other/airquality", &encode("kitchen", 1013.0), &m).is_err());
        assert!(parse_packet("sensors/a b/airquality", &encode("a b", 1013.0), &m).is_err());
        assert!(parse_packet("sensors/kitchen/airquality", &encode("kitchen", f32::NAN), &m).is_err());
        assert!(parse_packet("sensors/kitchen/airquality", b"\xff\xff", &m).is_err());
        assert_eq!(m.packets_dropped_total.load(Ordering::Relaxed), 4);
        assert_eq!(m.packets_spoofed_total.load(Ordering::Relaxed), 1);
    }
}
