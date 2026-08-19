# Easy firmware updates over LoRa

This guide shows the shortest manual path for sending firmware from a computer to a MeshCore node over
LoRa. Choose the package type for the **destination** node:

For an end-to-end controller that accepts a release ZIP or ready mOTA, see
[Scripted LoRa OTA from start to finish](lora_ota_automation.md).

| Destination | Update type | Files needed to build the `.mota` | Installer |
| --- | --- | --- | --- |
| ESP32 | Full firmware | New non-merged application `.bin` | ESP32 A/B firmware slots |
| nRF52 | In-place delta | Exact running `firmware.hex` and new `firmware.hex` | Exact-board OTAFIX bootloader |
| MeshTower V2 SD target | Full firmware or in-place delta | New `firmware.hex`; a delta also needs the exact running `firmware.hex` | Matching SD-aware OTAFIX bootloader |

A normal nRF52 target cannot install a full-image container. It deliberately accepts only an in-place
delta built against its exact running firmware. The MeshTower V2 microSD target is the exception because
it stages the complete container off-chip; see [MeshTower V2 microSD LoRa OTA](ota_meshtower_v2_sdcard.md).

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

- An OTA-enabled build whose artifact filename contains `-ota-` on the destination. The `-ota-` stamp confirms
  that the node can discover, download, verify, and install LoRa OTA. Intermediate repeaters do **not** need an
  OTA-enabled build: current repeater firmware relays OTA packets opaquely without storing or installing them.
  Standard logging and untagged builds cannot install LoRa OTA; FULL MQTT and FULL logging OTA builds can.
- An OTA-enabled MeshCore source connected to the computer by USB serial, or
  an ESP32 WiFi companion/FULL source connected over WiFi as described below.
- Overlapping `tempradio` windows on the source, destination, and every repeater needed between them.

LoRa OTA packets are generated, consumed, and relayed only while `tempradio` is active. Intermediate repeaters
apply their normal forwarding filters, duplicate checks, and flood limits; they do not interpret the OTA
payload. If any required window closes, the transfer stops making progress and can resume during a later
overlapping window.

`build.sh` provides a `*_repeater_lora_ota_no_external_sensors` build for every standalone ESP32 and nRF52
repeater target. The normal repeater build keeps its external-sensor support and can serve as an intermediate
OTA relay, but it cannot download or install an update for itself. The `-ota-` sibling omits optional external
I2C environmental sensors to preserve the update workspace, while retaining board-native features such as its
display, buttons, battery monitoring, and integrated GPS. ESP32 `-ota-` siblings also retain the lightweight
browser WiFi uploader (`start ota`), the complete CLI, and a 254-entry neighbor table. RP2040 and STM32 repeaters do not
currently have a safe self-apply path, but current repeater firmware can still relay OTA packets opaquely
during TempRadio.

nRF52 `-ota-` siblings are compiled with size optimization instead of the Adafruit platform's default
speed optimization. This prevents the retained software Ed25519 fallback from expanding beyond the fixed
in-place workspace; CC310 hardware crypto, hardware RNG mixing, telemetry history, and board-native features
remain enabled.

ESP32 `*-full-ota-*` artifacts retain all compiled features and enable LoRa OTA for every FULL role,
including room servers, sensors, observers, and bridges. A FULL image requires its expanded partition table:
install the matching merged image over USB once before installing later non-merged FULL updates over LoRa.
ESP32 MQTT observers and ESP-NOW bridges are always emitted as FULL artifacts; compact-CLI variants are no
longer built.
The `*-full-ota-*` profile uses MQTT with logging off. Use a `*-full-logging-ota-*` artifact when USB debug
and packet logging are needed instead; that diagnostic profile explicitly disables MQTT and can produce
substantial serial output.

### Choose the source radio

Use an OTA-enabled MeshCore node as the source. It receives the update folder
from the computer, then advertises it over LoRa. ESP32 USB/WiFi companions and
FULL ESP32 roles include the required transport. A
`*_companion_radio_full` target keeps only the source half of LoRa OTA: it
serves host images but cannot stage or install one for itself. ESP32 full
combines USB, BLE, and WiFi; nRF52 full combines USB and BLE because nRF52840
has no WiFi.
A small set of high-capacity classic ESP32 companions
keep their normal image and provide a separate `-full-ota-` image with 100 contacts, 8 group channels, and
a 16-frame offline queue. Install that variant's merged image over USB once before using it. Connect the
source by USB serial or, when supported, by WiFi. For an ordinary raw-text USB
source, confirm that its USB CLI accepts:

```text
ota folder on
```

If an older build reports that `OTA_FOLDER_SERIAL` is not compiled in, install a current `-ota-` or
`-full-ota-` build first. Do **not** use a KISS modem: KISS firmware is a TNC/KISS frame interface
and does not provide the MeshCore CLI or the OTA-folder transport that `motatool serve` requires.

An nRF52 `companion_radio_full` starts in USB Binary mode. Use
`+++MESHCORE-TERM-START` for local TempRadio commands, then return with
`+++MESHCORE-TERM-STOP`. When `motatool serve --serial` opens the port, its
automatic `ota folder on` command selects exclusive mOTA mode; stopping the
tool or disconnecting resets USB to Binary. BLE remains available throughout.

For an ESP32 WiFi companion or FULL ESP32 source with active WiFi, use its dedicated OTA seeder:

```bash
motatool serve --dir ./motas --tcp <source-host>:5001 -v
```

Port `5001` is separate from the companion application port (`5000`) and the
HTTP configuration/browser-OTA port (`80`, depending on the role). An ESP32
`companion_radio_full` also has a local OTA/TempRadio console on port `5002`;
see the [full Companion guide](./companion_radio_full.md). On a FULL repeater or room
server, `start webconfig` can bring up the saved WiFi connection. Other FULL
roles with browser OTA support can raise `MeshCore-OTA` with `start ota` and
use `192.168.4.1:5001`. The TCP seeder auto-attaches; do not also run
`ota folder on` for USB serial.

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
get bootloader.ver
ota self
```

The first command identifies the installed nRF52 bootloader. Continue only if
the `ota self` reply includes:

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
omits optional external environmental sensor packages so the delta fits the safe in-place workspace.

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

Discovery is asynchronous. `ota ls` says `refreshing`; wait a few seconds and run it again even if it first
shows an older row. Select `[same target]`: it should say `full` for the ESP32 path or `delta` for the nRF52
path. Do not select `[unsupported]` (for example, a source's self-served full image on a single-slot nRF52).
Use the row's stable eight-hex manifest ID rather than its changing list position:

```text
ota pull 838B8169 flash
```

If an internal-flash nRF52 reports `no EndF`, only a row marked `[rescue]` is eligible. Current rescue-capable
firmware requires `ota pull <mid8> flash rescue`, followed after completion by
`ota rescue install <base_hash16>`. Older running firmware without those commands must be recovered over USB.

Monitor the transfer:

```text
ota status
```

The update is ready when the status says `ready to install`. Discovery is background traffic, while an
active OTA download is primary mesh traffic. At the temporary-radio settings in this guide, allow roughly
**one hour** for a typical ESP32 full image over a quiet, direct link. That is a planning estimate, not an
upper bound: repeaters, retries, weak
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
  If it is the source or destination, install a supported `-ota-` build over WiFi or USB first. An intermediate
  repeater does not need the OTA CLI and can relay opaquely while its matching `tempradio` window is active.
- **The update shows another environment or a raw `[hw XXXXXXXX]`:** it is for a different board or firmware
  role. Do not install it.
- **An internal-flash nRF52 marks a full update `[unsupported]`:** it can install only an in-place delta.
  The MeshTower V2 microSD target accepts full images with its matching SD-aware bootloader.
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
