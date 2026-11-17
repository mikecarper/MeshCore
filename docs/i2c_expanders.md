<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/i2c_expanders/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 I2C expanders

ESP32 Full Companion and custom builds with `MESH_CUSTOM_PERIPHERALS=1`
support one configured expander on the board's existing expansion I2C bus.
The board must define `PIN_BOARD_SDA` and `PIN_BOARD_SCL`. Native GPIO
numbers retain their meanings; expander pins are explicitly named `x0`–`x15`.
Repeater GPIO/timer commands do not accept expander pins. A passive buzzer
still needs a native MCU pin for RTTTL tones.

For an OLED-only Heltec V4.3, an expander is optional: select OLED and reboot
to [release four native accessory pins](rotary_input.md#oled-expansion-mode-in-the-same-unified-firmware).

| Chip | CLI type | Pins | Address range |
| --- | --- | --- | --- |
| PCF8575 | `pcf8575` | `x0`–`x15` | `0x20`–`0x27` |
| PCF8574 / PCF8574T | `pcf8574` | `x0`–`x7` | `0x20`–`0x27` |
| PCF8574A / PCF8574AT | `pcf8574a` | `x0`–`x7` | `0x38`–`0x3f` |
| MCP23017 | `mcp23017` | `x0`–`x15`, except `x7` and `x15` | `0x20`–`0x27` |
| MCP23008 | `mcp23008` | `x0`–`x7` | `0x20`–`0x27` |

The MCP23017 exclusions accommodate the manufacturer's input restrictions
without leaving reassigned output pins driven. Leave MCP23017 GPA7/GPB7
unconnected. PCF inputs retain their weak
pull-ups; `rotary.pullup off` cannot be used with PCF encoder inputs.
Choose a general-purpose breakout with individually exposed GPIO terminals.
LCD1602/2004 backpack boards wire their outputs for a display and are not the
recommended breakout for this arrangement.

## Heltec V4.3: PCF8575, encoder, buzzer and three LEDs

Use the unified native-USB Full Companion image. Before powering the module,
set its address jumpers to **0x27** (A0, A1 and A2 high). Default `0x20`
can conflict with a soil-sensor probe. Compiled sensor/display addresses
are protected, so another board may require a different unused address.

| Connection | Destination |
| --- | --- |
| Expander VCC / GND | Heltec 3.3 V / GND |
| Expander SDA / SCL | GPIO4 / GPIO3 |
| Encoder CLK / DT | Expander P00 (`x0`) / P01 (`x1`) |
| Encoder common and switch ground | GND |
| Encoder switch | GPIO0 |
| Notification LED negative | Expander P02 (`x2`) |
| GPS LED negative | Expander P03 (`x3`) |
| Bluetooth LED negative | Expander P04 (`x4`) |
| Each LED positive | 3.3 V through its current-limiting resistor |
| Passive buzzer signal | GPIO6, through a driver if its load requires one |
| Passive buzzer ground | GND |

Power both the expander and its I2C pull-ups from **3.3 V**. Suitable
prewired LEDs with built-in resistors need no additional resistor; check
their current against the chip's per-pin and total ratings. Leave `INT`
unconnected: this implementation polls inputs. GPIO43 is unused here,
so its encoder series resistor is no longer needed. Release the GPIO0
switch during reset.

Enter each command separately:

```text
set rotary off
set expander pcf8575,0x27
get expander
set rotary.pullup on
set rotary.pins x0,x1,0
set rotary.steps 4
set rotary on
set peripheral.buzzer 6
set peripheral.led.notify x2,low
set peripheral.led.gps x3,low
set peripheral.led.ble x4,low
```

The existing sound/mute and notification rules still control the buzzer.
The GPS LED indicates active GPS use; the Bluetooth LED indicates that
Bluetooth is enabled, including advertising. See [notifications](notifications.md)
for sound and alert rules.

## Other expanders and settings

Use the matching type and the module's actual seven-bit address:

```text
get expander
set expander mcp23017,0x27
get rotary.pins.available
get peripheral.pins.available
```

`x0`–`x7` map to PCF8575 P00–P07 or MCP23017 GPA0–GPA7.
`x8`–`x15` map to PCF8575 P10–P17 or MCP23017 GPB0–GPB7.
Eight-pin chips use P0–P7/GP0–GP7. LED assignments accept `,low` or
`,high`. PCF pins default to `low` (current sinking), MCP pins to `high`.
An active-high MCP LED connects through its LED/resistor to ground.

Disable rotary and clear expander LED assignments before changing chip
type/address or using `set expander off`. Configuration and assignments
are saved separately. Firmware does not guess the chip type or scan all
addresses. These parts lack a universal immutable chip-ID register, so the
selected type must match the physical chip.

An absent chip or failed I2C transaction stops further input/output traffic;
physical outputs can retain their last state until the device is reset or
power-cycled. Correct the fault and explicitly reconfigure/re-enable them. Mechanical
encoders can lose fast turns while firmware is busy: these chips do not
queue every transition. Test physical turning speed on the completed
device. This feature does not turn an expander into a PWM/audio source.

Hardware references: [TI PCF8575](https://www.ti.com/lit/ds/symlink/pcf8575.pdf),
[NXP PCF8574/8574A](https://www.nxp.com/docs/en/data-sheet/PCF8574_PCF8574A.pdf),
[Microchip MCP23017](https://www.microchip.com/en-us/product/MCP23017), and
[MCP23008](https://www.microchip.com/en-us/product/MCP23008).
