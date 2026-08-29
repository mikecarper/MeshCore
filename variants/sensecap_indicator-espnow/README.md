# SenseCAP Indicator controls

The Indicator preserves the existing 160x160 UI coordinate system in a
320x320, 4-bit internal canvas and scales it 1.5x onto the 480x480 panel. The
canvas avoids PSRAM contention with RGB scanout while reserving enough internal
RAM for Bluetooth and WiFi to operate together. The screen blanks after five
minutes on USB power; the hardware button or a touch wakes it without selecting
anything.

## Touch navigation

- Swipe horizontally to move between home pages. The dots under the status bar
  show the current page.
- The middle 70% of the panel is the Select tap target. The outer 15% on each
  side performs Previous or Next; horizontal swipes remain available anywhere.
- A gesture is committed after a stable release. Brief missing touch samples
  are ignored so one swipe cannot become a second, opposite endpoint tap.

## Reading messages

The message preview is a non-destructive local inbox, newest first:

- The first home page shows the retained local `INBOX` count, which remains
  useful when a USB host has already drained the protocol's unread count. Tap
  the center to open the inbox and channel selector even when the count is 0.
- `Message 1/N` is the newest buffered preview.
- Each preview retains the complete 160-byte maximum chat payload. Long UTF-8
  messages wrap through the available middle of the screen instead of being
  cut at the small-display 78-byte limit.
- When a USB host drains the radio queue, the displayed preview is retained
  for five minutes rather than disappearing immediately.
- Swipe left for an older preview and right for a newer preview. Navigation
  stops at the oldest and newest entries instead of wrapping.
- The bottom bar shows the active inbox filter. Its large `<` and `>` end
  buttons select the previous or next filter; swipe up or down does the same.
  Filters include `All channels`, `Direct`, and configured channels such as
  `Ch 1 #testing`. A newly received message automatically selects its source
  channel.
- Tap the center to return home without deleting the previews.
- On the first home page, tap the wide center target to reopen the previews,
  including when `INBOX` is zero.

Channel messages identify both the configured slot and name, such as
`Ch 0 Public` or `Ch 1 #testing`. The value in brackets after it is the route,
for example `[0h]` for a zero-hop flood or `[direct]` for a direct route.

The bottom selector filters received messages; it does not change the outbound
channel because the device UI does not include a text composer. Select the
outbound channel in the connected application, or list and address channel
slots from the CLI with `get_channels`, `public <message>`, or `chan <slot>
<message>`. Channel 0 is conventionally Public; always use `get_channels`
before assuming the other slot numbers.

## Radio page

The radio page redraws once per second. Noise-floor calibration publishes a
new 64-sample average about every two seconds, so a faster redraw would usually
repeat the same value. The display shows the fractional sample mean to one
decimal place; individual SX1262 instantaneous RSSI samples are still
quantized to 0.5 dB, so the decimals are useful for comparing averages rather
than claiming hundredth-dB measurement accuracy.

## USB + WiFi Companion

Build `SenseCapIndicator-LoRa_comp_radio_usb_wifi` to retain the USB Companion
link and expose the same framed Companion protocol over WiFi TCP port `5000`.
Fresh devices advertise an open `MeshCore-Setup-XXXX` access point, where the
four-character suffix identifies the device; join it and open
`http://192.168.4.1/` to save WiFi credentials. Saved credentials and the WiFi
on/off preference survive application-only reflashes. Setup mode explicitly
restores standard 2.4 GHz b/g/n operation, so a previously installed ESP-NOW
image cannot leave the setup AP hidden behind its long-range protocol setting.

The combined profile uses minimum WiFi modem power saving by default. USB
remains available for recovery and management if the configured network is
unavailable. TCP port `5001` serves host-backed LoRa OTA files, while port
`5002` provides the bounded OTA management console. The USB text terminal also
accepts `get wifi.status`, `get wifi.ssid`, `get wifi.cli`, and
`start webconfig ap`; enter it with `+++MESHCORE-TERM-START` and return to the
framed Companion protocol with `+++MESHCORE-TERM-STOP`.

If a Companion client does not supply device time over USB or WiFi, this build
starts evaluating LoRa time after two minutes. It requires three independent,
agreeing sources from signed adverts or authenticated Public-channel messages;
one repeated sender cannot set the clock alone. A successful Companion or
local CLI time update always wins and disables the LoRa fallback until reboot.

The combined image uses two 2.5 MiB OTA application slots. Its partition map
keeps the prior NVS and SPIFFS addresses, so installing the partition table and
application over an existing Indicator preserves identity, channels, radio
settings, and saved WiFi credentials.

## SD-backed font

The RP2040 streams the checked-in pixel-font asset from the SD card to the
ESP32-S3 at boot. Text is binary 18x24 Noto Sans Mono Bold. Emoji use 12x12
RGB332 color cells with binary transparency and are enlarged 2x with
nearest-neighbor scaling. Periodic status updates preserve unchanged emoji in
the panel framebuffer so the full-color cell is not replaced and redrawn once
per second. See
[`../../tools/sensecap_indicator_rp2040/README.md`](../../tools/sensecap_indicator_rp2040/README.md)
for installation and diagnostics.
