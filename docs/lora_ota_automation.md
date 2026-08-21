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
privileged install command. Do the first nRF52 bootloader installation over USB;
an already-capable XIAO QSPI repeater may later use the separate, manual
[`ota bootloader install`](ota_nrf52_qspi.md#explicit-xiao-bootloader-updates-over-lora)
workflow.

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
the mOTA data framing. On an nRF52 full Companion, the script uses that mode
briefly for `ota status` and TempRadio commands. It then closes the CLI and
starts `motatool`, whose existing `ota folder on` preamble switches the same
USB port into exclusive mOTA mode. On an ESP32 Full Companion with serial
folder support, `motatool --companion-terminal` keeps the ASCII session open
while the same link carries framed folder requests. BLE remains available.

## Destination requirements

| Destination | Package installed | One-time prerequisite | Raw ZIP handling |
| --- | --- | --- | --- |
| ESP32 | Full application image | OTA-enabled image with an A/B partition table | Builds a full mOTA from the matching non-merged application `.bin` |
| nRF52, internal flash | In-place delta | Exact-board OTAFIX bootloader with mOTA apply support | Requires `--base` with the exact image currently running |
| nRF52 repeater, external QSPI | Full image or in-place delta | QSPI-aware exact-board OTAFIX bootloader and matched repeater build | Builds a full mOTA; adding `--base` requests a delta |
| MeshTower V2 nRF52, microSD | Full image or in-place delta | SD-aware exact-board OTAFIX bootloader and compatible card | Builds a full mOTA; adding `--base` requests a delta |

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
  --initstring '+++MESHCORE-TERM-START' \
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

The default TempRadio tuple is:

```text
909.950,250,5,5,120
```

The test default is 250 kHz bandwidth, SF5, and CR5. The frequency is only a
North American example: choose a legal frequency supported by every
participating radio and appropriate to your location. Older radios that do not
support SF5 require a complete replacement tuple passed with `--temp-radio`.

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
`--source-cli-tcp`. The source-only Companion never stages or installs the
image itself; it streams the host folder to other nodes over LoRa. See the
[full Companion guide](./companion_radio_full.md) for manual operation and
interface details.

An nRF52 `companion_radio_full` uses one USB source port sequentially. The
runner automatically wraps local control commands in the terminal tokens, and
unmodified `motatool` switches that port into mOTA mode when seeding starts:

```bash
./tools/lora_ota/lora_ota.sh ./release.mota "Remote Target" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

If the source is already on the exact TempRadio tuple through a scheduled or
manual operation, `--source-already-temp` lets a TCP source run without a raw
CLI link. The script cannot verify, extend, or shorten that source window, so
leave a comfortable time margin.

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
  `--leave-controller-radio` deliberately preserves the TempRadio topology.
- `--allow-non-upgrade` deliberately permits the same or an older version.
- `--replace-active-download` deliberately discards a different update already
  downloading or staged on the target. Without it, that update is preserved.
- `--source-shares-controller` is for a Full Companion whose USB Binary API is
  the controller while its TCP port `5001` is the source. It verifies that the
  source's port-`5000` public key equals the controller key. The Binary API
  first moves the shared physical radio, port `5002` then enables the local OTA
  egress gate, and cleanup sends `normalradio` before restoring the saved
  Binary radio tuple.
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
   OTA-enabled raw CLI or a source-only full Companion control interface.
2. Authenticate to the target and query its target ID, hardware, running body
   hash, firmware version, bootloader version, and nRF52 bootloader
   capabilities.
3. Select or build one compatible **v2 application** mOTA and verify all block
   hashes, Merkle root, full-image hash where applicable, identity fields,
   signature, codec, base, and the firmware's 1024-byte maximum block size.
   Version-3 bootloader packages are refused before any target state changes.
4. Save the controller's normal radio tuple and show the confirmation prompt.
5. Start TempRadio on the target, then far-to-near relays, then the source;
   finally switch the controller to the same tuple and read it back. The runner
   rejects a TempRadio window that cannot cover setup, seeder startup,
   discovery, the transfer timeout, final polling, and install checks.
6. Start `motatool serve`, discover the exact eight-hex manifest ID, request
   `ota pull <id> flash`, and poll until that same ID reports ready. A seeder
   process exit stops the run immediately. For `--no-install`, schedule all
   script-controlled nodes back to their normal radios before restoring the
   controller, unless `--leave-controller-radio` was requested.
7. Recheck that exact ID, give the target a short final TempRadio safety window,
   and request `ota install`. Then shorten each relay's TempRadio window so the
   normal multi-hop route returns, stop the seeder, shorten the source window,
   restore the controller, and probe `ota self` at 10 and 20 seconds instead of
   sleeping for 90 seconds. The exact new body hash is the readiness signal;
   only then does the runner require the exact package version. A relayed run
   continues the 10-second probes through the mandatory relay-return window. A
   source supplied with `--source-already-temp` is never modified.
   `--leave-controller-radio` moves the controller back to TempRadio only after
   this normal-channel verification.

Remote replies are matched only after queued messages have been drained and
only when they come from the intended contact and fit the command. A ready
status for another manifest ID is an error, never permission to install it.

## Transmission loss and retries

Read-only and replay-safe transmissions retry up to three times. Three retries
or 90 seconds, whichever comes first, opens a 10-second stop-or-continue
prompt. Continue is the default on timeout, Enter, and unattended input, so a
temporary outage does not silently abandon a resumable transfer. Enter `s` or
`stop` to end the run; Ctrl-C also remains immediate.

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

The working directory is retained and printed at exit. It contains the exact
served mOTA, `motatool-serve.log`, extracted build inputs when needed, and
`controller-radio.txt`. It contains no saved admin password.

## Interruption and recovery

Ctrl-C stops the seeder, detaches its serial folder, makes one best-effort
request to shorten a source TempRadio window started by the script, and attempts
to restore the controller. The target and relays remain on TempRadio only until
their bounded windows end; rebooting also restores their saved radio settings.
A partial download remains safe. Once the target is reachable again (after its
TempRadio window ends, or after putting the controller back on that tuple),
rerunning the same package recognizes its manifest ID and resumes the existing
session instead of clearing it.

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

If you stop during final confirmation, reconnect on the node's normal channel
and run `ota self` and `ver`. A completed run returns success only when
`ota self` reports a valid new body hash and `ver` exactly matches the package;
an unverified install returns status 2. Do not immediately replace a staged
image: the default active-download guard preserves it until you explicitly use
`--replace-active-download` or run `ota cancel`.

Exit status is `0` for success, `2` for a validation or operational error, and
`130` for Ctrl-C.
