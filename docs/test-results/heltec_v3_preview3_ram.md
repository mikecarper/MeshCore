# Full Companion RAM audit and Heltec V3 preview 3

Date: 2026-10-02. Baseline: `0ccac453` plus the early startup-screen changes
included in this revision. All 13 selected ordinary Full builds passed the
existing linked runtime RAM guard. This is a targeted audit, not a claim that
every Companion has been rebuilt or hardware-qualified.

## Builds needing the most attention

| Hardware | Available bytes | Reserved bytes | Margin bytes |
| --- | ---: | ---: | ---: |
| Generic ESP-NOW | 143,872 | 142,336 | 1,536 |
| Tracker V2 (FEM on) | 180,000 | 177,746 | 2,254 |
| Heltec V3 | 185,032 | 182,016 | 3,016 |
| XIAO ESP32-C3 | 146,048 | 142,336 | 3,712 |
| Heltec CT62 | 146,240 | 142,336 | 3,904 |
| Wireless Tracker | 184,304 | 178,946 | 5,358 |
| Heltec T096 (FEM on) | 84,152 | 75,298 | 8,854 |
| Heltec T1 | 84,488 | 75,298 | 9,190 |
| LilyGo TLora V2.1.1.6 | 185,304 | 173,840 | 11,464 |
| Heltec V2 | 187,176 | 173,840 | 13,336 |
| TBeam SX1276 | 194,160 | 169,744 | 24,416 |
| TBeam SX1262 | 196,112 | 169,744 | 26,368 |
| Heltec T114 | 84,336 | 46,880 | 37,456 |

The six smallest margins belong to non-PSRAM ESP32 Full Companions. They pass
their existing release capacities but have little room for further growth.
Heltec T096 and T1 are the next constrained screen-equipped nRF52 Full builds.
T114 Full passes; the failed older T114 BLE-only recipe is not a Full failure.

Values come from the firmware RAM guard in the retained local
`out/companion-ram-*-before.log` build logs. Available bytes exclude statically
reserved memory and unavailable internal regions; the reservation covers
startup allocations and a transient allowance. The margin is not measured
live free heap or a prediction of time to failure. No safety budget was lowered.

## V3 solution under test

The opt-in `platformio.heltec-v3-preview3.ini` profile uses the existing
NimBLE-Arduino adapter, pinned to version 2.5.1, instead of Bluedroid. It retains
the current ordinary V3 Full capacities: 100 contacts, 40 channels, and 256
offline frames, including during mOTA. It adds no new queue-sharing or storage
changes and does not enable the larger 350-contact capacity trial.

| V3 image | Available internal bytes | Reserved bytes | Margin bytes |
| --- | ---: | ---: | ---: |
| Published preview 2 | 185,040 | 182,016 | 3,024 |
| Preview 3 | 200,152 | 182,016 | 18,136 |

Compared with published preview 2, preview 3 recovers 15,112 bytes of internal
RAM without removing Full features. Its bootloader and partition-table bytes
match preview 2. The normal release matrix is unchanged.

Bluetooth still provides the Companion server, encrypted PIN pairing,
bonding, MAC policy and stealth controls. Existing Bluedroid bonds are not
migrated: forget the old device in the phone's Bluetooth settings and pair
again after installation. This does not require erasing contacts or identity.

The preview also contains the early `Starting...` screen and animated key
generation display. The saved Off and pairing-only display modes remain
respected. Normal UI timeout starts after startup. Fresh installs still
default to Bluetooth on and infrastructure WiFi off; saved WiFi-on settings
remain on. Use `set wifi.enabled 1` to enable WiFi.

## Verification and remaining work

The real embedded build passes the unchanged RAM guard and fits both existing
WiFi OTA slots. Capability checks inspect the linked ELF, packaged application
and WebConfig gzip asset. The package includes ELF-bound RAM proof and hashes.

Adapter regressions exercise actual NimBLE transport code under sanitizers,
including identity/bond failures, encrypted PIN authentication, stealth,
notifications, MTU gating and frame retry. Startup-screen, display policy,
Companion preferences, USB defaults, identity and frame-queue regressions are
also checked. Embedded compilation uses the actual pinned NimBLE library.

This is still a hardware-test preview. V3 first-boot screen, phone pairing and
reconnection, contact sync, WiFi/BLE coexistence, mOTA sending, and sustained
load need physical verification. A build passing RAM checks does not establish
that all reported V3 boot symptoms are fixed. Do not make this stack the default
on every board before those checks.

Reproduce with one PlatformIO process at a time:

```sh
pio run -c platformio.heltec-v3-preview3.ini \
  -e Heltec_v3_companion_radio_full_preview3 -t mergebin
python3 -B test/test_heltec_v3_preview_profile.py
python3 -B test/test_nimble_companion.py
python3 -B test/test_startup_screen.py
python3 -B test/test_firmware_ram.py
```
