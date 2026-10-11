<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/sensor_dog_tracker/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Sensor dog tracker validation

The opt-in [Sensor dog tracker](../sensor_tracker.md) was checked on
2026-10-10 using executable host tests and firmware builds. This report
qualifies the code paths named below; no radio was flashed for these checks,
and battery current, GPS acquisition outdoors, and live RF delivery were not
measured.

## Executed regressions

| Suite | Checks |
| --- | --- |
| `test_tracker_route_policy.py` | 99 boundary and byte-format assertions: six-hour gates, repeater postponement, sticky four-day cutoff, invalid clocks, overflow and restart. |
| `test_sensor_tracker_store.py` | 6,063 storage and failure scenarios over five filesystem API modes, including torn saves, unavailable storage, durable flood reservations and a monotonic request-tag floor. |
| `test_sensor_tracker_client.py` | 22 tests execute actual `Tracker.cpp`, peer dispatch, route/store/codec and packet hash implementation: setup, Safe/Lost/Unknown cadence, fresh GPS, transmit-based response deadline, exact owner identity, nonce reuse across reboot, path updates and packet ownership. |
| `test_tracker_owner_response.py` | Actual Companion responder across four feature configurations: persistent contact authentication, flags and padding, time-bounded position updates, rate limits, and preserved ordinary message state. |
| `test_sensor_tracker_gps.py` | 5 tests: fresh parser/PVT samples, GPS ownership, 120-second bound, validated UTC recovery after a cold start, stale sync notifications and saved preference restoration. |
| `test_sensor_tracker_sleep.py` | 4 tests: actual sleep gates, nRF52 timer allocation/queue failures, warm radio pause and resume, Dispatcher watchdog anchors, and feature defaults. |
| `test_sensor_subscriptions.py` | 18 tests: existing delivery behavior plus suppression in dog mode and restoration after leaving it. |
| `test_radio_interrupt_recovery.py` | 9 tests: radio recovery preserves the no-flood-retry policy when a missing transmit-complete interrupt could conceal a transmission. |

Host C++ tests use AddressSanitizer and UndefinedBehaviorSanitizer where
available. Negative controls intentionally remove guards and verify that
assertions detect the resulting regressions. Transport, UART, receiver and
filesystem hardware are replaced by bounded adapters; they do not qualify
physical RF range or current draw.

The ordinary PlatformIO suites `native`, `native_kiss_modem` and
`native_ota_channel` also passed: 1,829 test cases, with no failures.

The client checks also cover:

- A backward RTC change while already asleep remains latched after the clock
  is restored, until a matching authenticated owner response.
- A forward UTC correction cannot manufacture six hours of RF silence.
- A flood reply crossing the exact four-day boundary retains the cutoff;
  a subsequent matching direct response clears it.
- A failed warm radio wake attempts peripheral recovery and restores the
  dispatcher's watchdog ownership even when recovery fails.
- An unset RTC defers the request without being reported as a storage failure.
- The GPS manager completes its bounded position/time acquisition before the
  client consumes the result.

## Firmware builds

All six firmware builds passed their flash and runtime memory checks. Values
below are linked firmware bytes, rather than measured running heap usage.

| Environment | Flash used / limit | Static RAM used | Runtime internal RAM available / required |
| --- | --- | --- | --- |
| `t1000e_sensor` | 377,520 / 811,008 | 64,400 | 171,112 / 54,320 |
| `RAK_WisMesh_Tag_sensor` | 421,084 / 815,104 | 66,336 | 169,176 / 54,320 |
| `heltec_v4_sensor` | 1,490,461 / 6,553,600 | 97,024 | 277,760 / 95,296 |
| `t1000e_companion_radio_usb` | 427,964 / 708,608 | 187,272 | 48,240 / 38,944 |
| `RAK_3x72_companion_radio_usb` | 229,000 / 229,376 | 39,780 | 24,728 / 17,408 |
| `wio-e5-mini_companion_radio_usb` | 229,012 / 229,376 | 39,804 | 24,704 / 17,408 |

The compact STM32 Companion retains its existing feature exclusions and
application/storage boundary. Its limited remaining flash is checked by the
existing CI build. The workflow now also builds T1000-E and RAK Tag Sensors
and the ESP32-S3 V4 Sensor, in addition to the executable tracker regressions.
The Wio-E5 Mini check uses the complete
`v1.17.1.9-halo-keymind-cascade-dev` version string and USA Cascade profile.

## Limits

Bluetooth Find My/OpenHaystack advertising and automatic Room mailbox polling
are not implemented by this change. RP2040's interrupt-driven physical wake
has not been tested on a board. The GPS/RF and current measurements still
needed on T1000-E and RAK Tag are listed in the
[tracker guide](../sensor_tracker.md#remaining-work).
