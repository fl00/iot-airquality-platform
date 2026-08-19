//! Metric Sink Abstraction & InfluxDB v2 Line Protocol Batch Engine

use std::fmt::Write;
use std::sync::Arc;
use std::time::Duration;
use async_trait::async_trait;
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use thiserror::Error;
use tracing::{debug, error, info, warn};

use crate::circuit_breaker::CircuitBreaker;
use crate::config::AppConfig;

#[derive(Error, Debug)]
pub enum SinkError {
    #[error("HTTP transport error: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("Upstream server error (status: {0})")]
    ServerError(reqwest::StatusCode),
    #[error("Client error (status: {0}): {1}")]
    ClientError(reqwest::StatusCode, String),
    #[error("Circuit breaker open - write rejected")]
    CircuitBreakerOpen,
    #[error("Serialization error: {0}")]
    Serialization(String),
}

/// Normalized in-memory telemetry metric sample
#[derive(Clone, Debug, serde::Serialize)]
pub struct MetricSample {
    pub device_id: String,
    pub timestamp_ns: u64,
    pub co2_ppm: u32,
    pub temperature_celsius: f32,
    pub humidity_percent: f32,
    pub pm25_ug_m3: f32,
    pub pm10_ug_m3: f32,
    pub tvoc_ppb: f32,
    pub pressure_hpa: f32,
    pub battery_millivolts: u32,
    pub rssi_dbm: i32,
    pub status: i32,
}

#[async_trait]
pub trait MetricSink: Send + Sync {
    async fn write_batch(&self, samples: &[MetricSample]) -> Result<(), SinkError>;
}

/// High-performance InfluxDB v2 Sink using Line Protocol
pub struct InfluxDbSink {
    client: reqwest::Client,
    write_url: String,
    circuit_breaker: Arc<CircuitBreaker>,
}

impl InfluxDbSink {
    pub fn new(config: &AppConfig) -> Self {
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Token {}", config.influx_token))
                .expect("Valid Influx token header"),
        );
        headers.insert(
            CONTENT_TYPE,
            HeaderValue::from_static("text/plain; charset=utf-8"),
        );

        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(5))
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(4)
            .tcp_nodelay(true)
            .build()
            .expect("Failed to build InfluxDB HTTP client");

        let write_url = format!(
            "{}/api/v2/write?db={}&bucket={}&org={}&precision=ns",
            config.influx_url.trim_end_matches('/'),
            config.influx_bucket,
            config.influx_bucket,
            config.influx_org
        );

        let circuit_breaker = Arc::new(CircuitBreaker::new(
            config.circuit_breaker_threshold,
            Duration::from_secs(config.circuit_breaker_cooldown_secs),
        ));

        Self {
            client,
            write_url,
            circuit_breaker,
        }
    }

    pub fn circuit_breaker(&self) -> &Arc<CircuitBreaker> {
        &self.circuit_breaker
    }

    /// Formats a batch of `MetricSample` records into compact Influx Line Protocol lines (Zero-Allocation)
    pub fn format_line_protocol(samples: &[MetricSample]) -> String {
        let mut buffer = String::with_capacity(samples.len() * 128);
        for s in samples {
            // air_quality,device_id=sensor-01 co2=650i,temperature=22.45,humidity=48.20,pm25=8.10,pm10=12.96,tvoc=85.00,pressure=1013.25,battery_mv=3850i,rssi=-65i,status=0i 1692374400000000000
            let _ = writeln!(
                buffer,
                "air_quality,device_id={} co2={}i,temperature={:.2},humidity={:.2},pm25={:.2},pm10={:.2},tvoc={:.2},pressure={:.2},battery_mv={}i,rssi={}i,status={}i {}",
                s.device_id,
                s.co2_ppm,
                s.temperature_celsius,
                s.humidity_percent,
                s.pm25_ug_m3,
                s.pm10_ug_m3,
                s.tvoc_ppb,
                s.pressure_hpa,
                s.battery_millivolts,
                s.rssi_dbm,
                s.status,
                s.timestamp_ns
            );
        }
        buffer
    }
}

#[async_trait]
impl MetricSink for InfluxDbSink {
    async fn write_batch(&self, samples: &[MetricSample]) -> Result<(), SinkError> {
        if samples.is_empty() {
            return Ok(());
        }

        if !self.circuit_breaker.can_execute() {
            warn!("[Sink] Write dropped: Circuit breaker is OPEN.");
            return Err(SinkError::CircuitBreakerOpen);
        }

        let body = Self::format_line_protocol(samples);
        debug!("[Sink] Flushing batch of {} points to InfluxDB", samples.len());

        let response = match self.client.post(&self.write_url).body(body).send().await {
            Ok(res) => res,
            Err(err) => {
                self.circuit_breaker.record_failure();
                error!("[Sink] HTTP request error: {}", err);
                return Err(SinkError::Transport(err));
            }
        };

        let status = response.status();
        if status.is_success() {
            self.circuit_breaker.record_success();
            debug!("[Sink] InfluxDB write successful (HTTP {})", status);
            Ok(())
        } else if status.is_server_error() {
            self.circuit_breaker.record_failure();
            error!("[Sink] InfluxDB server error (HTTP {})", status);
            Err(SinkError::ServerError(status))
        } else {
            let err_text = response.text().await.unwrap_or_default();
            error!("[Sink] InfluxDB client error (HTTP {}): {}", status, err_text);
            Err(SinkError::ClientError(status, err_text))
        }
    }
}
