# USB Companion client compatibility

The RAK4631 report was reproduced on the MercerWoodMesh Pi with the published
`RAK_4631_companion_radio_full-v1.17.1.6-halo-keymind-cascade-dev-d4a641ff`
image. The fixes described here are in the source and test firmware;
the existing GitHub release assets have not yet been replaced with this fix.
Hardware validation was completed on 2026-09-14 with test builds labeled
`v1.17.1.6-usb-client-test-0e5955f8` (base commit plus these USB fixes).

## Port selection

| Hardware | Companion/ASCII port | USB logging |
| --- | --- | --- |
| RAK4631 and other nRF52 Full Companions | Primary CDC, Linux `*-if00` | Optional second CDC, `*-if02` |
| ESP32 Full Companions | Primary USB serial port | Shares that port; must be off for Binary Companion |

`+++MESHCORE-TERM-STOP` changes the primary port to Binary Companion. Its
`OK - Binary mode` reply does not move the client to a second port. Close the
terminal before opening that same port with `meshcli -s PORT infos`. Do not use
MeshCLI's repeater-mode `-r` option for a binary Companion connection.

## Two reproduced failures

1. Stock `meshcore-cli` 1.6.3 was tested with `meshcore` 2.3.10, whose serial client
   deliberately deasserts DTR. The nRF52 primary USB gate and response route
   both required DTR. A raw battery query returned a valid 14-byte response
   with DTR high and no bytes with DTR low.
2. The automatic ASCII banner ended in `> `. MeshCLI recognized that `>` as a
   binary frame marker and consumed the real response's leading `>` as a
   length byte. Even forcing DTR high in a diagnostic client did not recover
   the first `APP_START` response. Stock MeshCLI exited without node information.

The nRF52 transport now accepts real USB input as evidence of a client, tagged
with the current session generation. Close, line-coding, and device-reset
events invalidate that evidence; an old reader racing cleanup cannot grant
access to a newer session. The write path remains bounded and does not wait
for an unread host. CDC1 logging retains its own DTR requirement.

The default ASCII terminal now suppresses its banner and unsolicited terminal
events until ASCII input arrives. Enter or a command reveals the terminal.
A binary probe receives a clean first response. Explicit START/STOP remains
available, and an incomplete binary probe still times out back to ASCII.

On a shared logging port, STOP now reports
`ERROR: set usb.logging off before Binary mode` while logging is enabled.
It no longer acknowledges Binary mode and immediately reclaims the port for
ASCII logging.

## Hardware validation

The RAK4631 Full test firmware passed with an unmodified installed MeshCLI and
its normal DTR-low serial library:

- ASCII `ver` from the default mode, without START;
- explicit STOP acknowledgement followed by 10 binary battery queries;
- 10 immediate close/reopen queries each with DTR low and high;
- 10 stock-library reconnects and three separate `meshcli ... infos` runs;
- all of the above while the second CDC streamed logs: 598 bytes received,
  no logging disconnections.

The Heltec V4 Full Companion also passed using its ESP32-S3 USB Serial/JTAG
interface, with USB logging disabled:

- default ASCII `ver`, then STOP and 10 clean binary battery replies;
- 10 stock-library reconnects and three unmodified `meshcli ... infos` runs;
- a stock-client connection directly after reboot, without either mode token.

The shared-port logging policy was exercised on the V4 as well. With logging
on, STOP returned the explicit error, a binary probe could not claim the port,
and stock MeshCLI could not connect. After `set usb.logging off`, STOP worked,
10 binary replies were clean, and three stock-client reconnects succeeded.

After testing, the V4's complete original flash snapshot was restored and
verified by the flasher. Its original `v1.17.1-soak-v4-B` firmware answered
again, and the previously active memory-soak collector was restarted. This
test interrupted that board's uptime; the restored firmware starts a new run.
Fresh successful V4 telemetry was verified at `2026-09-14T19:46:01Z`.
The RAK4631 remains on the patched Full Companion test build with its separate
logging interface enabled. The Pi's previously active services were restored.

The nRF52 DTR stress sequence is deliberately not used on ESP32. Changing those
control lines during immediate close/reopen triggered the V4's hardware
bootloader and produced an `ESP` boot message. Preselecting RTS before opening
did not prevent that transition on this host. ESP32 reconnect results above
use the unmodified client's normal connection sequence instead.

The test issues local information queries only. It does not transmit test chat
messages over LoRa. Firmware installation and logging configuration are
separate from the reusable test runner:

```sh
python3 tools/hil/companion_usb_client.py \
  --port /dev/serial/by-id/PRIMARY-if00 \
  --logging-port /dev/serial/by-id/LOGGING-if02 \
  --test-dtr --cycles 10 --output usb-client-results.json
```

Replace the example paths with the device's actual stable paths. Install
`meshcore-cli` in the Python environment used for the test. Start after a fresh
reboot so the default-ASCII check has a new session. On ESP32, omit both
`--logging-port` and `--test-dtr`, and disable USB logging first.

## Automated checks

- 53 Python USB checks pass, including compiled execution of the real nRF52
  callbacks and transport gate with DTR-low clients, cleanup races, early
  input, line-coding changes, physical disconnects, full TX FIFO, CDC1 isolation,
  and the 1200-baud bootloader entry.
- The real terminal methods are compiled for ordinary and Full modes to verify
  that unsolicited text is suppressed before the first binary response.
- The actual STOP branch is exercised for logging off, shared-port logging on,
  and dedicated-port logging on.
- 63 native serial-switching, flow-control, and terminal-session tests pass.
- RAK4631 Full and Heltec V4 Full firmware compile and pass their packaged
  capability checks.

The USB workflow runs the new compiled regression tests on subsequent changes.
