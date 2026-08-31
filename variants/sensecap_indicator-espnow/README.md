# SenseCAP Indicator controls

The primary mesh radio is selected by the firmware image, not by the WiFi/BLE
screen. Use `SenseCapIndicator-LoRa_companion_radio_full` for a LoRa-equipped
D1L or D1Pro. Build a fresh image explicitly with:

```sh
bash build.sh build-firmware SenseCapIndicator-LoRa_companion_radio_full \
  --radio-preset usa-cascadia --profile cascade --clean
```

The build uses the live USA/Canada preset when the settings service is
available; its offline USA Cascadia fallback is 910.525 MHz, BW 62.5, SF7,
CR5. The Cascade profile also supplies its fresh-install defaults, including
RX delay 2. `SenseCapIndicator-ESPNow_companion_radio_full` remains the image
for an Indicator without the LoRa assembly. An application-only update
preserves the previously saved numeric radio tuple; erase/install the LoRa
merged image when the compiled fresh defaults must replace it.

The Indicator preserves the existing 160x160 UI coordinate system. Ordinary
images use a 320x320, 4-bit internal canvas scaled 1.5x onto the 480x480 panel.
Full Companion images first request a native 480x480 canvas, then normally
shrink to 320x320 only for the tight ESP-NOW + BLE combination before Bluetooth
starts. The canvas stays out of PSRAM to avoid contention with RGB scanout. The
screen blanks after five minutes on USB power; the hardware button or a touch
wakes it without selecting anything.

## Touch navigation

- Swipe horizontally to move between home pages. The dots under the status bar
  show the current page.
- The middle 70% of the panel is the Select tap target. The outer 15% on each
  side performs Previous or Next; horizontal swipes remain available anywhere.
- A gesture is committed after a stable release. Brief missing touch samples
  are ignored so one swipe cannot become a second, opposite endpoint tap.
- The SenseCAP controller reports a mirrored physical X coordinate. The board
  profile corrects stationary visual left/right taps independently from its
  existing swipe-direction correction.
- The transport page is the exception for a very quick stationary contact:
  either tall, outlined WiFi/BLE box can accept one sampled point after the
  same stable-release debounce. Taps in the title, center gap, or footer stay
  inert, and a moving contact is still handled as a swipe first.

## Reading messages

The message preview is a non-destructive local inbox, newest first:

- A seventh home page, immediately after the status page, shows the newest
  retained message for each heard channel. Each two-line row contains the
  channel name, rollover-safe relative age, and a one-line ellipsized message
  preview. Direct messages are grouped by contact and labelled `Direct`.
- The summary retains up to 32 received messages in RAM even after a connected
  Companion drains its unread queue. The oldest history is overwritten and a
  reboot starts with an empty list; messages are not written to flash.
- Tap the center of the message-summary page to open the existing full message
  view. Horizontal swipes continue to move among the seven home pages.
- The first home page shows the retained local `INBOX` count, which remains
  useful when a USB host has already drained the protocol's unread count. Tap
  the center to open the inbox and channel selector even when the count is 0.
- `Message 1/N` is the newest buffered preview.
- Each preview retains the complete 160-byte maximum chat payload. Long UTF-8
  messages wrap through the available middle of the screen instead of being
  cut at the small-display 78-byte limit.
- When a USB host drains the radio queue, the detailed message screen remains
  visible for five minutes before returning home; its recent-message history
  remains available from the summary page and inbox.
- Swipe left for an older preview and right for a newer preview. Navigation
  stops at the oldest and newest entries instead of wrapping.
- The bottom bar shows the active inbox filter. Its large `<` and `>` end
  buttons select the previous or next filter; swipe up or down does the same.
  Filters include `All channels`, `Direct`, and configured channels such as
  `Ch 1 #testing`. A newly received message automatically selects its source
  channel. On the 480x480 Indicator, the full inbox's top status strip and
  bottom selector use a compact 24-physical-pixel font; their visual heights
  are 27 and 42 pixels while the forgiving arrow touch regions remain 120x120.
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

### Full Companion transport selection

Both Full Indicator layouts run exactly one secondary wireless Companion
transport per boot. Select infrastructure WiFi or BLE from the Indicator UI,
or use the USB/TCP text terminal:

```text
get companion.transport
set companion.transport wifi
set companion.transport ble
```

The getter replies `wifi` or `ble`. A setter saves the next-boot choice and
replies that a reboot is required; it does not restart either radio in the
current session. WiFi mode never initializes BLE and releases the ESP32
Bluetooth controller and host memory. BLE mode does not start infrastructure
WiFi, WebConfig, MQTT, OTA networking, or TCP Companion services. USB remains
available in both modes for recovery.

The Indicator transport page presents WiFi and BLE as full-height side-by-side
choices. Tapping either choice saves it and reboots only when it differs from
the active mode. WiFi is split over two size-4 rows, BLE uses size 4, and the
short `ON`/`NEXT` state uses size 3; all render at the same physical size in
the 320 and 480 profiles. On a retained native 480 canvas, the first home page
uses separate full-width size-4 `INBOX` and count rows plus a large lower
action or BLE-status block. Long IP addresses retain a smaller bounded row
instead of clipping.

LoRa remains the primary radio in both LoRa modes. On the ESP-NOW layout,
selecting BLE leaves the primary ESP-NOW WiFi radio and its fixed channel
running; it disables only infrastructure WiFi. Rendering follows the memory
cost of each combination:

- LoRa + WiFi: targets a native 480x480 canvas.
- LoRa + BLE: targets a native 480x480 canvas.
- ESP-NOW + infrastructure WiFi: targets a native 480x480 canvas.
- ESP-NOW + BLE: 320x320 canvas scaled 1.5x to the panel.

If the early native allocation or a later profile retry cannot obtain a
contiguous DMA-capable block, any mode requesting 480x480 keeps or restores the
320x320 emergency canvas instead of disabling the display. The startup log
reports the canvas actually retained; an emergency fallback does not change the
saved Companion transport or stop the primary LoRa/ESP-NOW radio.

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

Both Full Companion layouts, `SenseCapIndicator-LoRa_companion_radio_full` and
`SenseCapIndicator-ESPNow_companion_radio_full`, can repair a missing, corrupt,
or older font after station WiFi connects. The LoRa USB/WiFi base profile has
the same recovery support; USB-only images omit it. Startup never waits for the
network: the UI uses its built-in font while a bounded background task fetches
the 1,302,608-byte asset from GitHub's official, content-addressed Git Blob REST
API:

`https://api.github.com/repos/mikecarper/MeshCore/git/blobs/45dfe8acac20974f53648ef71a31efefa1333fea`

Before each bounded download attempt, the client must receive a fresh SNTP
response in that attempt (waiting at most 15 seconds) before it opens TLS or
downloads any bytes; a plausible retained clock is not enough. The initial
request and every validated Range reconnect recheck that the proof is still
younger than five minutes, WiFi is connected, and the signed wall clock is
valid immediately before their TLS handshakes. The request uses GitHub's
documented raw media type and then requires CA-verified TLS (never an insecure
client), an exact `Content-Length`, and SHA-256
`61bce9662db314054e7bcfaa26147a28ad7b500b51baac4cae1caacce90b7421`
before it tells the RP2040 to publish the staged file. The RP2040 independently
checks the 1,302,608-byte size and CRC32 `0x19f80d64`, and uses separate stage,
temporary, backup, and live paths. An interrupted download or reset therefore
cannot replace the last valid font. A missing/corrupt font activates live after
recovery; a valid older font stays active until the next boot to avoid holding
two large runtime font buffers in PSRAM.

The ESP32-S3 and RP2040 do not always finish reset together. If the startup
`MCFONT INFO` exchange is unavailable rather than explicitly missing, the WiFi
image waits for station WiFi and re-probes the font service in the same
background worker. A current font found by that probe is installed without a
network download. A confirmed missing, corrupt, or older font enters normal
recovery; four still-unavailable probes (immediate, then after 2, 5, and 15
seconds) stop until the next boot.

Recovery retries at most four times per boot (immediately, then after 30
seconds, 2 minutes, and 10 minutes). A later reboot starts a fresh bounded set.
The recovery client uses a per-client PEM trust anchor for Sectigo Public
Server Authentication Root E46, so it does not replace the process-global CA
bundle. That root expires on 2046-03-21; future firmware must still review and
retest GitHub's certificate chain because an origin can change chains before a
root expires. GitHub permits 60 unauthenticated REST requests per hour per
source IP. Devices behind one public IP share that allowance, so the network
request runs only after a missing, corrupt, or older font is confirmed; a
current font performs no GitHub request.

After `MCFONT COMMIT` is sent, a transient INFO/GET failure does not authorize
another download of the same immutable blob. A missing or malformed COMMIT
reply is also treated as ambiguous rather than failed, because the RP2040 may
already have completed its durable rename; only an explicit `ERROR ...` reply
can return to the network retry path. The ESP32 instead performs at most four
local-only probes (immediate, then after 2, 5, and 15 seconds). If fallback text
is active, that probe streams and verifies the exact size, CRC32, and SHA-256
before live activation. If a valid older font remains active until reboot, an
exact INFO size/CRC32 check relies on COMMIT's full stored-file CRC pass and
avoids allocating a second 1.3 MiB runtime font.
If the display's runtime parser nevertheless rejects a buffer that already
passed the exact size, CRC32, and SHA-256 checks, recovery stops for that boot
and keeps the built-in fallback; downloading the same immutable bytes again
cannot repair a parser or memory-state failure.

Automatic recovery requires the matching RP2040 font-service image with the
two-phase `MCFONT STAGE` / `MCFONT COMMIT` protocol. An older RP2040 service
safely rejects the commands, leaves the existing font untouched, and can still
be updated over its own USB connector as described below.
