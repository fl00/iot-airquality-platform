/**
 * ==============================================================================
 * Zero-Bloat Bare-Metal IoT Air Quality Platform - ESP32 Firmware
 * Architecture: Static Allocation, Nanopb Protobuf, LittleFS Store-and-Forward,
 *               Hardware Task Watchdog Timer (TWDT), High-Cadence Telemetry.
 * ==============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <FS.h>
#include <LittleFS.h>
#include <esp_task_wdt.h>
#include <pb_encode.h>
#include <pb_decode.h>
#include "air_quality.pb.h"

// Configuration constants
#define WIFI_SSID           "BareMetal-IoT-AP"
#define WIFI_PASSWORD       "StrictZeroBloat2026"
#define MQTT_BROKER_HOST    "192.168.1.100"
#define MQTT_BROKER_PORT    1883
#define DEVICE_ID           "sensor-esp32-01"
#define MQTT_TOPIC_TELEMETRY "sensors/" DEVICE_ID "/airquality"

// Timing and limits
#define SAMPLING_INTERVAL_MS 5000       // 5 seconds cadence
#define TWDT_TIMEOUT_SECONDS 15         // 15 seconds Task Watchdog limit
#define MAX_STATIC_TX_BUFFER 256        // Fixed static Nanopb frame buffer
#define QUEUE_MAX_FILE_BYTES (512 * 1024) // 512KB Store-and-Forward buffer cap
#define QUEUE_FILE_PATH     "/offline_queue.bin"

// Preprocessor Flag for Mock Sensor Generation
#ifndef MOCK_SENSOR_DATA
#define MOCK_SENSOR_DATA 1
#endif

// =============================================================================
// Static Memory Structures (Zero Heap Churn Guarantee)
// =============================================================================
static uint8_t s_tx_buffer[MAX_STATIC_TX_BUFFER];
static iot_airquality_AirQualityPacket s_packet = iot_airquality_AirQualityPacket_init_zero;
static uint32_t s_sequence_counter = 0;
static uint32_t s_last_sample_tick = 0;
static float s_last_co2 = 420.0f;
static float s_last_temp = 21.5f;
static float s_last_hum = 45.0f;
static float s_last_pm25 = 5.0f;

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

    // Fast-path size check: In-memory tracker avoids redundant LittleFS.open("r")
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

    // Write 2-byte length prefix followed by raw protobuf payload
    uint16_t frame_len = (uint16_t)length;
    file.write((const uint8_t *)&frame_len, sizeof(frame_len));
    file.write(buffer, length);
    file.flush();
    file.close();

    s_offline_queue_bytes += frame_total_bytes;
    Serial.printf("[FS] Buffered offline packet (%u bytes, total queue: %u bytes).\n",
                  (unsigned int)length, (unsigned int)s_offline_queue_bytes);
}

static void drain_offline_queue() {
    if (!s_fs_mounted || !LittleFS.exists(QUEUE_FILE_PATH) || !s_mqtt_client.connected()) {
        return;
    }

    File file = LittleFS.open(QUEUE_FILE_PATH, "r");
    if (!file) return;

    Serial.println(F("[FS] Draining store-and-forward offline buffer..."));
    uint32_t drained_count = 0;

    while (file.available() >= (int)sizeof(uint16_t) && s_mqtt_client.connected()) {
        uint16_t frame_len = 0;
        if (file.read((uint8_t *)&frame_len, sizeof(frame_len)) != sizeof(frame_len)) {
            break;
        }

        if (frame_len == 0 || frame_len > MAX_STATIC_TX_BUFFER) {
            Serial.println(F("[FS] Corrupted frame detected, aborting replay."));
            break;
        }

        if (file.read(s_tx_buffer, frame_len) != frame_len) {
            break;
        }

        // Forward to MQTT broker
        if (s_mqtt_client.publish(MQTT_TOPIC_TELEMETRY, s_tx_buffer, frame_len, false)) {
            drained_count++;
            esp_task_wdt_reset();
            delay(10); // Throttle burst transmission
        } else {
            Serial.println(F("[FS] MQTT publish stalled during replay."));
            break;
        }
    }

    file.close();
    LittleFS.remove(QUEUE_FILE_PATH);
    s_offline_queue_bytes = 0;
    Serial.printf("[FS] Replay finished: %u packets sent.\n", drained_count);
}

// =============================================================================
// Synthetic Sensor Metric Generator (Zero Allocations)
// =============================================================================
#if MOCK_SENSOR_DATA
static void read_synthetic_sensors(iot_airquality_FullSample *out_sample) {
    static uint32_t step = 0;
    step++;

    // Realistic diurnal oscillation simulation
    float angle = (float)(step % 360) * 0.0174533f; // Degrees to radians
    float noise = (float)(random(-10, 10)) * 0.05f;

    s_last_temp = 21.0f + 2.5f * sinf(angle) + (noise * 0.2f);
    s_last_hum  = 48.0f + 5.0f * cosf(angle * 0.5f) + noise;
    s_last_co2  = 450.0f + 120.0f * fabsf(sinf(angle * 0.3f)) + ((float)random(0, 30));
    s_last_pm25 = 6.0f + 4.0f * fabsf(sinf(angle * 0.8f)) + (float)random(0, 4);

    out_sample->temperature_celsius = s_last_temp;
    out_sample->humidity_percent    = s_last_hum;
    out_sample->co2_ppm             = (uint32_t)s_last_co2;
    out_sample->pm25_ug_m3          = s_last_pm25;
    out_sample->pm10_ug_m3          = s_last_pm25 * 1.6f;
    out_sample->tvoc_ppb            = 85.0f + 25.0f * sinf(angle * 1.2f);
    out_sample->pressure_hpa        = 1013.25f + 3.0f * sinf(angle * 0.1f);
}
#endif

// =============================================================================
// Network & Telemetry Transmission Loop
// =============================================================================
static void ensure_network_connectivity() {
    // Check Wi-Fi state
    if (WiFi.status() != WL_CONNECTED) {
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        uint32_t start_try = millis();
        while (WiFi.status() != WL_CONNECTED && (millis() - start_try) < 3000) {
            delay(100);
            esp_task_wdt_reset();
        }
    }

    // Check MQTT connection
    if (WiFi.status() == WL_CONNECTED && !s_mqtt_client.connected()) {
        s_mqtt_client.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
        s_mqtt_client.setBufferSize(MAX_STATIC_TX_BUFFER);
        if (s_mqtt_client.connect(DEVICE_ID)) {
            Serial.println(F("[MQTT] Connected to broker successfully."));
            drain_offline_queue();
        }
    }
}

static void sample_and_transmit() {
    s_sequence_counter++;
    uint64_t current_time_sec = (uint64_t)(millis() / 1000ULL);

    // Initialize static packet structure
    memset(&s_packet, 0, sizeof(s_packet));
    strncpy(s_packet.device_id, DEVICE_ID, sizeof(s_packet.device_id) - 1);
    s_packet.sequence_number = s_sequence_counter;
    s_packet.base_timestamp_sec = current_time_sec;
    s_packet.battery_millivolts = 3850 - (uint32_t)((s_sequence_counter / 50) % 300); // 3.85V to 3.55V
    s_packet.rssi_dbm = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -88;
    s_packet.status = iot_airquality_SensorStatus_STATUS_OK;

    // Use FullSample keyframe
    s_packet.which_payload = iot_airquality_AirQualityPacket_full_sample_tag;
#if MOCK_SENSOR_DATA
    read_synthetic_sensors(&s_packet.payload.full_sample);
#endif

    // STRICTLY STATIC NANOPB ENCODING (Zero Heap Allocation)
    pb_ostream_t stream = pb_ostream_from_buffer(s_tx_buffer, sizeof(s_tx_buffer));
    bool encode_ok = pb_encode(&stream, iot_airquality_AirQualityPacket_msg, &s_packet);

    if (!encode_ok) {
        Serial.println(F("[Proto] ERROR: Protobuf serialization failed!"));
        return;
    }

    size_t payload_size = stream.bytes_written;

    // Transmit over MQTT or fallback to LittleFS Store-and-Forward
    if (s_mqtt_client.connected()) {
        bool pub_ok = s_mqtt_client.publish(MQTT_TOPIC_TELEMETRY, s_tx_buffer, payload_size, false);
        if (pub_ok) {
            Serial.printf("[TX] Packet #%u published (%u bytes): CO2=%uppm, Temp=%.2fC, Hum=%.1f%%\n",
                          s_sequence_counter,
                          (unsigned int)payload_size,
                          s_packet.payload.full_sample.co2_ppm,
                          s_packet.payload.full_sample.temperature_celsius,
                          s_packet.payload.full_sample.humidity_percent);
        } else {
            Serial.println(F("[TX] Publish dropped, spooling to Flash..."));
            persist_packet_to_flash(s_tx_buffer, payload_size);
        }
    } else {
        persist_packet_to_flash(s_tx_buffer, payload_size);
    }
}

// =============================================================================
// Arduino Core Setup & Loop
// =============================================================================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n=================================================="));
    Serial.println(F("   Zero-Bloat Bare-Metal IoT Firmware v1.0       "));
    Serial.println(F("=================================================="));

    // 1. Initialize Task Watchdog Timer (TWDT)
    esp_task_wdt_config_t twdt_config = {
        .timeout_ms = TWDT_TIMEOUT_SECONDS * 1000,
        .idle_core_mask = (1 << 0) | (1 << 1),
        .trigger_panic = true
    };
    esp_task_wdt_reconfigure(&twdt_config);
    esp_task_wdt_add(NULL); // Subscribe current setup/loop task
    Serial.printf("[WDT] Hardware Task Watchdog initialized (%d sec).\n", TWDT_TIMEOUT_SECONDS);

    // 2. Initialize LittleFS
    init_filesystem();

    // 3. Initialize Wi-Fi & MQTT
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    ensure_network_connectivity();

    s_last_sample_tick = millis();
}

void loop() {
    // 1. Service Watchdog
    esp_task_wdt_reset();

    // 2. Service MQTT background loop
    if (s_mqtt_client.connected()) {
        s_mqtt_client.loop();
    } else {
        ensure_network_connectivity();
    }

    // 3. Telemetry sampling timer
    uint32_t now = millis();
    if (now - s_last_sample_tick >= SAMPLING_INTERVAL_MS) {
        s_last_sample_tick = now;
        sample_and_transmit();
    }

    delay(20); // Minimal sleep for idle power saving
}
