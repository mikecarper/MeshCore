<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/tdeck_radio2_display/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# T-Deck radio2 scheduling during display redraw

Measured on 18 October 2026 (America/Los_Angeles), using one ESP32-S3 T-Deck
with 16 MiB flash and 8 MiB PSRAM. The receive-only harness uses the production
`CustomSX1262Wrapper`, profile scheduler and `ST7789LCDDisplay`. It never sends
radio packets or starts WiFi/Bluetooth. Source base: `22cdb7014`, with the
changes and application readback hash identified in the
[machine-readable results](tdeck_radio2_display_results.json).

## Change

T-Deck renders into a 153,600-byte RGB565 canvas allocated explicitly in PSRAM.
It transfers at most 960 pixels per TFT transaction (384 microseconds of wire
time at 40 MHz). Each transfer ends and releases chip select before servicing
the radio. CPU rasterization also offers bounded cooperative checkpoints.
The callback runs only the radio driver, without recursively entering the mesh
dispatcher or UI. Pending-packet, active-reception and transmit guards remain.

The T-Deck radio also uses the existing buffered 8 MHz SPI HAL. The TFT and radio
retain their shared HSPI peripheral and separate chip selects. Display wake
restores the backlight without repeating controller initialization. Other
boards keep their existing display path. If PSRAM allocation fails, the driver
warns and falls back to direct rendering.

## Same-image measurements

The two profiles were 909.5 MHz / SF7 / BW62.5 and 910.5 MHz / SF8 / BW500,
both CR5 with explicit 32-symbol preambles. These are measurement controls,
not a packet-delivery or preamble recommendation.

Each stress case ran three times for five seconds, requesting 20 redraws per
second. Rendering could take longer than the requested frame interval; actual
frame counts and elapsed times are retained in the results. Values below use
all samples across the three trials, with worst observed maxima.

| Display path | Radio SPI | Mean retune | Maximum retune | Maximum gap between radio service calls |
| --- | --- | ---: | ---: | ---: |
| Static screen | Bytewise, 2 MHz | 497 us | 525 us | 584 us |
| Original direct redraw | Bytewise, 2 MHz | 610 us | 629 us | 76,089 us |
| PSRAM canvas and bounded strips | Bytewise, 2 MHz | 581 us | 668 us | 1,343 us |
| PSRAM canvas and bounded strips | Buffered, 8 MHz | 468 us | 581 us | 1,237 us |

At a normal requested refresh rate of one frame per second (two four-second
trials per case), the original 2 MHz path's maximum service gap was 73,383 us;
the new 8 MHz path's was 1,251 us. Reducing refresh frequency alone does not
remove the individual blocking redraw.

Retune samples bracket the production retune hooks. They are separate from
display frame duration and the gap between radio service calls. Histogram
percentiles in the JSON are bucket upper bounds, not exact percentiles.

All 30 trials completed 7,025 profile switches with zero reported switch
failures, failed retunes, BUSY timeouts or chip errors. Every final RX-mode and
modulation-cache check passed. The minimum remaining loop stack was 3,340
bytes in this harness. No packets were decoded, so these measurements do not
establish packet delivery under redraw, loaded-channel behavior, Bluetooth/
WiFi interference, or long-term reliability.

## Reproduction and checks

Include `tools/hil/tdeck_display_switch.ini` in `platformio.local.ini`, build
`tdeck_display_switch`, and flash the exact T-Deck. The board needs a
ROM-compatible DIO boot header; the first QIO-header attempt failed before the
application started. The supplied harness recipe selects DIO.

Run the collector with `--fps 20 --seconds 5 --repeats 3`, then with
`--fps 1 --seconds 4 --repeats 2`. Only one tool may own the selected serial
port, and PlatformIO commands must run sequentially.

Sixteen focused native display/R8/radio tests passed. They cover pixel parity,
clipping, partial updates, strip bounds, released SPI before callbacks,
callback recursion protection, PSRAM failure, mode toggling and sleep/wake.
The new display regression is included in CI.

## Full Companion smoke check

The optimized canonical Full Companion image
`v1.18.0.1-tdeck-radio2-fast-22cdb701` compiled and passed its unchanged RAM
gate (230,680 internal bytes available; 164,896 required). It was installed
on the same board. USB ASCII commands and Binary Companion device queries
worked; Bluetooth reported on. Device queries retained 350 contacts and 40
channels. A Bluetooth client connection was not tested.

With the screen held on, a temporary receive-only secondary accumulated 759
switches with zero switch errors during a short runtime check. Its maximum
successful active-pair retune was 1,013 us and the guarded budget was 1,115 us.
One additional primary packet was received during that interval. This is a
smoke check rather than a controlled packet-delivery trial. Returning to
single-profile RX power saving raised the lifetime `max` diagnostic to 4,510 us;
that teardown is outside the active pair's measured switching budget.

The board was left running Full Companion with Bluetooth on, radio2 off and
its original `button-pairing` USB display mode. Seven hosted-document tests
also passed after adding this report.
