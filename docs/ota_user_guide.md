# Updating your node over the air (OTA) - user guide

This guide is for **node operators**: how to update your MeshCore device's firmware over the radio, in
plain language. No cables, no programmer - your node can download a new firmware from a neighbour and
install it. (For the technical wire format, see [the OTA protocol spec](ota_protocol.md).)

LoRa OTA download and installation are present only in supported Keymind artifacts whose filename contains
`-ota-`. Use an `-ota-` build on the source and receiver. Intermediate repeaters do not need OTA-enabled
firmware: current repeater builds transport OTA floods opaquely, subject to their normal forwarding filters,
duplicate checks, and flood limits. OTA radio traffic is accepted, generated, and relayed only while
`tempradio` is actually running on that node. Every source, receiver, and intermediate repeater must therefore
have an overlapping temporary-radio window.

The recommended temporary OTA settings use 250 kHz bandwidth, SF5, CR5, and a 120-minute window. For a
North American node currently configured for 909.950 MHz, run this on every participating node:

```text
tempradio 909.950,250,5,5,120
```

Use the node's current permitted regional frequency in place of `909.950` when necessary.

> **Can my node install the update?** Choose a supported repeater artifact carrying the `-ota-` filename stamp.
> LoRa OTA firmware is available for supported **ESP32** boards and nRF52 repeater targets. Every nRF52
> installation also requires the OTAFIX bootloader built for that exact board; having an OTA-capable
> application image alone is not enough. An intermediate repeater only relays packets and needs neither the
> `-ota-` image nor OTAFIX. Check the bootloader release for an exact board match before attempting an update.

The following nRF52 repeater targets gained firmware-side LoRa OTA support in this release without losing
their normal external-sensor support:

- Heltec Mesh Solar, T1, and Tower V2
- Keepteen LT1, LilyGo T-Impulse Plus, Mesh Pocket, and Nano G2 Ultra
- Minewsemi ME25LS01, RAK3401, SenseCAP Solar, and Wio WM1110

The full-sensor `RAK_4631_repeater` image is too large for the safe nRF52 in-place update limit. Use
`RAK_4631_repeater_lora_ota_no_external_sensors` when LoRa OTA is required. That target removes optional
external environmental/GPS sensor packages, but retains the RAK4631's built-in battery-voltage reading,
battery telemetry, and `battery.alert` behavior.

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
each with a **number**, its version, whether it's a full image or a small delta, how many nodes have it,
and how recently it was seen. For example:

```
Updates 1/1 (2 src) - `ota get <#>`:
 1) v1.2.3 delta [yours] 3n 5s
 2) v1.2.0 full [other hw] 1n 12s [downloading]
```

Each row shows the version, full-vs-delta, **whether it fits your node**, how many nodes have it, and how
long ago it was seen. The fit marker:

- **[yours]** - built for your exact hardware **and** role; safe to install.
- **[other hw]** - a different board or role (e.g. a companion image, or another board). Don't install it.
- **[?]** - can't tell (a build with no target id set, e.g. a bare IDE build rather than a release build).

Run it again after a few seconds - discovery happens in the background, so the list fills in. Nothing is
downloaded yet; this is just looking around. Two updates fit in each remote CLI reply; use `ota ls 2`,
`ota ls 3`, and so on for later pages. The displayed update numbers remain global across pages.
(`ota neighbors` / `ota updates` also work.)

### 3. Download an update

Pick one from the list by its **number**, and say **where** to put it:

```
ota pull 1 flash            # stage it in this node's flash, to install here
ota pull 1 folder           # capture it onto a connected motatool folder as <id>.mota (don't install here)
ota pull 1 folder validate  # same capture, warm-started from a motatool --seed build (much faster; below)
```

The destination is required - `ota pull 1` on its own just shows the choices. **`flash`** is always
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
into the new version**. If the check fails, it tells you why and does **not** install. (If you haven't
added the signer's key, an unsigned/untrusted image will only install with this explicit command - never
automatically.)

After it reboots, run `ota status` to confirm the new version.

### 5. If something goes wrong

- A download that stalls or gets interrupted just **resumes** later, or you can `ota cancel` and try again.
- An internal-flash **nRF52** that still runs but reports `no EndF` can use the pre-provisioned rescue path
  if its physical EndF is intact and only app-side validation is failing. Fetch the exact in-place delta,
  obtain its 16-hex-digit `base_hash` from the package metadata, then run:

  ```text
  ota rescue install <base_hash16>
  ```

  This is not a force option. It refuses a normally valid EndF, a different package hash, hardware or
  target mismatch, corrupt payload, and invalid/untrusted signatures. The bootloader independently hashes
  the running app and rejects a wrong base before writing the app. If the physical EndF is absent or this
  command was not already in the running firmware, recover over USB.
  Release chains should put this command in their first bridge and keep it in every bridge after that.
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

ota config autoinstall trusted  # auto-INSTALL a downloaded update IF it's signed by a key you trust
ota config autoinstall off      # never auto-install (default)

ota config advert 1440          # re-advertise every N minutes while temp radio is running
ota config advert 0             # only advertise when a temp-radio window starts

ota config hops 3               # how far OTA travels: accept from / relay up to N repeater hops (default 3)
ota config hops 0               # only exchange OTA with directly-connected nodes (never relay)

ota config                      # show the current settings
```

Recommended for most people: leave both **off** and update by hand. Use `autoinstall trusted` only once
you've added the signer's key (next section) and you trust them to push updates unattended.

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

Only updates signed by a trusted key are eligible for auto-install. Manual `ota install` still lets you
install anything yourself, on your own responsibility.

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
- **Building your own** with the `mota` packaging tool - see [tools/mota/README.md](../tools/mota/README.md)
  (this is for people distributing updates, not everyday operators).

---

## Quick reference

| I want to... | Command |
|---|---|
| List all commands | `ota help` |
| See my firmware + any download | `ota status` (or just `ota`) |
| Admin: ids/hashes + serving + policy | `ota stats` (admin-only remotely) |
| Find updates nearby | `ota ls` |
| Download update #1 for installation | `ota get 1 flash` |
| Cancel a download | `ota cancel` |
| Install a finished download | `ota install` |
| Recover app-side `no EndF` on internal nRF52 | `ota rescue install <base_hash16>` |
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
