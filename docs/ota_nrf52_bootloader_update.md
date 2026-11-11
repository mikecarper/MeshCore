<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/ota_nrf52_bootloader_update/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# nRF52 bootloader updates over LoRa

For **MeshCore 1.18.0.1**, choose the exact board and storage layout in the
[firmware picker](firmware_picker.md). The matching bootloader release is
[OTAFIX 2.4.12](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/v0.11.0-OTAFIX2.4.12).
Firmware and bootloader versions are separate. See the
[1.18.0.1 update guide](ota_user_guide.md) for ordinary application updates.

For an application image that carries its own board-matched 2.4.12 replacement,
see [embedded nRF52 bootloader updates](nrf52_embedded_bootloader.md). A separate
`with-bootloader-2.4.12` image checks once after 70 seconds of startup and updates
a recognized compatible older bootloader when radio and OTA activity are idle.
The authenticated admin activation command remains available for manual retry.

Selected nRF52840 Repeater and Room Server LoRa-OTA builds can replace their matching OTAFIX
bootloader without replacing the running application. This is a privileged
maintenance path, not a normal firmware update. A stock bootloader must first
be replaced with the exact ABI-3 self-update-capable OTAFIX build over USB/BLE
DFU or SWD. MeshTower SD also requires the BLM2 retained-auth version to be
provisioned locally before either application or bootloader OTA.

The released adaptive RAK3401 and RAK4631 OTAFIX 2.4.8/2.4.9 profiles support
application full and delta updates, but not bootloader self-update over LoRa.
USB UF2 and local Bluetooth/serial Legacy DFU bootloader updates still work.
Their running bootloader protects `0xF4000..0x100000` with the nRF52840 ACL before starting
MeshCore. This includes the MBR parameter page at `0xFE000`, so an application
cannot initiate `COPY_BL` even after verifying a signed bootloader package.
Hardware testing on a RAK3401 confirmed the MBR SVC enters a bus fault and
leaves the installed bootloader unchanged. For new RAK installations use the
normal OTAFIX 2.4.12 `wiscore_rak3401_auto` or `wiscore_rak4631_auto` image.
These retain the standard `3401_DFU` / `4631_DFU` identities. A compatible
standard installation can receive the exact signed bootloader `.mota` over
LoRa. Historical `3401_AUTO_DFU` / `4631_AUTO_DFU` and dedicated external-storage
identities need the matching local recovery bridge before moving to the normal
image; select it from the separate `OTAFIX-2.4.12-R_recovery.zip` and follow the
release's recovery instructions. Recovery images are migration tools, not the
normal download.

## SenseCAP Solar P1 (experimental)

The P1 Repeater and LoRa-OTA Room builds support the signed QSPI bootloader
workflow below, but an end-to-end bootloader update and recovery have **not yet
been tested on P1 hardware**. Do not rely on this for an inaccessible device.
The installed bootloader must report board `28860044`, name `SCAP_DFU` and
self-update capability; a XIAO bootloader has a different identity even when
the board ID matches. The running application must preserve the scratch area
and its boot settings must not describe a CRC-bound bank overlapping it.

Install the matching application first and check `ota bootloader status`. The
official OTAFIX 2.4.12 bootloader signing key is built in for bootloader
packages, so it does not need to be added with `ota key add`. Stage the exact
signed P1 bootloader package and use `ota bootloader install <MID8> <HASH16>`
to explicitly authorize it. Application OTA and bootloader OTA are separate.
This path stages the package in onboard QSPI and uses application-flash scratch;
it does not unlock protected bootloader pages or enable bootloader updates on
an unsupported installed bootloader.

The built-in key is used only for signed bootloader packages; it does not add a
trusted application-firmware signer or change the saved user key list. Other
bootloader signers still need to be added explicitly. Installation requires
the separate administrator confirmation and all board and flash checks.

## Storage layouts

| Layout | Application limit | Staged boot package | Work area | Handoff / capability |
| --- | ---: | ---: | ---: | --- |
| XIAO-module or experimental SenseCAP Solar P1 raw QSPI | below `0xE0000` | external QSPI offset 0 | dedicated internal `0xE0000..0xEA000` scratch | source `0x51`, flags `0x0E` |
| Qualified internal-flash target | normal `0xED000` flash limit; top 64 KiB SRAM reserved for application-delta staging | shared internal slot, exact start `0xE2000` | the same eleven-page slot; no second flash reservation | source `0xED`, flags `0x0A` |
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

The removable SD authorization is fail-closed for both application and
bootloader packages. MeshCore directly authenticates one exact signed
manifest, requires the streamed copy to remain byte-identical, verifies all
leaves/payload/image bytes, and computes a normalized full-container SHA-256 in
that same pass (`APRV` bytes are treated as zero). After syncing `APRV`, it
publishes a 72-byte `MOTASDA2` record in reset-retained RAM at `0x20006008`.
That record binds purpose/format, first LBA, sector count, total length, card
sector count, and the normalized digest. OTAFIX copies, validates, and clears
the record before SD access. A card swap/mutation or a power cycle therefore
fails closed; there is no general sector-1 ownership inference.

For fmt3, MeshCore also writes and readback-verifies a 64-byte `MOTASDBL` token
at `0xE0000`. That token contains the exact container length and the exact
authenticated signed manifest `image_hash`. OTAFIX requires the same hash in
the parsed manifest, streamed payload, and final scratch image. The page is
consumed as scratch during success and is not permanently reserved.

The first BLM2-capable MeshTower bootloader cannot be delivered to preview.12
through the retained-RAM protocol that preview.12 predates. MeshCore does not
write a raw-sector compatibility record because a blank sector is not proof of
ownership. Upgrade preview.12 through USB/BLE DFU or SWD. Both fmt2 application
and fmt3 bootloader OTA then require the installed BLM2 metadata to match the
live SoftDevice/application layout; neither MeshCore nor OTAFIX uses sector 1.

The internal path does not change the application's `0xED000` flash limit or
permanently set aside separate app-OTA, boot-package, and flash-scratch
regions. Qualified applications do use a dedicated linker that reserves the
top 64 KiB of SRAM for hybrid application-delta staging and a retained
authorization record. That SRAM is never part of a bootloader-update package.
The ordinary bottom-aligned internal flash store holds one container prefix at
a time: either an application delta or the complete bootloader package.

The exact bootloader container is 41,330 bytes: 365 bytes of signed mOTA
metadata, a 40 KiB payload, and the five-byte trailer. Below the normal
`0xED000` store ceiling it bottom-aligns at `0xE2000`. Admission requires a
hash-valid live `EndF` proving the current application, including its trailer,
ends at or before `0xE2000`. OTAFIX then reads each source window before
erasing and compacts the payload forward in place to the page-aligned raw range
`0xE2000..0xEC000`; it verifies every page and the whole image before asking the
MBR to copy that image over `0xF4000..0xFE000`.

An ordinary application delta can be smaller or larger than this eleven-page
shape. On the qualified profile, a delta larger than one page uses the minimum
page-aligned flash prefix ending at `0xED000` and keeps up to 64 KiB of its
logical tail in reset-retained SRAM; its detools workspace must stop at the
actual flash-prefix start. A one-page-or-smaller delta stays wholly in flash.
In either case the reconstructed application must stop below `0xED000`. The
two package kinds are mutually exclusive because they use the same flash
store, and a bootloader package never uses the volatile SRAM suffix.

For internal-self-update builds, an absent or corrupt live `EndF` disables
**all** internal staging before the first erase. The older 608 KiB rescue
estimate is unsafe when a normally linked application may extend to
`0xED000`. Builds without this feature retain the legacy rescue behavior.

## Internal-flash target inventory

The release builder consumes the allowlist in
`tools/mota/nrf52_internal_bootloader_targets.txt`. It includes literal
PlatformIO environments and release aliases; build aliases through `build.sh`
so the common pre-build guard receives the same storage/layout decision.
The inventory includes lean repeaters and bridges plus the explicitly listed
adaptive RAK receiver roles. Their bootloader identities are listed below;
use the picker for current application names and downloads.

| Build target(s) | Installed OTAFIX identity | Boot target ID |
| --- | --- | ---: |
| Historical internal-only MeshTower V2 layout; current releases use the combined SD target | `239A0071 / TOWER_V2_OTA` | `1150F50E` |
| T096 lean repeater and RS232 bridge | `239A0071 / T096_DFU` | `42354C85` |
| `Heltec_t1_repeater_lora_ota_no_external_sensors` | `239A0071 / T1_DFU` | `FC556FFC` |
| T114 display and without-display lean repeaters | `239A0071 / T114_DFU` | `0C3F2902` |
| `Mesh_pocket_repeater_lora_ota_no_external_sensors` | `239A0071 / MESH_POCKET_OTA` | `059277F4` |
| `KeepteenLT1_repeater_lora_ota_no_external_sensors` | `239A00B3 / KeepteenLT1_OTA` | `DB2E7B51` |
| `Minewsemi_me25ls01_repeater_lora_ota_no_external_sensors` | `239A0029 / MX25_DFU` | `026AA982` |
| `ProMicro_repeater_lora_ota_no_external_sensors` | `239A00B3 / PROM_DFU` | `AF79E8CC` |
| `t1000e_repeater_lora_ota_no_external_sensors` | `28860057 / T1KE_DFU` | `E6F5F03F` |
| `ThinkNode_M3_repeater_lora_ota_no_external_sensors` | `239A00DA / TNM3_DFU` | `0CA41DB2` |
| RAK3401 reduced repeater and unified repeater | `239A0029 / 3401_DFU` | `23818A80` |
| RAK4631 reduced/unified repeater, both reduced RS232 bridges, and listed reduced Room/Sensor roles | `239A0029 / 4631_DFU` | `2D0DF000` |
| GAT562 30S/Tracker Pro/EVB Pro lean targets | `239A0029 / GAT562_DFU` | `D50D2D44` |
| `R1Neo_repeater_lora_ota_no_external_sensors` | `239A0029 / 4631_DFU` | `2D0DF000` |
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

Earlier GAT562 installations may report the RAK-compatible `4631_DFU`
identity. The installed and candidate identity pair must match exactly, so a
remote bootloader update cannot migrate one of those devices to `GAT562_DFU`.
Provision the GAT562-specific OTAFIX bootloader once through USB/BLE DFU or SWD;
subsequent signed bootloader mOTA updates use target `D50D2D44`.

## Embedded continuity and version policy

Every remotely supplied successor retains the legacy 44-byte CRC-valid `BLMF`
v1 record for identity continuity and diagnostics. Immediately after that
record is a 32-byte `BLM2`/`SOFT` extension. The complete 76-byte envelope is
fixed at the final raw-image offset `0x9FB4`; relocated candidates are refused,
while installed legacy-v1 discovery remains a generic diagnostic scan. The extension carries the actual packed
bootloader version, SoftDevice family and FWID, application base, layout ABI,
and zero compatibility/reserved fields; the legacy whole-image CRC covers the
extension too. Scanners first count CRC-valid 44-byte base manifests, then
interpret continuity only after one base identity remains. A malformed claimed
extension therefore cannot make a duplicate identity disappear, and a sole
half-present extension is rejected rather than treated as legacy.

MeshCore requires the candidate extension, exact equality between its embedded
version and the signed outer mOTA version, and exact agreement with the running
SoftDevice family/FWID/application layout. Qualified internal/QSPI paths may
bootstrap a CRC-valid installed legacy-v1 image once; the SD path never does
and requires local BLM2 provisioning. Once the installed bootloader has BLM2
metadata, the candidate version must be strictly greater. Preview low bytes are `1..254`, a
stable release uses `0xFF`, and low-byte zero or all-ones values are rejected.
There is no remote rollback/migration override; intentional rollback uses
USB/BLE DFU or SWD.

The legacy `no_external_sensors` profiles trim selected optional environmental
and ranging drivers to preserve flash headroom; they do not generally disable
the I2C bus. Reduced RAK3401 and RAK4631 recipes retain INA219, INA226, INA260,
and INA3221 voltage/current monitors. Those are entries in the optional sensor
table, not the complete set of I2C consumers: the SSD1306 OLED, supported
autodiscovered RTCs, and RAK12500 GPS remain separate I2C peripherals where the
exact board recipe enables them. RAK3401 retains its compatible RAK12500 I2C
and RAK12501/L76K UART GPS paths in sensor slot A. The plain RAK4631 repeater
and Serial2 bridge retain GPS. Its RAK12501 path uses Serial1; the explicitly
compiled Serial1 RS232 bridge therefore omits the combined GPS provider,
including RAK12500, even though RAK12500 itself is I2C rather than UART.

The firmware-configured INA3221 and RAK12500 addresses are both `0x42`. They
cannot coexist on the same bus at those addresses. Leave RAK12500 at `0x42`,
strap INA3221 A0 to SCL for `0x43`, and use firmware built with
`-DTELEM_INA3221_ADDRESS=0x43` when both modules are installed.

The current MeshTower V2 release uses the combined SD/internal bootloader;
an internal-only installation needs the local migration described in the
[MeshTower SD guide](ota_meshtower_v2_sdcard.md). Sharing a board/name pair
does not make incompatible storage layouts interchangeable.

Boards with onboard external flash are not silently redirected to internal
staging. Mesh Solar, Nano G2 Ultra, T-Impulse Plus, ThinkNode M8, T-Echo
Lite/Card, MeshTracker X1, and Wio WM1110 have board-specific QSPI hardware and
need a separately matched QSPI path where available. Full Companions, other
SD/QSPI/ExtraFS roles, source-only roles, and unlisted full-sensor roles are
excluded from the internal-flash inventory. The explicitly allowlisted RAK4631
Ethernet Room role is an exception; the exact MeshTower V2 SD role above has
its own SD path.

This internal layout is limited to nRF52840 devices with 1 MiB internal flash,
the exact S140 v6/v7 map, and the 40 KiB boot region at
`0xF4000..0xFE000`. nRF52833 and smaller nRF52 parts cannot provide that map
plus a non-overlapping 41,330-byte live staging slot, so configuration fails
closed instead of selecting smaller or overlapping geometry.

## Explicit install workflow

The `tools/lora_ota/lora_ota.sh` and `.ps1` runners can perform the discovery,
transfer, verification and radio cleanup for a bootloader mOTA with
`--no-install`. They detect format 3 automatically and check the destination's
bootloader-specific identity/capabilities. They do not install it: the final
MID/hash confirmation below remains an explicit operator action. A mixed
application/bootloader ZIP requires `--zip-member`.

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
typed MID/hash confirmation. The manifest root and target inside the received
manifest must also equal the MID/target that opened the fetch before any store
is allocated. On SD, `APRV`, the retained geometry/container authorization,
and the internal signed-image-hash token are published in that order before
the reset trigger. OTAFIX consumes that application-written authorization and
then
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
consumes the trigger and authorization before its first destructive action, so
an interrupted operation cannot automatically retry a partly consumed
package. On SD, the retained-RAM authorization is zero-consumed before media
access; `APRV` and the fmt3 flash token may persist but are inert without a new
explicit authenticated re-arm and reset trigger. No SD raw-sector handoff is
created or consumed.
Use USB/BLE DFU or SWD for initial provisioning and local recovery.

For XIAO and ordinary external-QSPI details, see
[nRF52 repeater LoRa OTA with external QSPI](ota_nrf52_qspi.md).
