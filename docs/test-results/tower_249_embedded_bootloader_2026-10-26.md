<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/tower_249_embedded_bootloader_2026-10-26/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# MeshTower SD: embedded updater on OTAFIX 2.4.9

**Tested, 2026-10-26.** The MercerWoodMesh Pi's Tower reproduced
`idle; last=9; auto=locked for this firmware`. Its embedded updater refused
protected flash without changing the audited regions. The separate signed
SD bootloader update then installed official 2.4.12 successfully.

## Exact fixture

| Item | Verified value |
| --- | --- |
| Installed normal Tower SD bootloader | OTAFIX 2.4.9, CRC `C6870878` |
| Board / target / name | `239A0071` / `1150F50E` / `TOWER_V2_OTA` |
| Application | Released Full SD repeater with embedded 2.4.12, source `03214a16` |
| Application body / image | 624,212 / 624,268 bytes; body hash `75134BA65EB52E51` |
| Embedded 40 KiB candidate | SHA256 `c9479891c5d0446d9a86bf2e7af03fdf0ad6032312395f48b2340ab7fe4b22ed` |

The original test application ZIP had SHA256
`42359f1abb69fab0eb500638c9d11f455630d8ee255935255d59c6467f48c17a`.
It was later replaced by the [updated application ZIP](https://github.com/mikecarper/MeshCore/releases/download/lora-ota-v1.18.0.1-halo-keymind-cascade-dev-03214a16/Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors-with-bootloader-2.4.12-full-ota-v1.18.0.1-halo-keymind-cascade-dev-40dd42de.zip),
which was not the image used in this test.
The [normal 2.4.9 bootloader-update UF2](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/download/v0.11.0-OTAFIX2.4.9/update-heltec_mesh_tower_v2_sdcard_bootloader-v0.11.0-OTAFIX2.4.9_mbr.uf2)
SHA256 was `09ca85492c322af09a31fcb7776364e7a63612eba0b545c9abfa8a0ef39e9aab`.

## Automatic check and manual refusal

At 55 seconds uptime, status showed `last=0; auto=70s after boot`. At 72
seconds it showed `last=0; auto=unrecognized`, without a reset. This normal
2.4.9 compact SD image omits the complete `UF2 Bootloader ...` version record.
The application's conservative classifier cannot distinguish it from older
unlabelled recovery images, so it saves the firmware lock and skips automatic
installation. This is separate from the protection failure below.

One explicit `ota bootloader update` acknowledged that it was armed, then
reset. Uptime dropped from 137 seconds to 19 seconds; status matched the
reported `last=9` and automatic lock. Bootloader CRC remained `C6870878`.
Uptime then advanced through 71, 140, 174 and 204 seconds, with zero core
errors and no additional reset. It remained up beyond the 70-second startup
check delay without another attempt.

These audit prefixes were identical before the manual attempt, after its
reset and after the later successful signed SD update:

```text
app=FCB1E90BD3625E64 settings=4764645806638517
fs=194B60A9ECA6A0A0 sd=0972167BB84FD824
```

The first automatic check legitimately changed the filesystem audit when it
created `/bl_auto`. Source inspection maps result 9 to flash protection before
the MBR copy. Manual retry does not unlock that hardware protection.

## Signed SD update and helper correction

The first helper run reused the retained matching SD package, then stopped
before installation because the 160-byte progress reply ended at `id=57CBEC`.
The authoritative staged reply still contained the full MID and image hash.
`ota cancel` removed that test package and confirmed the receive slot clear.

The [helper correction](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/commit/2c48fe556e6580d18593e2f57f2f92411912b541)
uses exact staged identity/hash or the compact fetch status when progress is
truncated. Explicit mismatches still fail, and unconfirmed progress has a
finite retry budget. Both Python updater suites passed, 37 tests total;
the required T096 and T114 CMake builds also passed.

The corrected helper downloaded a fresh 0/40 through 40/40 blocks over LoRa
using the official package MID `57CBECA2`, hash `C9479891C5D0446D`. Source was
a Heltec V4.3 Full Companion; both radios used temporary 909.5 MHz, BW500,
SF7, CR5 and 0 dBm. USB carried target control commands. The install reply
was lost at reboot and was not replayed. Independent checks established:

- Installed 2.4.12, CRC `59326292`, `blup:C8`, no download and no staged image.
- Unchanged application body hash `75134BA65EB52E51` and all four audit prefixes.
- Embedded status `up to date; last=0; auto=locked for this firmware`.
- A further `ota bootloader update` returned `OK bootloader already up to date`;
  uptime advanced from 44 to 46 seconds without a reset.

The Pi Tower's original 1.18.0.1 application `807343de` was restored through
application-only USB serial DFU, retaining the stable 2.4.12 bootloader.
Its body hash returned to `E5324C388E987A8E`. Checked settings and original
signer entries were restored; settings were not erased. The source radio,
folder state, serial ownership and Pi services were restored as well.
This run does not qualify cold-power interruption or wireless admin control.

The local rollback to 2.4.9 produced the previously recorded bootloader-UF2
final `sync` EIO after detach. The returned application independently confirmed
CRC `C6870878`; the copy was not blindly repeated.

Private evidence archive SHA256:
`69ce711c76108b14448e617a669a4896a88dbf56d0b7bddecfbdd32d435e2299`.
