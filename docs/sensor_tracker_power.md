# RAK WisMesh Tag dog tracker: power target

The design target is at least 30 days between charges in normal use. RAK
specifies a nominal 1,000 mAh battery, an nRF52840, an LIS2DH accelerometer,
and an AT6558R GNSS receiver in the
[WisMesh Tag](https://docs.rakwireless.com/product-categories/meshtastic/wismesh-tag/overview/).
Actual usable capacity and current must be measured on the hardware.

At nominal capacity, 1,000 mAh / 720 hours is 1.39 mA average. Allowing an
illustrative 20% capacity reserve leaves about 1.11 mA average. This budget
includes the entire board, motion sensing, GNSS, BLE, LoRa, and unsuccessful
fix attempts; it is not a measured battery-life estimate.

GNSS acquisition is a major uncertainty. As arithmetic examples only, at
an assumed 20 mA while acquiring, a 30-second acquisition every 15 minutes
averages 0.67 mA; a 120-second acquisition every 15 minutes averages 2.67 mA.
Both exclude all other loads. Actual current, fix time, available capacity,
and conversion efficiency must be measured on the Tag.

The intended normal policy is to use low-power motion sensing, sleep the CPU
and LoRa receiver between needed work, request fresh GNSS fixes when movement
and the fix schedule warrant them, and advertise Find My slowly. A confirmed
outside fix or the owner's lost state can request a faster Find My pace.
Motion by itself must not assert that the dog is outside; no fix must not
assert that the dog is inside. Alert operation may use power much faster than
normal operation.

Before treating the target as met, measure average board current, the GNSS
startup and failed-fix costs, motion behavior with GNSS power removed, BLE
advertising while the CPU sleeps, and representative LoRa receive and transmit
windows. Validate the moving, stationary, poor-sky, and lost cases separately.
Long advertising intervals can increase the time before a nearby phone hears
the beacon. On ESP32, leaving BLE on can also prevent manual light sleep; an
interval change alone is not proof of low overall current.

On one connected Tag, the accelerometer answered with identity `0x33` at I2C
address `0x19` both while the GPS enable pin was high and while it was low.
Its internal latch also reported an event during an electrical self-test.
A Bluetooth receiver observed the expected Find My advertisement, and the
unconnected BLE DFU start/stop sequence suspended and resumed the beacon.
These checks do not measure current, prove real animal-motion sensitivity,
validate a connected DFU transfer, or establish Apple location delivery.

## Hiking mode and planned phone proximity

Hiking mode uses more frequent motion-triggered fixes than backyard mode.
An authenticated owner-phone connection could supply a near indication, but a
disconnect alone is not proof of separation: phone background restrictions,
interference, and a flat phone battery can also cause it. The Sensor firmware
does not yet provide this phone connection or select modes using it.

The Tag's BLE firmware has one advertising set. A phone service, the Find My
beacon, and BLE firmware-update mode therefore need an explicit ownership and
handoff policy. A usable phone service also needs compatible phone-side code,
authentication, and background-operation tests. Measure the battery impact
and verify coexistence before treating phone proximity as a reliable signal.

An ESP32 connected to the home Wi-Fi could provide a separate authenticated
Bluetooth home beacon. Multiple configured beacons could cover a large yard;
their packets can be received in the same scan window. A fresh, trusted home
observation is intended to suppress automatic box and phone-disconnect alerts,
but never an explicit owner lost setting. A missing or expired beacon is
unknown, not evidence that the dog has left. The gateway and codec are
experimental; tracker-side scanning and provisioning are not yet implemented,
and the gateway has not been tested over the air. Bluetooth coverage is not an
exact property boundary.
