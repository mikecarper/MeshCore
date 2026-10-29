<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/ota_wizard/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Guided LoRa updates

Run one Python command to update a directly reachable Repeater or Room Server.
The wizard identifies the board, chooses compatible firmware, prepares the
transfer, installs it, checks the result, and restores the original radio,
RX power-saving, and OTA settings. On supported nRF52 boards it also checks
for a newer compatible signed bootloader and includes that update in the plan.

## Connect a source radio

Use either connection:

| Computer connection | Source radio | Destination |
| --- | --- | --- |
| Raspberry Pi → USB | ESP32 Repeater with OTA and `ota.peer` support | Repeater or Room Server |
| Computer or Pi → USB | Full Companion with USB OTA source and direct admin support | Repeater or Room Server |

A **Station G2 Repeater connected to a Pi** can manage and serve an update to
an **Ikoka Stick Repeater** through that single USB connection. It does not
need a second local radio or the phone app. Install a source firmware build
containing `ota.peer` first; older Repeater firmware cannot manage the remote
node through USB. Check with `ota.peer status` in its USB console.

For slow replies during manual USB administration, `ota.peer timeout 180`
raises the source's request timeout from 30 to 180 seconds. This RAM-only setting
survives closing the admin session and resets on reboot; keep the controller's
timeout longer than the source's.

The Full Companion source also needs a current build containing the wizard's
version 2 direct admin support. An older Full Companion may serve OTA files but cannot
provide this guarded admin connection; the wizard will ask for a source
firmware upgrade instead of proceeding with that connection.
Check `get ota.direct` from its USB console for `v=2`. Update replies use a
separate mailbox; the wizard does not consume ordinary queued messages or change
saved contact routes. Some Full Companion boards borrow queue space for OTA and
retain up to 128 unread messages during an update. If more are waiting, sync them
with an app first; the wizard refuses to discard them to make room.

The source and destination must already use the same main-channel settings
and hear each other directly. The destination needs working LoRa OTA support
and either its admin password or an admin ACL entry for the source radio.
A stock nRF52 bootloader may need a matching local
installation before it can accept remote updates.

By default, the wizard sends discovery, admin commands, and OTA traffic directly with no
relay hops. An older destination may put its first login reply in a flood
envelope; the wizard accepts it only if it arrives with zero hops, establishes
a direct return route, then requires a fresh direct identity check.

Close other apps using the USB port before starting.

### Use a known repeater key

With a USB repeater source, `--target-type repeater` lets you authenticate a
known destination without waiting for a discovery response. Supply its complete
64-character public key with `--target`. The source must reply
`OTApeer known_repeater=1` to `ota.peer capabilities`; update its firmware first if that
capability is missing.

For example, replace `PUBLIC_KEY` with the destination repeater's full key:

```bash
python3 tools/lora_ota/ota_wizard.py --mode repeater --port /dev/ttyACM0 \
  --target PUBLIC_KEY --target-type repeater --check
```

The source still sends a zero-hop login and requires an authenticated admin
reply and proof of the exact public key before reading settings or updating.
It does not fall back to flooding or relays. Automatic discovery and Room
Server connections keep their existing behavior. Use the same target-type
option when recovering an interrupted update through this connection.

If the destination already grants the source admin permission through its ACL,
set `MESHCORE_ADMIN_PASSWORD` to an empty string for an unattended login. In Bash:

```bash
export MESHCORE_ADMIN_PASSWORD=''
```

## Start

From the MeshCore checkout, run:

```bash
python3 tools/lora_ota/ota_wizard.py
```

On Windows, use `py` in place of `python3`. No command-line options are needed.
The menu provides these actions:

1. **Update a nearby radio** — the default.
2. **Check radios / save a preparation request** — inspect without installing.
3. **Prepare an update bundle on this computer** — no radios needed.
4. **Recover an interrupted update** — restore saved settings.
5. **Settings** — change transfer, bootloader, connection, or build preferences.
6. **Exit**.

The first live connection installs USB and verification dependencies in a
private Python environment if needed. On Raspberry Pi OS, install
`python3-venv` if Python reports that it is missing.
Preparing a signed input file can also install signature-verification support
on first use, without installing the USB software.

For an update, the wizard selects the USB radio and destination automatically
when there is only one of each. Otherwise, choose from the displayed lists.
Enter the destination admin password once, choose firmware, then review the
plan and press Enter to start. Password entry is hidden and passwords are
never saved. After an action, the menu is shown again.

## Choose firmware from GitHub releases

The firmware menu offers:

- **Latest compatible GitHub release (recommended):** downloads the published
  stable file for the detected board and role.
- **Choose another GitHub release:** lists compatible releases, including
  clearly labelled previews, so you can choose a version.
- **Local firmware file or prepared bundle:** accepts an application DFU ZIP,
  BIN, HEX, full mOTA, or a bundle prepared on another computer.
- **Build custom Full firmware from source:** offered on a computer with the
  firmware build tools and Bash. It is not offered by default on a Pi.

You do not need to copy release URLs or manually download published files.
The wizard checks the board and role, searches release files for an exact
running base when preparing a differential update, and checks for a compatible
signed bootloader on supported nRF52 destinations. Firmware that is already
installed or older is left in place.

The final plan shows the package sizes, firmware version, and any bootloader
or trusted update signer being installed. After reboot, the wizard checks the
running firmware hash and version, or the bootloader identity and version,
before reporting success.

## Differential or full updates

**Automatic** is the default transfer setting. It uses a differential package
when a compatible builder and the **exact running firmware** are available
and the package is smaller. A file with the same version label but a different
body hash is not a usable base. Published bases are found through GitHub
releases; a local base can be selected when the running image was custom.
Unsigned full `.mota` files can also supply the new image for a differential
update. Signed packages retain their signatures; prepared bundles retain their
chosen transfer format.

On a computer, the differential builder can be prepared automatically with a
compatible Rust toolchain. On a Pi without a builder, a full update is made by
Python without compiling. The destination must support full updates, such as
an Ikoka Stick with working QSPI storage.

To force a full application transfer, open **Settings**, choose **Full** for
the transfer method, then start the update. Settings also lets you turn the
bootloader check off for an application-only update, choose a USB port or
source type, or set a builder path, custom source folder/version, or cache folder.

An internal-flash nRF52 destination that requires differential installation
cannot use the full fallback. Use the computer-to-Pi workflow below.

## Prepare on a computer, send from a Pi

All steps use the same menu; no command-line options are needed.

1. On the Pi, choose **Check radios / save a preparation request**. Connect to
   the source and select the destination as prompted. Copy the JSON file
   shown after **Preparation request:** to the computer. It contains public
   board information and the exact running hash, without the password.
2. On the computer, choose **Prepare an update bundle on this computer**.
   Select the saved request or enter its file path. Choose firmware from
   GitHub releases, or select a local custom image. The wizard searches for
   the exact base and offers a local base file if needed. Accept the proposed
   output filename or choose another one.
3. Copy the resulting `.ota.zip` file to the Pi. Choose **Update a nearby
   radio**, then **Local firmware file or prepared bundle**, and select that
   ZIP.

The preparation step does not open a radio. When the builder and input files
are already present, local preparation also works without network access.
For a full bundle on a destination that supports it, choose **Full** in
**Settings** before preparation; this needs neither a differential compiler
nor USB dependencies.

The live destination is checked again before installation. A changed base
hash or a different board stops a differential update.

## If the connection is interrupted

Keep the same source radio connected and run the wizard again. Choose
**Recover an interrupted update**, then select the saved session by its
public-key prefix and progress state. The wizard checks the complete source
and destination keys before restoring settings. A normal update also restores an unfinished
session for the selected destination before proceeding.

Keep the recovery record until restoration is complete. A lost install reply
is followed by a running-image check; the wizard does not blindly repeat an
install command. A cancelled download is cleared only when its package ID
matches this update. Automatic installation is restored only after installation
or receive-slot clearance is confirmed; an uncertain result leaves it off and
keeps the recovery record.

Temporary radio windows use the existing main-channel
settings. Existing temporary schedules block an update before settings are
changed, including on older firmware.
The window covers both bootloader and firmware transfers, including reboot
and setup time. Status checks normally run every ten seconds.

The cache is normally `~/.cache/meshcore/ota` on Linux,
`~/Library/Caches/MeshCore/ota` on macOS, or `%LOCALAPPDATA%\MeshCore\ota` on
Windows. It contains firmware, public preparation records, selected board
keys, and restoration settings. Trusted signer keys added for an update remain
installed on the destination.

<details markdown="1">
<summary>Optional command-line use for automation</summary>

Interactive users can use the menus for every step. Existing command-line
options remain available for scripts:

| Option | Purpose |
| --- | --- |
| `--check` | Inspect without downloading or installing firmware |
| `--port /dev/ttyUSB0` | Choose a port; Windows example: `--port COM5` |
| `--mode repeater` or `--mode companion` | Select the source type |
| `--target PUBLIC_KEY` | Choose the full destination key; direct discovery is required by default |
| `--target-type repeater` | With a repeater source and full `--target`, authenticate a known repeater without discovery |
| `--firmware FILE` | Use a local firmware image or prepared bundle |
| `--base FILE` | Supply the exact running image for differential preparation |
| `--full` | Force a full transfer where supported |
| `--build` | Build custom Full firmware from source |
| `--no-bootloader` | Update application firmware only |
| `--prepare-only --request DEVICE.json --firmware FILE --output update.ota.zip` | Prepare without radios |
| `--recover --target PUBLIC_KEY` | Restore settings and exit |
| `--yes` | Accept the displayed plan; credentials and required choices must still be supplied |

For unattended use, provide `MESHCORE_ADMIN_PASSWORD` in the process environment
and pass the port, source type, and target key. The password is excluded from
preferences, recovery records, and the custom build environment.

</details>

For individual commands and a manual board example, see
[Station G2 to Ikoka Stick](ota_station_g2_to_ikoka_stick.md). For routed updates
and explicit multi-node setup, use the [scripted LoRa OTA guide](lora_ota_automation.md).
