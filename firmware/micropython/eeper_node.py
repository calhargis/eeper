"""eeper sensor node — MicroPython fallback template.

A minimal, dependency-light node for boards or sensors ESPHome doesn't cover. It does
what the ESPHome reference configs do, by hand: join WiFi, sync the clock over SNTP
(the contract's ``ts`` is unix seconds, and eeper derives online/offline health from
it — an unsynced node reads as permanently offline), open a TLS MQTT connection with
the deployment's CA, and publish SensorMessage JSON to ``eeper/dev/{id}/{metric}``.

This is a TEMPLATE: fill in the CONFIG block and replace ``read_metrics()`` with your
sensor. Tested against MicroPython 1.22+ on ESP32. Copy ``mqtt-ca.crt`` (your
deployment's deploy/mosquitto/certs/mqtt-ca.crt) next to this file on the device.

The per-device username/password and numeric id come from the eeper web UI's
Devices → Pair screen; the password is shown once, so save it when you pair.
"""

import json
import time

import network
import ntptime
from umqtt.simple import MQTTClient

# ─── CONFIG — fill these in ──────────────────────────────────────────────────
WIFI_SSID = "your-wifi-ssid"
WIFI_PASSWORD = "your-wifi-password"

DEVICE_ID = 0  # ← the numeric id from the eeper Devices screen
MQTT_HOST = "eeper.local"  # ← the eeper host on your network
MQTT_PORT = 8883
MQTT_USER = "dev-0"  # ← always dev-{DEVICE_ID}
MQTT_PASSWORD = "paste-the-per-device-password"  # ← shown once at pairing
CA_CERT_PATH = "mqtt-ca.crt"  # ← your deployment's MQTT CA, copied onto the device

PUBLISH_INTERVAL_S = 15
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_PREFIX = "eeper/dev/{}/".format(DEVICE_ID)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(40):  # ~20 s
            if wlan.isconnected():
                break
            time.sleep(0.5)
    if not wlan.isconnected():
        raise OSError("wifi connect failed")
    return wlan


def sync_clock():
    """Block until SNTP gives us a real time; without it every reading is stamped 1970
    and the device never shows online. Retries — NTP can be flaky right after boot."""
    for _ in range(10):
        try:
            ntptime.settime()  # sets the RTC to UTC
            return True
        except OSError:
            time.sleep(2)
    return False


def connect_mqtt():
    with open(CA_CERT_PATH, "rb") as fh:
        ca = fh.read()
    # Verify the broker against the eeper CA (the broker is TLS-only, no anonymous).
    ssl_params = {"cadata": ca, "server_hostname": MQTT_HOST}
    client = MQTTClient(
        client_id="eeper-node-{}".format(DEVICE_ID),
        server=MQTT_HOST,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        keepalive=30,
        ssl=True,
        ssl_params=ssl_params,
    )
    # Last-will mirrors the ESPHome nodes: parked under the device subtree (the ACL
    # forbids publishing anywhere else) and one level below the metric space so eeper's
    # ingestion ignores it instead of trying to parse it as a reading.
    client.set_last_will(TOPIC_PREFIX + "node/status", b"offline", retain=True)
    client.connect()
    client.publish(TOPIC_PREFIX + "node/status", b"online", retain=True)
    return client


def publish_reading(client, metric, value, unit, quality):
    """Build and publish one SensorMessage. ``ts`` is current unix seconds."""
    payload = json.dumps(
        {
            "ts": time.time() + 946684800,  # MicroPython epoch is 2000-01-01; offset to unix
            "type": metric,
            "value": value,
            "unit": unit,
            "quality": quality,
        }
    )
    client.publish(TOPIC_PREFIX + metric, payload.encode(), qos=1)


def read_metrics():
    """REPLACE ME. Return a list of (metric, value, unit, quality) tuples for your
    sensor. ``quality`` is 0..1. Example returns a dummy movement reading."""
    return [("movement", 0.0, "index", 0.5)]


def main():
    connect_wifi()
    if not sync_clock():
        raise OSError("clock sync failed — readings would be rejected as stale")
    client = connect_mqtt()
    try:
        while True:
            for metric, value, unit, quality in read_metrics():
                publish_reading(client, metric, value, unit, quality)
            time.sleep(PUBLISH_INTERVAL_S)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
