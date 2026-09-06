# nRF52 repeater LoRa OTA with external QSPI

For **1.17.1.5**, use the exact board/storage profile from
[OTAFIX 2.4.6](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6)
for nRF52 OTAFIX installations. New internal-flash hybrid receivers require
its 64 KiB retained-RAM handoff; QSPI and microSD targets require their own
matching bootloader profiles. Earlier preview versions mentioned below
describe compatibility/migration history, not the current recommended download.
Full Companion is a MOTA source and normally updates itself over USB.


Selected nRF52840 repeater builds use their dedicated external QSPI NOR flash as
a raw LoRa OTA staging device. This removes the internal-flash staging conflict:
the complete `.mota` stays off-chip, and the bootloader can use the entire
internal application region while it installs either a full image or an
in-place delta.

This is a matched application-and-bootloader feature. A board merely having an
nRF52840, free RAM, or pins named QSPI is not enough. Both halves must use the
exact flash wiring, and `ota self` must confirm the store and bootloader before
an update is downloaded.

## Supported repeater families

QSPI staging is enabled only for explicitly matched repeater-role environments
(and RS232 bridge variants that directly inherit one of those repeater targets)
on these currently matched families:

- Seeed XIAO nRF52840 and XIAO nRF52840 Sense modules, including the
  `Xiao_nrf52`, SolarXiao 30S/33S, and XIAO-module Ikoka handheld, Nano, and
  Stick repeaters
- original LilyGo T-Echo
- Elecrow ThinkNode M1 and M6
- Seeed Wio Tracker L1
- Seeed SenseCAP Solar Node P1
- RAK4631 with a RAK15001 in WisBlock sensor slot C, using the dedicated
  `RAK_4631_repeater_rak15001_slot_c_lora_ota` application and matching
  `wiscore_rak4631_board_rak15001_slot_c` OTAFIX bootloader
- RAK4631 with an externally wired Winbond W25Q16JV breakout on RAK19007,
  using `RAK_4631_repeater_w25q16_lora_ota`
- RAK3401 with its RAK13302 1 W radio and the same externally wired W25Q16JV
  breakout on RAK19007, using
  `RAK_3401_repeater_rak13302_w25q16_lora_ota`

Heltec T114 is intentionally not in this list. Its public V1, V2.0, and V2.1
schematics show U9 (MX25R1635F) as an optional QSPI footprint, so standard T114
application and bootloader targets do not assume that external NOR is populated.

The XIAO-module derivatives use the matching XIAO or XIAO Sense OTAFIX
bootloader shown by the module's `INFO_UF2.TXT`. Do not substitute a similar
bootloader for a board with different QSPI pins. For example, the T-Echo Card
and T-Echo Lite have different flash wiring and are not enabled by the original
T-Echo target.

Companion builds are intentionally excluded. Some companion targets use the
same external QSPI as a LittleFS message/data store, where raw OTA staging
would corrupt the filesystem; other companions simply do not assign that chip
to OTA. Room-server, sensor, and KISS roles are unchanged. Raw QSPI OTA is
scoped to the explicitly matched repeater-derived environments.

### RAK15001 placement and module conflicts

[RAK15001](https://docs.rakwireless.com/product-categories/wisblock/rak15001/datasheet/)
is a 2 MiB GD25Q16C **standard SPI** module, not a quad-I/O flash. The dedicated
RAK4631 target uses the nRF52840 QSPI peripheral in its single-data-line
FAST_READ/page-program modes at 8 MHz and accepts only the module's exact
`C8 40 15` JEDEC ID. `ota self` reports `QSPI store:2048K` only when the
expected module responds. An empty slot or a different SPI device reports
`QSPI store:ERR 0K`, and install is refused.

The module is electrically usable in sensor slot A-D because those slots share
the SPI signals and RAK15001 has onboard 10 kOhm pull-ups on WP# and HOLD#.
The supported MeshCore/OTAFIX combination nevertheless requires **slot C**. It
is the placement that remains safe when a
[RAK12501 GNSS](https://docs.rakwireless.com/product-categories/wisblock/rak12501/datasheet/)
is fitted in either of its supported slots, A or D: slot C avoids the GNSS
PPS/reset nets on IO1/IO2 and IO5/IO6. In particular, GPS in A plus flash in B
would share IO1/IO2 through the modules' auxiliary pins.

Only one device that uses the shared WisBlock SPI chip-select may be fitted.
Do not combine this target with RAK13800 Ethernet, RAK15002 SD, or another SPI
module. RAK13800 and RAK15001 cannot coexist because they use the same SPI
chip-select. Update a RAK13800 Ethernet build locally over USB using the
release's **Manual UF2** or **Serial DFU (.zip)** download; it cannot use this
RAK15001 LoRa-OTA staging target.

RAK3401 with RAK13302 remains incompatible with **RAK15001**. The 1 W radio
uses the same WisBlock SPI clock/data pins **and the same chip-select** as
RAK15001. Firmware cannot independently select or detect those two chips, so a
stock RAK3401 + RAK15001 assembly cannot provide reliable OTA staging without
a hardware chip-select rework. This restriction does not apply to the
separate-CS W25Q16 wiring below.

<a id="one-w25q16-wiring-for-rak4631-or-rak3401--rak13302"></a>

### One W25Q16 wiring for RAK4631 or RAK3401 + RAK13302

An external Winbond W25Q16JV breakout can remain attached to a RAK19007 while
the core is changed between RAK4631 and RAK3401 + RAK13302. It shares only the
WisBlock SPI clock/data nets and has its own chip-select on AIN1:

| W25Q16 breakout | RAK19007 connection | nRF52840 / Arduino pin |
| --- | --- | --- |
| `CLK` | IO connector pin 26, `SPI_CLK` | P0.03 / `3` |
| `DO` / MISO | IO connector pin 27, `SPI_MISO` | P0.29 / `29` |
| `DI` / MOSI | IO connector pin 28, `SPI_MOSI` | P0.30 / `30` |
| `CS` | J11 `AIN1` | P0.31 / `31` |
| `VCC` | J12 `VDD` | regulated 3.3 V |
| `GND` | J12 `GND` | ground |

Fit a physical approximately 10 kOhm pull-up from `CS` to J12 `VDD`. That
keeps the flash deselected during reset, bootloader entry, and a core-module
swap. Power the breakout only from J12 `VDD`; **never use J11 `VBAT`**, which
can exceed the flash's supply rating. The six-pin breakout does not expose
WP#/IO2 or HOLD#/IO3; do not add nRF IO2/IO3 wiring. A breakout with an onboard
power LED works, but that LED draws continuously and is undesirable for
low-power or solar operation.

This arrangement consumes no WisBlock sensor slot. However, `SPI_CLK`,
`SPI_MISO`, and `SPI_MOSI` are not available on the easy 2.54 mm J10/J11/J12
headers. Those three signals require a short underside-pad tap or a suitable
IO-connector interposer at pins 26-28. J11 and J12 alone are not sufficient.

GPS remains supported in sensor slot A. RAK12500 uses I2C, while RAK12501/L76K
uses UART plus its auxiliary control signals; neither uses the shared SPI
clock/data bus. On RAK4631 the built-in SX1262 has a separate private SPI bus.
On RAK3401 the RAK13302 and flash share clock/data, but have independent
chip-selects: RAK13302 NSS is P0.26 and flash CS is P0.31. The matched firmware
holds the radio NSS high while it temporarily hands the bus to the flash, then
restores the radio SPI interface.

The breakout target accepts only a 2 MiB W25Q16 with JEDEC ID `EF 40 15` and
runs it at 8 MHz in standard single-data-line SPI mode. A substituted W25Q32,
W25Q64, or W25Q128 fails closed instead of being treated as the OTA store.
Use the complete application/bootloader identity row that matches the fitted
core:

| Core and radio | PlatformIO environment | MOTA hardware identity | OTAFIX board / DFU device name |
| --- | --- | --- | --- |
| RAK4631 built-in SX1262 | `RAK_4631_repeater_w25q16_lora_ota` | `RAK4631_W25Q16` | `wiscore_rak4631_w25q16` / `4631_W25Q16_DFU` |
| RAK3401 + RAK13302 1 W | `RAK_3401_repeater_rak13302_w25q16_lora_ota` | `RAK3401_RAK13302_W25Q16` | `wiscore_rak3401_rak13302_w25q16` / `3401_W25Q16_DFU` |

The physical flash wiring is identical, but the two application and
bootloader pairs are not interchangeable.

## One-time prerequisite

Install a QSPI-capable OTAFIX 2.4.1 preview.9 or newer bootloader for the exact
board from the
[OTAFIX releases](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases)
before using LoRa OTA. The release notes must explicitly list that board's QSPI
mode. Also install the SoftDevice version expected by that target. The
application refuses the install handoff when the bootloader does not advertise
QSPI support.

For the external W25Q16 option, install the corresponding bootloader once
before installing its MeshCore application: `wiscore_rak4631_w25q16`
(`DEVICE_NAME=4631_W25Q16_DFU`) for RAK4631, or
`wiscore_rak3401_rak13302_w25q16` (`DEVICE_NAME=3401_W25Q16_DFU`) for RAK3401
plus RAK13302. Use only a release that explicitly names that exact pairing. Do
not substitute the RAK15001 bootloader or swap the two W25Q16 bootloaders
merely because the base-board wiring is the same.

For the first migration from the ordinary `wiscore_rak4631_board` bootloader to
`wiscore_rak4631_board_rak15001_slot_c`, use Nordic serial DFU or a compatible
BLE DFU client with the exact slot-C OTAFIX **combined bootloader + SoftDevice
DFU package** (or use SWD), then reinstall the slot-C MeshCore application. The
release filename has this form:

```text
wiscore_rak4631_board_rak15001_slot_c_bootloader-<OTAFIX-version>_s140_6.1.1.zip
```

Do not copy the slot-C bootloader-update UF2 onto the stock UF2 drive. The
stock loader is bound to `DEVICE_NAME=4631_DFU`, while the slot-C image is bound
to `4631_15001C_DFU`, so that UF2 is intentionally rejected. After the one-time
DFU/SWD migration, later canonical slot-C bootloader UF2 files work normally.
The combined OTAFIX package above is not the MeshCore application's Serial DFU
`.zip`; an application package does not migrate the bootloader.

After installing the repeater application, check:

```text
get bootloader.ver
ota self
ota status
ota qspi
```

A ready RAK15001 target reports all of the following:

```text
QSPI store:2048K
bootloader: QSPI apply OK
bl:QSPI
QSPI jedec=C84015 size=2048K sr1=00 stage=jedec
```

A ready external W25Q16 target instead identifies the Winbond part:

```text
QSPI store:2048K
bootloader: QSPI apply OK
bl:QSPI
QSPI jedec=EF4015 size=2048K sr1=00 stage=jedec
```

Other supported boards can report a capacity different from 2048K; the
RAK15001 and W25Q16 targets must report exactly 2048K and their respective
exact JEDEC IDs. `QSPI store:ERR 0K`, `NO QSPI`, or `bl:NO-QSPI` means the flash
wiring, flash power, chip-select pull-up, application, or bootloader does not
match. Do not start an install in that state.

`ota qspi` is a read-only diagnostic probe available on QSPI OTA builds. It
reports the exact JEDEC ID, status-register byte, last store stage, and the
first latched storage error. Run it after an immediate `storage error` before
starting another pull; later capacity probes preserve that failure detail.

## Capacity and package types

The store reads the JEDEC capacity at runtime and accepts supported 1 MiB
through 16 MiB devices using 24-bit addressing. QSPI capacity is not the final
firmware limit. The reconstructed application, including its 56-byte `EndF`
trailer, must fit its build's linked application region:

| SoftDevice layout | Application region | Maximum image |
| --- | --- | --- |
| S140 v7, boot-update-capable XIAO module | `0x27000..0xE0000` | `0xB9000` (757,760 bytes) |
| S140 v7, app base `0x27000` | `0x27000..0xED000` | `0xC6000` (811,008 bytes) |
| S140 v6, app base `0x26000` | `0x26000..0xED000` | `0xC7000` (815,104 bytes) |

The XIAO limit is deliberately 52 KiB smaller. Its linker and post-link record
cap every ordinary application at `0xE0000`; OTAFIX reserves
`0xE0000..0xEA000` as a 40 KiB self-update scratch bank, with the remaining gap
left unused before InternalFS. Full and delta application packages for these
targets derive their effective application end from that `0xE0000` record and
are rejected if their reconstructed image or detools geometry crosses it.

A full package needs only the new raw `firmware.hex` or non-merged application
image. An in-place delta still needs the exact image currently running. The
automation uses a conservative `0xC6000` detools workspace for external nRF52
staging so one package setting is safe for both layouts.

For online automation, QSPI is detected from `ota self` or `ota status`. For
offline preparation, identify it explicitly:

```bash
./tools/lora_ota/lora_ota.sh ./release.zip target-name \
  --prepare-only \
  --platform nrf52 \
  --nrf-qspi \
  --target-id 12345678 \
  --target-hw Xiao_nrf52
```

Use the real target ID and hardware identity from the destination. A ready
full `.mota` normally uses that target ID for discovery and routing. An
operator can deliberately override the routing target for a role change, so
`target_id` is not an apply-time safety assertion; the destination still
enforces the package's hardware identity before approval.

## Explicit XIAO bootloader updates over LoRa

This section documents the deployed XIAO raw-QSPI layout. Curated nRF52840
targets without external staging use a separate internal-flash layout with the
same explicit operator safety model; see
[nRF52 bootloader updates over LoRa](ota_nrf52_bootloader_update.md).

Bootloader delivery is available only when all of these are already true:

- the application is an `OTA_QSPI_BOOTLOADER_UPDATE` XIAO-module repeater build
  linked below `0xE0000`;
- `ota bootloader` shows a CRC-valid installed `XIAO_DFU` embedded identity for
  board ID `28860044` (base) or `28860045` (Sense);
- the installed OTAFIX marker reports ABI 3 or newer and both QSPI (`0x04`) and
  boot-update (`0x08`) capability bits;
- the package is the exact signed v3/40-KiB/1-KiB-block profile for that board,
  and its signer is already in `ota key`'s trusted allowlist.

This is not part of normal automation. Bootloader catalog rows are labelled
`bootloader`; even a capable node never autofetches or autoinstalls them, and
ordinary `ota install` rejects them in every application backend. The
`tools/lora_ota` deployment runner also refuses v3 packages. Fetch and arm one
only with the explicit flow:

```text
ota ls
ota pull <MID8> flash
# wait for ota status to report the same bootloader download as ready
ota bootloader
# copy the exact mid= and hash= values printed above
ota bootloader install <MID8> <HASH16>
```

The pull command is only transport intent; it does not authorize an install.
The final command must reproduce both the complete staged manifest ID and the
first eight bytes of its signed image hash. Before approval the application
again checks package/root/payload/image hashes, Ed25519 signature and trusted
allowlist, exact signed `XIAO_BL_...` ID, installed and incoming embedded
  manifest/CRC/name/board identity, vector table, ABI-3 QSPI+boot-update marker,
  and the adjacent CRC-covered `BLM2`/`SOFT` continuity extension at the exact
  final-image offset `0x9FB4`. The embedded
  boot version must equal the signed outer package version, the SoftDevice
  family/FWID/application base/layout ABI must match the running platform, and
  a successor to installed BLM2 must be strictly newer. Low-byte-zero and
  all-ones versions are invalid. Remote rollback is refused; use local DFU/SWD.

After the reply drains, GPREGRET `0x6B` and GPREGRET2 `0x51` enter the special
OTAFIX path. `APRV` carries the app's signature/allowlist and explicit operator
authorization decision. OTAFIX independently rechecks the strict structure,
identity/capabilities, vectors, payload SHA, embedded CRC, and copy hashes
before using the reserved scratch bank to replace its own `0xF4000..0xFE000` region. The running
application remains preserved; a rejected candidate returns to it unchanged.
Post-reboot `ota status` reports bootloader-update diagnostics as `blup:C0` to
`blup:CF` (`blup:C8` is success), separately from ordinary application `blrc`.

This feature cannot update a stock or old bootloader that lacks the capability
marker: perform the first exact-board combined bootloader+SoftDevice migration
over USB/BLE DFU or SWD. Do not attempt to bootstrap it with a v3 package.

## Storage ownership and recovery

The QSPI store is raw, not a file inside LittleFS. It owns the flash from
offset zero, erases 4 KiB sectors as blocks arrive, writes data before progress
metadata, and verifies every programmed page. A previously interrupted
download is reopened only when its header and trailer are valid; every claimed
block is re-hashed before it is trusted. Between a probe, transfer operation,
or checkpoint, firmware puts the NOR into deep power-down, deactivates the nRF
QSPI peripheral, and turns off a board-provided flash power-enable pin. The
next operation powers the chip, shifts the NOR's `0xAB` wake command over GPIO,
then activates the nRF QSPI peripheral and identifies the flash. Waking it
before peripheral activation is required because a flash in deep power-down
ignores the activation traffic itself. Merely running `ota self` therefore does
not leave QSPI drawing active-mode current or prevent the following operation
from reactivating it.

Installing a QSPI repeater build over a former companion build therefore
repurposes the external flash and destroys companion filesystem data as OTA
sectors are written. Back up anything important first. Returning to a companion
build may require formatting its external data store.

Before changing internal application flash, the application verifies package
integrity, hardware identity, signature policy, and bootloader capabilities.
The target ID selects discovery/fetch routing and can be deliberately
overridden; it is not a second hardware gate. The bootloader then verifies a
full payload before its first application erase. For a delta, the application
rejects invalid detools geometry before approval and the bootloader independently
repeats the base and geometry checks before applying it.
It clears the one-shot approval marker before invalidating the running image.
If power is lost after application writes begin, the bank remains invalid and
OTAFIX enters USB/BLE recovery rather than booting a partial image.

See [Easy firmware updates over LoRa](ota_easy.md) for the transfer commands and
[the OTA protocol](ota_protocol.md) for the container and handoff details.
