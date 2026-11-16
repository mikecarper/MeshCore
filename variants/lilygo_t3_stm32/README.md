# LilyGO T3 STM32 V1.0

This fork imports the STM32WL55CCU6 board definition and radio initialization
from upstream, with a KISS modem environment:

```sh
pio run -e LilyGo_T3_STM32_kiss_modem
```

The modem uses USART1 through the board's USB serial adapter at 115200 baud.
Application flash stays limited to 224 KiB. InternalFS occupies the final
32 KiB at `0x08038000`–`0x0803FFFF`; preserve it during updates.

Upstream's Repeater and OLED Companion recipes are deferred in this fork.
The OLED Companion exceeded application flash by 54,972 bytes even with GCC 14,
`-Oz` and LTO. Those recipes are not exposed as usable environments here.
The imported SPI OLED driver support remains available for future qualification.

| Function | Pins / setting |
| --- | --- |
| USART1 | TX PA9, RX PA10 |
| Radio RF switch | PB2/PB6: idle 0/0, RX 1/0, HP TX 0/1 |
| Radio oscillator | 32 MHz crystal; TCXO mode disabled |
| SPI1 | SCK PA5, MISO PA6, MOSI PA7 |
| OLED | CS PB12, DC PA8, no software reset pin |
| SD card | CS PA4, held high |
| User button | PH3/BOOT, active high |
| LEDs | PA0/PA1, active high |

Built-in battery telemetry, SD storage and low-power optimization are not
implemented. This integration has no physical-board validation; upstream's
hardware observations do not qualify this fork's changed radio and KISS code.
