/**
 * Sensor snapshot -> AirQualityPacket encoding.
 * Pure C++ on top of Nanopb: no Arduino dependency, so it is unit-tested on
 * the host (see test_host/).
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include <pb_encode.h>
#include "air_quality.pb.h"

struct SensorSnapshot {
    bool  bme_ok;           // BME680 answering and BSEC producing fresh outputs
    bool  bme_calibrating;  // BSEC IAQ accuracy still 0 (first ~5 min after power-up)
    bool  pms_ok;           // Valid PMS7003 frame received recently
    bool  pms_warming;      // Within the 30 s fan/laser stabilisation window
    float temperature_c;
    float humidity_pct;
    float pressure_hpa;
    float eco2_ppm;         // BSEC CO2 *equivalent* (derived from VOC, not a CO2 measurement)
    bool  scd_enabled;      // An SCD41 is expected (HAS_SCD41)
    bool  scd_ok;           // Fresh SCD41 measurement available
    float co2_ppm;          // SCD41 photoacoustic CO2 measurement
    float bvoc_ppm;         // BSEC breath-VOC equivalent
    float static_iaq;       // BSEC static IAQ index, 0-500
    uint8_t iaq_accuracy;   // BSEC calibration state of that index, 0-3
    float pm25_ug_m3;
    float pm10_ug_m3;
};

inline iot_airquality_SensorStatus compute_status(const SensorSnapshot &s) {
    bool scd_missing = s.scd_enabled && !s.scd_ok;
    if (!s.bme_ok && !s.pms_ok && (scd_missing || !s.scd_enabled)) return iot_airquality_SensorStatus_STATUS_FAULT;
    if (!s.bme_ok || !s.pms_ok || scd_missing) return iot_airquality_SensorStatus_STATUS_DEGRADED;
    if (s.bme_calibrating || s.pms_warming) return iot_airquality_SensorStatus_STATUS_WARMING_UP;
    return iot_airquality_SensorStatus_STATUS_OK;
}

/**
 * Encodes one packet into `buf`. Returns the encoded size, 0 on failure.
 * A missing sensor reports 0 for its fields (the ingestor formats floats
 * straight into Influx line protocol, so NaN is not an option); consumers
 * must rely on `status` to discard them.
 */
inline size_t encode_packet(iot_airquality_AirQualityPacket *pkt,
                              const char *device_id, uint32_t sequence,
                              uint64_t unix_ts_sec, int32_t rssi_dbm,
                              uint32_t battery_mv, const SensorSnapshot &s,
                              uint8_t *buf, size_t buf_len) {
    memset(pkt, 0, sizeof(*pkt));
    strncpy(pkt->device_id, device_id, sizeof(pkt->device_id) - 1);
    pkt->sequence_number    = sequence;
    pkt->base_timestamp_sec = unix_ts_sec;
    pkt->battery_millivolts = battery_mv;
    pkt->rssi_dbm           = rssi_dbm;
    pkt->status             = compute_status(s);
    pkt->has_full_sample    = true;

    iot_airquality_FullSample *fs = &pkt->full_sample;
    if (s.bme_ok) {
        fs->temperature_celsius = s.temperature_c;
        fs->humidity_percent    = s.humidity_pct;
        fs->pressure_hpa        = s.pressure_hpa;
        fs->tvoc_ppb            = s.bvoc_ppm * 1000.0f;
        fs->static_iaq          = s.static_iaq;
        fs->iaq_accuracy        = s.iaq_accuracy;
    }
    // CO2: real measurement when an SCD41 is fitted, BSEC estimate otherwise.
    // The two are never mixed: a silent SCD41 reports 0 + STATUS_DEGRADED.
    if (s.scd_enabled) {
        if (s.scd_ok) fs->co2_ppm = s.co2_ppm > 0.0f ? (uint32_t)(s.co2_ppm + 0.5f) : 0;
    } else if (s.bme_ok) {
        fs->co2_ppm = s.eco2_ppm > 0.0f ? (uint32_t)(s.eco2_ppm + 0.5f) : 0;
    }
    if (s.pms_ok) {
        fs->pm25_ug_m3 = s.pm25_ug_m3;
        fs->pm10_ug_m3 = s.pm10_ug_m3;
    }

    pb_ostream_t stream = pb_ostream_from_buffer(buf, buf_len);
    if (!pb_encode(&stream, iot_airquality_AirQualityPacket_fields, pkt)) return 0;
    return stream.bytes_written;
}
