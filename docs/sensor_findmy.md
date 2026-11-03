# Sensor Find My beacon

The experimental Sensor Find My beacon advertises an OpenHaystack-compatible
public key over Bluetooth Low Energy. Nearby Apple devices can report its
location to Apple's Find My network. The beacon is independent of the LoRa
radio and starts off until configured.

The initial test build is `heltec_v4_sensor_findmy`. The feature is compiled
only when `MESH_SENSOR_FINDMY=1`; it is not included in ordinary Sensor
builds. This first implementation targets ESP32 boards with a compatible BLE
controller. It has not yet been qualified on hardware.

## Setup

Create a tracker using [Go Haystack](https://github.com/hybridgroup/go-haystack)
or another compatible key generator. Keep its private key and account
credentials in your tracking tools. Copy only its **28-byte public advertising
key**, encoded as standard padded Base64, to the Sensor:

```text
set findmy.key <base64-public-key>
set findmy on
get findmy
```

Use `get findmy` to check whether the saved setting and beacon state are
available. Turn advertising off with:

```text
set findmy off
```

The experimental beacon uses one fixed public key. It does not rotate its
identity, so nearby observers can recognize the device over time. Do not paste
the private key into the Sensor console or send it over LoRa.

## Location reports and power

This beacon does not register the Sensor as an official accessory in Apple's
Find My app. A separate compatible service and client, such as a
Macless-Haystack installation, are needed to retrieve and view reports. Those
services and their licenses are separate from the Sensor firmware. Reports
depend on a participating Apple device passing within Bluetooth range and can
arrive late; the reported location is approximate. This feature does not use
Google Find Hub.

Bluetooth advertising uses additional power and can prevent ESP32 manual light
sleep while active. The beacon is suspended during emergency-power sleep
windows; it cannot be found through the beacon while suspended or powered off.
Bluetooth startup and shutdown can briefly pause sensor radio processing;
failures can take several seconds to time out. Power consumption, radio
coexistence, and real-world report delivery need hardware testing.
