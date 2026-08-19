# Mesh America provider catalogs

Generated provider catalogs for the Keymind Cascade MeshCore release assets.
The provider URLs stay stable when a new firmware release is published.

Use these catalog URLs after this directory is committed and pushed to
`keymindCascade`:

```text
Provider name: Keymind Cascade
Catalog URL:   https://raw.githubusercontent.com/mikecarper/MeshCore/keymindCascade/mesh-america/keymind-cascade-v1.16.0-provider.json
```

```text
Provider name: Keymind Cascade Logging
Catalog URL:   https://raw.githubusercontent.com/mikecarper/MeshCore/keymindCascade/mesh-america/keymind-cascade-logging-v1.16.0-provider.json
```

The standard catalog currently keeps unaffected infrastructure on v1.17.1.1
and tracks the corrective v1.17.1.2 release for every Companion role. Update
only those Companion choices from the completed corrective matrix with:

```text
python3 mesh-america/update-provider-release.py \
  --release-dir /path/to/release \
  --artifact-version v1.17.1.2-c9652754 \
  --main-tag v1.17.1.2-halo-keymind-cascade-c9652754 \
  --advanced-tag lora-ota-v1.17.1.2-halo-keymind-cascade-c9652754 \
  --utility-tag kiss-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --companion-only
```

The updater preserves the catalog's curated device names, role choices, and
hardware guidance; validates every referenced artifact; routes URLs to the
correct GitHub release page; and replaces legacy portable MQTT choices with the
matching expanded-partition FULL MQTT observer builds.

The logging catalog likewise keeps unaffected roles on v1.17.1.1 while its
Companion, Full Companion, and expanded Companion logging profiles track the
v1.17.1.2 correction. Logging builds are for a USB-connected MQTT/logging host;
they are not the direct on-device Wi-Fi MQTT bridge. Update only the Companion
choices from the same completed matrix with:

```text
python3 mesh-america/update-logging-provider-release.py \
  --release-dir /path/to/release \
  --artifact-version v1.17.1.2-c9652754 \
  --logging-main-tag logging-v1.17.1.2-halo-keymind-cascade-c9652754 \
  --logging-utility-tag logging-utility-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --full-tag full-profiles-v1.17.1.2-halo-keymind-cascade-c9652754 \
  --main-tag v1.17.1.2-halo-keymind-cascade-c9652754 \
  --advanced-tag lora-ota-v1.17.1.2-halo-keymind-cascade-c9652754 \
  --utility-tag kiss-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --companion-only
```

In Companion-only mode, each updater preserves every unaffected catalog entry,
validates every selected replacement artifact, and adds the one-time
power-saving migration guidance. Without `--companion-only`, the existing
all-role behavior remains available for a future complete matrix release; the
logging updater then validates all 648 referenced firmware identities.

The older PowerShell generator remains available for the historical standard
and logging source folders:

```powershell
powershell -ExecutionPolicy Bypass -File mesh-america\generate-mesh-america-catalogs.ps1
```
