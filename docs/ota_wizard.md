<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/ota_wizard/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Guided LoRa updates

Run one Python command to update a Repeater or Room Server.
The wizard identifies the board, chooses compatible firmware, prepares the
transfer, installs it, checks the result, and restores the original radio,
RX power-saving, and OTA settings. On supported nRF52 boards it also checks
for a newer compatible signed bootloader and includes that update in the plan.
Direct LoRa is the default. The optional **LoRa relays** link sends both
administration and firmware through an ordered route of repeaters.

## Connect a source radio

Use either connection:

| Computer connection | Source radio | Destination |
| --- | --- | --- |
| Raspberry Pi â†’ USB | ESP32 Repeater with OTA and `ota.peer` support | Repeater or Room Server |
| Computer or Pi â†’ USB | Full Companion with USB OTA source and direct admin support | Repeater or Room Server |

A **Station G2 Repeater connected to a Pi** can manage and serve an update to
an **Ikoka Stick Repeater** through that single USB connection. It does not
need a second local radio or the phone app. Install a source firmware build
containing `ota.peer` first; older Repeater firmware cannot manage the remote
node through USB. Check with `ota.peer status` in its USB console.

For slow replies during manual USB administration, `ota.peer timeout 180`
raises the source's request timeout from 30 to 180 seconds. This RAM-only setting
survives closing the admin session and resets on reboot; keep the controller's
timeout longer than the source's.

If the destination already grants this repeater's public key the required ACL
role, `ota.peer acl PUBLIC_KEY` opens a USB admin session without a password
login. It sends an authenticated direct return path and verifies the
destination's full public key before accepting `ota.peer cmd PUBLIC_KEY get tx`.
The destination still enforces its ACL permissions for every command.

For a single command using an existing ACL grant, use
`ota.peer cmd.acl PUBLIC_KEY get tx`. It sends only the encrypted zero-hop CLI
request, without login, return-path changes or an identity-proof exchange.
The reply is correlated to that request; this does not open an authenticated
admin session. The destination still checks the source's ACL role.
It requires the destination's saved return route to be direct.

For manual USB administration on a configured secondary MeshCore profile, use
`ota.peer radio radio2` before discovery or login; `ota.peer radio radio` selects
the primary profile. `ota.peer radio` reports the choice. The secondary profile
must be enabled for RX and TX. This RAM-only choice survives `ota.peer close`
and resets to primary on reboot. Changing the choice clears authentication and
discovered peers; changing the selected profile's configuration cancels a pending
request and clears authentication. Admin traffic stays on the selected profile.

The Full Companion source also needs a current build containing the wizard's
version 2 direct admin support. An older Full Companion may serve OTA files but cannot
provide this guarded admin connection; the wizard will ask for a source
firmware upgrade instead of proceeding with that connection.
Check `get ota.direct` from its USB console for `v=2`. Update replies use a
separate mailbox; the wizard does not consume ordinary queued messages or change
saved contact routes. Some Full Companion boards borrow queue space for OTA and
retain up to 128 unread messages during an update. If more are waiting, sync them
with an app first; the wizard refuses to discard them to make room.

For a direct update, start with the source's primary frequency, bandwidth and
spreading factor matching the destination's saved primary profile, with a direct link between
them. The saved coding rates may differ. The destination needs working LoRa OTA support
and either its admin password or an admin ACL entry for the source radio.
A stock nRF52 bootloader may need a matching local
installation before it can accept remote updates.

By default, the wizard sends discovery, admin commands, and OTA traffic directly with no
relay hops. An older destination may put its first login reply in a flood
envelope; the wizard accepts it only if it arrives with zero hops, establishes
a direct return route, then requires a fresh direct identity check.

Close other apps using the USB port before starting.

### Use a known repeater key

With a USB Repeater source, `--target PUBLIC_KEY` selects a known repeater
without waiting for a discovery response. Supply its complete 64-character
public key. The legacy `--target-type repeater` flag is still accepted.
The source must advertise `known_repeater=1` in its `ota.peer capabilities`
reply; update its firmware first if that capability is missing.

For an existing admin ACL grant, replace `PUBLIC_KEY` with the destination
repeater's full key:

```bash
python3 tools/lora_ota/ota_wizard.py --mode repeater --port /dev/ttyACM0 \
  --target PUBLIC_KEY --auth acl --check
```

Password authentication sends a zero-hop login and verifies the exact public
key before updating. ACL authentication uses the source's existing permission
without a password login or a fresh identity-proof exchange. It requires a
known full repeater key and a saved direct return route on the destination.
The default direct link does not fall back to relays. Select **LoRa relays**
explicitly for a routed update. Keep the same source radio connected when
recovering an interrupted update.

After an install or during recovery, the wizard rebuilds the authenticated
direct return route and checks the full public key once. This uses the existing
ACL grant without a password login. Learned return routes can disappear on
reboot; ordinary ACL commands still use the single-command path above.

In **Settings**, set **Authentication** to **ACL** for this setup. Use
**Password** when the source has no admin ACL grant; an empty password is
not an ACL-mode selector.

**Auto** uses ACL for a USB Repeater with a supplied or reused full repeater
key when no password environment variable is set. Discovery and Full
Companion connections use password authentication. Explicit **ACL** ignores
the password environment variable and does not fall back to a login.

## Start

From the MeshCore checkout, run:

```bash
python3 tools/lora_ota/ota_wizard.py
```

On Windows, use `py` in place of `python3`. No command-line options are needed.
The menu provides these actions:

1. **Update a nearby radio** â€” the default.
2. **Check radios / save a preparation request** â€” inspect without installing.
3. **Prepare an update bundle on this computer** â€” no radios needed.
4. **Recover an interrupted update** â€” restore saved settings.
5. **Settings** â€” change transfer, bootloader, connection, or build preferences.
6. **Exit**.

The first live connection installs USB and verification dependencies in a
private Python environment if needed. On Raspberry Pi OS, install
`python3-venv` if Python reports that it is missing.
Preparing a signed input file can also install signature-verification support
on first use, without installing the USB software.

For an update, the wizard selects the USB radio and destination automatically
when there is only one of each. Otherwise, choose from the displayed lists.
With a USB Repeater, enter or reuse a known full repeater key to skip discovery.
Choose firmware, review the plan, then press Enter to start. If password
authentication is needed, enter it once. Password entry is hidden and
passwords are never saved. After an action, the menu is shown again.

## Use radio2 for the transfer

With **Link** set to **Direct** and a USB Repeater source, set **Source transfer
radio** to **radio2** in **Settings**. Optionally set **Transfer channel** to `FREQ,BW,SF,CR`, using
MHz, kHz, spreading factor and coding-rate denominator. Choose values allowed
in your region and supported by both boards. Without an override, the wizard
uses the destination's saved primary settings.

The wizard starts administration on the shared primary profile, creates a
temporary RXTX secondary profile on the source, then moves the destination
to a matching temporary primary profile. It accepts the destination's `OK`
acknowledgement before switching subsequent administration to radio2. The
source's primary radio settings and repeating state stay in place. Source
RX power saving is temporarily disabled for scanning and restored afterward.

The source secondary profile must be unused, with no configured profile,
temporary lease or schedule, and cross-profile forwarding off. Existing
temporary schedules on the destination also block setup. This lets recovery
clear the wizard's own lease without replacing another operator's profile.
The wizard restores the destination to its original primary profile before
removing the source lease. Full Companion sources use their primary profile.

For manual administration of an already configured secondary profile, use
`ota.peer radio radio2`; the guided wizard's ownership checks are stricter.

### Keep control traffic low

A successful temporary-profile acknowledgement is sufficient; there is no
need to follow it with repeated `get tempradio` queries. Retry a necessary
read or idempotent setting at most three times. Use `get tx` as a connectivity
probe only if all three attempts fail.

Do not blindly repeat a relative temporary lease, download, install or cancel
after losing its reply. Check the selected profile and owned package state
first. A lost acknowledgement can mean that the command already took effect.

## Update through LoRa relays

In **Settings**, select **Link → LoRa relays**. Enter the complete relay public
keys under **Relay keys**, separated by commas, in order from the source to the
destination. One to eight relays are supported. The hop limit is raised to the
number of relays when needed. Then choose **Check radios** or **Update**, and
enter the destination's complete public key.

```text
USB Repeater --> relay 1 --> relay 2 --> destination
              LoRa administration and firmware
```

This mode requires a USB Repeater source with `routed_path=1` in
`ota.peer capabilities`. Each relay and the destination must grant that source
an admin ACL role. Password login, Full Companion sources and source radio2
are supported by the default direct mode; relay mode selects the USB
Repeater's primary radio and ACL authentication.

Start with every participant on the same primary frequency, bandwidth,
spreading factor and coding rate. This mode keeps that channel; a different
**Transfer channel** is rejected. The wizard checks the relay identities and
settings, opens temporary OTA windows, disables RX power saving, enables
relay repeating, and sets a positive OTA hop limit. The temporary preamble
defaults to 128 symbols. Saved radio, RXPS, OTA and relay repeating settings
are restored afterward, including when recovering an interrupted update.
Existing temporary profiles, schedules or downloads stop setup before
settings are changed.

Relays must already forward administration along the selected route so the
initial checks can reach every node. Their ACL and filter rules must also
permit OTA forwarding; the wizard does not change those rules.
Keep all participants powered until installation and recovery finish. The
normal **Direct** link continues to use zero-hop OTA.

For manual USB administration, `ota.peer path ABCDEF,123456` selects ordered
relay prefixes; `ota.peer path off` restores direct operation. Prefixes must
all use the same width of one, two or three bytes. The wizard uses three-byte
prefixes derived from the supplied full keys and checks for collisions.

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
Recovery reuses the recorded authentication and profile options, rather than
the current menu settings. Relay sessions also retain the ordered relay keys, link type, hop limit,
channel and preamble. Recovery needs the same source and LoRa relay route.

Keep the recovery record until restoration is complete. A lost install reply
is followed by a running-image check; the wizard does not blindly repeat an
install command. A cancelled download is cleared only when its package ID
matches this update. Automatic installation is restored only after installation
or receive-slot clearance is confirmed; an uncertain result leaves it off and
keeps the recovery record.

Temporary radio windows use the existing primary settings unless a transfer
profile was chosen. Existing temporary schedules block an update before
settings are changed, including on older firmware.
The window covers both bootloader and firmware transfers, including reboot
and setup time. Remote progress checks normally run every 45 seconds.

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
| `--target PUBLIC_KEY` | Choose the full destination key; a USB Repeater source skips discovery |
| `--target-type repeater` | Legacy flag for known-repeater selection with a USB Repeater source |
| `--auth auto`, `--auth acl` or `--auth password` | Select authentication; ACL uses the USB Repeater's existing grant |
| `--source-radio radio` or `--source-radio radio2` | Choose the source transfer profile; radio2 requires a USB Repeater |
| `--transfer-radio FREQ,BW,SF,CR` | Direct link's temporary profile; relay mode accepts only the existing shared profile |
| `--link direct` or `--link lora-relay` | Choose zero-hop LoRa or an ordered LoRa relay route |
| `--relay PUBLIC_KEY` | Full relay key; repeat in order from source to destination, up to eight |
| `--ota-hops 1` | Relay link's LoRa hop limit, 1-8; at least the relay count |
| `--transfer-preamble 128` | Relay link's temporary preamble in symbols |
| `--firmware FILE` | Use a local firmware image or prepared bundle |
| `--base FILE` | Supply the exact running image for differential preparation |
| `--full` | Force a full transfer where supported |
| `--build` | Build custom Full firmware from source |
| `--no-bootloader` | Update application firmware only |
| `--prepare-only --request DEVICE.json --firmware FILE --output update.ota.zip` | Prepare without radios |
| `--recover --target PUBLIC_KEY` | Restore settings and exit |
| `--yes` | Accept the displayed plan; credentials and required choices must still be supplied |

For unattended password authentication, provide `MESHCORE_ADMIN_PASSWORD` in
the process environment and pass the port, source type, and target key. With
an existing ACL grant, use `--auth acl` and the full known repeater key.
Passwords are excluded from preferences, recovery records, and the custom
build environment.

</details>

For individual commands and a manual board example, see
[Station G2 to Ikoka Stick](ota_station_g2_to_ikoka_stick.md). For routed updates
with a Companion controller and explicit multi-node setup, use the
[scripted LoRa OTA guide](lora_ota_automation.md).
