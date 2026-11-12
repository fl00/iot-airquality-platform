/**
 * Hardware & runtime configuration - ESP32-DevKitC (38 pins) + BME680 + SCD41 + PMS7003.
 * Pin assignment = carrier PCB (hardware/pcb): everything sits on the 3V3..5V header row.
 * Network credentials live in secrets.h (git-ignored, see secrets.example.h).
 */
#pragma once

// ---- BME680 (I2C, 3V3) ------------------------------------------------------
#define PIN_I2C_SDA          33
#define PIN_I2C_SCL          32
#define BME680_I2C_ADDR      0x77     // 0x76 when the breakout ties SDO to GND
// Subtracted by BSEC from the measured temperature to cancel enclosure
// self-heating. Calibrate against a reference thermometer after 1 h of uptime.
#define BME_TEMP_OFFSET_C    0.0f

// ---- SCD41 (I2C, 3V3, same bus as the BME680, fixed address 0x62) ------------
// 1: co2_ppm is the real CO2 measured by the SCD41.
// 0: no SCD41 fitted, co2_ppm falls back to the BSEC CO2-equivalent estimate.
#define HAS_SCD41            1
#define SCD_POLL_MS          1000UL   // Data-ready polling period (new sample every 5 s)
#define SCD_STALE_MS         20000UL
#define SCD_PRESSURE_SYNC_MS 300000UL // Feed the BME680 pressure to the SCD41 every 5 min

// ---- PMS7003 (UART2, 5 V supply, 3V3 logic) ---------------------------------
#define PIN_PMS_RX           25       // ESP32 RX2  <- PMS7003 TX  (pin 9)   J4 "TX"
#define PIN_PMS_TX           26       // ESP32 TX2  -> PMS7003 RX  (pin 7)   J4 "RX"
#define PIN_PMS_SET          27       // -> PMS7003 SET (pin 10)             J4 "SET": HIGH = run, LOW = sleep
#define PMS_WARMUP_MS        30000UL  // Fan/laser stabilisation after wake-up (datasheet: 30 s)
#define PMS_STALE_MS         10000UL  // No valid frame for 10 s => sensor considered lost

// ---- Telemetry --------------------------------------------------------------
#define SAMPLING_INTERVAL_MS 5000UL   // Platform cadence: one packet every 5 s
#define BME_STALE_MS         15000UL  // BSEC LP mode outputs every 3 s
#define BSEC_STATE_SAVE_MS   (6UL * 60UL * 60UL * 1000UL)  // Persist BSEC calibration every 6 h
#define NTP_SERVER_1         "pool.ntp.org"
#define NTP_SERVER_2         "time.google.com"

// ---- Reliability ------------------------------------------------------------
#define TWDT_TIMEOUT_SECONDS 15
#define MQTT_RETRY_MS        5000UL
#define MQTT_SOCKET_TIMEOUT_S 4       // Must stay well below the watchdog timeout
#define MAX_STATIC_TX_BUFFER 256
#define QUEUE_MAX_FILE_BYTES (512 * 1024)
#define QUEUE_FILE_PATH      "/offline_queue.bin"
#define BSEC_STATE_PATH      "/bsec_state.bin"
