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
use crate::sink::{InfluxDbSink, MetricSample, MetricSink};

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
        let metrics_addr = format!("0.0.0.0:{}", config.metrics_port);
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
            let mut batch_buffer: Vec<MetricSample> = Vec::with_capacity(batch_size);
            let mut ticker = interval(Duration::from_secs(flush_secs));
            ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);

            loop {
                tokio::select! {
                    sample_opt = rx_channel.recv() => {
                        match sample_opt {
                            Some(sample) => {
                                batch_buffer.push(sample);
                                if batch_buffer.len() >= batch_size {
                                    let start = Instant::now();
                                    let count = batch_buffer.len() as u64;
                                    let res = sink.write_batch(&batch_buffer).await;
                                    let latency_ms = start.elapsed().as_millis() as u64;
                                    let cb_state = sink.circuit_breaker().current_state_code();
                                    metrics.record_batch_result(count, latency_ms, res.is_ok(), cb_state);
                                    if let Err(e) = res {
                                        error!("[BatchEngine] Batch write error: {:?}", e);
                                    }
                                    batch_buffer.clear();
                                }
                            }
                            None => {
                                info!("[BatchEngine] Ingestion channel closed; flushing remaining {} samples.", batch_buffer.len());
                                if !batch_buffer.is_empty() {
                                    let start = Instant::now();
                                    let count = batch_buffer.len() as u64;
                                    let res = sink.write_batch(&batch_buffer).await;
                                    let latency_ms = start.elapsed().as_millis() as u64;
                                    let cb_state = sink.circuit_breaker().current_state_code();
                                    metrics.record_batch_result(count, latency_ms, res.is_ok(), cb_state);
                                    batch_buffer.clear();
                                }
                                break;
                            }
                        }
                    }
                    _ = ticker.tick() => {
                        if !batch_buffer.is_empty() {
                            info!("[BatchEngine] Periodic timeout flush triggered for {} samples.", batch_buffer.len());
                            let start = Instant::now();
                            let count = batch_buffer.len() as u64;
                            let res = sink.write_batch(&batch_buffer).await;
                            let latency_ms = start.elapsed().as_millis() as u64;
                            let cb_state = sink.circuit_breaker().current_state_code();
                            metrics.record_batch_result(count, latency_ms, res.is_ok(), cb_state);
                            if let Err(e) = res {
                                error!("[BatchEngine] Periodic batch write error: {:?}", e);
                            }
                            batch_buffer.clear();
                        }
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

    let (mqtt_client, mut eventloop) = AsyncClient::new(mqttoptions, 100);

    // Subscribe to telemetry topic
    mqtt_client
        .subscribe(&config.mqtt_topic, QoS::AtLeastOnce)
        .await
        .expect("Failed to subscribe to MQTT telemetry topic");
    info!("[MQTT] Subscribed to topic: {}", config.mqtt_topic);

    // 5. Ingestion Loop with Signal Handler for Graceful Shutdown
    let mut shutdown_signal = std::pin::pin!(tokio::signal::ctrl_c());
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
                                // Anti-Spoofing Topic Verification (CWE-290 / P0.1)
                                let topic_parts: Vec<&str> = publish.topic.split('/').collect();
                                if topic_parts.len() >= 3 && topic_parts[1] != packet.device_id {
                                    warn!("[Security] Dropped spoofed packet: topic id '{}' != payload id '{}'", topic_parts[1], packet.device_id);
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

                                        if let Ok(json_str) = serde_json::to_string(&sample) {
                                            let _ = stream_tx.send(Arc::new(json_str));
                                        }

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

                                                if let Ok(json_str) = serde_json::to_string(&sample) {
                                                    let _ = stream_tx.send(Arc::new(json_str));
                                                }

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
                        info!("[MQTT] Connected & Broker acknowledged.");
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
