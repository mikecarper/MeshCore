# Full Companion

`companion_radio_full` combines every Companion transport available on its
platform and acts as a host-backed LoRa mOTA source for updating other nodes.
The full Companion is deliberately not a LoRa OTA destination: it has no
firmware staging store, refuses `ota install`, and never advertises its own
firmware as an mOTA image.

| Capability | ESP32 full | nRF52 full |
| --- | --- | --- |
| USB Binary Companion | Yes | Yes |
| BLE Binary Companion | Yes | Yes |
| USB ASCII terminal | Yes | Yes |
| Host-backed LoRa mOTA source | WiFi TCP 5001 | Exclusive USB mode |
| WiFi Companion/WebConfig | Yes | No - nRF52840 has no WiFi |
| LoRa self-update | No | No |

## Build and install

The target is synthesized by `build.sh` only when matching transport recipes
exist for the exact board variant:

- ESP32 requires matching WiFi, USB, and BLE Companion environments.
- nRF52 requires matching USB and BLE Companion environments.

List the available targets:

```bash
bash build.sh list | grep companion_radio_full
```

Build by using one exact listed name:

```bash
bash build.sh build-firmware heltec_v4_r8_companion_radio_full \
  --firmware-version v1.17.0

bash build.sh build-firmware RAK_4631_companion_radio_full \
  --firmware-version v1.17.0
```

To build every available full Companion target, select the corresponding
interactive menu item or run:

```bash
bash build.sh build-full-companion-firmwares \
  --firmware-version v1.17.0
```

Artifacts are written to `out/` by default.

On 4 MB ESP32 boards, the full target uses a single 3 MB application
partition so WiFi, BLE, WebConfig, and source-only mOTA fit together. Flash
the generated `-merged.bin` when first installing this partition layout.
Boards with 8 MB or more retain dual application partitions. Heltec V2 and
TLora V2 use 100 contacts, 8 group channels, and a 16-frame offline queue in
this combined profile because of internal DRAM limits.

The nRF52 target inherits the board's ordinary USB Companion installation
format and adds BLE plus the serial mOTA source. It does not enable an SD cache
or any other board-specific storage behavior; host files are streamed as they
are requested.

## Interfaces

| Platform | Interface | Purpose |
| --- | --- | --- |
| Both | USB, 115200 baud | Binary Companion by default; terminal switch available |
| Both | BLE | Binary Companion; default pairing PIN `123456` |
| ESP32 | TCP 5000 | Binary Companion over WiFi |
| ESP32 | HTTP 80 | Companion WebConfig and first-boot WiFi setup |
| ESP32 | TCP 5001 | Host `.mota` folder from `motatool serve --tcp` |
| ESP32 | TCP 5002 | Local `ota`, `tempradio`, and `normalradio` console |
| nRF52 | USB mOTA mode | Host `.mota` folder from `motatool serve --serial` |

Binary Companion replies are broadcast through the multi-interface manager,
so use one active Companion application at a time. On nRF52, BLE remains
available while USB is in terminal or mOTA mode.

ESP32 ports 5000, 5001, 5002, and WebConfig have no independent login layer.
Expose them only on a trusted LAN or temporary setup network. See
[WiFi setup](./WiFi.md) for credential setup and reconnect behavior.

## USB Binary and terminal modes

USB starts in Binary mode for MeshCore apps and `meshcli`:

```bash
meshcli -s /dev/ttyACM0 -b 115200 ver
```

Open the port with the terminal start token sent automatically:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring '+++MESHCORE-TERM-START' \
  /dev/ttyACM0
```

The input map prevents any Binary Companion control bytes received during the
mode transition from changing the local terminal's character set or display
state while leaving UTF-8 emoji intact. The banner confirms that terminal mode
is active; do not enter the start token again after it appears.

The terminal supports Companion chat commands, including `channels`,
`channel <name-or-slot> <message>`, remote administration with
`login <admin-password>` and `cmd <remote-command>`, and routed
`trace [recipient-name-or-prefix]`, plus local `ota`, `tempradio`, and
`normalradio` controls. For example:

```text
channels
channel #rgdata Hello from Eugene 👋
to Hilltop Repeater
path A1B2C3,D4E5F6
login my-admin-password
cmd ver
trace
```

The `to` command selects the remote-administration target. `path` shows its
saved outgoing route; `path direct`, `path clear`, or a comma-separated list
such as `path A1B2C3,D4E5F6` changes the route used by subsequent `login`,
`send`, and `cmd` commands. Every hop must use the same 2-, 4-, or 6-digit
hexadecimal width. Login passwords are masked during entry and limited by the
radio protocol to 15 UTF-8 bytes. Wait for the asynchronous login result before
using `cmd`; command replies appear as `CLI -> from <name>`. Remote ACL
permissions determine which commands the target accepts.

With no argument, `trace` uses the current `to` recipient. A name-prefix
argument traces that contact directly without changing the current recipient.
The contact must already have a known direct path; results show the SNR at each
hop, or a timeout if the round trip does not return.

An explicit route can use 1-, 2-, or 4-byte hexadecimal prefixes. Spaces,
commas, and mixed separators are accepted:

```text
trace path 1 12 34 56 34 12
trace path 2 1234,ABCD,5678,ABCD,1234
trace path 4 12345678, ABCDEF01 89ABCDEF, ABCDEF01,12345678
```

The entered route must include both the outward and return prefixes. Exact
three-byte traces are not supported.

Return to Binary mode with:

```text
+++MESHCORE-TERM-STOP
```

Closing the USB data connection also resets the port to Binary mode. A
different baud rate, including 57600, does not select ASCII mode.

## nRF52 USB mOTA mode

The nRF52 full target has a third, exclusive USB mode for the host folder.
Unmodified `motatool serve --serial` already sends `ota folder on` when it
opens the port. The Binary parser recognizes that exact idle control sequence,
stops USB Binary traffic, and attaches the serial folder source. The sequence
is not examined inside a framed Binary Companion packet.

While mOTA mode owns USB:

- mOTA manifests and blocks stream from the computer on demand.
- USB Binary and the USB text terminal are unavailable.
- BLE Binary Companion remains available.
- `motatool` sending `ota folder off`, or disconnecting the USB data session,
  detaches the folder and restores Binary mode.

No manual mode token or modified `motatool` build is required.

## Serve mOTA images manually

First put the destination, required relays, controller, and source on the same
bounded TempRadio tuple. The example frequency below is not legal everywhere;
choose a legal tuple supported by every participating radio.

### ESP32 source

Use the local console to start TempRadio:

```bash
nc 192.168.1.50 5002
```

```text
tempradio 909.950,250,7,5,120
ota status
```

Then start the dedicated TCP seeder:

```bash
motatool serve --dir ./motas --tcp 192.168.1.50:5001 -v
```

### nRF52 source

Use the USB terminal briefly to schedule TempRadio, then return to Binary mode
and close the terminal:

```bash
picocom -b 115200 \
  --imap spchex \
  --initstring '+++MESHCORE-TERM-START' \
  /dev/ttyACM1
```

```text
tempradio 909.950,250,7,5,120
+++MESHCORE-TERM-STOP
```

After sending the stop token, exit `picocom` with Ctrl-A, Ctrl-X.

Start the serial seeder on that same port:

```bash
motatool serve --dir ./motas --serial /dev/ttyACM1 --baud 115200 -v
```

`motatool` switches the port into mOTA mode automatically. Stop it with
Ctrl-C to detach the folder. Reopen the terminal and use `normalradio` if the
source should return early; otherwise the saved radio settings return when the
bounded window expires.

Both platforms intentionally refuse firmware installation commands such as:

```text
ota pull <id> flash
ota install
ota dev ...
```

## Script a complete update

The Bash and PowerShell wrappers accept a release ZIP or ready `.mota`, set up
TempRadio, run `motatool`, monitor the exact image, install it on the
destination, and restore the radio path. Use a separate Companion as the
controller.

For an ESP32 full source:

```bash
export MESHCORE_ADMIN_PASSWORD='target-admin-password'

./tools/lora_ota/lora_ota.sh ./release.zip "Roof Node" \
  --controller-serial /dev/ttyACM0 \
  --source-tcp 192.168.1.50:5001 \
  --source-cli-tcp 192.168.1.50:5002
```

For an nRF52 full source, the script automatically detects the token-switched
terminal and uses the same source port sequentially for control and seeding:

```bash
export MESHCORE_ADMIN_PASSWORD='target-admin-password'

./tools/lora_ota/lora_ota.sh ./release.zip "Roof Node" \
  --controller-serial /dev/ttyACM0 \
  --source-serial /dev/ttyACM1
```

```powershell
$env:MESHCORE_ADMIN_PASSWORD = 'target-admin-password'

& .\tools\lora_ota\lora_ota.ps1 '.\release.zip' 'Roof Node' `
  --controller-serial COM7 `
  --source-serial COM8
```

See the [start-to-finish LoRa OTA guide](./lora_ota_automation.md) for package
selection, nRF52 in-place deltas, relays, trust checks, and recovery behavior.
