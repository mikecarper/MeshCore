# Easy full-firmware update over LoRa

This guide shows the shortest manual path for sending a **full firmware image** from a computer to a
MeshCore node over LoRa. It uses this temporary OTA channel:

| Setting | Value |
| --- | --- |
| Center frequency | 909.950 MHz |
| Bandwidth | 125 kHz |
| Spreading factor | SF5 |
| Coding rate used in this guide | CR5 |
| Example window | 120 minutes |

The copy/paste command is:

```text
tempradio 909.950,125,5,5,120
```

The fourth value is the transmit coding rate. This guide uses CR5, but the participating nodes' coding rates
do not need to match.

`tempradio` is not saved and the node returns to its normal radio settings when the window ends or the node
reboots. This frequency is intended for North American configurations. Confirm that it is permitted in your
location and change it when necessary.

## Before you start

You need:

- A standard, non-logging Keymind OTA build on the source and destination nodes. Logging and MQTT builds do
  not include LoRa OTA. Use the WiFi or USB connection to update those.
- An OTA-capable destination. ESP32 boards use their A/B firmware slots. nRF52 nodes also need the
  MeshCore OTAFIX bootloader.
- The new, non-merged application firmware for the destination's exact board **and role**. Use `.bin` for
  ESP32 or `.hex` for nRF52; do not package an ESP32 `-merged.bin` factory image.
- A source node connected to the computer by USB serial.
- Overlapping `tempradio` windows on the source, destination, and every repeater needed between them.

LoRa OTA packets are generated, received, and relayed only while `tempradio` is active. If any required
window closes, the transfer stops making progress and can resume during a later overlapping window.

## 1. Install `motatool`

Install Rust if necessary, then install the standalone packaging and serving tool:

```bash
git clone https://github.com/vk496/motatool
cargo install --path ./motatool
```

## 2. Package the full firmware

Put the application firmware in a working directory, then build a full `.mota` container. For example:

```bash
mkdir -p ./motas
motatool build --fw ./Heltec_v3_repeater-v1.16.05.bin --out-dir ./motas
motatool verify ./motas/*.mota
```

Replace the example filename with the firmware for the destination's exact target. With no `--base`
argument, `motatool build` creates a full-image update. The firmware must contain its MeshCore `EndF`
identity trailer so `motatool` and the destination can verify the target, hardware, and version.

Do not continue if `motatool verify` reports a failure.

## 3. Start the temporary OTA channel

On the source node, destination node, and every intermediate repeater, run:

```text
tempradio 909.950,125,5,5,120
```

All participating nodes must use the same frequency, bandwidth, and spreading factor. Their time windows must
overlap. Start with the farthest hop (the destination) and work back toward the source when using `tempradio`.

If you administer the destination over LoRa, the controller used to send later `ota` commands must also be
able to communicate on this temporary channel. For unattended nodes, use synchronized `tempradioat` entries
instead of manually starting the windows. Ensure the nodes' clocks are set before using `tempradioat`.

## 4. Serve the update from the computer

Close any serial terminal using the source node's USB port, find its device name, and start the server:

```bash
motatool serve --dir ./motas --serial /dev/ttyACM0 -v
```

Replace `/dev/ttyACM0` with the source node's serial device. `motatool` attaches the folder to the source
node, and the source advertises the full update over LoRa while its temporary-radio window is active.

Leave this command running until the destination finishes downloading.

## 5. Find and download the update

On the destination node, check its state and ask for nearby updates:

```text
ota status
ota ls
```

Discovery is asynchronous. Wait a few seconds and run `ota ls` again if the list is initially empty. Find
the entry marked `[yours]` and `full`, then use its displayed number. If it is entry 1, run:

```text
ota pull 1 flash
```

Monitor the transfer:

```text
ota status
```

The update is ready when the status says `ready to install`. OTA is deliberately the lowest-priority mesh
traffic, so a busy channel can make a full-image transfer take longer than the example window. If necessary,
start another overlapping `tempradio` window; the download resumes rather than starting over.

## 6. Verify and install

Once the destination reports `ready to install`, run:

```text
ota install
```

The destination verifies the complete firmware again before approving it. If verification succeeds, it
reboots and applies the update. If verification fails, it keeps the current firmware and reports an error.

After the node returns, reconnect on its normal radio channel and confirm:

```text
ota status
```

## Quick troubleshooting

- **Nothing appears in `ota ls`:** confirm that `motatool serve` is still running and every required node
  has an active `tempradio 909.950,125,5,5,120` window.
- **The update is marked `[other hw]`:** it is for a different board or firmware role. Do not install it.
- **The download stalls:** check the source, destination, and intermediate repeaters. Restart matching,
  overlapping temporary-radio windows if one expired.
- **The serial port is busy:** close the serial terminal before starting `motatool serve`.
- **The destination rejects the package:** verify that you used the non-merged application image with the
  `EndF` trailer and that `motatool verify` succeeds.

For additional commands and safety details, see [the full OTA user guide](ota_user_guide.md). For protocol
and container internals, see [the OTA protocol specification](ota_protocol.md).
