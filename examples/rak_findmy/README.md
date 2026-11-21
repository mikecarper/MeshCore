# RAK WisMesh Tag dog tracker with Apple Find My

These experimental nRF52840 builds run Nordic's Find My accessory service and
a direct MeshCore dog check on the same WisMesh Tag. The sensor sends fresh GPS
fixes to its owner. The tracker companion can also receive a configured
collar's checks and answer with a saved lost status. Both have a USB console.
The ordinary Keymind Cascade Sensor, USB Companion and BLE Companion retain
their full MeshCore roles. They use a different Bluetooth stack. The combined
tracker firmware also includes this Find My service alongside Android Find Hub
and Sidewalk. Its
Apple provisioning address differs from the standalone address below.
[Combined tracker setup](../rak_location/native_pings/README.md)

## First bench test

Use a spare RAK WisMesh Tag with its RAK/Adafruit nRF52840 S140 6.1.1 UF2
bootloader. Enter its bootloader with a double reset and copy one application
UF2 to the mounted drive. Reopen its USB serial console after restart.

```text
tag status
tag sound
dog status
```

The first command reports product-plan and provisioning presence, stack state,
battery voltage and accelerometer detection. The second briefly sounds the
buzzer. The last prints the role, MeshCore public key and tracker state.
A regular unprovisioned Tag should report `ready=0`. Its GPS and MeshCore
radio can be tested immediately. The image creates and saves a dedicated mesh
identity on first boot.

### Test the collar against a Keymind Companion

1. Enter `dog radio <frequency-Hz> <bandwidth-kHz> <SF> <CR>` on the Tag.
   Copy the owner's active radio settings. Supported bandwidths are 62.5, 125,
   250 and 500 kHz. The preamble is 16 symbols, the coding rate is the
   denominator (5 through 8), and transmit power is 14 dBm.
2. Enter `dog owner <full-owner-public-key>`. Use the owner's 64 hexadecimal
   characters. Take the Tag outdoors with a sky view and enter `dog advert`.
   Once GPS establishes UTC, the Tag sends a signed, direct advertisement.
   Save that contact on the owner Companion and set `set lost.reply no` there.
3. Enter `dog check` on the Tag. After a fresh GPS fix it sends one encrypted
   check and listens for the owner's matching reply. In `dog status`,
   `status=1` means safe and `status=2` means lost. Set
   `set lost.reply yes` on the owner and repeat to test the change.
4. Enter `dog on` for automatic checks or `dog off` to stop. The bench
   build checks every 15 minutes when safe or unknown and every minute when
   lost. It waits up to two minutes for a fresh GPS fix. Automatic checks
   use direct radio range.

For a two-Tag test, install the Find My tracker companion on the owner Tag.
Configure matching radio settings on both. On the owner enter
`dog watch <collar-public-key>` and `dog lost no` (or `yes`). Give both Tags
a sky view. `dog last` on the owner shows the last accepted check, including
coordinates in ten-millionths of a degree and battery millivolts. The owner
uses GPS time to reject stale requests and keeps listening while watch is
enabled. Use `dog watch off` to stop it. This test companion exposes its
tracker controls over USB. The full BLE Companion has its own firmware image.

## Add a provisioned Tag to the iPhone

A native iPhone item requires a Find My product plan and an Apple-issued
software authentication UUID and token for that individual accessory. Use
your organization's plan and the instructions accompanying Nordic's SDK. Keep
the plan source, credentials and personalized firmware outside this repo. An
OpenHaystack advertising key cannot enroll a Tag through Apple's Items tab.
[Apple's Find My network accessory program](https://developer.apple.com/find-my/)
and [Nordic's Find My SDK](https://www.nordicsemi.com/Products/Technologies/Apple-Find-My-network)
describe the supported accessory program.

Build with `--product-plan /absolute/path/to/product_plan.c`. It must define
the three product-plan constants specified in the SDK. Use Nordic's
`ncsfmntools provision` with your issued device credentials and
**`--settings-base 0xd2000`** for this flash layout. Provision a fresh test Tag
before pairing. The SDK's generic nRF52840 example address overlaps this RAK
bootloader. The firmware UF2 contains only the application. Program the
device-specific settings using the SDK provisioning procedure, preserving the
bootloader and other settings. Reprovisioning that settings area replaces
existing ownership data.

With the Tag running, check for `plan=configured uuid=present token=present
ready=1` in `tag status`. Enter `tag pair` (or press the Tag's button while
unpaired). On the iPhone open **Find My > Items > + > Add Other Item** and
follow the prompts. Check `paired=1`, then try **Play Sound** in Find My.
An enrollment failure remains visible in `tag status`. Apple's instructions
cover adding an accessory and identifying it in the Items tab.
[Add a third-party item in Find My](https://support.apple.com/guide/iphone/add-a-third-party-item-iph09cb07b40/ios)

## Build

Use an nRF Connect SDK v2.4.2 development environment and a compatible Nordic
Find My SDK v2.4.2 checkout. Build the standard RAK Sensor environment once to
install the matching RadioLib, Crypto and MicroNMEA libraries. From the NCS
environment, with `west` available, run the helper for each role.

```sh
python /path/to/MeshCore/examples/rak_findmy/build.py sensor \
  --sdk /path/to/sdk-find-my --build-dir /work/tag-sensor \
  --output /work/RAK_WisMesh_Tag_FindMy_sensor.uf2

python /path/to/MeshCore/examples/rak_findmy/build.py companion \
  --sdk /path/to/sdk-find-my --build-dir /work/tag-companion \
  --output /work/RAK_WisMesh_Tag_FindMy_companion.uf2
```

Use `--libdeps /absolute/path/to/RAK_WisMesh_Tag_companion_radio_ble` to reuse
another RAK build's libraries. The helper checks the reset vector, stack,
UF2 family, block order and application address range. Use its application
UF2 to update the Tag. The application fits within `0x26000..0xc2fff`.
The [other locator profiles](../rak_location/README.md) reserve the next
flash pages through `0xd1fff`. The Apple settings partition occupies `0xd2000..0xd3fff`, and `0xd4000` and above is reserved.
The SDK and private product data remain external to this repository.
