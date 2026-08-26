# SenseCAP Indicator SD font

`generate_font.py` creates the native-resolution font used by the SenseCAP
Indicator. Text is Noto Sans Mono Bold rendered into an 18x24 monospaced cell.
Every text pixel is binary (`0` or `255`), so the font remains sharp and has no
antialiasing.

The complete Unicode Emoji 17.0 RGI set is rendered from Noto Color Emoji into
12x12 RGB332 cells. Transparency is binary, and each cell is enlarged 2x with
nearest-neighbor scaling for a crisp 24x24 result on the 480x480 panel. Emoji
sequences are mapped into the BMP private-use range by a trie appended to the
font file because the display library's runtime font interface accepts 16-bit
glyph identifiers. The private-use glyph is intercepted at draw time and the
corresponding color cell is overlaid after the UI canvas is copied.
On later UI refreshes, unchanged emoji pixels are excluded from the indexed
canvas transfer. This preserves the already-presented color cell and avoids a
visible fallback-to-color flash.

The generated `ui-font.vlw` is stored on the Indicator's SD card. The RP2040
font service streams it to the ESP32-S3 at boot; the application falls back to
its built-in font if the card, service, file, checksum, or mapping is invalid.

To reproduce the checked-in asset:

```bash
python3 -m venv /tmp/meshcore-font-venv
/tmp/meshcore-font-venv/bin/pip install -r tools/sensecap_indicator_font/requirements.txt
/tmp/meshcore-font-venv/bin/python tools/sensecap_indicator_font/generate_font.py \
  --output variants/sensecap_indicator-espnow/sd/ui-font.vlw \
  --preview /tmp/meshcore-indicator-font-preview.png
```

The source fonts are licensed under the SIL Open Font License 1.1; see
`OFL-1.1.txt`. The Unicode data source, pinned color-font revision, and exact
input hashes are recorded in the generated JSON manifest.
