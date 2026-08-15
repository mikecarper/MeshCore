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

The standard catalog currently tracks the v1.17.1.1 Cascade/USA release. Update
its curated device and role choices from a completed all-target release with:

```text
python3 mesh-america/update-provider-release.py \
  --release-dir /path/to/release \
  --artifact-version v1.17.1.1-759a35fc \
  --main-tag v1.17.1.1-halo-keymind-cascade-759a35fc \
  --advanced-tag lora-ota-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --utility-tag kiss-v1.17.1.1-halo-keymind-cascade-759a35fc
```

The updater preserves the catalog's curated device names, role choices, and
hardware guidance; validates every referenced artifact; routes URLs to the
correct GitHub release page; and replaces legacy FULL MQTT choices with the
matching portable MQTT observer builds.

The logging catalog tracks the diagnostic and expanded-partition profiles in
the supplemental v1.17.1.1 release pages. Update it from the same completed
matrix with:

```text
python3 mesh-america/update-logging-provider-release.py \
  --release-dir /path/to/release \
  --artifact-version v1.17.1.1-759a35fc \
  --logging-main-tag logging-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --logging-utility-tag logging-utility-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --full-tag full-profiles-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --main-tag v1.17.1.1-halo-keymind-cascade-759a35fc \
  --advanced-tag lora-ota-v1.17.1.1-halo-keymind-cascade-759a35fc \
  --utility-tag kiss-v1.17.1.1-halo-keymind-cascade-759a35fc
```

This updater preserves the logging catalog's curated role and hardware
guidance, validates all 648 referenced firmware identities, and selects the
release page that owns each file.

The older PowerShell generator remains available for the historical standard
and logging source folders:

```powershell
powershell -ExecutionPolicy Bypass -File mesh-america\generate-mesh-america-catalogs.ps1
```
