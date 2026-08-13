# RAK3401 1W repeater LoRa update chain

> Status: the corrected v1.17.01 26-package release candidate has passed
> complete offline reconstruction, independent container verification, and
> both stable OTAFIX 2.4 and Preview 6 C simulations. It still needs its first
> complete physical RAK3401 run and is gated behind
> `--accept-test-candidate`; do not use it in production yet.

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
The artifact checks do not replace the pending complete physical-board run.
Use this candidate only for the next recoverable lab test.

## Corrected test-candidate bundle

The replacement rebuilds steps 6 through 14 so both `Utils::sha256` overloads
use software SHA-256; hardware AES and HMAC stay enabled. Temporary steps 14
through 25 have monotonic EndF versions `1.16.9.110` through `1.16.9.121`.
The final endpoint is a real v1.17.01 build from `c96bdd6e`, with both its
runtime string and EndF version set to `1.17.1.0`. No v1.17.02 endpoint is used.

```text
/home/mesh/git/MeshCore/out-rak3401-mota-v1.17.01-corrected/RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-c96bdd6e.zip
```

ZIP SHA-256:
`693f08187e42cce72124f01328983965726bfbbb3fef80de503f06c4cbe9256a`.
The largest package is 88,280 bytes, leaving 1,832 bytes below the staging
limit. All 26 deltas reconstruct with both zero-filled and 0xFF-filled
workspaces. The endpoint has whole-image SHA-256
`e923f6209e0f071a5862ffbc7690e0b355c1e74829067e0bfdacd0de6c316065`
and EndF body hash `AC267B02E055F42E`.

The candidate includes the exact v1.16.7 test-start UF2/Nordic DFU ZIP, a
diagnostic corrected step-6 image, and the final recovery UF2/ZIP. Restore the
lab node locally to the included test-start image before rerunning the chain.
The bundle's `RUNBOOK.md` contains the direct and two-hop commands.

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

Recovery now requires a local data path to the RAK3401 itself. The Heltec
controller cable cannot recover the remote RAK, and this host currently has no
Bluetooth adapter.

1. Connect the RAK3401 itself to this host by USB.
2. Enter its exact-board UF2 bootloader, using a local `uf2reset` command or
   the board's double-reset gesture.
3. Flash the final RAK3401 UF2 from the corrected bundle's `recovery/final/`
   directory:
   `RAK_3401_repeater_lora_ota_no_external_sensors-ota-v1.17.01-halo-keymind-cascade-dev-c96bdd6e.uf2`.
4. After it boots, require version `v1.17.1`, target `2FA509C1`, hardware
   `RAK_3401`, a valid `ota self` reply, normal radio settings, and watchdog
   `on`.

Do not send step 7, force an apply, or issue a remote recovery reboot. Once the
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

These controls are active for the corrected candidate. They cannot repair the
old bridge whose own EndF check is broken. The candidate must pass a complete
physical-board run before its explicit lab-only gate is removed.

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
`693f08187e42cce72124f01328983965726bfbbb3fef80de503f06c4cbe9256a`.
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

## Corrected-release live preflight

The remote-admin password for the tested RAK is the lowercase default
`password`. Prefer the environment variable so it is not placed in the child
process command line:

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
  --preflight-only
```

This validates the corrected bundle, Full Companion identity, source, and
destination without changing radio or watchdog settings. Omitting
`--accept-test-candidate` exits before connecting to a device. The withdrawn
v1.17.02 chain is also recognized and always blocked before device access.

## Direct lab template

Use only on a locally recoverable test RAK3401. Keep the work directory for
resume, and retain the explicit `--accept-test-candidate` lab gate.

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
the exact command after a host restart or recoverable RF failure. Its default
60-second status interval deliberately leaves airtime for the low-priority OTA
transfer; avoid shortening it on a busy or relayed mesh.

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

Do not begin a production deployment until the corrected bundle passes a
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
