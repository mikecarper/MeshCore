# Offline Reader lookup

ESP32 and nRF52 **Full Companion** USB terminals (and the ESP32 TCP terminal)
include an embedded 879-verse corpus from the World English Bible:

```text
get reader 3:16
```

Book/verb case and surrounding spaces are ignored. One chapter:verse reference
is required; ranges, whole chapters and other books are not supported. ASCII
verse text is printed in full, including verses longer than the ordinary
160-byte CLI response. This is a local terminal command, not a LoRa request or
an extension of the framed Companion API. No WiFi, phone, API key, filesystem
space or bootloader update is needed. Reduced/ordinary Companion and other
roles omit the corpus. `COMPANION_FEATURE_READER=0` excludes it from a Full build.

## On-device Reader

Full Companions using the `ui-new` display (including the V4) also offer a
Reader from the radio-settings page:

- Long press opens at the first reference, or resumes the saved position.
- Single click advances one screenful, then to the next verse.
- Double click goes back one screenful; at a verse boundary it shows the
  previous verse's last screenful.
- Three taps jump to verse one of the next chapter; four taps jump to verse
  one of the previous chapter. Navigation stops at the first and last chapters.
- Long press exits to the radio page. It does not invoke early-boot CLI rescue
  while opening or closing the reader.

The header shows the reference and part count. Single-button builds alternate
`<- 2 tap  1 tap ->  long press: exit` and `<<- 4 tap  3 tap ->>` in the footer
every three seconds, matching the message controls (which change channels).
These are taps of the user button. Narrower displays use `hold: X` for exit
or split the hint across lines. Both hint views reserve the same height; the
V4 keeps its existing single footer row. Touch/joystick builds keep their own
controls. The entry gesture stays hidden on the radio page. The footer has
reserved space in the pagination calculation, so text continues on another
page when necessary.
Text wraps at word boundaries without truncation. Both the screen and CLI use
the same text with plain ASCII quotes, apostrophes, dashes and spaces; wording
and capitalization are unchanged. Incoming messages are retained without
replacing the reader; Bluetooth pairing retains its existing priority. Existing
screen-sleep/wake behavior is retained: the first gesture while asleep wakes
the display.

Sub-160px displays use the same adaptive font selector as received messages:
Squeezed Regular 6 on panels at least 128x64 (or 64x128 when rotated), and
5px Picopixel on smaller panels. The reference and part counter retain their
normal header font, stacking on narrow rotated screens to avoid overlap. On
the V4's 128x64 display, five complete 8-pixel-spaced body rows fit above the
navigation hint, and reference 3:16 fits on one screen. Wrapping and pagination use the selected font's
actual character widths and line height. Any panel with either dimension at
least 160px uses its normal font, including rotated 80x160 panels. Larger display classes, and builds with
`UI_SMALL_MESSAGE_FONT=0`, retain the regular body font. Existing bookmarks
resume at the page containing the same text offset after a font/layout change.

The T096 and other ST7735 boards use native 160x80 coordinates and the normal
body font (five 10-pixel-spaced rows between the header and button hint). The existing color
framebuffer is reused, with no framebuffer RAM increase. ST7789, ST7789LCD,
NV3001B and GxEPD displays likewise expose their actual panel dimensions and
render pixels without coordinate scaling; portrait orientations are retained.
SenseCAP Indicator scaling is unchanged. Integer heading sizes remain explicit
font choices, not whole-screen coordinate scaling.

A 12-byte, versioned/checksummed bookmark stores the verse and text offset.
It is checkpointed after two seconds without navigation and flushed on exit,
screen sleep, or UI shutdown. Writes occur only for changed positions; failures
remain pending for retry. Abrupt power loss during the two-second debounce may
lose that last movement. Returning to the very first screen clears the bookmark
instead of saving a first-reference record. Opening and closing there creates no file.

The bookmark uses `/reader.pos` on the primary filesystem, with a verified `.tmp`
replacement and `.bak` fallback for interrupted saves. It does not change the
radio-preference layout, contacts, channels, or keys. A malformed bookmark
falls back to the previous valid record or the first reference; no filesystem is formatted.
The reader allocates only a small cursor object on first use, reusing the
same flash corpus and on-demand 2 KiB decode scratch as the terminal.

## Source and reproduction

`reader-web.json` contains the embedded corpus, copied from eBible.org's verse-per-line
**engwebp** source (World English Bible, American English Protestant edition,
a subset of the World English Bible Updated). The JSON records the archive URL,
source date and SHA-256 hashes. The verse-per-line export omits footnotes and
headings; punctuation and words are preserved as UTF-8 in this source file.
The packer converts curly quotes/apostrophes to straight ones, en/em dashes to
hyphens, and nonbreaking spaces to ordinary spaces before compression. No
letters or words are changed. Any other non-ASCII character is rejected for
review, never silently dropped or transliterated. This conversion happens on
the computer, with no firmware conversion buffer or extra decoding step.
No NLT or ERV text is included.

The text is [public domain](https://ebible.org/engwebp/copyright.htm).
World English Bible is a trademark of eBible.org; the source must not be
rewritten while continuing to label it WEB. The source and generated header
are versioned together so ordinary builds work offline and never fetch a
moving upstream text. See the [official download inventory](https://ebible.org/find/details.php?all=1&id=engwebp).

Regenerate or verify the firmware header:

```sh
python -m pip install -r requirements-build.txt
python tools/bible/pack_reader.py
python tools/bible/pack_reader.py --check
python tools/bible/pack_reader.py --compare
python test/test_companion_reader.py
```

To deliberately update the source, download `engwebp_vpl.zip` from the recorded
URL, then run `python tools/bible/import_web_reader.py /path/to/engwebp_vpl.zip`.
Review the plaintext changes and regenerate the header. This importer does
not download content automatically or add other books to the firmware.

## Compression and memory

Measured after ASCII typography conversion (96,568 bytes including verse
terminators, versus 98,911 bytes for the pinned UTF-8 source):

| Block limit | Blocks | DEFLATE bytes | Index bytes | Total flash data | Savings |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 KiB | 101 | 47,133 | 1,212 | 48,345 | 49.94% |
| **2 KiB** | **49** | **42,187** | **588** | **42,775** | **55.70%** |
| 4 KiB | 24 | 38,366 | 288 | 38,654 | 59.97% |

Savings in the table are relative to uncompressed ASCII. Google Zopfli 0.4.3
uses 15 optimization iterations to write standards-compliant raw DEFLATE, so
the existing firmware decoder and RAM use do not change. At the chosen 2 KiB
limit, the Zopfli asset is **2,097 bytes (4.67%)** smaller than the prior
44,872-byte UTF-8 level-9 DEFLATE data and index; that result includes the
existing ASCII typography conversion. No 6-bit packing is used.

The chosen 2 KiB limit saves 5,570 flash bytes over 1 KiB for another 1 KiB
of temporary decode storage. Going to 4 KiB saves only another 4,121 flash
bytes and doubles that buffer. The 2 KiB choice leaves more stack headroom
for nRF52's existing BLE/USB/radio calls. These figures include block-index
padding but exclude the small command code, attribution strings and decoder.

Compression runs on the computer. Firmware reuses the existing bounds-checked
C `tinf` decoder and expands one independent raw-DEFLATE block per lookup.
The block index stores only each block's first verse; scanning NUL separators
inside the decoded block avoids a large per-verse index. Text/indexes remain
in memory-mapped flash on ESP32 and nRF52. There is no permanent text buffer,
cache, or additional compression library. The terminal needs no heap allocation;
the screen reader's small cursor object is described above. The temporary
2 KiB output buffer is **in addition to** the decoder's trees and call stack.

A Cortex-M4 GCC 14.2 `-Os -fstack-usage` build of the RAK3401 Full Companion
reports a 2,088-byte frame for verse output, a 16-byte command dispatcher,
and about 1,720 bytes for the nested decoder calls. Including the terminal
and main-loop frames, the decode path is approximately **4.3 KiB**, before
framework/task and interrupt overhead; the nRF52 loop task has an 8 KiB stack.
Before the navigation-hint addition, the small-font reader's decode/draw
function had a 2,480-byte frame on RAK3401
and a 2,496-byte frame on V4. Its nested decode path is approximately 4.3 KiB
on RAK3401 and 4.7 KiB on V4, including the main-loop/UI frames but before
framework/task and interrupt overhead. The ESP32-S3 V4 terminal's corresponding
decode path is approximately 4.6 KiB.
This is a compiler-derived estimate, not a hardware stack high-water reading.
Ordinary non-reader commands never enter the decode-buffer function. Its object
file has zero `.data`/`.bss`; the screen reader adds only its small UI state.

Tests compare all 879 decoded verses and terminal responses with an independently
ASCII-normalized copy of the pinned plaintext source, check references across
every block boundary, exercise malformed references and corrupted block
bounds, and verify that unrelated build profiles exclude the data.

Reader tests additionally traverse every part of all 879 verses in both
directions, with and without the button hint, using regular and adaptive
5px/6px fonts at 128x64, 64x128, 160x80,
240x135, 72x40, 40x72, 128x32, 32x128, 64x48 and 48x64. They compare rendered
body pixels/text separately from the hint, check active-reader resizing across
font thresholds, and cover resume
within a verse, book/chapter boundaries, no writes at the start, clearing stale
bookmarks,
corruption, I/O failure at each replacement step, timer rollover and bookmark
migration from the older font. The small-font renderer leaves the underlying
driver/header font unchanged.
`preview_reader.py` can render the fixture's `--preview` output using an
installed Adafruit GFX `glcdfont.c` and Pillow for pixel-level V4 layout checks.
Adaptive-font body pixels come directly from the real shared renderer, not a
substitute desktop font.

Earlier verification on 2026-09-08: all ten host tests pass on Windows and Linux after
the ASCII conversion and integration with the smaller font at `03e42830`.
The small-font C++11 reader fixture also passes AddressSanitizer and
UndefinedBehaviorSanitizer, the 22 existing nRF52 ExtraFS contract tests pass,
and the separate shared-font check matches all 95 printable ASCII glyphs
against Adafruit Picopixel. All 13 memory-policy tests pass in WSL. The following
USA Cascade Full builds include the local ASCII Reader changes on top of
`03e42830`; both pass firmware-size, runtime-RAM and capability checks:

| Build | Flash bytes | Static RAM bytes | Runtime RAM available / required |
| --- | ---: | ---: | ---: |
| RAK3401 Full | 574,028 | 135,620 | 99,892 / 53,792 |
| V4 Full | 2,220,529 | 112,824 | 248,824 / 175,872 |

These totals also include the pulled contact-cache/message-font changes, so
they are not an isolated reader-size comparison with earlier builds. The
separate upstream contact-cache host suite could not run in this WSL image
because its OpenSSL development headers are absent; firmware compilation and
the nRF52 ExtraFS contract checks succeeded.

Firmware build/stack measurements are compiler checks, not hardware high-water
readings; those earlier builds were not flashed to hardware.

### Adaptive 6px/5px update, 2026-09-08

The reader now shares `SmallMessageText` with messages on base `8f5614c9`.
All ten reader host tests pass on both Windows and Linux. The actual reader
also passes AddressSanitizer and UndefinedBehaviorSanitizer across all ten
screen geometries above, including resizing and bookmark migration. The
separate shared-font test checks all 95 glyphs of both fonts against their
upstream fixtures; the four T096 footer/profile checks also pass.

The T096 Full FEM-on USA Cascade build uses 573,384 flash bytes and 135,452
static RAM bytes, with 100,060 runtime bytes available against 75,298 required.
Its ten capability markers pass. The application-only UF2 was installed on
the T096, then the full running version and references 1:1, 3:16 and 21:25 were
verified over USB. The API snapshots confirm unchanged identity, contacts,
channels, self settings, auto-add, tuning and custom variables. No bootloader
or filesystem was erased. Physical on-screen readability and button behavior
remain a user check, separate from these host-rendering and USB tests.

The V4 Full NimBLE build uses 1,826,513 flash bytes and 97,836 static RAM bytes,
with 263,936 internal runtime bytes available against 175,872 required. All 13
capability markers pass. The nonmerged application was installed in both
existing V4 app slots; their hashes were verified, and the bootloader, partition
table, NVS, OTA selector, SPIFFS and coredump regions compare byte-for-byte with
the pre-update full-flash backup. The full running version and references 1:1, 3:16,
21:17 (the longest verse) and 21:25 were verified over USB. Pre/post API snapshots
match for identity, contacts, channels, self settings, auto-add, tuning, custom
variables, Bluetooth name and default flood scope.

The subsequent T096 native-resolution build uses 573,288 flash bytes with the
same 135,452 static RAM bytes and runtime budget as the scaled build. The actual
driver header/drawing-method fixture passes in native and legacy modes, with
both constructor variants; it checks complete pixel coverage and all 95 native
6px glyphs. Linux additionally runs that fixture with address/undefined-behavior
sanitizers. The reader tests include 160x80 navigation and bookmark migration.
The adaptive reader's compiler-reported process frame is 2,504 bytes on T096
and 2,512 bytes on V4; these are not total stack high-water measurements.

### Native display profiles and normal-font cutoff, 2026-09-08

All five formerly scaled non-Indicator driver families now use native panel
coordinates: ST7735, ST7789, ST7789LCD, NV3001B and GxEPD. This includes both
portrait and landscape TFTs and the repeater observer dashboard. Controller
RAM offsets, panel wake/reset wiring and existing framebuffer allocations are
retained. ST7789 flush tests also cover widths above 255 and final partial
8-row bands, in both single- and double-buffer modes.

The small-font policy is runtime geometry-based: either dimension >=160px
selects the normal driver font. It does not change message-buffer capacity or
the compact-panel footer policy. Normal-font line heights drive message and
radio spacing; Indicator render profiles keep their existing geometry.

Validation: 35 Python-host tests (including actual-driver recording fixtures,
all 879 corpus verses across 17 geometries, shared fonts, wake and pairing),
30 native dashboard tests, and nine message-history/layout tests pass. Linux
pixel/transfer/font fixtures use address/undefined-behavior sanitizers. The
T096 normal-font routing check was rerun after its final assertion was added.

The T096 Full FEM-on USA Cascade build on `0058f721` plus these local changes
uses 573,480 flash bytes and 135,452 static RAM bytes. Runtime capacity is
100,060 bytes against 75,298 required; all ten capability checks pass. Its
application-only UF2 was installed, and USB reports
`v1.17.1.5-native-normal-0058f721`. References 1:1, 3:16 and 21:25 work. API snapshots
match for identity/private key, contacts, channels, self settings, auto-add,
tuning and custom variables. The bootloader and storage were not flashed.
Other panel families were host-tested, not physically flashed or visually
qualified in this step. Physical readability remains a user check.
