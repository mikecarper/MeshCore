<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/rotary_input/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Rotary controls and external notification outputs

This repository's Full Companion on ESP32, nRF52 and RP2040 supports a
user-wired incremental rotary encoder, an optional push switch, a passive buzzer, and separate
notification, GPS and Bluetooth LEDs. All assignments start disabled. Settings
are saved separately from the node preferences and take effect without reboot.
The encoder needs a supported display UI; outputs also work without a display.
Existing board-provided encoders remain the default until a custom encoder is
enabled.

## Connect and configure an encoder

Disconnect USB, the battery and any solar/external supply before soldering.
Use an incremental quadrature encoder, such as an EC11 with A, B and common
contacts plus a separate normally-open push switch. A three-terminal analog
potentiometer is not a substitute. Identify the actual terminals from the
component documentation or a continuity test; shaft shape and photographs do
not establish its pinout or whether a switch is fitted.

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
pins held by other functions. A board's existing active-low user-button pin can
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
get rotary
set rotary.pullup on
get rotary.pins.available
set rotary.pins 6,43,0
set rotary.steps 4
set rotary on
get rotary
```

This assignment is checked against the firmware pin policy; test physical
encoder operation on your hardware before relying on it.

| Encoder terminal | Connection on the native-USB Full Companion |
| --- | --- |
| A / CLK | GPIO6 |
| B / DT | GPIO43 through a 1 kohm series resistor |
| Common / C | GND |
| One push-switch contact | GPIO0 |
| Other push-switch contact | GND |

These are GPIO numbers, not positions in the header. A bare mechanical encoder
needs no 5 V supply. Use the two actual switch contacts, not mounting tabs.

GPIO0 reuses the existing user button. Release the switch during reset to
avoid entering the board's download mode. GPIO43 is available here because
the console uses native USB CDC; it remains reserved in a UART-console image
or when an explicit serial/bridge assignment claims it.

Fit a 1 kohm series resistor between GPIO43 and the encoder contact. The
ESP32-S3 ROM can drive UART0 TX on this pad before firmware starts, even
when the application console uses USB. The resistor limits current while
that contact is grounded. See Espressif's
[ESP32-S3 startup wiring](https://docs.espressif.com/projects/esp-techpedia/en/latest/esp-friends/get-started/try-firmware/try-firmware-hardware/esp32s3.html).

In the default Auto mode, the unified image protects the union of OLED, TFT and touch-controller pins,
even if startup detects only an OLED or no display. GPIO44, GPIO47 and GPIO48
remain reserved for touch reset/I2C, and GPIO40/41 remain reserved for GNSS
wake/PPS. Radio, GNSS, battery, memory, debug and USB nets also stay protected.
After GPIO6/43 are allocated to this encoder, Auto mode has
no spare permitted native GPIO for the four additional outputs. The
[ESP32 I2C expander arrangement](i2c_expanders.md) moves the encoder and
LEDs to an external chip and frees GPIO6 for the passive buzzer.
Alternatively, explicitly select [OLED expansion mode](#oled-expansion-mode-in-the-same-unified-firmware)
and reboot to use four native output pins. Do not substitute display or GNSS
pins merely because an accessory was not detected.

In particular, the following proposed LED/buzzer assignments are **not valid**
in Auto mode:

| Proposed connection | Why the pin is unavailable |
| --- | --- |
| Notification LED on GPIO35 | The board's built-in LED already uses this pin; firmware reserves it. |
| GPS LED on GPIO34 | This controls the GNSS power supply. It is not an external status-LED output. |
| Bluetooth LED on GPIO36 | This controls the Vext power rail. Toggling it would change peripheral power. |
| Passive buzzer on GPIO33 | Auto mode reserves it for TFT display data, including when only an OLED is detected. Explicit OLED mode plus reboot can release it. |

The GPIO34/35/36 board connections are documented in Heltec's
[V4 pin map](https://heltec.org/project/wifi-lora-32-v4/#DocsResource) and
[V4.3 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf).
This mapping is for the ESP32-S3R2 board, not the V4 R8 variant.

Check `get peripheral.pins.available` after assigning the encoder. An empty
GPIO list means there are no permitted output pins left. The commands below
do not override reservations. For an encoder plus three independent LEDs and
a buzzer, use hardware with enough available pins or an expansion design with
matching firmware support. ESP32 Full Companion can use
[explicitly named expander pins](i2c_expanders.md) for rotary inputs and LEDs;
a passive buzzer still needs a native waveform output/driver.

### OLED expansion mode in the same unified firmware

For a V4/V4.3 **ESP32-S3R2 with only an OLED**, the unified Full Companion firmware can
release GPIO33, GPIO44, GPIO47 and GPIO48 for accessories. This is an
explicit boot profile: saving `display.type oled` and rebooting releases
the pins. Auto-detection of an OLED does not. Do not attach the TFT/touch expansion kit in this mode.
It does not apply to V4 R8 boards.

With the new firmware installed, enter separately:

```text
set display.type oled
reboot
```

Reconnect and check `get display.type`: it should report `oled` without `reboot required`.
Disconnect power before adding wires.

| Component | Connection |
| --- | --- |
| Encoder CLK | GPIO6 |
| Encoder DT | GPIO43 through 1 kohm |
| Encoder switch | GPIO0 |
| Encoder common and switch ground | GND |
| Passive buzzer signal | GPIO33, with a suitable driver if required |
| Notification LED positive | GPIO44 through its current-limiting resistor |
| GPS LED positive | GPIO47 through its current-limiting resistor |
| Bluetooth LED positive | GPIO48 through its current-limiting resistor |
| Buzzer and LED negatives | GND |

After reconnecting:

```text
set rotary.pins 6,43,0
set rotary.steps 4
set rotary on
set peripheral.buzzer 33
set peripheral.led.notify 44
set peripheral.led.gps 47
set peripheral.led.ble 48
```

The usual sound/mute and alert rules still apply. GPIO38/39 and GPIO40/41
remain reserved for GNSS. Native USB must be enabled to use GPIO43/44 this
way. Release the encoder button during reset. Prewired LEDs with verified
suitable built-in resistors do not need another series resistor.

Clear assignments and remove accessory wiring from these four pins before
using the TFT/touch kit. Save `set display.type auto` and reboot to restore
the default TFT/OLED pin protections. Profile changes never release pins
immediately while the old display is running.

## Passive buzzer and status LEDs

Use current-limiting resistors for LEDs, and an appropriate driver when a
buzzer or other load exceeds the GPIO's current rating. Native LED outputs
default to active high; configured expander LEDs can select either polarity.

### Check the electrical ratings before choosing outputs

- **Pre-wired LEDs:** verify that each assembly actually contains a series
  resistor and check its value/current specification. A listing that says
  "5 V" or "3-5 V" does not establish the resistor value or guarantee useful
  brightness from a 3.3 V GPIO. Use an assembly specified for 3.3 V, or check
  the current using its resistor value and the LED's forward voltage. Keep
  the resistor in circuit; positive goes to an available output and negative
  to common ground for direct active-high drive.
- **5 V loads:** a separate 5 V supply requires an appropriate driver and common
  ground. Do not connect 5 V to an ESP32-S3 GPIO; the chip's pins are
  [not 5 V tolerant](https://docs.espressif.com/projects/esp-idf/en/v5.0/esp32s3/api-reference/peripherals/usb_device.html#self-powered-device).
- **Buzzer:** use a passive transducer compatible with the generated tones.
  A black two-pin case or a polarity mark does not identify its type or current
  demand. Verify the part specification before connecting it directly. Use a
  suitable transistor/MOSFET driver for a higher-current or higher-voltage
  transducer, with a flyback diode for an inductive magnetic load. An active
  buzzer with its own oscillator is not a substitute for variable-pitch RTTTL
  playback.

### Configure outputs on a board with available pins

Numeric pins below are Arduino MCU GPIOs. ESP32 Full Companion also accepts
explicit `xN` expander pins for LEDs; see the [expander guide](i2c_expanders.md).

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
exception for an existing active-low user button. In-use pins are locked across rotary
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
