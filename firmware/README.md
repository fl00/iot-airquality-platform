# ESP32 Bare-Metal Firmware (`iot-airquality-firmware-esp32`)

## 1. Architectural Highlights

- **Strict Zero-Heap Churn:** Every serialization and network buffer is statically allocated at startup (`MAX_STATIC_TX_BUFFER = 256 bytes`). Zero dynamic `malloc()` or `new` invocations in the transmission loop to guarantee zero heap fragmentation over years of continuous uptime.
- **Nanopb Compact Encoding:** Uses Nanopb static output stream (`pb_ostream_from_buffer`), packing full environmental frames into **~30 bytes** of binary payload.
- **Store-and-Forward Flash Buffer (`LittleFS`):** If Wi-Fi or MQTT disconnects, packets are appended with frame headers to `/offline_queue.bin` on SPI flash (up to 512KB). When connectivity returns, the queue is drained before resuming real-time streaming.
- **Hardware Task Watchdog Timer (TWDT):** 15-second hardware timeout resets the microcontroller if network operations hang or loop deadlocks occur.
- **Synthetic Sensor Mock Engine (`#define MOCK_SENSOR_DATA`):** Simulates realistic diurnal oscillations of CO2 (400-800 PPM), Temperature (18-25°C), Humidity (40-60% RH), PM2.5, battery decay, and Wi-Fi RSSI.

---

## 2. Memory & Hardware Budget

| Subsystem | RAM Allocated (Static) | Flash Allocated |
| :--- | :--- | :--- |
| **Nanopb Static Packet Struct** | 448 bytes | ~3.2 KB |
| **Static TX Buffer** | 256 bytes | N/A |
| **PubSubClient Buffer** | 256 bytes | ~4.1 KB |
| **LittleFS Cache Buffer** | ~4.0 KB | 512 KB Partition |
| **Free Heap Remaining** | **> 280 KB** | **> 3.2 MB** |

---

## 3. Build & Flash Instructions

```bash
# Build firmware
pio run -e esp32dev

# Upload firmware via USB/UART
pio run -e esp32dev -t upload

# Monitor Serial output (115200 baud)
pio device monitor -b 115200
```
