# Updating your node over the air (OTA) - user guide

This guide is for **node operators**: how to update your MeshCore device's firmware over the radio, in
plain language. No cables, no programmer - your node can download a new firmware from a neighbour and
install it. (For the technical wire format, see [the OTA protocol spec](ota_protocol.md).)

LoRa OTA download and installation are present only in supported Keymind destination artifacts; the receiver
must already be running one of those install-capable builds. Some internal-staging nRF52 targets use a lean
`lora_ota_no_external_sensors` target, while matched external-QSPI boards can retain their normal full-sensor
repeater features. Release filenames include an OTA marker, but capability must still be confirmed on the
running device. A source can be an OTA-enabled infrastructure node or a source-only Full Companion backed
by `motatool`. Intermediate repeaters
do not need OTA-enabled firmware: current repeater builds transport OTA floods opaquely, subject to their normal
forwarding filters, duplicate checks, and flood limits. OTA radio traffic is accepted, generated, and relayed
only while `tempradio` is actually running on that node. Every source, receiver, and intermediate repeater must
therefore have an overlapping temporary-radio window.

The recommended temporary OTA settings use 250 kHz bandwidth, SF5, CR5, and a 120-minute window. For a
North American node currently configured for 909.950 MHz, run this on every participating node:

```text
tempradio 909.950,250,5,5,120
```

Use the node's current permitted regional frequency in place of `909.950` when necessary.

> **Can my node install the update?** Choose a release-table artifact explicitly labelled LoRa-OTA capable,
> then confirm `ota self` and `ota status` expose install support; do not infer support from the filename alone.
> LoRa OTA firmware is available for supported **ESP32** boards and nRF52 repeater targets. Every nRF52
> installation also requires the OTAFIX bootloader built for that exact board; having an OTA-capable
> application image alone is not enough. An intermediate repeater only relays packets and needs neither an
> install-capable image nor OTAFIX. Check the bootloader release for an exact board match before attempting an update.

The following nRF52 repeater families gained firmware-side LoRa OTA targets in
this release. Their ordinary repeater remains the full-sensor build; the
install-capable `lora_ota_no_external_sensors` sibling is smaller:

- Heltec Mesh Solar, T1, and Tower V2
- Keepteen LT1, LilyGo T-Impulse Plus, Mesh Pocket, and Nano G2 Ultra
- Minewsemi ME25LS01, RAK3401, SenseCAP Solar, and Wio WM1110

The RAK3401 `RAK_3401_repeater_lora_ota_no_external_sensors` image retains
RAK12501 GPS while omitting the other optional environmental sensors. Install
the GPS in sensor slot A. Slot D's reset/PPS lines conflict with the RAK13302
radio's BUSY/DIO1 lines.

The RAK4631 internal-flash OTA repeater likewise retains GPS. A RAK12501 can
use sensor slot A or D; a RAK12500 can use slot A or C. The RAK4631 Serial1
RS232 OTA bridge is the exception because the bridge owns the GPS UART. The
Serial2 RS232 OTA bridge retains GPS on Serial1.

Selected nRF52 repeaters with dedicated external QSPI can now stage the
complete package off-chip, so their normal full-sensor repeater build can
install a full image or an in-place delta. The current matched families are
XIAO nRF52840 and its XIAO-module derivatives, original LilyGo T-Echo,
ThinkNode M1/M6, Wio Tracker L1, SenseCAP Solar, and the dedicated RAK4631 +
RAK15001 slot-C target. These require the corresponding QSPI-aware OTAFIX bootloader; see
[the nRF52 QSPI guide](ota_nrf52_qspi.md).

The ordinary full-sensor `RAK_4631_repeater` image remains too large for the
safe internal in-place update limit. Without external flash, use
`RAK_4631_repeater_lora_ota_no_external_sensors`; it removes optional external
environmental/GPS packages but retains battery monitoring. A RAK4631 fitted
with RAK15001 in sensor slot C can instead use
`RAK_4631_repeater_rak15001_slot_c_lora_ota` to retain the full sensor/GPS set
and stage full images or deltas off-chip.

---

## The important part first: it's safe

- **Nothing installs by itself.** Your node can *discover* and download an update, but
  it only **installs** when you say so (unless you deliberately turn on auto-install - see below).
- **Bad downloads can't sneak in.** Every piece of the firmware is checked against a cryptographic
  fingerprint as it arrives, and the whole image is verified again before install. A corrupt or tampered
  download is rejected, not installed.
- **You choose who to trust.** Updates can be *signed* by their author. You can tell your node to only
  auto-install firmware signed by keys you've added.
- **Discovery stays quiet; a transfer is deliberate.** Periodic update discovery uses background priority.
  Once a download starts, its transfer packets are primary traffic across every relay hop, so use
  TempRadio as an OTA maintenance window when delaying unrelated mesh traffic would matter.
- **It can recover.** If an install ever fails, the node falls back to a safe recovery mode (you can
  re-flash a known-good firmware over USB) - it won't be left bricked.

---

## How to talk to your node

Connect to your node's **console** - usually a USB serial terminal at **115200 baud** (or whatever tool
you already use to manage the node). You type `ota ...` commands and the node replies in plain words.

The commands have short, friendly names (and most accept aliases, so you don't have to remember exact
spelling): type **`ota help`** any time to see the list, or just **`ota`** for a status summary.

---

## Common tasks

### 1. See what I'm running and whether anything is going on

```
ota status
```

Shows your current firmware version, your node's update "target" (its hardware/role id), and whether a
download is in progress.

For a denser admin view - your firmware's content id (`mid`) **and** its body hash, the fingerprint of the
set you're serving, live download progress, and the current policy - use:

```
ota stats
```

On a remote node this is **admin-only** (the remote command console requires the admin password) - send it
from the app's repeater command screen, or the WiFi/serial OTA console.

### 2. Find updates available near me

```
ota ls
ota ls 2                 # page 2 when more than two updates are available
```

Your node asks around and lists the firmware updates other nodes nearby are offering, in plain words -
each with a temporary **number**, a stable eight-hex **manifest ID**, its version, whether it's a full image
or a small delta, how many nodes have it, and how recently it was seen. For example:

```
Updates 1/1 (2 src; refreshing):
 1) 838B8169 v1.2.3 delta [same target] 3n 5s
 2) BF0AB0C4 v1.2.0 full [unsupported] 1n 12s
```

Each row shows the version, full-vs-delta, **whether it fits your node**, how many nodes have it, and how
long ago it was seen. The fit marker:

- **[same target]** - the advertised target ID matches this hardware-and-role build. Download and apply
  still enforce codec, bootloader, signed hardware tag, base hash, and integrity checks.
- **[unsupported]** - the target may match, but this build or its bootloader cannot apply that codec. A common
  example is the source node's self-served **full** image on an internal-staging nRF52, which needs an
  in-place delta. A matched external-QSPI nRF52 can accept that full codec.
- **[rescue]** - an installable in-place nRF52 delta for the same target, but this running firmware has no
  valid app-side EndF. It requires the explicit rescue download and install flow below.
- **[name]** - a different known board or role (for example `[ProMicro_companion_radio_usb]`). Don't install it.
- **[?]** - can't tell (a build with no target id set, e.g. a bare IDE build rather than a release build).

Run it again after a few seconds - discovery happens in the background, so the list fills in. Nothing is
downloaded yet; this is just looking around. `refreshing` means the command has just sent asynchronous
catalog queries, so run it again even when an older row is already visible. Two updates fit in each remote
CLI reply; use `ota ls 2`, `ota ls 3`, and so on for later pages. Catalog rows can change while replies arrive,
so use the displayed manifest ID for scripts and important operations rather than a numeric position.
(`ota neighbors` / `ota updates` also work.)

### 3. Download an update

Pick one from the list by its stable **manifest ID** (a number also works for interactive use), and say
**where** to put it:

```
ota pull 838B8169 flash            # stage it in this node's flash, to install here
ota pull 838B8169 folder           # capture it onto a connected motatool folder as <id>.mota
ota pull 838B8169 folder validate  # warm-start capture from a motatool --seed build (much faster; below)
```

The destination is required - `ota pull 838B8169` on its own just shows the choices. **`flash`** is always
available (stage here, then `ota install`). **`folder`** appears only while a `motatool serve` link is
attached (it shows the link, e.g. `folder: tcp 192.168.4.5`); it streams the firmware straight onto the
host folder - nothing is staged on this node. That's how you grab an **exact copy of another device's
firmware** off the mesh (to a `.mota` file) so you can later build a *delta* against firmware you don't
otherwise have. (`ota get` is an alias.)

**`validate` (warm-start, advanced).** Capturing a full image over the radio is slow. If you have a
*similar* build on the computer (e.g. a fresh recompile of the same firmware), run motatool with
`--seed <that.mota>` and add **`validate`**: the node fetches just the target's block fingerprints, keeps
every block your seed already matches, and pulls over the radio only the handful that actually differ -
turning a ~30-minute capture into seconds. The result is still a byte-exact, verified copy of the target.

*Where the seed comes from:* it is the **`--seed <file>` you pass to `motatool serve`** - **not** a file you
drop into the capture (`--dir`) folder, which is only the destination and starts empty. There is exactly one
configured seed. When you run `... folder validate`, the node asks motatool to begin the capture and motatool
stamps that seed's payload into the fresh `.part` in the same step - so it is always the file you named, with
no guessing. **`validate` is the switch:** a plain `folder` pull ignores any seed and fetches from scratch;
re-running a `validate` pull re-begins fresh (it never resumes a stale partial). Nothing about the seed is
trusted - every kept block is checked against the target's own fingerprints, so a mismatched or missing seed
just means those blocks are fetched over the radio (correct result, only slower).

The node fetches from one source as **primary traffic**, with bounded adaptive request flights. Every session
probes with one 1 KiB block, then clean flights grow `1 -> 2 -> 3 -> 4` blocks on RAK3401 OTA builds. All blocks
in a flight share one request packet, and the receiver stays silent until the source/relays finish returning
them. A recovery halves the next flight. Retry timing follows the active SF/BW airtime, duty budget, and path
length, so faster settings recover sooner without a fixed one-second request colliding with a valid multi-hop
response. The signed block size itself remains 1 KiB. Mesh repeaters carry the packets only while their
temporary-radio windows are active.
Check progress with `ota status`.

If a `folder` pull loses its link mid-transfer, `ota status` shows **paused** - the host keeps the
partial and the pull resumes (filling only what's missing) the moment you reconnect motatool; it never
falls back to flash. To **stop** a download you no longer want:

```
ota cancel
```

### 4. Install a downloaded update

Once `ota status` shows the download is **ready to install**:

```
ota install
```

The node verifies the firmware one last time, and if everything checks out it installs it and **reboots
into the new version**. If the check fails, it tells you why and does **not** install. Unsigned images
normally install only through this explicit command. The MeshTower V2 SD target is stricter and requires an
allowlisted signature even for a manual application install, because it authorizes removable-media bytes for
the bootloader. A signed image whose signer is not in the device allowlist is rejected; trusted signed images
can auto-install only when that policy is enabled and the signed version is strictly newer than the running
hash-valid EndF version. Manual `ota install` remains the deliberate equal-version/rollback override.

After it reboots, run `ota status` to confirm the new version.

### Updating an nRF52 bootloader (advanced, explicit only)

This is available on specially marked no-external-flash nRF52840 lean
repeater/bridge builds, on the legacy XIAO nRF52840/Sense raw-QSPI builds, and
on the exact `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors`
microSD build,
after a one-time exact-board ABI-3 OTAFIX installation over USB/BLE DFU or
SWD. It is not the normal firmware update path. Check support first:

```text
ota bootloader
```

The reply must show a CRC-valid installed identity plus ABI 3 and the exact
storage/boot-update capability bits for that build (`0x09` for MeshTower V2
microSD, `0x0A` for the shared internal store, or `0x0E` for XIAO raw QSPI). A bootloader package appears as
`bootloader` in `ota ls`. It is never downloaded or installed automatically,
even if both OTA automation settings are enabled. Use its stable ID explicitly:

```text
ota pull <MID8> flash
# wait until ota status says this bootloader download is ready
ota bootloader
ota bootloader install <MID8> <HASH16>
```

Copy both confirmation values exactly from the second `ota bootloader` reply.
Ordinary `ota install` deliberately refuses this package. The privileged
command requires an exact 40 KiB candidate payload in the fixed 41,330-byte
container, a valid exact embedded identity/CRC and vector table, continued
boot-update support, required `BLM2`/`SOFT` continuity metadata at canonical
raw-image offset `0x9FB4`, a boot version
that exactly matches the package and is newer than installed BLM2, and a valid
signature from a key already in `ota key`'s trusted allowlist. It preserves the
running application while OTAFIX replaces itself; `blup:C8` in post-reboot
`ota status` means success. Remote rollback is refused. Any node lacking this
command or those capabilities must update its bootloader locally instead.

On an internal-flash target, the package shares the ordinary store below
`0xED000` and bottom-aligns at `0xE2000`; there is no separate reserved scratch
bank. A valid live `EndF` must prove the current image ends at or below that
address before any page is erased. If `EndF` is missing/corrupt or the app is
too large, the pull is refused and local DFU/SWD is required.
On the MeshTower V2 SD target, the application linker remains at `0xED000`, but
bootloader replacement needs temporary scratch beginning at `0xE0000`. A
hash-valid live `EndF` must therefore prove the complete current image ends by
`0xE0000`. A CRC-bound boot-settings bank must cover that complete image while
also ending by `0xE0000`. MeshCore binds both application and bootloader SD
approval to purpose, exact raw geometry, and a normalized SHA-256 in a
reset-retained `MOTASDA2` record. Boot updates also bind the exact authenticated
signed image hash in the E0000 token. OTAFIX consumes the retained record before
media access, so a later card change or power cycle fails instead of authorizing
different bytes. MeshCore never claims or writes raw sector 1. Both application
and bootloader OTA wait until a matching BLM2 bootloader has been provisioned
locally; preview.12 requires USB/BLE DFU or SWD first.
Larger applications can continue to use normal application mOTA;
only bootloader self-update is refused.
See [the nRF52 bootloader-update guide](ota_nrf52_bootloader_update.md) for the
complete target inventory, storage layouts, and safety contract.

### 5. If something goes wrong

- A download that stalls or gets interrupted just **resumes** later, or you can `ota cancel` and try again.
- A legacy app-only internal-flash **nRF52** that still runs but reports `no EndF` can use the pre-provisioned rescue path
  if its physical EndF is intact and only app-side validation is failing. Fetch the exact `[rescue]`
  in-place delta with an explicit acknowledgement, obtain its 16-hex-digit `base_hash` from the package
  metadata, then run:

  ```text
  ota pull <mid8> flash rescue
  # wait for ota status to say ready to install
  ota rescue install <base_hash16>
  ```

  This is not a force option. It refuses a normally valid EndF, a different package hash, hardware or
  target mismatch, corrupt payload, and invalid/untrusted signatures. The bootloader independently hashes
  the running app and rejects a wrong base before writing the app. If the physical EndF is absent or the
  rescue commands were not already in the running firmware, recover over USB.
  Release chains should put this command in their first bridge and keep it in every bridge after that.
  The shared-internal bootloader-update builds are intentionally excluded:
  without valid live `EndF`, they refuse every internal pull before erase
  because their normal application can extend through `0xED000`. Recover one
  of those builds over USB/BLE DFU or SWD.
- If an **install** fails, the node won't boot a broken image - it lands in **recovery mode**:
  - **nRF52:** it appears as a USB drive; drag a known-good firmware `.uf2` for that exact board onto it
    to recover.
  - **ESP32:** it keeps the previous firmware in the other slot and rolls back.
- When in doubt, you can always re-flash over USB the normal way.

---

## Optional: let it update automatically

By default your node only *discovers* updates - it won't download or install on its own. If you want more
automation (e.g. for a remote node you can't easily reach), you can opt in. These settings are saved.

```
ota config autofetch any        # auto-DOWNLOAD any compatible update for this node (still won't install)
ota config autofetch signed     # auto-download only signed updates
ota config autofetch off        # back to manual (default)

ota config autoinstall trusted  # auto-INSTALL only a trusted signed version newer than the running EndF
ota config autoinstall off      # never auto-install (default)

ota config advert 1440          # re-advertise every N minutes while temp radio is running
ota config advert 0             # only advertise when a temp-radio window starts

ota config hops 3               # how far OTA travels: accept from / relay up to N repeater hops (default 3)
ota config hops 0               # only exchange OTA with directly-connected nodes (never relay)

ota config                      # show the current settings
```

These policies also govern automatic adoption of an interrupted staged download after reboot. `off` leaves
it untouched, `signed` requires the stored manifest's signed flag, and automatic resume requires the stored
target to match this node and its version to be newer than the running valid EndF. Reissuing an explicit
`ota pull <MID8>` remains the deliberate override for an older or unsigned partial.
For bring-up/debugging, `ota dev resume <MID8>` performs the same explicit MID-bound re-adoption without
starting a new network fetch. After reboot it requires the MID; bare `ota dev resume` is accepted only while
an active/requested session MID still exists, and malformed or missing identifiers are rejected.

Recommended for most people: leave both **off** and update by hand. Use `autoinstall trusted` only once
you've added the signer's key (next section) and you trust them to push updates unattended. Automatic
admission and final apply both reject zero, equal, or older signed versions; a dishonest catalog version
cannot bypass the manifest check. Use manual `ota pull` plus `ota install` for an intentional rollback.

The MeshTower V2 SD OTA target has a separate, default-on **archive** policy. It saves all mOTAs it sees
to the SD card so the repeater can seed them later; this does not install them and does not change the
install-oriented `autofetch` default above. Use `ota cache` for status and `ota cache off` or
`ota config cache off` to stop new archive captures. Already cached files remain available to peers.
Manual `ota pull` commands take priority and an interrupted archive capture resumes later.
See [Preload many mOTAs from a computer](ota_meshtower_v2_sdcard.md#preload-many-motas-from-a-computer)
for the required `/mota/<manifest-id>.mota` filenames and the complete TempRadio seeding workflow.

---

## Optional: only trust updates from specific people

If you'll use auto-install, tell your node which signing keys to trust. The firmware author shares their
**public** key (a hex string); you add it:

```
ota key add <public-key-hex>    # trust this signer
ota key list                    # show trusted signers
ota key rm <public-key-hex>     # stop trusting one
```

Only strictly newer updates signed by a trusted key are eligible for auto-install. Manual `ota install`
permits an unsigned package after all integrity, hardware, base, and bootloader checks pass, except on the
MeshTower V2 removable-SD path where all application installs must be signed and allowlisted. A signed
package whose signer is not in the device allowlist is rejected rather than silently treated as unsigned.

---

## Sharing updates with others (advanced)

### Relay a folder of firmware from a computer

If your node is connected to a computer (e.g. a gateway on a Raspberry Pi), it can **hand out** a whole
folder of firmware files to the mesh - without storing them itself. Useful for seeding a new release to a
remote area.

1. Put the firmware files (`.mota` files - see below) in a folder on the computer.
2. Install the helper tool once - the standalone `motatool` CLI (<https://github.com/vk496/motatool>) -
   then point it at your node and the folder - over the node's **USB serial**, or over **WiFi** if it is
   an ESP32 WiFi companion or FULL ESP32 node:
   ```
   git clone https://github.com/vk496/motatool && cargo install --path ./motatool
   # over USB serial:
   motatool serve --dir ./my_firmware/ --serial /dev/ttyACM0 -v
   # ...or over WiFi: the seeder is on dedicated TCP port 5001:
   motatool serve --dir ./my_firmware/ --tcp 192.168.1.50:5001 -v
   ```
   It answers the node's requests; your node then advertises those updates to neighbours, who can
   `ota get` them like any other. (A WiFi node prints its IP + seeder port to the serial log on connect.
   Details: <https://github.com/vk496/motatool>.)

Check the device's attach reply or run `ota folder`: `host=X/Y` means the firmware is advertising `X` of
the `Y` valid entries reported by the host. Serve registries are deliberately RAM-bounded on smaller builds,
and the node's own firmware also consumes a slot. If `X < Y`, split the chain across seeders/folders or use
a higher-capacity seeder; `motatool` saying that every file is valid does not mean every file fit on-device.

To stop, just stop the daemon - over WiFi the node auto-detaches when the connection closes; over USB you
can also run `ota folder off` on the node. `ota folder` on its own lists what your node is offering.
On a FULL repeater or room server, run `start webconfig` first if WiFi is not
already active. Other FULL roles with browser OTA support can use the
`MeshCore-OTA` access point from `start ota` and connect to
`192.168.4.1:5001`. Every LoRa participant still needs an overlapping
`tempradio` window.

### Everyone helps share

You don't have to be a gateway to help. Once **any** node finishes downloading an update, it automatically
offers it to *its* neighbours too. So a new firmware spreads outward node-to-node, instead of everyone
hammering the one node that had it first. Discovery remains background traffic; an actual transfer is
primary traffic for the duration of its TempRadio maintenance window.

---

## Where firmware files come from

OTA distributes **`.mota`** files - a packaged, verifiable firmware image (full image or a small "delta"
that only contains what changed). You get them by:

- **Downloading a build.** This fork publishes a rolling **`dev-latest`** release on GitHub with the
  current firmware for many boards, each accompanied by a `.full.mota` and a tiny `.delta.mota`. Grab the
  one for your board to test.
- **Building your own** with the `mota` packaging tool - see [tools/mota/README.md](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/mota/README.md)
  (this is for people distributing updates, not everyday operators).

---

## Quick reference

| I want to... | Command |
|---|---|
| List all commands | `ota help` |
| See my firmware + any download | `ota status` (or just `ota`) |
| Admin: ids/hashes + serving + policy | `ota stats` (admin-only remotely) |
| Find updates nearby | `ota ls` |
| Download a listed update for installation | `ota get <mid8> flash` |
| Cancel a download | `ota cancel` |
| Install a finished download | `ota install` |
| Recover app-side `no EndF` on a legacy app-only internal nRF52 | `ota rescue install <base_hash16>` |
| Turn on auto-download | `ota config autofetch any` |
| Turn on auto-install (trusted only) | `ota config autoinstall trusted` |
| Trust a signer | `ota key add <hex>` |
| Relay a folder (gateway) | `ota folder on` + the seeder daemon |
| List what I'm offering | `ota folder` |

(Older names still work too: `neighbors`/`updates` = `ls`, `pull` = `get`, `applydelta`/`apply` = `install`, `drop`/`stop` = `cancel`.)

---

## A few terms

- **Firmware** - the software running your node. Updating it can add features or fix bugs.
- **`.mota`** - a packaged firmware update file, with built-in integrity checks.
- **Target** - your node's hardware + role identity. Your node only auto-fetches updates built for the
  same target, so it won't grab firmware meant for a different board.
- **Delta** - a small update containing only the changes from your current firmware (faster to send than a
  full image). Your node rebuilds the complete firmware from it and verifies the result before installing.
- **Signed** - the update carries the author's cryptographic signature, so you can verify who made it.

For the full technical details (the file format and the radio protocol), see
[the OTA protocol spec](ota_protocol.md).
