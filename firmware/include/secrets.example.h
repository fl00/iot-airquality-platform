/**
 * Copy to secrets.h (git-ignored) and fill in.
 * DEVICE_ID must be unique per device and match [A-Za-z0-9_-]{1,31}: the ingestor drops packets whose
 * payload device_id differs from the id in the topic sensors/<id>/airquality.
 */
#pragma once

#define WIFI_SSID          "my-ssid"
#define WIFI_PASSWORD      "my-password"
#define MQTT_BROKER_HOST   "192.168.1.100"
#define MQTT_BROKER_PORT   1883
#define DEVICE_ID          "sensor-esp32-01"

// Required when the broker enforces authentication (always the case after
// deploy/deploy-baremetal.sh: see /etc/iot-airquality/sensor-credentials).
// #define MQTT_USER       "sensor"
// #define MQTT_PASSWORD   "change-me"
