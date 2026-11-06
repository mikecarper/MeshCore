<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/heltec_v4_wifi_partition_migration/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Heltec V4 legacy-partition migration over Wi-Fi

For **[1.18.0.1 USA Cascade](releases/1.18.0.1.md)**, choose the exact V4
board and role in the [firmware picker](firmware_picker.md). When the installed
layout needs expansion, download the matching standalone Wi-Fi expansion image
and the normal Full app-only image for that board, role, and release.

Heltec V4/V4.3 OLED repeaters with their normal 16 MiB flash use the shared
[ESP32 partition migration](esp32_wifi_partition_migration.md) bridge.
It changes Arduino's old `default.csv` layout, with two 1.25 MiB OTA slots,
to `default_16MB.csv`, with two 6.25 MiB OTA slots.

Build the complete V4 package with the repeatable ESP32 recipe in WSL/Linux:

```bash
python3 -B scripts/build_esp32_partition_migration.py \
  --board heltec-v4 --version v1.18.0.1-halo-keymind-cascade-dev \
  --radio-preset usa-cascade-fixed --profile cascade
```

Upload the Wi-Fi expansion image through the legacy browser Wi-Fi updater.
Rejoin `MeshCore-Migrate` using the node's saved
admin password as the Wi-Fi password (see the shared
[credential and fallback rules](esp32_wifi_partition_migration.md#bridge-wi-fi-credentials));
when it reports **Expanded layout ready**, upload the normal Full app-only
`.bin` at `/update`. See the [quick steps](esp32_wifi_partition_migration.md#quick-steps-wi-fi-migration).

Do not use an `-merged.bin` in either browser uploader. Merged images contain
bootloader and table offsets for cable flashing. The bridge preserves the
private identity and supported saved configuration, including node name,
radio profiles, ACL and login replay state. Files outside the supported
preservation set may need to be recreated after the Full image starts.

For an existing `heltec_v4_repeater` that already supports compatible LoRa
mOTA, use the matching standalone LoRa expansion file and follow the
[LoRa migration steps](esp32_wifi_partition_migration.md#lora-migration).
The older identity-only handoff does not restore saved settings; its
post-expansion Wi-Fi recovery AP normally uses the published fallback. A
combined Partition Expander restores supported settings and retains the saved
admin password.
