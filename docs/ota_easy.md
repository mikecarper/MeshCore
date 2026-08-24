# Easy firmware updates over LoRa

This guide shows the shortest manual path for sending firmware from a computer to a MeshCore node over
LoRa. Choose the package type for the **destination** node:

For an end-to-end controller that accepts a release ZIP or ready mOTA, see
[Scripted LoRa OTA from start to finish](lora_ota_automation.md).

| Destination | Update type | Files needed to build the `.mota` | Installer |
| --- | --- | --- | --- |
| ESP32 | Full firmware | New non-merged application `.bin` | ESP32 A/B firmware slots |
| nRF52, internal staging | In-place delta | Exact running `firmware.hex` and new `firmware.hex` | Exact-board OTAFIX bootloader |
| Supported nRF52 QSPI repeater | Full firmware or in-place delta | New `firmware.hex`; a delta also needs the exact running `firmware.hex` | Matching QSPI-aware OTAFIX bootloader |
| MeshTower V2 SD target | Full firmware or in-place delta | New `firmware.hex`; a delta also needs the exact running `firmware.hex` | Matching SD-aware OTAFIX bootloader |

An internal-staging nRF52 target accepts only an in-place delta built against its exact running firmware.
Matched QSPI repeater targets and the MeshTower V2 microSD target can also install a full image because the
complete container stays off-chip. See [nRF52 repeater OTA with external QSPI](ota_nrf52_qspi.md) and
[MeshTower V2 microSD LoRa OTA](ota_meshtower_v2_sdcard.md).

MeshTower V2 microSD application and bootloader containers must be Ed25519-signed by a key in the device
allowlist; pass `--sign-key` when building one. Its BLM2-capable SD-aware bootloader is also required before
ordinary application or bootloader OTA can use the reset-retained authorization record. Upgrade preview.12
locally over USB/BLE DFU or SWD first; there is no raw-card compatibility handoff.

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
reboots. Current full-parser firmware also accepts `normalradio`, which cancels the temporary window and
restores the saved tuple after replying on the current channel. This frequency is intended for North American
configurations. Confirm that it is permitted in your location and change it when necessary.

## Before you start

Both paths require:

- A destination artifact explicitly identified by its release table as install-capable. Some lean internal
  nRF52 builds carry `lora_ota_no_external_sensors` in the filename, while matched QSPI boards enable install
  support in the normal full-sensor repeater artifact, so filename text alone is not authoritative. Confirm
  support with `ota self` and `ota status`. Intermediate repeaters do **not** need an install-capable build:
  current repeater firmware relays OTA packets opaquely without storing or installing them.
- An OTA-enabled MeshCore source connected to the computer by USB serial, or
  an ESP32 WiFi companion/FULL source connected over WiFi as described below.
- Overlapping `tempradio` windows on the source, destination, and every repeater needed between them.

LoRa OTA packets are generated, consumed, and relayed only while `tempradio` is active. Intermediate repeaters
apply their normal forwarding filters, duplicate checks, and flood limits; they do not interpret the OTA
payload. If any required window closes, the transfer stops making progress and can resume during a later
overlapping window.

`build.sh` provides a `*_repeater_lora_ota_no_external_sensors` build for standalone ESP32 and nRF52 repeater
targets that need a smaller internal update workspace. Those siblings omit optional external I2C
environmental sensors while retaining board-native features such as displays, buttons, battery monitoring,
and GPS where the target uses the GPS-preserving lean profile. The RAK3401 OTA
repeater retains RAK12501 support in sensor slot A; slot D conflicts with the
RAK13302 radio's BUSY/DIO1 lines. The RAK4631 OTA repeater retains GPS except
for its Serial1 RS232 bridge, which uses the same UART. Selected nRF52 boards with matched external
QSPI application and bootloader support can instead make the normal full-sensor
repeater install-capable; those targets do not need to reserve internal flash
for the downloaded container. SolarXiao 30S and 33S use this matched external-QSPI
path and therefore do not emit redundant no-external-sensors siblings. Other
normal repeaters can still serve as intermediate relays but cannot necessarily
install an update themselves. ESP32
`-ota-` siblings also retain the lightweight browser WiFi uploader (`start
ota`), the complete CLI, and a 254-entry neighbor table. RP2040 and STM32
repeaters do not currently have a safe self-apply path, but current repeater
firmware can still relay OTA packets opaquely during TempRadio.

nRF52 `-ota-` siblings are compiled with size optimization instead of the Adafruit platform's default
speed optimization. This prevents the retained software Ed25519 fallback from expanding beyond the fixed
in-place workspace; CC310 hardware crypto, hardware RNG mixing, telemetry history, and board-native features
remain enabled.

ESP32 `*-full-usb-wifi-ota-*` artifacts retain all compiled features and enable LoRa OTA for every FULL role,
including room servers, sensors, observers, and bridges. A FULL image requires its expanded partition table:
install the matching merged image over USB once before installing later non-merged FULL updates over LoRa.
ESP32 MQTT observers and ESP-NOW bridges are always emitted as FULL artifacts; compact-CLI variants are no
longer built.
The `*-full-usb-wifi-ota-*` profile compiles USB packet logging and direct WiFi MQTT into one image. Use
`set logging.output off|usb|wifi|both` to persist the desired path. A `*-full-logging-ota-*` artifact is now
only a fallback for a hardware/role combination without a WiFi MQTT target and can produce substantial
serial output.

### Choose the source radio

Use an OTA-enabled MeshCore node as the source. It receives the update folder
from the computer, then advertises it over LoRa. ESP32 USB/WiFi companions and
FULL ESP32 roles include the required transport. A
`*_companion_radio_full` target keeps only the source half of LoRa OTA: it
serves host images but cannot stage or install one for itself. ESP32 full
combines USB, BLE, and WiFi; nRF52 full combines USB and BLE because nRF52840
has no WiFi.
A small set of high-capacity, non-PSRAM classic ESP32 companions
keep their normal image and provide a separate `-full-logging-ota-` fallback with 100 contacts, 8 group
channels, a 16-frame offline queue, and persistent USB output selection. Install that variant's merged
image over USB once before using it. Connect the
source by USB serial or, when supported, by WiFi. For an ordinary raw-text USB
source, confirm that its USB CLI accepts:

```text
ota folder on
```

If an older build reports that `OTA_FOLDER_SERIAL` is not compiled in, install a current `-ota-`,
`-full-usb-wifi-ota-`, or applicable `-full-logging-ota-` build first. Do **not** use a KISS modem: KISS firmware is a TNC/KISS frame interface
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
`companion_radio_full` also has its complete role-specific text terminal on
port `5002`; OTA automation uses its `ota` and TempRadio subset. See the
[full Companion guide](./companion_radio_full.md). On a FULL repeater or room
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

## nRF52: package a full image or in-place delta

### 1. Install and check the OTAFIX bootloader

This is a one-time prerequisite. Install the OTAFIX bootloader built for the destination's **exact board**
from the
[OTAFIX nRF52 bootloader releases](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases).
Internal-staging delta updates require OTAFIX 2.4 or newer. External QSPI and SD staging require OTAFIX
2.4.1 or newer and release notes that explicitly list the exact board and storage mode. Follow the release's
board-specific installation and erase instructions. If it does not contain the destination's exact board,
this LoRa install path is not yet available for that board; never substitute a similar board's bootloader.

Before preparing or downloading a LoRa update, run this on the destination:

```text
get bootloader.ver
ota self
```

The first command identifies the installed nRF52 bootloader. Continue only if
the `ota self` reply includes the apply mode required by this target:

```text
bootloader: apply OK
bootloader: QSPI apply OK
bootloader: SD apply OK
```

The reply also contains the running firmware's `base_hash`. Save it for the package check below. A stock,
legacy, or older OTAFIX bootloader without `.mota` in-place-apply support will report that apply support is
missing, and `ota install` will refuse to reboot into it.

### 2. Choose full or delta packaging

For a QSPI- or SD-backed target, a full update needs only the new raw
`firmware.hex`:

```bash
mkdir -p ./motas
motatool build --fw ./LilyGo_T-Echo_repeater-new.hex --out-dir ./motas
motatool verify ./motas/*.mota
```

Use a full package when an exact base image is unavailable. Use the delta path
below when reducing airtime is worth retaining the exact running image.

### 3. Keep the exact current and new application images for a delta

You need the raw `.pio/build/<environment>/firmware.hex` from the build that is **actually running**, plus
the corresponding `firmware.hex` from the new build. Save the current file before building the new version,
because PlatformIO reuses that path. For example:

```bash
# Save this immediately after building/flashing the version now running on the node.
cp .pio/build/Heltec_t114_repeater_lora_ota_no_external_sensors/firmware.hex \
  ./Heltec_t114_repeater_lora_ota_no_external_sensors-running.hex

# After checking out and building the new version, save its image separately.
cp .pio/build/Heltec_t114_repeater_lora_ota_no_external_sensors/firmware.hex \
  ./Heltec_t114_repeater_lora_ota_no_external_sensors-new.hex
```

Replace `Heltec_t114_repeater_lora_ota_no_external_sensors` with the destination's exact PlatformIO
environment. The two images must be
for the same board and role, and both must contain their `EndF` trailers. Do not pass a release `.uf2` or
BLE-DFU `.zip` to `motatool`; those are installation containers rather than raw application images.

Keeping a file with the same version label is not enough: the base must be byte-for-byte identical to the
running application. The hash check in the next step proves that it is the right file.

On RAK4631 repeaters, use the
`RAK_4631_repeater_lora_ota_no_external_sensors` environment. It retains built-in battery monitoring but
omits optional external environmental sensor packages so the delta fits the safe in-place workspace.
If the device has a RAK15001 installed in sensor slot C and the matching
RAK15001 OTAFIX bootloader, use
`RAK_4631_repeater_rak15001_slot_c_lora_ota` instead. That target retains the
full sensor/GPS set and can install either a full image or a delta from the
external 2 MiB store.

### 4. Build and check the in-place delta

```bash
mkdir -p ./motas
motatool build \
  --base ./Heltec_t114_repeater_lora_ota_no_external_sensors-running.hex \
  --fw ./Heltec_t114_repeater_lora_ota_no_external_sensors-new.hex \
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

Current layout-aware `motatool` derives the safe workspace from the new firmware's authenticated EndF layout
record, so the normal recipe deliberately omits `--inplace-memory`. If older tooling requires an explicit
override, use `0xC6000` only for matched external SD/QSPI staging and `0x98000` for an internal-staging nRF52
such as the RAK4631 lean OTA build. Do not substitute the external value on an internal target, and do not
override either value unless you have verified the exact app base, bootloader, and package geometry. Do not
continue if verification or any identity check fails.

## Transfer and install either package

### 1. Start the temporary OTA channel

First save the current `ota config` output. For a direct source-to-destination link, set both OTA-enabled
endpoints to direct-only mode before starting the transfer:

```text
ota config hops 0
```

Do not use `hops 1` as extra margin on a direct link. It tells a receiving repeater to retransmit each direct
DATA/PROOF packet; that unnecessary half-duplex transmission can make it miss the source's next fragment,
especially with older single-block receivers. Use `hops 1` only when one real intermediate relay is required,
`hops 2` for two, and so on. This setting is saved, so restore the original value after the maintenance window.

On the source node, destination node, and every intermediate repeater, then run:

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
shows an older row. Select `[same target]`: it can say `full` for ESP32 and external SD/QSPI nRF52 targets,
or `delta` for any supported nRF52 target. Do not select `[unsupported]` (for example, a source's self-served
full image on an internal-staging nRF52).
Rows labelled `bootloader` are also outside this ordinary application flow:
they are never automatic and `ota install` refuses them. Only an
already-capable, allowlisted internal-flash, XIAO-QSPI, or exact MeshTower V2
microSD nRF52 target can use the separate
[explicit bootloader workflow](ota_nrf52_bootloader_update.md).
Use the row's stable eight-hex manifest ID rather than its changing list position:

```text
ota pull 838B8169 flash
```

If a legacy internal-flash nRF52 reports `no EndF`, only a row marked `[rescue]` is eligible. Current rescue-capable
firmware requires `ota pull <mid8> flash rescue`, followed after completion by
`ota rescue install <base_hash16>`. Older running firmware without those commands must be recovered over USB.
Shared-internal bootloader-update builds are stricter: without a valid live
`EndF`, every internal pull is refused before erase because the normal
application may extend through `0xED000`. Recover those builds over USB/BLE DFU
or SWD instead of relying on the older 608 KiB rescue estimate.

Monitor the transfer:

```text
ota status
```

For a powered bench update where restart-resume is not needed, `ota config checkpoint 0` on the destination
removes periodic progress writes. It is a smaller optimization than selecting the correct hop count and it
trades away persisted mid-download resume; restore the previous checkpoint cadence afterward.

Current repeater firmware automatically uses the full transmit budget during a bounded TempRadio window.
Older receivers do not: if `get af` reports a nonzero value on such a node, record it, use `set af 0` for the
maintenance window, and restore it afterward. This affects how promptly the legacy receiver can send its next
block/proof request; it does not increase LoRa transmit power. Use a full duty budget only where the selected
frequency and local rules permit it.

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
its inactive A/B slot. nRF52 checks the bootloader and storage capabilities and, for a delta, the base hash,
then reboots into OTAFIX. The bootloader independently rechecks the package, installs the external full image
or applies the delta in place, and verifies the resulting image.
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
  A matched QSPI repeater or MeshTower V2 microSD target accepts full images with its corresponding bootloader.
- **A QSPI nRF52 reports `QSPI store:ERR 0K` or `bl:NO-QSPI`:** do not download an install package. Install
  the exact QSPI-aware bootloader and check that the selected application matches the board's flash wiring.
- **nRF52 reports no bootloader apply support:** install the exact-board in-place-delta OTAFIX bootloader
  before trying LoRa OTA.
- **nRF52 reports a base mismatch:** the file passed to `--base` is not the exact application running on
  the destination. Rebuild the delta from the correct saved `firmware.hex`.
- **The download stalls:** check the source, destination, and intermediate repeaters. Restart matching,
  overlapping temporary-radio windows if one expired. On a direct link, also require `ota config hops 0`;
  a larger reach makes a repeater destination unnecessarily echo source packets.
- **The serial port is busy:** close the serial terminal before starting `motatool serve`.
- **The destination rejects the package:** verify the source files, their `EndF` trailers, the package's
  target/hardware identity, and the result of `motatool verify`.

For additional commands and safety details, see [the full OTA user guide](ota_user_guide.md). For protocol
and container internals, see [the OTA protocol specification](ota_protocol.md).
