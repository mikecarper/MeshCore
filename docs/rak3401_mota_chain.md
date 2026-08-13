# RAK3401 1W repeater LoRa update chain

> Status: the second corrected v1.17.01 candidate is withdrawn. A physical
> RAK3401 run passed steps 1 through 15, then step 16 booted but could not
> validate its EndF. The runner blocks this exact bundle before device access.

This procedure records the exact 26-step update chain from the release
[`rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e`](https://github.com/mikecarper/MeshCore/releases/tag/rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e).
It is intentionally specific to the following destination firmware:

- target ID: `2FA509C1`
- hardware: `RAK_3401`
- role: `RAK_3401_repeater_lora_ota_no_external_sensors`
- starting version: `1.16.7.0`, build `c1caa5ad`
- starting EndF body hash: `71F4026CBE4B8B74`
- destination key used for this deployment:
  `63d8df6387eaffd2e25db7d2a8ad967a65202182a48d681d7e7a9260f917280d`

Do not use this chain on another node, and do not start it on another RAK3401.
The physical test has withdrawn this exact chain. Use it only for offline
diagnosis; the runner refuses live deployment even with
`--accept-test-candidate`.

## Withdrawn second v1.17.01 candidate

The replacement rebuilt steps 6 through 15 around the first observed SHA
failure. Step 15 used software SHA-256 and passed its physical EndF check.
Step 16 re-enabled CC310 SHA with checked return codes. It downloaded and
applied successfully, booted `v1.16.9.112-ea3843e0`, then returned `ERR no
EndF`. This proves that CC310 can report success yet produce a wrong digest
for the memory-mapped application image; return-code fallback is insufficient.

```text
/home/mesh/git/MeshCore/out-rak3401-mota-v1.17.01-corrected/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-c96bdd6e.zip
```

ZIP SHA-256:
`46c7480ed6bdc2aa01fb23a0f70e34c4012ffdd42b616d07bde66cf66d594630`.
The largest package is 88,280 bytes, leaving 1,832 bytes below the staging
limit. All 26 deltas reconstruct with both zero-filled and 0xFF-filled
workspaces. The endpoint has whole-image SHA-256
`e923f6209e0f071a5862ffbc7690e0b355c1e74829067e0bfdacd0de6c316065`
and EndF body hash `AC267B02E055F42E`.

The withdrawn bundle includes the exact v1.16.7 test-start UF2/Nordic DFU ZIP, a
diagnostic corrected step-6 image, and the final recovery UF2/ZIP. Restore the
lab node locally to the included test-start image before rerunning the chain.
The bundle's `RUNBOOK.md` is retained for diagnosis only.

## Requirements for the next chain

The next chain must use software SHA-256 for every bridge. CC310 may remain
enabled for AES, HMAC, and entropy, but not for either `Utils::sha256`
overload. The guarded `ota rescue install <base_hash16>` command must be in
the first installed bridge (step 1) and every target after it. Offline
validation must reconstruct each target and prove that command is present;
the live runner also probes `ota help` after every successful transition and
refuses to expose a bridge without the rescue path to another update.

## Withdrawn v1.17.02-chain smoke-test result

The lab topology used a Heltec V4.3 OLED ESP32 Full Companion on
`/dev/ttyACM0` and a direct LoRa path to `RAK3401 1W Repeater`. The remote
admin password is the lowercase default `password`, not `Password`.

Steps 1 through 5 completed and passed an exact post-boot version and EndF
body-hash check. Step 6 then did all of the following successfully:

- downloaded all 79 blocks of mOTA ID `7C8BC884`;
- verified the watchdog was off immediately before install;
- returned `verified (unsigned); applying`;
- booted `v1.16.08.7-halo-keymind-cascade-b-0c4ed3ac`;
- retained a soft-reset boot record consistent with OTAFIX apply success.

The resulting bridge returns `ERR no EndF (firmware lacks the trailer?)` from
`ota self`. The release package is not corrupt: independently applying its
delta reconstructs the expected 524,204-byte image with whole-image SHA-256
`61ced8b63953c614748c2fa1b04c2c01e8eb6626a604f6ef95fd2594d6d8ce71`
and valid EndF body hash `A0130F9BD6760D47`.

The regression is in the intermediate application. This is the first bridge
in the chain built with the old CC310 hardware-crypto SHA path. That path
passes the memory-mapped application body to CC310 and ignores a failed return
code instead of falling back to software SHA. The preceding v1.16.08 image
uses software SHA, while later code contains the required checked fallback.
The normal nRF52 install gate correctly refuses step 7 when it cannot validate
the running body. Forcing the bootloader trigger would bypass an application
safety gate and is not an acceptable recovery.

The lab RAK remains alive on v1.16.08.7. Its normal radio is restored to
`910.525,62.5,7,5`, and `system.watchdog` is restored and verified `on`.

## Recover the lab RAK

The latest physical run stopped on `v1.16.9.112-ea3843e0`: normal radio is
restored to `910.525,62.5,7,5` and `system.watchdog` is verified `on`, but the
application reports `ERR no EndF`. That bridge predates the guarded rescue
command, so recovery requires a local data path to the RAK3401 itself. The
Heltec controller cable cannot recover the remote RAK.

1. Connect the RAK3401 itself to this host by USB.
2. Enter its exact-board UF2 bootloader, using a local `uf2reset` command or
   the board's double-reset gesture.
3. For another chain test, flash the exact c1caa5ad test-start UF2 from the
   withdrawn bundle's `recovery/test-start/` directory. Do not use its final
   recovery image as a production recovery target; it predates the new
   software-only SHA rule.
4. After it boots, require the c1caa5ad start identity, target `2FA509C1`,
   hardware `RAK_3401`, a valid `ota self` reply, normal radio settings, and
   watchdog `on`.

Do not force an apply or issue a remote recovery reboot. Once the
RAK USB cable is attached, the port and UF2 mount can be detected and the
recovery completed without guessing device paths.

## Intended runner safety behavior

Stable OTAFIX 2.4 cannot feed a system watchdog inherited by the bootloader.
The dormant live path implements the release's watchdog sequence:

1. Persist `system.watchdog off` without issuing a normal reboot.
2. Wait 90 seconds for the already-running watchdog to reset the node.
3. Require the node to report `> off`.
4. Prove another 90 seconds of stable uptime.
5. Enforce the requested `ota config hops` reach before every chain step.
6. Recheck `> off` immediately before every `ota install` command.
7. Re-enable the watchdog only after the exact step 26 image boots and passes
   both version and EndF body-hash verification.

These controls remain requirements for the next candidate. They cannot repair
a running bridge that lacks both valid app-side EndF validation and the rescue
command. A replacement must pass a complete physical-board run before its
lab-only gate is removed.

An interrupted download is resumable. Avoid interrupting power or radio
coverage while `ota install` is applying a package. Physical USB recovery is
the fallback if an apply is interrupted at the wrong instant.

## Host and radio requirements

Install:

- Python 3.9 or newer
- `meshcli` 1.6.0 or newer
- a current `motatool`
- a Full Companion or OTA-enabled repeater to seed the files

The ESP32 Full Companion serves mOTA data through its dedicated TCP port
`5001`; its USB port remains the Binary API controller. Its local OTA and
TempRadio console is TCP port `5002`. For a Heltec Full Companion at
`192.168.1.51`, use these three connections together:

```text
controller:  /dev/ttyACM0
mOTA source: 192.168.1.51:5001
source CLI:  192.168.1.51:5002
```

Before a deployment, verify that USB and TCP port `5000` report the same
Companion public key:

```bash
meshcli -s /dev/ttyACM0 -j -c off infos
meshcli -t 192.168.1.51 -p 5000 -j -c off infos
```

The tested Heltec key is
`8ce031bc322f3cf77376eeacfe8800b30d8c5dfe927f3636d801f3455a2dd4cf`.
Change the IP address for a different deployment.

The source, controller, destination, and every intermediate relay must support
the temporary tuple `909.950,250,5,5`. Confirm that this frequency and
bandwidth are legal at the deployment location. All nodes return to their
ordinary radio after each step.

## Verify without touching a radio

The chain runner pins the release ZIP SHA-256 to
`46c7480ed6bdc2aa01fb23a0f70e34c4012ffdd42b616d07bde66cf66d594630`.
It also pins and verifies the inner checksum list, checks complete checksum
coverage, parses every manifest, verifies the chain continuity, and runs
`motatool verify` on all 26 containers.

It downloads the release automatically when `--bundle` is omitted:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

An already-downloaded asset can be supplied with `--bundle`. Only the pinned
ZIP or its extracted root is accepted.

## Withdrawn-release verification

No password or device connection is needed for offline verification:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --work-dir ./rak3401-mota-chain-work \
  --motatool /path/to/motatool \
  --verify-only
```

Use `--verify-only` for this withdrawn bundle. Every live invocation exits
before connecting to a device, including one with `--accept-test-candidate`.
The older failed v1.17.01 and v1.17.02 chains are also recognized and blocked.

## Future direct lab template

This template becomes usable only after a new ZIP and its hashes are pinned in
the runner. Use it only on a locally recoverable test RAK3401. Keep the work
directory for resume, and retain the explicit `--accept-test-candidate` gate.

```bash
export MESHCORE_ADMIN_PASSWORD='password'

python3 tools/lora_ota/rak3401_mota_chain.py \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --ota-hops 3 \
  --accept-test-candidate \
  --motatool /path/to/motatool \
  --yes
```

The intended runner logs each isolated step attempt below `steps/`, records every
verified transition in `progress.jsonl`, and prints the source log path. Rerun
the exact command after a host restart or recoverable RF failure. Status polling
starts at 60 seconds, expands adaptively when replies are slow or the link is
contended, and contracts after quick replies. The transfer itself is primary
traffic, so reserve the TempRadio window for OTA on a busy or relayed mesh.

## Relayed lab template

Every intermediate relay must enter the same TempRadio tuple. Add one
`--relay` argument for each intermediate repeater, ordered from the destination
side back toward the controller. In other words, list the farthest relay first
and the nearest relay last. A bare relay name uses
`MESHCORE_ADMIN_PASSWORD`; append `=PASSWORD` only when that relay has a
different password.

The word "hop" is sometimes counted two ways:

- `controller -> relay -> destination` is two RF links but has one
  intermediate relay, so pass one `--relay`.
- `controller -> near relay -> far relay -> destination` has two intermediate
  relays, so pass two `--relay` values, `far` first and `near` second.

For one intermediate relay:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --ota-hops 3 \
  --relay 'Intermediate Repeater' \
  --accept-test-candidate \
  --motatool /path/to/motatool \
  --yes
```

For two intermediate relays:

```bash
python3 tools/lora_ota/rak3401_mota_chain.py \
  --work-dir ./rak3401-mota-chain-work \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.51:5001 \
  --source-cli-tcp 192.168.1.51:5002 \
  --source-shares-controller \
  --ota-hops 3 \
  --relay 'Far Repeater' \
  --relay 'Near Repeater' \
  --accept-test-candidate \
  --motatool /path/to/motatool \
  --yes
```

Do not begin a production deployment until a replacement bundle passes a
direct physical smoke test from start to endpoint. Run `--preflight-only` with
the same relay arguments first. The runner sends
the destination to TempRadio, then the relays farthest-to-nearest while their
remaining normal routes still exist. It checks that the 120-minute TempRadio
window covers the selected relay count and all configured timeouts. The
starting build may report `hops=0` after the watchdog-driven reset, so the
runner re-enforces `--ota-hops 3` before every step. That setting accepts OTA
packets received through up to three intermediate repeaters.

For a lossy multi-hop path, increase `--reply-timeout`,
`--discovery-timeout`, or `--transfer-timeout-minutes` as needed. If their sum
no longer fits the TempRadio window, also increase the minutes in
`--temp-radio`; the runner rejects an unsafe combination before connecting.

## Completion checks

Success requires all of the following:

- version `v1.17.1.0`
- target ID `2FA509C1`
- hardware `RAK_3401`
- final EndF body hash `AC267B02E055F42E`
- `get system.watchdog` returning `> on`

Do not treat a successful transfer alone as completion. Each temporary bridge
is a complete bootable image, but the RAK should not be left on a bridge
version between planned maintenance sessions.

See [LoRa OTA automation](lora_ota_automation.md) for the generic single-step
workflow and source topology details.
