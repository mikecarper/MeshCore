# Sensor Find My beacon

The experimental Sensor Find My beacon advertises an OpenHaystack-compatible
public key over Bluetooth Low Energy. Nearby Apple devices can report its
location to Apple's Find My network. The beacon is independent of the LoRa
radio and starts off until configured.

The initial test builds are `heltec_v4_sensor_findmy` and
`RAK_WisMesh_Tag_sensor_findmy`. The feature is compiled only when
`MESH_SENSOR_FINDMY=1`; ordinary Sensor builds do not include it. A RAK Tag
has been observed transmitting the expected beacon, but end-to-end location
reports and battery life have not yet been qualified.

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
the private key into the Sensor console or send it over LoRa. Use
`set findmy.key clear` to remove the public key and turn the beacon off.

Normal advertising uses an interval of about 10 seconds; a confirmed box exit
or an owner-reported lost status changes the interval to about 1.28 seconds.
`get findmy` reports the requested rate. See [Sensor dog tracker](sensor_tracker.md)
for box configuration and the difference between a new fix and a last-known
position. A missing fix does not establish that the dog is inside.

## Location reports and power

This beacon does not register the Sensor as an official accessory in Apple's
Find My app. A separate compatible service and client, such as a
Macless-Haystack installation, are needed to retrieve and view reports. Those
services and their licenses are separate from the Sensor firmware. Reports
depend on a participating Apple device passing within Bluetooth range and can
arrive late; the reported location is approximate. This feature does not use
Google Find Hub.

Bluetooth advertising uses additional power and can prevent ESP32 manual light
sleep while active. On nRF52, the beacon selects 0 dBm Bluetooth transmit
power; BLE DFU keeps its existing +4 dBm setting. The beacon is suspended
during emergency-power sleep windows; it cannot be found through the beacon
while suspended or powered off.
Bluetooth startup and shutdown can briefly pause sensor radio processing;
failures can take several seconds to time out. Power consumption, radio
coexistence, and real-world report delivery need hardware testing.
