/**
 * Sensirion SCD40/SCD41 CO2 sensor - protocol helpers (I2C address 0x62).
 * Header-only, no Arduino dependency: framing and CRC are unit-tested on the
 * host, the I2C transfers themselves live in main.cpp.
 *
 * Every 16-bit word on the bus is followed by a CRC-8 (poly 0x31, init 0xFF).
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

#define SCD4X_I2C_ADDR                  0x62
#define SCD4X_CMD_START_PERIODIC        0x21B1  // One measurement every 5 s
#define SCD4X_CMD_READ_MEASUREMENT      0xEC05  // 1 ms, returns 3 words: CO2, T, RH
#define SCD4X_CMD_STOP_PERIODIC         0x3F86  // Needs 500 ms before the next command
#define SCD4X_CMD_GET_DATA_READY        0xE4B8  // 1 ms, returns 1 word
#define SCD4X_CMD_SET_AMBIENT_PRESSURE  0xE000  // Argument in hPa, allowed while measuring

inline uint8_t scd4x_crc8(const uint8_t *data, size_t len) {
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
    return crc;
}

/** Command + one argument word -> 5 bytes to write. */
inline void scd4x_frame_with_arg(uint16_t cmd, uint16_t arg, uint8_t out[5]) {
    out[0] = (uint8_t)(cmd >> 8); out[1] = (uint8_t)cmd;
    out[2] = (uint8_t)(arg >> 8); out[3] = (uint8_t)arg;
    out[4] = scd4x_crc8(&out[2], 2);
}

/** Extracts word `index` from a response (3 bytes per word). False on CRC mismatch. */
inline bool scd4x_word(const uint8_t *rx, size_t index, uint16_t *out) {
    const uint8_t *p = rx + 3 * index;
    if (scd4x_crc8(p, 2) != p[2]) return false;
    *out = (uint16_t)((p[0] << 8) | p[1]);
    return true;
}

/** Data-ready response: a measurement is waiting when the 11 low bits are not all zero. */
inline bool scd4x_is_data_ready(uint16_t status_word) { return (status_word & 0x07FF) != 0; }

/** Decodes a 9-byte read_measurement response. False on CRC mismatch. */
inline bool scd4x_parse_measurement(const uint8_t rx[9], uint16_t *co2_ppm, float *temp_c, float *rh_pct) {
    uint16_t w_co2, w_t, w_rh;
    if (!scd4x_word(rx, 0, &w_co2) || !scd4x_word(rx, 1, &w_t) || !scd4x_word(rx, 2, &w_rh)) return false;
    *co2_ppm = w_co2;
    *temp_c  = -45.0f + 175.0f * (float)w_t / 65535.0f;
    *rh_pct  = 100.0f * (float)w_rh / 65535.0f;
    return true;
}
