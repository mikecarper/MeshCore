# ESP32 legacy-partition migration over Wi-Fi

The partition-migration bridge lets a supported ESP32 node move from a small
dual-OTA layout to a verified larger dual-OTA layout without cable flashing.
It is designed for inaccessible repeaters: the bridge itself fits a legacy
1.25 MiB slot, replaces the table from Wi-Fi, and then exposes Wi-Fi again for
the full application image.

It currently has explicit, byte-for-byte generated target plans for Arduino
ESP32 `default_8MB.csv` and `default_16MB.csv`. It does not guess a table for
other flash sizes. Adding a board later means adding one reviewed target plan
and its generated table-prefix test; the bridge core is otherwise shared.

| Flash size | Target table | app0 / app1 size | Typical board |
| --- | --- | --- | --- |
| 8 MiB | `default_8MB.csv` | 0x330000 / 0x330000 | Seeed XIAO ESP32-S3 |
| 16 MiB | `default_16MB.csv` | 0x640000 / 0x640000 | Heltec V4 / V4.3 OLED |

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

## Eligible source layout

The bridge only accepts a source table that has all of the following:

- NVS at `0x9000`, size `0x5000`, and OTA metadata at `0xE000`, size `0x2000`.
- Two distinct, non-empty OTA application slots and a non-empty SPIFFS
  partition, all inside the physical flash size.
- A known exact 8 MiB or 16 MiB physical flash capacity and a source table
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

## Build and migrate a Seeed XIAO ESP32-S3

The production bridge is a legacy-slot application image:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e xiao_s3_partition_migrator
```

Build the Full XIAO image with its 8 MiB table:

```powershell
$env:MESHCORE_ESP32_FULL_BUILD = '1'
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e Xiao_S3_WIO_repeater_observer_mqtt
```

Upload the bridge through the legacy node's existing Wi-Fi browser updater.
Then join `MeshCore-Migrate` (password `meshcore-migrate`) and wait for
**Expanded layout ready**. Upload the Full app-only `.bin` from the second
build at `/update`. Do not upload an `-merged.bin`: merged images include
bootloader and table offsets for cable flashing, not browser OTA.

`xiao_s3_partition_legacy_seed` is a disposable-hardware test image only. It
models the legacy table and an identity file; it is not a deployable repeater.

## Heltec V4 / V4.3

Build `heltec_v4_partition_migrator`, upload it through the legacy Wi-Fi
updater, then upload the Full V4 app-only image after the bridge reports ready.
The 16 MiB target table creates two 6.25 MiB OTA slots. See the
[V4 build notes](heltec_v4_wifi_partition_migration.md) for its build command.
