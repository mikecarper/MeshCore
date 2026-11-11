<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/ota_station_g2_to_ikoka_stick/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# LoRa OTA example: Station G2 on a Pi to an Ikoka Stick

For the automatic version using only the Pi's USB-connected source radio,
start with the [guided LoRa updater](ota_wizard.md).

Update the Ikoka Stick's bootloader to **OTAFIX 2.4.12**, then install
**1.18.0.1 Full Repeater** firmware. The Station G2 stays connected to the Pi
by USB and serves the files. Use the app's authenticated Repeater Admin console for the Ikoka
Stick, through your existing Companion.

| Board | Job |
| --- | --- |
| Raspberry Pi | Runs the file server |
| Station G2 Repeater | USB-connected LoRa seeder |
| Ikoka Stick 33 dBm Repeater | Receives and installs both updates |
| Companion | Keeps the app connected to the Ikoka Stick's admin console |

This example requires a **direct LoRa link** between the Station G2 and Ikoka
Stick. It keeps their existing main-channel settings during a four-hour
TempRadio window. The Companion stays on that same channel. Only the Station
G2 needs a USB connection to the Pi. For a route through intermediate
repeaters, every relay also needs an overlapping TempRadio window; see the
[scripted update guide](lora_ota_automation.md).

## 1. Check the Ikoka Stick

Run these in **Ikoka Stick — Repeater Admin** and save the replies:

```text
ver
get bootloader.ver
ota self
ota qspi
ota bootloader status
ota config
ota key list
get radio
get radio.rxps.config
```

The example starts with these reported values:

| Check | Required value for this example |
| --- | --- |
| Application hardware / target | `ikoka_stick_nrf_33dbm` / `E5331F02` |
| Installed bootloader | OTAFIX 2.4.11 |
| Bootloader identity | Board and target `28860044`, device `XIAO_DFU` |
| Bootloader support | `abi=3 caps=0E`, `QSPI apply OK`, `codecs=0x5` |
| Storage | `QSPI store:2048K`, working `ota qspi` probe |

These values support a remote bootloader upgrade. A stock bootloader or
missing QSPI apply support needs the matching local installation first.
The Stick uses its onboard flash; no additional storage is needed.

If `get radio.rxps.config` is unavailable, record `get radio.rxps` instead.
Keep each board's original RXPS and `ota config` replies for cleanup.

## 2. Install the Pi tools

On **Raspberry Pi — terminal**, install the host dependencies:

```bash
sudo apt update
sudo apt install -y build-essential pkg-config libudev-dev libssl-dev curl unzip pipx python3-venv
pipx ensurepath
pipx install 'meshcore-cli>=1.6.0'
```

Install stable [Rust](https://rustup.rs/) if it is not already installed:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/meshcore-rustup.sh
sh /tmp/meshcore-rustup.sh -y --profile minimal
```

Open a new terminal so the Pi finds `meshcli` and Cargo. Check that both Rust
and Cargo are **1.86 or newer**, then install the supported motatool revision:

```bash
rustc --version
cargo --version
cargo install --git https://github.com/mikecarper/motatool.git \
  --rev 5d9646cb08105245fb96c28450669c58763224bb --locked --bin motatool
meshcli -v
motatool --version
mkdir -p ~/ikoka-stick-ota/downloads ~/ikoka-stick-ota/inputs ~/ikoka-stick-ota/serve
cd ~/ikoka-stick-ota
```

## 3. Prepare the two update files

On **Raspberry Pi — terminal**, download the signed bootloader bundle:

```bash
BOOT_URL=https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/download/v0.11.0-OTAFIX2.4.12
curl -fL "$BOOT_URL/OTAFIX-2.4.12-bootloader-mota.zip" -o downloads/bootloader-2.4.12.zip
unzip -q downloads/bootloader-2.4.12.zip -d inputs/bootloader-2.4.12
cp inputs/bootloader-2.4.12/mota/update-xiao_nrf52840_ble_bootloader-v0.11.0-OTAFIX2.4.12.mota serve/
BOOT_MOTA=serve/update-xiao_nrf52840_ble_bootloader-v0.11.0-OTAFIX2.4.12.mota
OTAFIX_PUBLIC_KEY=272564CC588D3D122285A15E6E2566D2ABE7177BB7EA1D1E41B23B29F0F85D2D
BOOT_KEY_FILE=inputs/bootloader-2.4.12/OTAFIX_MOTA_SIGNING_PUBLIC_KEY.txt
test "$(tr -d '[:space:]' < "$BOOT_KEY_FILE")" = "$OTAFIX_PUBLIC_KEY"
motatool verify "$BOOT_MOTA" --pub "$BOOT_KEY_FILE"
motatool inspect "$BOOT_MOTA"
```

The bootloader package must be signed, format 3, for **XIAO nRF52840 BLE** /
`XIAO_DFU`, target `28860044`, version 2.4.12. The key comparison and package
verification must both pass before continuing.

Download the exact ordinary **Ikoka Stick 33 dBm Full Repeater** application
DFU ZIP on **Raspberry Pi — terminal**:

```bash
APP_URL=https://github.com/mikecarper/MeshCore/releases/download/lora-ota-2-v1.18.0.1-halo-keymind-cascade-dev-03214a16
curl -fL "$APP_URL/ikoka_stick_nrf_33dbm_repeater-full-ota-v1.18.0.1-halo-keymind-cascade-dev-03214a16.zip" \
  -o downloads/ikoka-stick-33dbm-repeater.zip
```

For a later release, use the [firmware picker](firmware_picker.md) to select
the same board, Repeater role, Full profile, and application DFU ZIP.

On **Raspberry Pi — terminal**, extract the application binary named by the
DFU manifest and check its firmware identity trailer:

```bash
python3 - <<'PY'
import hashlib, json, struct, zipfile
from pathlib import Path
with zipfile.ZipFile('downloads/ikoka-stick-33dbm-repeater.zip') as archive:
    app = json.loads(archive.read('manifest.json'))['manifest']['application']
    image = archive.read(app['bin_file'])
trailer = image[-56:]
assert len(trailer) == 56 and trailer[:4] == b'EndF', 'Missing EndF'
assert struct.unpack_from('<I', trailer, 4)[0] == len(image) - 56
assert hashlib.sha256(image[:-56]).digest()[:8] == trailer[8:16], 'Body hash mismatch'
assert struct.unpack_from('<I', trailer, 20)[0] == 0xE5331F02, 'Wrong application target'
assert trailer[24:].rstrip(b'\0') == b'ikoka_stick_nrf_33dbm', 'Wrong board'
Path('inputs/ikoka-stick-33dbm-repeater.bin').write_bytes(image)
print('Expected post-install base_hash:', trailer[8:16].hex().upper())
PY
```

Save the printed `base_hash`. Stop if extraction or any identity check fails.
Then make a full application package from that binary:

```bash
motatool build --fw inputs/ikoka-stick-33dbm-repeater.bin \
  --out serve/ikoka-stick-33dbm-repeater.mota
motatool verify serve/ikoka-stick-33dbm-repeater.mota
motatool inspect serve/ikoka-stick-33dbm-repeater.mota
```

Check application target `E5331F02`, hardware `ikoka_stick_nrf_33dbm`, and the
expected newer firmware version. Save its eight-character `merkle_root` as the application
manifest ID. The package's `image_hash` covers the complete image; the
post-install `base_hash` is the body hash printed above. A full QSPI
application update needs no copy of the old firmware.
This locally packaged application is unsigned and uses explicit manual
installation; the separate bootloader package remains signed.

<details id="differential-upgrade" markdown="1">
<summary>Optional: differential firmware upgrade (smaller download)</summary>

A differential update sends a patch from the Ikoka Stick's exact running
application to the new application. It can reduce LoRa transfer time. The
bootloader update in step 7 still uses the signed full bootloader package.

Prepare the new binary and full package above first. Use this option only
when you also have the **original application DFU ZIP for the firmware
currently running on the Ikoka Stick**. A matching version label or a rebuild
of the same commit is not enough. If that original file is unavailable, use
the full update above.

On **Ikoka Stick — Repeater Admin**, get its current identity:

```text
ver
ota self
```

Copy the original application DFU ZIP onto **Raspberry Pi** as
`downloads/ikoka-stick-33dbm-repeater-running.zip`. In the Pi terminal, paste
the 16-character `base_hash` from that fresh `ota self` reply when prompted:

```bash
read -r -p 'Current Ikoka Stick base_hash (16 hex characters): ' IKOKA_BASE_HASH
export IKOKA_BASE_HASH
python3 - <<'PY'
import hashlib, json, os, re, struct, zipfile
from pathlib import Path
expected = os.environ['IKOKA_BASE_HASH'].strip().upper()
assert re.fullmatch(r'[0-9A-F]{16}', expected), 'Invalid base_hash'
with zipfile.ZipFile('downloads/ikoka-stick-33dbm-repeater-running.zip') as archive:
    app = json.loads(archive.read('manifest.json'))['manifest']['application']
    image = archive.read(app['bin_file'])
trailer = image[-56:]
assert len(trailer) == 56 and trailer[:4] == b'EndF', 'Missing EndF'
assert struct.unpack_from('<I', trailer, 4)[0] == len(image) - 56
assert hashlib.sha256(image[:-56]).digest()[:8] == trailer[8:16], 'Body hash mismatch'
assert struct.unpack_from('<I', trailer, 20)[0] == 0xE5331F02, 'Wrong application target'
assert trailer[24:].rstrip(b'\0') == b'ikoka_stick_nrf_33dbm', 'Wrong board'
assert trailer[8:16].hex().upper() == expected, 'This is not the running firmware'
Path('inputs/ikoka-stick-33dbm-repeater-running.bin').write_bytes(image)
print('Exact running application confirmed:', expected)
PY
```

Stop if any check fails. Then build and check the **in-place** nRF52 delta:

```bash
motatool build \
  --base inputs/ikoka-stick-33dbm-repeater-running.bin \
  --fw inputs/ikoka-stick-33dbm-repeater.bin \
  --patch-type in-place \
  --out inputs/ikoka-stick-33dbm-repeater-delta.mota
motatool verify inputs/ikoka-stick-33dbm-repeater-delta.mota
motatool inspect inputs/ikoka-stick-33dbm-repeater-delta.mota
ls -lh serve/ikoka-stick-33dbm-repeater.mota inputs/ikoka-stick-33dbm-repeater-delta.mota
```

Check `codec_id` is `2 (detools-in-place)`, `base_hash` matches the live Ikoka
Stick, target is `E5331F02`, and the new version/hardware match the full
package. Let motatool derive the safe workspace from the firmware; do not
override its memory or target checks. Compare package sizes and keep the full
update if the delta is not smaller. `verify` checks the delta container's
integrity; it does not prove that you selected the running base image.

To serve the delta, replace the full application package in the serving
folder **before starting motatool**:

```bash
cp inputs/ikoka-stick-33dbm-repeater-delta.mota serve/ikoka-stick-33dbm-repeater.mota
```

Continue with steps 4–9. In step 8, choose the **application, delta** row and
use its own eight-character manifest ID with `ota pull <APP_MID8> flash`,
then `ota install` when ready. The target's `base_hash` must still match
immediately before that pull. Updating only the bootloader in step 7 leaves
the running application unchanged, so it does not invalidate the delta base.
After installation, check the same new firmware version and expected
post-install body hash printed earlier in step 3.

</details>

## 4. Identify the Station G2's USB port

On **Raspberry Pi — terminal**:

```bash
ls -l /dev/serial/by-id/
```

Select the Station G2's actual entry. Set `G2_PORT` to that complete path;
`/dev/ttyUSB0` below is only an example:

```bash
G2_PORT=/dev/ttyUSB0
meshcli -r -s "$G2_PORT" -b 115200 "board"
meshcli -r -s "$G2_PORT" -b 115200 "ver"
meshcli -r -s "$G2_PORT" -b 115200 "ota status"
meshcli -r -s "$G2_PORT" -b 115200 "get radio"
meshcli -r -s "$G2_PORT" -b 115200 "get radio.rxps.config"
meshcli -r -s "$G2_PORT" -b 115200 "ota config"
```

Confirm that this is the Station G2 and that its firmware includes LoRa OTA.
Save its radio, RXPS, and OTA settings. If access is denied, add your Pi login
to `dialout` with `sudo usermod -aG dialout "$(id -un)"`, then log out and in.
Close other programs using that USB port.

## 5. Open the OTA windows and trust the bootloader signer

The commands below assume the Station G2, Ikoka Stick, and Companion already
use **910.525 MHz, BW 62.5 kHz, SF7, CR5**. If yours differ, use their current
shared tuple in every TempRadio command below. Preserve an explicit preamble
if one is configured by adding it after the duration. This keeps admin access
on the existing channel; TempRadio is still required to enable LoRa OTA.

On **Ikoka Stick — Repeater Admin**:

```text
ota key add 272564CC588D3D122285A15E6E2566D2ABE7177BB7EA1D1E41B23B29F0F85D2D
ota key list
ota config autofetch off
ota config autoinstall off
ota config hops 0
set radio.rxps off
get radio.rxps
tempradio 910.525,62.5,7,5,240
get tempradio
```

On **Raspberry Pi — Station G2 USB CLI**:

```bash
meshcli -r -s "$G2_PORT" -b 115200 "ota config autofetch off"
meshcli -r -s "$G2_PORT" -b 115200 "ota config autoinstall off"
meshcli -r -s "$G2_PORT" -b 115200 "ota config hops 0"
meshcli -r -s "$G2_PORT" -b 115200 "set radio.rxps off"
meshcli -r -s "$G2_PORT" -b 115200 "get radio.rxps"
meshcli -r -s "$G2_PORT" -b 115200 "tempradio 910.525,62.5,7,5,240"
meshcli -r -s "$G2_PORT" -b 115200 "get tempradio"
```

Both receivers must report RXPS off and an active temporary tuple. Wait if
`get tempradio` says `pending`; do not start serving while it says `off`.
The `hops 0` setting is for this direct link. These configuration changes are
saved; the TempRadio window is temporary.

## 6. Start the Station G2 seeder

On **Raspberry Pi — terminal**, leave this running in the foreground:

```bash
motatool serve --dir ./serve --serial "$G2_PORT" --baud 115200 -v
```

It attaches the folder and the Station G2 advertises the two packages. Wait
for a successful folder attachment/catalog count. While motatool owns USB,
send later commands through **Ikoka Stick — Repeater Admin**. Keep the Pi and
both repeaters powered through each transfer and installation.

## 7. Update the Ikoka Stick bootloader

On **Ikoka Stick — Repeater Admin**:

```text
ota ls
```

Discovery is asynchronous. Wait a few seconds and repeat it. Select the
**bootloader** row for XIAO target `28860044`, version 2.4.12. Copy its
eight-character manifest ID into `BOOT_MID8` below; placeholders must be
replaced with the actual values, without angle brackets:

```text
ota pull <BOOT_MID8> flash
ota status
```

Check progress until it reports **ready to install**, then run:

```text
ota bootloader status
```

Confirm `staged:ready`. Copy that reply's exact `mid` and 16-character `hash`:

```text
ota bootloader install <BOOT_MID8> <BOOT_HASH16>
```

Wait for the reboot, reconnect in the app, and check:

```text
get bootloader.ver
ota bootloader status
ota status
ver
```

Expect **OTAFIX 2.4.12**, the same `XIAO_DFU` identity, ABI 3 / capabilities
`0E`, and `blup:C8`. The application version should still be the old version.

## 8. Update the Ikoka Stick firmware

The bootloader reboot cleared the Ikoka Stick's temporary window. On
**Ikoka Stick — Repeater Admin**, reopen it before downloading firmware:

```text
get radio.rxps
tempradio 910.525,62.5,7,5,240
get tempradio
```

Wait until `get tempradio` shows the active tuple, repeating the query if it
says `pending`. Then run:

```text
ota ls
```

Keep the Station G2 seeder running. Select the **application, full** row with
target `E5331F02` and the version inspected in step 3. Use its stable manifest
ID, not its list position or the bootloader ID:

```text
ota pull <APP_MID8> flash
ota status
```

Repeat `ota status` until **ready to install**, then run:

```text
ota install
```

After the application reboots, reconnect and check:

```text
ver
get bootloader.ver
ota self
ota status
```

Confirm the selected firmware version, bootloader **2.4.12**, working QSPI
apply support, and the `base_hash` recorded from the application package.
The Ikoka Stick has returned to its saved main radio settings.

## 9. Stop serving and restore settings

On **Raspberry Pi — terminal**, press **Ctrl+C** and wait for motatool to
detach the folder and return to the shell. Then run:

```bash
meshcli -r -s "$G2_PORT" -b 115200 "ota folder"
meshcli -r -s "$G2_PORT" -b 115200 "normalradio"
meshcli -r -s "$G2_PORT" -b 115200 "get tempradio"
```

Confirm the host folder detached. Restore each board's original RXPS, hop
limit, autofetch, and autoinstall
settings from steps 1 and 4. For the example starting values, on **Ikoka
Stick — Repeater Admin**:

```text
set radio.rxps level 8 preamble 16
ota config hops 3
ota config autofetch off
ota config autoinstall off
get radio.rxps.config
ota config
get tempradio
```

Use the Station G2's own recorded values through `meshcli -r` as in step 5.
An original RXPS value of `off` stays off; for an automatic preamble (`0`),
restore with `set radio.rxps level N`; fixed manual timings use
`set radio.rxps RX_US SLEEP_US`. Retain the official signing key for future
bootloader updates. Both boards should now report `get tempradio` as `off`.

## If a transfer stops

- **No update rows:** check the seeder attachment, matching radio settings,
  direct reachability, and active TempRadio windows on both boards.
- **Four-hour window expired:** stop motatool before reopening the G2 USB
  CLI, renew both windows, restart the same seeder, and pull the same MID.
  Keep the same package; current firmware can resume committed blocks.
- **Signature/key error:** verify the signed package against its trusted
  publisher key. Do not substitute an arbitrary key from an update row.
- **Download failed or wrong selection:** inspect `ota status`; use
  `ota cancel` to discard that download before selecting another package.
- **No completed install:** keep the running firmware until the destination
  reports a verified, ready package. See the
  [bootloader guide](ota_nrf52_bootloader_update.md) or
  [firmware guide](ota_user_guide.md) for the reported error.
