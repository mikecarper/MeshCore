<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/optional_screens/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Optional screens

Use the normal display-capable firmware for your exact board and role. When
automatic detection is included, that image starts the supported screen if
it responds, or continues headless if it is absent. There is no screen-presence
setting. Power the board off before attaching or removing an optional screen.

The [firmware picker](firmware_picker.md) describes automatic screen detection
only when the selected application has been verified to contain it. Previously
published firmware retains its original behavior. These additions become
available as new firmware is built and qualified; changing the picker does not
add detection to an older download.

## Manufacturer-confirmed optional screens

| Hardware | Manufacturer evidence | Firmware behavior |
| --- | --- | --- |
| Heltec T114 | [Heltec](https://heltec.org/project/mesh-node-t114/) offers the optional stock screen. | Automatic ST7789 detection; see the [T114 guide](heltec_t114.md). |
| Heltec RC32 with T108 | The [T108](https://heltec.org/project/t108/) is a separate pin-header display module. The [manufacturer example](https://github.com/HelTecAutomation/RadioCore_Library/blob/45f20ff79f66d84022497040fda7cb7d0824639b/examples/NV3001B_Display/README.md) documents its pins and power controls. | New display-capable images detect the NV3001B panel. Legacy `without_display` targets remain headless compatibility builds. |
| Heltec Wireless Tracker V1/V1.1 | The [manufacturer listing](https://old.heltec.org/project/wireless-tracker/) explicitly offers Screen Yes/No. | New display-capable images detect the ST7735 panel. Its absence releases only the display's power claim; GPS retains its own claim on the shared supply. |
| Heltec V4 R8 Expansion Kit | The [manufacturer kit](https://heltec.org/project/v4-r8-ex/) offers front-panel configurations; its V2 expansion schematic connects the display's separate return line. | New R8 TFT images detect the panel through the documented MISO pin. Existing touch reset and backlight-only sleep/wake behavior are retained. |
| Heltec V4/V4 R8 OLED | The [manufacturer listing](https://heltec.org/project/wifi-lora-32-v4/) offers OLED and No Display configurations. | Existing SSD1306 images already check whether the OLED responds. |
| RAK WisBlock | The [RAK4631 kit guide](https://downloads.rakwireless.com/Meshtastic/Quick%20Start%20Guide_WisMesh%20RAK4631%20Starter%20Kit.pdf) labels the RAK1921 OLED optional. | Existing display-capable SSD1306 images already check presence. |
| iKoka Stick and Handheld | The designer documents an [optional Stick OLED](https://github.com/ndoo/ikoka-stick-meshtastic-device#oled-display-optional) and [optional Handheld OLED](https://github.com/ndoo/ikoka-handheld-meshtastic-device#oled-display-optional). | Existing display-capable images already check presence. Handheld Companion supports the screen; currently headless infrastructure recipes remain headless. |
| Original LilyGo T-Beam | [LilyGo](https://lilygo.cc/products/t-beam-v1-1-esp32-lora-module) offers soldered/non-soldered OLED configurations. | Existing display-capable SSD1306 images already check presence. |
| XIAO ESP32-S3 with Wio-SX1262 | [Seeed](https://wiki.seeedstudio.com/wio_sx1262_with_xiao_esp32s3_kit/#recommended-accessories) lists the expansion board as an accessory for adding screens and sensors. | Existing display-capable Companion images already check presence. |

The older V4 TFT expansion configuration is also optional, but its current
recipe has no verified panel readback connection. Detection is not enabled for
that wiring. A touchscreen acknowledgement alone does not establish that the
LCD responds. The V4 R8 TFT and older V4 TFT pin mappings are distinct.

Full Wireless Tracker repeaters retain the board's 50-neighbor capacity, as
the V1.1 and V2 Full profiles already do. This preserves RAM for the screen,
GPS, MQTT and update services; the RAM admission limits remain unchanged.

The R8 readback route is documented in Heltec's
[Expansion Kit V2.03 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_Expansion_Kit-V2/schematic/Expansion_board_V2.03.pdf).
Tracker V1 wiring is documented in the
[V1.1 schematic](https://resource.heltec.cn/download/Wireless_Tracker/Wireless_Tacker1.1/HTIT-Tracker_V0.5.pdf).

Screen presence does not establish controller type, dimensions, physical
orientation, or board revision. Continue choosing the exact hardware profile.
Use USB when changing an old firmware target name; a different target's LoRa
update package is not interchangeable. Saved identity and settings do not need
to be erased for screen detection.

## Integral screens

Manufacturer documentation identifies built-in screens on the
[LilyGo T-Echo Card](https://lilygo.cc/products/t-echo-card),
[Heltec T190](https://wiki.heltec.org/docs/devices/open-source-hardware/esp32-series/vision-master/vison-master-t190/),
[T1](https://heltec.org/project/mesh-node-t1/),
[T096](https://heltec.org/project/t096/),
[Wireless Tracker V2](https://heltec.org/project/wireless-tracker-v2/),
[ThinkNode M9](https://www.elecrow.com/wiki/ThinkNode_M9_MeshCore_Communication_Terminal_with_Full_Keyboard.html)
and [T-Deck](https://wiki.lilygo.cc/products/t-deck-series/t-deck/).
No new optional-screen detection is enabled for these boards. In particular,
the T190's optional module is its LoRa radio, not its screen.

## Validation

The new probes run before display SPI initialization and framebuffer
allocation, with the backlight off. Bounded repeated reads under opposite
pull directions reject floating, stuck and unstable return lines. An absent
screen stays disabled until reboot; later drawing or wake requests cannot
power it back up. Shared supplies retain other peripherals' ownership.

Native tests cover controller replies, timing, pin turnaround, power ownership,
headless behavior, rendering, sleep/wake, and exclusions for integral screens.
Release checks require the detection marker in the uploadable application,
the exact resolved board/driver flags and the matching source commit before
the picker can advertise detection.

| Check | Result |
| --- | --- |
| Native detection, rendering, power and headless regressions | PASS, 31 test methods, including 12 new detection methods with sanitizers and negative controls |
| Recipe, release-proof, controls and offline-picker regressions | PASS, 42 test methods |
| Resolved PlatformIO configuration | PASS, 31 optional-screen environments across the three hardware families; integral-screen recipes excluded |
| Picker JavaScript regressions | PASS, 4 suites |
| Complete build-profile selection suite | PASS |
| Hosted documentation links | PASS, 7 test methods |
| Packaged ESP32 firmware smoke builds | Pending |

Physical verification on RC32, Tracker V1 and V4 R8 with and without their
manufacturer-supported screen has not been performed. Hardware qualification
requires cold USB/battery boots, screen sleep/wake, headless input, normal radio
transmit/receive, and GPS or touch operation where fitted.
