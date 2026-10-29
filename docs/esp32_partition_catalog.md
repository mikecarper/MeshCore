<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/esp32_partition_catalog/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 release partition lookup table

The canonical LUT is `firmware/esp32_partition_catalog.json` in this fork.
`scripts/precompute_esp32_partitions.py` regenerates it from GitHub release
assets. MeshCore Open bundles an identical copy for offline phone updates.
There is no need to run a command on the phone or download old firmware there.

## Smallest partition by board

The table shows each board's **smallest known application partition** across
all cataloged releases, roles, and profiles. It does not count smaller NVS or
filesystem partitions. Sizes use MiB (1,048,576 bytes), with exact bytes below.
Mixed layouts means some releases have dual-slot OTA and others do not.
These are historical factory-image minimums, not measurements of your device;
use `get storage.layout` to check its actual installed layout.

Use `get boot.slot` to see the running application, next boot selection and
installed `app0`/`app1` capacities. Select an existing application with
`set boot.slot app0` or `set boot.slot app1`, then run `reboot` to start it.
The command validates the image and refuses a missing slot; it does not change
the partition table. See [application slot selection](cli_commands.md#select-the-next-application-slot-esp32)
for update conflicts and shared settings.

<div class="partition-catalog" id="partition-catalog">
  <div class="partition-catalog-controls">
    <label for="partition-catalog-search">Board</label>
    <input id="partition-catalog-search" type="search" placeholder="Search board name" autocomplete="off" disabled>
    <a id="partition-catalog-open" href="../_data/esp32_partition_catalog.json">Open JSON</a>
    <a href="../_data/esp32_partition_catalog.json" download="esp32_partition_catalog.json">Download JSON</a>
  </div>
  <p id="partition-catalog-status" role="status" aria-live="polite">Loading catalog JSON...</p>
  <p id="partition-catalog-count" role="status" aria-live="polite"></p>
  <div class="partition-catalog-table" id="partition-catalog-table" tabindex="0" aria-label="Smallest application partition by board" hidden>
    <table>
      <thead><tr><th scope="col">Board</th><th scope="col">Smallest app partition</th><th scope="col">OTA layouts</th></tr></thead>
      <tbody id="partition-catalog-boards"></tbody>
    </table>
  </div>
  <details class="partition-catalog-raw" id="partition-catalog-raw">
    <summary>Show JSON details</summary>
    <div class="partition-catalog-controls">
      <label for="partition-catalog-view">JSON view</label>
      <select id="partition-catalog-view" disabled>
        <option value="layouts">Snapshot and partition layouts</option>
        <option value="releases">Release inventory</option>
        <option value="all">Complete catalog (large)</option>
      </select>
    </div>
    <pre class="partition-catalog-json" tabindex="0" aria-label="Partition catalog JSON"><code id="partition-catalog-json">Expand this section to view the JSON.</code></pre>
  </details>
</div>

With JavaScript disabled, use [the complete JSON file](_data/esp32_partition_catalog.json).

## Coverage

The initial snapshot enumerates every published release (including prereleases,
excluding drafts) in these repositories, without a recent-release cutoff:

| Repository | Releases | Inspected ESP32 factory images |
| --- | ---: | ---: |
| meshcore-dev/MeshCore | 94 | 1,998 |
| IoTThinks/EasySkyMesh (PowerSaving) | 14 | 336 |
| mikecarper/MeshCore (including keymindCascade releases) | 69 | 7,808 |

All 10,142 standalone `-merged.bin` / `-cleanInstall.bin` images were parsed,
with nine distinct partition tables and no unresolved selected assets.
PowerSaving release tags can contain several firmware patch versions; the LUT
uses each asset's version, not merely its containing release tag.

Eleven releases have no standalone ESP32 factory image. These include nRF52
update chains, RP2040/nRF52-only releases, an empty release, checksum-only
metadata, and the ZIP-only Station G3 KISS modem package. They remain in the
release inventory with an explicit note, but supply no inferred ESP32 layout.
Unpublished builds, custom partition tables, and partition tables inside ZIP
archives are not covered. This is an inventory of published factory layouts,
not a claim that every Git revision has a known layout.

## Matching and safety

1. Ask the device for `get storage.layout` internally. Complete, valid OTA slot
   measurements take precedence over the LUT. A truncated reply is not proof
   that a slot is absent.
2. Otherwise match board, role, and complete firmware version. Prefer the exact
   build hash when reported and present. Preserve profile candidates; do not
   conflate PowerSaving, upstream, and fork version suffixes or V4/R8 boards.
3. Compare the chosen application length against both OTA slots (use the
   smaller slot so the next update also fits). If multiple profiles disagree,
   only make a fit/expansion recommendation when all candidates agree.
4. An oversized image needs a supported exact-board two-stage migration bundle.
   A single-app layout cannot accept the bridge via normal dual-slot OTA and
   needs cable installation. Unknown layouts remain explicitly unknown.

Version-based results are **estimates**. OTA usually replaces only the app, so
a newer version can run on an older partition table. Never use this LUT alone
to authorize a partition-table write. The migration bridge must validate the
actual flash geometry and identity recovery on the device before the final
application upload. See [two-stage migration](esp32_wifi_partition_migration.md).

The phone blocks known oversized regular uploads and opens its two-step
migration section. Companion updates use the same capacity guard, but the
Companion screen does not yet perform partition migration; it offers a smaller
exact-board image or cable migration when necessary.

## Schema 1

- `repositories`, `generatedAt`: snapshot provenance.
- `releases`: repository, tag, publication date, asset counts and coverage note.
- `builds`: release index, original asset name/ID/size/GitHub digest, board,
  role/profile, version, full build version and layout key.
- `layouts`: table SHA-256 mapped to partition entries, minimum OTA slot size
  and `dualOta`. Entry fields are `[type, subtype, offset, size, label, flags]`.
- `unresolved`: selected assets that could not be read or parsed, with reasons.

The generator reads at most the first 36 KiB of each image, including partition
tables at merged-file offset 0x8000 or 0x7000. It validates partition bounds,
overlaps, duplicate app subtypes and the table MD5 when supplied. GitHub asset
digests are recorded as provenance; a prefix read does **not** verify the full
asset SHA-256. This LUT is not a substitute for firmware download verification.

## Refresh and tests

Use an authenticated `gh` CLI and Python 3. The generator has bounded downloads,
retries, and a disposable prefix cache keyed by asset identity/update metadata.
No secrets or firmware binaries are committed.

```sh
python3 scripts/precompute_esp32_partitions.py \
  --output firmware/esp32_partition_catalog.json \
  --cache-dir /tmp/meshcore-partition-catalog-cache \
  --copy-to ../meshcore-open-android-5.1.1/assets/firmware/esp32_partition_catalog.json
python3 test/test_partition_catalog.py
```

`--cached-inventory` deliberately reuses the saved release listing for a
resumable, reproducible snapshot. Omit it to discover newly published assets.
Review coverage counts and `unresolved` after each refresh before shipping the
LUT. No scheduled refresh job or release upload is installed by this script.
