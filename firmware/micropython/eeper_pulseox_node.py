"""eeper pulse-ox node — MicroPython reference (OPTIONAL, INSIGHTS-ONLY).

Publishes the pulse-ox wire contract to ``eeper/dev/{id}/pulseox``:

    {ts, hr, spo2, perfusion, quality}

``quality`` (0..1) is MANDATORY and honest — the server discards low-confidence samples
rather than store misleading data, so a node that lies about quality just gets its bad
samples dropped. This node computes quality from finger-presence + signal stability and
publishes only when a finger is actually present.

eeper is NOT a vital-sign monitor and this node is NOT a medical device: the readings feed
sleep-trend context and fusion arousal features only — never a readout or alarm. Consumer
optical sensors like the MAX3010x are not medical-grade. Follow safe-sleep guidance.

ESPHome has no MAX3010x heart-rate/SpO2 component, so pulse-ox uses this MicroPython
reference rather than the ESPHome path the movement/presence nodes use. It reuses the
same join/clock/TLS-MQTT plumbing as ``eeper_node.py``.

This is a TEMPLATE: fill in the CONFIG block and wire ``read_pulseox()`` to your sensor
driver. Tested against MicroPython 1.22+ on ESP32 with a MAX30102 breakout on I2C. Copy
``mqtt-ca.crt`` (your deployment's deploy/mosquitto/certs/mqtt-ca.crt) next to this file.

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

PUBLISH_INTERVAL_S = 5
# A sample this far below full confidence is worthless context; don't even send it (the
# server gate would discard it anyway). Keep this at/above the server's threshold.
MIN_QUALITY = 0.5
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
        client_id="eeper-pulseox-{}".format(DEVICE_ID),
        server=MQTT_HOST,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        keepalive=30,
        ssl=True,
        ssl_params=ssl_params,
    )
    # Last-will parked under the device subtree and one level below the metric space so
    # eeper's ingestion ignores it instead of trying to parse it as a reading.
    client.set_last_will(TOPIC_PREFIX + "node/status", b"offline", retain=True)
    client.connect()
    client.publish(TOPIC_PREFIX + "node/status", b"online", retain=True)
    return client


def publish_pulseox(client, hr, spo2, perfusion, quality):
    """Build and publish one pulse-ox reading. ``ts`` is current unix seconds. Fields map
    1:1 to the server's PulseOxMessage contract; ``quality`` is mandatory."""
    payload = json.dumps(
        {
            "ts": time.time() + 946684800,  # MicroPython epoch is 2000-01-01; offset to unix
            "hr": hr,
            "spo2": spo2,
            "perfusion": perfusion,
            "quality": quality,
        }
    )
    client.publish(TOPIC_PREFIX + "pulseox", payload.encode(), qos=1)


def sample_quality(finger_present, perfusion, hr):
    """Honest 0..1 confidence for a sample. A worn, well-perfused sensor reporting a
    plausible rate is high-confidence; no finger or a collapsed signal is ~0. Tune to
    your driver — the point is to NEVER inflate quality, since the server trusts it to
    gate what gets stored."""
    if not finger_present:
        return 0.0
    perf = min(1.0, max(0.0, perfusion / 4.0))  # ~4% perfusion index → solid contact
    plausible = 1.0 if 40.0 <= hr <= 220.0 else 0.0
    return round(perf * plausible, 3)


def read_pulseox():
    """REPLACE ME. Read your MAX3010x driver and return
    ``(finger_present, hr, spo2, perfusion)``. The example returns an idle no-finger
    sample so the template runs (and publishes nothing) before you wire the sensor.

    A real implementation collects a window of IR/red samples, runs the driver's HR/SpO2
    estimator, and reads the perfusion index. Keep raw optical data on-device — only the
    derived numbers above ever leave the node."""
    return (False, 0.0, 0.0, 0.0)


def main():
    connect_wifi()
    if not sync_clock():
        raise OSError("clock sync failed — readings would be rejected as stale")
    client = connect_mqtt()
    try:
        while True:
            finger_present, hr, spo2, perfusion = read_pulseox()
            quality = sample_quality(finger_present, perfusion, hr)
            # Publish only usable context; a low-quality sample would be discarded
            # server-side anyway, so save the bandwidth and don't send it.
            if quality >= MIN_QUALITY:
                publish_pulseox(client, hr, spo2, perfusion, quality)
            time.sleep(PUBLISH_INTERVAL_S)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
