# nRF52 bootloader updates over LoRa

Selected nRF52840 repeater LoRa-OTA builds can replace their matching OTAFIX
bootloader without replacing the running application. This is a privileged
maintenance path, not a normal firmware update. It cannot bootstrap a stock or
older bootloader: install the exact ABI-3 self-update-capable OTAFIX build once
over USB/BLE DFU or SWD.

## Storage layouts

| Layout | Application limit | Staged boot package | Work area | Handoff / capability |
| --- | ---: | ---: | ---: | --- |
| XIAO-module raw QSPI | below `0xE0000` | external QSPI offset 0 | dedicated internal `0xE0000..0xEA000` scratch | source `0x51`, flags `0x0E` |
| Qualified internal-flash target | normal `0xED000` limit | shared internal slot, exact start `0xE2000` | the same eleven-page slot; no second reservation | source `0xED`, flags `0x0A` |
| MeshTower V2 microSD | normal `0xED000` limit | contiguous `/meshcore-ota.mota` | dynamic internal `0xE0000..0xEA000` scratch; live image must end by `0xE0000` | source `0x53`, flags `0x09` |

The exact SD target is
`Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors`. Its normal
application FULL and delta updates continue to use the SD file without the
bootloader-update scratch restriction. For a bootloader package only, both
MeshCore and OTAFIX require a hash-valid live `EndF` proving the complete
running image ends at or below `0xE0000`. If boot settings carry a nonzero app
bank CRC, the recorded bank size must also cover that full EndF-inclusive
image and stop by `0xE0000`; erased or explicitly CRC-disabled settings remain
valid. OTAFIX then copies the verified 40
KiB payload from SD into `0xE0000..0xEA000` and uses the MBR copy operation to
replace `0xF4000..0xFE000`. The application linker remains at `0xED000`; a
future application extending above `0xE0000` can still use application mOTA
but must update its bootloader through local DFU/SWD.

The removable SD authorization is fail-closed. MeshCore directly authenticates
one exact manifest, requires the streamed manifest on SD to remain
byte-identical, and verifies its complete payload. After writing and syncing
`APRV`, but before publishing the raw-sector handoff, it writes and
readback-verifies a 64-byte `MOTASDBL` token at `0xE0000`. The token contains
the exact container length and the authenticated signed manifest's
`image_hash`. OTAFIX requires that same hash in the parsed manifest, the
streamed raw payload, and the final scratch image. A card swap or mutation
after application verification therefore causes refusal rather than
authorizing different bytes; the token page is consumed as scratch during a
successful update and is not a permanent reservation.

The internal path does not change the application linker or permanently set
aside separate app-OTA, boot-package, and scratch regions. The ordinary
bottom-aligned internal store holds one container at a time: either an
application delta or a bootloader package.

The exact bootloader container is 41,330 bytes: 365 bytes of signed mOTA
metadata, a 40 KiB payload, and the five-byte trailer. Below the normal
`0xED000` store ceiling it bottom-aligns at `0xE2000`. Admission requires a
hash-valid live `EndF` proving the current application, including its trailer,
ends at or before `0xE2000`. OTAFIX then reads each source window before
erasing and compacts the payload forward in place to the page-aligned raw range
`0xE2000..0xEC000`; it verifies every page and the whole image before asking the
MBR to copy that image over `0xF4000..0xFE000`.

An ordinary application delta can be smaller or larger than this eleven-page
shape. It bottom-aligns dynamically below `0xED000` and may begin below
`0xE2000`; its detools workspace must stop at its actual container start, while
the reconstructed application must stop below `0xED000`. The two package kinds
are mutually exclusive because they use the same store.

For internal-self-update builds, an absent or corrupt live `EndF` disables
**all** internal staging before the first erase. The older 608 KiB rescue
estimate is unsafe when a normally linked application may extend to
`0xED000`. Builds without this feature retain the legacy rescue behavior.

## Internal-flash target inventory

The release builder consumes the allowlist in
`tools/mota/nrf52_internal_bootloader_targets.txt`. Ten names are also literal
PlatformIO environments and are enabled automatically when built directly.
The other eleven are release aliases assembled from a base environment plus
the lean OTA overlay; build them through `build.sh`, which passes the same
allowlist decision to the common pre-build guard. These lean repeater/bridge
targets have no OTA-owned SD/QSPI store and have an exact curated OTAFIX
manifest identity:

| Build target(s) | Installed OTAFIX identity | Boot target ID |
| --- | --- | ---: |
| `Heltec_tower_v2_repeater_lora_ota_no_external_sensors` | `239A0071 / TOWER_V2_OTA` | `1150F50E` |
| T096 lean repeater and RS232 bridge | `239A0071 / T096_DFU` | `42354C85` |
| `Heltec_t1_repeater_lora_ota_no_external_sensors` | `239A0071 / T1_DFU` | `FC556FFC` |
| T114 display and without-display lean repeaters | `239A0071 / T114_DFU` | `0C3F2902` |
| `Mesh_pocket_repeater_lora_ota_no_external_sensors` | `239A0071 / MESH_POCKET_OTA` | `059277F4` |
| `KeepteenLT1_repeater_lora_ota_no_external_sensors` | `239A00B3 / KeepteenLT1_OTA` | `DB2E7B51` |
| `Minewsemi_me25ls01_repeater_lora_ota_no_external_sensors` | `239A0029 / MX25_DFU` | `026AA982` |
| `ProMicro_repeater_lora_ota_no_external_sensors` | `239A00B3 / PROM_DFU` | `AF79E8CC` |
| `t1000e_repeater_lora_ota_no_external_sensors` | `28860057 / T1KE_DFU` | `E6F5F03F` |
| `ThinkNode_M3_repeater_lora_ota_no_external_sensors` | `239A00DA / TNM3_DFU` | `0CA41DB2` |
| `RAK_3401_repeater_lora_ota_no_external_sensors` | `239A0029 / 3401_DFU` | `23818A80` |
| RAK4631 lean repeater and both lean RS232 bridges | `239A0029 / 4631_DFU` | `2D0DF000` |
| GAT562 30S/Tracker Pro/EVB Pro and R1Neo lean carrier aliases | `239A0029 / 4631_DFU` | `2D0DF000` |
| `RAK_WisMesh_Tag_repeater_lora_ota_no_external_sensors` | `239A0029 / RTAG_DFU` | `C72E9C9C` |

Board IDs are not globally unique. For generic targets, the signed hardware ID
is the exact NUL-padded 32-byte value
`NRF_BL_<BOARD_ID>_<DEVICE_NAME>`. The wire target is the little-endian first
four SHA-256 bytes of all 32 padded bytes. The installed and candidate embedded
manifest pairs must match exactly. XIAO retains its deployed
`XIAO_BL_28860044` / `XIAO_BL_28860045` identity and raw board-ID target.

The Python reference builder and release tooling audit these boot targets for
duplicates and collisions with application target IDs. Generic image parsing
can inspect a future canonical identity, but signing/building a package fails
until that exact identity is in the qualified inventory.

The `no_external_sensors` profiles omit optional/add-on sensor and GPS packages
to preserve flash headroom. Board-integrated GPS can be retained by a board's
recipe. The RAK3401 lean target specifically omits RAK12501/add-on GPS; use the
ordinary full-sensor target when that module is required.

Boards with onboard external flash are not silently redirected to internal
staging. Mesh Solar, Nano G2 Ultra, T-Impulse Plus, ThinkNode M8, T-Echo
Lite/Card, MeshTracker X1, and Wio WM1110 have board-specific QSPI hardware and
need a separately matched QSPI path where available. Full Companions, other
SD/QSPI/ExtraFS roles, Ethernet roles, source-only roles, and unqualified
full-sensor roles are excluded from the internal-flash inventory. The exact
MeshTower V2 SD role above is separately qualified for its SD path.

This internal layout is limited to nRF52840 devices with 1 MiB internal flash,
the exact S140 v6/v7 map, and the 40 KiB boot region at
`0xF4000..0xFE000`. nRF52833 and smaller nRF52 parts cannot provide that map
plus a non-overlapping 41,330-byte live staging slot, so configuration fails
closed instead of selecting smaller or overlapping geometry.

## Explicit install workflow

Check the installed identity and capability marker:

```text
ota bootloader
```

The response must show a CRC-valid exact identity, ABI 3 or newer, both FULL
and INPLACE application codecs (`codecs=0x5`), and exact flags `0x09` for
MeshTower V2 SD, `0x0A` for internal shared storage, or `0x0E` for XIAO QSPI. A
bootloader row is visible in `ota ls`, but it is never autofetched or
autoinstalled. Fetch its exact MID, then explicitly arm it:

```text
ota pull <MID8> flash
# wait for ota status to report the download ready
ota bootloader
ota bootloader install <MID8> <HASH16>
```

Copy both confirmation values from the second `ota bootloader` response.
Ordinary `ota install` rejects a bootloader package; the bootloader command
rejects an application package. The FULL-codec exception exists only for that
manual bootloader MID. Ordinary application FULL remains disabled on an
internal single-slot node, bootloader autofetch remains off, and a partial
bootloader package is not automatically resumed after an application reboot.

Before writing `APRV`, the application authenticates and authorizes the
package: exact v3 geometry, trusted Ed25519 signer, signed/embedded identity,
one unambiguous capability marker, embedded CRC, sane vectors, complete
Merkle/payload/image hashes, storage-specific safe live placement, and the
typed MID/hash confirmation. On SD, `APRV` and the internal signed-image-hash
token are synced and read back before the handoff becomes visible. OTAFIX consumes that application-written
authorization and then
independently rechecks the safety/integrity subset: strict v3 structure,
canonical identity/capabilities, vectors, full payload SHA, embedded manifest
CRC, the applicable live `EndF`/bank-settings no-overlap geometry, the SD token
binding where applicable, scratch readback, and final
copy hash. It does not re-run Ed25519, the signer allowlist, Merkle leaves/root,
or the typed operator confirmation. Success is reported as `blup:C8`.

## Failure behavior

The feature fails closed when the MCU/map is wrong, required valid `EndF`
headroom is insufficient, an unsupported external/ExtraFS role owns the target,
the exact installed capability marker is absent or ambiguous, identity cannot be derived
unambiguously, or any package check fails. Before the storage-specific
scratch/copy step, the application and bootloader are unchanged. OTAFIX
consumes the trigger and, for internal/QSPI storage, clears approval before its
first scratch erase, so an interrupted operation cannot retry a partly
consumed package. The SD backend cannot rewrite raw card sectors: its `APRV`
and checksummed handoff may persist, but the one-shot GPREGRET trigger is
consumed before validation and they remain inert unless the running app again
authenticates and explicitly re-arms that package. Use USB/BLE DFU or SWD for
initial provisioning and local recovery.

For XIAO and ordinary external-QSPI details, see
[nRF52 repeater LoRa OTA with external QSPI](ota_nrf52_qspi.md).
