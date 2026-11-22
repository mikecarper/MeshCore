# Apple Find My, Google Find Hub and Sidewalk gateway location on one RAK WisMesh Tag

The experimental Sensor and USB tracker Companion each run Apple Find My (A),
Android Find Hub (B) and Amazon Sidewalk gateway location (C) on one nRF52840. The
Bluetooth controller interleaves advertisements using a separate identity for
each service. All three are selected on first boot. Each SDK manages its own
enrollment and changes of state. Regular pings share one saved interval. The tracker also runs
the direct GPS and LoRa dog-check protocol. [Tracker commands](../../rak_findmy/README.md#test-the-collar-against-a-keymind-companion)

## Check the pings

On a fresh bench tag with its stock S140 6.1.1 UF2 bootloader, double-reset
and copy the UF2 for your role to the bootloader drive. Open the USB serial
console after restart.

| Command | What to look for |
| --- | --- |
| `pings status` | Selected services and heartbeat interval for this boot and the next boot. |
| `pings interval [seconds]` | Read the interval or save a whole number from 1 to 300. The default is 10 seconds. Reset the tag to apply a change. |
| `finder ping` | A, B and C each show their current native advertising state and the number of payload updates and starts the Bluetooth host accepted. |
| `tag status` | A needs a configured product plan, device credentials and successful iPhone enrollment. Its counter recognizes separated-mode advertisements. |
| `findhub status` | B reports ready after reading a valid Google model record. Check for `provisioned=1` after pairing with Android. |
| `sidewalk status` | C reports `mode=location`. Look for `registered=1`, `cloud_ble=1` and `gateway_location=1` on a gateway that supports positioning. |
| `sidewalk locate` | Queue a gateway location request immediately while connected. `pending=1` indicates a request in progress. `sent` counts successful uplinks. |

To use all three, run `pings on all` and reset the tag. To use A and C alone,
run `pings off B` and reset. To use only B, run `pings off all`, then
`pings on B`, and reset once. The saved choice survives power loss.

For example, enter `pings interval 30` and reset once to send one regular
heartbeat per enabled network about every 30 seconds. Enter `pings interval`
to see the active and saved values. Apple pairing and fast reconnect and
Android Fast Pair discovery keep their native timing. A longer interval gives
a passing receiver fewer chances to hear the tag.

The controller repeats accepted payloads automatically, so the counters can
stay steady while packets continue. Pairing, owner proximity and live
connections can change advertising state. Check the item timestamp in Find My
or Find Hub to confirm a sighting.

C automatically requests a BLE gateway connection and, once registered and
connected, requests an approximate position at the configured ping interval.
Only one request runs at a time. Timeouts, retries and gateway availability can
delay updates. To check coverage manually, run `sidewalk connect`, then
`sidewalk status`. A gateway with Community Finding enabled can report
`gateway_location=1`. Run `sidewalk locate` and check for a new timestamp
and coordinates at your configured AWS location destination. These coordinates
are based on the gateway. The collar's own GPS fix travels separately over LoRa.

## Prepare a tag for live tests

Use a fresh test tag. For Apple, build the combined UF2 with your product
plan. Before first boot, provision its Apple-issued device UUID and token with
Nordic's `ncsfmntools provision` using **`--settings-base 0xb9000`**. The image
built without a product plan reports `plan=absent` and leaves A idle.
On the iPhone use **Find My → Items → + → Add Other Item** after entering
`tag pair`. [Find My enrollment](../../rak_findmy/README.md#add-a-provisioned-tag-to-the-iphone)

For B, use the registered Google model and its provisioning record at
`0xc8000`, then enroll it with the owner’s Android phone. For C, use the
tag's AWS manufacturing record at `0xd1000`, enable positioning in AWS IoT
Wireless and configure a location destination. The existing provisioning checker
validates the two record files before programming.
[Google model setup](../README.md#google-find-hub-item-test) · [AWS device provisioning](../README.md#sidewalk-gateway-test)

The combined UF2 writes only application blocks starting at `0x26000` and
ending before `0xb9000`. Settings and ownership records share eight flash
sectors at `0xb9000..0xc0fff`. Program device records using sector erase and
retain the original provisioning material. Older controller-test and
single-network images have different application limits and settings
layouts. Flashing one over an enrolled combined build can overwrite its
settings.

## Build from source

Use the Nordic Sidewalk v1.2.0 workspace with nRF Connect SDK v3.3.0, a
Find My SDK Git checkout with tag v2.4.2, and the ordinary RAK Sensor's
RadioLib, Crypto and MicroNMEA dependencies. Set `ZEPHYR_BASE` to the
workspace's Zephyr directory and activate its toolchain environment.
The build reads the pinned SDK sources without editing the SDK checkout.
[SDK and dependency setup](../controller_test/README.md#rebuild)

```sh
python examples/rak_location/controller_test/build.py sensor --native-pings \
  --findmy-sdk /work/sdk-find-my --product-plan /private/product_plan.c \
  --build-dir /work/native-sensor --output /work/RAK_native_ABC_sensor.uf2
```

Use `companion` and a separate build directory for the USB tracker Companion.
Omit `--product-plan` for an unprovisioned bench build. Supply `--libdeps` if
the RAK libraries are in a different checkout.

## Repeat the offline tests

With Python 3.11 or later, install `pyelftools`, `unicorn==2.1.4` and
`bumble==0.0.236`. Run these against the ELF for each role.

```sh
python examples/rak_location/native_pings/verify_boot.py /work/native-sensor/native-firmware/zephyr/zephyr.elf
python examples/rak_location/native_pings/verify_location.py /work/native-sensor/native-firmware/zephyr/zephyr.elf
python examples/rak_location/native_pings/verify_interval.py /work/native-sensor/native-firmware/zephyr/zephyr.elf
python examples/rak_location/native_pings/verify_pings.py /work/native-sensor/native-firmware/zephyr/zephyr.elf
python examples/rak_location/native_pings/verify_pings.py /work/native-sensor/native-firmware/zephyr/zephyr.elf --interval 300 --virtual-time
```

The boot test executes the application’s ARM code with simulated drivers for
all eight selections. The location test exercises automatic and manual gateway
requests with simulated Sidewalk responses. The interval test exercises USB settings and checks that
the linked Zephyr host and Nordic radio controller accept the full interval
without truncation. The scanner test runs the linked native advertising
functions with synthetic fixtures, enables three advertising sets on one
virtual controller and checks the gaps between advertisements with one passive
scanner. The optional virtual clock advances to each scheduled event.
Physical RF reception, native app enrollment and network sightings require a
provisioned tag and nearby participating phones or gateways.
