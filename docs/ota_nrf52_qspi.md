# nRF52 repeater LoRa OTA with external QSPI

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

QSPI staging is enabled only for repeater-role environments on these currently
matched families:

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
to OTA. Room-server, sensor, KISS, and repeater-bridge roles are also unchanged.
Raw QSPI OTA is scoped to the explicitly matched repeater environments.

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

RAK3401 is intentionally unsupported. Its external RAK13302 1 W radio already
uses the same WisBlock SPI clock/data pins **and the same chip-select** as
RAK15001. Firmware cannot independently select or detect the two chips, so a
stock RAK3401 + RAK15001 assembly cannot provide reliable OTA staging without
a hardware chip-select rework.

## One-time prerequisite

Install a QSPI-capable OTAFIX 2.4.1 preview.8 or newer bootloader for the exact
board from the
[OTAFIX releases](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases)
before using LoRa OTA. The release notes must explicitly list that board's QSPI
mode. Also install the SoftDevice version expected by that target. The
application refuses the install handoff when the bootloader does not advertise
QSPI support.

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

Other supported boards can report a capacity different from 2048K; the
RAK15001 target must report exactly 2048K. `QSPI store:ERR 0K`, `NO QSPI`, or
`bl:NO-QSPI` means the flash wiring, flash power, or bootloader does not match.
Do not start an install in that state.

`ota qspi` is a read-only diagnostic probe available on QSPI OTA builds. It
reports the exact JEDEC ID, status-register byte, last store stage, and the
first latched storage error. Run it after an immediate `storage error` before
starting another pull; later capacity probes preserve that failure detail.

## Capacity and package types

The store reads the JEDEC capacity at runtime and accepts supported 1 MiB
through 16 MiB devices using 24-bit addressing. QSPI capacity is not the final
firmware limit. The reconstructed application, including its 56-byte `EndF`
trailer, must fit below InternalFS at `0xED000`:

| SoftDevice layout | Application region | Maximum image |
| --- | --- | --- |
| S140 v7, app base `0x27000` | `0x27000..0xED000` | `0xC6000` (811,008 bytes) |
| S140 v6, app base `0x26000` | `0x26000..0xED000` | `0xC7000` (815,104 bytes) |

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

## Storage ownership and recovery

The QSPI store is raw, not a file inside LittleFS. It owns the flash from
offset zero, erases 4 KiB sectors as blocks arrive, writes data before progress
metadata, and verifies every programmed page. A previously interrupted
download is reopened only when its header and trailer are valid; every claimed
block is re-hashed before it is trusted. Between a probe, transfer operation,
or checkpoint, firmware puts the NOR into deep power-down, deactivates the nRF
QSPI peripheral, and turns off a board-provided flash power-enable pin. The
next operation powers and identifies the chip again, so merely running
`ota self` does not leave QSPI drawing active-mode current.

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
