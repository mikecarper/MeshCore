<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Test Results

Completed validation reports, experiments, and test guides are collected here. Older page addresses redirect to these copies. Results are tied to the hardware and firmware versions named in each report; they do not automatically qualify a newer build.

- [1.17.1.8 Preview 1 qualification and raw reports](preview-1.17.1.8/index.md)

## Radio and RF

- [SX1262 channel-scanning hardware results](cad_scan_validation.md)
- [CW and CW2 validation](carrier_wave_validation.md)
- [Double center-frequency write: V4 transmitter / XIAO receiver](double_frequency_v4_validation.md)
- [Four fixed transmitters: longer dwell repeats](four_fixed_tx_dwell_validation.md)
- [Four fixed transmitters / fast single-pass receiver](four_fixed_tx_single_pass_validation.md)
- [Mixed-bandwidth two-channel reception test](mixed_scan_validation.md)
- [Equal-symbol-time mixed SF/BW, four-channel test](mixed_sf_bw_validation.md)
- [SF7/62.5 + SF8/500: 4.6 slow chirps, 0.3 ms reserve](pair_4p6_300us_validation.md)
- [SF7/62.5 + SF8/500, fast switching and preamble 32](pair_preamble32_validation.md)
- [Off-channel preamble investigation](preamble_detection_investigation.md)
- [Shared radio/radio2 chirp timing and preamble warnings](radio_chirp_math_validation.md)
- [Automatic dwell policy: 4.6 chirps on both profiles](radio_dwell_policy_validation.md)
- [SX1262 profile-switch validation](radio_profile_switch_validation.md)
- [Dual-profile receive validation](radio_profiles_validation.md)
- [Radio receive calibration and recovery](radio_receive_calibration.md)
- [Separated radios, normal XIAO RX gain, and unchanged-modulation timing](separated_radio_modulation_cache_validation.md)
- [SF10/125 full samples: 10 Hz versus 100 Hz first-pass detour](sf10_10_vs_100hz_full_sample_validation.md)
- [SF10/125: tiny first-pass frequency offset and coding-rate detour](sf10_10hz_cr_detour_validation.md)
- [SF10/125: apply the full radio settings twice per hop](sf10_full_retune_twice_validation.md)
- [SF10 / 125 kHz, XIAO nRF52 TX, double write, 1 ms delay grid](sf10_nrf52_double_write_settling_validation.md)
- [SF10/125 rollback and wider-spacing failure isolation](sf10_rollback_failure_validation.md)
- [Descending SF10..SF5 / 125 kHz settling limits](sf125_settling_limits_validation.md)
- [SF5 / 250 kHz: four-channel dwell sweep](sf5_250_dwell_validation.md)
- [SF6/125 four-channel post-switch settling sweep](sf6_125_settling_validation.md)
- [SF6 / 125 kHz: four channels at 5.1 chirps](sf6_5p1_trace_validation.md)
- [SF6 / 125 kHz channel-count test](sf6_channel_scan_validation.md)
- [SF8 / 125 kHz: four-channel comparison at 5.1 chirps](sf8_5p1_trace_validation.md)

## Companion, USB, BLE, and display

- [Companion lost-status replies](companion_lost_reply.md)
- [Sensor dog tracker checks](sensor_dog_tracker.md)
- [Contact-cache and NimBLE RAM qualification](companion_contact_cache_results.md)
- [USB Companion ASCII default validation](companion_usb_ascii_validation.md)
- [USB Companion client compatibility](companion_usb_client_validation.md)
- [Full USB logging repair - 1.17.1.6](full_usb_logging_validation.md)
- [Home-screen text spacing](home_text_spacing.md)
- [ESP32-S3 NimBLE trial results, 2026-09-08](nimble_companion_trial_results.md)
- [ESP32-S3 NimBLE Full Companion trial](nimble_companion_trial.md)
- [nRF52 USB READY hang](nrf52-usb-ready-fix.md)
- [nRF52 Bluetooth DFU application handoff](nrf52-bluetooth-dfu-handoff.md)
- [Native USB backpressure and radio liveness](usb_serial_backpressure.md)
- [Small-screen message fonts](v4_pixel5_font_trial.md)

## Build, storage, memory, and checklists

- [Personal room mailbox checks](room_mailboxes.md)
- [Stock nRF52 bootloader version audit](bootloader_version_stock_audit.md)
- [CLI setting dispatch audit](cli_settings_audit.md)
- [Hardware validation checklist](hardware_validation_checklist.md)
- [XIAO Bluetooth stealth hardware validation - 2026-09-07](hardware_validation_bluetooth_stealth_2026-09-07.md)
- [Shrinking the per-connection TLS footprint on non-PSRAM observers](mbedtls-tls-footprint.md)
- [nRF52 Companion automatic ExtraFS recovery](nrf52_companion_storage_recovery.md)
- [PR #7 review and validation](pr7_review_validation.md)
- [ESP32-S3 OTA memory experiment](s3_memory_soak_validation.md)
- [SPIFFS regular-file reads and login replay state](spiffs_regular_file_reads.md)
