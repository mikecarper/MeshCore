# SenseCAP Indicator SD font service

The Indicator connects its SD card to the RP2040 rather than the ESP32-S3.
This small RP2040 image owns the card and streams `/meshcore/ui-font.vlw` to
the main application over the internal UART at boot. The ESP32-S3 verifies the
whole-file CRC and falls back to its built-in font if the service, card, file,
or emoji map is unavailable or invalid.

Build and flash the service through the RP2040 USB connector:

```bash
pio run -d tools/sensecap_indicator_rp2040
pio run -d tools/sensecap_indicator_rp2040 -t upload --upload-port /dev/ttyACM2
```

Then upload the generated font to the SD card through that same USB serial
port. The update is checksummed and uses temporary and backup files so an
interrupted upload keeps the last complete font:

```bash
python3 -m pip install pyserial
python3 tools/sensecap_indicator_rp2040/upload_font.py --port /dev/ttyACM2
```

The checked-in asset contains binary 18x24 Noto Sans Mono Bold text and a
12x12 RGB332 Unicode Emoji 17.0 color atlas. Text and emoji transparency have
no antialiasing; the ESP32-S3 renders the asset at the panel's native 480x480
resolution. See
`../sensecap_indicator_font/README.md` to regenerate the asset.

For diagnostics, send `MCFONT STATUS` over RP2040 USB serial. The response
reports internal-UART INFO requests, GET attempts, completed streams, and
bytes sent since the RP2040 last booted.
