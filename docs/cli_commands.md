# CLI Commands

This document provides an overview of CLI commands that can be sent to MeshCore Repeaters, Room Servers and Sensors.

See [CLI Availability by Firmware Build](cli_build_matrix.md) for the role and
profile matrix, including the commands intentionally omitted from portable
firmware.

See [CLI Command Availability Matrix](cli_command_availability.md) for the
command-by-command nRF52 and ESP32 build tables.

The first word of a command is case-insensitive, so `set`, `Set`, and `SET`
are equivalent, as are `get`, `Get`, and the other command verbs. The case of
arguments such as node names, passwords, and keys is left unchanged.

## Navigation

- [Operational](#operational)
- [Neighbors](#neighbors-repeater-only)
- [Statistics](#statistics)
- [Logging](#logging)
- [Information](#info)
- [Configuration](#configuration)
  - [Radio](#radio)
  - [System](#system)
    - [GPIO](#control-an-exposed-gpio)
  - [Routing](#routing)
  - [Flood Rules](#change-persistent-flood-rules-in-the-field)
  - [Group Text Moderation](#moderate-flood-group-text-by-channel-sender-and-source-path)
  - [ACL](#acl)
  - [Region Management](#region-management-v110)
    - [Region Examples](#region-examples)
  - [GPS](#gps-when-gps-support-is-compiled-in)
  - [Sensors](#sensors-when-sensor-support-is-compiled-in)
  - [Bridge](#bridge-when-bridge-support-is-compiled-in)
  - [Ethernet](#ethernet-when-ethernet-support-is-compiled-in)

---

## Operational

### Reboot the node
**Usage:** 
- `reboot`

**Note:** No reply is sent.

---

### Power-off the node
**Usage:**
- `poweroff`, or
- `shutdown`

**Note:** No reply is sent.

---

### Enter the UF2 bootloader (nRF52 only)
**Usage:**
- `uf2reset`

**Serial Only:** Yes

**Note:** Reboots directly into the UF2 bootloader on supported nRF52 boards.

---

### Reset the clock and reboot
**Usage:**
- `clkreboot`

**Note:** No reply is sent.

---

### Sync the clock with the remote device
**Usage:** 
- `clock sync`

---

### Display current time in UTC
**Usage:**
- `clock`

---

### Set the time to a specific timestamp
**Usage:** 
- `time <epoch_seconds>`

**Parameters:**
- `epoch_seconds`: Unix epoch time

---

### Send a flood advert
**Usage:** 
- `advert`

---

### Send a zero-hop advert
**Usage:**
- `advert.zerohop`

---

### Start or stop an Over-The-Air (OTA) firmware update
**Usage:**
- `start ota`
- `start ota ap`
- `stop ota`

`start ota` serves the web upload page on the station IP when connected to WiFi;
otherwise it raises the `MeshCore-OTA` access point. `start ota ap` always raises
the access point, which is useful when the normal network uses client isolation.

On an ESP32 build with WebConfig, the manual OTA uploader and WebConfig both use HTTP
port 80 and cannot run together. Stop WebConfig before `start ota`, or stop OTA
before `start webconfig`.

FULL ESP32 builds also expose the `.mota` folder seeder on TCP port 5001
whenever WiFi is usable. This listener is independent of port 80, so a host can
run `motatool serve --dir ./motas --tcp <node-ip>:5001` while WebConfig is
active. The TCP connection auto-attaches and detaches the folder; do not run
`ota folder on` at the same time because that command selects the USB-serial
folder transport.

---

### Browser configuration portal (ESP32 repeater and room server)

**Usage:**

- `start webconfig`
- `start webconfig ap`
- `stop webconfig`
- `set webui on`
- `set webui off`
- `get webui`
- `get wifi.ssid`
- `get wifi.status`
- `get wifi.powersave`
- `get wifi.cli`
- `set wifi.ssid <network name>`
- `set wifi.pwd [password]`
- `set wifi.powersave <none|min|max>`
- `set wifi.cli <on|off>`

`set webui on` is the persistent master switch. It starts the portal now and
again after future reboots; `set webui off` closes it and disables that boot
start. `get webui` reports the saved on/off state plus whether the portal is
inactive, joining WiFi, serving a setup AP, or serving a LAN URL. The default is
`off` on repeater and room-server builds. On display-equipped repeaters, an
otherwise-unused triple click toggles the same saved setting.
Consequently, `> off, http://192.168.1.130/` means the saved boot setting is
off while a temporary WebConfig session is currently active at that URL.

`start webconfig` is a temporary start that does not change the saved switch.
It serves the shared WiFi, radio, flood, loop, and status page and reports its
URL. MQTT builds also show the MQTT tab and wizard step; non-MQTT builds remove
them entirely. Sign in with the node's admin password. If the node has no saved
WiFi SSID, the command starts the open `MeshCore-Setup-XXXX` captive AP at
<http://192.168.4.1/> instead.

`start webconfig ap` forces captive-AP mode. It will not interrupt an active
MQTT bridge, so run `set bridge.enabled off` first. Use `stop webconfig` to
close either mode for the current boot. (`stop webconfig` does not change a
saved `webui on`.)
LAN mode otherwise remains active until reboot; setup mode stops after 10 minutes
with no connected client.

The saved `wifi.cli` setting defaults to `on`. Use `set wifi.cli off` to disable
the **CLI** tab.
`get wifi.cli` reports `off`, `on, waiting for WiFi client`, or `on, active`.
The saved setting becomes active only in station/LAN mode while the WiFi client
is connected. It is deliberately unavailable on the open setup access point.
The tab sends one command at a time through the local administrator command
parser and displays the reply in the browser. It uses remote-administrator
command permissions, so commands explicitly restricted to a physical serial
connection remain unavailable.
Select **Command block** to paste up to 100 commands with one command per line.
Blank lines are ignored, and every nonblank line must fit the normal 159-byte
CLI command limit. The browser sends the lines sequentially and waits for each
reply before sending the next line. The queue exists only in that browser page;
closing it stops any commands that have not yet been sent. A lost connection
also stops the remaining block.

Up/down arrow keys recall commands from the current browser session in
single-command mode. A command that stops WebConfig, changes its WiFi
connection, disables `wifi.cli`, or reboots the node stops the remaining block
and can close the page before its reply is collected.

On FULL MQTT and FULL logging ESP32 repeater/room-server builds,
`get wifi.ssid` reports the saved standalone WebConfig network and
`get wifi.status` reports whether WiFi is unconfigured, off, connecting,
running the setup AP, failed, or connected. A connected result includes the
SSID, LAN IP, and RSSI. When the shared OTA seeder is running, the reply appends
`OTA TCP 5001: listening` or `OTA TCP 5001: client connected`. WiFi being off
is normal while WebConfig is inactive; run `start webconfig` when a temporary
connection is wanted. `get wifi.powersave` reports the saved standalone setting
as `none`, `min`, or `max`; the default is `none`.

The WiFi `set` commands work on MQTT observers and on FULL standalone
ESP32 repeater/room-server builds. On a standalone build, changing the SSID or
password stops an active WebConfig session; run `start webconfig` again to use
the new credentials. `set wifi.pwd` with no value selects an open network.
Power-save changes are applied immediately when WiFi is running and otherwise
take effect on the next connection. `get wifi.pwd` is intentionally unavailable
so the standalone password is never returned by the CLI.

The browser portal is not compiled into the two 4 MB
`LilyGo_TLora_V2_1_1_6_*_observer_mqtt` targets because it does not fit while
retaining the two app slots required for LoRa OTA. Their normal CLI settings
remain available.

---

### Erase/Factory Reset
**Usage:**
- `erase`

**Serial Only:** Yes

**Warning:** _**This is destructive!**_

---

## Neighbors (Repeater Only)

### List nearby neighbors
**Usage:** 
- `neighbors`

**Note:** The output of this command is limited to the 8 most recent adverts.

**Note:** Each line is encoded as `{pubkey-prefix}:{timestamp}:{snr*4}`

---

### Remove a neighbor
**Usage:** 
- `neighbor.remove <pubkey_prefix>`

**Parameters:** 
- `pubkey_prefix`: The public key of the node to remove from the neighbors list

---

### Discover zero hop neighbors

**Usage:** 
- `discover.neighbors`

This command is available in every repeater build profile, including portable
MQTT, standard, logging, OTA, FULL, and FULL logging artifacts. It does not
require MQTT or PSRAM.

---

### Discover neighbor scopes (MQTT observer, PSRAM only)

Refreshes the zero-hop neighbor table, then queries each neighbor for its region
scopes and publishes the assembled table to the MQTT `neighbors` topic once.

**Usage:**
- `discover.scopes`

**Note:** Requires a PSRAM board with the MQTT bridge running. On non-PSRAM MQTT
builds it replies `Err - not supported (requires PSRAM)`. If a `discover.neighbors`
refresh is already in flight, the scope pass is queued behind it.

---

## Statistics

### Clear Stats
**Usage:** `clear stats`

---

### System Stats - Battery, Uptime, Queue Length and Debug Flags
**Usage:** 
- `stats-core`

**Serial Only:** Yes

---

### Radio Stats - Noise floor, Last RSSI/SNR, Airtime, Receive errors
**Usage:** `stats-radio`

**Serial Only:** Yes

---

### Packet stats - Packet counters: Received, Sent
**Usage:** `stats-packets`

**Serial Only:** Yes

---

### Read repeater telemetry history

Repeater firmware records one UTC-aligned sample every 30 minutes. Temperature
and battery voltage retain 336 samples (seven rolling days). GPS-capable builds
retain three GPS days by default and request a seven-day default at startup.
Builds without a GPS provider omit the GPS history commands to conserve flash.
The history and any runtime retention change are held in RAM and reset after a
reboot.

The feature is omitted from flash-constrained STM32 repeater images.

**Usage:**

- `get telemetry.temp [page]`
- `get telemetry.volt [page]`
- `get telemetry.gps [page]`
- `set telemetry.gps <days>`
- `get telemetry.tx`
- `set telemetry.tx <off|direct|path>`
- `set telemetry.tx schedule <off|1-30d>`
- `send telemetry.tx now`

**Parameters:**

- `page`: Page `1` is always newest. Temperature and voltage pages each hold
  24 hours and accept `1`-`7`. GPS pages each hold 12 hours and accept `1`
  through twice the current GPS retention in days. Omitting the page selects
  page `1`.
- `days`: Requested GPS retention from `1` through `30` days. Retention above
  three days uses heap memory. The allocator reduces the requested value as
  needed to leave at least 2048 bytes free and replies with the days and pages
  actually available. For example, a request can return
  `OK - telemetry.gps days=18 pages=36 requested=30`.
- `direct`: Send the binary temperature and voltage snapshots zero-hop to a
  neighboring MQTT observer.
- `path`: A comma-separated direct route using the same one-, two-, or
  three-byte hop hashes accepted by `set outpath`.
- `schedule`: Automatic interval in whole days. The default is `2d`; `off`
  retains the configured direct path for manual test sends.

Local serial and remote administrator CLI sessions can read the history.
Collection uses the MCU temperature, battery voltage, and an already-valid
onboard GPS fix. It does not wake GPS, so an off, sleeping, or unfixed GPS
produces a missing location sample without changing its power-saving schedule.

Replies contain `> ` followed by standard padded Base64. After decoding, all
multi-byte integers are little-endian. Packed fields are written most
significant bit first, oldest sample first.

Use the browser-based [Telemetry history decoder](telemetry_decoder.md) to
turn a reply into a timestamped table or downloadable CSV without uploading
the data.

`telemetry.tx` is disabled by default. Its schedule and direct path are stored
across reboots. Configuring `direct` or a routed path enables the default `2d`
schedule; `set telemetry.tx schedule` changes it from one through 30 days or
turns it off. An automatic run waits until 165 half-hour positions are
available, then sends one maximum-size temperature packet and one maximum-size
voltage packet. GPS is never included in this raw transmission. The history
remains boot-local, so the first scheduled pair after a reboot needs about
82.5 hours to fill. On the default two-day interval, each 82.5-hour packet
overlaps the previous packet by 34.5 hours. A queue failure is retried after
30 minutes only for the packet that did not queue; successfully queued packets
are not duplicated.

`send telemetry.tx now` is an administrator-only test action. It immediately
queues one temperature and one voltage direct packet over the configured path,
using all currently available positions up to 165. It works while the schedule
is off and does not move the next scheduled send time. RAW_CUSTOM direct
packets do not enter the normal encrypted direct-message retry mechanism.

Full snapshots are 184-byte RAW_CUSTOM payloads. Manual tests can be shorter
while history fills. Both formats are binary, not Base64 and not encrypted:

| Bytes | Meaning |
|---|---|
| `0`-`3` | ASCII magic `TTB1` for temperature or `TVB1` for voltage |
| `4`-`11` | First eight bytes of the repeater public key |
| `12`-`15` | First sample UTC epoch, unsigned 32-bit little-endian |
| `16`-`17` | Sample interval in minutes, unsigned 16-bit little-endian (`30`) |
| `18` | Sample count (`1`-`165`) |
| `19` onward | Oldest-to-newest encoded samples for the selected series |

Temperature codes reserve `0` for no reading, `1` for below `-50 C`, and `2`
for above `+77 C`. Codes `3` through `130` represent exact whole degrees from
`-50 C` through `+77 C`; decode them as `code - 53`. Voltage codes use the
same encoding as the paged voltage payload documented below.

Match bytes `4`-`11` to the first 16 hex characters of the repeater public key
shown by its advert or `get public.key`. This compact identifier is useful for
association but is not authenticated and can be spoofed. An MQTT observer can
upload the received raw packet to LetsMesh Analyzer `/packets`; its packet hex
contains the direct-route header and path followed by this payload.

Temperature payload (`0x11`, 61 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x11` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`48`) |
| `7`-`18` | 48 packed 2-bit temperature statuses |
| `19`-`60` | 48 packed 7-bit temperatures |

Temperature status codes are `0` none, `1` value, `2` below range, and `3`
above range. For status `1`, the 7-bit temperature is an exact whole-degree
integer from `-50 C` through `+77 C`; decode it as `code - 50`. Low values use
code `0`, and high values use code `127`. The separate status map is required
because 7 bits contain exactly 128 codes, leaving no spare code for none, low,
or high when the complete range is represented at 1 C resolution. No
fractional temperature is stored or transmitted.

Voltage payload (`0x12`, 55 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x12` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`48`) |
| `7`-`54` | 48 8-bit voltage codes |

Voltage codes reserve `0` for no reading, `1` for below `1.88 V`, and `255`
for above `4.40 V`. Codes `2`-`254` represent `1.88 V` through `4.40 V` in
`0.01 V` steps; decode millivolts as `1880 + (code - 2) * 10`.

GPS payload (`0x13`, 101 bytes):

| Bytes | Meaning |
|---|---|
| `0` | Format/type `0x13` |
| `1`-`4` | First sample UTC epoch, unsigned 32-bit |
| `5` | Sample interval in minutes (`30`) |
| `6` | Sample count (`24`) |
| `7`-`10` | Page origin latitude in signed degrees times `10^7` |
| `11`-`14` | Page origin longitude in signed degrees times `10^7` |
| `15` | Origin sample index, or `255` when the page has no GPS fix |
| `16` | Flags; bit 0 means at least one differential was clipped |
| `17`-`100` | 24 records: signed 14-bit north then signed 14-bit east |

GPS differentials use signed 14-bit two's-complement values at 10-meter
resolution and are applied to the preceding decoded valid point. The origin
sample begins at the header coordinates. A no-fix slot encodes `0,0` and does
not advance the reference; a stationary valid fix also quantizes to `0,0`.
When a page has no fixes, its origin is `0,0`, origin index is `255`, and all
differentials are `0,0`. Values outside `-8192` through `8191` are clipped and
set flag bit 0.

---

## Logging

Builds compiled with `MESH_PACKET_LOGGING` emit one `RAW:` line for every
received radio frame. Serial output uses backpressure: if a connected host
temporarily stops reading, packet processing waits for USB transmit space
instead of silently omitting the record. A disconnected host cannot retain an
unbounded capture, so logging deployments should keep the reader attached and
draining the serial port.

Ordinary `-logging-` artifacts keep packet logging separate from LoRa OTA.
Use the separately named `-ota-` artifact when LoRa OTA is required. A
`-full-logging-ota-` artifact is intentionally the exception: it combines
logging with LoRa OTA and the expanded FULL feature set, while MQTT remains
disabled.

### Begin capture of rx log to node storage
**Usage:** `log start`

---

### End capture of rx log to node storage
**Usage:** `log stop`

---

### Erase captured log
**Usage:** `log erase`

---

### Print the captured log to the serial terminal
**Usage:** `log`

**Serial Only:** Yes

---

## Info

### Get the Version
**Usage:** `ver`

---

### Show the hardware name
**Usage:** `board`

---

## Configuration

### Radio

#### View or change this node's radio parameters
**Usage:**
- `get radio`
- `set radio <freq>,<bw>,<sf>,<cr>`

**Parameters:**
- `freq`: Frequency in MHz
- `bw`: Bandwidth in kHz. Most targets allow `7.8`, `10.4`, `15.6`, `20.8`, `31.25`, `41.7`, `62.5`, `125`, `250`, `500`. LR1110 targets allow `62.5`, `125`, `250`, `500`.
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)

**Set by build flag:** `LORA_FREQ`, `LORA_BW`, `LORA_SF`, `LORA_CR`

**Default:** `869.525,250,11,5`

**Note:** Requires reboot to apply

---

#### View or change this node's transmit power
**Usage:**
- `get tx`
- `set tx <dbm>`

**Parameters:**
- `dbm`: Power level in dBm (1-22)

**Set by build flag:** `LORA_TX_POWER`

**Default:** Varies by board

**Notes:** This setting only controls the power level of the LoRa chip. Some nodes have an additional power amplifier stage which increases the total output. Refer to the node's manual for the correct setting to use. **Setting a value too high may violate the laws in your country.**

---

#### Change the radio parameters for a set duration
**Usage:** 
- `tempradio <freq>,<bw>,<sf>,<cr>,<timeout_mins>`

**Parameters:**
- `freq`: Frequency in MHz (150-2500)
- `bw`: Bandwidth in kHz (same allowed values as `set radio`)
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)
- `timeout_mins`: Duration in minutes (must be > 0)

**Note:** This is not saved to preferences and will clear on reboot

---

#### Schedule radio parameter changes
**Usage:**
- `set radioat <freq>,<bw>,<sf>,<cr>,<start_time>`
- `get radioat [n|all]`
- `del radioat [n|all]`
- `set tempradioat <freq>,<bw>,<sf>,<cr>,<start_time>,<end_time>`
- `get tempradioat [n|all]`
- `del tempradioat [n|all]`

**Parameters:**
- `freq`: Frequency in MHz (150-2500)
- `bw`: Bandwidth in kHz (same allowed values as `set radio`)
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)
- `start_time`: Unix epoch time when the setting starts
- `end_time`: Unix epoch time when a temporary setting reverts
- `n`: Scheduled entry number from `get radioat` or `get tempradioat`

**Notes:**
- `get radioat` and `get tempradioat` list all entries when `n` is omitted.
- `del radioat` and `del tempradioat` delete all entries when `n` is omitted.
- Each queue supports 3 entries. Scheduled entries are not saved across reboot.
- `radioat` saves the new radio preferences when it fires. `tempradioat` applies temporarily, then reverts to the saved radio preferences.

---

#### View or change this node's frequency
**Usage:**
- `get freq`
- `set freq <frequency>`

**Parameters:**
- `frequency`: Frequency in MHz

**Default:** `869.525`

**Note:** Requires reboot to apply
**Serial Only:** `set freq <frequency>`

---

#### View or change this node's rx boosted gain mode (SX12xx and LR1110, v1.14.1+)
**Usage:**
- `get radio.rxgain`
- `set radio.rxgain <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** Target-specific. Most SX1262 and LR1110 targets default to `on`;
Station G2/G3 targets default to `off`.

**Notes:**
- The saved setting is applied immediately and persists across reboots.
- Periodic AGC resets restore the saved runtime setting; they do not replace it
  with the target's compile-time default.
- Existing installations retain their previously saved value after an upgrade.

---

#### View or change RX duty-cycle power saving
**Usage:**
- `get radio.rxps`
- `get rxps.wd`
- `set radio.rxps off`
- `set radio.rxps on`
- `set radio.rxps conservative`
- `set radio.rxps balanced`
- `set radio.rxps <1-10>`
- `set radio.rxps level <1-10>`
- `set radio.rxps level <1-10> preamble <16|32>`
- `set radio.rxps <rx_us> <sleep_us>`

**Parameters:**
- `rx_us`, `sleep_us`: Receive and sleep durations in microseconds (`1000`-`30000000`).
- `level`: A power-saving level from `1` (most conservative) to `10` (least power saving).
- `preamble`: LoRa preamble length in symbols; `16` or `32`.

**Notes:**
- `get rxps.wd` reports the RXPS watchdog's soft and hard recovery counts.
- `on` and `conservative` select level `1` with a 16-symbol preamble; `balanced` selects level `5` with a 16-symbol preamble.
- Level-based settings automatically recalculate their timings when the spreading factor or bandwidth changes. Custom `<rx_us> <sleep_us>` timings remain fixed.
- The selected mode is applied immediately, persisted, and restored after reboot.

---

#### View or change the LoRa FEM receive-path gain state on supported boards
**Usage:**
- `get radio.fem.rxgain`
- `set radio.fem.rxgain <state>`

**Parameters:**
- `state`: `on`|`off`

**Notes:**
- This controls the external LoRa FEM receive-path LNA where the board supports it.
- This is separate from `radio.rxgain`, which controls the radio chip receive gain mode.

---

#### View or change the LoRa FEM transmit-path gain state on supported boards
**Usage:**
- `get radio.fem.txgain`
- `set radio.fem.txgain <state>`

**Parameters:**
- `state`: `on`|`off`

**Notes:**
- This controls a software-selectable external LoRa FEM transmit gain where the board supports it.
- On Station G3, remove the PA PL1 jumper to allow software control. `on` selects PA PL1 high/short and `off` selects PA PL1 low/open. The PA PL2 hardware jumper determines whether this switches between power levels 1/3 or 2/4.
- Select an operating level and SX1262 transmit power that comply with local RF limits and the Station G3 power-supply requirements.
- The setting is saved immediately, but on Station G3 the level is applied to the hardware at the start of the next transmit, so that the PA supply rail is never re-targeted while the PA is being driven. `get` reports the configured state, which may lead the hardware until the node next transmits.

---

### System

#### View or change this node's name
**Usage:**
- `get name`
- `set name <name>`

**Parameters:**
- `name`: Node name

**Set by build flag:** `ADVERT_NAME`

**Default:** Varies by board

**Note:** Advertised names can use up to 23 bytes when location is included and 31 bytes otherwise. Emoji and Unicode characters may take more than one byte. Names that exceed the available advert space are truncated at a valid UTF-8 code point boundary.

---

#### View or change this node's latitude
**Usage:**
- `get lat`
- `set lat <degrees>`

**Set by build flag:** `ADVERT_LAT`

**Default:** `0`

**Parameters:**
- `degrees`: Latitude in degrees

---

#### View or change this node's longitude
**Usage:**
- `get lon`
- `set lon <degrees>`

**Set by build flag:** `ADVERT_LON`

**Default:** `0`

**Parameters:**
- `degrees`: Longitude in degrees

---

#### View or change this node's identity (Private Key)
**Usage:**
- `get prv.key`
- `set prv.key <private_key>`

**Parameters:**
- `private_key`: Private key in hex format (64 hex characters)

**Serial Only:**
- `get prv.key`: Yes
- `set prv.key`: No

**Note:** Requires reboot to take effect after setting

---

#### Change this node's admin password
**Usage:**
- `password <new_password>`

**Parameters:**
- `new_password`: New admin password

**Set by build flag:** `ADMIN_PASSWORD`

**Default:** `password`

**Note:** Command reply echoes the updated password for confirmation.

**Note:** Any node using this password will be added to the admin ACL list.

---

#### View or change this node's guest password
**Usage:**
- `get guest.password`
- `set guest.password <password>`

**Parameters:**
- `password`: Guest password

**Set by build flag:** `ROOM_PASSWORD` (Room Server only)

**Default:** `<blank>`

---

#### View or change this node's owner info
**Usage:**
- `get owner.info`
- `set owner.info <text>`

**Parameters:**
- `text`: Owner information text

**Default:** `<blank>`

**Note:** `|` characters are translated to newlines

**Note:** Requires firmware 1.12+

---

#### Fine-tune the battery reading
**Usage:**
- `get adc.multiplier`
- `set adc.multiplier <value>`

**Parameters:**
- `value`: ADC multiplier (0.0-10.0)

**Default:** `0.0` (value defined by board)

**Note:** Returns "Error: unsupported by this board" if hardware doesn't support it

---

#### Send a repeater flood text
**Usage:**
- `send text.flood <message>`

**Parameters:**
- `message`: Text to send to the shared `#repeaters` flood channel, prefixed with this node's name. Any `:` in the node name is sent as `;` so the prefix delimiter stays unambiguous.

**Example:**
```
send text.flood checking ridge link
```

---

#### View or change battery alert state
**Usage:**
- `get battery.alert`
- `get battery.alert.region`
- `set battery.alert on [region]`
- `set battery.alert off`

**Parameters:**
- `region`: Optional named region scope. When omitted, the repeater selects the single deepest (most narrow) named region in the configured hierarchy. If multiple regions tie for deepest, specify one explicitly.

**Defaults:**
- `battery.alert`: `off`
- `battery.alert.region`: `<unset>`

**Notes:**
- Enabling fails until at least one usable named region is defined. Alerts are never sent as unscoped floods. If the selected region is later removed, alerts stop until battery alerts are enabled again with a valid region.
- Region hierarchy edits are not persistent until `region save` is run. After `region def west pnw wa w-wa sea`, run `region save` before enabling the alert if the hierarchy must survive a reboot.
- A region must have a usable transport key. Public named regions derive one automatically; a private region without an available key is rejected.
- The first alert is suppressed until the repeater has been up for at least 30 minutes. After that, the repeater checks every 30 minutes and sends low-battery warnings to the `#repeaters` channel in the selected region.
- Once an alert finishes transmitting, another battery alert is suppressed for at least 12 hours. A queue rejection, stale-queue drop, or radio send failure does not start the cooldown. Battery recovery or toggling alerts off and back on does not bypass a completed alert's cooldown during the same boot.
- With `region def west pnw wa w-wa sea`, `set battery.alert on` selects `sea`; `set battery.alert on w-wa` overrides that default.
- `get battery.alert.region` returns the selected scope, for example `> sea`.
- The battery check never requests a wake earlier than its 30-minute deadline. If the normal loop is already awake when that deadline has elapsed, the check is effectively free of an additional wake. Time in light/event sleep counts toward the startup delay, and a pending alert keeps the repeater awake until the packet is handled.

---

#### View or change battery alert thresholds
**Usage:**
- `get battery.alert.low`
- `set battery.alert.low <1-100>`
- `get battery.alert.critical`
- `set battery.alert.critical <0-99>`

**Defaults:**
- `battery.alert.low`: `20`
- `battery.alert.critical`: `10`

**Note:** The low threshold must be greater than the critical threshold. Alerts at or below the critical threshold use `CRITICAL BATTERY` in the message; both severities use the same 12-hour resend cooldown.

---

#### Enable or disable the RX inactivity watchdog (Repeater Only)
**Usage:**
- `get rx.watchdog`
- `set rx.watchdog on`
- `set rx.watchdog off`

**Default:** `off`

**Notes:**
- When enabled, the first check is due after a full 12-hour observation window. The repeater then checks roughly every 12 hours and reboots if it has not successfully received a radio packet during the preceding 12 hours.
- Enabling the watchdog starts a new 12-hour observation window. Rebooting also starts a new window, so a quiet mesh can reboot no more often than once every 12 hours.
- The check reuses the radio driver's existing last-receive timestamp. It does not poll, sample, or wake the radio or CPU. A due check waits for the next normal loop/wake, so its actual cadence can drift around the 12-hour target. With RX power saving enabled, packets received during normal listening windows count as activity; the watchdog does not alter the RX/sleep timing.

---

#### Enable or disable the nRF52 system watchdog
**Usage:**
- `get system.watchdog`
- `set system.watchdog on`
- `set system.watchdog off`

**Default:** `on`

**Notes:**
- This nRF52-only hardware watchdog resets the device if the application loop stops for 60 seconds, including an indefinite SoftDevice flash-write wait.
- Enabling takes effect without a reboot.
- The nRF52 hardware cannot stop a watchdog after it has started. Disabling is persisted immediately, then the current firmware stops feeding it so the board performs one watchdog restart within 60 seconds. It remains off after that restart.
- This setting is nRF52-only. The SoftDevice flash deadlock does not apply to ESP32, whose existing watchdog behavior is unchanged.

---

#### Estimate and correct infrastructure-node time after startup

**Usage:**
- `get clock.sync`
- `get clock.sync.status`
- `get clock.sync.status.table`
- `get clock.sync.status.<1-16>`
- `get clock.sync.mesh`
- `set clock.sync.mesh <on|off>`
- `get clock.sync.mesh.edge`
- `set clock.sync.mesh.edge <on|off>`
- `clock.sync.mesh now`
- `get clock.sync.internet`
- `set clock.sync.internet <on|off>`
- `get clock.sync.drift`
- `set clock.sync.drift <30-86400>`
- `get clock.sync.samples`
- `set clock.sync.samples <3-16>`

**Defaults:**
- `clock.sync.mesh`: `on` for all repeater, sensor, and room-server builds
- `clock.sync.mesh.edge`: `on`
- `clock.sync.internet`: `off`
- `clock.sync.drift`: `3600` seconds
- `clock.sync.samples`: `9`

When either source is enabled, the node makes its first clock-bootstrap
attempt after 30 minutes of uptime, or immediately when the configured number
of fresh evidence sources has been collected, whichever comes first. A
successful estimate changes the RTC only when the absolute difference is
**greater than** `clock.sync.drift`; correction can move the clock forward or
backward. A valid estimate within the threshold counts as a successful sync
without changing the clock. Seven days after each successful estimate, the
node evaluates time again; the seven-day deadline therefore starts from the
last successful estimate rather than from boot. This is a lazy uptime deadline:
the check runs on the first normal loop/wake after it becomes due and does not
wake the device by itself. If no source or consensus is available, the node
retries every 30 minutes, and newly collected evidence triggers another
immediate evaluation once the configured source count is present. Every reboot
starts with the initial bootstrap attempt. An existing saved setting always
overrides the platform default.

`clock.sync.mesh now` bypasses the startup/seven-day deadline and queues a
LoRa-only consensus evaluation on the next normal loop, even when the internet
source is also enabled. It uses any currently fresh samples without clearing the
16-slot table. If there is not yet enough evidence, mesh collection remains open
and the next attempt follows the normal 30-minute retry. The command requires
`clock.sync.mesh` to be on and does not bypass CLI, GPS, or NTP suppression; it
also retains the normal quorum, timestamp-validity, and drift checks.
The separate `clock` command only displays the current RTC and does not request
a synchronization attempt.

`clock.sync.mesh` collects signature-verified advert timestamps and MAC-valid,
decrypted Public-channel plain-text timestamps. In normal path mode, collection
occurs only after the packet passes every forwarding filter. Sources are
deduplicated by advert public key or case-insensitive Public-channel display
name. Every fresh sample must also have a different full received path; all
direct, zero-hop receptions count as the same empty path. This prevents repeated
packets or multiple names arriving over one route from increasing the vote
count.

For a node at the edge of the network where every packet arrives through one
relay path, `set clock.sync.mesh.edge on` changes the evidence requirement from
distinct receive paths to distinct sources. Signature-verified adverts are
deduplicated by public key, and Public-channel timestamps are deduplicated by
case-insensitive display name. Repeated packets from one source still count
once. Edge mode observes this verified evidence on the receive path, independently
of the forwarding decision, so `repeat off` and forwarding filters do not prevent
clock collection. Other packet types are observed normally but cannot be clock
evidence because they do not provide a suitable authenticated Unix timestamp.
Changing edge mode clears the in-memory sample table so evidence collected under
the other policy is not reused. The setting is persistent and defaults on.

At least the configured number of distinct fresh evidence sources (nine by
default) and a strict majority of all fresh samples must fall within ten minutes
of the median. In normal mode each source must use a distinct receive path. In
edge mode signed adverts are distinct by public key and Public-channel messages
are distinct by display name, but all may use the same receive path. The
effective quorum is therefore the larger of
`clock.sync.samples` and half the fresh sample count plus one. For example, a
9-vs-7 split can succeed but an 8-vs-8 split cannot. The median is used.
`clock.sync.samples` accepts `3` through `16`; samples older than two hours are
ignored. Status reports `mode=paths` or `mode=edge` and labels the collected
evidence as `paths` or `sources`. It reports `reason=need-more-paths` or
`reason=need-more-sources` when fewer than the configured number exist, and
`reason=no-consensus` when enough evidence exists but the effective quorum does
not agree. Mesh collection begins immediately after boot. Following a
successful estimate, it resumes two hours before the next seven-day deadline so
only evidence that can still be fresh at evaluation time is processed.

`get clock.sync.status` reports whether the clock was set and a `reason` when it
was not. Common reasons include `waiting-deadline`, `need-more-sources`,
`need-more-paths`, `no-consensus`, `within-drift`, `mesh-off`, and suppression
by CLI, GPS, or internet time. It also reports whether collection is active, the
fresh evidence count, the number of occupied table slots, and the next
evaluation deadline.

`get clock.sync.status.table` shows the active sample table in compact form.
Each item is `slot:type:id-prefix:age`, where type `A` is a signed advert and
type `P` is a Public-channel message. A trailing `!` marks a stale sample. If
the compact reply is truncated, query any slot with
`get clock.sync.status.<1-16>`. The detail view reports the full source and path
hashes, age-adjusted epoch, difference from the local clock, and freshness.

A timestamp is eligible for a clock-sync sample only when it falls between the
UTC build epoch embedded by `build.sh` and that time plus ten calendar years.
Direct developer builds that bypass `build.sh` fall back to the compiler
timestamp. Validation happens before a slot is selected or written, so an
advert or Public-channel timestamp outside that window is not recorded as a
clock sample.

Before voting, the node advances each packet timestamp by an estimated transit
time. The estimate sums the radio airtime at the original packet length and at
each progressively longer relay length, plus the expected midpoint of the
random flood-forward delay at every prior hop. The normal elapsed time since
the radio recorded local receipt is then added when consensus is evaluated, so
local signature/decryption/filter processing time is included too. This is
better than using hop count alone because LoRa airtime changes with packet
length and radio settings. Transit compensation is capped at the ten-minute
consensus window. It cannot know sender queueing, channel contention, or a
remote relay's non-matching `txdelay`, so the consensus window and median still
absorb residual error.

If a `clock sync` or `time <epoch>` CLI command successfully sets the clock, or
a GPS provider writes a valid GPS time, LoRa-derived clock collection and
correction are suppressed for the rest of that boot. Turning
`clock.sync.mesh` off and back on does not clear this safety latch; only a
reboot does. `get clock.sync.mesh` and `get clock.sync.status` report whether
CLI or GPS time caused the suppression. On a WiFi MQTT build, a successful NTP
sync is authoritative and also suppresses LoRa correction for the rest of that
boot. Source selection starts fresh after a reboot, so LoRa remains the fallback
when NTP cannot obtain internet time during that boot.

Public-channel display names are not authenticated and can be spoofed. Received
path hashes are also truncated, unauthenticated routing hints; requiring unique
paths prevents ordinary duplicate-route inflation but is not a cryptographic
identity check. Signed adverts authenticate the advert contents but do not
prove that the advertising node's own clock is correct. Mesh time is therefore
a consensus estimate, not an authoritative time service. Edge mode intentionally
gives up receive-path diversity. Public-channel display names can be spoofed, so
one sender can claim multiple names and inflate the edge-mode vote count.

`clock.sync.internet` is available on WiFi MQTT repeater-observer builds. Its
initial and seven-day queries run on the MQTT/WiFi task and are read-only until
the repeater applies the configured drift test. Failed queries retry after 30
minutes. On other infrastructure-node builds, the preference can be stored but
status reports that internet time is unavailable. MQTT builds retain their
existing startup NTP behavior required for MQTT/TLS/JWT operation; this setting
controls the additional delayed drift checks. Startup NTP is always preferred when it
succeeds, regardless of this setting.

Sensor and room-server builds support mesh clock consensus and report
`clock.sync.internet` as unavailable. Changing any `clock.sync.*` setting starts
a new attempt for the current boot.
Settings are persistent in `/clock_sync`; samples and schedule state are not.

A backward correction is intentionally allowed, but peers that already recorded
a later timestamp from this node may temporarily reject its lower timestamps as
replays until corrected time passes the previously observed value.

**Example:**
```text
set clock.sync.drift 1800
set clock.sync.samples 3
set clock.sync.mesh on
set clock.sync.mesh.edge on
clock.sync.mesh now
get clock.sync.status
```

---

#### View this node's public key
**Usage:** `get public.key`

---

#### View this node's firmware version
**Usage:** `ver`

---

#### View this node's configured role
**Usage:** `get role`

---

#### View or change this node's power saving flag (Repeater Only)
**Usage:**
- `powersaving`
- `powersaving on`
- `powersaving off`

**Parameters:** 
- `on`: enable power saving
- `off`: disable power saving

**Default:** `off`

**Note:** When enabled, device enters sleep mode between radio transmissions. Enabling is refused from the local serial console or while an active USB serial data connection is detected; USB power alone does not block power saving.

---

#### Control an exposed GPIO

**Availability:** ESP32 Repeater, Room Server, Bridge, and Sensor firmware. Companion firmware does not expose these commands. On nRF52, the commands are enabled only for Sensor builds on the Heltec T096, ProMicro, RAK3401, and RAK4631. GPIO expanders are not supported.

**Usage:**

- `get gpio` - list the Arduino pin numbers this firmware build permits
- `get gpio state`, `get gpio states`, or `get gpio status` - list every available pin currently controlled by the user (anything not in `reset`)
- `get gpio state <pin>` - show one pin's state; `states` and `status` are accepted here too
- `get gpio <pin>` - show `on`, `off`, or `reset`, plus any pending timed transition
- `set gpio <pin> on`
- `set gpio <pin> off`
- `set gpio <pin> reset`
- `set gpio <pin> <on|off> <duration> <on|off|reset>`

**Examples:**

- `set gpio 16 on 30 off` - drive GPIO16 high for 30 seconds, then drive it low
- `set gpio 16 on 5ms off` - drive GPIO16 high for 5 milliseconds, then drive it low
- `set gpio 16 off 5 reset` - drive GPIO16 low for 5 seconds, then return it to high impedance

An integer duration has seconds as its default unit, so `5` means 5 seconds. Add `ms` for milliseconds (`5ms`); an explicit `s` suffix is also accepted (`5s`). The maximum duration is 24 hours (86,400 seconds or 86,400,000 milliseconds). `on` or `off` without a duration remains in that state until another command, reset, or reboot.

`reset` changes the pin to an input with no pull resistor (high impedance). It does not reboot the node. It also cancels any pending timer for that pin. A new timed command for the same pin cancels and replaces the previous timer. GPIOs begin in `reset`; states and timers are not saved and are lost on reboot.

The immediate reply confirms the applied state and any pending transition. When a timed transition finishes, a command issued over the authenticated remote CLI receives a second report such as `> GPIO 16 timer complete: off`. Retries of the same authenticated timed command are recognized and do not restart its countdown.

The pin number is the Arduino pin number used by that target (the normal GPIO number on ESP32 and the board's `D`/pin index on nRF52). The available-pin list is build-specific. Radio, flash/PSRAM, USB, serial console, display, GPS, I2C, buttons, LEDs, battery measurement, power control, bridge, Ethernet, watchdog, and other pins claimed by the firmware are rejected. A pin must also be physically broken out on your board; `get gpio` cannot detect wiring or an attached peripheral that is not represented by the firmware configuration.

**Electrical warning:** GPIOs use 3.3 V logic and have limited drive current. Do not power a relay, motor, solenoid, or other load directly from a GPIO. Use a suitable transistor, MOSFET, optocoupler, or driver with the required protection components.

---

### Routing

#### View or change this node's repeat flag
**Usage:**
- `get repeat`
- `set repeat <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** `flood.channel.data on`; `flood.channel.data.hops h=all`

---

#### View or change this node's advert path hash size
**Usage:**
- `get path.hash.mode`
- `set path.hash.mode <value>`

**Parameters:**
- `value`: Path hash size (0-2)
  - `0`: 1 Byte hash size (256 unique ids)[64 max flood]
  - `1`: 2 Byte hash size (65,536 unique ids)[32 max flood]
  - `2`: 3 Byte hash size (16,777,216 unique ids)[21 max flood]
  - `3`: DO NOT USE (Reserved) 

**Default:** `0`

**Note:** the 'path.hash.mode' sets the low-level ID/hash encoding size used when the repeater adverts. This setting has no impact on what packet ID/hash size this repeater forwards, all sizes should be forwarded on firmware >= 1.14. This feature was added in firmware 1.14

**Temporary Note:** adverts with ID/hash sizes of 2 or 3 bytes may have limited flood propagation in your network while this feature is new as v1.13.0 firmware and older will drop packets with multibyte path ID/hashes as only 1-byte hashes are supported. Consider your install base of firmware >=1.14 has reached a criticality for effective network flooding before implementing higher ID/hash sizes. 

---

#### View or change this node's loop detection
**Usage:**
- `get loop.detect`
- `set loop.detect <state>`

**Parameters:**
- `state`: 
  - `off`: no loop detection is performed
  - `minimal`: packets are dropped if repeater's ID/hash appears 4 or more times (1-byte), 2 or more (2-byte), 1 or more (3-byte)
  - `moderate`: packets are dropped if repeater's ID/hash appears 2 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  - `strict`: packets are dropped if repeater's ID/hash appears 1 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  
**Default:** `off`

**Note:** When it is enabled, repeaters will now reject flood packets which look like they are in a loop. This has been happening recently in some meshes when there is just a single 'bad' repeater firmware out there (probably some forked or custom firmware). If the payload is messed with, then forwarded, the same packet ends up causing a packet storm, repeated up to the max 64 hops. This feature was added in firmware 1.14

**Example:** If preference is `loop.detect minimal`, and a 1-byte path size packet is received, the repeater will see if its own ID/hash is already in the path. If it's already encoded 4 times, it will reject the packet.  If the packet uses 2-byte path size, and repeater's own ID/hash is already encoded 2 times, it rejects. If the packet uses 3-byte path size, and the repeater's own ID/hash is already encoded 1 time, it rejects. 

---

#### View or change the retransmit delay factor for flood traffic
**Usage:**
- `get txdelay`
- `set txdelay <value>`

**Parameters:**
- `value`: Transmit delay factor (0-2)

**Default:** `0.5`

**Note:** When multiple nearby repeaters all hear the same flood packet, each waits a random amount of time before retransmitting to avoid simultaneous collisions. This factor scales the size of that random window. Higher values reduce collision risk at the cost of added latency. `0` disables the window entirely.

---

#### View or change the retransmit delay factor for direct traffic
**Usage:**
- `get direct.txdelay`
- `set direct.txdelay <value>`

**Parameters:**
- `value`: Direct transmit delay factor (0-2)

**Default:** `0.2`

**Note:** Same collision-avoidance random window as `txdelay`, but applied to direct (non-flood, routed) traffic. The default is lower because direct packets are addressed to a specific next hop, so far fewer nodes compete to retransmit them.

---

#### [Experimental] View or change the processing delay for received traffic
**Usage:**
- `get rxdelay`
- `set rxdelay <value>`

**Parameters:**
- `value`: Receive delay base (0-20)

**Default:** `0.0`

**Note:** When enabled, repeaters that received a flood packet with a weak signal are held in a delay queue before processing, while those that received it with a strong signal process it immediately. This gives strong-signal paths forwarding priority. By the time weak-signal nodes process their copy, the packet may have already propagated and will be suppressed as a duplicate, reducing redundant retransmissions.

---

#### View or change the duty cycle limit
**Usage:**
- `get dutycycle`
- `set dutycycle <value>`

**Parameters:**
- `value`: Duty cycle percentage (1-100)

**Default:** `50%` (equivalent to airtime factor 1.0)

**Examples:**
- `set dutycycle 100` - no duty cycle limit
- `set dutycycle 50` - 50% duty cycle (default)
- `set dutycycle 10` - 10% duty cycle
- `set dutycycle 1` - 1% duty cycle (strictest EU requirement)

> **Note:** Added in firmware v1.15.0

---

#### View or change the airtime factor (duty cycle limit)
> **Deprecated** as of firmware v1.15.0. Use [`get/set dutycycle`](#view-or-change-the-duty-cycle-limit) instead.

**Usage:**
- `get af`
- `set af <value>`

**Parameters:**
- `value`: Airtime factor (0-9). After each transmission, the repeater enforces a silent period of approximately the on-air transmission time multiplied by the value. This results in a long-term duty cycle of roughly 1 divided by (1 plus the value). For example:
  - `af = 1` -> ~50% duty
  - `af = 2` -> ~33% duty
  - `af = 3` -> ~25% duty
  - `af = 9` -> ~10% duty
  You are responsible for choosing a value that is appropriate for your jurisdiction and channel plan (for example EU 868 Mhz 10% duty cycle regulation).

**Default:** `1.0`

---

#### View or change the local interference threshold
**Usage:**
- `get int.thresh`
- `set int.thresh <value>`

**Parameters:**
- `value`: Interference threshold value

**Default:** `0.0`

---

#### Enable or disable hardware Channel Activity Detection (CAD)
**Usage:**
- `get cad`
- `set cad <on|off>`

**Description:** When enabled, the radio performs a hardware Channel Activity Detection scan before transmitting and defers if the channel is busy. Runs independently of `int.thresh` - either, both, or none may be active.

The Cascade firmware profile defaults CAD to `on`; target-default builds continue to default it to `off`.
The repeater applies the saved toggle to the radio during its periodic
noise-floor service. With one runnable packet, a busy result uses the normal
CAD retry delay and allows roughly four seconds of continuous busy results.
As the runnable transmit queue grows, both delays are divided by its depth:
retry spacing will not fall below 50 ms and the busy ceiling will not fall
below 500 ms. Reaching the busy ceiling records a CAD-timeout error and
attempts the next queued transmission rather than waiting indefinitely.
Future-scheduled packets do not accelerate CAD. `get cad` includes the
hardware busy-result count.

**Parameters:**
- `on|off`: Enable or disable hardware CAD

**Default:** `off`

---

#### View or change the AGC Reset Interval
**Usage:**
- `get agc.reset.interval`
- `set agc.reset.interval <value>`

**Parameters:**
- `value`: Interval in seconds rounded down to a multiple of 4 (17 becomes 16). 0 to disable.

**Default:** `0.0`

---

#### View or change the radio watchdog interval (MQTT observer only)
**Usage:**
- `get radio.watchdog`
- `set radio.watchdog <minutes>`

**Parameters:**
- `minutes`: `0` to disable, or `1-120` minutes

**Default:** `5`

**Note:** This watchdog belongs to the MQTT observer runtime and is not
available on a standalone FULL repeater. On quiet meshes, increasing it can
reduce false recoveries when no traffic is expected.

---

#### Enable or disable Multi-Acks support
**Usage:**
- `get multi.acks`
- `set multi.acks <state>`

**Parameters:**
- `state`: `0` (disable) or `1` (enable)

**Default:** `0`

---

#### View or change the flood advert interval
**Usage:**
- `get flood.advert.interval`
- `set flood.advert.interval <hours>`

**Parameters:**
- `hours`: Interval in hours (3-168)

**Default:** `12` (Repeater) - `0` (Sensor)

---

#### View or change the zero-hop advert interval
**Usage:**
- `get advert.interval`
- `set advert.interval <minutes>`

**Parameters:**
- `minutes`: Interval in minutes rounded down to the nearest multiple of 2 (61 becomes 60) (60-240)

**Default:** `0`

---

#### Limit the number of hops for a flood message
**Usage:**
- `get flood.max`
- `set flood.max <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64)

**Default:** `64`

---

#### Limit the number of hops for an unscoped flood message
**Usage:**
- `get flood.max.unscoped`
- `set flood.max.unscoped <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for a packet without a scope (no region set)

**Default:** `0xFF` - indicates it hasn't been set, will track flood.max until it is.

**Note:** An alternative to `region denyf *`, setting `flood.max.unscoped` to a lower value such as `3` would allow for local unscoped messages to propagate, while preventing noisy neighbors from flooding a local region.

---

#### Limit the number of hops for an advert flood message
**Usage:**
- `get flood.max.advert`
- `set flood.max.advert <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for an advert packet

**Default:** `8`

---

#### Forward flood group data packets on repeaters
**Usage:**
- `get flood.channel.data`
- `get flood.channel.data.hops`
- `set flood.channel.data <on|off>`
- `set flood.channel.data.hops <all|1-7>`

**Parameters:**
- `on`: Retransmit received flood `GRP_DATA` channel packets.
- `off`: Do not retransmit received flood `GRP_DATA` channel packets.
- `all`: When `flood.channel.data` is `off`, block `GRP_DATA` at any received flood hop count.
- `1-7`: When `flood.channel.data` is `off`, repeat `GRP_DATA` at this hop count or lower and block longer paths.

**Default:** `flood.channel.data on`; `flood.channel.data.hops h=all`

**Forwarding behavior:** Repeater firmware only. The repeater still receives and
logs the packet when logging is enabled; this only blocks retransmission.
On generalized repeaters these commands manage an ordinary visible FPF7
`type=grp_data` drop row. `off` with `all` maps to `hops=all`; `off` with `N`
maps to `hops=N+1+`. The 240 KB compact FPF6 profiles retain the legacy hard
gate. Because it is an ordinary FPF7 row, a matching higher-priority `stop`
rule can exempt traffic from it. The compact `get flood.filter` list marks the
managed row with `~data`. Flood group text (`GRP_TXT`) is unaffected by this
setting.

`get flood.channel.data` includes the active hop gate as `h=all` or `h>N`.

---

#### Block selected flood channels with FPF7

The separate `flood.channel.block` command and 15-row table have been retired.
Generalized repeaters use the 63-row FPF7 forward phase for authenticated channel
blocks:

```text
set flood.rule type=any channel=#test hops=all drop
set flood.rule.2 type=any channel=#wardriving hops=5+ drop
set flood.rule type=any channel=9cd8fcf22a47333b591d96a2b848b73f hops=4+ drop
get flood.rule
del flood.rule.2
```

`type=any` with a channel condition can authenticate only `GRP_TXT` and
`GRP_DATA`, so it does not match other payload types. Use `hops=all` to block
at every received hop count. To preserve the old `h=N` meaning of repeating
through `N` hops and blocking longer paths, use `hops=N+1+`; old `h=4` is
therefore `hops=5+`.

New generalized repeater tables seed slot 2 with the second example. Existing
FCB2 rows are imported once into free FPF7 slots and the retired file is then
removed. The fixed-size STM32WL FPF6 build cannot match authenticated channels.

---

#### Force a transport scope onto floods
**Usage:**
- `get flood.channel.scope`
- `get flood.channel.scope.<n>`
- `set flood.channel.scope <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]`
- `set flood.channel.scope.<n> <channel|txt:*|login:*|other:*> <region|scope=name> [path=blacklist|path=bucket:1-6] [tx=slow]`
- `del flood.channel.scope.<n>`
- `del flood.channel.scope all`

**Parameters:**
- `n`: Slot number within the table compiled for the target. Roomy ESP32 builds
  provide `1-255`; DRAM-tight classic ESP32 LoRa-OTA repeaters and nRF52/other
  normal constrained builds provide `1-31`; very-tight STM32WL repeaters provide
  `1-15`; the no-PSRAM LilyGo T-LoRa V2.1 repeater/observer provides `1-4`.
- `channel`: `public`, a public `#channel`, or a 128/256-bit channel key in hex.
- `txt:*`: Unauthenticated fallback for otherwise-unmatched `GRP_TXT` and
  `GRP_DATA`. Plain `*` is an alias for `txt:*`.
- `login:*`: Type-based wildcard for the remote-login/admin family: `REQ`,
  `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH` (`0x00`, `0x01`, `0x02`,
  `0x07`, and `0x08`). It classifies the outer type; a transit repeater cannot
  authenticate whether a packet is actually part of a login session.
- `other:*`: Type-based wildcard for every remaining flood payload type,
  including flood-form TRACE, ACK, advert, multipart, control, OTA, reserved
  types, and raw custom.
- `region`: Existing named region with a usable transport key. A unique region
  name prefix is accepted; wildcard region `*` is not a scope target.
- `scope=<name>`: Regionless alternative to `region`. The public name is
  normalized with a leading `#`, and its 128-bit transport key is derived
  directly from that hashtag exactly as for `flood.filter scope=<name>`. It
  does not need to exist in the region list. Public names up to 30 characters
  are accepted; private `$` scopes are not.
- `path=blacklist`: Optional. Require the received path to match the passive
  `flood.filter.blacklist` ID table. No `flood.filter` drop row needs to be
  enabled. One exact listed ID qualifies a 3-byte path. A 2-byte path requires
  two matching received path entries, while a 1-byte path never qualifies.
- `path=bucket:<1-6>`: Optional alternative to `path=blacklist`. Match IDs in
  the selected persistent `flood.retry.bucket`. Each bucket holds up to 17
  three-byte IDs and remains usable when `flood.retry.bridge` is off. It uses
  the same 3-byte, 2-byte, and 1-byte thresholds as `path=blacklist`.
  `recent.repeater` freshness and `flood.retry.ignore` do not affect this
  passive match.
- `tx=slow`: Optional. Use an effective inbound `rxdelay` base of
  `max(2, configured rxdelay * 2)`, keep normal outbound queue priority, and
  schedule retransmission with the maximum supported `txdelay` factor of
  `2.0` after changing the scope. The default is fast; `tx=fast` may be
  supplied explicitly when replacing a slow row.

**Default:** No forced scopes.

Remote ACL permission `4` (region/scope manager) can use all `get`, `set`, and
`del flood.channel.scope` forms. Filter managers and other non-admin roles
cannot change this table.

Without `.n`, `set` updates the row for the same exact channel key or wildcard
class with the same path selector, otherwise it uses the first empty slot.
This permits an ordinary fallback and separate blacklist or bridge-bucket
rows for the same channel. With `.n`, it replaces that slot. The three
wildcard classes are independent and consume one slot each.
`get flood.channel.scope` reports active/total slot counts; use the numbered
form for row detail. Keyed rows are displayed by the first four bytes of their
derived channel hash because channel secrets are never returned. Regionless
targets are displayed with their normalized leading `#`.

This acts on received `ROUTE_TYPE_FLOOD` and
`ROUTE_TYPE_TRANSPORT_FLOOD` packets. An unscoped packet gains the configured
scope; an already-scoped packet has its existing transport codes replaced. For
`GRP_TXT` and `GRP_DATA`, all exact channel-key rows are tried first and must
validate the packet MAC/decryption. Matching path-qualified exact rows are
  tried before ordinary exact fallback rows. A region-backed row whose target
  is missing or unusable is skipped; later exact rows and then `txt:*` are tried.
Exact keyed rows with a usable target therefore beat `txt:*` regardless of
slot number. Within each wildcard class, path-qualified rows similarly precede
ordinary fallback rows. `login:*` and `other:*` select their non-overlapping
outer-type families without decrypting the payload. The lowest usable slot
wins within each priority tier.

Standard traceroute is direct-routed and is therefore outside this flood-only
table. A custom flood-form `TRACE` is treated like every other flood: an
applicable wildcard may rewrite it and region/unknown-code gates still apply.

On a match, the repeater sets the route to `ROUTE_TYPE_TRANSPORT_FLOOD`,
computes transport code 0 from the selected region or direct hashtag key and
packet payload, and sets transport code 1 to zero. This occurs before region
enforcement, forwarding filters, and the seen-packet lookup. For an
already-scoped packet, the selected code replaces both incoming transport-code
fields. Direct routes are never rewritten. A packet converted from unscoped is
no longer subject to `flood.max.unscoped`; all rewritten packets remain subject
to normal payload handling, `flood.max`, `flood.filter`,
loop detection, and moderation. Assigning a scope does
not make a packet type forwardable if the core would otherwise reject it. By
default, if the selected scope differs and the rewritten packet is accepted
for forwarding, its initial retransmission uses zero `txdelay` and the highest
outbound queue priority so the newly scoped copy can win at the next hop.
Adding `tx=slow` uses an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, keeps the ordinary queue priority, and uses
the maximum `txdelay` factor of `2.0` for the retransmission. As with ordinary
`txdelay`, the actual transmit delay is randomized from zero through the
resulting window; factor `2.0` gives a maximum of ten packet airtimes.
Neither mode preempts an active radio transmission or bypasses CAD and
airtime-budget limits. Selecting the scope already carried by the packet is a
no-op and does not grant special transmit treatment.

A region-backed target must be locally flood-allowed and remains subject to
the normal region gate. A `scope=<name>` target is trusted for this matched
receive pass even though it has no region-list entry, matching the behavior of
`flood.filter scope=<name>`. It does not create a region, consume a region
slot, or change the allow/deny state for unrelated packets carrying the same
transport code.

If a region-backed row's target has been removed or has no usable key, the repeater
tries the next applicable row. For group packets this means later authenticated
exact rows followed by `txt:*`; wildcard duplicates likewise fall through to
the next usable slot. When no usable mapping exists, the packet retains its
original unscoped or scoped route.

LoRa OTA remains functional when `other:*` is configured. OTA packets are
given that target's transport code, replacing an existing code when necessary,
but the OTA handler still accepts and re-floods them during the temporary-radio
window. A region target must allow flooding; a direct target follows the
regionless trust behavior above. The OTA core itself is dormant outside that
window; no default flood filter row is needed for that behavior. Forced scope
does not make OTA operate outside the window.

**Capacity cost:** Each rule slot retains its 36-byte runtime and persistent
record. A separate 32-byte-name table holds up to the smaller of the rule count
or 32 distinct regionless targets; very-tight STM32WL builds hold one reusable
direct target. Region-backed targets do not consume this table.
The four-slot minimum uses 272 bytes RAM and a 278-byte file; it has room for
the three wildcard classes plus one exact channel mapping. Very-tight 15-slot
builds use 572 bytes RAM and a 578-byte file. The 31-slot table uses 2,108
bytes RAM and a 2,114-byte file. Roomy ESP32 builds use 255 rule slots and 32
direct-target slots: 10,204 bytes RAM and a 10,210-byte file. Classic ESP32
LoRa-OTA builds that cannot afford the 255-rule table use 31 slots instead.
Both configured regions and regionless targets can be reused by any number of
rules.

**Duplicate behavior:** Mesh dedup hashes payload type and payload bytes; it
does not hash route type, transport codes, or the ordinary flood path. Adding
a transport scope therefore does not create a new duplicate identity. If the
same payload later arrives scoped, unscoped, or through a different region, it
is still the same seen packet. `TRACE` is the exception only in that its
encoded `path_len` byte is also hashed.

While equivalent non-TRACE flood copies are waiting in `rxdelay`, the normal
receive-quality timing still selects the packet to process, but that winner
receives a scope from the queued scoped copies with the same dedupe identity. If the copies
carry different locally allowed scopes, the scope from the shortest received
path wins. Unknown and denied transport codes are not candidates and therefore
cannot overwrite an unscoped winner. With equal path lengths, the deeper child
region wins because it is narrower. A remaining tie keeps queue order. The
winner's own path, SNR reading, and scheduled time are not changed, and an
already-scoped winner may have its code replaced by the better queued scope.

The comparison is deferred until dequeue so each copy retains its original
scope and path for arbitration. It can only use copies still present in
`rxdelay`; it cannot replace a packet that already won the dedupe race.
Flood-form TRACE participates in this arbitration; direct traceroute does not
enter the flood queue.

A packet that matches a fast `flood.channel.scope` or `flood.filter scope=`
action and needs its scope changed bypasses the inbound `rxdelay` queue. A
`tx=slow` row remains in that queue with twice the configured base, floored at
`2.0`, and participates in normal queued-copy scope arbitration.

**Examples:**
```text
region put west
region save
set flood.channel.scope #local west
set flood.channel.scope.2 txt:* west tx=slow
set flood.channel.scope.3 login:* west
set flood.channel.scope.4 other:* west
get flood.channel.scope
get flood.channel.scope.1
del flood.channel.scope.2
```

A regionless exact mapping needs no `region` command:

```text
set flood.channel.scope #rgdata scope=BlackHole86
get flood.channel.scope
get flood.channel.scope.1
```

For example, if an authenticated `#rgdata` packet arrives carrying scope
`#usa`, that rule replaces `#usa` with `#BlackHole86` before forwarding. The
rule also assigns `#BlackHole86` when the packet is unscoped or carries any
other scope; it is a channel-to-target mapping, not an incoming-scope filter.

To use bridge bucket 1 to assign `east` to `public` packets whose received
3-byte path contains `7576FB`, while assigning `west` to every other
authenticated `public` packet:

```text
set flood.retry.bucket 1 7576FB
set flood.channel.scope public west
set flood.channel.scope public east path=bucket:1
```

Additional 3-byte IDs may be added to bucket 1 later; any one exact hit
qualifies the `east` row. This use is passive and does not require
`flood.retry.bridge` to be enabled. The separate blacklist selector remains
available for tables shared with `flood.filter path=blacklist` rules.

---

#### Require valid incoming scopes only on selected channels

**Usage:**
- `get flood.channel.scope.require`
- `get flood.channel.scope.require.<n>`
- `set flood.channel.scope.require <public|#channel|128/256-bit-key>`
- `set flood.channel.scope.require.<n> <public|#channel|128/256-bit-key>`
- `del flood.channel.scope.require.<n>`
- `del flood.channel.scope.require all`

**Default:** Empty; normal global region enforcement remains active.

Once this table contains a row, received flood `GRP_TXT` and `GRP_DATA`
packets use selective region enforcement. A packet authenticating against a
listed channel key must already carry a transport scope matching a locally
flood-allowed region. Listed channels arriving unscoped, with an unknown code,
or with a denied region are not retransmitted. This tests the original
incoming scope before any `flood.channel.scope` or `flood.filter scope=`
rewrite. Those rewrite actions are skipped for a rejected listed channel, so
they cannot rescue it or grant special receive/transmit timing.

Other group channels bypass the region/unknown-code gate while the table is
active. They remain subject to every other forwarding control, including
`repeat`, `flood.max*`, packet filters, loop detection, payload
validation, and moderation. Non-channel flood payload types retain normal
global region enforcement.

Channel matching validates the packet MAC/decryption with the configured key;
the visible one-byte channel hash is only a prefilter. Public hashtag channels
use their derived public key. Without `.n`, setting an existing key updates it
and a new key uses the first empty slot. Numbered `set` replaces that slot.
`get ...<n>` reports a four-byte derived prefix and key size without exposing
the key. The table uses the same build-dependent slot count as
`flood.channel.scope`.

Remote ACL permission `4` can manage this table. Deleting its final row
restores normal global region enforcement for group channels.

**Example:**
```text
set flood.channel.scope.require #bot
get flood.channel.scope.require
get flood.channel.scope.require.1
```

---

#### Change persistent flood rules in the field

For setup guidance, interactions with the existing forwarding controls, and
worked moderation examples, see [Flood Filtering and Moderation](flood_filtering.md).

**Usage:**
- `get flood.rule`
- `get flood.rule.<n>`
- `set flood.rule[.<n>] type=<type> [hops=<range>] [channel=<channel>] [prefix=<path-prefix>] [in=<input-scope>] <drop|scope=<name>|region=<name>|rate=<N>/min|stop> [priority=<0-255>] [tx=slow] [suspend=tempradio]`
- `del flood.rule.<n>`
- `del flood.rule all`
- `get flood.filter`
- `get flood.filter.<n>`
- `get flood.filter.blacklist`
- `get flood.filter.blacklist.<n>`
- `set flood.filter.blacklist <ID[,ID...]>`
- `set flood.filter.blacklist.<n> <ID[,ID...]>`
- `del flood.filter.blacklist`
- `del flood.filter.blacklist.<n>`
- `set flood.filter <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]`
- `set flood.filter.<n> <type> [hops] [path=blacklist] [scope=<name>] [require=region] [tx=slow] [suspend=tempradio]`
- `del flood.filter.<n>`
- `del flood.filter all`

The extended table is available on repeaters with the rule engine enabled and
on FULL-profile ESP32 room servers. A FULL room server exposes both
`flood.rule` and `flood.filter`, has 31 slots, and requires an administrator
for remote changes. It does not have the repeater's passive path blacklist, so
`flood.filter.blacklist*` and `path=blacklist` are repeater-only; use the
ordered `prefix=` match on a room server. Standard room-server profiles do not
compile this table.

**Parameters:**
- `n`: Forward-rule slot in the build's compiled table (`1-63` on generalized
  repeaters and `1-31` on FULL room servers; compact profiles may use fewer).
- `type`: Payload type name, full `PAYLOAD_TYPE_*` name, decimal value `0-15`,
  hexadecimal value `0x00-0x0F`, or `any`.
- `hops`: Optional; omitted means `all`.
  - `N`: Match only at received hop count `N`.
  - `N+`: Match at received hop count `N` and higher.
  - `N-M`: Match the inclusive received-hop range.
  - `all`: Match every received hop count (`0-63`).
  - `0+`, `all`, and an omitted hop expression are equivalent. The CLI displays
    the saved range as `all`.
- `channel=*|public|#name|128-bit-key|256-bit-key`: Optional channel match.
  `channel=*` means no channel condition at all, so the row matches everything
  selected by `type=` (including all flood payload types with `type=any`). It
  does not authenticate a packet. `public`, `#name`, and raw keys authenticate
  one channel and therefore narrow the row to `GRP_TXT`/`GRP_DATA`.
- `prefix=<ID[,ID...]>`: Optional ordered source-path prefix of one to three
  pbyte IDs. IDs must all be 2, 4, or 6 hex characters, matching a packet's
  1-, 2-, or 3-byte pbyte width. `path=<prefix>` is an alias.
- `in=any|none|scoped|allowed|unknown|scope:<name>|region:<name>`: Optional
  condition on the original incoming scope, before any rule rewrites it.
  `none` is an unscoped flood. `scope:name` is the exact public
  hashtag-derived scope. `region:name` is an exact allowed region match.
- `drop`: Explicit drop action. The `flood.rule` form requires an explicit
  action. For compatibility, a legacy `flood.filter` row with no rewrite,
  rate, or stop action is treated as drop.
- `scope=<name>`: Direct public-name scope rewrite. It derives a transport key
  from the name and does not require a configured region. For example,
  `scope=BlackHole86` is a regionless sink scope; `region=BlackHole86` would
  instead require a real configured, flood-allowed region with that name.
- `region=<name>`: Rewrite using an existing locally allowed region and one of
  that region's transport keys.
- `rate=N/min`: Per-node, per-row fixed one-minute forwarding limit. It can be
  the only action or accompany `scope=`/`region=`. Counters are charged only
  for packets that pass all forwarding gates.
- `priority=0-255`: Optional processing order. Higher values run first and
  lower slot number breaks a tie. The default is `0`; `pri=` is an alias.
- `stop` or `action=stop`: Apply this matching row, then stop lower-order FPF7
  rows from processing. It can stand alone or accompany drop, rewrite, or
  rate. A stop-only row acts as an exception to lower-priority FPF7 rules.
  If the same row uses `region=` and that configured region is missing, denied,
  wildcard, or has no usable transport key, both the rewrite and its `stop`
  are inert so lower-order safety rows still run. A direct `scope=` target does
  not depend on region configuration.
- `suspend=tempradio`: Optional. Skip this row only while the temporary radio
  is actually active.
- `require=region`: Legacy alias for `in=allowed`. Apply the row only if the
  original incoming packet already passes this repeater's
  region gate. An incoming transport scope must resolve to a locally allowed
  region; an unscoped flood must be allowed by the wildcard region. The check
  occurs before any scope rewrite during this receive pass.
- `tx=slow`: Optional and valid with `scope=` or `region=`. Use an effective inbound
  `rxdelay` base of `max(2, configured rxdelay * 2)`, keep normal outbound
  queue priority, and retransmit with the maximum supported `txdelay` factor
  of `2.0`. Scope rows default to fast; `tx=fast` explicitly restores that
  default when replacing a slow row.
- `path=blacklist`: Optional unordered path condition. The persistent blacklist
  is repeater-only. It contains up to 255 unique 3-byte repeater IDs on ESP32
  builds and 18 on other builds, each written as six hexadecimal digits. A
  packet with 3-byte path hashes matches after one exact ID hit. A packet with
  2-byte path hashes matches after two path entries match the first two bytes
  of listed IDs. Packets with 1-byte path hashes never match this condition.
  Each received path entry is counted at most once.

The payload names follow the [MeshCore packet-format allocation](https://docs.meshcore.io/packet_format/):

| Value | Short name | Full name |
| --- | --- | --- |
| `0x00` | `req` | `PAYLOAD_TYPE_REQ` |
| `0x01` | `response` | `PAYLOAD_TYPE_RESPONSE` |
| `0x02` | `txt_msg` | `PAYLOAD_TYPE_TXT_MSG` |
| `0x03` | `ack` | `PAYLOAD_TYPE_ACK` |
| `0x04` | `advert` | `PAYLOAD_TYPE_ADVERT` |
| `0x05` | `grp_txt` | `PAYLOAD_TYPE_GRP_TXT` |
| `0x06` | `grp_data` | `PAYLOAD_TYPE_GRP_DATA` |
| `0x07` | `anon_req` | `PAYLOAD_TYPE_ANON_REQ` |
| `0x08` | `path` | `PAYLOAD_TYPE_PATH` |
| `0x09` | `trace` | `PAYLOAD_TYPE_TRACE` |
| `0x0A` | `multipart` | `PAYLOAD_TYPE_MULTIPART` |
| `0x0B` | `control` | `PAYLOAD_TYPE_CONTROL` |
| `0x0C` | `ota` | `PAYLOAD_TYPE_OTA` (this fork's LoRa OTA extension; reserved upstream) |
| `0x0D` | `13` | reserved |
| `0x0E` | `14` | reserved |
| `0x0F` | `raw_custom` | `PAYLOAD_TYPE_RAW_CUSTOM` |

**Route scope:** Rules are evaluated only for the two flood route values:
`ROUTE_TYPE_TRANSPORT_FLOOD` (`0x00`, flood plus transport codes) and
`ROUTE_TYPE_FLOOD` (`0x01`, unscoped flood). Direct routes `0x02` and `0x03`
are never affected.

**Behavior:** Match fields within one row are ANDed. Every FPF7 row is matched
against the same immutable receive-time packet, before any rule changes its
scope. Matching rows are processed in descending `priority`, with lower slot
number winning a tie. The first matching `stop` row is included and all
lower-order FPF7 matches are discarded. A stop cannot undo an earlier drop or
bypass hard forwarding gates or the other policy phases. A row with
`path=blacklist` must meet the path condition as well as its other conditions;
blacklist IDs can occur anywhere in the received path and their configured
order is irrelevant. In contrast, `prefix=` begins at the first received path
entry and preserves order. A matching drop row prevents retransmission. The
highest-order remaining matching scope/region row wins; matching drop and rate
rows remain independent and can still block the rewritten packet. Scope rewriting
happens before region enforcement and is trusted even
when its name is absent from the local region list. It does not bypass
`repeat`, `flood.max`, other drop rows, loop detection, or
moderation.

With `require=region`, a failed check makes that scope row ineligible. It leaves
the packet unchanged and does not set the filter-scope trust bypass, so an
unknown or denied incoming region is rejected normally unless another
independent scope rule rewrites it. Later eligible filter scope rows may still
match.

By default, when a scope row will change the packet's transport codes, the
packet bypasses inbound `rxdelay`; its retransmission then uses zero `txdelay`
and the highest outbound queue priority. With `tx=slow`, the rewrite instead
uses an effective inbound `rxdelay` base of
`max(2, configured rxdelay * 2)`, normal queue priority, and the maximum
`txdelay` factor of `2.0`. The actual randomized transmit wait ranges from zero
to ten packet airtimes. Selecting the scope already carried is a no-op and
does not grant special treatment. An active radio transmission is not
preempted, and CAD and airtime-budget limits still apply.

The packet is still received and can still be logged. Rules are persistent
data and can be changed over serial or authenticated remote CLI without an OTA
or reboot. `flood.rule` and `flood.filter` address the same table on extended
builds; FPF6 files are migrated in memory and the next save writes FPF7.
FPF1-FPF5 files are rejected and filtering fails open. FPF7 stores canonical
region names rather than transient numeric region
IDs. Removing, reordering, or reusing a region ID therefore cannot silently
retarget a rule. If a saved input or target region name is absent, that input
match or rewrite is inert; restoring the same region name reactivates it.
While the temporary radio is active, only rows explicitly marked
`suspend=tempradio` are skipped. `tempradio` is a radio state, not an OTA mode;
normal payload types can also use the temporary channel. Other rows remain in
force. A malformed persisted table fails open (no general rules are applied).

Within one receive evaluation, rows that use the same channel key share one
authentication result. The cache is discarded after that packet and stores
neither plaintext nor passwords; different keys are authenticated separately.

**Default row:** Repeater firmware and FULL ESP32 room-server firmware seed a
new flood-filter table with
`ota all suspend=tempradio` in slot 1. This blocks repeated LoRa OTA (`0x0C`)
floods at every received hop unless temporary radio is actually active. The OTA
core independently refuses OTA receive, relay, and transmit outside temporary
radio. The row is editable and deletable; once the table is saved, deletion is
persistent. Restore the exact seeded row with:

```text
set flood.filter.1 0x0C all suspend=tempradio
```

Omitting `all` is equivalent. Omit `.1` as well to reuse an identical rule or
the first empty slot instead of replacing slot 1.

**Remote-admin lockout warning:** There are no hidden payload-type or short-hop
exceptions. FPF7 drop and rate rows may block `req`, `response`, `txt_msg`,
`anon_req`, `path`, ACK, and multipart traffic beginning at hop `0` when their
match fields say so. Transit repeaters cannot decrypt these outer types to
distinguish an admin exchange from ordinary peer traffic. Keep a serial or
other recovery path and stage broad deny/rate rules carefully.

Without `.n`, `set` reuses an identical rule or uses the first empty slot. With
`.n`, it replaces that slot, which is the intended way to change a row's match
or action.
`get flood.filter` or `get flood.rule` gives a compact list. Use the numbered
form for full details, including channel, prefix, original-scope condition,
action, timing, rate, and temporary-radio suspension.

If all of those fields plus long names would exceed one CLI reply, the
numbered form automatically switches to a non-truncating compact spelling.
The compact aliases are also accepted by `set`: `c=` means `channel=`, `p=`
means `prefix=`, `i=*|n|s|a|u|s:<scope>|r:<region>` means the corresponding
`in=` condition, `q=N` means `rate=N/min`, `pri=N` means priority, and `f=st`
combines slow timing
(`s`) with temporary-radio suspension (`t`). Packet type is shown numerically
in that fallback. Normal-sized rows keep the descriptive spelling above.

On generalized repeaters, filter rows, scope-rewrite rows, the shared
blacklist, and `flood.channel.data` compatibility state are committed in one
atomic FPF7 image. Compact FPF6 profiles retain separate files. Replacing or deleting
the blacklist does not delete rows containing `path=blacklist`; such rows
remain dormant while the list is empty. Path hashes are truncated routing
identifiers, not authenticated identities, so this is a forwarding signal
rather than proof that a particular repeater handled a packet.

A common use is containment of bulk internet-to-mesh dumping: list the path
IDs associated with the offending gateways, then add a broad
`type=any hops=all path=blacklist drop` row. This prevents this repeater from
retransmitting matching floods; it does not delete them from local logs or
prove who originated them.

The unnumbered blacklist `set` replaces the whole list and accepts up to 18 IDs
so it fits every CLI transport. Numbered `set` writes up to 18 consecutive
entries beginning at an existing slot or exactly the next slot, allowing an
ESP32 list to grow to 255 entries in batches. Numbered deletion compacts
subsequent slots. The unnumbered `get` reports the total and as many leading
IDs as fit in one reply; use numbered `get` to inspect entries beyond that
reply.

Standard traceroute is direct-routed and therefore outside `flood.filter`
entirely. A custom flood-form trace participates normally: `type=any`, explicit
`trace`, rewrite, rate, drop, and stop rows can all apply.

**Examples:**
```text
set flood.rule.2 type=grp_data hops=4+ channel=#rgdata in=none scope=BlackHole86
set flood.rule.3 type=grp_data channel=#rgdata in=scope:usa scope=BlackHole86
set flood.rule.4 type=any prefix=860C rate=10/min
set flood.rule.5 type=grp_data hops=0-2 channel=#rgdata priority=200 stop
get flood.rule.2
set flood.filter grp_data 4+
set flood.filter.2 PAYLOAD_TYPE_ADVERT 6+
set flood.filter ota 2-4
set flood.filter.1 0x0C all suspend=tempradio
set flood.filter grp_data all suspend=tempradio
set flood.filter grp_txt all scope=local
set flood.filter grp_data all scope=local require=region
set flood.filter grp_data all path=blacklist scope=local tx=slow
set flood.filter.blacklist A1B2C3,D4E5F6,112233
set flood.filter.blacklist.4 445566
set flood.filter.blacklist.19 778899,AABBCC,DDEEFF
set flood.filter any all path=blacklist
get flood.filter.blacklist
get flood.filter.blacklist.4
set flood.filter any 12+
get flood.filter
get flood.filter.2
del flood.filter.2
```

The first rule authenticates `#rgdata`, requires more than three received hops,
and adds `#BlackHole86` only when no scope was present. The second rewrites the
exact incoming `#usa` scope. The third demonstrates a two-byte pbyte source
prefix and a global per-row rate cap. The fourth authenticates `#rgdata` at
zero through two hops, applies no FPF7 action of its own, and stops lower-order
FPF7 forward rows; hard gates and the rewrite/moderation phases still apply.

The fixed 240 KB STM32WL profiles leave
`MESH_ENABLE_FLOOD_RULE_ENGINE=0` and retain the compact, persistent FPF6
`flood.filter` and blacklist commands. They still filter floods, but do not
expose the `flood.rule` alias or its extended channel, prefix, input-scope,
region-action, or rate fields. No partition size changes are required.

---

#### Moderate flood group text by channel, sender, and source path

**Usage:**
- `get flood.moderation`
- `get flood.moderation.<n>`
- `set flood.moderation <channel> <sender> <action> [action...]`
- `set flood.moderation.<n> <channel> <sender> <action> [action...]`
- `del flood.moderation.<n>`
- `del flood.moderation all`

**Parameters:**
- `n`: Moderation slot from `1` to `16`.
- `channel`:
  - `public`: Built-in Public channel.
  - `#channel`: Derive the well-known hashtag-channel key.
  - A 128-bit or 256-bit channel key in hex, for any other/private channel.
- `sender`: Exact group-text display name. Matching is ASCII case-insensitive.
  Quote names containing spaces, for example `"Field User"`.
- `drop`: Do not forward matching messages. Equivalent to `rate=0/min`.
- `rate=X/min`: Forward at most `X` matching messages per 60-second local
  window. This option requires an exact sender rather than `*`.
- `hops=N`: Do not forward a matching message whose received flood path count
  is `N` or higher. `hops=all` removes this constraint.
- `path=H1[,H2,H3]`: Match the start of the flood path. One to three hashes are
  accepted; every hash must have the same 1-, 2-, or 3-byte width.
- `path=*`: Match any source path (the default).

At least one of `drop`, `rate=X/min`, or `hops=N` is required. Rate and hop
limits can be combined. Rate counters are local to this repeater and rule, use a
60-second window beginning with the first matching message, and reset on reboot.

**Decode and identity behavior:** Moderation applies only to flood
`PAYLOAD_TYPE_GRP_TXT`. The repeater first checks the packet's channel-hash byte,
then validates and decrypts with the configured key. It extracts the text before
the first `:` from the standard `<sender>: <message>` plaintext. The channel key
is stored locally but is never printed by `get`.

The group-text sender is an **unverified display name**, not a public key. It can
be spoofed. Combining it with the first one to three path hashes makes a more
useful moderation signal, but path hashes are truncated and are not proof of the
originating user. A path-qualified rule begins matching only after the packet
contains all configured starting hops; it cannot identify a first hop on a
zero-hop packet.

As with general filtering, matching messages are still received/logged; only
retransmission is denied. There are no moderation rules by default.

**Examples:**
```text
set flood.moderation public "Noisy User" rate=5/min
set flood.moderation #local bot drop path=A1B2C3,D4E5F6
set flood.moderation.3 00112233445566778899AABBCCDDEEFF alice rate=10/min hops=4 path=71CE82
get flood.moderation
get flood.moderation.3
del flood.moderation.3
```

---

### ACL

#### Add, update or remove permissions for a companion
**Usage:** 
- `setperm <pubkey> <permissions>`

**Parameters:**
- `pubkey`: Companion public key
- `permissions`: 
  - `0`: Guest
  - `1`: Read-only
  - `2`: Read-write
  - `3`: Admin
  - `4`: Region/scope manager (repeater delegated region and forced-scope management)
  - `5`: Filter manager (repeater delegated forwarding-filter management)

**Filter manager scope:** Permission `5` can use an explicit allowlist of
non-secret operational/filter status commands and can change the forwarding
controls `repeat`, `loop.detect`, `flood.max*`, `flood.channel.data*`,
`flood.filter*`, `flood.rule*`, and
`flood.moderation*`. It cannot read
guest, WiFi, MQTT, bridge, or other credentials, and it cannot change regions,
ACL entries, radio settings, or other admin configuration. Permission `4`
is limited to region commands, `flood.channel.scope*`, and the same non-secret
status allowlist.
Both delegated manager roles are protected from least-recently-active ACL
eviction like administrators.

**Note:** Removes the entry when `permissions` is omitted

---

#### View the current ACL
**Usage:** 
- `get acl`

**Serial Only:** Yes

---

#### View or change this room server's 'read-only' flag
**Usage:**
- `get allow.read.only`
- `set allow.read.only <state>`

**Parameters:**
- `state`: `on` (enable) or `off` (disable)

**Default:** `off`

---

### Region Management (v1.10.+)

#### Bulk-load region lists
**Usage:** 
- `region load`
- `region load <name> [flood_flag]`

**Parameters:**
- `name`: A name of a region. `*` represents the wildcard region

**Note:** `flood_flag`: Optional `F` to allow flooding

**Note:** Indentation creates parent-child relationships (max 8 levels)

**Note:** `region load` with an empty name will not work remotely (it's interactive)

---

#### Save any changes to regions made since reboot
**Usage:** 
- `region save`

---

#### Allow a region
**Usage:** 
- `region allowf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Note:** Setting on wildcard `*` allows packets without region transport codes

---

#### Block a region
**Usage:** 
- `region denyf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Note:** Setting on wildcard `*` drops packets without region transport codes

---

#### Show information for a region
**Usage:** 
- `region get <name>`

**Parameters:**
- `name`: Region name (or `*` for wildcard)

---

#### View or change the home region for this node
**Usage:** 
- `region home`
- `region home <name>`

**Parameters:**
- `name`: Region name

---

#### View or change the default scope region for this node
**Usage:** 
- `region default`
- `region default {name|<null>}`

**Parameters:**
- `name`: Region name,  or <null> to reset/clear

---

#### Create a new region
**Usage:** 
- `region put <name> [parent_name]`

**Parameters:**
- `name`: Region name
- `parent_name`: Parent region name (optional, defaults to wildcard)

**Note:** In firmware **v1.15.0** and later, `region put` enables flooding for that region by default (you do not need a separate `region allowf <name>` after each `put`). On **v1.14.0** and earlier, new regions may still require `region allowf` for flooding-see [`region allowf`](#allow-a-region).

---

#### Define region hierarchy (single line)
**Usage:**
- `region def <token> [<token> ...]`

**Parameters (tokens):** Space-separated. A logical **cursor** starts at the wildcard `*`.

- **`name`** - Create `name` as a child of the current cursor (equivalent to `region put name` with the cursor as parent). Cursor moves to `name`.
- **`name|jump`** *(or `name,jump`)* - Create `name` as a child of the current cursor, then move the cursor to `jump` (must already exist on the node, or have been created earlier in this command). `jump` is **not** the parent of `name`; use this form to pop back up and start another branch.

**Behavior:** Each created region defaults to flood-allowed (same as `region put`). The reply is the resulting region tree (same format as bare `region`); review it before running `region save` to persist. The command is transactional: invalid names, unknown or ambiguous jumps, table overflow, and hierarchy cycles return `Err - ...` without changing the existing tree.

**Existing regions:** `region def` does not clear the existing tree - if a name already exists, its parent is updated to the current cursor; otherwise a new region is created. To start from scratch, `region remove` the unwanted regions first.

**Limits:** Repeater serial accepts one line up to **160 characters**. For larger trees, split across multiple `region def` commands; the cursor resets to `*` between commands, so lead the next command with `child|ancestor` to reposition. Each token splits at most once on `|` - `region def a|b|c|d` is not a flat-list shorthand; see the flat-list example below.

**Example - linear chain** (each token becomes a child of the previous):
```
region def a b c d e
region save
```

**Example - branched tree** (equivalent to `region put a`, `region put b a`, `region put c b`, `region put d c`, `region put e b`, `region put f e`):
```
region def a b c d|b e f
region save
```

**Example - transactional error:**
```
region def a b c|nope d
```
The reply is `Err - unknown or ambiguous jump: nope`. The existing tree is unchanged; re-run with a corrected jump.

**Example - flat list** (each region a child of `*`). Use `|*` after each token to pop the cursor back to the root before the next token:
```
region def a|* b|* c|* d|* e|* f
region save
```

---

#### Remove a region
**Usage:** 
- `region remove <name>`

**Parameters:**
- `name`: Region name

**Note:** Must remove all child regions before the region can be removed 

---

#### View all regions
**Usage:** 
- `region list <filter>`

**Serial Only:** Yes

**Parameters:**
- `filter`: `allowed`|`denied`

**Note:** Requires firmware 1.12+

---

#### Dump all defined regions and flood permissions
**Usage:** 
- `region`

**Serial Only:** For firmware older than 1.12.0

---

### Region Examples

**Example 1: Using F Flag with Named Public Region**
```
region load
#Europe F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a region named `#Europe` with flooding enabled
- Packets from this region will be flooded to other nodes

---

**Example 2: Using Wildcard with F Flag**
```
region load 
* F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a wildcard region `*` with flooding enabled
- Enables flooding for all regions automatically
- Applies only to packets without transport codes

---

**Example 3: Using Wildcard Without F Flag**
```
region load 
*
<blank line to end region load>
region save
```
**Explanation:**
- Creates a wildcard region `*` without flooding
- This region exists but doesn't affect packet distribution
- Used as a default/empty region

---

**Example 4: Nested Public Region with F Flag**
```
region load 
#Europe F
  #UK
    #London
    #Manchester
  #France
    #Paris
    #Lyon
<blank line to end region load>
region save
```

**Explanation:**
- Creates `#Europe` region with flooding enabled
- Adds nested child regions (`#UK`, `#France`)
- All nested regions inherit the flooding flag from parent

---

**Example 5: Wildcard with Nested Public Regions**
```
region load 
* F
  #NorthAmerica
    #USA
      #NewYork
      #California
    #Canada
      #Ontario
      #Quebec
<blank line to end region load>
region save
```

**Explanation:**
- Creates wildcard region `*` with flooding enabled
- Adds nested `#NorthAmerica` hierarchy
- Enables flooding for all child regions automatically
- Useful for global networks with specific regional rules

---
### Direct Retry

Direct retry resends direct-routed packets when the downstream echo is not heard. It applies to direct messages, ACK packets, multipart packets carrying ACK payloads, and TRACE packets.

The shared state, count, base, and step controls work on repeater, room-server,
and sensor firmware. Recent-repeater/SNR controls are repeater-only because the
other roles do not keep the repeater reachability table they require.

#### View or change direct retry state
**Usage:**
- `get direct.retry`
- `set direct.retry <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Notes:**
- New installs and older preference files without direct retry settings default to `on` with the `rooftop` preset.

**Examples:**
```
get direct.retry
set direct.retry on
set direct.retry off
```

---

#### View or change direct retry heard-table gate
**Usage:**
- `get direct.retry.heard`
- `set direct.retry.heard <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Note:** This command is repeater-only. When enabled, the recent repeater table is the direct retry eligibility
gate. Prefixes missing from the table are assumed reachable; prefixes in the
table below the active SNR gate are blocked.

**Examples:**
```
get direct.retry.heard
set direct.retry.heard on
set direct.retry.heard off
```

---

#### View or apply a retry preset
**Usage:**
- `get retry.preset`
- `set retry.preset <preset>`

**Parameters:**
- `preset`: `infra`|`rooftop`|`mobile`

**Notes:**
- Applies shared direct retry and flood retry defaults.
- `infra`: fewer, slower retries for stable fixed infrastructure.
- `rooftop`: default long retry window for weak rooftop links.
- `mobile`: long retry count with shorter spacing for moving or changing links; flood retry count is `15`.
- Changing `direct.retry.count`, `direct.retry.base`, `direct.retry.step`, `direct.retry.margin`, `flood.retry.count`, `flood.retry.path`, or `flood.retry.group.path` makes the preset report as `custom`.

**Examples:**
```
get retry.preset
set retry.preset infra
set retry.preset rooftop
set retry.preset mobile
```

---

### Flood Retry

Flood retry resends flood-routed packets when the same packet is not heard from
another qualifying repeater.

The count, path, group-data path, and advert controls work on repeater, room-server, and sensor
firmware. Flood forwarding must also be enabled for retries to run. Prefix,
ignore, bridge, and bucket controls are repeater-only.

#### View or change flood retry count
**Usage:**
- `get flood.retry.count`
- `set flood.retry.count <count>`

**Parameters:**
- `count`: Base retry attempts after the original send, from `0` to `15`. `0` disables flood retry.

**Note:** The role first calculates its retry count: path count 0 uses `count * 2`, path count 1 uses `count * 1.5` rounded up, and path count 2 and higher uses the configured base count, with a hard cap of `15`. A shared payload policy then applies to every build: `REQ` never retries; `GRP_TXT` keeps the role-calculated count; remote-login-critical `RESPONSE`, `TXT_MSG`, `ANON_REQ`, and `PATH` packets keep up to `15` at the originating node (path count 0) and cap at `2` after entering the path; all other flood payload types cap at `1`. These caps never raise a lower role-calculated count. Setting `count` to `0` immediately removes queued and future flood retries; a packet already transmitting is allowed to finish.

Forwarded neighbor adverts have an additional loop guard independent of the advert retry setting. After this node completes an advert transmission and hears a downstream copy with a longer path, it does not forward that same advert again while the advert's signed timestamp is less than six hours old. Self-originated adverts, adverts without a heard echo, and adverts six hours old or older are unaffected.

An enabled self-originated advert retry waits at least one additional minute beyond the normal airtime-aware retry delay. Once a newer self advert has successfully entered the outbound queue, its retry sequence replaces queued or future retries for older self adverts; an older advert already transmitting is allowed to finish. Companion firmware permits this single slow retry for its own adverts while continuing to block retry attempts for neighbor adverts it forwards.

**Defaults:**
- `infra`: `1`
- `rooftop`: `3`
- `mobile`: `15`

**Examples:**
```
get flood.retry.count
set flood.retry.count 0
set flood.retry.count 15
```

---

#### View or change flood retry path gate
**Usage:**
- `get flood.retry.path`
- `set flood.retry.path <count|off>`

**Parameters:**
- `count`: Maximum flood path hash count eligible for retry, from `0` to `63`.
- `off`: Disable the path-length gate.

**Defaults:**
- `infra`: `1`
- `rooftop`: `2`
- `mobile`: `1`

**Examples:**
```
get flood.retry.path
set flood.retry.path 1
set flood.retry.path off
```

---

#### View or change the group-data flood retry path gate
**Usage:**
- `get flood.retry.group.path`
- `set flood.retry.group.path <count|off>`

**Parameters:**
- `count`: Maximum flood path hash count eligible for retry for group data packets (`PAYLOAD_TYPE_GRP_DATA`/type 6), from `0` to `63`.
- `off`: Disable only the group-data-specific gate. The general `flood.retry.path` gate still applies.

**Default:** `1` for `infra`, `rooftop`, and `mobile` presets.

**Note:** The stricter of `flood.retry.path` and `flood.retry.group.path` is used. A value of `1` allows retry sequences at path counts `0` and `1`; group data at path count `2` or higher is still forwarded normally but does not start a flood retry sequence. A value of `0` allows retries only at the originating sender.

Setting `flood.retry.path` to `0` also sets `flood.retry.group.path` to `off` because the general zero-hop gate is already stricter. While the general gate remains `0`, attempts to set the group-data gate keep it `off`. Applying a named retry preset restores the group-data default of `1`.

**Examples:**
```
get flood.retry.group.path
set flood.retry.group.path 1
set flood.retry.group.path off
```

---

#### View or change flood retry advert handling
**Usage:**
- `get flood.retry.advert`
- `set flood.retry.advert <on|off>`

**Parameters:**
- `on`: Retry node advert floods.
- `off`: Do not retry node advert floods.

**Default:** `off`

**Examples:**
```
get flood.retry.advert
set flood.retry.advert off
```

---

#### View or change flood retry target prefixes
**Usage:**
- `get flood.retry.prefixes`
- `set flood.retry.prefixes <prefixes|none|off>`

**Parameters:**
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 8 entries.
- `none` or `off`: Clear the list.

**Note:** When set, non-bridge flood retry only accepts same-packet echoes whose
last hop matches one of these prefixes. When unset, any non-ignored last hop can
cancel the retry.

**Examples:**
```
get flood.retry.prefixes
set flood.retry.prefixes A58296,860CCA,425E5C
set flood.retry.prefixes none
```

---

#### View or change flood retry ignored prefixes
**Usage:**
- `get flood.retry.ignore`
- `set flood.retry.ignore <prefixes|none|off>`

**Parameters:**
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 8 entries.
- `none` or `off`: Clear the list.

**Note:** Non-bridge flood retry does not cancel on same-packet echoes whose
last hop matches this list. Bridge mode also excludes these prefixes from bucket
and `other` hits.

**Examples:**
```
get flood.retry.ignore
set flood.retry.ignore 71CE82,C7618C
set flood.retry.ignore none
```

---

#### View or change flood retry bridge mode
**Usage:**
- `get flood.retry.bridge`
- `set flood.retry.bridge <on|off>`

**Note:** Bridge mode retries until each configured fresh bucket, plus the non-source `other` bucket, has been heard or the retry count is exhausted. If prefixes in different buckets share their first byte, configuration commands return a warning because a 1-byte path cannot distinguish those buckets. The configuration remains valid: an ambiguous source is treated as belonging to every matching source bucket, and an ambiguous echo credits every matching target bucket so it cannot force retry exhaustion.

Flood retry timing retains its fixed maximum-frame plus 20 packet-airtime wait, then adds a random `0-200%` of one additional packet airtime on every attempt. This de-synchronizes repeaters that may have missed the same echo while capping the added wait at two frames.

Only one active retry sequence is kept for a given logical flood packet. An identical flood can still transmit normally, but it does not create a second sequence of extra attempts. Retry state is released if a queued packet is evicted, and the final echo window retains metadata without reserving a packet-pool entry.

Bridge reachability learned from earlier hops in a successful echo is cached separately from `recent.repeater`. Only the final RF hop updates `recent.repeater` and its SNR, so indirect path entries cannot affect direct-retry SNR gating or coding-rate selection.

**Examples:**
```
get flood.retry.bridge
set flood.retry.bridge on
```

---

#### View or change flood retry bridge buckets
**Usage:**
- `get flood.retry.bucket.<n>`
- `set flood.retry.bucket <n> <prefixes|none|off>`

**Parameters:**
- `n`: Bucket number from `1` to `6`.
- `prefixes`: Comma-separated 3-byte path hash prefixes, up to 17 entries per bucket.
- `none` or `off`: Clear the bucket.

**Examples:**
```
get flood.retry.bucket.1
set flood.retry.bucket 1 71CE82,C7618C
set flood.retry.bucket 2 none
```

---

#### View or change direct retry count
**Usage:**
- `get direct.retry.count`
- `set direct.retry.count <count>`

**Parameters:**
- `count`: Maximum retry attempts after the original send, from `1` to `15`.

**Default:** `15` with the `rooftop` preset

**Note:** Direct-routed type 2 text packets always use 21 retry attempts in the
shared retry logic, regardless of this setting or the repeater short-path cap.

**Examples:**
```
get direct.retry.count
set direct.retry.count 1
set direct.retry.count 4
set direct.retry.count 15
```

---

#### View or change direct retry base delay
**Usage:**
- `get direct.retry.base`
- `set direct.retry.base <ms>`

**Parameters:**
- `ms`: First retry wait in milliseconds, from `10` to `5000`.

**Default:** `175` with the `rooftop` preset

**Explanation:**
- The first retry waits for `base + packet-length add-on + random forwarding jitter`
  after the preceding transmission completes.
- TRACE and
  ANON_REQ/type 7 packets use a 3x line-time add-on. TXT_MSG/type 2
  packets use 7x. Other direct retry packets use 6x.
- Room-server and sensor firmware use this configured base with the same
  packet-type add-ons.
- Larger values reduce channel pressure and give slow repeaters more time.
- Smaller values recover faster but create tighter retry bursts.

**Examples:**
```
get direct.retry.base
set direct.retry.base 175
set direct.retry.base 275
set direct.retry.base 500
```

---

#### View or change direct retry step delay
**Usage:**
- `get direct.retry.step`
- `set direct.retry.step <ms>`

**Parameters:**
- `ms`: Extra milliseconds added for each subsequent retry, from `0` to `5000`.

**Default:** `100` with the `rooftop` preset

**Explanation:**
- Retry delay is `base + packet-length add-on + random forwarding jitter + attempt_index * step`.
- TRACE and ANON_REQ/type 7 packets
  use a 3x packet-length add-on. TXT_MSG/type 2 packets use 7x.
  Other direct retry packets use 6x.
- Room-server and sensor firmware use this configured step with the same
  packet-type add-ons.
- With `base=175` and `step=100`, the fixed portion is `175`, `275`, `375`,
  `475` ms, and so on, before the packet-length add-on and random jitter.
- `step=0` keeps every retry at the same delay.
- Larger steps spread retries over time and are safer on busy channels.

**Examples:**
```
get direct.retry.step
set direct.retry.step 0
set direct.retry.step 50
set direct.retry.step 100
set direct.retry.step 250
```

---

#### View or change direct retry SNR margin
**Usage:**
- `get direct.retry.margin`
- `set direct.retry.margin <snr_db>`

**Parameters:**
- `snr_db`: Extra SNR margin above the SF receive floor, from `0` to `40`.

**Default:** `5.00` with the `rooftop` preset

**Notes:**
- This command is repeater-only.
- Unknown repeaters are still retried.
- Known repeaters below the receive floor plus this margin are skipped.
- Failed attempts lower the recent repeater SNR estimate by `0.25 dB`.

**Examples:**
```
get direct.retry.margin
set direct.retry.margin 0
set direct.retry.margin 2.5
set direct.retry.margin 5
set direct.retry.margin 10
```

---

#### View or change adaptive direct retry coding rate
**Usage:**
- `get direct.retry.cr`
- `set direct.retry.cr off`
- `set direct.retry.cr on` (room-server and sensor)
- `set direct.retry.cr <cr4_min>,<cr5_min>,<cr7_min>,<cr8_max>`

**Parameters:**
- `cr4_min`: Minimum SNR in dB to retry at CR4.
- `cr5_min`: Minimum SNR in dB to retry at CR5.
- `cr7_min`: Minimum SNR in dB to retry at CR7.
- `cr8_max`: Maximum SNR in dB that forces CR8.

**Default:** `10.00,7.50,2.50,2.50`

**Explanation:**
- Higher SNR uses faster coding rates.
- Lower SNR uses more robust coding rates.
- Repeater retry attempts escalate from the adaptive starting CR. CR4 starts as CR4, CR5, CR7, CR7, then CR8. CR5 starts as CR5, CR7, CR7, then CR8. CR7 gets two attempts, then CR8.
- Repeater adaptive CR selection intentionally skips CR6.
- Non-repeater retry packets start at the current radio CR and follow the same escalation pattern, clamped at CR8. With the normal CR5 radio setting this is CR5, CR7, CR7, then CR8.
- Room-server and sensor firmware accept `on` or `off`; numeric SNR thresholds
  are repeater-only because those roles do not keep recent-repeater SNR data.
- `off` disables per-packet retry CR overrides and uses the current radio CR.
- Direct path retry packets sent at CR4 or CR5 temporarily use a shorter 16-symbol preamble, then restore the radio's default preamble.
- Unknown repeaters start at `+3.00 dB` for adaptive CR selection.
- A failed unknown repeater is seeded at `+2.75 dB`.
- Each later failure lowers the SNR estimate by `0.25 dB`.

**Examples:**
```
get direct.retry.cr
set direct.retry.cr off
set direct.retry.cr on
set direct.retry.cr 10.0,7.5,2.5,2.5
set direct.retry.cr 12.0,8.0,4.0,1.0
set direct.retry.cr 8.0,5.0,1.5,0
set direct.retry.cr 6.0,3.0,0,-2.0
set direct.retry.cr 20.0,12.0,6.0,2.0
set direct.retry.cr 4.0,2.0,0,-4.0
```

**Example profiles:**
- Conservative weak-link profile:
```
set direct.retry.cr 12.0,8.0,4.0,1.0
```
- Balanced rooftop profile:
```
set direct.retry.cr 10.0,7.5,2.5,2.5
```
- Faster strong-link profile:
```
set direct.retry.cr 6.0,3.0,0,-2.0
```
- Very cautious noisy-link profile:
```
set direct.retry.cr 20.0,12.0,6.0,2.0
```

---

#### View, seed, or clear the recent repeater table
**Usage:**
- `get recent.repeater`
- `get recent.repeater <page>`
- `get recent.repeaters <page>`
- `get recent.repeaters search <prefix> [page]`
- `set recent.repeater <prefix> [snr_db]`
- `clear recent.repeater`

**Parameters:**
- `prefix`: Repeater path-hash prefix as 2, 4, or 6 hex characters.
- `snr_db`: Optional SNR in dB. If omitted or invalid, defaults to `3.0`.
- `page`: 1-based result page.

**Note:** These commands are repeater-only.

**Output order:**
- `get recent.repeater` lists 3-byte prefixes first, then 2-byte prefixes, then 1-byte prefixes.
- Within each prefix length, entries are sorted from highest SNR to lowest SNR.
- `search` returns every overlapping path-hash entry. For example, searching
  `860C` can return `86`, `860C`, and `860CCA`; it does not return a different
  branch such as `86D0`.
- Search rows include the monotonic age of the entry's most recent recording,
  compacted to a whole `s`, `m`, or `h` field. Search pages contain up to six
  rows so the result remains within the remote CLI reply limit.

**SNR details:**
- Recent repeater SNR is stored internally in quarter-dB units.
- Heard repeater samples update an existing table entry with a weighted blend: `75%` existing SNR and `25%` new heard SNR, rounded up.
- Direct retry success also feeds the heard echo SNR back into the same weighted table.
- Direct retry failure is not weighted: each final echo-timeout failure lowers that repeater's SNR by `0.25 dB`.
- Unknown repeaters start at `+3.00 dB` for adaptive CR selection.
- If an unknown repeater fails, it is seeded into the table at `+2.75 dB`.
- `set recent.repeater <prefix> [snr_db]` seeds a missing prefix or adds another weighted sample for an existing prefix.
- Successful `set recent.repeater` replies include the stored prefix and SNR, for example `OK - set A1B2C3 at 3.0 SNR`.
- Entries strictly older than 24 hours are removed during a sweep every three hours, so an entry can remain for at most approximately 27 hours.

**Examples:**
```
get recent.repeater
get recent.repeater 2
get recent.repeaters search 86
get recent.repeaters search 860C page 2
set recent.repeater A1B2C3 8.5
set recent.repeater 71CE82 -3.25
set recent.repeater A1B2C3
clear recent.repeater
```

---
### GPS (When GPS support is compiled in)

#### View or change GPS state
**Usage:**
- `gps`
- `gps <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `off`

**Note:** Output format:
- `off` when the GPS hardware is disabled
- `on, {active|deactivated}, {fix|no fix}, {sat count} sats` when the GPS hardware is enabled

---

#### Sync this node's clock with GPS time
**Usage:** 
- `gps sync`

The GPS must be enabled. When GPS power saving has put an enabled receiver to
sleep, this command schedules a sync and wakes it; after `gps off`, it reports
`gps is off` without scheduling work.

---

#### Set this node's location based on the GPS coordinates
**Usage:** 
- `gps setloc`

---

#### View or change the GPS advert policy
**Usage:**
- `gps advert`
- `gps advert <policy>`

**Parameters:** 
- `policy`: `none`|`share`|`prefs` 
  - `none`: don't include location in adverts
  - `share`: share gps location (from SensorManager)
  - `prefs`: location stored in node's lat and lon settings

**Default:** `prefs`

---

### Sensors (When sensor support is compiled in)

#### View or change telemetry access mode
**Usage:**
- `get telemetry.access`
- `set telemetry.access <mode>`

**Parameters:**
- `mode`: `all`|`acl`
  - `all`: allow telemetry requests using the requester-provided telemetry mask
  - `acl`: require ACL read-only or higher for telemetry, including GPS

**Default:** `all`

**Note:** `all` matches the previous sensor telemetry behavior.

---

#### View the list of sensors on this node
**Usage:** `sensor list [start]`

**Parameters:**
- `start`: Optional starting index (defaults to 0)

**Note:** Output format: `<var_name>=<value>\n`

---

#### View or change the value of a sensor
**Usage:** 
- `sensor get <key>`
- `sensor set <key> <value>`

**Parameters:**
- `key`: Sensor setting name
- `value`: The value to set the sensor to

---

### Bridge (When bridge support is compiled in)

#### View the compiled bridge type
**Usage:** `get bridge.type`

---

#### View or change the bridge enabled flag
**Usage:**
- `get bridge.enabled`
- `set bridge.enabled <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `off`

---

#### Add a delay to packets routed through this bridge
**Usage:**
- `get bridge.delay`
- `set bridge.delay <ms>`

**Parameters:**
- `ms`: Delay in milliseconds (0-10000)

**Default:** `500`

---

#### View or change the source of packets bridged to the external interface
**Usage:**
- `get bridge.source`
- `set bridge.source <source>`

**Parameters:**
- `source`:
  - `logRx`: bridges received packets
  - `logTx`: bridges transmitted packets

**Default:** `logTx`

> **Note:** For MQTT bridges, use `mqtt.rx` and `mqtt.tx` instead of `bridge.source`. These provide independent per-direction control and support both RX and TX simultaneously. `bridge.source` still works as a convenience alias for MQTT (setting `bridge.source rx` sets `mqtt.rx on` + `mqtt.tx off`, and vice versa), but `mqtt.rx`/`mqtt.tx` are preferred.

---

#### View or change MQTT RX packet uplinking
**Usage:**
- `get mqtt.rx`
- `set mqtt.rx <on|off>`

**Parameters:**
- `on`: uplink received (RX) packets to MQTT brokers
- `off`: disable RX packet uplinking

**Default:** `on`

---

#### View or change MQTT TX packet uplinking
**Usage:**
- `get mqtt.tx`
- `set mqtt.tx <on|off|advert>`

**Parameters:**
- `on`: uplink all transmitted (TX) packets to MQTT brokers
- `advert`: uplink only this node's own advert packets (self-originated advertisements only - forwarded adverts from other nodes are filtered out)
- `off`: disable TX packet uplinking

**Default:** `advert`

> **Note:** `mqtt.rx` and `mqtt.tx` take effect immediately - no restart required. Both can be enabled simultaneously.

---

#### View or change periodic neighbors publishing (MQTT observer, PSRAM only)
**Usage:**
- `get mqtt.neighbors`
- `set mqtt.neighbors <on|off>`

**Parameters:**
- `on`: periodically discover neighbor scopes and publish the neighbor table to the `neighbors` topic
- `off`: disable periodic neighbors publishing

**Default:** `off`

> **Note:** Requires a PSRAM board. On non-PSRAM MQTT builds this replies
> `Err - not supported (requires PSRAM)`. The setting is read live by the mesh
> loop -- no restart required; enabling it triggers a discovery on the next pass.
> While enabled, `get mqtt.status` gains a trailing `nbr: <next>/<last>` field
> (time to next publish, and how the last publish went).

---

#### View or change the neighbors publish interval (MQTT observer, PSRAM only)
**Usage:**
- `get mqtt.neighbors.interval`
- `set mqtt.neighbors.interval <hours>`

**Parameters:**
- `hours`: how often to publish the neighbor table (12-336, default 24)

**Default:** `24` (hours)

> **Note:** Out-of-range values are rejected (not clamped). Requires a PSRAM board.

---

#### View or change the NTP server (MQTT observer only)
**Usage:**
- `get mqtt.ntp`
- `set mqtt.ntp <hostname>`
- `set mqtt.ntp none`

**Description:** Sets the primary NTP server used for clock sync (required for JWT MQTT auth). On `set`, the device attempts an immediate sync of the just-configured server (primary only, so a typo fails fast) when WiFi is connected and the MQTT bridge is running.

**Fallbacks:** If the primary fails, the firmware tries `pool.ntp.org`, `time.google.com`, `time.cloudflare.com`, `time.aws.com`, and `time.nist.gov` in order (skipping duplicates).

**Default:** `pool.ntp.org` (when unset or `none`)

---

#### Diagnose NTP server connectivity (MQTT observer only)
**Usage:**
- `get mqtt.ntp.diag`

**Description:** Probes every configured NTP server (the custom primary, if set, plus the built-in fallbacks) and reports whether each responds. This is a pure connectivity diagnostic - it does **not** change the system clock.

- **Serial console:** prints a detailed table with each server's reported UTC time (or `FAIL`).
- **Over LoRa:** returns a compact `<server> ok|fail` list, one per line.

Requires WiFi connected and the MQTT bridge running.

---

#### View or change the speed of the bridge (RS-232 only)
**Usage:**
- `get bridge.baud`
- `set bridge.baud <rate>`

**Parameters:**
- `rate`: Baud rate (`9600`, `19200`, `38400`, `57600`, or `115200`)

**Default:** `115200`

---

#### View or change the channel used for bridging (ESPNow only)
**Usage:**
- `get bridge.channel`
- `set bridge.channel <channel>`

**Parameters:**
- `channel`: Channel number (1-14)

---

#### Set the ESP-Now secret
**Usage:** 
- `get bridge.secret`
- `set bridge.secret <secret>`

**Parameters:**
- `secret`: ESP-NOW bridge secret, 1-15 characters

**Default:** Varies by board

---

#### View the bootloader version (nRF52 only)
**Usage:** `get bootloader.ver`

---

#### View power management support
**Usage:** `get pwrmgt.support`

---

#### View the current power source
**Usage:** `get pwrmgt.source`

**Note:** Returns an error on boards without power management support.

---

#### View the boot reset and shutdown reasons
**Usage:** `get pwrmgt.bootreason`

**Note:** Returns an error on boards without power management support.

---

#### View the boot voltage
**Usage:** `get pwrmgt.bootmv`

**Note:** Returns an error on boards without power management support.

---

### Ethernet (when Ethernet support is compiled in)

Ethernet support is available on RAK4631 boards with a RAK13800 (W5100S) Ethernet module. Use the `_ethernet` firmware variants (e.g. `RAK_4631_repeater_ethernet`) to enable this feature.

---

#### View Ethernet connection status
**Usage:**
- `eth.status`

**Output:**
- `ETH: <ip>:<port>` when connected (e.g. `ETH: 192.168.1.50:23`)
- `ETH: not connected` when Ethernet is not active

**Notes:**
- Available on repeater and room server firmware only. Companion radio ethernet firmware does not expose a CLI.
- The Ethernet interface obtains an IP address via DHCP automatically on boot.
- A TCP server listens on port 23 (default) for CLI connections.
- Connect with any TCP client (e.g. `nc`, PuTTY) to access the same CLI available over serial.

---
