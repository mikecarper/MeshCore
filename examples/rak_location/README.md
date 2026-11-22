# RAK WisMesh Tag location test firmware

For one RAK WisMesh Tag to use Apple Find My, Android Find Hub and Amazon
Sidewalk gateway location together, use the **combined native pings** Sensor or
USB tracker Companion build. Each service has a separate Bluetooth identity.
[Combined firmware, setup and USB commands](native_pings/README.md)

## Individual network images

Use **Sidewalk Sensor** for a collar bench test. The tag uses GPS and LoRa for
encrypted Keymind Cascade checks. It can also request an independent approximate
position from a nearby Amazon Sidewalk Bluetooth gateway with Community Finding
enabled. AWS delivers that position to your configured location destination.

There are separate **Find Hub** builds for testing an item with Android.
Each service has a Sensor and a USB tracker Companion image. Both roles can
send checks. The Companion can also receive a configured tag's checks and
answer with the owner's saved lost status. These experimental images have a
USB shell and direct LoRa tracker protocol. The standard firmware still
provides the full Sensor and Companion interfaces.

| Slot and image | First USB commands | Live test requires |
| --- | --- | --- |
| A · Apple Find My | `pet status` and `tag status` | A product plan and the individual accessory's Apple-issued credentials. See the [iPhone setup guide](../rak_findmy/README.md). |
| B · Android / Google Find Hub | `pet status` and `findhub status` | Your registered Find Hub-capable Google model and a compatible Android phone configured for that model |
| C · Amazon Sidewalk presence | `pet status` and `sidewalk status` | A registered AWS Sidewalk device, a participating gateway and AWS proximity events enabled |
| C · Amazon Sidewalk gateway location | `pet status` and `sidewalk status` | An AWS Sidewalk device and credentials, positioning enabled with a destination, and a participating gateway |

Each image in the table runs one commercial network. Changing a Bluetooth address does
not enroll an accessory. Before switching a paired item to a different network,
remove it from the original owner's app and complete that service's reset or
deregistration. Google specifies one active finding network per device.
[Google switchable protocol guidelines](https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn#switchable-protocol-guidelines)

## Test one-way sightings

For a Sidewalk last-seen timestamp, flash **C presence** and provision the tag
with its own AWS manufacturing data as described below. In AWS IoT Wireless,
enable proximity events for the registered wireless device. In the AWS IoT
MQTT test client, subscribe to the following topic after replacing the final
placeholder with its wireless device ID.

```text
$aws/iotwireless/events/proximity/+/sidewalk/wireless_devices/wireless_device_id/<WirelessDeviceId>
```

Open the USB console and run `sidewalk status` to check for `mode=proximity`
and `registered=1`. Bring the tag near a participating gateway. AWS filters
received beacons on a ten-minute interval. A `beacon_discovered` event with
your device ID and a timestamp confirms a sighting. These events contain no
coordinates. The Sidewalk task sleeps between SDK events and makes no
connection or location requests. The Sidewalk stack can still exchange traffic
for registration or clock maintenance.
[AWS proximity events](https://docs.aws.amazon.com/iot-wireless/latest/developerguide/iot-sidewalk-proximity-events.html)
and [Sidewalk Bluetooth behavior](https://docs.sidewalk.amazon/specifications/sidewalk-protocol-spec/ble-protocol-stack/amazon-sidewalk-ble-protocol-stack.html)

On any individual A, B or C build, run `finder ping` in the USB console.
`payload_updates` counts matching advertising payloads accepted by the tag's
Bluetooth host. `advertising_starts` counts successful starts with one of
those payloads. The controller repeats each advertisement on its own, so these
counters can stay steady while the radio continues to transmit. Apple counts
separated-mode frames after enrollment. Android counts Find Hub frames after
provisioning. Sidewalk counts its service and manufacturer data together,
including enrollment beacons. Check the owner's Find My or Find Hub app, or
the AWS event above, to confirm a network sighting.

## First test with an unprovisioned tag

1. On a RAK **WisMesh Tag** with its stock S140 6.1.1 UF2 bootloader,
   double-reset and copy the chosen application UF2 onto the bootloader drive.
   The tag reboots. Open its USB serial console.
2. Run the two status commands in the table. On a fresh tag, Sidewalk reports
   `needs AWS manufacturing data`. Google reports `Needs Google model registration data`.
   `pet status` shows the tracker role and the tag's mesh public key.
3. Test `pet radio`, `pet owner`, and `pet check` with a Keymind Companion.
   Each network profile has its own stored mesh identity and owner settings.
   [Radio setup and expected replies](../rak_findmy/README.md#test-the-collar-against-a-keymind-companion)

## Sidewalk gateway test

Register an endpoint in your AWS account, enable positioning, and configure the
location destination. Use the Nordic Sidewalk add-on v1.2.0 provisioning tool
in a private working directory. For a device certificate issued in the AWS
prototype flow, generate the RAK's manufacturing file with the following command.
[Set up the Sidewalk prototype](https://github.com/nrfconnect/sdk-sidewalk/blob/v1.2.0-add-on/doc/setting_up_sidewalk_environment/setting_up_sidewalk_prototype.rst)

```sh
python /path/to/sidewalk/tools/provision/provision.py nordic aws \
  --certificate_json certificate.json --chip nrf52840 --addr 0xd1000 \
  --output_bin mfg.bin --output_hex rak-sidewalk.hex
python /path/to/MeshCore/examples/rak_location/check_provision.py sidewalk rak-sidewalk.hex
```

Keep the AWS certificate and generated files private. On a fresh test tag,
program the verified manufacturing page using a debug probe in **sector erase**
mode. Preserve the bootloader and settings. The Nordic SDK imports device
secrets into its trusted storage on first boot. Retain the original
provisioning material. This bench build uses Nordic's nRF52 UID-derived
storage key for the stock bootloader.

1. Run `sidewalk status`, then `sidewalk connect` near a participating gateway.
2. When `registered=1` and `cloud_ble=1`, run `sidewalk locate`.
   `gateway_location=1` means that the gateway reported L1 location available,
   `0` means unavailable, and `-1` means it has not reported availability.
   The standalone location firmware also requests a location about every 15 minutes
   while connected. The combined build uses its saved `pings interval`.
3. Watch `sent` for a successful SDK uplink, then check the timestamp and
   coordinates at your AWS location destination. The second check confirms a
   sighting. The gateway position and the collar's GPS fix can differ.
   [AWS Sidewalk location setup](https://docs.aws.amazon.com/iot-wireless/latest/developerguide/sidewalk-configure-location.html)

## Google Find Hub item test

Use your own registered model ID and anti-spoofing key from Google's Nearby
Device Console. The manufacturer's and model's displayed names must match
your registration. Set them in `examples/rak_findhub/prj.conf` when building
for that model. Generate the private record using Nordic's Fast Pair
provisioning tool with **`--address 0xc8000`**. Run the check below, then
program only the verified page on a fresh test tag using a debug probe with
sector erase. The SDK validates the complete record on startup.
[Nordic Fast Pair provision tool](https://github.com/nrfconnect/sdk-nrf/tree/v3.3.0/scripts/nrf_provision/fast_pair)
and [Google Find Hub device registration](https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn)

```sh
python /path/to/MeshCore/examples/rak_location/check_provision.py findhub rak-findhub.hex
```

1. Open the USB console. Verify `findhub status` reports `Ready` and use
   Android's Fast Pair prompt. For a development model use Google's authorized
   test account and model configuration. Verify `provisioned=1`.
2. Open the item in Find Hub and choose **Play sound**. The RAK's buzzer
   should sound. Press the physical button to silence it. The LIS2DH
   accelerometer supplies real motion observations for the SDK's unwanted
   tracker detection. Check sound level, identification, disablement and
   separation alerts on the actual hardware before deployment. The advertising
   power correction starts at the Nordic development-kit value and needs
   measurement on the RAK antenna.
3. For a network sighting, move the paired phone away and check the location
   timestamp after another eligible Android device passes the item.

| Button action after startup | Result |
| --- | --- |
| Tap and release | Toggle Fast Pair discoverability when unpaired |
| Hold 2 to under 5 seconds and release | Enter identification mode. Green LED lights |
| Hold 5 to under 8 seconds and release | Enter recovery mode. Green LED lights |
| Hold 8 to under 12 seconds and release | Power down. Test on battery; press the button to wake |
| Hold at least 12 seconds and release | Factory reset the Google pairing and finder keys |

The blue LED indicates Fast Pair advertising. A press while ringing silences
the buzzer and consumes that press. Native Google support is intended for
item-finding tests. [Google Find Hub guidance](https://support.google.com/android/answer/3265955)

## Rebuild

Use Nordic's nRF Connect SDK **v3.3.0** SDK environment. For Sidewalk, use the
[Sidewalk add-on v1.2.0 manifest](https://github.com/nrfconnect/sdk-sidewalk/blob/v1.2.0-add-on/west.yml),
which pins that SDK. Set `ZEPHYR_BASE` to the SDK's `zephyr` directory.
Build the ordinary PlatformIO RAK Sensor once to install the matching
RadioLib, Crypto and MicroNMEA dependencies, or pass their directory
using `--libdeps`. Use a separate build directory for each network and role.

```sh
python /path/to/MeshCore/examples/rak_location/build.py C sensor --proximity \
  --build-dir /work/rak-presence-sensor --output /work/RAK_C_presence_sensor.uf2
python /path/to/MeshCore/examples/rak_location/build.py sidewalk sensor \
  --build-dir /work/rak-sidewalk-sensor --output /work/RAK_Sidewalk_sensor.uf2
python /path/to/MeshCore/examples/rak_location/build.py findhub companion \
  --build-dir /work/rak-findhub-companion --output /work/RAK_FindHub_companion.uf2
```

The helper packages and verifies the executable's UF2 blocks. The application
occupies `0x26000` through at most `0xc2fff`. Google settings occupy
`0xc3000..0xc7fff` and its model record occupies `0xc8000..0xc8fff`.
Sidewalk settings occupy `0xc9000..0xd0fff` and its device record occupies
`0xd1000..0xd1fff`. Apple settings occupy `0xd2000..0xd3fff`. The stock
RAK filesystem and reserved area start at `0xd4000`. A firmware UF2 contains
only the application. The separate provisioning checker verifies addresses,
record headers, and the Google checksum. Network enrollment and gateway
coverage require a live test.

To run the offline startup regression on a built ELF, install `pyelftools` and
`unicorn==2.1.4` in a test environment and run the command below for each role.
It executes the compiled ARM application entry with simulated flash and RTOS
calls, checks the exact provisioning-page address, and verifies that the
MeshCore worker is scheduled with both blank and unreadable storage.

```sh
python examples/rak_location/tests/blank_boot.py sidewalk /work/rak-sidewalk-sensor/zephyr/zephyr.elf
```

Run the advertising-counter regression for any native build and the
event-loop regression for a C presence build. The tests execute the firmware
in an ARM emulator with simulated Bluetooth and Sidewalk SDK responses.

```sh
python examples/rak_location/tests/native_pings.py C /work/rak-presence-sensor/zephyr/zephyr.elf
python examples/rak_location/tests/sidewalk_presence.py /work/rak-presence-sensor/zephyr/zephyr.elf
```

The [three-library controller test](controller_test/README.md) links all three
SDKs into one RAK image per role. Run `locator ids start` to broadcast three
named BLE beacons concurrently, each with its own address. For example,
`locator ids stop B` stops the Android test beacon and `locator ids start B`
restarts it. The native build helper accepts the same letters. Use the
v2.4.2 SDK environment and pass `--sdk /path/to/sdk-find-my` for A.
Use the v3.3.0 environment for B and the Sidewalk add-on for C.
