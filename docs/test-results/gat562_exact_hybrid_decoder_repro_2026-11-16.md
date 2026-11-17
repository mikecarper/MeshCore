<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/gat562_exact_hybrid_decoder_repro_2026-11-16/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Exact GAT562 hybrid decoder reproduction

The reported transfer `9DF841DF`, 234 blocks, is reproduced by the first
published Full application bridge from GAT562 30S Mesh Kit 1.18.0.1. This test
runs the complete pinned OTAFIX 2.4.12 `ota_delta_check_and_apply()` C entry
point with a separate retained-RAM suffix and simulated NOR flash. It does
not emulate the ARM processor, flash timing, power, watchdog or application
startup.

| Input | Size | SHA-256 |
| --- | ---: | --- |
| Original 1.18.0.1 Full, release `03214a16` | 601752 | `c308f6e097045692d3b9ab7f4f542b8a01ce6ed21954e905734ab1f84fe4706e` |
| First Full bridge, source `27d9aaf7d` | 606800 | `d06a56d213ff15b3bd03b87ebf36b31d842d0226a450804f5162d575786fe2a9` |
| Reconstructed first-bridge package | 240061 | `5cdacdbb1b2b3dc4a6c1105da66fd9470427decb0211b28610394dc70aa749ba` |

The package uses an in-place workspace of 626688 bytes, a 176128-byte flash
prefix and a 63933-byte retained-RAM suffix. The harness models full-page
erases and writes that only clear NOR bits, and checks that application writes
occur only after the application bank is invalidated. It also computes the
image CRC used by the final boot-bank setting.

The three checks pass:

- Exact package: result `B8`, a valid bank, matching image CRC and byte-for-byte
  reconstruction of the complete bridge.
- Damaged retained suffix: rejection before any application write; the original
  image remains intact.
- Failed destructive flash write: failure leaves the bank invalid, which is
  the bootloader's deliberate DFU recovery behavior.

The published GAT562 OTAFIX 2.4.12 binary's initial stack pointer is
`0x20030000`, below the retained arena. Its continuity metadata and machine
code use application base `0x26000`. Its final image CRC routine executes a
physical byte-read loop after the post-write SHA check and before the settings
commit; inspection did not show a CRC calculation moved before the update.

These results do not identify the cause of the reported physical failure.
In particular, a correct decoded image does not prove that the intermediate
application starts or operates on a physical GAT562. The bootloader's result
code or a pre-recovery flash capture would distinguish a decoder/flash failure
from an application startup failure. USB reflashing replaces that evidence.

## Reproduction

Place the exact original BIN, decoded bridge BIN, and reconstructed package
in a local directory as `1.18.0.1.bin`, the full bridge SHA-256 plus `.bin`,
and `first-bridge.mota`. The original release is the Full GAT562 30S Kit
LoRa-OTA/no-external-sensors artifact for 1.18.0.1. The bridge is recorded in
the immutable browser bridge bank.

Generate the exact package with `lora_ota_delta.buildInPlaceDelta()` using
`allowSameVersion: true`, `memorySize: 626688`,
`runtimeStageCeiling: 0xED000`, and
`runtimeHybridRam: 'OTA RAM: hybrid=1 abi=1 record=72 arena=65536'`.
The test verifies all three complete SHA-256 values before execution.

```powershell
$env:MESHCORE_GAT_REPRO_DIR = 'C:\path\to\exact-inputs'
python test/test_gat562_hybrid_repro.py -v
```

The test requires GCC and the repository's pinned OTAFIX source. It skips
without the explicitly provided offline fixture directory. No hardware is
accessed and no firmware is installed.
