//! Runtime configuration loader for the IoT Air Quality Ingestor

use std::env;

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub mqtt_host: String,
    pub mqtt_port: u16,
    pub mqtt_client_id: String,
    pub mqtt_topic: String,
    pub influx_url: String,
    pub influx_org: String,
    pub influx_bucket: String,
    pub influx_token: String,
    pub batch_size: usize,
    pub flush_interval_secs: u64,
    pub circuit_breaker_threshold: u32,
    pub circuit_breaker_cooldown_secs: u64,
    pub metrics_port: u16,
    pub metrics_enabled: bool,
}

impl AppConfig {
    pub fn from_env() -> Self {
        Self {
            mqtt_host: env::var("MQTT_BROKER_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            mqtt_port: env::var("MQTT_BROKER_PORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(1883),
            mqtt_client_id: env::var("MQTT_CLIENT_ID")
                .unwrap_or_else(|_| "iot-airquality-ingestor-rust".to_string()),
            mqtt_topic: env::var("MQTT_TOPIC")
                .unwrap_or_else(|_| "sensors/+/airquality".to_string()),
            influx_url: env::var("INFLUXDB_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8086".to_string()),
            influx_org: env::var("INFLUXDB_ORG").unwrap_or_else(|_| "baremetal-iot".to_string()),
            influx_bucket: env::var("INFLUXDB_BUCKET")
                .unwrap_or_else(|_| "airquality".to_string()),
            influx_token: env::var("INFLUXDB_TOKEN")
                .unwrap_or_else(|_| "baremetal-dev-token-987654321".to_string()),
            batch_size: env::var("INGESTOR_BATCH_SIZE")
                .ok()
                .and_then(|b| b.parse().ok())
                .unwrap_or(12),
            flush_interval_secs: env::var("INGESTOR_FLUSH_INTERVAL_SECS")
                .ok()
                .and_then(|t| t.parse().ok())
                .unwrap_or(30),
            circuit_breaker_threshold: env::var("CB_FAILURE_THRESHOLD")
                .ok()
                .and_then(|t| t.parse().ok())
                .unwrap_or(3),
            circuit_breaker_cooldown_secs: env::var("CB_COOLDOWN_SECS")
                .ok()
                .and_then(|t| t.parse().ok())
                .unwrap_or(30),
            metrics_port: env::var("METRICS_PORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(9100),
            metrics_enabled: env::var("METRICS_ENABLED")
                .map(|v| v != "false" && v != "0")
                .unwrap_or(true),
        }
    }
}
