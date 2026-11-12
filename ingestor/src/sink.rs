//! InfluxDB v2 writer (line protocol over HTTP).

use std::fmt::Write;
use std::time::Duration;
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use reqwest::StatusCode;

use crate::config::AppConfig;

/// One measurement, ready to be stored and streamed.
#[derive(Clone, Debug, serde::Serialize)]
pub struct MetricSample {
    pub device_id: String,
    pub timestamp_ns: u64,
    /// Packet counter of the node; restarts at 1 after a node reboot.
    pub sequence_number: u32,
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

#[derive(Debug)]
pub enum WriteError {
    /// InfluxDB unreachable or failing (network, 5xx, 401...): the same batch can be retried.
    Retryable(String),
    /// InfluxDB refused the data itself (HTTP 400 malformed, 422 outside the retention
    /// period): retrying cannot succeed.
    Rejected(String),
}

pub struct InfluxDbSink {
    client: reqwest::Client,
    write_url: String,
}

impl InfluxDbSink {
    pub fn new(config: &AppConfig) -> Self {
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Token {}", config.influx_token))
                .expect("INFLUXDB_TOKEN contains characters that are not valid in an HTTP header"),
        );
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("text/plain; charset=utf-8"));

        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(5))
            .build()
            .expect("Failed to build InfluxDB HTTP client");

        let write_url = format!(
            "{}/api/v2/write?bucket={}&org={}&precision=ns",
            config.influx_url.trim_end_matches('/'),
            config.influx_bucket,
            config.influx_org
        );

        Self { client, write_url }
    }

    pub async fn write_batch(&self, samples: &[MetricSample]) -> Result<(), WriteError> {
        let body = format_line_protocol(samples);
        let response = self
            .client
            .post(&self.write_url)
            .body(body)
            .send()
            .await
            .map_err(|e| WriteError::Retryable(e.to_string()))?;

        let status = response.status();
        if status.is_success() {
            return Ok(());
        }
        let detail = format!("HTTP {}: {}", status, response.text().await.unwrap_or_default());
        if status == StatusCode::BAD_REQUEST || status == StatusCode::UNPROCESSABLE_ENTITY {
            Err(WriteError::Rejected(detail))
        } else {
            Err(WriteError::Retryable(detail))
        }
    }
}

/// One line per sample:
/// `air_quality,device_id=<id> co2=650i,temperature=22.45,...,status=0i,seq=42i <timestamp_ns>`
///
/// A node reports 0 for a missing sensor. 0 ppm CO2 and 0 hPa cannot be real
/// measurements, so these two fields are omitted instead of polluting the averages.
/// `device_id` is validated upstream (no escaping needed).
pub fn format_line_protocol(samples: &[MetricSample]) -> String {
    let mut out = String::with_capacity(samples.len() * 160);
    for s in samples {
        let _ = write!(out, "air_quality,device_id={} ", s.device_id);
        if s.co2_ppm > 0 {
            let _ = write!(out, "co2={}i,", s.co2_ppm);
        }
        if s.pressure_hpa > 0.0 {
            let _ = write!(out, "pressure={:.2},", s.pressure_hpa);
        }
        let _ = writeln!(
            out,
            "temperature={:.2},humidity={:.2},pm25={:.2},pm10={:.2},tvoc={:.2},battery_mv={}i,rssi={}i,status={}i,seq={}i {}",
            s.temperature_celsius,
            s.humidity_percent,
            s.pm25_ug_m3,
            s.pm10_ug_m3,
            s.tvoc_ppb,
            s.battery_millivolts,
            s.rssi_dbm,
            s.status,
            s.sequence_number,
            s.timestamp_ns
        );
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> MetricSample {
        MetricSample {
            device_id: "kitchen".into(),
            timestamp_ns: 1_794_000_000_000_000_000,
            sequence_number: 42,
            co2_ppm: 650,
            temperature_celsius: 22.456,
            humidity_percent: 48.2,
            pm25_ug_m3: 8.1,
            pm10_ug_m3: 12.0,
            tvoc_ppb: 500.0,
            pressure_hpa: 1013.25,
            battery_millivolts: 0,
            rssi_dbm: -65,
            status: 0,
        }
    }

    #[test]
    fn line_protocol_full_sample() {
        assert_eq!(
            format_line_protocol(&[sample()]),
            "air_quality,device_id=kitchen co2=650i,pressure=1013.25,temperature=22.46,humidity=48.20,\
             pm25=8.10,pm10=12.00,tvoc=500.00,battery_mv=0i,rssi=-65i,status=0i,seq=42i 1794000000000000000\n"
        );
    }

    #[test]
    fn line_protocol_omits_impossible_zeros() {
        let mut s = sample();
        s.co2_ppm = 0;
        s.pressure_hpa = 0.0;
        let line = format_line_protocol(&[s]);
        assert!(!line.contains("co2=") && !line.contains("pressure="), "{line}");
        assert!(line.starts_with("air_quality,device_id=kitchen temperature="), "{line}");
    }
}
