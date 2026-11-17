<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/_data/lora_ota_bridges/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Browser application bridge bank

The six GAT562 30S intermediate applications in this bank are withdrawn after
a reported boot failure involving the first bridge (`9DF841DF`). Automatic
selection, manual installation of these exact images, and saved-plan resumption
are blocked. The files remain here for investigation; they are not approved
update candidates. Keep recovery records. An unreachable device requires USB
recovery. A supported standalone release may still need USB if a direct
differential package does not fit.

These content-addressed JSON resources contain gzip/base64 encodings of
unchanged, compiled nRF52 application images. They are not bootloader images.
`index.json` pins the complete installed and selected image hashes, board and
role identity, and forward-ordered candidates. The browser checks every decoded
image, builds candidate deltas, and applies each patch locally before offering
an update. Runtime storage, installed image and bootloader capabilities are
checked again for every installation.

GAT562 30S Mesh Kit repeater candidates were compiled from these source commits:

- `27d9aaf7d`: 28 October source snapshot.
- `f3abf1d05`: 3 November source snapshot.
- `b73365831`: 11 November source snapshot.

Each uses the existing Full/Reduced release recipes, USA Cascade defaults, and
an explicitly compiled `v1.18.0.1-halo-keymind-cascade-bridge-COMMIT` version.
No version, code or trailer bytes are changed after compilation. Equal packed
versions are allowed only for explicitly prepared, exact-hash application
bridges. The selected final firmware must be newer than the starting firmware.
Each intermediate still runs a complete LoRa OTA receiver.

Reproduce a candidate from its source checkout:

```sh
OUTPUT_DIR=/path/to/bridge-output bash build.sh build-firmware \
  GAT562_30S_Mesh_Kit_repeater_lora_ota_no_external_sensors \
  --firmware-version v1.18.0.1-halo-keymind-cascade-bridge-COMMIT \
  --radio-preset usa-cascadia --profile cascade
```

Package compiled BIN files with `scripts/package_browser_ota_bridges.py`, passing
the exact original and final application images and forward-ordered
`--candidate COMMIT=BIN_PATH` arguments. The browser searches their graph; the
catalog is not permission to skip live identity, hash, storage or install checks.

The older-GAT retained-RAM compatibility pin comes from the public
`gat562_bootloader-v0.11.0-OTAFIX2.4.12_s140_6.1.1.zip` asset, SHA-256
`86490cbca323a8b81fc8490f9a43fd06db4c776bac6aa0bda91ee807cf801c60`.
Its 40 KiB bootloader has manifest CRC `03F81FDD`, board `239A0029`, target
`D50D2D44`, name `GAT562_DFU`, and one aligned `MOTARAMA` record at offset
37648 (ABI 1, authorization record 72 bytes, retained arena 65536 bytes).
The browser uses this fallback only after an explicit unsupported `ota ram`
reply and a fresh, exact installed-manifest/CRC match.

Validation covers complete reference-decoder reconstruction, flash/RAM layout
arithmetic, browser workers, simulated reboots and interruption recovery.
These newly compiled intermediate applications have not been exercised on a
physical GAT562 board. Keep that distinction in release/test reports.
