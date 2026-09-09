# Small-screen message fonts

Small-screen Companion builds automatically select a compact font for received
message text and the channel/sender line, including `Ch 0 Public`. SSD1306 and
SH1106 OLEDs and the U8g2 T-Echo Card display use this on panels smaller than
160 pixels on both axes. ST7735 TFTs now use their native 160x80 dimensions
and normal font. The shared renderer selects by panel dimensions, including
rotation:

| Panel size | Font | Capital height | Line spacing |
| --- | --- | ---: | ---: |
| At least 128 x 64, or 64 x 128 rotated; both axes below 160 | Squeezed Regular 6 | 6 pixels | 8 pixels |
| Smaller panels, including 72 x 40, 128 x 32 and 64 x 48 | Picopixel | 5 pixels | 7 pixels |
| Either axis at least 160 | Normal display font | Depends on panel | Depends on panel |

Both fonts allow one pixel for descenders and one blank pixel row between
lines. Character widths vary: most letters advance by 4 pixels, with narrow
letters taking less space and `M` and `W` taking 6 pixels. Squeezed Regular 6
is a [public-domain font by Oliver Kraus](https://github.com/olikraus/u8g2/wiki/fntgrpu8g#squeezed_r6).
Its bitmap data is stored as constants without heap allocation.

On a 128 x 64 OLED with the single-button message reader, the compact
channel/sender line starts at y=8 and message text at y=16. The header uses
the same small font. Five complete 6px-font rows fit above the navigation
hint at y=56. The software comparison fits three representative 160-character
messages in those five rows. Capacity
depends on the characters; messages with many wide letters can still overflow.
Text wraps at character boundaries, with `...` on the last line if necessary.
Unsupported characters appear as `?`. Long channel/sender names are ellipsized
to stay on their own line. An enabled channel footer reserves its own space.
Readers without the button hint retain the y=14 origin and y=22 message text.

On V4 and other single-button builds using this reader, the home screen says
`hold button: inbox`. Hold the user button for about 1.2 seconds to open it.
The bottom continuously shows `4 <<-  2 <- tap -> 1  ->> 3  hold: Exit` without alternating or
blinking. Double tap goes to the previous
message, one tap advances, three taps select the next channel, and four taps
select the previous channel. Channel selection cycles through All, configured
channels, and direct messages, starting at the newest message in each filter.
The header shows `All`, `Ch N`, or `DM`, including when a channel is empty.
Hold to return home; advancing past the last message also returns home.
The complete hint fits in the V4's existing 8-pixel footer at y=56, leaving
five message rows. The footer keeps the same 6px font, using 108 pixels with
10-pixel margins on the V4. Narrower screens first use the compact `hold:X`
hint without the extra spaces or arrow dashes, then split the
same controls across fixed lines. The hint appears even when the inbox is
empty. These are button taps. Touchscreen and joystick builds retain
instructions appropriate to their controls. During the first eight
seconds after startup, holding the button on an ordinary home page enters
CLI rescue instead; wait for that startup window to finish before opening
the inbox. Exiting a message preview and the WiFi setup page's hold action
remain available immediately.

The hidden [John reader](https://github.com/mikecarper/MeshCore/blob/keymindCascade/tools/bible/README.md#on-device-reader), opened
by a long press on the radio page, uses the same button hints. Three taps jump
to the first verse of the next chapter; four taps jump to the first verse of
the previous chapter. Chapter navigation stops at the beginning and end of
the book. It reserves the hint before pagination and resumes saved bookmarks
at the page containing the same text, even after the available page size changes.

Larger display classes keep their existing font. Menus, Bluetooth PINs and WiFi
setup QR codes keep their normal layout. TFT drivers use native panel
coordinates; the SenseCAP Indicator retains its existing scaling. The normal OLED font uses 7-pixel letters,
8-pixel line spacing and 6-pixel character advances.

The preview buffer holds a complete 160-byte MeshCore message plus its
terminator. Previously the main message UI allocated 78 bytes, leaving room
for only 77 bytes of text. It still retains 32 previews; larger records add
about 2.8 KB of RAM. The firmware RAM guard includes that increase. This
history is separate from the offline queue and its mOTA policy.

The tiny 72 x 40 T-Echo Card interface now previews the latest received
message below its status bar, with three small-font message rows. A button
press dismisses it. It stores one full message, and shows `...` when the
screen fills. Incoming text does not replace an active Bluetooth pairing PIN.

## V4 hardware trial

Enable `platformio.nimble.ini` in the ignored `platformio.local.ini` as shown
in the [NimBLE trial guide](nimble_companion_trial.md#build), then run:

```sh
OUTPUT_DIR=.releases/v4-smallfont bash build.sh build-firmware \
  heltec_v4_2_v4_3_companion_radio_full_femon_nimble \
  --firmware-version v1.17.1.5-halo-keymind-cascade-squeezed6-trial \
  --radio-preset usa-cascadia --profile cascade --standard --require-ota
```

This build keeps NimBLE, 350 contacts, 40 channels, the V4's 512-frame
PSRAM queue, USB mOTA sending and WiFi OTA support. The smaller font is also
the default in ordinary small-screen Companion builds from this source.
The older `_nimble_pixel5` environment name remains available, but also uses
the automatic 5px/6px selection.

Use the application `.bin` for WiFi OTA. A clean USB install uses the merged
image at address 0. When manually writing the application at `0x10000`, an
existing OTA selector may still boot app1. Check the running `ver` afterward.
On the matching V4 16 MB layout only, clearing the 8 KB `otadata` partition at
`0xe000` selects the newly written app0 without clearing NVS/settings. Confirm
the partition table and verify the app write before changing that selector.

A custom build can set `-D UI_SMALL_MESSAGE_FONT=0` to restore the old font
and spacing. Remove any explicit `UI_MSG_PREVIEW_SIZE` flag too if the old
preview capacity is desired. These are compile-time options, not CLI commands.
No settings erase is necessary when changing between matching V4 layouts.

## Verification

```sh
python3 -B test/test_ssd1306_picopixel.py
python3 -B test/test_firmware_ram.py
pio test -e native -f test_display_driver -f test_companion_message_history
```

The native tests exercise both capital heights, automatic font selection,
160-character messages, complete rows at display edges, long sender lines,
navigation hints, reserved footers, overflow markers and tiny/rotated screen
geometry. The additional rendering comparison checks all 95 printable ASCII
glyphs in each
font, under address/undefined-behavior sanitizers. Picopixel is compared pixel
for pixel with Adafruit GFX; Squeezed Regular 6 is compared with the upstream
BDF fixture independently of the converted C++ tables. This needs a cached
PlatformIO Adafruit GFX library; set `MESHCORE_GFX_LIBRARY` to its directory
if needed. It reports a skip when the library is absent. The native tests do
not require that dependency. Run only one PlatformIO command at a time.

## Automatic 5px/6px results, 2026-09-08

Source revision `469b47d1` passed five representative firmware builds. All
five retained the same static RAM usage and startup heap margin as their
earlier 5px builds. The four Full profiles also passed required OTA packaging.

| Hardware/profile | Display | Selected capital height | RAM beyond the required startup budget, bytes |
| --- | --- | ---: | ---: |
| V4.2/V4.3 Full NimBLE | SSD1306 | 6 pixels | 88,072 |
| T096 Full, FEM on | ST7735 | 6 pixels | 24,778 |
| Station G3 ESP32 Full | SH1106 | 6 pixels | 82,080 |
| RAK3401 Full | SSD1306 | 6 pixels | 46,108 |
| T-Echo Card BLE Companion | U8g2, 72 x 40 | 5 pixels | 30,992 |

The native display/history suites passed 36 tests. The Python font, RAM,
pairing, display-profile, queue and QR checks passed 38 tests, including
pixel comparisons for all 95 ASCII glyphs in each font. The actual SSD1306
software rendering matches the approved 6px comparison image pixel for pixel.

Both `NimBLE-V4-VM` and `NimBLE-V4-Trial` were flashed with
`v1.17.1.5-halo-keymind-cascade-squeezed6-trial-469b47d1`. Their partition tables
and application hashes were verified, their settings retained, and running
versions confirmed. Each passed 201 USB protocol requests without error flags.
The Mercerwood V4 reconnected over bonded Bluetooth at MTU 179 and retained
the factory-address policy.

The XIAO sent a private 160-byte LoRa message to the Mercerwood V4. All bytes
arrived, and the V4 stayed responsive through 20 seconds of display refreshes
with no uptime reset or error flags. Both temporary channel settings were
restored. After that interval the V4 had 147,196 bytes of free internal heap,
a minimum of 146,328 bytes, and a largest free block of 139,252 bytes.

These are short functional checks. Physical readability remains a user
judgment. A message with many wide characters can still need truncation.

V4 application size: 1,778,936 bytes (1,152 bytes larger than the earlier
5px trial). SHA-256:
`8e6797b90741bf013b3757668e5a23378ac8c0b0ed9c75830939f13305c56a68`.

## Earlier 5px hardware and build results, 2026-09-08

Source revision `eeea15ef` passed seven representative firmware builds:

| Hardware/profile | Display | RAM beyond the required startup budget, bytes |
| --- | --- | ---: |
| V4.2/V4.3 Full NimBLE Picopixel | SSD1306 | 88,072 |
| T096 Full, FEM on | ST7735 | 24,778 |
| Station G3 ESP32 Full | SH1106 | 82,080 |
| RAK3401 Full | SSD1306 | 46,108 |
| Wireless Tracker Full NimBLE capacity trial | ST7735 | 28,022 |
| Wio Tracker L1 Full | SH1106 | 45,744 |
| T-Echo Card BLE Companion | U8g2 | 30,992 |

These are linked-capacity checks before runtime allocation, not live free
heap measurements. The six Full profiles also passed their required OTA
packaging checks. The T-Echo Card row is a BLE Companion build.

Both physical V4.3 nodes, `NimBLE-V4-VM` and `NimBLE-V4-Trial`, were flashed,
their image hashes verified, and their running versions confirmed. Each
passed 201 USB protocol requests with zero reported error flags. The
Mercerwood V4 also reconnected over authenticated, bonded Bluetooth with
MTU 179 and the existing factory-address policy.

A private XIAO-to-V4 LoRa test delivered all 160 message bytes. The V4
continued responding during 20 seconds of message redraws, with zero error
flags. Both temporary channel configurations were restored. Free internal
heap after that interval was 147,200 bytes, minimum 146,340 bytes, and the
largest free block was 139,252 bytes. These are short functional checks;
physical readability remains a user judgment.

A matching pre/post-boot Mercerwood measurement showed the longer preview
using 2,816 additional PSRAM bytes, with unchanged internal free heap. Boards
without PSRAM use their ordinary RAM for the larger preview records.

The native display/history suites passed 31 tests. The Python font, RAM,
pairing, display-profile, queue and QR checks passed 38 tests. The Adafruit
comparison covers all 95 printable ASCII glyphs.

V4 application SHA-256:
`d914d80124b499d7b719f8427f3794ab1b6d15fd0112e3ef89ba2fac26b5a974`.
