# RAK3401 1W repeater compact LoRa update chain

> Status: physically qualified on 19-Aug-2026. All nine exact transitions
> completed on a deployed RAK3401 1W with its existing bootloader, using a
> Heltec V4 source at 909.950 MHz / 500 kHz / SF5 / CR5. Every post-boot EndF
> hash matched. The same files also pass independent reconstruction, container
> verification, and both deployed Preview 6 and current OTAFIX simulators.

Use this asset:

```text
RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip
```

- ZIP SHA-256: `9f80eef191b88833bf4d2e4fea559cf5233ca53f9266ba310d447f37fa445f3a`
- inner `SHA256SUMS.txt` SHA-256: `73d96e23237896a3e342fe736be12d94087a813bf09ad609fb55330bbe586055`
- endpoint image SHA-256: `2784e4b645bc3dc198de0b8b18d3d7369cd02eca61cd71c46a51b61854da5345`
- endpoint EndF body hash: `4BB1526BF647547D`

Until that asset is uploaded to the release tag pinned by the runner, pass its
local path with `--bundle`.

## Exact destination

This chain is intentionally specific to one firmware lineage and hardware
target:

- target ID: `2FA509C1`
- hardware: `RAK_3401`
- role: `RAK_3401_repeater_lora_ota_no_external_sensors`
- start: `v1.16.7.0-c1caa5ad`, EndF `71F4026CBE4B8B74`
- endpoint source: MeshCore `e742333a0ee393b0d55d2414f25b28f2f67e1ea1`
- endpoint label: `v1.17.1.02-halo-keymind-cascade-dev-e742333a`
- endpoint packed version: `0x01110102` (EndF tools render this as `1.17.1.2`)
- deployment target key: `63d8df6387eaffd2e25db7d2a8ad967a65202182a48d681d7e7a9260f917280d`

### GPS limitation

This chain's exact `RAK_3401_repeater_lora_ota_no_external_sensors` endpoint
compiles with `ENV_INCLUDE_GPS` removed. It will not detect, configure, or read
a RAK12501 GPS module. GPS requires the ordinary full-sensor
`RAK_3401_repeater` firmware and the RAK12501 must be installed in sensor slot
A. Do not use slot D with the RAK13302 1 W radio: the GPS reset/PPS signals
would overlap the radio's BUSY/DIO1 signals. The full-sensor build is a
different target and is not an endpoint of this compact OTA chain.

Do not use the chain on another target ID, hardware family, starting image, or
firmware body hash. The runner checks all four.

## Why the old bootloader works

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
passed 89,844-byte package. Step 2 is a 46,363-byte fixed-workspace package
that installs the compatibility receiver. Later receivers stage above the
real EndF image extent and let the bootloader enforce each package's selected
workspace.

An exhaustive search tested every page-aligned workspace relevant to a route
shorter than nine packages: 9,430 candidate geometries across 272 possible
layer-skipping edges. None fit. The 61 edges on the shortest-path graph were
then swept across another 2,296 workspace choices; 840 nine-package routes
were compared by total transfer size. The selected route is the byte-minimum
shortest route:

| Step | From | To | Workspace | Package | Margin |
|---:|---|---|---:|---:|---:|
| 1 | 1.16.7.0 | 1.16.7.9 | `0x98000` | 89,844 | 0 |
| 2 | 1.16.7.9 | 1.16.7.10 | `0x98000` | 46,363 | 40,960 |
| 3 | 1.16.7.10 | 1.16.8.7 | `0x91000` | 106,030 | 12,288 |
| 4 | 1.16.8.7 | 1.16.9.105 | `0x89000` | 149,927 | 0 |
| 5 | 1.16.9.105 | 1.16.9.110 | `0x8E000` | 111,307 | 16,384 |
| 6 | 1.16.9.110 | 1.16.9.111 | `0x8D000` | 70,679 | 61,440 |
| 7 | 1.16.9.111 | 1.16.9.116 | `0x89000` | 143,441 | 4,096 |
| 8 | 1.16.9.116 | 1.16.9.118 | `0x81000` | 98,188 | 86,016 |
| 9 | 1.16.9.118 | 1.17.1.02 | `0x7D000` | 186,385 | 12,288 |

Total mOTA transfer data is 1,002,164 bytes. `ROUTE.json`, `CHAIN.csv`, and
`validation-results.json` in the bundle pin the exact geometry and image hash
for every transition.

## Host requirements

Install Python 3.9 or newer, `meshcli` 1.6.0 or newer, the current `motatool`,
and an OTA-enabled Full Companion or repeater that can seed mOTA files.

## Verify offline

No password or device is needed:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

The runner checks the outer ZIP, complete inner checksum coverage, all nine
manifests, continuity, image anchors, final recovery image, and every container
with `motatool`.

## Direct recoverable bench run

Restore the test start locally with
`recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2`, then run:

```bash
export MESHCORE_ADMIN_PASSWORD='password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip \
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
  --yes
```

Keep the work directory. Rerunning the same command resumes only when the live
EndF body hash matches an exact chain node. Never manually skip a package.
`--legacy-full-airtime` temporarily sets the destination airtime factor to
zero and restores it at the endpoint. Use that option only where the selected
frequency and local duty-cycle rules permit a full transmit budget; omit it
otherwise.

## Manual operation

The pinned runner is the safer method. If it cannot be used, follow every
check below and keep a written copy of each original setting. Do not skip a
step even when a later package appears in `ota ls`.

### 1. Verify and extract the asset

```bash
sha256sum RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip
unzip RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip
cd RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a
sha256sum -c SHA256SUMS.txt
for package in motas/*.mota; do motatool verify "$package" || exit 1; done
```

The outer hash must be `9f80eef191b88833bf4d2e4fea559cf5233ca53f9266ba310d447f37fa445f3a`.
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

Put the source on the identical TempRadio tuple. If it is a binary-mode Full
Companion, the current `motatool` can switch modes for the serving session:

```bash
motatool serve \
  --dir ./motas \
  --serial /dev/ttyACM1 \
  --baud 115200 \
  --companion-terminal \
  -v
```

Omit `--companion-terminal` for an ordinary text-console OTA source. Restart
`motatool` for every step so the source emits a fresh catalog advert. Leave it
running during the download and stop it with Ctrl-C only after the destination
reports `ready to install`.

### 3. Install all nine packages in order

| Step | Manifest ID | Before hash | Expected version | After hash |
|---:|---|---|---|---|
| 1 | `C147BCEF` | `71F4026CBE4B8B74` | `1.16.7.9` | `42BC53A64288E845` |
| 2 | `C510B628` | `42BC53A64288E845` | `1.16.7.10` | `6F6E51D63BF3E93F` |
| 3 | `8CAD7045` | `6F6E51D63BF3E93F` | `1.16.8.7` | `F04DA8DB515E5C6D` |
| 4 | `8B1EEFF6` | `F04DA8DB515E5C6D` | `1.16.9.105` | `3894A4D7B11ED282` |
| 5 | `687E3BEA` | `3894A4D7B11ED282` | `1.16.9.110` | `CDC5CA630881DA15` |
| 6 | `0D59A34B` | `CDC5CA630881DA15` | `1.16.9.111` | `D0CC4A9E135B9A4D` |
| 7 | `2C5C2082` | `D0CC4A9E135B9A4D` | `1.16.9.116` | `66272A0E7FEF2773` |
| 8 | `994AB743` | `66272A0E7FEF2773` | `1.16.9.118` | `8E00019AA08E00BF` |
| 9 | `FE986948` | `8E00019AA08E00BF` | `1.17.1.02` | `4BB1526BF647547D` |

For each row, first prove that `ota self` exactly matches its **Before hash**.
Then run:

```text
ota cancel
ota ls
ota pull MANIFEST_ID flash
ota status
```

Repeat `ota status` at a restrained interval until it says `ready to install`.
If the manifest is initially absent, wait for a fresh source advert, run
`ota ls` again, or restart `motatool`; do not pull a different ID. Stop the
seeder and install:

```text
get system.watchdog
ota install
```

The watchdog reply must still be `> off`. Allow up to 90 seconds for the USB
port to disappear and return. Then run `ver`, `ota self`, and `ota help`.
Require the row's exact **After hash**, require `bootloader: apply OK`, and
require `rescue install <hash16>` in the help before exposing that bridge to
the next package. Clear only the now-proven retained download with `ota cancel`,
reapply the transfer guardrails and TempRadio tuple, restart `motatool`, and
continue with the next row.

If the board boots but `ota self` says the EndF is invalid, stop. Only when
`ota status` still identifies that row's complete staged package may the
guarded `ota rescue install BEFORE_HASH` command be used. It is not a force
option and must use that row's exact Before hash. If the board does not boot,
recover the documented start/recovery UF2 locally over USB.

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

After step 9 is proven, restore each saved destination and relay value exactly,
including `rxdelay`, RXPS, CPU power saving, `af`, `ota config hops`, and relay
timing. Current full-parser repeater firmware accepts `normalradio` and restores
the saved tuple after replying on the temporary channel:

```text
normalradio
```

The historical endpoint in this pinned bundle predates that command. If it
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

Final success requires version `v1.17.1.02-halo-keymind-cascade-dev-e742333a`,
body hash `4BB1526BF647547D`, target `2FA509C1`, hardware `RAK_3401`, and
`get system.watchdog` reporting `> on`. If a relay cannot be restored before
its TempRadio lease ends, wait for it to return to the normal channel and
restore its saved `rxdelay` and `txdelay` there.

## Two-relay deployment

For two intermediate relays, list them farthest-to-nearest and use three OTA
hops. Run a non-mutating preflight first:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.1.02-e742333a.zip \
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
  --preflight-only
```

After preflight succeeds, rerun with `--yes` in place of
`--preflight-only`. Confirm that the selected frequency and bandwidth are
legal at the deployment location.

## Rescue and completion

The guarded `ota rescue install <base_body_hash>` command is present after
step 1. It is not a force command: it refuses a valid normal EndF, a base
mismatch, wrong target/hardware, or invalid payload. A failure before step 1
completes still requires local USB recovery because the deployed start image
predates the rescue command.

Before the first mutation, the runner saves the destination's RXPS periods,
CPU power-saving state, RX flood delay, airtime factor, and OTA hop reach in
the persistent work directory. It verifies `radio.rxps off`, `powersaving off`,
and `rxdelay 0` after every bridge reboot, plus `af 0` when
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
only re-enables the watchdog after step 9. Success requires endpoint body hash
`4BB1526BF647547D`, target `2FA509C1`, hardware `RAK_3401`, normal radio
`910.525 MHz / 62.5 kHz / SF7 / CR5`, and the watchdog verified on.
