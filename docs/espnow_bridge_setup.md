# ESP-NOW bridge: simple setup

Use two ESP32 LoRa boards to join two MeshCore networks with different radio
settings. Each board repeats on its own LoRa network, and the boards exchange
packets directly over 2.4 GHz ESP-NOW. No router, internet connection, or manual
peer pairing is needed.

```text
LoRa network A <--> Repeater A <-- ESP-NOW --> Repeater B <--> LoRa network B
```

## 1. Choose the firmware

Install the exact board's **Full ESP-NOW bridge repeater** image on each board.
The target name ends in `_repeater_bridge_espnow`; a standard Heltec V4 uses
`heltec_v4_repeater_bridge_espnow`. Full/MQTT and Full Companion images are
different choices. See the [firmware picker guide](firmware_picker.md).

Open each board's [USB console](https://flasher.meshcore.io/console) at
115200 baud and run:

```text
get bridge.type
```

The answer must be `espnow`. `mqtt`, `rs232`, or `none` means that image does
not provide the ESP-NOW bridge described here.

## 2. Set each board's LoRa network

Configure one board at a time over USB. Replace the placeholders below with
the radio settings used by the network on that board's side. Use `Bridge-A`
for the first board and `Bridge-B` for the second, or choose your own names.

```text
set bridge.enabled off
set name Bridge-A
set radio <freq>,<bw>,<sf>,<cr>
reboot
```

Do not type the angle-bracket placeholders literally.

| Radio option | Meaning |
| --- | --- |
| `freq` | LoRa frequency in MHz. Match the local network on this side. |
| `bw` | LoRa bandwidth in kHz. Match that network's bandwidth. |
| `sf` | Spreading factor, 5-12. Match that network's setting. |
| `cr` | Coding-rate value, 5-8. Match that network's setting. |
| `name` | A label to tell the two repeaters apart. It does not pair them. |

**`set radio` requires a reboot to apply.** After reconnecting, `get radio`
shows the saved settings. Use permanent radio settings for this setup;
`tempradio` sessions expire.

## 3. Configure the ESP-NOW link on both boards

Run this block on **both** boards. Replace `YourSharedKey` with the same
case-sensitive secret on each board, using 1-15 characters. Channel `6` is
an example; both boards must use the same channel.

```text
set bridge.enabled off
set bridge.channel 6
set bridge.format wrapped
set bridge.secret YourSharedKey
set bridge.source tx
set bridge.delay 500
set repeat on
set bridge.enabled on
```

These settings are saved across reboot. Bridge changes apply immediately;
changing an enabled bridge's channel, format, or secret restarts the bridge.
Stopping it first lets you finish configuring both sides before connecting them.

## What each bridge option does

| Option | Choices and purpose |
| --- | --- |
| `bridge.enabled` | `on` starts the bridge; `off` stops it and keeps its saved settings. |
| `bridge.channel` | The 2.4 GHz channel, 1-13. Match it on both boards. This is separate from the LoRa frequency in `set radio`. |
| `bridge.format` | `wrapped` is the default for this two-repeater setup and uses the shared secret. `raw` connects to primary-ESP-NOW nodes such as `Generic_ESPNOW` or `SenseCapIndicator-ESPNow`; it ignores the secret. Both peers must use compatible formats. |
| `bridge.secret` | A matching, case-sensitive value of 1-15 characters for `wrapped` mode. It separates bridge groups; it is not strong encryption. MeshCore's own message encryption remains in place. |
| `bridge.source` | `tx` crosses packets this repeater transmits on LoRa, after its forwarding decisions; use this for the setup above. `rx` crosses received packets before those decisions and can pass traffic the local repeater would not forward. |
| `bridge.delay` | Wait before processing a packet received from the other bridge, in milliseconds. Range: 0-10000. Start with the default `500`. |
| `repeat` | `on` enables normal LoRa forwarding; `off` disables forwarding. Keep it `on` on both bridge repeaters. Normal routing, scope, and packet-filter rules still apply. |
| `bridge.type` | Read-only firmware capability. It must report `espnow` for this guide. |
| `bridge.running` | Read-only runtime state. `on` means the local bridge started; it does not prove the other board is reachable. |

This fork uses **`set bridge.source tx`** or **`rx`**. Some upstream guides
use `logTx` and `logRx`; those are not the command values used here.

For a LoRa gateway connected to primary-ESP-NOW Companions instead of another
LoRa repeater, see [raw ESP-NOW compatibility](WiFi.md#wifi-companion-setup).

## 4. Check the link

Run on both boards:

```text
get bridge.enabled
get bridge.running
get bridge.channel
get bridge.format
get bridge.secret
get bridge.source
get bridge.delay
get repeat
get radio
```

Expected: enabled and running `on`, matching channel/format/secret, source
`tx`, delay `500`, repeat `on`, and the intended LoRa settings on each side.

Send a message from a Companion on network A to one on network B, then reply
from B to A. Check both directions; configuration readback alone does not test
delivery.

If it does not work:

- **Commands are unavailable:** check `get bridge.type` and the firmware choice.
- **Enabled is on but running is off:** initialization failed. Reboot and
  check again; ensure another WiFi service is not occupying the radio.
- **Both are running but traffic does not cross:** compare channel, format,
  and secret; confirm each board's LoRa settings were applied with a reboot,
  and check the networks' forwarding and scope rules.

A running bridge keeps the device awake, even with device power saving enabled.
For every command's full details, see the [bridge CLI reference](cli_commands.md#bridge-when-bridge-support-is-compiled-in).

For a broader comparison with a Linux-based bridge, see the
[MeshCore Nexus bridging guide](https://meshcore.nexus/guides/lora-bridging#method-2-meshcore-esp-now-bridge).
