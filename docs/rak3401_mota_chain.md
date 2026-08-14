# RAK3401 1W repeater LoRa update chain

> Status: physically passed. The exact 29-step chain completed from the
> c1caa5ad v1.16.7.0 test image through v1.17.01 on a RAK3401 1W. Every step
> passed its post-boot version, EndF, rescue-command, and watchdog checks.

The release is
[`rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e`](https://github.com/mikecarper/MeshCore/releases/tag/rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e).
Use this asset:

```text
RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-cd824765.zip
```

- ZIP SHA-256: `eac67a0be12690b7e22c4d1f6a15bfdeb5bd627c4850b246b1be4220e5607b34`
- inner `SHA256SUMS.txt` SHA-256: `8097d75c5d11b9e32e3ebd4971068bf743eaa108054ab39eb0049016f89d185d`
- endpoint image SHA-256: `5c8d94bb23e87c23b0374ffb5a46e0c1205d6eebcb9d8bae3be6fda2613f9f79`
- endpoint EndF body hash: `DD45E7A23438B051`

The older v1.17.01 candidates and the v1.17.02 chain are retained only for
diagnosis. The runner recognizes their hashes and refuses live installation
before opening a device or reading a password.

## Exact destination

This chain is intentionally specific to one firmware lineage and hardware
target:

- target ID: `2FA509C1`
- hardware: `RAK_3401`
- role: `RAK_3401_repeater_lora_ota_no_external_sensors`
- start: `v1.16.7.0-c1caa5ad`, EndF `71F4026CBE4B8B74`
- endpoint: `v1.17.01-halo-keymind-cascade-dev-cd824765`, EndF `DD45E7A23438B051`
- deployment target key: `63d8df6387eaffd2e25db7d2a8ad967a65202182a48d681d7e7a9260f917280d`

Do not use the chain on another target ID, hardware family, starting image, or
firmware body hash. The runner checks all four.

## What was tested

The direct physical run used:

- a Heltec V4.3 OLED 2 MB Full Companion on `/dev/ttyACM0`;
- the same Full Companion at `192.168.1.51`, with mOTA seeding on TCP `5001`
  and its local OTA/TempRadio console on TCP `5002`;
- Full Companion key
  `8ce031bc322f3cf77376eeacfe8800b30d8c5dfe927f3636d801f3455a2dd4cf`;
- one direct RF link to `RAK3401 1W Repeater`;
- temporary radio `909.950 MHz`, `500 kHz`, `SF5`, `CR5`;
- all 29 freshly rebuilt 1 KiB-block mOTA packages.

The first verified transition completed at `2026-08-14T01:50:24Z`; the final
endpoint completed at `2026-08-14T06:41:36Z`. The ZIP contains the complete
29-row physical record as `PHYSICAL-TEST.jsonl`.

The largest package is 89,844 bytes and leaves 268 bytes of staging margin.
Every delta was also reconstructed offline with both zero-filled and
`0xFF`-filled workspaces and verified independently with `motatool`. Step 25 is
an intentional extra bridge that keeps the compiler-layout transition small.

Step 1 installs both the guarded rescue command and the accelerated four-block
primary requester. The old requester used for the first transfer is serial, so
step 1 is expected to be slower. Steps 2 through 29 use the accelerated
requester. Some historical bridge text still says `low priority`; those
images were built with `OTA_TX_PRIORITY=0`, so their transfer traffic is
actually primary.

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
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-cd824765.zip \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

When `--bundle` is omitted, the runner downloads the pinned release asset. It
checks the outer ZIP hash, complete inner checksum coverage, all 29 manifests,
chain continuity, audited image anchors, final recovery image, and every mOTA
container with `motatool`.

## Direct recoverable bench run: BW500/SF5

Restore the test RAK locally with the ZIP's
`recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2`. Before starting,
require start version `1.16.7.0`, body hash `71F4026CBE4B8B74`, target
`2FA509C1`, hardware `RAK_3401`, and OTAFIX mOTA ABI 2 with codec 2.

For the tested direct topology:

```bash
export MESHCORE_ADMIN_PASSWORD='password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-cd824765.zip \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --target-key 63d8df63 \
  --temp-radio 909.950,500,5,5,120 \
  --ota-hops 1 \
  --motatool /path/to/motatool \
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
  --bundle /path/to/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-cd824765.zip \
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

A bare relay name uses `MESHCORE_ADMIN_PASSWORD`. Append `=PASSWORD` only for
a relay with a different password. After preflight succeeds, rerun the same
command with `--yes` in place of `--preflight-only`.

Arm the relay chain farthest-to-nearest while all nodes are still reachable on
the ordinary mesh. The runner then moves the source and controller to the same
temporary tuple. Keep the private TempRadio network reserved for OTA during
the run; public-mesh flooding does not improve this transfer. A 120-minute
window is the minimum used here—raise it if the selected timeouts and relay
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
4. After every reboot, require the exact version and EndF body hash, then prove
   the rescue command is present before exposing that bridge to another step.
5. Re-enable the watchdog only after the exact step-29 endpoint boots.

Success requires all of the following:

- version `v1.17.01-halo-keymind-cascade-dev-cd824765`;
- EndF body hash `DD45E7A23438B051`;
- target `2FA509C1`, hardware `RAK_3401`;
- normal radio `910.525 MHz`, `62.5 kHz`, `SF7`, `CR5`;
- `system.watchdog` verified `on`.

A completed download or an accepted install reply is not success without the
post-reboot identity checks. The bundle includes exact test-start and final
local recovery UF2/ZIP files if USB recovery is ever needed.
