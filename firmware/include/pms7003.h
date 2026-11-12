/**
 * Plantower PMS7003 active-mode frame parser.
 * Header-only, no dynamic allocation, no Arduino dependency (host-testable).
 *
 * Frame (32 bytes, big-endian words):
 *   0x42 0x4D | len=28 | 13 data words | checksum = sum of the first 30 bytes
 *   data[0..2]  PM1.0 / PM2.5 / PM10 in ug/m3, CF=1 (factory calibration)
 *   data[3..5]  PM1.0 / PM2.5 / PM10 in ug/m3, atmospheric environment
 *   data[6..11] particle counts per 0.1 L (>0.3, >0.5, >1.0, >2.5, >5.0, >10 um)
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

struct Pms7003Frame {
    uint16_t pm1_0_cf1, pm2_5_cf1, pm10_cf1;
    uint16_t pm1_0_atm, pm2_5_atm, pm10_atm;
    uint16_t counts[6];
};

class Pms7003Parser {
public:
    static const size_t FRAME_LEN = 32;

    /** Feed one UART byte. Returns true when a checksum-valid frame completes. */
    bool feed(uint8_t b) {
        if (idx_ == 0) {
            if (b == 0x42) buf_[idx_++] = b;
            return false;
        }
        if (idx_ == 1) {
            if (b == 0x4D) buf_[idx_++] = b;
            else idx_ = (b == 0x42) ? 1 : 0;
            return false;
        }
        buf_[idx_++] = b;
        if (idx_ == 4 && word(2) != FRAME_LEN - 4) {  // Wrong length: resync
            idx_ = 0;
            return false;
        }
        if (idx_ < FRAME_LEN) return false;

        idx_ = 0;
        uint16_t sum = 0;
        for (size_t i = 0; i < FRAME_LEN - 2; i++) sum += buf_[i];
        if (sum != word(FRAME_LEN - 2)) {
            checksum_errors_++;
            return false;
        }
        frame_.pm1_0_cf1 = word(4);
        frame_.pm2_5_cf1 = word(6);
        frame_.pm10_cf1  = word(8);
        frame_.pm1_0_atm = word(10);
        frame_.pm2_5_atm = word(12);
        frame_.pm10_atm  = word(14);
        for (size_t i = 0; i < 6; i++) frame_.counts[i] = word(16 + 2 * i);
        return true;
    }

    const Pms7003Frame &frame() const { return frame_; }
    uint32_t checksum_errors() const { return checksum_errors_; }

private:
    uint16_t word(size_t off) const { return (uint16_t)((buf_[off] << 8) | buf_[off + 1]); }

    uint8_t buf_[FRAME_LEN] = {0};
    size_t idx_ = 0;
    uint32_t checksum_errors_ = 0;
    Pms7003Frame frame_ = {};
};

/** Running mean of the frames received between two telemetry transmissions. */
struct PmsAccumulator {
    uint32_t sum_pm25 = 0, sum_pm10 = 0, n = 0;

    void add(const Pms7003Frame &f) { sum_pm25 += f.pm2_5_atm; sum_pm10 += f.pm10_atm; n++; }
    bool take(float *pm25, float *pm10) {
        if (n == 0) return false;
        *pm25 = (float)sum_pm25 / (float)n;
        *pm10 = (float)sum_pm10 / (float)n;
        sum_pm25 = sum_pm10 = n = 0;
        return true;
    }
};
