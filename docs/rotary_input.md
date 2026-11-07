<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/rotary_input/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Rotary controls and external notification outputs

Full Companion on ESP32, nRF52 and RP2040 supports a user-wired incremental
rotary encoder, an optional push switch, a passive buzzer, and separate
notification, GPS and Bluetooth LEDs. All assignments start disabled. Settings
are saved separately from the node preferences and take effect without reboot.
The encoder needs a supported display UI; outputs also work without a display.
Existing board-provided encoders remain the default until a custom encoder is
enabled.

## Connect and configure an encoder

Use 3.3 V signals and a common ground. Connect the encoder's common contact to
ground, A/CLK and B/DT to two available MCU pins, and the optional normally-open
push switch between its selected pin and ground. Powered encoder modules must
also use 3.3 V. Internal pull-ups are enabled by default.

First inspect the running image:

```text
get rotary
get rotary.pins.available
```

The list shows rotation-input pins for the current pull-up setting, excluding
pins held by other functions. A board's existing user-button pin can
additionally be selected as the push switch, even when absent from that list.
Every wire must use a different physical pad.

| Command | Effect |
| --- | --- |
| `set rotary.pins <CLK>,<DT>[,<SW>]` | Select Arduino pin numbers; omit SW or use `-1` for no switch |
| `set rotary on` / `set rotary off` | Enable the selected encoder, or release its pins |
| `set rotary.steps <1..255>` | Electrical transitions per detent; default `4` |
| `set rotary.reverse on` / `off` | Reverse the turn direction |
| `set rotary.pullup on` / `off` | Select internal pull-ups or external pull resistors |
| `get rotary.pins`, `.steps`, `.reverse`, `.pullup` | Read an individual setting |

Select the pins before enabling. Turning the encoder moves through the new
menu UI; pressing selects, and holding uses the existing long-press action.
A sleeping screen wakes before a switch action is performed. The original
status UI retains its existing page/button actions. If a detent moves twice or needs
two detents, adjust `rotary.steps`; if direction is reversed, change
`rotary.reverse`. Test the actual encoder at your intended turning speed:
a polling driver cannot reconstruct electrical transitions missed between polls.

With power saving enabled, fast rotation polling runs while the screen is
on. Rotation is ignored while the screen is off; the optional push switch
still wakes it. With power saving disabled, rotation continues to be sampled
with the screen off. During fast polling, ESP32 skips manual CPU sleep and
uses a short cooperative yield; WiFi/Bluetooth modem power saving remains
available. nRF52/RP2040 use GPIO-change wakeups and stay awake for switch
debounce/hold deadlines. `set rotary off` releases the pins and restores
normal manual sleep behavior.

On classic ESP32, GPIO34 through GPIO39 cannot supply internal pull-ups and
cannot drive outputs. Set `rotary.pullup off` before selecting a permitted
input-only pin, and fit external pull resistors on every encoder wire.

### Heltec V4.2/V4.3 with the unified OLED/TFT image

The standard native-USB image permits this encoder assignment, subject to
`get rotary.pins.available` on the actual installed image:

```text
set rotary.pins 6,43,0
set rotary.steps 4
set rotary on
```

This assignment is checked against the firmware pin policy; test physical
encoder operation on your hardware before relying on it.

GPIO0 reuses the existing user button. Release the switch during reset to
avoid entering the board's download mode. GPIO43 is available here because
the console uses native USB CDC; it remains reserved in a UART-console image
or when an explicit serial/bridge assignment claims it.

Fit a 1 kohm series resistor between GPIO43 and the encoder contact. The
ESP32-S3 ROM can drive UART0 TX on this pad before firmware starts, even
when the application console uses USB. The resistor limits current while
that contact is grounded. See Espressif's
[ESP32-S3 startup wiring](https://docs.espressif.com/projects/esp-techpedia/en/latest/esp-friends/get-started/try-firmware/try-firmware-hardware/esp32s3.html).

The unified image protects the union of OLED, TFT and touch-controller pins,
even if startup detects only an OLED or no display. GPIO44, GPIO47 and GPIO48
remain reserved for touch reset/I2C, and GPIO40/41 remain reserved for GNSS
wake/PPS. Radio, GNSS, battery, memory, debug and USB nets also stay protected.
After GPIO6/43 are allocated to this encoder, the standard unified image has
no spare permitted GPIO for the four additional outputs. It cannot safely
host the encoder and all the lights/buzzer together with that hardware mapping.
Do not substitute display or GNSS pins because an accessory was not detected.

## Passive buzzer and status LEDs

Use current-limiting resistors for LEDs, and an appropriate driver when a
buzzer or other load exceeds the GPIO's current rating. LED outputs are
active high. These commands accept Arduino MCU pins, not I2C-expander numbers:

```text
get peripherals
get peripheral.pins.available
get peripheral.buzzer
set peripheral.buzzer <pin|off>
set peripheral.led.notify <pin|off>
set peripheral.led.gps <pin|off>
set peripheral.led.ble <pin|off>
```

Each `set` has a matching individual `get`. Inspect `peripheral.pins.available`
and choose unused output-capable pins for the exact board. The notification
LED indicates message arrivals and follows programmable alert patterns. The GPS LED indicates active GPS use.
The Bluetooth LED stays on while the transport is enabled, including
advertising; it does not distinguish a connected phone.

The selected passive buzzer replaces the built-in RTTTL output and follows
the existing sound/mute settings. Configure melodies and LED patterns through
the [Companion notification builder](notifications.md). For example,
`set notify.sound channel:3 order:d=8,o=5,b=180:c,e,g,4c6` selects that channel's
ringtone after the sound output is enabled. Assigning a buzzer does not unmute
notifications or enable a disabled alert rule.

## Pin protection and board coverage

Assignments are checked before pins are initialized. Radio/front-end,
flash/PSRAM, USB, debug, strapping, display/touch, active buses, GPS, battery,
charger and power-control pins are excluded. The push switch has a narrow
exception for the existing user button. In-use pins are locked across rotary
inputs, custom outputs, programmable notification GPIO and ordinary user
GPIO/timers. A low output remains owned until reset or reassignment.
nRF52/STM32 Arduino aliases of one pad share one claim.

The board policy covers ESP32, S2, S3, C3 and C6. nRF52 expansion maps cover
Heltec T096, ProMicro, RAK3401/4631 and XIAO/IKOKA boards. RP2040 maps cover
Pico/Pico W, XIAO and RAK11310. STM32 LoRa-E5 Mini has a header map for explicit
custom builds. Other boards require an audited `USER_GPIO_ALLOWED_PINS` list
of exposed Arduino pins in the target or build flags. That allowlist never
overrides hardware reservations. Use Arduino pin indices on nRF52/STM32,
rather than raw MCU port numbers.

Station G2 is limited to its documented IO extension socket. Cardputer-Adv,
T-Deck, SenseCAP Indicator and Wio Tracker L2 keep their internally wired display,
keyboard/audio and control nets protected and require an audited expansion
allowlist before accepting custom pins.

The MCU cannot detect extra wiring omitted from its board definition. Confirm
the physical header and attached modules before using an allowed pin. Invalid
saved records or pins that become reserved after an update are left inactive.
A failed or unconfirmed save returns an error.

## Custom builds

Full ESP32/nRF52/RP2040 images enable `MESH_CUSTOM_PERIPHERALS=1` automatically.
Legacy profiles and STM32 leave it off by default. Set the flag explicitly to
opt in, or set `UI_ROTARY_INPUT_GPIO=1` with the pin definitions below.
`MESH_CUSTOM_PERIPHERALS=0` disables the optional feature. Compact STM32 builds
still need enough flash and a supported display UI for rotary navigation.

A direct PlatformIO environment in `platformio.local.ini` can opt the legacy
V4 Bluetooth Companion into custom peripherals with the checked native-USB
pin mapping. This example retains that profile's Bluetooth transport; the
release builder selects the separate Full transport/profile configuration.

```ini
[env:heltec_v4_rotary]
extends = env:heltec_v4_companion_radio_ble_femon
build_flags =
  ${env:heltec_v4_companion_radio_ble_femon.build_flags}
  -D MESH_CUSTOM_PERIPHERALS=1
  -D UI_ROTARY_INPUT_GPIO=1
  -D PIN_ROTARY_CLK=6
  -D PIN_ROTARY_DT=43
  -D PIN_ROTARY_BUTTON=0
  -D ROTARY_TRANSITIONS_PER_DETENT=4
```

Build with `pio run -e heltec_v4_rotary`. Optional `ROTARY_REVERSE=1` and
`ROTARY_PULLUP=0` change the defaults. An existing saved record takes
precedence, including a saved disabled state. Every build default passes the
same board pin checks as a CLI assignment.
