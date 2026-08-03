# MeshTower V2 microSD LoRa OTA

The `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors` target uses the MeshTower V2 onboard
microSD socket as persistent storage for its own LoRa OTA downloads. It accepts
both full `.mota` images and in-place delta `.mota` images. After verification,
the matching SD-aware OTAFIX bootloader reads the staged file from the card and
programs the nRF52840 application region.

The pin assignment follows the
[Heltec MeshTower V2 partial reference circuit](https://resource.heltec.cn/download/MeshTower-V2/schematic/MeshTower_V2_Partial_Reference_Circuit.pdf):

| Signal | nRF52840 pin | Arduino pin number |
|---|---:|---:|
| SD CS | P1.00 | 32 |
| SD MOSI | P1.01 | 33 |
| SD SCK | P0.06 | 6 |
| SD MISO | P0.26 | 26 |

The SD socket uses its own SPI peripheral, so card traffic does not change the
LoRa radio pinout.

## Card requirements

Use a FAT16, FAT32, or exFAT card with an MBR partition table whose first
partition starts after sector 1. This is the normal layout produced by most SD
formatters. GPT and unpartitioned "super-floppy" layouts are rejected.

MeshCore creates `/meshcore-ota.mota` as a contiguous file. Sector 1, which is
outside the partition, holds a checksummed bootloader handoff record. The
firmware validates this gap before writing it; an incompatible card layout
fails safely without modifying sector 1.

## Capacity and update types

The card removes the internal-flash staging limit. The `.mota` container may be
much larger than the old internal staging gap, and either a full image or an
in-place delta may be downloaded. The installed firmware itself must still fit
the nRF52840 application region below InternalFS (ending at `0xED000`); SD
storage does not increase the MCU's executable flash.

For this S140 v6 target, the maximum application image including its `EndF`
trailer is `0xC7000` bytes (815,104 bytes). To package a full self-update:

```bash
motatool build --fw ./Heltec_tower_v2_sdcard-new.hex --out-dir ./motas
motatool verify ./motas/*.mota
```

A delta uses the exact installed image as its base. The normal `0x98000`
workspace remains compatible. If either image is larger than that legacy
limit, build the patch with the SD target's larger workspace:

```bash
motatool build \
  --base ./Heltec_tower_v2_sdcard-running.hex \
  --fw ./Heltec_tower_v2_sdcard-new.hex \
  --patch-type in-place \
  --inplace-memory 0xC7000 \
  --out-dir ./motas
motatool verify ./motas/*.mota
```

The download is resumable because the partial `.mota` stays on the card.
Once it reaches `ready`, `ota install` performs the final verification,
publishes the bootloader handoff, and reboots. Keep the card inserted through
the reboot and installation.

The SD-aware bootloader is mandatory. `ota install` refuses to reboot if the
bootloader capability marker does not advertise SD staging and the selected
codec. Existing `Heltec_tower_v2_repeater` firmware continues to use the
internal-flash delta path and is unchanged.
