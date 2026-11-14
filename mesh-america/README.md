# Mesh America provider catalogs

The two provider catalogs select the 1.18.0.2 USA Cascade release family. Each covers 717 firmware profiles across 107 hardware cards. Some automatic-radio profiles appear on more than one appropriate card, for 724 total entries. Both catalogs use the same binaries; the logging URL remains available for existing installations.

```text
Provider name: Keymind Cascade
Catalog URL:   https://raw.githubusercontent.com/mikecarper/MeshCore/keymindCascade/mesh-america/keymind-cascade-v1.16.0-provider.json

Provider name: Keymind Cascade Logging
Catalog URL:   https://raw.githubusercontent.com/mikecarper/MeshCore/keymindCascade/mesh-america/keymind-cascade-logging-v1.16.0-provider.json
```

The `v1.16.0` portion of each filename is part of its stable provider URL, not the firmware version. Meshfirmware on Linux and Windows reads these same provider URLs.

See the [1.18.0.2 release guide](../docs/releases/1.18.0.2.md) and [firmware picker](../docs/firmware_picker.md). Choose the exact board, radio, storage and role. An ESP32 merged image includes the bootloader, partitions and application; an app-only image needs a compatible partition layout. For an existing device that needs a partition expansion, use its exact board and role package from the [utility release](https://github.com/mikecarper/MeshCore/releases/tag/utility-v1.18.0.2-halo-keymind-cascade-dev-a723af94) and follow its instructions.

Ordinary nRF52 application images preserve the installed bootloader. Supported Repeater and Room images marked with a bootloader carry their exact-board OTAFIX image. Selected images carry OTAFIX 2.4.13-preview.1; those bootloaders have not been physically tested on the listed boards. Recognized older bootloaders are checked for replacement after startup; unrecognized or recovery bootloaders require the documented local route. LoRa OTA needs a compatible exact-board bootloader. See the [bootloader preview details](../docs/nrf52_bootloader_previews.md) and [release guide](../docs/releases/1.18.0.2.md) for limitations.

The catalogs are generated from the staged release manifest with asset URLs checked against the release groups. The main firmware source is `a723af94ed7d8036329fb1f57637e6a009864752`; the additional nRF52 preview images use `9d961a199325ca9f7d059e8c103f0a80e4698ef1`. Historical `provider-backup.json` files remain unchanged.
