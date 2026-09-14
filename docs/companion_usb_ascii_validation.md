# USB Companion ASCII default validation

The updated [1.17.1.6 Companion release](https://github.com/mikecarper/MeshCore/releases/tag/v1.17.1.6-halo-keymind-cascade-dev-306feebe)
covers 103 USB-enabled profiles: 52 ESP32 Full, 43 nRF52 Full, four RP2040 USB,
and four STM32 USB. The BLE-only WM1110 image has no USB interface and is outside
this change.

## Behavior and cause

Ordinary USB Companions previously started in Binary mode. Full Companions
started in ASCII, but host-session cleanup could select or retain Binary mode
for the next connection.

All USB Companions now select ASCII before framed dispatch begins. An
observable USB session boundary cancels old USB operations and clears partial
input, queued protocol output, frame ownership, and mOTA attachment before restoring
ASCII. A network terminal can defer that restoration; a new USB binary client
that connects during the deferral keeps its chosen mode. BLE and network
Companion transports keep their existing binary protocol.

A complete framed app command still selects Binary mode automatically. An
incomplete initial probe returns to ASCII after one second. Explicit terminal
start/stop controls remain available.

Session detection depends on the hardware. Native USB with DTR can detect a
terminal closing. ESP32 hardware USB Serial/JTAG observes bus resets and host
loss, but cannot reliably observe a program closing its handle. UART bridges
usually cannot observe that close either. Idle time never forces an active
binary app back into ASCII; use the start token on a port that remains Binary.
See the [USB mode guide](full_companion_usb_switcher.md).

## Automated validation

- The real firmware startup and reset functions are compiled and executed for
  nRF52, ESP32 TinyUSB, ESP32 hardware USB, and RP2040, with and without the
  Full macro. Cases include reconnecting from Binary or ASCII, delayed cleanup,
  mOTA cancellation, network ownership, and a new binary client during deferral.
- 55 native serial-mode/flow-control tests and 11 terminal-session/diagnostic
  tests passed locally, along with 49 USB Python tests and the picker tests.
- STM32 number formatting is tested against an Arduino API without `ltoa`.
  CI also compiles an ordinary RAK 3x72 USB Companion with the real core and
  RadioLib, so missing packages and GPIO API incompatibilities fail the build.

The 99 ESP32/nRF52/RP2040 profiles use
[`d4a641ff`](https://github.com/mikecarper/MeshCore/commit/d4a641ff8af1b413c8fb96a670282b799be1f6e8).
The four STM32 profiles additionally use the compatible Arduino 2.12 platform
pin and build test in
[`d2c52e4d`](https://github.com/mikecarper/MeshCore/commit/d2c52e4d047c4b1068a1053364d9acaddefa64a1).
Both source revisions passed GitHub checks:
[main repair](https://github.com/mikecarper/MeshCore/actions/runs/34792631229) and
[STM32 repair](https://github.com/mikecarper/MeshCore/actions/runs/34793043594).

All 103 builds passed capability and RAM qualification. Packaging checks bind
each application to its capability and memory manifests, retain the original
profile capabilities and download formats, and verify ESP32 application/merged
image consistency. Replacement publication verifies public SHA-256 digests
before deleting old images and checks that unrelated firmware assets retain
their original hashes. Release manifests, checksum lists, picker links, and
capacity-note source references follow the new binaries.

Publication completed with all 430 uploaded files verified through their public
download URLs. The 412 superseded firmware/manifest files were removed. The
remaining 1,837 assets kept their original hashes, and all five release checksum
lists matched the final GitHub asset digests.

No USB Companion was attached to the local computer for this repair. These
results are compilation and automated protocol tests, not physical USB tests
across the board matrix.
