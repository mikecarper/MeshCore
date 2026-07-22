# Easy firmware updates over LoRa

This guide shows the shortest manual path for sending firmware from a computer to a MeshCore node over
LoRa. Choose the package type for the **destination** node:

| Destination | Update type | Files needed to build the `.mota` | Installer |
| --- | --- | --- | --- |
| ESP32 | Full firmware | New non-merged application `.bin` | ESP32 A/B firmware slots |
| nRF52 | In-place delta | Exact running `firmware.hex` and new `firmware.hex` | Exact-board OTAFIX bootloader |

An nRF52 node cannot install an ESP32-style full-image container. It deliberately accepts only an in-place
delta built against its exact running firmware.

## Temporary OTA channel used in this guide

| Setting | Value |
| --- | --- |
| Center frequency | 909.950 MHz |
| Bandwidth | 250 kHz |
| Spreading factor | SF5 |
| Coding rate used in this guide | CR5 |
| Example window | 120 minutes |

The copy/paste command is:

```text
tempradio 909.950,250,5,5,120
```

The fourth value is the transmit coding rate. This guide uses CR5, but the participating nodes' coding rates
do not need to match.

`tempradio` is not saved and the node returns to its normal radio settings when the window ends or the node
reboots. This frequency is intended for North American configurations. Confirm that it is permitted in your
location and change it when necessary.

## Before you start

Both paths require:

- An OTA-enabled repeater build whose artifact filename contains `-ota-` on the source, destination, and
  every intermediate node. The `-ota-` stamp confirms that LoRa OTA is compiled into the build. Logging,
  MQTT, and untagged builds cannot perform LoRa OTA; use WiFi or USB to replace them first.
- A source node connected to the computer by USB serial.
- Overlapping `tempradio` windows on the source, destination, and every repeater needed between them.

LoRa OTA packets are generated, received, and relayed only while `tempradio` is active. If any required
window closes, the transfer stops making progress and can resume during a later overlapping window.

### Choose the source radio

Use an ESP32 **MeshCore companion** as the source node. It receives the update folder from the computer,
then advertises it over LoRa. Connect to that companion either by USB serial or by WiFi. For USB serial, the
companion firmware must include `OTA_FOLDER_SERIAL`; before starting the transfer, its USB CLI must accept:

```text
ota folder on
```

If that command reports that `OTA_FOLDER_SERIAL` is not built in, use the WiFi method below. Do **not** use
a KISS modem: KISS firmware is a TNC/KISS frame interface and does not provide the MeshCore CLI or the
OTA-folder transport that `motatool serve` requires.

For a companion connected by WiFi, use its dedicated OTA seeder connection instead:

```bash
motatool serve --dir ./motas --tcp <companion-host>:5001 -v
```

Port `5001` is the OTA seeder port; it is separate from the companion application port (`5000`).

## Install `motatool`

Install Rust if necessary, then install the standalone packaging and serving tool:

```bash
git clone https://github.com/vk496/motatool
cargo install --path ./motatool
```

## ESP32: package a full firmware image

The destination must be an OTA-capable ESP32 with an A/B partition table. Download or build the new
non-merged `.bin` application for the destination's exact board **and role**. Do not use an ESP32
`-merged.bin` factory image.

Put the application firmware in a working directory, then build a full `.mota` container. For example:

```bash
mkdir -p ./motas
motatool build --fw ./Heltec_v3_repeater-ota-v1.16.05.bin --out-dir ./motas
motatool verify ./motas/*.mota
```

Replace the example filename with the firmware for the destination's exact target. With no `--base`
argument, `motatool build` creates a full-image update. The firmware must contain its MeshCore `EndF`
identity trailer so `motatool` and the destination can verify the target, hardware, and version.

Do not continue if `motatool verify` reports a failure.

## nRF52: package an in-place delta

### 1. Install and check the OTAFIX bootloader

This is a one-time prerequisite. Install the OTAFIX bootloader built for the destination's **exact board**
from the
[OTAFIX 2.4 nRF52 bootloader release](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.9.2-OTAFIX2.4).
Follow the release's board-specific installation and erase instructions. If it does not contain the
destination's exact board, this LoRa install path is not yet available for that board; never substitute a
similar board's bootloader.

Before preparing or downloading a LoRa update, run this on the destination:

```text
ota self
```

Continue only if the reply includes:

```text
bootloader: apply OK
```

The reply also contains the running firmware's `base_hash`. Save it for the package check below. A stock,
legacy, or older OTAFIX bootloader without `.mota` in-place-apply support will report that apply support is
missing, and `ota install` will refuse to reboot into it.

### 2. Keep the exact current and new application images

You need the raw `.pio/build/<environment>/firmware.hex` from the build that is **actually running**, plus
the corresponding `firmware.hex` from the new build. Save the current file before building the new version,
because PlatformIO reuses that path. For example:

```bash
# Save this immediately after building/flashing the version now running on the node.
cp .pio/build/Heltec_t114_repeater/firmware.hex ./Heltec_t114_repeater-running.hex

# After checking out and building the new version, save its image separately.
cp .pio/build/Heltec_t114_repeater/firmware.hex ./Heltec_t114_repeater-new.hex
```

Replace `Heltec_t114_repeater` with the destination's exact PlatformIO environment. The two images must be
for the same board and role, and both must contain their `EndF` trailers. Do not pass a release `.uf2` or
BLE-DFU `.zip` to `motatool`; those are installation containers rather than raw application images.

Keeping a file with the same version label is not enough: the base must be byte-for-byte identical to the
running application. The hash check in the next step proves that it is the right file.

On RAK4631 repeaters, use the
`RAK_4631_repeater_lora_ota_no_external_sensors` environment. It retains built-in battery monitoring but
omits optional external environmental/GPS sensor packages so the delta fits the safe in-place workspace.

### 3. Build and check the in-place delta

```bash
mkdir -p ./motas
motatool build \
  --base ./Heltec_t114_repeater-running.hex \
  --fw ./Heltec_t114_repeater-new.hex \
  --patch-type in-place \
  --out-dir ./motas
motatool verify ./motas/*.mota
```

`motatool` prints the generated filename. Inspect that file:

```bash
motatool inspect ./motas/GENERATED_FILENAME.mota
```

Check all three of these before serving it:

- `codec_id` is `2 (detools-in-place)`.
- `base_hash` is the same 8-byte value reported by the destination's `ota self` command.
- The numeric `target_id` exactly matches `target:` in the destination's `ota status`, and the hardware and
  firmware version identify the intended board and role. If `inspect` shows `N/A` for the human-readable
  target name, the tool's name table is older than that environment; the numeric IDs still must match.

The default `--inplace-memory 0x98000` and 4096-byte segment size match the supported MeshCore OTAFIX
builds; do not override them for this normal nRF52 flow. Do not continue if verification or any identity
check fails.

## Transfer and install either package

### 1. Start the temporary OTA channel

On the source node, destination node, and every intermediate repeater, run:

```text
tempradio 909.950,250,5,5,120
```

All participating nodes must use the same frequency, bandwidth, and spreading factor. Their time windows must
overlap. Start with the farthest hop (the destination) and work back toward the source when using `tempradio`.

If you administer the destination over LoRa, the controller used to send later `ota` commands must also be
able to communicate on this temporary channel. For unattended nodes, use synchronized `tempradioat` entries
instead of manually starting the windows. Ensure the nodes' clocks are set before using `tempradioat`.

### 2. Serve the update from the computer

Close any serial terminal using the source node's USB port, find its device name, and start the server:

```bash
motatool serve --dir ./motas --serial /dev/ttyACM0 -v
```

Replace `/dev/ttyACM0` with the USB serial device of the source companion selected above.
`motatool` attaches the folder to the source, which advertises the update over LoRa while its temporary-radio
window is active. KISS modem serial ports cannot be used here.

Leave this command running until the destination finishes downloading.

### 3. Find and download the update

On the destination node, check its state and ask for nearby updates:

```text
ota status
ota ls
```

Discovery is asynchronous. Wait a few seconds and run `ota ls` again if the list is initially empty. Select
the entry marked `[yours]`: it should say `full` for the ESP32 path or `delta` for the nRF52 path. If it is
entry 1, run:

```text
ota pull 1 flash
```

Monitor the transfer:

```text
ota status
```

The update is ready when the status says `ready to install`. OTA is deliberately the lowest-priority mesh
traffic. At the temporary-radio settings in this guide, allow roughly **one hour** for a typical ESP32 full
image over a quiet, direct link. That is a planning estimate, not an upper bound: repeaters, retries, weak
links, and normal mesh traffic can extend it well past an hour. The 120-minute example window is
intentional. If necessary, start another overlapping `tempradio` window; the download resumes rather than
starting over.

### 4. Verify and install

Once the destination reports `ready to install`, run:

```text
ota install
```

The destination verifies the complete package again before approving it. ESP32 installs the full image into
its inactive A/B slot. nRF52 checks the base hash and bootloader capability, then reboots into OTAFIX; the
bootloader independently rechecks the package, applies the delta in place, and verifies the resulting image.
Pre-install failures leave the running firmware unchanged and report the reason. If power is lost after an
nRF52 in-place apply has begun, OTAFIX will not boot a partial image; it enters recovery DFU so a known-good
application can be restored.

After the node returns, reconnect on its normal radio channel and confirm:

```text
ota status
```

## Quick troubleshooting

- **Nothing appears in `ota ls`:** confirm that `motatool serve` is still running and every required node
  has an active `tempradio 909.950,250,5,5,120` window.
- **The CLI says LoRa OTA is not included:** that firmware does not contain the LoRa OTA feature.
  `tempradio` cannot enable it; install a supported `-ota-` repeater build over WiFi or USB first.
- **The update is marked `[other hw]`:** it is for a different board or firmware role. Do not install it.
- **An nRF52 node does not list a full update:** this is intentional. nRF52 accepts only an in-place delta.
- **nRF52 reports no bootloader apply support:** install the exact-board in-place-delta OTAFIX bootloader
  before trying LoRa OTA.
- **nRF52 reports a base mismatch:** the file passed to `--base` is not the exact application running on
  the destination. Rebuild the delta from the correct saved `firmware.hex`.
- **The download stalls:** check the source, destination, and intermediate repeaters. Restart matching,
  overlapping temporary-radio windows if one expired.
- **The serial port is busy:** close the serial terminal before starting `motatool serve`.
- **The destination rejects the package:** verify the source files, their `EndF` trailers, the package's
  target/hardware identity, and the result of `motatool verify`.

For additional commands and safety details, see [the full OTA user guide](ota_user_guide.md). For protocol
and container internals, see [the OTA protocol specification](ota_protocol.md).
