<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_t114_display_detection/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Heltec T114 automatic screen detection

The canonical T114 firmware now includes the optional ST7789 screen driver and
checks for the stock screen at boot. Screenless boards use the same firmware.
Legacy `without_display` build names remain compatibility aliases; automatic
release selection uses the canonical targets.

## Hardware basis

Heltec's [stock panel specification](https://resource.heltec.cn/download/Mesh_Node_T114/1.14inch%20LH114T-IF03%20VER%20C.pdf)
identifies the controller as ST7789V2 and describes SDA as an SPI input/output
pin. The existing T114 wiring supports register reads over this shared data
line; a separate MISO wire is unnecessary.

The [Sitronix controller specification](https://files.waveshare.com/wiki/common/ST7789V2.pdf)
documents the read-ID command and serial turnaround on pages 58–64 and 167,
and reset recovery on pages 48–49. Its ID bytes are programmable, so detection
does not require a single guessed factory ID.

## Expected behavior

- The backlight stays off during a bounded boot probe, before framebuffer
  allocation and display SPI startup.
- Consistent ID responses with both data-line pull directions distinguish a
  responding panel from floating, stuck, or unstable inputs.
- An absent panel or failed display initialization leaves display power off.
  Later drawing and wake requests cannot start an absent panel.
- Companion button events remain safe without a display. Notification
  acknowledgement, buzzer toggling and early-boot CLI rescue remain available;
  visual navigation is skipped when there is no display.
- A detected panel retains normal rendering, sleep, wake, and display settings.
- Detection is specific to the T114 automatic-display recipe. Other boards
  using the shared ST7789 driver retain their existing behavior.
- The RAM gate budgets a responding screen even when a particular board boots
  headless. The T114 display needs two 4,080-byte buffers and one temporary
  480-byte RGB565 row: 8,640 bytes of peak heap allocation, in addition to the
  normal allocator and transient margin. The same model follows the shared
  driver's T190 and M9 panel sizes and its single-buffer configuration.
- Picker consolidation requires evidence from the exact published application.
  Historical releases retain their original screen behavior and update IDs.

## Validation

| Check | Result |
| --- | --- |
| Native probe, GPIO, headless and wake regressions | PASS, 4 methods with ASan/UBSan; late-turnaround, reset-delay and floating-line negative controls |
| Resolved recipe and compatibility-alias checks | PASS, 5 methods; actual PlatformIO configuration also confirms all 5 legacy aliases match their canonical targets |
| Companion headless buttons and related navigation, reader, pairing, startup and deadline suites | PASS, 55 methods, including native headless fault regression and 4 input/reader configurations |
| Picker and release-selection regressions | PASS, 78 Python methods and 3 Node suites; old assets, scoped previews, newest-role defaults and unavailable legacy choices covered |
| Display allocation accounting and firmware RAM gates | PASS, 46 methods, including production allocation tracking across 3 panel sizes and buffer configurations |
| T114 firmware and package checks | Pending |
| Hosted documentation links | PASS, 7 methods |
| Physical T114 with and without a screen | Not performed |

Native tests simulate controller replies and GPIO state; they do not establish
physical detection reliability. Before calling the hardware qualified, test
cold USB and battery boots with a stock screen and with no screen, then verify
screen sleep/wake, Bluetooth/USB connections, and normal radio receive/transmit.
