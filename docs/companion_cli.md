<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/companion_cli/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

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
| Repeat Mode | `get repeat`, `set repeat on`, `set repeat off` |
| Transmit power | `get tx`, `set tx <dBm>` |
| Radio apply status | `get radio.apply` |
| Timezone, hours | `get tz.offset`, `set tz.offset <hours>` |
| Airtime limit | `get dutycycle`, `set dutycycle <percent>` |
| Receive gain | `get radio.rxgain`, `set radio.rxgain <on/off>` |
| Traces on radio2 only ([details](radio_profiles.md#companion-traces-on-radio2)) | `get radio2.trace`, `set radio2.trace <on/off>` |
| Device power saving | `get powersaving`, `set powersaving <on/off>` |
| GPS status, power and clock sync (GPS builds) | `gps`, `gps on`, `gps off`, `gps sync` |
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

## Change frequency in Repeat Mode

1. Enable **Repeat Mode** in the app on its starting frequency (918 MHz in the US).
2. Open the connected Companion’s **Command Line**. To change only the frequency, enter `set freq 910.525`. To set frequency, bandwidth, spreading factor and coding rate together, enter `set radio 910.525,62.5,7,5`.
3. Enter `get radio` to check the saved settings and `get repeat` to confirm it says `on`.

`set freq` keeps the current modem settings and preamble. For a 32-symbol preamble with `set radio`, append `,32`. The settings take effect without a reboot and persist after restart. Use matching settings on the nodes you want to relay.

You can also enable Repeat Mode from the CLI with `set repeat on` while tuned to a starting frequency.

## Duplicate channel messages

Filtering starts on for all channels. Use the Companion’s **Command Line** to
check or change it. For example, `2` below is the channel’s numbered slot.

```text
get channel.dedup
set channel.dedup off
set channel.dedup on

get channel.dedup.2
set channel.dedup.2 off
set channel.dedup.2 on
```

Global `off` pauses filtering on every channel. Global `on` restores each
slot’s saved setting. Changes take effect immediately and survive a reboot.
The Wio-E5, Wio-E5 Mini, RAK 3x72, and Tiny Relay USB Companion builds
omit the filter and its commands to preserve their 32 KiB filesystem.

## Power saving

On ESP32 Bluetooth Companions, `set powersaving on` requests a 15-30 ms
connection interval during app sync and 60-75 ms after two seconds of quiet.
Three closely spaced incoming app frames return the link to fast timing.
Mesh traffic sent to the phone does not count as app activity. With
`set powersaving off`, the link stays on the fast profile. The phone chooses the
actual connection interval.

ESP32 Companions with hardware USB Serial/JTAG and a tickless power-management
build can use automatic light sleep during short idle periods on battery. A USB
host or its reconnect grace period holds the device awake.

## Wi-Fi

Wi-Fi builds expose `get wifi.status`, `get wifi.ip`, and
`get/set wifi.enabled`. Configure credentials with `set wifi.ssid <SSID>` and
`set wifi.pwd <password>`. Credential access follows the normal local/admin
restrictions. Reboot after saving Wi-Fi settings to apply them.

Heltec V4 Wi-Fi builds also advertise `_meshcore._tcp` through mDNS once their
TCP listener is enabled and Wi-Fi connects. The hostname follows the existing
`meshcore-<node-name>` DHCP naming policy; TXT fields identify the node, firmware
and Companion protocol. Discovery follows node renames and stops when the
listener or Wi-Fi is disabled. Other ESP32 Wi-Fi recipes can opt in with
`ENABLE_ESP_MDNS` at build time.

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

On boards with a buzzer, `get buzzer` shows its current state. Use
`set buzzer on` or `set buzzer off` to change the saved setting. The device UI
uses the same setting.
