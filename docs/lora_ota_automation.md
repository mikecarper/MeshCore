# Scripted LoRa OTA from start to finish

The dedicated [RAK3401 chain report](rak3401_mota_chain.md) records the
physical failures of withdrawn migrations and the physically qualified compact
nine-step replacement. Its runner blocks the withdrawn chains and pins every
accepted bridge image by SHA-256.

[`tools/lora_ota/lora_ota.sh`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/lora_ota/lora_ota.sh) and
[`tools/lora_ota/lora_ota.ps1`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/lora_ota/lora_ota.ps1) automate a
MeshCore LoRa firmware update from a release `.zip` or ready `.mota`. They
identify the destination, validate the hardware and running firmware, prepare
the right container, move the participating nodes to a temporary radio
channel, serve and monitor the download, request installation, restore the
controller, and check the rebooted node.

The script cannot install the destination's first OTA-capable firmware. It is
also deliberately application-only: it rejects both v3 bootloader containers
and a v2 container carrying the bootloader flag, and it never sends the
privileged install command. Do the first nRF52 bootloader installation over USB.
An already-capable, explicitly supported internal-flash, XIAO QSPI, or exact
MeshTower V2 microSD repeater may later use the separate manual
[`ota bootloader install`](ota_nrf52_bootloader_update.md) workflow.

## Required topology

The reliable serial topology uses two local radios:

```text
                                      authenticated admin commands
computer -- MeshCore binary API --> controller Companion -------------------+
   |                                                                         |
   +-- raw text CLI + mOTA seeder --> OTA source ---- LoRa OTA blocks ----> target
                                                        |                    ^
                                                        +-- relay(s) --------+
```

- **Controller:** a Companion connected through serial, TCP, or BLE. The
  script uses `meshcli` to send remote admin commands to the target and changes
  the controller's live radio parameters during the transfer. A serial
  Companion stays in its normal **Binary** USB mode at 115200 baud.
- **OTA source:** an OTA-enabled repeater/FULL node whose USB port is a raw
  text CLI, an ESP32 `companion_radio_full` using WiFi ports 5001 and 5002, or
  an nRF52 `companion_radio_full` whose USB port switches between Binary,
  terminal control, and exclusive mOTA seeding.
- **Target:** an OTA-enabled ESP32 or nRF52 node present in the controller's
  contact list. Its admin password is required.
- **Relays:** optional. They do not need to install OTA themselves, but every
  relay on the path must be running current firmware and have an overlapping
  TempRadio window.

One serial port cannot serve both controller roles: `meshcli` must keep
reopening the controller while `motatool` owns the source port. The script
rejects an attempt to use the same port for both.

The USB ASCII switch (`+++MESHCORE-TERM-START`) is the local control path, not
the mOTA data framing. On an nRF52 dual-CDC Full Companion, the script uses
that mode briefly for `ota status` and TempRadio commands. It then closes the
CLI and starts `motatool`; the exact `ota folder on` preamble switches the same
USB interface `00` into exclusive mOTA mode from either startup ASCII or
Binary Companion mode. If USB logging was enabled and the Full Companion
rebooted, its separate interface `02` continues to provide plaintext logging
and is not a controller or source port. With default logging off, interface
`02` is not enumerated. BLE remains available.

ESP32 Full Companion normally uses its dedicated TCP seeder on port 5001.
ESP32 builds that also retain serial folder support use the same exclusive USB
mOTA ownership state as nRF52. The exact `ota folder on` line transfers the USB
port from startup ASCII or idle Binary Companion mode before binary mOTA frames
begin; neither console mode shares that port until the folder detaches.

## Destination requirements

| Destination | Package installed | One-time prerequisite | Raw ZIP handling |
| --- | --- | --- | --- |
| ESP32 | Full application image | OTA-enabled image with an A/B partition table | Builds a full mOTA from the matching non-merged application `.bin` |
| nRF52, internal flash | In-place delta | Exact-board OTAFIX bootloader with mOTA apply support | Requires `--base` with the exact image currently running |
| nRF52 repeater, external QSPI | Full image or in-place delta | QSPI-aware exact-board OTAFIX bootloader and matched repeater build | Builds a full mOTA; adding `--base` requests a delta |
| MeshTower V2 nRF52, microSD | Full image or in-place delta | SD-aware exact-board OTAFIX bootloader and compatible card | Builds a full mOTA; adding `--base` requests a delta |

MeshTower V2 microSD application and bootloader containers require an Ed25519 signature from a key in the
device allowlist. Supply `--sign-key` when the runner must build a container, and install the BLM2-capable
SD-aware bootloader locally before attempting application or bootloader OTA. Preview.12 requires USB/BLE
DFU or SWD; see [MeshTower V2 microSD LoRa OTA](ota_meshtower_v2_sdcard.md).

The firmware inside a raw ZIP must have a valid MeshCore `EndF` trailer. An
ESP32 merged/factory image is not an application image and is rejected. A
generic vendor DFU ZIP may also be unusable if it does not contain the raw
EndF-bearing `.hex` or `.bin`.

For an internal-staging nRF52, the exact base image is irreducible information.
The node reports its eight-byte body hash, but that hash cannot reconstruct the
firmware bytes needed to create a delta. Keep the `.pio/build/ENV/firmware.hex`
that was actually flashed. A matching filename or version alone is not enough.

## 1. Install the host tools

Install Python 3.10 or newer, [Rust](https://rustup.rs/), Git, the official
[`meshcore-cli`](https://github.com/meshcore-dev/meshcore-cli), and the official
[`motatool`](https://github.com/vk496/motatool).

On Bash:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install meshcore-cli

git clone https://github.com/vk496/motatool.git
cargo install --path ./motatool

meshcli -v
motatool --version
```

On PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
pipx install meshcore-cli

git clone https://github.com/vk496/motatool.git
cargo install --path .\motatool

meshcli -v
motatool --version
```

Restart the shell if `pipx` or Cargo reports that it changed `PATH`.

## 2. Identify and test both local links

List serial devices:

```bash
meshcli -l
```

The examples below assume `/dev/ttyACM0` is the controller and
`/dev/ttyACM1` is the OTA source. On Windows they might be `COM7` and `COM8`.
Close picocom, a serial monitor, the phone app, and any other program holding
either link.

Test the controller's binary API:

```bash
meshcli -s /dev/ttyACM0 -b 115200 ver
```

For an ordinary raw-text source, test its OTA support:

```bash
meshcli -r -s /dev/ttyACM1 -b 115200 "ota status"
```

The command must print an `OTA | ... target:XXXXXXXX` status.

For an nRF52 full Companion, open the source port with terminal mode selected
automatically:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring $'+++MESHCORE-TERM-START\r' \
  /dev/ttyACM1
```

Run `ota status`. It must report `OTA seeder`, `install:disabled`, and target
`00000000`; send `+++MESHCORE-TERM-STOP` before closing the terminal. The
automation detects and performs this token-wrapped preflight itself, so no
extra command-line option is needed.

For an ESP32 full Companion, test its separate WiFi control console instead:

```bash
printf 'ota status\r\n' | nc 192.168.1.50 5002
```

It must report `OTA seeder`, `install:disabled`, and target `00000000`.

Changing a terminal to 57600 baud does not select ASCII mode. USB Companion
builds and the normal raw management CLI use 115200 unless a particular build
was explicitly configured otherwise.

## 3. Check the destination once

The destination must be in the controller's contacts and remotely reachable
on the normal channel. The script runs these authenticated checks itself:

```text
ota status
get bootloader.ver
ota self
ota stats
```

The script uses `get bootloader.ver` to distinguish ESP32 from nRF52 and, for
nRF52, report the installed bootloader version. It then requires `ota self` to
report `bootloader: apply OK`, `bootloader: QSPI apply OK`, or
`bootloader: SD apply OK` and checks the
reported bootloader ABI and codec mask against the selected package. If the
version command is unavailable on older firmware, the script warns and falls
back to the legacy `ota self` platform marker. If an nRF52 bootloader lacks
the required capabilities, install the exact-board OTAFIX bootloader first.
Current firmware also reports `maxblk:2048` near the front of both OTA replies.
The runner treats a missing marker as the deployed 1 KiB receive limit, rejects
a ready 2 KiB package for such a target before transfer, and passes the live
limit to `motatool --block-size` when it builds from raw firmware. Thus updating
an older target from a release ZIP produces a compatible 1 KiB package while a
current target retains the 2 KiB application default.
`ota stats` is only an optional EndF version probe. It uses one bounded retry
cycle; unsupported firmware or a lost reply falls back directly to the
required `ver` command instead of entering an operator continuation loop.

The default TempRadio tuple is:

```text
909.950,250,5,5,120
```

The test default is 250 kHz bandwidth, SF5, and CR5. The frequency is only a
North American example: choose a legal frequency supported by every
participating radio and appropriate to your location. Older radios that do not
support SF5 require a complete replacement tuple passed with `--temp-radio`.

Before that long window is allowed, the live runner performs a mandatory
independent three-minute rehearsal. Its exact normal-channel `ota status` and
`ota self` identity proof gets one shared four-minute, read-only budget before
any schedule, radio override, seeder, or transfer is armed; this tolerates a
marginal link without consuming or extending a live lease. It advances a stale
managed controller with exact readback and treats a source terminal's
whole-minute display as an uncertainty window, never as an exact epoch. A
source minute overlapping the host is preserved; a completely stale minute is
pinned to a guarded future value and read back once. Neither clock is moved
backward, and the runner stops if a managed clock cannot be proven within the
ten-minute limit. Each remote participant must expose an empty `tempradioat`
schedule. The runner projects one fixed start/end interval into each
participant's independently sampled RTC, sends each mutation once, proves
every exact identity on the temporary tuple, waits for natural expiry, and
proves the complete normal path again. A lost schedule reply is resolved by
those on-air proofs; it is never blindly replayed with a fresh duration.

The published RAK3401 `v1.16.7-c1caa5ad` LoRa-OTA image includes
`get/set/del tempradioat` and can take this safe first step. A truly older or
reduced build without fixed TempRadio scheduling is rejected before mutation;
bootstrap it locally or with another explicitly controlled maintenance path.
The automation does not substitute an immediate `tempradio` command whose
first delivery could remain queued after cleanup.

### RXPS handling during TempRadio

RXPS improves receive performance per unit of radio-on time, so the runner
keeps the destination's saved RXPS preference enabled whenever the selected
fast tuple has a qualified timing window. Before changing any radio, it reads
`ver` from the destination, controller, source, and every named relay. It treats
v1.17.1.5 as the first forward contract in which every SF5-SF8 transmission,
including a retry, uses the same tuple-selected physical preamble: normally 32
symbols, 64 only when 32 cannot enable RXPS, and 128 only when neither 32 nor
64 can. A saved RXPS level is also safely retuned after a radio change. An
older or unparseable destination version fails closed: the runner temporarily
sends `set radio.rxps off` instead of assuming an ad-hoc build contains the
timing fixes. Automation deliberately treats the version as a wire-format
contract.

For the default SF5/BW250 tuple, the runner keeps destination RXPS on only when
the destination and every possible sender are v1.17.1.5 or newer. It does not
overwrite an existing level-based preference. Current firmware retunes that
preference against the tuple-selected 64-symbol wire preamble; the qualified boundary is
effective level 8, preamble 64 (`1252 / 6424 us`). A manually configured node
may save `set radio.rxps level 8 preamble 32` before entering TempRadio: when
32 symbols cannot cover the TCXO transition, firmware safely selects the real
64-symbol preamble. If the saved setting uses fixed manual timings, or even one
participant is older or unknown, RXPS is temporarily off. This avoids the
receive gap that a 32-symbol sender or an unretuned manual window would create.

The complete qualified SX1262+TCXO policy is:

| TempRadio SF/BW | Saved reference setting | Qualified boundary | Automation |
| --- | --- | --- | --- |
| SF7/BW500 | `level 7 preamble 32` | 7 / 32 | RXPS on for a current destination |
| SF6/BW250 | `level 7 preamble 32` | 7 / 32 | RXPS on for a current destination |
| SF5/BW125 | `level 7 preamble 32` | 7 / 32 | RXPS on for a current destination |
| SF5/BW250 | `level 8 preamble 32` | 8 / 64 | RXPS on only when every participant follows the 64-symbol contract |
| SF6/BW500 | `level 8 preamble 32` | 8 / 64 | RXPS on only when every participant follows the adaptive-preamble contract |
| SF5/BW500 | `level 8 preamble 32` | 8 / 128 | RXPS on only when every participant follows the adaptive-preamble contract |
| SF5/BW62.5 | `level 10 preamble 16` | 10 / 16 | RXPS on for a current destination |
| Unqualified tuple | none | continuous RX | RXPS temporarily off |

If RXPS was already off, the runner leaves it off. Otherwise it writes the
original level, preamble assumption, receive/sleep periods, and temporary
decision to protected
`target-rxps-settings.json` in the run's working directory, verifies the
temporary state, and restores the exact original setting after the target is
back on its normal radio. Current firmware exposes this complete state through
`get radio.rxps.config`; a legacy reply has only on/off and periods, so the
runner can restore those periods but cannot reconstruct an unreported saved
level. A radio change later recalculates from a preserved saved minimum, so
moving back to a slower tuple returns to the operator's saved level.

The OTA source has a stricter policy than the destination. For every source
with a managed serial or TCP CLI, the runner reads and retains its exact RXPS
preference, using the legacy fixed-period query only when the detailed query is
unavailable. It builds and verifies the package, completes the read-only target
checks, and obtains confirmation while that preference remains unchanged.
Immediately before the first radio mutation, it reads the source RXPS state
again, disables RXPS, and verifies the readback. Source RXPS stays off through
catalog serving,
download, installation, and post-install identity verification. Cleanup first
proves that the source has returned to its normal radio, then restores and
verifies the exact saved level/preamble or fixed-period state once. A source
whose RXPS state cannot be read, disabled, or restored safely fails closed.
If current firmware explicitly rejects an RXPS disable or restore with `radio
busy; retry`, the runner retries that idempotent mutation at staggered
210–378 ms intervals. All 32 delays are distinct and contribute about 9.4
seconds of waits; source-CLI command round-trip time is additional. This avoids
repeatedly sampling one radio phase while retaining a strict attempt cap. Other
rejections are not replayed.

## 4. Run an ESP32 update

The ZIP can contain a compatible ready `.mota` or the exact board-and-role
non-merged application `.bin`:

```bash
export MESHCORE_ADMIN_PASSWORD='target-admin-password'

./tools/lora_ota/lora_ota.sh ./release.zip "Roof ESP32" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

The script shows the detected target, hardware, running hash, chosen package,
version, manifest ID, and action before asking for confirmation. For an
unattended job, add `--yes`:

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Roof ESP32" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1 \
  --yes
```

PowerShell equivalents:

```powershell
$env:MESHCORE_ADMIN_PASSWORD = 'target-admin-password'

& .\tools\lora_ota\lora_ota.ps1 '.\release.zip' 'Roof ESP32' `
  --controller-serial COM7 `
  --source-serial COM8

& .\tools\lora_ota\lora_ota.ps1 '.\release.mota' 'Roof ESP32' `
  --controller-serial COM7 `
  --source-serial COM8 `
  --yes
```

Prefer the environment variable or the interactive password prompt. Passing
`--password` works, but the wrapper's own command line may be visible to other
local processes. The runner keeps the password out of child `meshcli` command
lines and removes its protected temporary command file after each call.

## 5. Run an nRF52 update

If the input ZIP already contains a compatible in-place delta `.mota`, no
base argument is needed: its embedded base hash is compared with the live
node. If the ZIP contains raw new firmware, supply the exact running image:

```bash
./tools/lora_ota/lora_ota.sh ./nrf52-new-release.zip "Hill nRF52" \
  --base ./firmware-that-is-running.hex \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

```powershell
& .\tools\lora_ota\lora_ota.ps1 '.\nrf52-new-release.zip' 'Hill nRF52' `
  --base '.\firmware-that-is-running.hex' `
  --controller-serial COM7 `
  --source-serial COM8
```

Before building a delta, the runner proves that the base's target ID,
hardware identity, firmware version when available, and `EndF` body hash match
the live destination. It then asks `motatool` for codec 2, the nRF52 in-place
format. The normal workspace is `0x98000`.

For a QSPI-backed repeater or the SD-backed MeshTower V2 target, a raw ZIP
becomes a full image without `--base`. Supplying an exact base requests a
smaller in-place delta and automatically selects the conservative external
workspace `0xC6000`, which is safe for S140 v6 and v7 application layouts. An explicit
`--inplace-memory` overrides the automatic value.

The live runner detects QSPI from `ota self` (`QSPI apply OK`) or `ota status`
(`bl:QSPI`) and refuses an explicit `QSPI store:ERR 0K` report even when the
bootloader itself advertises QSPI apply support. Offline `--prepare-only` runs
must supply `--nrf-qspi`; do not use that switch for a board that only exposes
QSPI pins or uses the chip as a
Companion filesystem. The application and bootloader must both be from the
matched repeater list in [the nRF52 QSPI guide](ota_nrf52_qspi.md).

## 6. Add intermediate relays

List relays from farthest to nearest so each command is sent before its route
moves to TempRadio. A bare relay name uses the destination password; use
`NAME=PASSWORD` when it differs:

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Remote Target" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1 \
  --relay "Far Relay=far-password" \
  --relay "Near Relay=near-password"
```

PowerShell uses the same arguments:

```powershell
& .\tools\lora_ota\lora_ota.ps1 '.\release.mota' 'Remote Target' `
  --controller-serial COM7 `
  --source-serial COM8 `
  --relay 'Far Relay=far-password' `
  --relay 'Near Relay=near-password'
```

## Other connection choices

The controller can use any one of:

```text
--controller-serial PORT
--controller-tcp HOST[:PORT]       # default port 5000
--controller-ble ADDRESS_OR_NAME
```

An ESP32 FULL/repeater source can serve over its dedicated WiFi seeder port
while its raw USB CLI is used to start TempRadio:

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Remote Target" \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.50:5001 \
  --source-cli-serial /dev/ttyACM1
```

An ESP32 `companion_radio_full` uses WiFi for both dedicated source links:

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Remote Target" \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.50:5001 \
  --source-cli-tcp 192.168.1.50:5002
```

Port 5002 defaults automatically when it is omitted from
`--source-cli-tcp`. Current Full Companion firmware exposes its complete
role-specific text terminal there; the runner uses only `ota`, `tempradio`,
and `normalradio`, and remains compatible with older bounded port-5002
consoles. The source-only Companion never stages or installs the image itself;
it streams the host folder to other nodes over LoRa. See the
[full Companion guide](./companion_radio_full.md) for manual operation and
interface details.

An nRF52 `companion_radio_full` uses one USB source port sequentially. The
runner automatically wraps local control commands in the terminal tokens, and
unmodified `motatool` switches that port into mOTA mode when seeding starts:

The wrapper sends STOP, then START, before each command. This makes fallback
independent of whether a prior raw probe left an unobservable USB-UART
connection in ASCII or Binary mode. Seeder startup is reported only after the
verbose device log contains its `COUNT` acknowledgement; an immediate device
`ERR` or a missing acknowledgement fails during startup instead of surfacing
later as a catalog timeout.

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Remote Target" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

If the source is already on the exact TempRadio tuple through a scheduled or
manual operation, `--source-already-temp` lets a TCP source run without a raw
CLI link. The script cannot verify, extend, or shorten that source window, so
leave a comfortable time margin. It also cannot inspect or change that
unmanaged source's RXPS state; disable source RXPS yourself before starting
the run and restore it only after returning the source to its normal radio.

Use `--controller-baud` or `--source-baud` only for a build whose corresponding
interface is genuinely configured to another speed.

## Package selection and safety gates

For a ZIP, the runner first examines every `.mota` without extracting paths.
It keeps only packages matching the live target, hardware, base, platform,
codec, and bootloader capabilities. It chooses the newest compatible version
and prefers a delta over a full image at the same version. If equally suitable
files differ, select one explicitly:

```text
--zip-member path/inside/archive/update.mota
```

If no ready mOTA is usable, it searches `.bin` and `.hex` members for a valid,
matching `EndF`, then builds the platform-appropriate container. Every result
is structurally checked by the runner and independently passed through
`motatool verify` before any radio changes. Direct firmware and mOTA inputs, as
well as individual ZIP members, are rejected above 64 MiB before being loaded.

Useful controls:

- `--public-key signer.key.pub` requires a particular Ed25519 signer during
  verification.
- `--sign-key signer.key` signs a newly built container.
- `--no-install` downloads and verifies the image but leaves it staged. By
  default the runner then schedules the target, relays, and a script-configured
  source back to their normal radios. Combining it with
  `--leave-controller-radio` deliberately preserves the destination, relays,
  and separate controller on the TempRadio topology; a managed source is still
  returned to normal so its exact RXPS setting can be restored.
  If the version gate required RXPS off, it stays off while that topology is
  preserved; use `target-rxps-settings.json` to restore it only after sending
  `normalradio`.
- `--allow-non-upgrade` deliberately permits the same or an older version.
- `--replace-active-download` deliberately discards a different update already
  downloading or staged on the target. Without it, that update is preserved.
- `--source-shares-controller` is for a Full Companion whose USB Binary API is
  the controller while its TCP port `5001` is the source. It verifies that the
  source's port-`5000` public key equals the controller key, then binds the
  fresh port-`5002` Full Terminal banner's complete public key to that Binary
  identity and challenges it with the supported `ver` command. It does not
  assume the Full Terminal implements repeater-only `get public.key`. Port
  `5002` uses a bounded local `tempradio` override to move the shared physical
  radio without overwriting its saved normal tuple; the Binary API remains the
  authenticated transport. Cleanup sends local `normalradio`, proves that
  override inactive, and then reasserts the saved Binary tuple. It cannot be
  combined with
  `--leave-controller-radio`,
  because exact source RXPS restoration requires that shared physical radio to
  be back on its verified normal tuple.
- `--require-system-watchdog-off` checks `get system.watchdog` immediately
  before every `ota install` transmission and refuses installation unless the
  destination reports `> off`. Use it for nRF52 chains whose bootloader cannot
  service an application watchdog inherited across reset.
- Managed `--relay` nodes have their original `rxdelay` and `txdelay` captured
  in the work directory. During transfer the runner verifies `rxdelay 0` and
  an airtime-scaled `txdelay 0.3` (override with `--relay-txdelay`), then
  restores both values before the relay leaves TempRadio.
- `--work-dir PATH` chooses a new, non-existent work directory.
- `--meshcli PATH` and `--motatool PATH` select binaries not on `PATH`.
- `--package-build-timeout SECONDS` bounds local mOTA generation. It defaults
  to 3600 seconds. Run detools, delta/compression, and raw-firmware package
  generation on a workstation or build VM, then transfer the completed,
  hash-verified `.mota` to a Pi-class radio host. The Pi should perform only
  lightweight identity-gated hardware I/O and serve that finished artifact;
  preparation still occurs before any radio state changes.
- `--debug` prints redacted child commands, timeouts, process status, stdout,
  and stderr. Admin passwords are never printed, but node names, addresses,
  paths, and command replies can still be sensitive; share the log carefully.

For offline package preparation only:

```bash
./tools/lora_ota/lora_ota.sh ./release.zip offline \
  --prepare-only \
  --platform nrf52 \
  --target-id 1234ABCD \
  --target-base-hash 0011223344556677 \
  --target-hw Heltec_T114 \
  --base ./firmware-that-is-running.hex
```

Live operation is safer because the script obtains these values directly from
the destination.

## What happens during a run

1. Validate the input paths and host tools, then prove the source is either an
   OTA-enabled raw CLI or a source-only full Companion control interface. Read
   and durably save a managed source's exact RXPS preference without changing
   it.
2. Before sending any remote packet, prove the managed source has no active,
   pending, or fixed TempRadio work, then gate the source and controller
   clocks. Advance a stale exact clock to host time; for a minute-resolution
   source, preserve an overlapping minute or use one guarded forward value
   with readback. Preserve a small future lead and fail closed above the
   ten-minute drift limit. Then
   authenticate to the target, query its target ID, hardware, running body
   hash, firmware version, bootloader version, and nRF52 bootloader
   capabilities, and save the controller's normal radio tuple.
3. Select or build one compatible **v2 application** mOTA and verify all block
   hashes, Merkle root, full-image hash where applicable, identity fields,
   signature, codec, base, and the live firmware's reported maximum block size
   (1 KiB for legacy replies without `maxblk`, currently 2 KiB otherwise).
   Version-3 bootloader packages are refused before any target state changes.
4. Read every participant's version, save the destination's RXPS state,
   select the qualified destination policy, and show the confirmation prompt.
5. Run the fixed-window three-minute rehearsal described above. This phase
   does not disable RXPS, start a seeder, request a pull, or install anything;
   the long transfer remains completely unarmed until temporary and normal
   reachability both pass.
6. Fresh-read, disable, and verify source RXPS; apply and verify the
   destination RXPS policy; then start TempRadio on the
   target, far-to-near relays, and source. A separate controller is moved and
   read back through Binary; a shared Full Companion instead schedules its
   bounded local override while Binary remains the transport. The runner
   rejects a TempRadio window that cannot cover setup, seeder startup,
   discovery, the transfer timeout, final polling, and install checks.
7. Start `motatool serve`, discover the exact eight-hex manifest ID, request
   `ota pull <id> flash`, and poll until that same ID reports ready. A seeder
   process exit stops the run immediately. For `--no-install`, schedule all
   script-controlled nodes back to their normal radios before restoring the
   controller, unless `--leave-controller-radio` was requested.
8. Recheck that exact ID, give the target a short final TempRadio safety window,
   and request `ota install`. Then shorten each relay's TempRadio window so the
   normal multi-hop route returns, stop the seeder, shorten the source window,
   restore the controller, and probe `ota self` every 10 seconds through the
   configured readiness window (five minutes by default). The exact new body
   hash is the readiness signal;
   only then does the runner require the exact package version. A relayed run
   continues the 10-second probes through the mandatory relay-return window. A
   source supplied with `--source-already-temp` is never modified.
   `--leave-controller-radio` moves the controller back to TempRadio only after
   this normal-channel verification. Restore the destination's exact original
   RXPS setting after normal-channel identity is proven. A managed source stays
   RXPS-off through that verification and its exact setting is restored only
   after its own TempRadio state is proven inactive.

Remote replies are matched only after queued messages have been drained and
only when they come from the intended contact and fit the command. A ready
status for another manifest ID is an error, never permission to install it.

## Transmission loss and retries

Read-only and replay-safe transmissions retry up to three times. Three retries
or 90 seconds, whichever comes first, opens a 10-second stop-or-continue
prompt when stdin is an interactive terminal. Continue remains the default on
timeout or Enter, so an operator can persist through a temporary outage; enter
`s` or `stop` to end the run, and Ctrl-C remains immediate. With non-interactive
stdin, the runner stops after that finite automatic retry cycle instead of
silently starting another cycle forever. Optional participant-version probes
always use a bounded cycle and degrade to `unknown`; optional destination
`ota stats` falls back directly to `ver`. The mandatory normal-channel
destination baseline is the narrow exception: it may make nine total read-only
attempts but is stopped by one hard four-minute deadline before any TempRadio
lease or OTA mutation exists. Proofs inside the live rehearsal retain the
smaller four-attempt limit.

Commands that change OTA state are reconciled before replay:

- After a lost `ota pull` reply, `ota status` must show the requested manifest
  ID before the runner treats the pull as started. Otherwise the safe retry
  policy applies.
- A lost `ota install` reply is not blindly resent. After a short wait, it is
  sent again only if the target replies that the same manifest is still ready.
  If the target has stopped replying because it may be rebooting, the runner
  restores the normal path and lets post-reboot identity resolve the outcome.
  The target's final three-minute safety window also returns a non-rebooting
  target to the normal channel promptly.

Retries and operator-selected continuation can outlast the original TempRadio
budget. If a bounded window expires, rerun the same package after the nodes
return to their normal channel; the manifest-ID check resumes its partial
download without replacing it.

The working directory is created before a managed source can be changed, then
retained and printed at exit. It contains the exact
served mOTA, `motatool-serve.log`, extracted build inputs when needed, and
`controller-radio.txt`. A managed source also gets a protected
`source-rxps-settings.json` containing its exact original preference and
idempotent restore command. Its contents and directory entry are flushed before
RXPS is disabled. When the destination
started with RXPS enabled, protected `target-rxps-settings.json` records its
manual recovery state. The RAK3401 chain points every nested step at one
chain-root source record, so a rerun after host power loss does not adopt the
temporary RXPS-off state as the original. A retained record is accepted only
for the same managed CLI endpoint. These files contain no saved admin password.
After exact source restoration, a standalone run atomically retires its record;
the chain retains its shared record between steps and retires it only after the
verified endpoint restoration completes.

## Interruption and recovery

Ctrl-C stops the seeder, detaches its serial folder, makes a bounded attempt to
shorten a source TempRadio window started by the script, and attempts to
restore the controller. For a managed source it leaves RXPS off until the
source is proven back on its normal radio, then restores and verifies the
saved source preference. A transient success-path restore failure remains
armed for one more idempotent cleanup attempt. The target and relays remain on
TempRadio only until their bounded windows end; rebooting also restores their
saved radio settings. A normal cleanup restores the destination's exact RXPS
periods. If that remote restore cannot be confirmed, use
`target-rxps-settings.json` after the target returns to its normal channel.
A partial download remains safe. Once the target is reachable again (after its
TempRadio window ends, or after putting the controller back on that tuple),
rerunning the same package recognizes its manifest ID and resumes the existing
session instead of clearing it.

When a chained run has already proved the newly running body hash, its retained
previous package can briefly report `verifying staged blocks` after TempRadio
reactivates the OTA manager. The runner waits only through the configured
discovery timeout, keeps checking source liveness, and accepts only the same
manifest becoming `ready to install` or the manager becoming idle. A changed
ID, failed or incomplete state, or timeout stops the chain. It then proves the
exact installed body again. If that same session is still attached and ready,
the runner detaches it with `ota cancel`; if the manager has become idle, it
sends no cancel. An ordinary-channel `no download` status proves only that the
manager is idle, not that persistent staging was erased. The runner therefore
does not issue or describe an IDLE cancel as durable cleanup. The next chain
transition explicitly re-adopts and proves the expected previous MID before detaching it;
after the final install OTAFIX has consumed the approval word, so any retained
container is inert and is replaced by the next valid pull.

A hard process kill or host power loss cannot run cleanup. Recover a serial
controller using the tuple saved in the printed work directory:

```bash
radio=$(tr -d '\r\n' < ./meshcore-lora-ota-20260807-123456-1234/controller-radio.txt)
meshcli -s /dev/ttyACM0 set radio "$radio"
```

```powershell
$radio = (Get-Content '.\meshcore-lora-ota-...\controller-radio.txt' -Raw).Trim()
meshcli -s COM7 set radio $radio
```

For a managed source, first return it to its ordinary radio, then inspect
`source-rxps-settings.json` and issue its exact `restore_command` through the
same serial or TCP-console endpoint recorded in that file. The command is
idempotent; confirm the full setting with `get radio.rxps.config` before
resuming an update.

If you stop during final confirmation, reconnect on the node's normal channel
and run `ota self` and `ver`. A completed run returns success only when
`ota self` reports a valid new body hash and `ver` exactly matches the package;
an unverified install returns status 2. Do not immediately replace a staged
image: the default active-download guard preserves it until you explicitly use
`--replace-active-download` or run `ota cancel`.

Exit status is `0` for success, `2` for a validation or operational error, and
`130` for Ctrl-C.
