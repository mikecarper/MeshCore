# `tools/mota/` - OTA Python reference library & build/test tooling

The Python side of MeshCore's `.mota` OTA system. It is the **reference implementation** of the wire
spec ([`docs/ota_protocol.md`](../../docs/ota_protocol.md)) and the **build + test infrastructure** - it is
no longer a user-facing CLI.

> **Want to build / verify / inspect / serve `.mota` from the command line?** Use the standalone
> [`motatool`](https://github.com/vk496/motatool) Rust CLI (its own repository). It supersedes the old
> `mota.py` / `mota_seeder.py` (now removed) and produces byte-identical containers. The files here are
> the spec oracle and the firmware build/test glue.

## Setup

Use one pipx-managed `detools` environment. `cryptography` (Ed25519) and
`intelhex` (nRF52 `.hex` handling) are injected into that same environment;
they are not separate pipx applications.

```bash
pipx install detools
pipx inject detools cryptography intelhex
DETOOLS_PYTHON="$(pipx environment --value PIPX_LOCAL_VENVS)/detools/bin/python"
```

## Files

| File | What |
|---|---|
| `motalib.py` | Core logic: multihash, EndF, merkle tree+proofs, manifest/container build/parse/verify. The **reference implementation** of the spec and the unit-test oracle. Imported by everything below. |
| `pio_endf.py` | **PlatformIO post-build hook** (wired in `platformio.ini`) that injects the `EndF` self-identity trailer into the flashed firmware (`-D ENABLE_OTA`). |
| `gen_vectors.py` | Generates `test/test_ota/mota_vectors.h` - the cross-check vectors the native C++ tests run against. |
| `gen_targets.py` | Generates `src/helpers/ota/OtaTargets.h` - the `target_id -> env-name` table (every resolved `ENABLE_OTA` env plus qualified build.sh-only release aliases). Shared by the firmware and `motatool` so a node can name a target seen over the air without sending the string. Regenerate when the OTA env or release-alias set changes. |
| `test_mota.py` | Unit tests for `motalib` (run directly or via pytest). |

## Tests

```bash
"$DETOOLS_PYTHON" tools/mota/test_mota.py      # EndF, merkle+proofs, v2 apps/v3 nRF52 bootloader,
                                               # signing, tamper detection, approval enforcement
"$DETOOLS_PYTHON" tools/mota/gen_vectors.py    # regenerate the native-test cross-check vectors
"$DETOOLS_PYTHON" tools/mota/gen_targets.py    # regenerate src/helpers/ota/OtaTargets.h (needs `pio`)
```

## `EndF` build integration

`EndF` must live in the **flashed** firmware (not just inside the `.mota`), because a node serves its own
firmware and matches a delta's `base_hash` against its own `EndF`. Wiring (handled by `pio_endf.py`):

- **ESP32 / RP2040** (emit `firmware.bin`): `post:tools/mota/pio_endf.py` in the env's `extra_scripts` plus
  `-D ENABLE_OTA=1`. The hook appends the 56-byte `EndF` (with `target_id`/`fw_version`/`hw_id`) to the app
  `.bin` before merge.
- **nRF52 / STM32** (emit `.hex` -> `.uf2`): the same hook rewrites the `.hex` with the trailer at the image
  end. The byte logic is `motalib.ensure_endf`, used everywhere.

`target_id` = `sha2-256:4(pio_env_name)`, `hw_id` = explicit `-D MOTA_HW_ID` or a role-stripped hardware
family derived from the environment name, and `fw_version` = parsed from `FIRMWARE_VERSION`. A node (and
`motatool`, reading the firmware's `EndF`) therefore auto-discovers identity without relying on filenames.

## Privileged nRF52 bootloader reference builder

Ordinary calls to `build_manifest()` continue to emit v2 application packages.
New application packages default to 2048-byte blocks; pass 1024 explicitly when
targeting a deployed receiver that predates the extended transport descriptor.
Passing `bootloader=True` is a deliberately narrow reference-only path: it
requires a nonzero version, a signed exact 40 KiB OTAFIX region, 1024-byte
blocks, full codec, zero base hash, a sane vector table, an exact embedded
manifest/CRC identity, and an ABI-3 marker retaining both application codecs
(`FULL|INPLACE`, mask `0x0005`), the selected storage, and boot-update
capabilities. Deployed XIAO packages retain their
`XIAO_BL_28860044`/`XIAO_BL_28860045` IDs. Generic packages derive the padded
`NRF_BL_<BOARD_ID>_<DEVICE_NAME>` ID and collision-checked wire target from the
full embedded manifest pair. Parsing and `verify()` repeat those gates; magic
literals that are not complete valid structures are skipped.

Generic parsing remains available for candidate inspection, but the signing
builder accepts only identities in the qualified inventory. That inventory
currently covers the shared-internal nRF52840 targets, the two deployed XIAO
raw-QSPI identities, and the exact MeshTower V2 identity whose candidate
marker may select either its internal (`0x0A`) or microSD (`0x09`) application
layout. The test suite verifies those boot target IDs are unique and disjoint
from the generated application target table.

This library does not authorize a device update. A capable node will only arm
such a v3 package through the exact manual confirmation described in
[`docs/ota_nrf52_bootloader_update.md`](../../docs/ota_nrf52_bootloader_update.md).
The ordinary `tools/lora_ota` runner rejects bootloader packages.
