<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/gat562_browser_delta_paths_2026-11-14/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# GAT562 browser differential update paths

The browser now prepares and runs application bridge sequences through the
ordinary Prepare update / Update remote firmware actions. Deep search is an
optional slower comparison of every page-aligned workspace. Both fewer-install
and lower-data paths are ranked; the selected path survives an interrupted
transfer in browser recovery storage.

## Exact release pair

- Hardware: `GAT562_30S_Mesh_Kit`, target `180BBE1F`.
- Start: 1.18.0.1, Full LoRa OTA/no-external-sensors release at `03214a16`;
  601752 bytes, complete SHA-256
  `c308f6e097045692d3b9ab7f4f542b8a01ce6ed21954e905734ab1f84fe4706e`.
- Finish: 1.18.0.2, matching Full release at `a723af94`; 636644 bytes,
  complete SHA-256
  `9261ac3cdcef3b01167f95fabd3f5b1cb258df770bd698f629eaac7586511eab`.
- Layout: app base `26000`, staging ceiling `ED000`, flags `10` (hex).
  Application workspace plus flash-staged prefix has 815104 bytes available.
- Retained RAM must be confirmed: 65536 bytes, ABI 1, authorization record 72.
  For the original application without `ota ram`, an exact installed GAT562
  OTAFIX 2.4.12 manifest/CRC match provides the pinned compatibility proof.

## Measured preparation results

The direct package needs 331969 bytes. Its minimum 638976-byte workspace plus
the 270336-byte hybrid flash prefix exceeds the available flash. Comparing
complete intermediate applications produces a three-install route.

The bounded lower-data search selected these packages:

| Step | Compiled destination | Package bytes | Workspace bytes | Flash prefix bytes | Flash margin bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Full bridge, `27d9aaf7d` | 240061 | 626688 | 176128 | 12288 |
| 2 | Full bridge, `f3abf1d05` | 226260 | 638976 | 163840 | 12288 |
| 3 | Published 1.18.0.2 Full | 237178 | 643072 | 172032 | 0 |

Total: 703499 package bytes (687 KiB), three installs. The default faster
workspace search also selects three installs, totaling 753613 package bytes.
The reported radio-data estimate additionally includes modeled legacy framing,
requests and proofs. Retries, congestion and unrelated mesh traffic are excluded.
These are finite-candidate results, not a global minimum over possible builds.

## Validation and limits

- Every selected package reconstructs its complete destination byte for byte
  using both the browser decoder and the independent reference detools decoder.
  Manifest/body hashes and exact flash/RAM geometry pass for every step.
- Real Chromium workers and the ordinary update buttons pass two-step and
  three-step tests. Test targets apply the packages using the pinned OTAFIX
  WebAssembly model. A stopped second transfer resumes from the saved bridge
  without repeating the first install or selecting intermediate files.
- A malformed bridge download prevents all firmware writes. Wrong identity,
  changed package, unconfirmed RAM, insufficient storage and uncertain install
  states remain blocked.
- Existing native and Companion source/session regression tests cover transfer,
  temporary channels, reboot reconnection and recovery.
- Newly compiled GAT562 intermediate applications have not been run on physical
  GAT562 hardware. Reference decoding and simulated reboots do not prove hardware
  startup, radio operation or a completed RF update on that board.

The immutable candidates and packaging instructions are in
[`docs/_data/lora_ota_bridges`](../_data/lora_ota_bridges/README.md).
