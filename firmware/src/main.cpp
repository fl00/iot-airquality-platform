/**
 * ==============================================================================
 * IoT Air Quality Platform - ESP32 sensor node firmware
 * Hardware:     ESP32 DevKit (WROOM-32) + Bosch BME680 (I2C) + Sensirion SCD41 (I2C)
 *               + Plantower PMS7003 (UART2)
 * Architecture: Static Allocation, Nanopb Protobuf, LittleFS Store-and-Forward,
 *               Hardware Task Watchdog Timer (TWDT), non-blocking main loop.
 * Wire format:  contracts/proto/air_quality.proto, one AirQualityPacket per
 *               SAMPLING_INTERVAL_MS on sensors/<DEVICE_ID>/airquality.
 * ==============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>
#include <FS.h>
#include <LittleFS.h>
#include <time.h>
#include <esp_task_wdt.h>

#include "config.h"
#include "secrets.h"
#include "pms7003.h"
#include "scd4x.h"
#include "telemetry.h"

#ifndef MOCK_SENSOR_DATA
#define MOCK_SENSOR_DATA 0
#endif

#if !MOCK_SENSOR_DATA
#include <bsec.h>
// BSEC tuning blob: 3.3 V supply, 3 s sampling (LP mode), 4-day calibration window
static const uint8_t s_bsec_config[] = {
#include "config/generic_33v_3s_4d/bsec_iaq.txt"
};
#endif

#define MQTT_TOPIC_TELEMETRY "sensors/" DEVICE_ID "/airquality"

// =============================================================================
// Static Memory Structures (Zero Heap Churn Guarantee)
// =============================================================================
static uint8_t s_tx_buffer[MAX_STATIC_TX_BUFFER];
static iot_airquality_AirQualityPacket s_packet = iot_airquality_AirQualityPacket_init_zero;
static uint32_t s_sequence_counter = 0;
static uint32_t s_last_sample_tick = 0;
static uint32_t s_last_mqtt_attempt = 0;
static bool     s_ntp_started = false;

static WiFiClient s_wifi_client;
static PubSubClient s_mqtt_client(s_wifi_client);

// =============================================================================
// Flash Storage (LittleFS) Store-and-Forward Buffer
// =============================================================================
static bool s_fs_mounted = false;
static size_t s_offline_queue_bytes = 0;

static void init_filesystem() {
    if (!LittleFS.begin(true)) {
        Serial.println(F("[FS] ERROR: LittleFS mount failed!"));
        s_fs_mounted = false;
        return;
    }
    s_fs_mounted = true;
    if (LittleFS.exists(QUEUE_FILE_PATH)) {
        File f = LittleFS.open(QUEUE_FILE_PATH, "r");
        if (f) {
            s_offline_queue_bytes = f.size();
            f.close();
        }
    }
    Serial.printf("[FS] LittleFS mounted (Initial queue size: %u bytes).\n", (unsigned int)s_offline_queue_bytes);
}

static void persist_packet_to_flash(const uint8_t *buffer, size_t length) {
    if (!s_fs_mounted || length == 0) return;

    size_t frame_total_bytes = sizeof(uint16_t) + length;
    if (s_offline_queue_bytes + frame_total_bytes > QUEUE_MAX_FILE_BYTES) {
        Serial.println(F("[FS] Warning: Queue limit reached, rotating buffer."));
        LittleFS.remove(QUEUE_FILE_PATH);
        s_offline_queue_bytes = 0;
    }

    File file = LittleFS.open(QUEUE_FILE_PATH, "a");
    if (!file) {
        Serial.println(F("[FS] ERROR: Failed to open queue file for appending."));
        return;
    }

    // 2-byte length prefix followed by the raw protobuf payload
    uint16_t frame_len = (uint16_t)length;
    file.write((const uint8_t *)&frame_len, sizeof(frame_len));
    file.write(buffer, length);
    file.close();

    s_offline_queue_bytes += frame_total_bytes;
}

static void drain_offline_queue() {
    if (!s_fs_mounted || s_offline_queue_bytes == 0 || !s_mqtt_client.connected()) return;

    File file = LittleFS.open(QUEUE_FILE_PATH, "r");
    if (!file) return;

    Serial.println(F("[FS] Draining store-and-forward offline buffer..."));
    uint32_t drained_count = 0;
    bool complete = true;

    while (file.available() >= (int)sizeof(uint16_t)) {
        uint16_t frame_len = 0;
        if (file.read((uint8_t *)&frame_len, sizeof(frame_len)) != sizeof(frame_len)) break;
        if (frame_len == 0 || frame_len > MAX_STATIC_TX_BUFFER) {
            Serial.println(F("[FS] Corrupted frame detected, dropping the rest of the queue."));
            break;
        }
        if (file.read(s_tx_buffer, frame_len) != frame_len) break;

        if (!s_mqtt_client.connected() ||
            !s_mqtt_client.publish(MQTT_TOPIC_TELEMETRY, s_tx_buffer, frame_len, false)) {
            Serial.println(F("[FS] MQTT publish stalled during replay, will retry."));
            complete = false;
            break;
        }
        drained_count++;
        s_mqtt_client.loop();
        esp_task_wdt_reset();
        delay(10);  // Throttle burst transmission
    }
    file.close();

    // Only discard the queue once everything went out. A partial replay is
    // retried in full: timestamped points are idempotent on the InfluxDB side.
    if (complete) {
        LittleFS.remove(QUEUE_FILE_PATH);
        s_offline_queue_bytes = 0;
    }
    Serial.printf("[FS] Replay %s: %u packets sent.\n", complete ? "finished" : "interrupted",
                  (unsigned int)drained_count);
}

// =============================================================================
// Sensors
// =============================================================================
#if MOCK_SENSOR_DATA

static void sensors_begin() {
    Serial.println(F("[Sensors] MOCK_SENSOR_DATA=1: synthetic telemetry, no hardware access."));
}

static void sensors_poll() {}

static void sensors_snapshot(SensorSnapshot *out) {
    static uint32_t step = 0;
    step++;
    float angle = (float)(step % 360) * 0.0174533f;
    float noise = (float)(random(-10, 10)) * 0.05f;

    out->bme_ok = out->pms_ok = out->scd_ok = true;
    out->scd_enabled = true;
    out->bme_calibrating = out->pms_warming = false;
    out->temperature_c = 21.0f + 2.5f * sinf(angle) + (noise * 0.2f);
    out->humidity_pct  = 48.0f + 5.0f * cosf(angle * 0.5f) + noise;
    out->pressure_hpa  = 1013.25f + 3.0f * sinf(angle * 0.1f);
    out->co2_ppm       = 450.0f + 120.0f * fabsf(sinf(angle * 0.3f)) + (float)random(0, 30);
    out->eco2_ppm      = out->co2_ppm;
    out->bvoc_ppm      = 0.60f + 0.15f * sinf(angle * 1.2f);
    out->static_iaq    = 40.0f;
    out->pm25_ug_m3    = 6.0f + 4.0f * fabsf(sinf(angle * 0.8f)) + (float)random(0, 4);
    out->pm10_ug_m3    = out->pm25_ug_m3 * 1.6f;
}

#else  // ---- Real hardware ---------------------------------------------------

static Bsec           s_bsec;
static bool           s_bme_present = false;
static uint32_t       s_bme_last_ok_ms = 0;
static uint32_t       s_bsec_last_save_ms = 0;
static bool           s_bsec_saved_once = false;
static uint8_t        s_bsec_state[BSEC_MAX_STATE_BLOB_SIZE];

static inline float bsec_pressure_hpa() { return s_bsec.pressure / 100.0f; }  // BSEC reports Pa

static Pms7003Parser  s_pms_parser;
static PmsAccumulator s_pms_acc;
static uint32_t       s_pms_last_frame_ms = 0;
static bool           s_pms_seen = false;
static uint32_t       s_pms_wake_ms = 0;
static float          s_pm25 = 0.0f, s_pm10 = 0.0f;

// ---- SCD41 (shares the I2C bus with the BME680; both are driven from loop()) ----
#if HAS_SCD41
static bool     s_scd_started = false;
static uint32_t s_scd_last_poll_ms = 0;
static uint32_t s_scd_last_ok_ms = 0;
static uint32_t s_scd_last_pressure_ms = 0;
static uint32_t s_scd_crc_errors = 0;
static uint16_t s_scd_co2 = 0;

static bool scd_write(const uint8_t *data, size_t len) {
    Wire.beginTransmission((uint8_t)SCD4X_I2C_ADDR);
    for (size_t i = 0; i < len; i++) Wire.write(data[i]);
    return Wire.endTransmission() == 0;
}

static bool scd_command(uint16_t cmd) {
    const uint8_t tx[2] = {(uint8_t)(cmd >> 8), (uint8_t)cmd};
    return scd_write(tx, sizeof(tx));
}

static bool scd_read(uint16_t cmd, uint8_t *rx, uint8_t len) {
    if (!scd_command(cmd)) return false;
    delay(1);  // Command execution time
    if (Wire.requestFrom((uint8_t)SCD4X_I2C_ADDR, len) != len) return false;
    for (uint8_t i = 0; i < len; i++) rx[i] = (uint8_t)Wire.read();
    return true;
}

static void scd_begin() {
    // After an ESP32-only reset the sensor is still measuring and ignores most
    // commands: stop first (harmless when idle), then (re)start.
    scd_command(SCD4X_CMD_STOP_PERIODIC);
    delay(500);
    s_scd_started = scd_command(SCD4X_CMD_START_PERIODIC);
    Serial.println(s_scd_started ? F("[SCD41] Periodic measurement started (first sample in 5 s).")
                                 : F("[SCD41] ERROR: no answer at I2C address 0x62. Check wiring."));
}

static void scd_poll(uint32_t now) {
    if ((now - s_scd_last_poll_ms) < SCD_POLL_MS) return;
    s_scd_last_poll_ms = now;
    if (!s_scd_started) {  // Hot-plug / late power-up: retry the start command
        s_scd_started = scd_command(SCD4X_CMD_START_PERIODIC);
        return;
    }

    uint8_t rx[9];
    uint16_t ready = 0;
    if (!scd_read(SCD4X_CMD_GET_DATA_READY, rx, 3) || !scd4x_word(rx, 0, &ready)) return;
    if (!scd4x_is_data_ready(ready)) return;

    uint16_t co2 = 0;
    float t = 0.0f, rh = 0.0f;  // SCD41 T/RH unused: the BME680 is the reference
    if (!scd_read(SCD4X_CMD_READ_MEASUREMENT, rx, 9)) return;
    if (!scd4x_parse_measurement(rx, &co2, &t, &rh)) {
        s_scd_crc_errors++;
        return;
    }
    if (co2 == 0) return;  // Sensor signals an invalid sample with 0 ppm
    s_scd_co2 = co2;
    s_scd_last_ok_ms = now;
}

/** Ambient pressure compensation from the BME680 (CO2 reading drifts ~1 % per 10 hPa). */
static void scd_sync_pressure(uint32_t now, float pressure_hpa) {
    if (!s_scd_started || pressure_hpa < 300.0f || pressure_hpa > 1200.0f) return;
    if (s_scd_last_pressure_ms != 0 && (now - s_scd_last_pressure_ms) < SCD_PRESSURE_SYNC_MS) return;
    uint8_t tx[5];
    scd4x_frame_with_arg(SCD4X_CMD_SET_AMBIENT_PRESSURE, (uint16_t)(pressure_hpa + 0.5f), tx);
    if (scd_write(tx, sizeof(tx))) s_scd_last_pressure_ms = now ? now : 1;
}
#endif  // HAS_SCD41

static void bsec_load_state() {
    if (!s_fs_mounted || !LittleFS.exists(BSEC_STATE_PATH)) return;
    File f = LittleFS.open(BSEC_STATE_PATH, "r");
    if (!f) return;
    if (f.size() == sizeof(s_bsec_state) && f.read(s_bsec_state, sizeof(s_bsec_state)) == sizeof(s_bsec_state)) {
        s_bsec.setState(s_bsec_state);
        Serial.println(F("[BME680] BSEC calibration state restored from flash."));
    }
    f.close();
}

static void bsec_save_state() {
    if (!s_fs_mounted) return;
    s_bsec.getState(s_bsec_state);
    if (s_bsec.bsecStatus != BSEC_OK) return;
    File f = LittleFS.open(BSEC_STATE_PATH, "w");
    if (!f) return;
    f.write(s_bsec_state, sizeof(s_bsec_state));
    f.close();
    Serial.println(F("[BME680] BSEC calibration state saved."));
}

static void sensors_begin() {
    // --- PMS7003: wake it up first, the 30 s stabilisation starts now ---
    pinMode(PIN_PMS_SET, OUTPUT);
    digitalWrite(PIN_PMS_SET, HIGH);
    Serial2.setRxBufferSize(512);  // ~16 frames of slack if the loop stalls on the network
    Serial2.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);
    s_pms_wake_ms = millis();

    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
#if HAS_SCD41
    scd_begin();
#endif

    // --- BME680 through BSEC (Low Power mode: one measurement every 3 s) ---
    s_bsec.begin(BME680_I2C_ADDR, Wire);
    s_bme_present = (s_bsec.bsecStatus >= BSEC_OK) && (s_bsec.bme68xStatus >= 0);
    if (!s_bme_present) {
        Serial.printf("[BME680] ERROR: init failed (bsec=%d, bme68x=%d). Check wiring / I2C address 0x%02X.\n",
                      (int)s_bsec.bsecStatus, (int)s_bsec.bme68xStatus, BME680_I2C_ADDR);
        return;
    }
    s_bsec.setConfig(s_bsec_config);
    s_bsec.setTemperatureOffset(BME_TEMP_OFFSET_C);
    bsec_load_state();

    bsec_virtual_sensor_t outputs[] = {
        BSEC_OUTPUT_IAQ,
        BSEC_OUTPUT_STATIC_IAQ,
        BSEC_OUTPUT_CO2_EQUIVALENT,
        BSEC_OUTPUT_BREATH_VOC_EQUIVALENT,
        BSEC_OUTPUT_RAW_PRESSURE,
        BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_TEMPERATURE,
        BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_HUMIDITY,
    };
    s_bsec.updateSubscription(outputs, sizeof(outputs) / sizeof(outputs[0]), BSEC_SAMPLE_RATE_LP);
    if (s_bsec.bsecStatus < BSEC_OK) {
        Serial.printf("[BME680] ERROR: BSEC subscription failed (%d).\n", (int)s_bsec.bsecStatus);
        s_bme_present = false;
        return;
    }
    Serial.printf("[BME680] BSEC %d.%d.%d.%d ready.\n", s_bsec.version.major, s_bsec.version.minor,
                  s_bsec.version.major_bugfix, s_bsec.version.minor_bugfix);
}

/** Must be called on every loop iteration: BSEC schedules its own measurements. */
static void sensors_poll() {
    uint32_t now = millis();

    while (Serial2.available() > 0) {
        if (s_pms_parser.feed((uint8_t)Serial2.read())) {
            s_pms_acc.add(s_pms_parser.frame());
            s_pms_last_frame_ms = now;
            s_pms_seen = true;
        }
    }

#if HAS_SCD41
    scd_poll(now);
#endif

    if (s_bme_present && s_bsec.run()) {
        s_bme_last_ok_ms = now;
#if HAS_SCD41
        scd_sync_pressure(now, bsec_pressure_hpa());
#endif
        // Persist the calibration the first time BSEC reaches full accuracy, then every 6 h
        bool first_full_accuracy = !s_bsec_saved_once && s_bsec.iaqAccuracy >= 3;
        if (first_full_accuracy || (now - s_bsec_last_save_ms) >= BSEC_STATE_SAVE_MS) {
            bsec_save_state();
            s_bsec_last_save_ms = now;
            s_bsec_saved_once = s_bsec_saved_once || first_full_accuracy;
        }
    }
}

static void sensors_snapshot(SensorSnapshot *out) {
    uint32_t now = millis();
    memset(out, 0, sizeof(*out));

    out->bme_ok = s_bme_present && s_bme_last_ok_ms != 0 && (now - s_bme_last_ok_ms) < BME_STALE_MS;
    if (out->bme_ok) {
        out->temperature_c   = s_bsec.temperature;
        out->humidity_pct    = s_bsec.humidity;
        out->pressure_hpa    = bsec_pressure_hpa();
        out->eco2_ppm        = s_bsec.co2Equivalent;
        out->bvoc_ppm        = s_bsec.breathVocEquivalent;
        out->static_iaq      = s_bsec.staticIaq;
        out->bme_calibrating = (s_bsec.iaqAccuracy == 0);
    }

#if HAS_SCD41
    out->scd_enabled = true;
    out->scd_ok = s_scd_last_ok_ms != 0 && (now - s_scd_last_ok_ms) < SCD_STALE_MS;
    out->co2_ppm = out->scd_ok ? (float)s_scd_co2 : 0.0f;
#endif

    // Average of the PMS frames received since the previous transmission
    // (the sensor emits one every 0.2 - 2.3 s); keep the last value otherwise.
    s_pms_acc.take(&s_pm25, &s_pm10);
    out->pms_ok = s_pms_seen && (now - s_pms_last_frame_ms) < PMS_STALE_MS;
    out->pms_warming = (now - s_pms_wake_ms) < PMS_WARMUP_MS;
    if (out->pms_ok) {
        out->pm25_ug_m3 = s_pm25;
        out->pm10_ug_m3 = s_pm10;
    }
}

#endif  // MOCK_SENSOR_DATA

// =============================================================================
// Network (never blocks longer than MQTT_SOCKET_TIMEOUT_S)
// =============================================================================
static void service_network() {
    if (WiFi.status() != WL_CONNECTED) return;  // Auto-reconnect runs in the Wi-Fi task

    if (!s_ntp_started) {
        configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);  // UTC, SNTP resyncs hourly in background
        s_ntp_started = true;
    }

    if (s_mqtt_client.connected()) {
        s_mqtt_client.loop();
        return;
    }

    uint32_t now = millis();
    if (s_last_mqtt_attempt != 0 && (now - s_last_mqtt_attempt) < MQTT_RETRY_MS) return;
    s_last_mqtt_attempt = now ? now : 1;

#ifdef MQTT_USER
    bool ok = s_mqtt_client.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD);
#else
    bool ok = s_mqtt_client.connect(DEVICE_ID);
#endif
    if (ok) {
        Serial.println(F("[MQTT] Connected to broker."));
        drain_offline_queue();
    } else {
        Serial.printf("[MQTT] Connection failed (state=%d), retrying in %lu ms.\n",
                      s_mqtt_client.state(), (unsigned long)MQTT_RETRY_MS);
    }
}

/** Unix time in seconds, or 0 until NTP has synced (the ingestor then stamps the packet itself). */
static uint64_t unix_time_or_zero() {
    time_t now = time(nullptr);
    return (now >= 1700000000) ? (uint64_t)now : 0;
}

static void sample_and_transmit() {
    SensorSnapshot snap;
    sensors_snapshot(&snap);

    s_sequence_counter++;
    int32_t rssi = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : 0;
    uint64_t unix_ts = unix_time_or_zero();
    size_t len = encode_packet(&s_packet, DEVICE_ID, s_sequence_counter, unix_ts, rssi,
                                 0 /* USB powered: no battery */, snap, s_tx_buffer, sizeof(s_tx_buffer));
    if (len == 0) {
        Serial.println(F("[Proto] ERROR: Protobuf serialization failed!"));
        return;
    }

    bool sent = s_mqtt_client.connected() &&
                s_mqtt_client.publish(MQTT_TOPIC_TELEMETRY, s_tx_buffer, len, false);
    // Without a synchronised clock a stored packet cannot be dated on replay
    // (the ingestor would stamp the whole backlog with the replay time): drop it.
    if (!sent && unix_ts != 0) persist_packet_to_flash(s_tx_buffer, len);

    Serial.printf("[TX] #%u %s %uB st=%d | T=%.2fC RH=%.1f%% P=%.1fhPa CO2=%.0fppm bVOC=%.3fppm sIAQ=%.0f PM2.5=%.1f PM10=%.1f\n",
                  (unsigned int)s_sequence_counter, sent ? "mqtt" : (unix_ts != 0 ? "flash" : "lost"), (unsigned int)len,
                  (int)s_packet.status, snap.temperature_c, snap.humidity_pct, snap.pressure_hpa,
                  snap.scd_enabled ? snap.co2_ppm : snap.eco2_ppm, snap.bvoc_ppm, snap.static_iaq, snap.pm25_ug_m3, snap.pm10_ug_m3);
}

// =============================================================================
// Arduino Core Setup & Loop
// =============================================================================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n=================================================="));
    Serial.println(F("   Air Quality Firmware v1.2 - BME680+SCD41+PMS7003"));
    Serial.println(F("=================================================="));

    // 1. Task Watchdog Timer (ESP-IDF 4.4 API, the version pinned in platformio.ini)
    esp_task_wdt_init(TWDT_TIMEOUT_SECONDS, true);
    esp_task_wdt_add(NULL);

    // 2. Flash storage, then sensors (BSEC state is read from LittleFS)
    init_filesystem();
    sensors_begin();

    // 3. Wi-Fi & MQTT: association happens in the background
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    s_mqtt_client.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
    s_mqtt_client.setBufferSize(MAX_STATIC_TX_BUFFER);
    s_mqtt_client.setSocketTimeout(MQTT_SOCKET_TIMEOUT_S);
    s_mqtt_client.setKeepAlive(30);

    s_last_sample_tick = millis();
}

void loop() {
    esp_task_wdt_reset();

    sensors_poll();
    service_network();

    uint32_t now = millis();
    if (now - s_last_sample_tick >= SAMPLING_INTERVAL_MS) {
        s_last_sample_tick = now;
        sample_and_transmit();
    }

    delay(10);
}
