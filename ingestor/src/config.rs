//! Runtime configuration, read from environment variables (see the root README).

use std::env;
use std::str::FromStr;

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub mqtt_host: String,
    pub mqtt_port: u16,
    pub mqtt_user: Option<String>,
    pub mqtt_password: Option<String>,
    pub influx_url: String,
    pub influx_org: String,
    pub influx_bucket: String,
    pub influx_token: String,
    pub batch_size: usize,
    pub flush_interval_secs: u64,
    pub http_port: u16,
}

fn non_empty(key: &str) -> Option<String> {
    env::var(key).ok().filter(|v| !v.is_empty())
}

/// Value of `key`, or `default` when unset. An invalid value stops the service:
/// silently falling back to the default would hide the mistake.
fn env_or<T: FromStr>(key: &str, default: T) -> T {
    match non_empty(key) {
        Some(raw) => raw.parse().unwrap_or_else(|_| panic!("{key}: invalid value '{raw}'")),
        None => default,
    }
}

impl AppConfig {
    pub fn from_env() -> Self {
        Self {
            mqtt_host: env_or("MQTT_BROKER_HOST", "127.0.0.1".to_string()),
            mqtt_port: env_or("MQTT_BROKER_PORT", 1883),
            mqtt_user: non_empty("MQTT_USER"),
            mqtt_password: non_empty("MQTT_PASSWORD"),
            influx_url: env_or("INFLUXDB_URL", "http://127.0.0.1:8086".to_string()),
            influx_org: env_or("INFLUXDB_ORG", "baremetal-iot".to_string()),
            influx_bucket: env_or("INFLUXDB_BUCKET", "airquality".to_string()),
            // No default on purpose: a missing token must fail at start-up, not at the first write.
            influx_token: non_empty("INFLUXDB_TOKEN").expect("INFLUXDB_TOKEN is not set (see .env.example)"),
            batch_size: env_or("INGESTOR_BATCH_SIZE", 12),
            flush_interval_secs: env_or("INGESTOR_FLUSH_INTERVAL_SECS", 30),
            http_port: env_or("METRICS_PORT", 9100),
        }
    }
}
