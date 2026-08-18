# RAK3401 1W repeater LoRa update chain

> Status: accelerated release candidate. All 30 exact transitions passed
> independent offline reconstruction, container verification, and stable
> bootloader simulation. The byte-identical first bridge and the superseded
> 29-step baseline completed physical RAK3401 testing; the new complete 30-step
> byte sequence still needs its final full-chain hardware qualification.

The release is
[`rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e`](https://github.com/mikecarper/MeshCore/releases/tag/rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e).
Use this asset:

```text
RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-8c5262c6.zip
```

- ZIP SHA-256: `b2781e02460b200a7c37bfae352bad81618716e550d1d042dca8aa29bfc73c29`
- inner `SHA256SUMS.txt` SHA-256: `1f7add658ae5771451cd66a0e5c58a5e461983c1b36029ef480093ae5d5f1020`
- endpoint image SHA-256: `a08b5791419410c760f31c26bb45c77d776eb7bbf68fde656e19bcd616a6227d`
- endpoint EndF body hash: `3DD88C3DD7B8A35B`

The superseded 29-step v1.17.01 release, earlier v1.17.01 candidates, and the
v1.17.02 chain remain useful only for provenance or diagnosis. The runner
recognizes their hashes and refuses live installation before opening a device
or reading a password.

## Exact destination

This chain is intentionally specific to one firmware lineage and hardware
target:

- target ID: `2FA509C1`
- hardware: `RAK_3401`
- role: `RAK_3401_repeater_lora_ota_no_external_sensors`
- start: `v1.16.7.0-c1caa5ad`, EndF `71F4026CBE4B8B74`
- endpoint: `v1.17.01-halo-keymind-cascade-dev-8c5262c6`, EndF `3DD88C3DD7B8A35B`
- deployment target key: `63d8df6387eaffd2e25db7d2a8ad967a65202182a48d681d7e7a9260f917280d`

Do not use the chain on another target ID, hardware family, starting image, or
firmware body hash. The runner checks all four.

## Validation and speed design

The direct physical baseline used:

- a Heltec V4.3 OLED 2 MB Full Companion on `/dev/ttyACM0`;
- the same Full Companion at `192.168.1.51`, with mOTA seeding on TCP `5001`
  and its local OTA/TempRadio console on TCP `5002`;
- Full Companion key
  `8ce031bc322f3cf77376eeacfe8800b30d8c5dfe927f3636d801f3455a2dd4cf`;
- one direct RF link to `RAK3401 1W Repeater`;
- temporary radio `909.950 MHz`, `500 kHz`, `SF5`, `CR5`;
- all 29 packages in the now-superseded `cd824765` chain.

The baseline started at `2026-08-14T01:50:24Z` and reached its endpoint at
`2026-08-14T06:41:36Z`. This ZIP preserves that 29-row record as
`PHYSICAL-BASELINE-cd824765.jsonl`; it does not claim those rows describe the
new bridge bytes.

All 30 new containers were freshly generated. Every delta was reconstructed
with both zero-filled and `0xFF`-filled workspaces, verified independently by
`motatool`, and exercised by the stable OTAFIX apply simulator. The largest
package is the byte-identical, physically passed step 1 at 89,844 bytes, which
leaves 268 bytes of the fixed 90,112-byte staging region. Step 2 is the earliest
safe place for the terminal-consumption backport: adding it directly to step 1
would exceed that staging limit. Step 26 remains an intentional compiler-layout
bridge.

The speedup keeps 1 KiB logical blocks. Firmware currently accepts at most 1
KiB, and larger logical blocks would still fragment into the same roughly
160-byte LoRa packets while increasing the amount at risk when a block must be
retried. Instead, one request can open an adaptive flight of multiple 1 KiB
blocks. It begins at one block, grows through two and three to four after clean
flights, and reduces the width after missing data or proofs. Retry timing is
derived from actual LoRa airtime and path length. Active transfer packets use
primary priority; periodic discovery remains background traffic.

Step 1 installs the guarded rescue command, the four-block adaptive requester,
and primary transfer priority. The starting firmware still requests step 1
serially. Step 2 installs terminal OTA bulk-packet consumption in the target,
so the echo/dispatch reduction takes effect for transfers 3 through 30. A
direct endpoint comparison confirmed the intended behavior: the older target
needed 147 host block reads for a 48-block package and reported ready after
267 seconds; the current target needed 82 reads for a larger 68-block package
and reported ready after 98 seconds. These differently sized transfers are a
protocol comparison, not a formal throughput benchmark.

## Host requirements

Install:

- Python 3.9 or newer;
- `meshcli` 1.6.0 or newer;
- the current `motatool`;
- a Full Companion or OTA-enabled repeater that can seed mOTA files.

The runner maintains one persistent controller connection. It logs into the
destination once, keeps that authenticated radio-node session across
application reboots, and does not treat a silent packet as lost
authentication. The source seeder reconnects for each isolated package but
does not need remote-admin login.

## Verify the release offline

No password or device is needed:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-8c5262c6.zip \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

When `--bundle` is omitted, the runner downloads the pinned release asset. It
checks the outer ZIP hash, complete inner checksum coverage, all 30 manifests,
chain continuity, audited image anchors, final recovery image, and every mOTA
container with `motatool`.

Offline verification does not need `--accept-test-candidate`. Live preflight or
installation does: the flag explicitly acknowledges that the exact 30-step
sequence has exhaustive offline validation but not yet a complete physical run.

## Direct recoverable bench run: BW500/SF5

Restore the test RAK locally with the ZIP's
`recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2`. Before starting,
require start version `1.16.7.0`, body hash `71F4026CBE4B8B74`, target
`2FA509C1`, hardware `RAK_3401`, and OTAFIX mOTA ABI 2 with codec 2. The live
runner queries `get bootloader.ver`, reports the installed version, and then
uses `ota self` to verify those apply capabilities before changing any radio
or watchdog setting.

For the tested direct topology:

```bash
export MESHCORE_ADMIN_PASSWORD='password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-8c5262c6.zip \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --target-key 63d8df63 \
  --temp-radio 909.950,500,5,5,120 \
  --ota-hops 1 \
  --motatool /path/to/motatool \
  --accept-test-candidate \
  --yes
```

BW500 is for the controlled direct bench setup. Confirm that the selected
frequency and bandwidth are legal at the test location.

Keep the work directory. Each package gets an isolated attempt directory, and
each exact transition is appended to `progress.jsonl`. After a host or RF
interruption, rerun the identical command with the same work directory. The
runner resumes only when the live body hash matches the recorded chain; never
manually skip to a package.

## Live deployment with two intermediate relays: BW250/SF5

For the intended private TempRadio deployment, use BW250/SF5 and two
intermediate relays. List relays from the destination side toward the
controller: farthest first, nearest last. With two relays there are three RF
links, so use `--ota-hops 3`.

Run a non-mutating preflight first:

```bash
export MESHCORE_ADMIN_PASSWORD='your-destination-and-relay-password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-8c5262c6.zip \
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

A bare relay name uses `MESHCORE_ADMIN_PASSWORD`. Append `=PASSWORD` only for
a relay with a different password. After preflight succeeds, rerun the same
command with `--yes` in place of `--preflight-only`.

Arm the relay chain farthest-to-nearest while all nodes are still reachable on
the ordinary mesh. The runner then moves the source and controller to the same
temporary tuple. Keep the private TempRadio network reserved for OTA during
the run; public-mesh flooding does not improve this transfer. A 120-minute
window is the minimum used here; raise it if the selected timeouts and relay
count require more time.

## Guarded no-EndF rescue

The rescue command is present from the first installed bridge onward. If a
bridge remains reachable but `ota self` reports no EndF, serve and fetch only
its exact successor package. Read that row's 16-hex `base_body_hash` from
`CHAIN.csv`, then issue:

```text
ota rescue install <base_body_hash>
```

This is not a force command. It refuses a valid normal EndF, a base mismatch,
wrong target or hardware, or invalid payload. OTAFIX hashes the physical
running application before its first write. A missing/corrupt EndF can
therefore continue through its exact successor instead of becoming stuck,
provided the installed bridge already contains the rescue command. The
c1caa5ad starting image predates rescue, so a failure before step 1 completes
still requires local USB recovery.

## Safety and completion

The runner performs the following guarded sequence:

1. Persist `system.watchdog off`, allow the already-running watchdog to reset
   once, and prove stable uptime with the watchdog off.
2. Recheck destination identity, OTA reach, source identity, and exact package
   base before every step.
3. Require `system.watchdog` to report `off` immediately before every install.
4. After every install, probe `ota self` at 10 and 20 seconds rather than
   sleeping for 90 seconds. Require the exact version and EndF body hash, then
   prove the rescue command is present before exposing that bridge to another
   step. Relayed runs keep the same 10-second probe cadence until the relays'
   mandatory return-to-normal window has elapsed.
5. Re-enable the watchdog only after the exact step-30 endpoint boots.

Success requires all of the following:

- version `v1.17.01-halo-keymind-cascade-dev-8c5262c6`;
- EndF body hash `3DD88C3DD7B8A35B`;
- target `2FA509C1`, hardware `RAK_3401`;
- normal radio `910.525 MHz`, `62.5 kHz`, `SF7`, `CR5`;
- `system.watchdog` verified `on`.

A completed download or an accepted install reply is not success without the
post-reboot identity checks. The bundle includes exact test-start and final
local recovery UF2/ZIP files if USB recovery is ever needed.
