# nRF52 USB READY hang

The RAK3401 on Mercer was found stuck in the framework's TinyUSB power handler:
USBD was enabled and attached, but its write-one-to-clear READY event was
already consumed. The duplicate-event guard also required HFCLK to be running.
If that guard missed the completed initialization, the following unbounded
READY wait could stop application startup. A bootloader update alone cannot
replace this USB driver in an already installed MeshCore application.

The wait and guard were inherited through the pinned Adafruit nRF52 Arduino
framework, not introduced by the recent OTAFIX upstream merge. TinyUSB's wait
dates back at least to 2019; the guard was added in
[upstream commit 7d9efd0697](https://github.com/hathach/tinyusb/commit/7d9efd06979f6cdde2a4093f0c26e8100312c92c).
OTAFIX's August 24 HFCLK backport addressed a later clock wait, leaving this
earlier READY wait uncovered. The exact live interrupt ordering is not known;
the stalled program counter and consumed event were read directly over SWD.

All nRF52 environments inherit `pre:scripts/nrf52_usb_power_fix.py`. It compiles
a build-local replacement for the matching framework driver, without changing
PlatformIO's shared SDK or affecting ESP32 builds. A changed/unrecognized
framework implementation stops the build for review. The backport:

- requests HFCLK before checking the consumed READY event;
- rechecks attachment while waiting, including completion by another callback;
- bounds both waits with one 100,000-poll budget, independent of OS ticks;
- ignores stale READY on a disabled peripheral and exits if it is removed.

The poll budget is an iteration limit, not a promised elapsed-time timeout.
If the clock/peripheral never becomes ready, the caller returns instead of
freezing the application. A later power event can retry; permanent electrical
failure can still require reconnecting USB. This does not establish or fix the
separate Pi hub/controller fault.

Run `python3 -B test/test_nrf52_usb_power.py -v`. The tests compile the actual
patched handler from the pinned-framework fixture, model W1C event semantics,
and cover duplicate/nested callbacks, delayed/missing clocks and READY, removal,
retry, and detached USB. Restoring the inherited READY prefix must reproduce
the infinite wait under a subprocess deadline. Additional tests check SDK
isolation, idempotence, fail-closed patching, and all nRF52 environment hooks.
The harness is shared with OTAFIX's TinyUSB fork at
`test/otafix/nrf5x_power_test.py`; keep the two copies in sync when extending it.
