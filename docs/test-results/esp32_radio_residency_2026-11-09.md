<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/esp32_radio_residency_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 radio switching rollout — 9 November 2026

The T-Deck Full Companion's average switching time fell from **0.592 to 0.331 ms** for frequency-only changes and from **0.713 to 0.449 ms** when modem settings also changed, at 240 MHz. The normal optimized image received **240/240 test packets**.

The existing radio driver now places selected retune, RadioLib and Arduino SPI functions in internal instruction RAM on additional ESP32 boards. Commands, waits, locks, SPI clocks and power-saving policy are unchanged. Existing per-board feature and capacity limits are retained; no additional cuts were made for acceleration. This reduces flash-cache stalls; it does not make the entire call chain safe while the flash cache is disabled.

## T-Deck measurements

These are matched builds of the same current source with and without the placement flags, not a comparison against the older release previously installed on the test node. Both used the existing 8,888,888 Hz SPI default, explicit CPU speeds with power saving off, AGC at 16 seconds and old AGC in Auto. Each result combines two quiet 20-second windows.

| CPU | Change | Before | After | Reduction |
|---|---|---:|---:|---:|
| 240 MHz | Frequency only | 0.592 ms | 0.331 ms | 44% |
| 240 MHz | Frequency and modem | 0.713 ms | 0.449 ms | 37% |
| 80 MHz | Frequency only | 0.786 ms | 0.525 ms | 33% |
| 80 MHz | Frequency and modem | 0.962 ms | 0.696 ms | 28% |

The paired diagnostic builds used **7,856 additional internal RAM bytes** for the placement change. No contacts, queue frames, advert records, OTA workspace or features were removed. The selected function locations were checked in the linked images. Timing windows recorded no switch errors.

Timing used 906.525/907.525 MHz, BW62.5/SF7/CR5/preamble32 for matching profiles, and BW500/SF5/CR5/preamble200 for the mixed secondary, without RF probes during the timing windows.

The normal-image reception test covered both CPU speeds and both profiles, with 30 packets per profile in eight windows. Payloads alternated 40/160 bytes without automatic RF retries. All **240/240** arrived without corruption, duplicates or switch errors. The mixed fast secondary used a **512-symbol preamble**; this does not qualify shorter fast preambles. The cumulative maximum retune during RF traffic was **3.333 ms**, so the lower averages are not latency bounds.

The T-Deck was restored to the normal optimized image, with its original identity and settings verified. An initial final check caught an old-AGC default changing from Off to Auto after the firmware update; Off was explicitly restored and the complete settings check then passed. Every flash retained the partition table, wrote only the application and verified its readback. Diagnostic commands are absent from the final image. The witness radio's settings were restored too.

## Build coverage and limits

**46 of 48 checked recipe families enable an acceleration tier**, with **121 selected builds** passing their existing RAM, flash and capability checks. This includes separate automatic-radio and legacy chip-specific recipes. T-Deck has the physical timing and reception evidence above; the other newly opted-in recipes have build evidence only.

Role limits:

- `ebyte_eora_s3` — Repeater and Room: Full Repeater exceeds its existing flash slot with every tested acceleration tier; infrastructure placement remains unchanged.
- `heltec_wireless_paper` — Companion: All tested placement tiers failed the unchanged RAM/flash budgets; Companion keeps its original placement.
- `lilygo_t3s3` — Repeater and Room: The legacy SX1262 Full Repeater exceeded its flash slot at every placement tier. Infrastructure placement stays unchanged; automatic-radio T3S3 builds passed separately.
- `lilygo_tbeam_SX1262`: existing code placement retained; Resident code cannot fit existing Full RAM/flash budgets.
- `lilygo_tbeam_SX1262` — Companion: All tested placement tiers failed the unchanged RAM/flash budgets; Companion keeps its original placement.
- `lilygo_tbeam_SX1276`: existing code placement retained; Resident code cannot fit existing Full RAM/flash budgets.
- `lilygo_tbeam_SX1276` — Companion: All tested placement tiers failed the unchanged RAM/flash budgets; Companion keeps its original placement.
- `m5stack_cardputer_adv` — Full Companion: Even the SPI-only tier is 704 bytes below the unchanged runtime RAM budget.
- `meshadventurer` — Companion: All tested placement tiers failed the unchanged RAM/flash budgets; Companion keeps its original placement.
- `xiao_c3` — Companion: All tested placement tiers failed the unchanged RAM/flash budgets; Companion keeps its original placement.

Tier names describe which code is placed in internal RAM:

- **Full:** retune, RadioLib command and Arduino SPI code.
- **Commands:** RadioLib command and Arduino SPI code.
- **SPI:** Arduino SPI code only.
- **Core:** retune and RadioLib command code; the newer Arduino SDK's SPI code is unchanged.

Smaller tiers retain the same driver features, executed from flash where necessary. The build artifacts are validation images, not a published release; the report records each image, ELF and build-recipe hash. No runtime RAM reserve or capability requirement was lowered. The reported margins are build-budget margins, not a guarantee of maximum MQTT/TLS or network-load headroom. SX1276 and LR11xx boards can benefit from common transport placement, but their switching times were not measured here.

Full Station G2/G3, Vision Master E213/E290/T190, RC32, T-Beam 1W, T-Beam Supreme SX1262 and Wio Tracker L2 repeaters now require their onboard PSRAM for the large repeater tables, using the existing missing-PSRAM USB recovery policy already used by other supported boards. This removes the budget for an internal-memory fallback without shrinking the tables. Ordinary builds, Companion and Room retain their existing PSRAM policy. The added T-Beam and Wio policies match the manufacturers' [T-Beam 1W configuration](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/blob/master/boards/ESP32-S3-WROOM-1-N16R8.json), [Supreme configuration](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/blob/master/boards/t-beams3-supreme.json) and [Wio Tracker L2 memory specification](https://wiki.seeedstudio.com/meshtastic_wio_tracker_l2_intro/).

The XIAO C6 and T-LoRa C6 Repeater builders still select their existing reduced LoRa OTA profiles: their complete images exceed the 4 MB dual-OTA layout even with both placement flags removed (2,034,440 bytes versus a 2,031,616-byte slot). The accelerated builds do not introduce this fallback. These two passing results therefore cover the reduced profiles, not the full feature set; their omitted WebConfig and optional sensors are recorded in the JSON report.

Indicator remains unchanged. ESP-NOW-only targets and partition-migration utilities are outside this rollout. Existing V3/V4 settings are retained. The [V3 reception qualification remains open](heltec_v3_radio_command_speed_2026-11-09.md): its earlier 234/240 and focused 199/200 results are not superseded by the T-Deck result. The [V4 measurements](heltec_v4_radio_command_speed_2026-11-09.md) also retain their own reception limits. Other boards below are build-qualified only; no all-board RF claim is made.

<details markdown="1">
<summary>Per-recipe build coverage and RAM margins</summary>

| Recipe | Checked images | Placement | Smallest RAM margin |
|---|---:|---|---:|
| `ebyte_eora_s3` | 1 | full | 48,168 bytes |
| `generic-e22` | 1 | full | 17,124 bytes |
| `geprc_linkflow_900` | 1 | full | 8,812 bytes |
| `heltec_ct62` | 2 | spi | 160 bytes |
| `heltec_e213` | 3 | full | 5,952 bytes |
| `heltec_e290` | 3 | full | 5,776 bytes |
| `heltec_rc32` | 3 | core | 6,720 bytes |
| `heltec_t190` | 3 | full | 1,016 bytes |
| `heltec_tracker` | 3 | full | 1,366 bytes |
| `heltec_tracker_v2` | 3 | full | 2,566 bytes |
| `heltec_v2` | 3 | full | 264 bytes |
| `heltec_wireless_paper` | 2 | commands | 2,796 bytes |
| `lilygo_t3s3` | 1 | full | 48,168 bytes |
| `lilygo_t3s3_sx1276` | 3 | full | 4,888 bytes |
| `lilygo_t3s3_universal` | 3 | full | 3,056 bytes |
| `lilygo_tbeam_1w` | 3 | full | 12,680 bytes |
| `lilygo_tbeam_SX1262` | — | unchanged | — |
| `lilygo_tbeam_SX1276` | — | unchanged | — |
| `lilygo_tbeam_supreme_SX1262` | 3 | full | 12,136 bytes |
| `lilygo_tbeam_universal` | 3 | commands | 176 bytes |
| `lilygo_tdeck` | 2 | full | 45,464 bytes |
| `lilygo_teth_elite` | 3 | full | 4,064 bytes |
| `lilygo_tlora_v2_1` | 3 | full | 152 bytes |
| `m5stack_cardputer_adv` | 2 | full | 49,828 bytes |
| `meshadventurer` | 2 | full | 3,280 bytes |
| `meshnology_w12` | 3 | full | 10,008 bytes |
| `mke_s3` | 3 | spi | 976 bytes |
| `nibble_screen_connect` | 3 | full | 1,456 bytes |
| `nibble_zero_connect` | 3 | full | 1,448 bytes |
| `rak3112` | 3 | full | 11,584 bytes |
| `station_g2` | 3 | full | 7,232 bytes |
| `station_g3_esp32` | 3 | full | 6,872 bytes |
| `tenstar_c3` | 1 | commands | 3,452 bytes |
| `thinknode_m2` | 3 | full | 49,248 bytes |
| `thinknode_m5` | 3 | full | 45,688 bytes |
| `thinknode_m7` | 3 | full | 9,272 bytes |
| `thinknode_m9` | 3 | full | 32,368 bytes |
| `waveshare_esp32s3_lr1121` | 3 | full | 8,672 bytes |
| `wio-tracker-l2` | 3 | full | 4,168 bytes |
| `xiao_c3` | 2 | commands | 2,604 bytes |
| `xiao_s3` | 3 | full | 1,736 bytes |
| `xiao_s3_wio` | 3 | full | 3,496 bytes |
| `xiao_c6` | 6 | core | 41,552 bytes |
| `lilygo_tlora_c6` | 3 | core | 41,504 bytes |
| `m5stack_unit_c6l` | 3 | core | 41,424 bytes |
| `generic_e22_universal` | 1 | full | 16,996 bytes |
| `tenstar_c3_universal` | 1 | commands | 3,324 bytes |
| `meshadventurer_universal` | 3 | full | 32 bytes |

</details>

Validation also passed: 13 focused radio-driver host tests, 87 PSRAM/recovery/RAM tests, and an audit of 973 resolved PlatformIO environments. The environment audit confirms that existing options change only for code placement, that RISC-V boards do not receive Xtensa flags, and that newer SDK targets omit the pinned Arduino 2 SPI transform. Seven hosted-document link tests passed. The full build-profile suite passed.

Exact build selections, image and ELF hashes, RAM checks, function placement evidence and sanitized T-Deck measurements are in the [machine-readable report](esp32_radio_residency_2026-11-09.safe.json). Failed placement attempts are retained there; they are not counted as qualified final builds.
