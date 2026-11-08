<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/companion_android_login/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Companion login and Android validation

Validation: 7 November 2026 (8 November UTC).

## Hardware result

- LG Android 5.1.1 phone running MeshCore Open 9.5.5, connected over Bluetooth
  to a Heltec V4.2 Full Companion built from `f31c6b6cb`.
- A Heltec V4.3 running published `1.18.0.1` Repeater firmware was the direct
  target. Both used a separate bench channel with repeating disabled.
- Android paired, loaded contacts, disconnected and reconnected without a new
  PIN prompt. After a Companion software reboot, it reconnected again without
  re-pairing.
- The app completed admin login with **Direct** displayed and fetched live
  repeater status, including battery voltage, uptime and radio statistics.
  Admin login and status retrieval succeeded again after the Companion reboot.
- After an application-only USB update from `f31c6b6cb` to `3144d2d30`, Android
  reconnected with the same bond and fetched fresh repeater status again.
- Initial no-reply tests were setup failures: the old repeater needed a restart
  after changing its channel. The USB control login succeeded after that restart,
  followed by the Android test. An earlier pairing attempt also used the wrong
  test-harness PIN; it is not counted as a firmware failure.

The retry fix is shared Companion logic, exercised directly by production-path
regression tests; it is not specific to the V4.2 radio driver. T096/T114 BLE
builds also passed their size and DFU checks. This physical test verifies Android
MeshCore Open, and does not establish MeshCore One/iOS behavior. The Android bond was
created during this test; preservation from stock firmware is not claimed.
The earlier independent Pi-central upgrade test is recorded in
[Bluedroid bond preservation](bluedroid_bond_preservation.md).

## CI regression

The reviewed login change first required the CLI source fingerprint to be
refreshed. The subsequent release-length RAK 3x72 link then exceeded flash by
232 bytes.

Sharing the constant terminal error prefix through the existing `printf` path
saved 392 bytes on that build. Both verbose and concise message bytes remain
unchanged. No features, contact capacities or filesystem boundaries were removed.

All four release-length STM32 USB Companion builds passed their link and RAM
checks: RAK 3x72, Wio E5 Mini, Wio E5 and Tiny Relay. The original 32 KiB
filesystem boundaries remain. RAK 3x72 still has only 160 bytes of physical flash
margin, so further code growth needs qualification.

The CLI test executes all 57 rewritten calls in both text modes and compares
the complete output bytes against the approved messages. The existing source
fingerprint still covers the surrounding control flow. Delayed reply production,
retry ownership, direct-admin guards, help output/stack and identity CLI tests
also passed, including sanitizer coverage where supported.

The later Companion OTA include test exposed a missing `<stdint.h>` include in
`RotarySettings.h`. The header now declares its own integer-type dependency;
all three production include configurations pass without transitive definitions.

Continuing through the remaining test commands found stale button and buzzer
fixtures. They now supply the rotary ownership boundary, no-event constant and
Arduino input mode used by production. Tests also verify that a rotary-owned
button is not polled or treated as a native rescue gesture. The hosted updater
browser test blocks external DNS so optional CDN resources cannot delay its
local navigation and simulated update; all existing transfer and install checks
remain enabled, with local HTTP diagnostics added on failure.
