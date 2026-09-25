# ESP32 legacy-partition migration over Wi-Fi or LoRa

The partition-migration bridge lets a supported ESP32 node move from a small
dual-OTA layout to a verified larger dual-OTA layout without cable flashing or
a full-chip erase. It fits a legacy 1.25 MiB slot. The Wi-Fi version exposes an
updater for the full image after migration. The LoRa version retains the old
OTA-capable repeater in the other expanded slot and returns to it after
restoring identity, so it can fetch the full image over LoRa.

It has explicit, byte-for-byte generated target plans for the 4 MiB MeshCore
dual-OTA Full table and Arduino ESP32 `default_8MB.csv` and
`default_16MB.csv`. It does not guess a table for other flash sizes. Adding a
flash size later means adding one reviewed target plan and its generated
table-prefix test; the bridge core is otherwise shared.

| Flash size | Target table | app0 / app1 size | Typical board |
| --- | --- | --- | --- |
| 4 MiB | `variants/dual_ota_full_4MB.csv` | 0x1F0000 / 0x1F0000 | ThinkNode M2/M5, LilyGo T3S3, Nibble, Heltec CT62 |
| 8 MiB | `default_8MB.csv` | 0x330000 / 0x330000 | Seeed XIAO ESP32-S3, Heltec V3 |
| 16 MiB | `default_16MB.csv` | 0x640000 / 0x640000 | Heltec V4 / V4.3, Station G2 |

## Repeatable ESP32 build recipe

From a clean commit, run the shell menu in WSL/Linux:

```bash
sh scripts/build_esp32_partition_migration.sh
```

Choose one exact board/role, or **all listed board/role pairs**. The menu
also offers all repeaters, all room servers, or all sensors separately. It asks
for the firmware version, radio preset, and profile, then builds Full application
images first, the available bridge(s) for each selected board, and verifies each
ZIP. Only one PlatformIO process runs at a time; `build.sh` can clean the
shared `.pio/build` tree between Full targets. The same menu can be run
non-interactively:

```bash
sh scripts/build_esp32_partition_migration.sh --board heltec-v4 \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
sh scripts/build_esp32_partition_migration.sh --all \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
sh scripts/build_esp32_partition_migration.sh --role sensor \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
```

`--all` creates one verified ZIP per listed board/role pair and a release
ZIP containing all of them, their hashes, and a manifest. This is a **local
release bundle**, not an upload to GitHub Releases. It is not an archive of
old firmware binaries. `--list-boards` shows the configured choices.
The release fails instead of publishing a partial bundle if one board package
is absent or fails verification.

The underlying Python recipe remains available:

```bash
python3 -B scripts/build_esp32_partition_migration.py \
  --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
```

The default builds all configured board/role recipes. Add `--board heltec-v4`
or `--board xiao-s3-wio` to build one; repeat `--board` for a selected set.
Use `--dry-run` to inspect the command order without building. The resulting
ZIPs appear under `.releases/esp32-expanded-<commit>/packages/`. The recipe
requires a clean checkout so each ZIP identifies the firmware commit it was
built from. It creates files only; it does not flash a device or erase flash.

The catalog covers canonical ESP32 repeater, room-server, and sensor roles on
4, 8, and 16 MiB chip-family plans, including historical 1.25 MiB candidates
now built with expanded tables. This includes ESP32-S3, original ESP32, and
ESP32-C3 boards with an evidenced old default layout. `--list-boards` is the
authoritative list of exact targets; specialty ESP-NOW/observer aliases are
separate identities and are not packaged under their old IDs. With Wi-Fi OTA
they can migrate to the
same physical board's canonical role image; their feature settings may need
to be recreated. The LoRa route still requires an exact old target ID. The
catalog omits boards with no evidenced legacy default-layout build, such as
boards introduced with `min_spiffs.csv` or another non-1.25 MiB layout. Some
listed boards use larger slots today but had historical default-layout builds.
A listed package is usable only when the installed old image has a working
Wi-Fi updater and the bridge verifies its live source table. Menu inclusion
does **not** override either check. The only LoRa
bridge packages currently available are the exact-target Heltec V4 and XIAO
S3 WIO repeater packages. On 4 MiB flash the expanded app1 overlaps old app1,
so the currently safe LoRa receiver-copy method cannot be offered there. A
4 MiB Full build offers 1,984 KiB slots, not 4 MiB per slot. A Wi-Fi-only ZIP
can omit `full-application.mota` when its application exceeds the current
2 KiB x 1024-block LoRa serving limit; `full-application.bin` remains the
verified Wi-Fi update image.

Other ESP32 boards need a reviewed source updater, chip/flash-size bridge,
verified Full image, and board entry before they can be added. The LoRa route
also needs an exact old OTA target ID and a non-overlapping receiver handoff.
Matching flash capacity alone is not enough to claim a package is safe.

## What the bridge preserves

Before it touches the partition table, the bridge reads the historical
`/identity/_main.id` SPIFFS file. It requires all 96 bytes (32-byte public key
followed by its 64-byte private key), stages them in an NVS namespace that is
unchanged by both target layouts, then changes the table. On the first boot
with the expanded layout it writes the staged value into SPIFFS, reads it back
byte-for-byte, and only then clears the NVS staging record and offers the final
uploader.

The private identity is the guaranteed preservation boundary. The old SPIFFS
image is deliberately not raw-copied into an expanded partition: SPIFFS is not
safe to resize that way. Other SPIFFS-backed settings can be recreated after
the full image boots.

Migration does erase the 4 KiB partition-table sector and the destination app
sectors. Expanded SPIFFS can be reformatted. It never issues a full-chip erase.

## Eligible source layout

The bridge only accepts a source table that has all of the following:

- NVS at `0x9000`, size `0x5000`, and OTA metadata at `0xE000`, size `0x2000`.
- Two distinct, non-empty OTA application slots and a non-empty SPIFFS
  partition, all inside the physical flash size.
- A known exact 4, 8, or 16 MiB physical flash capacity and a source table
  different from that capacity's target table.

This means the source app-slot and SPIFFS sizes can vary; the bridge validates
the live geometry instead of carrying board-name rules. It also ensures it is
running from one source OTA slot. If it arrived in B, it CRC-copies itself to
the future A address, selects A in OTA metadata, writes the table, and
restarts. It therefore works whether the browser OTA updater placed the bridge
in legacy A or B.

Keep power stable while the bridge is replacing the 4 KiB table sector. A loss
of power before that point leaves the old table and source intact. After that
small critical write, the selected A copy of the bridge is the recovery path.
The stock ESP32 bootloader reads one partition-table sector; rewriting that
sector is **not atomic**. A power loss during its erase/write window can leave
an unreadable table and require cable recovery. This recipe must not be called
power-loss-proof on a device whose USB/serial flash port is inaccessible.

## Migrate a Seeed XIAO ESP32-S3

Use `--board xiao-s3-wio` with the recipe above. Its Wi-Fi bridge is a
legacy-slot application image; its Full image keeps the old repeater's exact
LoRa OTA target ID. Upload `wifi-bridge.bin` from the ZIP through the legacy
node's existing Wi-Fi browser updater.
Then join `MeshCore-Migrate` (password `meshcore-migrate`) and wait for
**Expanded layout ready**. Upload `full-application.bin` at `/update`.
Do not upload an `-merged.bin`: merged images include
bootloader and table offsets for cable flashing, not browser OTA.

`xiao_s3_partition_legacy_seed` is a disposable-hardware test image only. It
models the legacy table and an identity file; it is not a deployable repeater.

## LoRa-only migration of a repeater

The exact old repeater must already support MeshCore LoRa mOTA and have a valid
EndF image identity. The LoRa bridge checks the old app's target ID and body
hash before any partition-table write; an unsupported or damaged receiver is
refused. It works from either legacy A or B. It copies legacy B to the future
B address, boots the bridge once to restore the private key in expanded
SPIFFS, then boots the preserved old repeater. That repeater can receive the
full image into its now-expanded inactive slot.

The board-specific LoRa bridges are `heltec_v4_partition_migrator_lora_repeater`
and `xiao_s3_partition_migrator_lora_repeater`. Build the final repeater with
`bash build.sh build-firmware <target> --full-exact` so its mOTA target ID
still matches the old repeater. `scripts/package_esp32_partition_migration.py`
checks the images, partition tables, hashes and mOTA containers and creates
separate Wi-Fi/LoRa migration ZIPs for those two boards. The ZIP README gives
the exact `ota ls`, `ota pull <id> flash`, `ota install` sequence.

A stock repeater without LoRa mOTA cannot use the LoRa-only route. The
Wi-Fi bridge remains the route for that device.

## Heltec V4 / V4.3

Build `heltec_v4_partition_migrator`, upload it through the legacy Wi-Fi
updater, then upload the Full V4 app-only image after the bridge reports ready.
The 16 MiB target table creates two 6.25 MiB OTA slots. See the
[V4 build notes](heltec_v4_wifi_partition_migration.md) for its build command.
