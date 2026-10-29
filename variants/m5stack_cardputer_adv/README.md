# M5Stack Cardputer-Adv + Cap LoRa-1262

Variant for the [M5Stack Cardputer-Adv](https://docs.m5stack.com/en/core/Cardputer-Adv)
(ESP32-S3, 8 MB flash) with the
[Cap LoRa-1262](https://docs.m5stack.com/en/cap/Cap_LoRa-1262) (SX1262 + ATGM336H GNSS).

| Environment | Role |
|---|---|
| `M5Stack_Cardputer_Adv_companion_radio_ble` | Companion with keyboard/display UI, BLE and USB terminal |
| `M5Stack_Cardputer_Adv_companion_radio_usb` | Same UI, USB only (no BLE) |
| `M5Stack_Cardputer_Adv_repeater` | Headless repeater |
| `M5Stack_Cardputer_Adv_room_server` | Headless room server |

The companion UI is adapted from
[MultiMote/meshcore-cardputer-adv](https://github.com/MultiMote/meshcore-cardputer-adv)
(MIT, see `LICENSE.MultiMote`) and runs on top of the standard companion, so
the USB terminal CLI (`help`, `set ...`, `powersaving ...`) stays available.

## Hardware notes

- LoRa: NSS 5, SCK 40, MISO 39, MOSI 14, RST 3, BUSY 6, DIO1 4. DIO2 drives the
  RF switch; TCXO 1.8 V.
- The Cap's antenna path is enabled through P0 of a PI4IOE5V6408 at 0x43 on the
  internal I2C bus (G8/G9, `Wire1`), shared with the TCA8418 keyboard and ES8311
  codec. `Wire` is the Grove port (G2/G1).
- The microSD slot (CS 12) shares the LoRa SPI bus, which runs on FSPI because
  the display owns SPI3.
- Battery on G10 (x2 divider). GNSS on UART 15/13 at 115200.
- Top button (G0) sends a flood advert.

## Storage

Contacts, channels, identity and settings live in the internal `spiffs`
partition (`default_8MB.csv`). The microSD card is optional and holds chat
history and keyboard layouts.

Companion builds retain all 350 contacts and use the existing bounded contact
path cache to leave RAM available for Bluetooth, the display and keyboard layouts.

Accepted incoming messages and successful sends from the keyboard, phone app
or USB terminal share the same local history. Retries do not add another copy.
Only the conversation actually visible on the screen is marked read.

Channel history identifies each channel by its full name (including protocol
prefix) and full key, so channels sharing a key keep separate conversations.
New channel logs use `/meshcore_custom/history/chan2` on the SD card. Older
`/meshcore_custom/history/chan` logs remain on the card for manual retrieval;
they are not imported because their channel identity can be ambiguous. Direct
message logs keep their existing paths.

When the device is managed by bmorcelli's Launcher, install updates by writing
the app image to the MeshCore app slot over USB rather than reinstalling from
the SD card: Launcher can repartition the flash to fit the image and drop the
`spiffs` partition, which wipes the node's identity.

## Keyboard

- `del` deletes, `esc` (top-left key) goes back, `Enter` selects or sends.
- Arrows are `;` `.` `,` `/`. While typing a chat message, prefix them with `Fn`.
- `Aa` (Shift) is sticky: press once for caps, again to release.
- `Fn` is one-shot: press `Fn`, then a key; it releases after that key.
  A `CAPS`/`FN` badge shows while either is active.

| Fn + | Action |
|---|---|
| A | Flood advert (same as the top button) |
| G | GPS on/off |
| B | Bluetooth on/off (same checks as `set bluetooth`) |
| M | Sound on/off |
| P | Power save on/off (same setting as `powersaving`) |
| L | Screen brightness |
| D | Discover repeaters |
| H / C / N / I | Home / Contacts / Channels / Info |
| S / T | Settings / Tools |
| K | Switch keyboard layout (layouts loaded from SD) |

## Display

Settings -> Text size cycles Normal (12 px), Large (16 px, default) and
Extra large (19 px). The larger fonts are generated from DejaVu Sans with
`tools/gen_vlw_font.py`.

Radio settings use the same live apply and save path as the USB terminal;
a reboot is not required. A busy radio applies the saved setting once idle.
Rejected settings or storage failures show an error
and retain the previous saved value. TX power supports the radio's signed range,
including negative values. Display brightness, text size and battery
correction are saved separately from the standard node preferences.

Screen wake and timeout follow the shared `display.mode` and `display.timeout`
settings for battery and USB power. Fresh installations use `automatic` mode
with a 15-second timeout; saved modes and timeouts are retained.
A key press wakes the screen for the selected timeout; the wake-up
press is consumed. The `automatic` mode also permits incoming messages to wake
the screen while no client is connected. Notification screen overrides and
muted notification outputs are respected. `set display.night auto|on|off` uses
the shared red-on-black palette without changing the saved daytime colors.
Automatic night mode uses GPS or recent direct-neighbor locations, starting
one hour after sunset by default. `set display.night.delay <minutes>` changes
that delay.

Bluetooth pairing requests show the PIN for up to two minutes when the selected
screen mode permits it. Esc dismisses the prompt; the current chat and draft
remain selected. An explicit screen-off setting keeps the screen dark.

The speaker plays fixed UI tones without blocking the mesh loop and honors
the saved notification outputs and mute settings. A key or top-button press
dismisses notifications configured to stop on button input. Custom RTTTL
notification melodies are unsupported on this board.

Tools -> Restart and Power off flush pending contact writes before shutdown.
Power off waits for Enter release without blocking radio processing; holding
the key for two seconds or pressing Esc cancels it.

## Third-party notices

The keyboard/display UI is adapted from MultiMote's MIT-licensed project;
retain [LICENSE.MultiMote](LICENSE.MultiMote) with redistributed source and
firmware. The embedded DejaVu Sans fonts have separate Bitstream/Arev terms
in [LICENSE.DejaVu](LICENSE.DejaVu), including the original font attribution.
Retain that notice with the generated font headers and firmware containing
them. The font generator requires separately installed Pillow and fontTools;
neither dependency is bundled here.

## Power

- Power save uses the companion's own light-sleep loop. It only sleeps while the
  screen is off and no USB or BLE client is connected.
- With GPS and power save enabled, the receiver sleeps after clock acquisition
  or at the end of its 10-minute acquisition window, then wakes after 30 minutes.
  Location telemetry can temporarily hold it awake under the common GPS policy.
  Turning GPS off cancels current acquisitions; saved GPS intervals apply.
- Repeater and room-server builds also put the Cap's fixed GNSS receiver into
  standby when unused, while retaining external sensor support. GPS-free builds
  send the standby command without adding a GPS parser or GPS settings.
- Below 3.4 V the UI shows "Battery low" and powers off (no LoRa wake).

## Building on Windows

`win_long_cmdline.py` shortens the compiler include list with `-iprefix`, since
these environments otherwise exceed Windows' 32,767-character command limit.
It does nothing on other platforms.
