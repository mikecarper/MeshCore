# W12 PRAM scanner lab - experimental only

This branch is a laboratory proof of concept, not a release candidate. It contains an experimental on-chip LR2021 dual-profile CAD scanner and guarded host TX handoff, plus per-received-packet internal settings snapshots. It does not change the on-air packet format. The W12 scanner cannot coexist with the Semtech vendor PRAM patch: both occupy the same PRAM. It uses the LDO regulator while active. Normal settings are restored on explicit stop/reset.

An RX-only hardware trial saw packets on both profiles with correct attribution, including independently observed primary packets. The mixed-bandwidth receive error counter increased; the secondary receiver used preamble 72 while the peer used 32. The cause and delivery rate are not yet established. TX handoff, automatic resume, new diagnostic commands and directional timing benchmark have not been qualified on hardware. An interrupt may arrive during guarded handoff; lossless operation is not established. If ownership is uncertain, the lab firmware freezes the radio and requires explicit operator recovery, which discards any ambiguous packet. Do not use this on operational nodes.

## Provenance

The scanner derives from Alex Beal's MIT-licensed https://github.com/beala/lr2021-re, commit 20b2a85efc206eaf7bbaedcfb8b7985b0a620aab; its copyright and license are in LICENSE. scan.c, scan_hdr.S and pram.ld are the lab source; changes include guarded stop and timing command. scan-safe.bin is the corresponding image; src/helpers/radiolib/W12PramScannerBlob.h contains the same bytes as little-endian words. The exact ROM checks in the host are required; do not transplant these commands to other chip revisions.

Base MeshCore commit: 28da91a6e0854552a5f92e8df85963974bd982c5. Upstream unmodified scanner SHA-256: ecbf4e2c677156ab21469cffb4c973645e40bc9903c324391432e959586f292f. Lab scanner SHA-256: 6acb2b8656acb744065381264aa6e1b545f3c133c95900c29e4d1b70b9270ca7.

## Rebuild and test

Run python3 lab/pram_scanner/run_tests.py with Python 3 and g++. It compiles tests in a temporary directory, verifies the guard predicate against the PRAM source, exercises host decisions and Dispatcher with actual implementation code, checks packet serialization, and verifies blob/header equality.

To regenerate PRAM, build the patched ARC GCC 13.3 Docker image from the pinned upstream repository's docker/Dockerfile.gcc, as described upstream, and use its firmware/pram/build_scan.sh with this directory's scan.c, scan_hdr.S and pram.ld. Rename resulting scan.bin to scan-safe.bin and regenerate the host header as little-endian 32-bit words; run the tests. Retain the license notice.

The W12 Full image uses a separate checkout and one PlatformIO process:

    PLATFORMIO_BUILD_FLAGS="-DWEBCONFIG_WIFI_CONNECT_TIMEOUT_MS=60000UL -DMESH_WIFI_ALL_CHANNEL_SCAN=1 -DMESH_WIFI_RECOVERY=1" OUTPUT_DIR=lab/pram_scanner/full-tx-final bash build.sh build-firmware meshnology_w12_repeater_observer_mqtt --full-exact --profile default --radio-preset target --firmware-version v1.18.0.2-w12-pramscan-txlab

Commands include set pramscan on, set pramscan off, get pramscan, get pramscan.last, get pramscan.diag, get pramscan.timing, set pramscan.rxpre 2 32, run pramscan.bench, and get pramscan.bench. Change receive preamble only while stopped. The benchmark measures full SPI command round trips for frequency/modulation configuration, not RF lock, full RX readiness or packet switching. Reset/recovery has different costs.

## CPU light-sleep and single-profile RXPS experiment

With `set powersaving on`, the experiment permits the existing ESP32 sleep policy to enter bounded light sleep while the PRAM scanner owns both profiles. GPIO14 is configured as an RxDone wake source. The existing policy still blocks sleep for an attached USB host, active Wi-Fi/Bluetooth, pending work, or an unavailable radio. The MCU wakes at least once per second for scanner health checks; the radio itself does not enter retention sleep between scan passes. With radio2 off, the lab build additionally exposes single-profile `set radio.rxps conservative` and `set radio.rxps off`, using LR2021 hardware duty cycling. Radio2 suspends ordinary RXPS as in the existing firmware; the experimental PRAM scanner must not be active while changing RXPS.

These additions are not release qualified. Native tests cover timing arithmetic and scanner idle admission. In one controlled W12 trial, continuous RX received five of five test packets, while the initial RXPS implementation received none. After removing an SPI airtime query immediately following RXPS arm, a second trial received five of five in each mode. RadioLib status checks and noise-floor calibration may still access the radio during duty cycling, and long-idle and busy-channel delivery remain unqualified. GPIO-triggered CPU wake, sleep residency, wireless coexistence, and current consumption have not been validated. Experimental RXPS, scanner TX handoff, and their recovery remain disabled outside this lab build. Keep the configured radio and its existing USB/network policies available while testing.
