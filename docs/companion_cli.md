# Companion CLI

Companions accept local CLI commands through the Companion protocol. Remote
commands require the configured admin permission. Available commands depend on
the board and firmware profile; use `help` for the commands in your build.

## Common commands

| Purpose | Commands |
| --- | --- |
| Firmware version | `ver` |
| Node name | `get name`, `set name <name>` |
| Radio profile | `get radio`, `set radio <MHz>,<kHz>,<SF>,<CR>` |
| Frequency | `get freq`, `set freq <MHz>` |
| Transmit power | `get tx`, `set tx <dBm>` |
| Radio apply status | `get radio.apply` |
| Timezone, hours | `get tz.offset`, `set tz.offset <hours>` |
| Airtime limit | `get dutycycle`, `set dutycycle <percent>` |
| Receive gain | `get radio.rxgain`, `set radio.rxgain <on/off>` |
| Receive power saving | `get radio.rxps`, `get radio.rxps.config` |
| FEM receive gain | `get radio.fem.rxgain`, `set radio.fem.rxgain <on/off>` |
| FEM transmit gain | `get radio.fem.txgain`, `set radio.fem.txgain <on/off>` |
| BLE PIN | `set pin <0-999999>` |
| Restart | `reboot` |
| Power off, where supported | `poweroff` or `shutdown` |

Supported frequencies, bandwidths and power limits depend on the radio and
board. RF changes may wait for active traffic; `get radio.apply` reports the
apply state. Transmit power is the radio-chip setting, which can differ from
antenna output on amplified boards.

See [CLI commands](cli_commands.md) for setting ranges and access restrictions,
and [radio profiles](radio_profiles.md) for radio2, temporary profiles and
schedules.

## Wi-Fi

Wi-Fi builds expose `get wifi.status`, `get wifi.ip`, and
`get/set wifi.enabled`. Configure credentials with `set wifi.ssid <SSID>` and
`set wifi.pwd <password>`. Credential access follows the normal local/admin
restrictions. Reboot after saving Wi-Fi settings to apply them.

## Display and notifications

This fork uses the saved `display.*` controls described in
[display controls](cli_commands.md#set-display-modes-and-timeouts):

```text
get display.mode
get display.timeout
get display.usb.mode
get display.usb.timeout
get display.inbox
```

Timeouts default to 900 seconds on e-paper and 15 seconds on other screens.
Battery and USB settings are independent. Display settings live in a separate
checked preferences file and preserve the existing node-preferences layout.
The upstream `ui.*` settings are not aliases in this fork.

For sound, vibration and custom notification rules, see
[notifications](notifications.md).
