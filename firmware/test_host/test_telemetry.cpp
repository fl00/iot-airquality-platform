// Host-side regression test: PMS7003 parser + Nanopb keyframe encoding.
// Build & run: see run.sh (needs a nanopb checkout and python3 protobuf).
#include <stdio.h>
#include <assert.h>
#include "pms7003.h"
#include "scd4x.h"
#include "telemetry.h"

static void make_frame(uint8_t *f, uint16_t pm25_atm, uint16_t pm10_atm) {
    memset(f, 0, 32);
    f[0] = 0x42; f[1] = 0x4D; f[3] = 28;
    f[12] = pm25_atm >> 8; f[13] = pm25_atm & 0xFF;
    f[14] = pm10_atm >> 8; f[15] = pm10_atm & 0xFF;
    uint16_t sum = 0;
    for (int i = 0; i < 30; i++) sum += f[i];
    f[30] = sum >> 8; f[31] = sum & 0xFF;
}

int main() {
    Pms7003Parser parser;
    PmsAccumulator acc;
    uint8_t frame[32];
    int valid = 0;

    // Garbage + a stray 0x42, then two good frames, then a corrupted one
    const uint8_t junk[] = {0x00, 0x42, 0x13, 0x42};
    for (uint8_t b : junk) assert(!parser.feed(b));
    make_frame(frame, 12, 20);
    for (uint8_t b : frame) if (parser.feed(b)) { acc.add(parser.frame()); valid++; }
    make_frame(frame, 300, 1000);
    for (uint8_t b : frame) if (parser.feed(b)) { acc.add(parser.frame()); valid++; }
    make_frame(frame, 7, 9); frame[13] ^= 0xFF;
    for (uint8_t b : frame) assert(!parser.feed(b));
    assert(valid == 2 && parser.checksum_errors() == 1);

    float pm25 = 0, pm10 = 0;
    assert(acc.take(&pm25, &pm10) && pm25 == 156.0f && pm10 == 510.0f);
    assert(!acc.take(&pm25, &pm10));

    // SCD41: datasheet CRC vector, then the datasheet read_measurement example
    // (500 ppm, 25.0 C, 37.0 %RH) and a corrupted copy of it.
    const uint8_t beef[2] = {0xBE, 0xEF};
    assert(scd4x_crc8(beef, 2) == 0x92);
    uint8_t meas[9] = {0x01, 0xF4, 0x33, 0x66, 0x67, 0xA2, 0x5E, 0xB9, 0x3C};
    uint16_t co2 = 0; float t = 0, rh = 0;
    assert(scd4x_parse_measurement(meas, &co2, &t, &rh));
    assert(co2 == 500 && t > 24.99f && t < 25.01f && rh > 36.99f && rh < 37.01f);
    meas[1] ^= 0x01;
    assert(!scd4x_parse_measurement(meas, &co2, &t, &rh));
    uint8_t tx[5];
    scd4x_frame_with_arg(SCD4X_CMD_SET_AMBIENT_PRESSURE, 1013, tx);
    assert(tx[0] == 0xE0 && tx[1] == 0x00 && tx[2] == 0x03 && tx[3] == 0xF5 && tx[4] == scd4x_crc8(&tx[2], 2));
    assert(!scd4x_is_data_ready(0x8000) && scd4x_is_data_ready(0x8006));

    SensorSnapshot s = {};
    assert(compute_status(s) == iot_airquality_SensorStatus_STATUS_FAULT);
    s.pms_ok = true;
    assert(compute_status(s) == iot_airquality_SensorStatus_STATUS_DEGRADED);
    s.bme_ok = true; s.bme_calibrating = true;
    assert(compute_status(s) == iot_airquality_SensorStatus_STATUS_WARMING_UP);
    s.temperature_c = 21.37f; s.humidity_pct = 46.5f; s.pressure_hpa = 1008.2f;
    // SCD41 expected but silent: degraded, and the BSEC estimate must NOT leak into co2_ppm
    s.scd_enabled = true; s.bme_calibrating = false;
    assert(compute_status(s) == iot_airquality_SensorStatus_STATUS_DEGRADED);
    s.scd_ok = true; s.co2_ppm = 612.4f; s.bme_calibrating = true;
    s.eco2_ppm = 999.0f; s.bvoc_ppm = 0.731f; s.pm25_ug_m3 = pm25; s.pm10_ug_m3 = pm10;

    static iot_airquality_AirQualityPacket pkt;
    uint8_t buf[256];
    size_t n = encode_keyframe(&pkt, "sensor-esp32-01", 42, 1794000000ULL, -61, 0, s, buf, sizeof(buf));
    assert(n > 0 && n <= 128);
    for (size_t i = 0; i < n; i++) printf("%02x", buf[i]);
    printf("\n");
    return 0;
}
