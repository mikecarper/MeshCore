<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/regulator_recovery_2026-10-18/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Regulator selection and recovery qualification

Baseline firmware source: [`388134599`](https://github.com/mikecarper/MeshCore/commit/3881345992629fc6255259076c9751db7f7971ef). Later STM32 compatibility and fixture source: [`a2b2a8616`](https://github.com/mikecarper/MeshCore/commit/a2b2a8616fb117c8c877e59a1b8c4fef2fe64ad8). Post-campaign timing correction: [`5083c236b`](https://github.com/mikecarper/MeshCore/commit/5083c236b7f19390b52f9b59a44f44a735fcb685). Version base: `v1.18.0.1-halo-keymind-cascade-dev`; package filenames and build records identify their source suffix. PlatformIO commands ran one at a time. **Local software qualification is complete: eleven baseline target selections produced twelve packages at `388134599`, five STM32 targets passed at `a2b2a8616`, and a later RAK4631 Full build passed at `5083c236b`. The scoped RAK4631 recovery, shipping-image checks and final restoration also passed; hosted workflow status is recorded separately below.**

The [machine-readable evidence](regulator_recovery_2026-10-18.json) records the exact requested and packaged targets, application and container SHA-256 values, memory admission, profile reductions and source-bound linked checks. These are locally built qualification packages; this record does not publish a release or establish a successful GitHub workflow run.

## Behavior covered

The four main firmware roles save independent `mcu.regulator` and `radio.regulator` choices (`auto`, `dcdc`, `ldo`) where supported. Saving changes the next boot; it does not change an active radio. `auto` retains the board default. An explicit setter clears only that domain's fallback and crash streak, including when the same value is saved again. Unsupported hardware rejects the selection. Failed initialization reports an unknown active mode.

A domain can fall back to LDO after three consecutive known crash resets while its previous active mode was DC-DC, using a valid voltage-derived battery estimate of at least 50% and a sample at most 60 seconds old at the last heartbeat. Five healthy minutes clear the consecutive streak. The saved LDO fallback remains until an explicit setter clears it; there is no automatic return to DC-DC. Clean resets, cold starts, sleep wakes, unclassified resets, bad retained CRCs and ineligible battery samples cannot extend the streak. ESP32 brownouts are excluded.

Choices are prepared after primary filesystem/ADC startup and before display/radio/wireless initialization. Volatile fallback storage cannot acknowledge a durable setting. Crash bookkeeping uses retained RAM, and failed fallback persistence is retried after boot. Checked nRF52 APIs preserve SoftDevice ownership. KISS and other roles outside the saved-settings integration retain their original board default.

Supported CPU control covers approved nRF52 REG1 circuits. Radio controls cover supported SX1262, LR1110/LR1121 and verified Meshnology W12 LR2021 circuits. Current integrated STM32WL targets retain their existing shared MCU/radio supply configuration and expose neither selector. Their ordinary CLI reports an unsupported setting/command. The exact STM32+STM32WLx build guard removes unreachable persistence/recovery code, not a supported control. Other platforms or future STM32 external-radio drivers retain the generic implementation; regression checks require reconsidering the guard if current STM32 classes gain selection. STM32WL supply requests need a coordinated design because MCU and radio share the regulator ([ST datasheet](https://www.st.com/resource/en/datasheet/stm32wle4cc.pdf)); Wio-E5 already uses SMPS for high-power TX ([Seeed documentation](https://wiki.seeedstudio.com/LoRa_E5_mini/)).

CLI usage and policy details are in [CLI commands](../cli_commands.md#select-mcu-and-lora-regulator-modes) and [power diagnostics](../power_saving_diagnostics.md#user-selectable-regulators-and-crash-recovery). ESP32 WebConfig exposes authenticated save/reboot controls under **Radio → Advanced → Power regulators**. Firmware-picker metadata does not advertise these new regulator controls.

## Baseline packages at `388134599`

| Requested target | Packaged target and profile | Application bytes | RAM admission margin | Application SHA-256 prefix |
| --- | --- | ---: | ---: | --- |
| `RAK_4631_companion_radio_full` | `RAK_4631_companion_radio_full` (full) | 712,600 | 4,516 | `39babcaf8228` |
| `heltec_v4_3_companion_radio_full_femoff` | `heltec_v4_3_companion_radio_full_femoff` (full) | 2,559,064 | 34,432 | `af0e8cc40a2a` |
| `PicoW_companion_radio` | `PicoW_companion_radio` (auto) | 911,148 | 58,432 | `23fc6982cec8` |
| `muzi_base_duo_companion_radio_full` | `muzi_base_duo_companion_radio_full` (full) | 571,940 | 32,560 | `1d9773c16872` |
| `wio_wm1110_repeater` | `wio_wm1110_repeater_lora_ota_no_external_sensors-full-ota` (full sensors) | 521,908 | 87,256 | `588f8e6c2c70` |
| `wio_wm1110_repeater` | `wio_wm1110_repeater_lora_ota_no_external_sensors-reduced-ota` (reduced sensors) | 492,120 | 88,904 | `731bb26bcf44` |
| `meshnology_w12_sensor` | `meshnology_w12_sensor` (full) | 1,597,944 | 127,288 | `ba348f2791d4` |
| `LilyGo_Tlora_C6_room_server_` | `LilyGo_Tlora_C6_room_server_lora_ota_no_external_sensors` (reduced) | 1,584,776 | 228,736 | `d654c84d85ca` |
| `wio-e5-mini_repeater` | `wio-e5-mini_repeater` (auto) | 245,652 | 12,240 | `c7297a2fadd9` |
| `RAK_4631_kiss_modem` | `RAK_4631_kiss_modem` (auto) | 278,656 | 172,988 | `ae7537531dd8` |
| `Xiao_nrf52_companion_radio_full` | `Xiao_nrf52_companion_radio_full` (full) | 583,772 | 43,944 | `540f935740b3` |
| `Heltec_t114_companion_radio_full` | `Heltec_t114_companion_radio_full` (full) | 633,672 | 22,680 | `c6459844b69e` |

The C6 Room Server selector uses the build system's Auto policy. The baseline-source build selected `LilyGo_Tlora_C6_room_server_lora_ota_no_external_sensors`, **Reduced LoRa OTA** on Auto pass 2 after the expanded Full attempt exceeded flash/partition capacity. Only that exact packaged target/profile is qualified; this is not a C6 Full pass. Wio WM1110's sensor OTA policy produced both Full-sensor and Reduced-sensor packages, recorded separately. The matrix also covers all three nRF52 Full Companion CI smoke targets: RAK4631, XIAO and Heltec T114; their individual status remains visible above.

For each completed baseline package, the verifier recomputed every memory manifest's bound file hash, read the actual application BIN (directly or from the nRF52 DFU ZIP), and checked embedded version/source suffixes where present. KISS has no embedded version/source string; its source binding is the clean source-bound build record and artifact hashes. Each bound UF2 was decoded and matched against that exact BIN/DFU application, allowing only uniform final-block padding; each ESP32 merged image contained one exact copy of its application BIN. Container hashes and application hashes have separate meanings. RAM margins are modeled linked capacity and do not measure runtime allocation or physical high-water.

The baseline RAK4631 Full application is **712,600 bytes** of its 712,704-byte application region, leaving **104 bytes**. Its RAM admission model has 79,092 bytes available against 74,576 required, a **4,516-byte margin**. The package remains the Full profile and retains its verified BLE/USB, logging, mOTA, Meshtext and Reader capabilities.

The first matrix at `ef2edc52005f6bc64880911e9d08d7f782a0ddfb` passed seven targets, then failed Wio-E5 Mini Repeater with a 1,908-byte flash overflow. Commit `7e3359c1a` added the exact unsupported STM32WL path; its final rebuild result is recorded in the table rather than inferred from the change. No deployed filesystem boundary or working feature was removed to resolve that failure.

## STM32 compatibility packages at `a2b2a8616`

The earlier CI run at `388134599` exposed a flash overflow in the small RAK3x72 USB Companion and a missing regulator mock in an extracted fleet-command fixture. The compatibility change keeps the exact integrated STM32WL driver on its existing shared supply, omits eight unsupported regulator vtable entries and lets the ordinary CLI report an unknown selector. The RAK3x72 conversion preserves all eight 12-bit ADC samples and the exact prior calibration/truncation using integer arithmetic. Native tests compare all **32,761** valid sample sums; the maximum product is 283,374,000 and fits uint32_t. Contacts, channels, interfaces and deployed filesystem boundaries are retained.

| Target | Actual BIN bytes | Application region | Actual flash margin | RAM admission margin | Application SHA-256 prefix |
| --- | ---: | ---: | ---: | ---: | --- |
| `RAK_3x72_companion_radio_usb` | 229,284 | 229,376 | 92 | 6,848 | `613708364b7d` |
| `wio-e5-mini_companion_radio_usb` | 229,356 | 229,376 | 20 | 6,816 | `e1aab0f4e12f` |
| `wio-e5_companion_radio_usb` | 229,204 | 229,376 | 172 | 6,856 | `e797ca7e77b1` |
| `Tiny_Relay_companion_radio_usb` | 229,200 | 229,376 | 176 | 6,848 | `5882f6965730` |
| `wio-e5-mini_repeater` | 245,280 | 245,760 | 480 | 12,240 | `80f462312e8d` |

These are the four exact STM32 USB Companion CI targets/flag sets plus the Mini Repeater rebuild. The USB Companions retain 32 KiB LittleFS; the Mini Repeater retains its existing 16 KiB LittleFS. Each BIN includes **352 bytes of alignment/layout extent beyond PlatformIO's summed flash sections**; the table uses the actual packaged BIN size. In particular, Mini USB has **20 bytes** of application space remaining. Independent verification matches every initialized ELF flash-load byte and Intel HEX byte to its BIN, checks all memory-bound file hashes, verifies the embedded version/source suffix and excludes private regulator fault markers. These checks establish packaged size and consistency, not hardware behavior or wireless self-update. The cable-only Mini Repeater retry omitted `--require-ota` after that flag was rejected during preflight.

The earlier eleven targets were not all rebuilt at `a2b2a8616`. Their results keep source `388134599`. Read-only preprocessing of the actual MainBoard/Radio/mode headers and complete settings implementation shows identical tokens to the baseline in six ordinary configurations: generic, ESP32 with the STM32 radio name, nRF52, RP2040, future STM32 with an external SX1262, and STM32 with no specified driver. Eight-method override/runtime-dispatch controls preserve the generic virtual API; only exact STM32+CustomSTM32WLx loses those unsupported slots. The preprocessing proof uses native adapters and is a conditional-source/API comparison, not a new whole-firmware build for the other platforms. It compares `388134599` with `a2b2a8616`; it does not include the later intentional Morse scheduling change below.

The [source `a2b2a8616` GitHub workflow](https://github.com/mikecarper/MeshCore/actions/runs/39202695205) finished with **eleven jobs passing and the main test job failing**. The failure was a missing regulator lifecycle adapter in an extracted Morse role-loop fixture. Fixture-only commit `a08d7f07a` fixes that dependency and two downstream console/setup-GUI fixtures; 24 affected and related suites (121 tests) pass locally. The [new source `5083c236b` workflow](https://github.com/mikecarper/MeshCore/actions/runs/39205923096) is in progress; no successful hosted workflow is claimed for these corrections.

## Software, browser and linked checks

These are focused campaign checks. Method counts identify suite coverage; they are not a claim that the entire GitHub unit-test workflow completed in one run.

| Check | Result | Coverage |
| --- | --- | --- |
| Pure recovery policy | PASS, 16 scenarios | Three crashes; domain independence; >=50% boundary; stale/invalid samples; CRC/corruption; clean reset; healthy interval; sticky fallback. |
| Settings integration and actual API dispatch | PASS, 21 methods | Real settings implementation with ESP32/RP2040/nRF52 filesystems; deferred application; failed writes/rename/corruption; absent/volatile storage; capacity guards; exact STM32WL unsupported path and future external-radio guard controls. |
| STM32 Companion profile/ADC checks | PASS, 21 methods | Existing capacities, storage, interfaces and all 32,761 ADC sums retain the prior result. |
| Fleet and related extraction fixtures | PASS, 65 tests across nine suites | Real-codec fleet 44 with sanitizers; local/remote access 4; primary persistence 1; lost reply 1; save results 3; uncertain save 1; G3 dispatch 7; Mini logging 2; fleet role wiring 2. |
| Radio regulator selection | PASS, 6 methods | SX1262, LR1110, LR1121 and LR2021 boot choices, supported circuits, initialization failure, repeated LoRa/GFSK initialization. |
| nRF52 CPU/reset/watchdog integration | PASS, 5 methods | Checked SoftDevice API; actual versus requested mode; early reset capture; clean planned watchdog expiry and cancellation/rearm. |
| nRF52 linker retention reservation | PASS, 16 methods | All eight application linker scripts; 64-byte NOLOAD reservation; deliberate overflow rejection; unchanged clock and OTA authentication ranges. |
| nRF52 BLE DFU handoff | PASS, 11 methods | Clean callback only after successful final handoff checks; actual ARM/Unicorn path; aborted handoffs remain armed. |
| WebConfig regulator controls | PASS, 4 methods | One native JSON/authenticated-save/forced-reboot method and three Chromium methods; supported choices, clearing fallback and failed-save behavior. |
| Reader packing/lookup/UI | PASS, 15 methods | All 879 references and canonical ASCII bytes; terminal pages; screen layouts/fonts/navigation; bookmarks; backpressure and Morse guards. |
| Adjacent CLI/preferences/button/G3 suites | PASS, six suites | Settings contract, CLI error/capacity bounds, CommonCLI argument bounds, Companion preferences transactions, headless buttons and G3 dispatch |
| Complete build-profile selection suite | PASS | `test_build_profiles: OK` |
| Existing WebConfig coverage | PASS | 25 real Chromium methods, three lost-reply methods, 13 source methods, seven secrets methods and five generated-gzip methods |

| Baseline RAK Full ELF check (`388134599`) | Result |
| --- | --- |
| Linked RAK crypto | PASS |
| Linked RAK workspace | PASS |
| Linked RAK reader | PASS |

The crypto probe executes linked ARM RFC8032 keypair/signing vectors and bounds their function frames. The OTA workspace probe executes empty-queue acquisition/release and checks protected authentication ranges. The Reader probe executes the actual linked `replyVerse` instructions, checks canonical output/continuation commands, 157-byte reply guards and NUL termination, and measures callee frames plus a 1,536-byte caller reserve. These checks do not establish full-firmware timing, ISR/SoftDevice stack use or hardware behavior. Linked Reader negative controls also reject a valid DEFLATE stream with one altered word and a balanced enlarged function frame measuring 9,984 bytes. Only disposable ELF copies were mutated; they were never flashed or published.

## Post-campaign Morse scheduling correction at `5083c236b`

Review of the failed Morse fixture also exposed a foreground timing issue: the periodic median-of-three battery read includes two 5 ms gaps, and a failed fallback save can block on flash. The production service now defers those two periodic operations while keyed Morse TX/RX is active, using the existing Morse timing policy. Active-domain tracking and one-second recovery heartbeats continue. Deferred sample/retry deadlines stay unchanged, stale samples cannot qualify a crash, and overdue work runs on the next eligible service tick. Unkeyed CW does not defer these operations. Boot-time sampling and deliberate recovery rearming retain their fresh-measurement requirements.

The real settings/API implementation passes **23 methods with ASan/UBSan**, including TX/RX controls, no ADC/delay/flash retry during timing, changing active-domain masks, heartbeats, samples older than 60 seconds, resumed overdue work and saved LDO after a cold boot. Disabling the guard in a disposable source copy produces the expected ten failing platform subtests. The fixture-only correction at `a08d7f07a` separately passes **24 suites / 121 tests**; it does not change firmware source.

A clean source-bound RAK4631 Full rebuild at `5083c236b` passes with an actual application of **712,616 bytes**, leaving **88 bytes** in the existing 712,704-byte region. The modeled RAM margin remains **4,516 bytes**, and the Full capability set is unchanged. Bound file hashes, ZIP/BIN/UF2 and ELF flash-load bytes agree; no private regulator fault markers are present. Linked crypto, OTA workspace and all-reference Reader checks pass. The new linked Reader check reconstructs all 879 references / 95,689 text bytes through 1,091 calls and 379,674,263 ARM instructions. Its maximum callee frame remains 6,016 bytes; with the 1,536-byte caller allowance this is 7,552 of 8,192 bytes, within the same model as the baseline.

The earlier package matrix and completed physical campaign retain their own tested revisions. Other target packages were not all rebuilt at `5083c236b`, and the new periodic-task guard has not received physical Morse timing or fault qualification. The source change keeps the exact unsupported STM32WL branch unchanged.

## Reader flash and stack preservation

The Reader's pinned source, all **879 references** and **95,689 ASCII text bytes** (96,568 bytes including the 879 NUL terminators) are unchanged. Packing uses 4,096-byte blocks instead of 2,048-byte blocks. Compressed data plus index fell from 42,775 to 38,654 bytes, saving **4,121 flash bytes** with no removed text, feature or heap cache. Canonical source JSON SHA-256: `916bf9d1feae0ad410d69f61e6c5f328ac6acba10651d751b5bb29719732c49e`.

ARM GCC source-compiled frame bounds including decoder helpers were 6,264 bytes for terminal output and 6,400 bytes for the adaptive screen path. Adding the 1,536-byte outer-caller reserve gives 7,800 and 7,936 bytes, within the existing 8,192-byte loop stack. The linked `replyVerse` probe reconstructs all 879 references through 1,091 calls and 379,674,263 ARM instructions, checking all 95,689 text bytes. Its maximum callee stack is 6,016 bytes; adding the 1,536-byte caller allowance gives 7,552 of 8,192 bytes, leaving 640 bytes within that model. Neither compiler bounds nor emulated stack use substitutes for a physical high-water measurement.

## Mercerwood hardware qualification

The physical campaign used one RAK4631 connected to Mercerwood Mesh Pi, bound to source `388134599`. The private fault image was **Full Companion with Reader omitted**, retaining the Full USB/BLE transport configuration. Fault injection was identity-bound and explicitly armed once per fault boot. The separate shipping phase used the **unmodified Full Companion with every shipping feature**, with no fault instrumentation. Earlier legacy USB transport failures do not establish a recovery pass or show that the fault timer caused those failures.

Both images passed all five saved-mode combinations: `(ldo, ldo)`, `(dcdc, ldo)`, `(ldo, dcdc)`, `(dcdc, dcdc)` and `(auto, auto)`. The ten completed cases verified independent MCU/radio selection, unchanged active modes until reboot, the expected active modes after reboot, and zero crash counts/fallback after intentional reboots. On this board, `auto` selected DC-DC for both domains.

Three explicitly armed ARM UDF faults produced observed counter transitions **0 → 1 → 2 → 3** in both domains, with three USB-instance changes and lower post-reset uptime. Both domains stayed in DC-DC without fallback at counts 0–2; both selected sticky LDO at count 3 while the requested choices remained `auto`. The voltage readings at these observations were 4,253–4,284 mV, estimated 100%. USB was connected; physical battery presence was not independently verified. These observations qualify the voltage-estimate gate, not battery-only operation or the exact 50% boundary on hardware.

The healthy window advanced device uptime by **314 seconds** on the same boot. Neither a fourth reset nor an automatic return to DC-DC occurred. Both crash streaks cleared to zero after five healthy minutes while both sticky LDO fallbacks remained. Updating to unmodified shipping Full preserved both fallbacks.

Shipping Full also returned exact canonical ASCII for Reader references `1:1` (79 bytes, one page) and `21:17` (290 bytes, three pages), through four bounded queries with valid continuation commands. The USB instance stayed the same, uptime did not decrease and no new core errors appeared. This is a read-only USB check; it does not establish shipping Full/BLE fault or load qualification.

The first shipping manual attempt stopped at a serial ownership guard during reconnect. A subsequent read-only check proved both interfaces unowned. The unchanged guarded driver then completed all five cases from the start without another DFU. The stopped attempt remains separate evidence and is not counted among the ten completed cases; no owner was killed, service stopped or guard bypassed.

Cleanup removed only the owned trial regulator files and directly proved all three paths absent, matching the directly observed pretrial state. Original identity/preferences file fingerprints remained unchanged. The earliest pre-session absence of the new REG1 path was inferred from source/session provenance rather than captured directly: a prior diagnostic's exact 12-byte record was privately exported and verified before guarded cleanup. The current campaign directly observed absence before its mode tests and again after cleanup.

Finally, the exact original application package was restored. The original full version, complete device information/PIN, identity, contacts, all 40 meaningful channel entries and all eight checked power/USB preferences matched the saved baseline. Channel comparison preserves names and complete keys, excluding only unused bytes after the name's first NUL; it does not claim every raw reply-padding byte is identical. No bootloader update, SWD, hub power operation or test mesh message was used. Private diagnostic images were kept out of releases and moved out of the normal build-discovery directory after testing.

| Physical check | Result |
| --- | --- |
| Private Full saved choices and normal reboots | PASS, five combinations |
| Private Full three qualifying real faults, then LDO | PASS, both domains |
| Healthy interval leaves sticky LDO | PASS, 314-second uptime advance; streaks clear |
| Unmodified shipping Full manual/read-only Reader phase | PASS, five combinations and four Reader queries |
| Cleanup restores original trial-file state | PASS, direct absence proof |
| Original firmware and stored state restored | PASS, independently compared against baseline |

The machine-readable record includes the exact private diagnostic, shipping and restored-original package hashes. Independent evidence review confirmed the completed mode matrices, fault sequence, healthy window, Reader output, cleanup and restoration. Private snapshots, keys, channel payloads and device serials are excluded.

## Physical acceptance still required

- Battery-only cold starts and absent-battery/sub-50% ADC characterization on each supported board.
- Matched current measurements in both modes and supply voltage during radio TX.
- Sleep/wake and BLE/WiFi operation under radio load on the applicable hardware families.
- Three-fault recovery on other supported boards and reset-retention implementations.
- Shipping Full/BLE fault/load qualification beyond the private Full diagnostic's scope.
- Physical Morse timing under load with the later periodic-task deferral.

Battery percentage is a voltage estimate, not independent proof of a physically connected battery. Sample freshness is relative to the last retained heartbeat, not elapsed wall time after a hard hang. Runtime load, current and supply measurements remain separate acceptance evidence.
