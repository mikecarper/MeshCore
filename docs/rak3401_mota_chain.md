# RAK3401 1W repeater compact LoRa update chain

> Status: **unreleased candidate with physically passed package transitions.**
> On 29-Aug-2026 all ten exact package transitions completed on the target
> RAK3401 with its deployed `0.9.2-OTAFIX2.4` bootloader and a Heltec V4 source at
> 909.950 MHz / 500 kHz / SF5 / CR5. Every intermediate EndF hash matched.
> Independent SWD readback then matched the endpoint application, original
> bootloader, SoftDevice/MBR, and UICR byte-for-byte. The host runner received
> cleanup/recovery fixes after that test and has not had a new clean end-to-end
> physical rerun. Multi-hop and alternate-bandwidth estimates remain planning
> data, not physical qualification.

The candidate is a local qualification artifact, not a GitHub release:

```text
RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip
```

- candidate ZIP SHA-256: `c0b33f4568985e8b2b8dc99411295907212cf2bad21764b6333d5e0ba298fd61`
- inner `SHA256SUMS.txt` SHA-256: `3f8c4af8096b96a4aa6506825c387cc8a06f74d5213a29c9387bd11689546881`
- endpoint image SHA-256: `31c182c888ceb1135e5afb2376610d93cee2e807b556c838e07fd4486c79d095`
- endpoint EndF body hash: `9179B98432895924`

Always pass the exact local path with `--bundle`. The runner has no download
URL for this candidate. `--verify-only` remains the safe default. The hidden
`--accept-test-candidate` override is intentionally absent from `--help` and
remains required until this exact artifact is published with its qualification
record; direct physical success does not silently turn a local file into a
released deployment asset.

## Exact destination

This chain is intentionally specific to one firmware lineage and hardware
target:

- target ID: `2FA509C1`
- hardware: `RAK_3401`
- role: `RAK_3401_repeater_lora_ota_no_external_sensors`
- start: `v1.16.7.0-c1caa5ad`, EndF `71F4026CBE4B8B74`
- endpoint source: MeshCore `fd98bc90f7fb25d5956facd550e227b217abbe95`
- endpoint label: `v1.17.1.5-halo-keymind-cascade-dev-fd98bc90`
- endpoint packed version: `0x01110105`
- deployment target key: `63d8df6387eaffd2e25db7d2a8ad967a65202182a48d681d7e7a9260f917280d`

### Retained and omitted hardware support

Despite the legacy `no_external_sensors` target name, the endpoint retains the
RAK12501 GPS provider and the common INA3221, INA219, INA226, and INA260 I2C
voltage/current monitors. Install RAK12501 in sensor slot A. Do not use slot D
with the RAK13302 1 W radio because the GPS reset/PPS signals overlap the
radio's BUSY/DIO1 wiring.

The reduced profile omits optional external environmental and ranging devices:
AHTX0, BME280, BMP280, SHTC3, SHT4x, LPS22HB, MLX90614, VL53L0X, BME680,
BMP085, RAK12035, and BME680 BSEC. Board radio, display, buttons, battery
telemetry, GPS, and the INA monitor family remain. No additional “remove I2C
temperature” image is needed: the optional I2C temperature/environment drivers
are already in the omitted set, while removing the remaining I2C/INA support
would discard desired voltage/current monitoring and create another firmware
identity without helping this route.

Do not use the chain on another target ID, hardware family, starting image, or
firmware body hash. The runner checks all four.

## Why Preview 5 and the old staging ceiling work

The deployed bootloader is not changed and no package uses the newer expanded
`0xED000` ceiling. Each package remains bottom-aligned below the old
bootloader's `0xD4000` scan ceiling. Its detools workspace is page-aligned and
must satisfy:

```text
0x26000 + inplace_memory <= staged_package_start < 0xD4000
```

The old bootloader already reads and validates that encoded detools geometry
before its first destructive write. The previous application receiver was the
limiting part: it reserved a fixed `0x98000` workspace before accepting a
download. Step 1 therefore remains byte-for-byte identical to the physically
passed 89,844-byte package. Step 2 is a 46,364-byte fixed-workspace package
that installs the compatibility receiver. Later receivers stage above the
real EndF image extent and let the bootloader enforce each package's selected
workspace.

Every package in this candidate is an ordinary application container using
format ABI 2 and in-place codec 2. The historical bridge applications scan the
bootloader capability marker byte-by-byte, so they recognize the released
Preview 5 RAK4631 marker at absolute address `0xFCDEA` even though it is two
bytes off a word boundary. The `fd98bc90` endpoint deliberately retains that
exact Preview 5 application-update fallback. Its separately compiled internal
bootloader-update feature remains unavailable on Preview 5 because privileged
self-update requires a current ABI 3 marker with the exact storage profile.
Installing this chain therefore does not replace the bootloader, and the final
application can still accept a future valid ABI-2/codec-2 application update.

The schema-2 exhaustive search checked 17,402 candidate geometries and found
121 feasible forward edges in the declared 33-image inventory. Fourteen
ten-package routes tied for the minimum package count; the selected route has
the smallest total container size among them. That minimum applies to the
declared inventory, not every conceivable firmware image. The selected route
is:

| Step | From | To | Workspace | Package | Margin |
|---:|---|---|---:|---:|---:|
| 1 | 1.16.7.0 | 1.16.7.9 | `0x98000` | 89,844 | 0 |
| 2 | 1.16.7.9 | 1.16.7.10 | `0x98000` | 46,364 | 40,960 |
| 3 | 1.16.7.10 | 1.16.8.7 | `0x91000` | 106,029 | 12,288 |
| 4 | 1.16.8.7 | 1.16.9.105 | `0x89000` | 149,927 | 0 |
| 5 | 1.16.9.105 | 1.16.9.110 | `0x8E000` | 111,312 | 16,384 |
| 6 | 1.16.9.110 | 1.16.9.113 | `0x8F000` | 120,624 | 4,096 |
| 7 | 1.16.9.113 | 1.16.9.117 | `0x8B000` | 139,637 | 0 |
| 8 | 1.16.9.117 | 1.16.10.0 | `0x7E000` | 174,202 | 20,480 |
| 9 | 1.16.10.0 | 1.17.1.3 | `0x6B000` | 271,569 | 0 |
| 10 | 1.17.1.3 | 1.17.1.5 | `0x74000` | 173,334 | 61,440 |

Total mOTA transfer data is 1,382,842 bytes. `ROUTE.json`, `CHAIN.csv`, and
`validation-results.json` in the bundle pin the exact geometry and image hash
for every transition.

## Host requirements

Install Python 3.9 or newer, `meshcli` 1.6.0 or newer, the current `motatool`,
and an OTA-enabled Full Companion or repeater that can seed mOTA files.

## Verify offline

No password or device is needed:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

When given the ZIP, the runner checks its pinned outer hash and binds the
extraction cache to that exact archive. An explicitly supplied extracted root
starts at the pinned inner checksum list. In either case it checks complete
inner coverage, all ten manifests, continuity, image anchors, both recovery
images, and every container with `motatool`. Passing this command means offline
and simulator qualification only; it does not unlock live use.

## Current guarded reproduction command

These are the same direct-link arguments used for the physical transition run,
but the current host runner also contains later cleanup and recovery fixes.
That revised runner has not been cleanly rerun through all ten transitions.
Keep local USB/SWD recovery available while the artifact remains unreleased.
The hidden override bypasses only the candidate-status block; all identity,
checksum, bootloader, route, watchdog, and post-boot gates still run.

Restore the test start locally with
`recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2`, then run:

```bash
export MESHCORE_ADMIN_PASSWORD='password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --target-key 63d8df63 \
  --temp-radio 909.950,500,5,5,120 \
  --ota-hops 0 \
  --legacy-full-airtime \
  --motatool /path/to/motatool \
  --accept-test-candidate \
  --yes
```

Keep the work directory. Rerunning the same command resumes only when the live
EndF body hash matches an exact chain node. Never manually skip a package.
`--legacy-full-airtime` temporarily sets the destination airtime factor to
zero and restores it at the endpoint. Use that option only where the selected
frequency and local duty-cycle rules permit a full transmit budget; omit it
otherwise.

### Measured direct-link timing

The first `ready to install` transfer ages were:

| Step | Blocks | Transfer |
|---:|---:|---:|
| 1 | 88 | 598 s |
| 2 | 45 | 42 s |
| 3 | 103 | 81 s |
| 4 | 146 | 105 s |
| 5 | 109 | 76 s |
| 6 | 118 | 94 s |
| 7 | 136 | 102 s |
| 8 | 170 | 127 s |
| 9 | 264 | 187 s |
| 10 | 169 | 193 s |

The physically observed bulk-transfer total for those exact packages is 1,605
seconds (26m45s). The log came from the pre-cleanup-fix runner revision. Step 1
was measured before the host began forcing source RXPS off and is therefore a
conservative outlier. A new complete run with that fix is projected at roughly
18–19 minutes of bulk transfer, but that projection is not a substitute for
the measured total. Allow about 70–75 minutes direct at BW500 for validation,
ten transfers, installs/reboots, retained-store checks, and final restoration.

Step 10's longer per-block time was not a V4 TempRadio delay: the source RXPS
readback was off. Its v1.17.1.3 requester uses non-overlapping adaptive flights
and a five-second recovery floor and logged more block reloads than step 8.
The V4 itself intentionally schedules its local radio handoff after 1.5 seconds
so the normal-channel command reply can drain; the host waits three seconds and
then checks the live TempRadio state.

For planning only, scale the conservative measured 26m45s bulk baseline by the
qualified adaptive-preamble airtime and by each additional relay transmission:

| Bandwidth, SF5/CR5 | Direct / 0 relays | 1 relay | 2 relays |
|---:|---:|---:|---:|
| 500 kHz | 26m45s | 53m30s | 1h20m15s |
| 250 kHz | 46m51s | 1h33m42s | 2h20m33s |
| 125 kHz | 1h27m03s | 2h54m07s | 4h21m11s |
| 62.5 kHz | 2h54m07s | 5h48m15s | 8h42m23s |

These are transfer-only estimates, not measured alternate-bandwidth or relay
results. Packet loss, relay contention, discovery, administrative commands,
staged verification, and ten reboot cycles add time. The bandwidth factors are
not simple powers of two because the adaptive wire preamble is 128 symbols at
500 kHz, 64 at 250 kHz, and 32 at 125/62.5 kHz.

## Manual operation

The automated runner is preferred because it binds every transition to the
expected MID and body hash. The manual details below document the checks used
during qualification and recovery; they are not permission to skip the
runner's candidate gate. Keep a written copy of each original setting and
never skip a step even when a later package appears in `ota ls`.

### 1. Verify and extract the asset

```bash
sha256sum RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip
unzip RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip
cd RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90
sha256sum -c SHA256SUMS.txt
for package in motas/*.mota; do motatool verify "$package" || exit 1; done
```

The outer hash must be
`c0b33f4568985e8b2b8dc99411295907212cf2bad21764b6333d5e0ba298fd61`,
and the SHA-256 of the extracted `SHA256SUMS.txt` must be
`3f8c4af8096b96a4aa6506825c387cc8a06f74d5213a29c9387bd11689546881`.
Do not continue after any checksum or verification error.

### 2. Record and prepare the destination

Before changing anything, save the complete replies from:

```text
ver
ota self
get bootloader.ver
get system.watchdog
get radio.rxps
powersaving
get rxdelay
get af
ota config
```

The starting `ota self` hash must be `71F4026CBE4B8B74`, the target must be
`2FA509C1`, and the hardware must be `RAK_3401`. Disable the nRF52 system
watchdog before the chain:

```text
set system.watchdog off
```

The hardware watchdog cannot stop immediately. The RAK will reset once within
about 60 seconds. Reconnect, wait at least 90 seconds from the command, and
require `get system.watchdog` to report `> off` before continuing.

For a direct bench link, apply these temporary transfer guardrails to the RAK
after every bridge reboot:

```text
powersaving off
set radio.rxps off
set rxdelay 0
ota config hops 0
tempradio 909.950,500,5,5,120
```

Where local duty-cycle rules permit it, `set af 0` can also remove the legacy
firmware's saved airtime wait during this bounded maintenance window. Record
`get af` first and restore that exact value after the final step. Current
firmware grants the bounded TempRadio transfer budget without overwriting the
saved airtime factor.

`powersaving off` is an isolation guardrail, not the fix for the observed
failure. The actual source failure was an oversized USB CDC reply; current
firmware splits those reads. Some historical bridge builds may report RXPS as
unsupported while already operating continuously. Verify the reported state
and do not substitute a normal reboot for the one-time watchdog reset. Direct
tests use `hops 0`: `hops 1` makes the target echo each source response despite
there being no intermediate relay, increasing half-duplex loss and legacy
three-second retries.

The exact candidate cannot use fast RXPS throughout because its historical
bridge applications predate the v1.17.1.5 adaptive-preamble contract. Keep
RXPS off for all ten steps whether using SF5/BW250 or the faster direct-bench
SF5/BW500 tuple. After the final endpoint is proven, a later update whose
target, controller, source, and relays are all v1.17.1.5 or newer can use the
generic LoRa OTA runner and its qualified adaptive-preamble boundaries.

Put the source on the identical TempRadio tuple. A current ASCII-first Full
Companion recognizes `motatool`'s initial `ota folder on` line directly:

```bash
motatool serve \
  --dir ./motas \
  --serial /dev/ttyACM1 \
  --baud 115200 \
  -v
```

The same command also works with older Full Companion firmware in binary mode,
where the idle parser recognizes the identical preamble. Restart `motatool` for
every step so the source emits a fresh catalog advert. Leave it running during
the download and stop it with Ctrl-C only after the destination reports `ready
to install`.

### 3. Install all ten packages in order

| Step | Manifest ID | Before hash | Expected version | After hash |
|---:|---|---|---|---|
| 1 | `C147BCEF` | `71F4026CBE4B8B74` | `1.16.7.9` | `42BC53A64288E845` |
| 2 | `BEB578FC` | `42BC53A64288E845` | `1.16.7.10` | `1AD2AA8B6C478DA6` |
| 3 | `6D8EF814` | `1AD2AA8B6C478DA6` | `1.16.8.7` | `D709A25308BC1831` |
| 4 | `40AC4CEB` | `D709A25308BC1831` | `1.16.9.105` | `457BEDA5E6406C81` |
| 5 | `F6C8890B` | `457BEDA5E6406C81` | `1.16.9.110` | `E76BFA205634CAB8` |
| 6 | `82405E89` | `E76BFA205634CAB8` | `1.16.9.113` | `65328FC8A1FBED2D` |
| 7 | `80F40DB6` | `65328FC8A1FBED2D` | `1.16.9.117` | `AF7532E13337FADD` |
| 8 | `DBF6310D` | `AF7532E13337FADD` | `1.16.10.0` | `22002359BBDFA76E` |
| 9 | `C2979E08` | `22002359BBDFA76E` | `1.17.1.3` | `FE65A6135A1E7B3F` |
| 10 | `319DEC92` | `FE65A6135A1E7B3F` | `1.17.1.5` | `9179B98432895924` |

For each row, first prove that `ota self` exactly matches its **Before hash**,
then inspect the manager before changing it:

```text
ota status
```

Proceed to discovery only after that status is reconciled. When the manager is
idle, run:

```text
ota ls
ota pull MANIFEST_ID flash
ota status
```

If the first status reports this row's MID as partial, use `ota pull` with that
same MID to resume it. If this row's MID is already ready, do not pull or
cancel it; continue with the install checks below. If status reports the
immediately previous row's MID as `verifying staged blocks`, wait through the
bounded discovery window; require either that exact MID to become `ready to
install` or the manager to become `no download`, and re-prove the current
**Before hash**. Cancel only the same previous MID while it is visibly
attached and ready. If it has become idle, send no cancel. Stop on any other
MID, failed or incomplete state, ambiguous reply, or timeout. Never issue a
blind `ota cancel`: it can discard a resumable or unrelated session.

Repeat `ota status` at a restrained interval until it says `ready to install`.
If the manifest is initially absent, wait for a fresh source advert, run
`ota ls` again, or restart `motatool`; do not pull a different ID. Stop the
seeder and install:

```text
get system.watchdog
ota install
```

The watchdog reply must still be `> off`. Allow the current default five-minute
readiness window for the USB/LoRa identity to return; automation probes every
10 seconds and returns early as soon as the exact body is visible. Then run
`ver`, `ota self`, and `ota help`.
Require the row's exact **After hash**, require `bootloader: apply OK`, and
require `rescue install <hash16>` in the help before exposing that bridge to
the next package. This rescue-help gate applies to steps 1-9; step 10 is the
endpoint and is not exposed to another package in this chain. A normal-radio
`ota status` response of `no download` proves only that the manager is idle;
legacy internal-flash firmware does not durably erase a retained container by
accepting an IDLE `ota cancel`. Do not claim otherwise. When the same expected
manifest reports `verifying staged blocks` after the next TempRadio starts,
wait only for the bounded discovery window and require that it become `ready
to install` or `no download`; stop on a changed ID, failed/incomplete state, or
timeout. Prove the exact running body before cancelling an attached completed
session. OTAFIX consumes the approval word during a successful install, so a
retained endpoint container is inert and a later valid pull replaces it.
Reapply the transfer
guardrails and TempRadio tuple, restart `motatool`, and continue with the next
row.

If the board boots but `ota self` says the EndF is invalid, stop. Only when
`ota status` still identifies that row's complete staged package may the
guarded `ota rescue install BEFORE_HASH` command be used. It is not a force
option and must use that row's exact Before hash. If the board does not boot,
recover the documented start/recovery UF2 locally over USB. The fd98 endpoint
deliberately disables rescue on its shared internal bootloader-update build;
if its EndF is invalid, use local DFU/SWD rather than attempting another LoRa
package.

### 4. Intermediate relays and restoration

On every managed intermediate relay, save `get rxdelay` and `get txdelay`, then
use the airtime-scaled collision window during the maintenance session:

```text
set rxdelay 0
set txdelay 0.3
```

Current OTA transfer packets honor that configured relay `txdelay`; setting
`0.3` gives competing relays a small randomized, airtime-scaled forwarding
window. The source's manifest-fragment gap independently follows active packet
airtime and duty spacing, clamped to 100-1000 ms. These two delays solve
different problems.

After step 10 is proven, restore each saved destination and relay value exactly,
including `rxdelay`, RXPS, CPU power saving, `af`, `ota config hops`, and relay
timing. Current full-parser repeater firmware accepts `normalradio` and restores
the saved tuple after replying on the temporary channel:

```text
normalradio
```

The fd98 endpoint supports that command. If a recovery build unexpectedly
replies `Unknown command`, shorten its lease to one minute with the same tuple:

```text
tempradio 909.950,500,5,5,1
```

Wait for the lease to expire, return the controller and ordinary source to
their saved normal channels, and then re-enable the RAK system watchdog:

```text
set system.watchdog on
get system.watchdog
```

A Full Companion source is the exception: its local TCP console supports
`normalradio` and the automated runner uses it before restoring the shared
Binary API radio tuple.

Final lab success requires version
`v1.17.1.5-halo-keymind-cascade-dev-fd98bc90`, body hash
`9179B98432895924`, target `2FA509C1`, hardware `RAK_3401`, and
`get system.watchdog` reporting `> on`. If a relay cannot be restored before
its TempRadio lease ends, wait for it to return to the normal channel and
restore its saved `rxdelay` and `txdelay` there.

## Two-relay deployment

The exact direct package transitions above were physically observed; this
post-run host-runner revision and multi-hop operation are not cleanly
end-to-end qualified. A controlled, recoverable multi-hop lab can exercise two
intermediate relays by
listing them farthest-to-nearest and using three OTA hops. Run a non-mutating
preflight first; even preflight requires the hidden candidate override because
the artifact remains unreleased and the command connects to live devices:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.5-fd98bc90.zip \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp SOURCE_IP:5001 \
  --source-cli-tcp SOURCE_IP:5002 \
  --source-shares-controller \
  --target-key 63d8df63 \
  --relay 'Far Repeater' \
  --relay 'Near Repeater' \
  --temp-radio 909.950,250,5,5,120 \
  --ota-hops 3 \
  --motatool /path/to/motatool \
  --accept-test-candidate \
  --preflight-only
```

After preflight succeeds, rerun with `--yes` in place of
`--preflight-only`. Confirm that the selected frequency and bandwidth are
legal at the deployment location.

## Rescue and completion

The guarded `ota rescue install <base_body_hash>` command is present after
step 1 and remains available through the step-9 bridge. It is not a force
command: it refuses a valid normal EndF, a base mismatch, wrong
target/hardware, or invalid payload. A failure before step 1 completes still
requires local USB recovery because the deployed start image predates the
rescue command. The final fd98 endpoint uses the stricter shared-slot profile;
its invalid-EndF recovery is local DFU/SWD, not the rescue command.

Before the first mutation, the runner saves the destination's RXPS periods,
CPU power-saving state, RX flood delay, airtime factor, and OTA hop reach in
the persistent work directory, flushing both the file and its directory entry
before changing a guardrail. It also saves the managed source's exact RXPS
preference in the chain-root `source-rxps-settings.json` before disabling it.
Every step reuses that protected record, so a rerun after a killed host process
restores the chain-start preference instead of treating the temporary off state
as original. The record is accepted only for the same CLI endpoint. The runner
retires that chain-root record only after it has proved the source is back on
its normal radio and restored the exact preference at the verified endpoint.
The runner
reads the destination, controller, source, and relay versions before applying
the same RXPS policy as the generic runner.
The mixed historical chain therefore verifies `radio.rxps off` after every
bridge reboot; a future all-v1.17.1.5-or-newer SF5/BW250 chain would instead
keep RXPS on under the qualified level-8/preamble-64 boundary. It also verifies
`powersaving off` and `rxdelay 0`, plus `af 0` when
`--legacy-full-airtime` was explicitly selected. It restores every original
value only after the exact endpoint is proven. An interrupted run deliberately
leaves those transfer guardrails active; rerun the same command with the same
work directory to resume and restore them.
CPU power saving was not the cause of the observed folder-source failure: that
was a USB CDC receive-ring overrun fixed by bounded serial reads. The runtime
settings remain conservative isolation and fast-link reliability guardrails.

For every named `--relay`, the per-step runner also captures `rxdelay` and
`txdelay`, writes them to `relay-timing-settings.json`, applies `rxdelay 0` and
`txdelay 0.3` during transfer, verifies the readback, and restores both before
the relay leaves TempRadio.

The runner also keeps the watchdog off across the chain, rechecks identity and
OTA reach before every package, requires the exact post-boot EndF hash, and
only re-enables the watchdog after step 10. Lab success requires endpoint body
hash `9179B98432895924`, target `2FA509C1`, hardware `RAK_3401`, normal radio
`910.525 MHz / 62.5 kHz / SF7 / CR5`, and the watchdog verified on.

## External-radio startup and manual USB recovery

RAK3401 is a distinct target from RAK4631. Its RAK13300/RAK13302 radio is on
the WisBlock SPI bus (`SPI1` in the Adafruit nRF52 core), with BUSY and DIO1 on
P0.09/P0.10. Current builds explicitly enable those NFC-capable pads as GPIO
and do not advertise the radio bus as an on-board QSPI flash device.

The RAK13302's FEM and boost supply use switched `3V3_S`, but the SX1262 core
uses unswitched 3V3. An MCU reset, a 1200-baud DFU touch, or disconnecting only
USB may therefore leave a battery-powered SX1262 in its previous state. At
startup, current firmware quiesces the FEM, reinitializes the dedicated SPI
instance, asserts NRESET, and, if BUSY remains high, sends the Semtech
NSS/GET_STATUS wake sequence without waiting on RadioLib first. The one-time
board startup cold-starts `3V3_S`; later radio retries leave that shared rail
enabled so they do not reset GPS or sensor modules.

If BUSY still cannot be released, firmware must not enter a reset loop:

- Companion starts its USB/BLE management services after three bounded probes,
  uses nRF52 hardware entropy if it needs a first-boot identity, and retries the
  radio every 60 seconds. Radio statistics remain zero until recovery.
- Repeater keeps the same USB CDC session open and prints
  `Radio unavailable; retrying in 60 seconds`. It repeats the board-level probe
  in place. The normal repeater CLI and mesh transport cannot start until the
  radio responds.

A permanently high BUSY after NRESET and the direct wake transaction indicates
an electrically unavailable radio, loose module, or power-domain fault; an MCU
reboot cannot manufacture a response. If local access is possible, disconnect
the battery as well as USB and reseat the WisBlock module. Remote firmware will
continue retrying without requiring that physical intervention or churning the
USB device.

For a local Serial DFU recovery with the repository helper:

```bash
cd /path/to/meshfirmware
MCFIRMWARE_NO_SUDO=1 ./mcfirmware.sh
```

Select the serial entry whose USB identity is `WisCore RAK3401 Board`, then
choose **Custom**, **nrf52**, and the exact RAK3401 `.zip`. Use **flash-update**
to preserve InternalFS preferences and identity. Use **flash-wipe + flash** only
when erasing application data is intentional. The helper follows the same USB
device into bootloader mode, so do not substitute another `/dev/ttyACM*` merely
because its number appears first.
