# Heltec V4 legacy-partition migration over Wi-Fi

Heltec V4/V4.3 OLED repeaters with their normal 16 MiB flash use the shared
[ESP32 partition migration](esp32_wifi_partition_migration.md) bridge.
It changes Arduino's old `default.csv` layout, with two 1.25 MiB OTA slots,
to `default_16MB.csv`, with two 6.25 MiB OTA slots.

Build the complete V4 package with the repeatable ESP32 recipe in WSL/Linux:

```bash
python3 -B scripts/build_esp32_partition_migration.py \
  --board heltec-v4 --version v1.17.1.7-halo-keymind-cascade-dev \
  --radio-preset usa-cascadia --profile cascade
```

The ZIP contains the Wi-Fi bridge, LoRa bridge, and exact-target Full image.
Upload its `wifi-bridge.bin` through the
legacy browser Wi-Fi updater. Rejoin `MeshCore-Migrate` with password
`meshcore-migrate`; when it reports **Expanded layout ready**, upload the
ZIP's `full-application.bin` at `/update`.

Do not use an `-merged.bin` in either browser uploader. Merged images contain
bootloader and table offsets for cable flashing. The bridge preserves the
private identity; other SPIFFS-backed settings may be recreated after the
Full image starts.

For an existing `heltec_v4_repeater` that already supports LoRa mOTA, use the
separate LoRa migration package described in the shared guide. It preserves
the old repeater across the partition change so the full image can also be
installed over LoRa without reaching the board's Wi-Fi network.
