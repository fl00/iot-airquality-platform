//! Zero-Bloat Bare-Metal IoT Ingestor (Rust)
//! High-throughput MQTT Protobuf Subscriber & InfluxDB Micro-Batch Pipeline

mod circuit_breaker;
mod config;
mod metrics;
mod proto;
mod sink;

use std::sync::Arc;
use std::time::{Duration, Instant};
use prost::Message;
use rumqttc::{AsyncClient, MqttOptions, QoS};
use tokio::sync::mpsc;
use tokio::time::{interval, MissedTickBehavior};
use tracing::{error, info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::config::AppConfig;
use crate::metrics::IngestorMetrics;
use crate::proto::{air_quality_packet::Payload, AirQualityPacket};
use crate::sink::{InfluxDbSink, MetricSample, MetricSink, SinkError};

/// Samples kept in memory while InfluxDB is unreachable (about 7 h for one sensor at 5 s).
const MAX_BUFFERED_SAMPLES: usize = 5000;

/// Device ids are used as an Influx tag and in dashboard URLs.
fn is_valid_device_id(id: &str) -> bool {
    (1..=64).contains(&id.len())
        && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn is_finite_sample(s: &crate::proto::FullSample) -> bool {
    [s.temperature_celsius, s.humidity_percent, s.pm25_ug_m3, s.pm10_ug_m3, s.tvoc_ppb, s.pressure_hpa]
        .iter()
        .all(|v| v.is_finite())
}

/// Writes the buffer to InfluxDB. Returns false when the samples were kept for a later retry.
async fn flush(sink: &InfluxDbSink, metrics: &IngestorMetrics, buffer: &mut Vec<MetricSample>) -> bool {
    if buffer.is_empty() {
        return true;
    }
    let start = Instant::now();
    let res = sink.write_batch(buffer).await;
    let cb_state = sink.circuit_breaker().current_state_code();
    metrics.record_batch_result(buffer.len() as u64, start.elapsed().as_millis() as u64, res.is_ok(), cb_state);
    match res {
        Ok(()) => {
            buffer.clear();
            true
        }
        // InfluxDB rejected the data itself: retrying the same lines cannot succeed.
        Err(SinkError::ClientError(status, body)) if status == reqwest::StatusCode::BAD_REQUEST => {
            error!("[BatchEngine] InfluxDB rejected {} samples (HTTP 400), dropped: {}", buffer.len(), body);
            buffer.clear();
            true
        }
        Err(e) => {
            warn!("[BatchEngine] Write failed, {} samples kept for retry: {}", buffer.len(), e);
            false
        }
    }
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> anyhow::Result<()> {
    // Initialize low-overhead structured logging
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,iot_airquality_ingestor=debug".into()),
        )
        .with(tracing_subscriber::fmt::layer().compact())
        .init();

    info!("============================================================");
    info!("   Zero-Bloat Bare-Metal IoT Ingestor Engine (Rust v1.0)   ");
    info!("============================================================");

    let config = AppConfig::from_env();
    info!("Configuration loaded: MQTT={}:{} Topic='{}' Influx={} MetricsPort={}",
        config.mqtt_host, config.mqtt_port, config.mqtt_topic, config.influx_url, config.metrics_port);

    // Initialize Lock-Free Prometheus Metrics Registry & Downstream IPC Broadcast Stream
    let metrics = IngestorMetrics::new();
    let (stream_tx, _) = tokio::sync::broadcast::channel::<Arc<String>>(512);

    if config.metrics_enabled {
        // Local IPC only: the dashboard runs on the same host.
        let metrics_addr = format!("127.0.0.1:{}", config.metrics_port);
        tokio::spawn(metrics::run_metrics_server(metrics.clone(), stream_tx.clone(), metrics_addr));
    }

    // 1. Initialize MPSC Channel to isolate MQTT loop from Storage I/O
    let (tx_channel, mut rx_channel) = mpsc::channel::<MetricSample>(1024);

    // 2. Initialize InfluxDB Sink and shared state
    let sink = Arc::new(InfluxDbSink::new(&config));
    let batch_size = config.batch_size;
    let flush_secs = config.flush_interval_secs;

    // 3. Spawn the In-Memory Ring-Buffer Batch Processing Task
    let sink_task = {
        let sink = Arc::clone(&sink);
        let metrics = metrics.clone();
        tokio::spawn(async move {
            info!("[BatchEngine] Background flusher started (Capacity={}, Timeout={}s)", batch_size, flush_secs);
            let mut buffer: Vec<MetricSample> = Vec::with_capacity(batch_size);
            let mut ticker = interval(Duration::from_secs(flush_secs));
            ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
            // After a failed write, retry on the ticker only (not on every sample).
            let mut write_failed = false;

            loop {
                tokio::select! {
                    sample_opt = rx_channel.recv() => {
                        let Some(sample) = sample_opt else {
                            info!("[BatchEngine] Channel closed; flushing remaining {} samples.", buffer.len());
                            flush(&sink, &metrics, &mut buffer).await;
                            break;
                        };
                        buffer.push(sample);
                        if buffer.len() > MAX_BUFFERED_SAMPLES {
                            let excess = buffer.len() - MAX_BUFFERED_SAMPLES;
                            buffer.drain(..excess);
                            warn!("[BatchEngine] Retry buffer full: dropped {} oldest samples.", excess);
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
        })
    };

    // 4. Initialize MQTT Client
    let mut mqttoptions = MqttOptions::new(
        &config.mqtt_client_id,
        &config.mqtt_host,
        config.mqtt_port,
    );
    mqttoptions.set_keep_alive(Duration::from_secs(15));
    mqttoptions.set_clean_session(true);
    mqttoptions.set_max_packet_size(64 * 1024, 64 * 1024);
    if let (Some(user), Some(password)) = (&config.mqtt_user, &config.mqtt_password) {
        mqttoptions.set_credentials(user, password);
    }

    let (mqtt_client, mut eventloop) = AsyncClient::new(mqttoptions, 100);

    // 5. Ingestion Loop with Signal Handler for Graceful Shutdown
    // systemd stops services with SIGTERM; Ctrl-C sends SIGINT. Both flush the pending batch.
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut shutdown_signal = std::pin::pin!(async {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = sigterm.recv() => {}
        }
    });
    let mut last_known_samples = std::collections::HashMap::<String, crate::proto::FullSample>::new();

    loop {
        tokio::select! {
            _ = &mut shutdown_signal => {
                info!("[Shutdown] Termination signal received. Initiating graceful shutdown...");
                break;
            }
            notification = eventloop.poll() => {
                match notification {
                    Ok(rumqttc::Event::Incoming(rumqttc::Packet::Publish(publish))) => {
                        metrics.record_packet_received();
                        match AirQualityPacket::decode(&publish.payload[..]) {
                            Ok(packet) => {
                                // The id ends up in Influx line protocol: accept a strict charset only.
                                if !is_valid_device_id(&packet.device_id) {
                                    warn!("[Security] Dropped packet with invalid device id on {}", publish.topic);
                                    metrics.record_packet_dropped();
                                    continue;
                                }
                                // Anti-spoofing: the topic sensors/<id>/airquality must match the payload id.
                                if publish.topic.split('/').nth(1) != Some(packet.device_id.as_str()) {
                                    warn!("[Security] Dropped spoofed packet: topic '{}' != payload id '{}'", publish.topic, packet.device_id);
                                    metrics.record_packet_spoofed();
                                    continue;
                                }

                                let device_id = packet.device_id;
                                let base_ts_sec = if packet.base_timestamp_sec >= 1_000_000_000 {
                                    packet.base_timestamp_sec
                                } else {
                                    std::time::SystemTime::now()
                                        .duration_since(std::time::UNIX_EPOCH)
                                        .unwrap_or_default()
                                        .as_secs()
                                };
                                let timestamp_ns = base_ts_sec * 1_000_000_000;
                                metrics.record_packet_valid(&device_id, base_ts_sec);

                                match packet.payload {
                                    Some(Payload::FullSample(full)) => {
                                        if !is_finite_sample(&full) {
                                            warn!("[Ingest] Dropped packet with non-finite values from {}", device_id);
                                            metrics.record_packet_dropped();
                                            continue;
                                        }
                                        last_known_samples.insert(device_id.clone(), full.clone());

                                        let sample = MetricSample {
                                            device_id: device_id.clone(),
                                            timestamp_ns,
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
                                        };

                                        let _ = stream_tx.send(Arc::new(metrics::stream_event_json(&sample)));

                                        if let Err(err) = tx_channel.send(sample).await {
                                            error!("[Ingest] Failed to dispatch sample to batch channel: {}", err);
                                        }
                                    }
                                    Some(Payload::DeltaBatch(batch)) => {
                                        if let Some(base) = last_known_samples.get_mut(&device_id) {
                                            for delta in batch.samples {
                                                let offset_ns = (delta.time_offset_sec as u64) * 1_000_000_000;
                                                let delta_ts = timestamp_ns + offset_ns;

                                                base.co2_ppm = (base.co2_ppm as i64 + delta.co2_delta_ppm as i64).max(0) as u32;
                                                base.temperature_celsius += (delta.temp_delta_centi_deg as f32) / 100.0;
                                                base.humidity_percent = (base.humidity_percent + (delta.hum_delta_centi_pct as f32) / 100.0).clamp(0.0, 100.0);
                                                base.pm25_ug_m3 = (base.pm25_ug_m3 + (delta.pm25_delta_centi_ug as f32) / 100.0).max(0.0);

                                                let sample = MetricSample {
                                                    device_id: device_id.clone(),
                                                    timestamp_ns: delta_ts,
                                                    co2_ppm: base.co2_ppm,
                                                    temperature_celsius: base.temperature_celsius,
                                                    humidity_percent: base.humidity_percent,
                                                    pm25_ug_m3: base.pm25_ug_m3,
                                                    pm10_ug_m3: base.pm10_ug_m3,
                                                    tvoc_ppb: base.tvoc_ppb,
                                                    pressure_hpa: base.pressure_hpa,
                                                    battery_millivolts: packet.battery_millivolts,
                                                    rssi_dbm: packet.rssi_dbm,
                                                    status: packet.status,
                                                };

                                                let _ = stream_tx.send(Arc::new(metrics::stream_event_json(&sample)));

                                                if let Err(err) = tx_channel.send(sample).await {
                                                    error!("[Ingest] Failed to dispatch delta sample: {}", err);
                                                }
                                            }
                                        } else {
                                            warn!("[Ingest] Received DeltaBatch before initial FullSample keyframe from {}", device_id);
                                        }
                                    }
                                    None => {
                                        warn!("[Ingest] Empty payload received in packet from {}", device_id);
                                    }
                                }
                            }
                            Err(err) => {
                                metrics.record_packet_dropped();
                                warn!("[Protobuf] Decode failed for packet on {}: {}", publish.topic, err);
                            }
                        }
                    }
                    Ok(rumqttc::Event::Incoming(rumqttc::Packet::ConnAck(_))) => {
                        // Sessions are clean: the subscription must be renewed after every reconnect.
                        match mqtt_client.subscribe(&config.mqtt_topic, QoS::AtLeastOnce).await {
                            Ok(()) => info!("[MQTT] Connected; subscribed to '{}'.", config.mqtt_topic),
                            Err(e) => error!("[MQTT] Subscribe failed: {}", e),
                        }
                    }
                    Ok(_) => {}
                    Err(e) => {
                        warn!("[MQTT] Connection poll error: {}. Retrying in 1s...", e);
                        tokio::time::sleep(Duration::from_secs(1)).await;
                    }
                }
            }
        }
    }

    // Drop channel transmitter so background flusher drains and terminates
    drop(tx_channel);
    let _ = sink_task.await;
    info!("[Shutdown] Ingestor stopped cleanly.");

    Ok(())
}
