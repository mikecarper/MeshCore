# Companion Protocol

- **Last Updated**: 2026-08-26
- **Protocol Version**: 14 (`FIRMWARE_VER_CODE`)

> The command and response catalogs track
> `examples/companion_radio/MyMesh.cpp`. Applications should negotiate the
> protocol and validate lengths because older firmware exposes a subset.

This document is a practical guide to MeshCore's binary companion protocol.
The same protocol frames can be carried by the enabled BLE, USB serial, Wi-Fi,
or Ethernet companion interface; connection details differ by build.

On builds exposing more than one transport, delivery-required replies follow
the interface which supplied the command. The multi-frame contact-list response
holds that route until `END_OF_CONTACTS`; best-effort asynchronous observations
may still be broadcast to enabled clients. Treat the device as one Companion
session rather than as independent per-transport sessions.

The examples focus on BLE, but the packet formats are transport-independent.

## Official Libraries

Please see the following repos for existing MeshCore Companion Protocol libraries.

- JavaScript: [https://github.com/meshcore-dev/meshcore.js](https://github.com/meshcore-dev/meshcore.js)
- Python: [https://github.com/meshcore-dev/meshcore_py](https://github.com/meshcore-dev/meshcore_py)

## Important Security Note

All secrets, hashes, and cryptographic values shown in this guide are example values only.

- All hex values, public keys and hashes are for demonstration purposes only
- Never use example secrets in production
- Always generate new cryptographically secure random secrets
- Please implement proper security practices in your implementation
- This guide is for protocol documentation only

## Table of Contents

1. [BLE Connection](#ble-connection)
2. [Packet Structure](#packet-structure)
3. [Commands](#commands)
4. [Channel Management](#channel-management)
5. [Message Handling](#message-handling)
6. [Response Parsing](#response-parsing)
7. [Example Implementation Flow](#example-implementation-flow)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)

---

## BLE Connection

### Service and Characteristics

MeshCore Companion devices expose a BLE service with the following UUIDs:

- **Service UUID**: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- **RX Characteristic** (App -> Firmware): `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- **TX Characteristic** (Firmware -> App): `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`

An nRF52 Full Companion also exposes a separate LoRa mOTA source service. It
does not replace or multiplex the normal Companion UART service:

- **mOTA Service**: `14518FC2-7E7A-4D84-8CAE-6664B0234CF2`
- **Device Request** (notify): `2BFAA1EE-7030-459A-B65A-E7CFD5B09735`
- **Host Response** (write with response): `ACF38A51-DD58-4DCE-917F-0B1135E41B1A`

All three mOTA attributes require an encrypted, MITM-authenticated connection
using the Companion's six-digit PIN. The source remains inactive until the
client subscribes to Device Request and explicitly starts it with command
`0x4B`. See [Bluetooth LoRa mOTA source](#bluetooth-lora-mota-source).

ESP32 and nRF52 Companion UART characteristics require the same PIN-protected,
MITM-authenticated link. ESP32 advertises DisplayOnly capability so a central
must enter the PIN shown by the Companion; a Just Works bond is insufficient.

### Connection Steps

1. **Scan for Devices**
    - Scan for BLE devices advertising the MeshCore Service UUID
    - Optionally filter by device name (typically contains "MeshCore" prefix)
    - Note the device MAC address for reconnection

2. **Connect to GATT**
    - Connect to the device using the discovered MAC address
    - Wait for connection to be established

3. **Discover Services and Characteristics**
    - Discover the service with UUID `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
    - Discover the RX characteristic `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
        - Your app writes to this, the firmware reads from this
    - Discover the TX characteristic `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
        - The firmware writes to this, your app reads from this

4. **Enable Notifications**
    - Subscribe to notifications on the TX characteristic to receive data from the firmware

5. **Send Initial Commands**
    - Send `CMD_APP_START` to identify your app to firmware and get radio settings
    - Send `CMD_DEVICE_QUERY` to fetch device info and negotiate supported protocol versions
    - Send `CMD_SET_DEVICE_TIME` to set the firmware clock
    - Send `CMD_GET_CONTACTS` to fetch all contacts
    - Send `CMD_GET_CHANNEL` multiple times to fetch all channel slots
    - Send `CMD_SYNC_NEXT_MESSAGE` to fetch the next message stored in firmware
    - Setup listeners for push codes, such as `PUSH_CODE_MSG_WAITING` or `PUSH_CODE_ADVERT`
    - See [Commands](#commands) section for information on other commands

**Note**: MeshCore devices may disconnect after periods of inactivity. Implement auto-reconnect logic with exponential backoff.

### BLE Write Type

When writing commands to the RX characteristic, specify the write type:

- **Write with Response** (default): Waits for acknowledgment from device
- **Write without Response**: Faster but no acknowledgment

**Platform-specific**:

- **Android**: Use `BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT` or `WRITE_TYPE_NO_RESPONSE`
- **iOS**: Use `CBCharacteristicWriteType.withResponse` or `.withoutResponse`
- **Python (bleak)**: Use `write_gatt_char()` with `response=True` or `False`

**Recommendation**: Use write with response for reliability.

### MTU (Maximum Transmission Unit)

The default BLE MTU is 23 bytes (20 bytes payload). For larger commands like `SET_CHANNEL` (50 bytes), you may need to:

1. **Request Larger MTU**: Request MTU of 512 bytes if supported
    - Android: `gatt.requestMtu(512)`
    - iOS: `peripheral.maximumWriteValueLength(for:)`
    - Python (bleak): MTU is negotiated automatically

### Command Sequencing

**Critical**: Commands must be sent in the correct sequence:

1. **After Connection**:
    - Wait for BLE connection to be established
    - Wait for services/characteristics to be discovered
    - Wait for notifications to be enabled
    - Now you can safely send commands to the firmware

2. **Command-Response Matching**:
    - Send one command at a time
    - Wait for a response before sending another command
    - Use a timeout (typically 5 seconds)
    - Match response to command by type (e.g: `CMD_GET_CHANNEL` -> `RESP_CODE_CHANNEL_INFO`)

### Command Queue Management

For reliable operation, implement a command queue.

**Queue Structure**:

- Maintain a queue of pending commands
- Track which command is currently waiting for a response
- Only send next command after receiving response or timeout

**Error Handling**:

- On timeout, clear current command, process next in queue
- On error, log error, clear current command, process next

---

## Packet Structure

The MeshCore protocol uses a binary format with the following structure:

- **Commands**: Sent from app to firmware via RX characteristic
- **Responses**: Received from firmware via TX characteristic notifications
- **All multi-byte integers**: Little-endian byte order (except CayenneLPP which is Big-endian)
- **All strings**: UTF-8 encoding

Most packets follow this format:
```
[Packet Type (1 byte)] [Data (variable length)]
```

The first byte indicates the packet type (see [Response Parsing](#response-parsing)).

---

## Commands

The first byte selects the command. This is the current protocol-v14 command
catalog; bytes `0x2C`-`0x31` are parked and `0x35` is unused.

| Byte | Firmware name | Purpose |
|---|---|---|
| `0x01` | `CMD_APP_START` | Start an app session and request self information. |
| `0x02` | `CMD_SEND_TXT_MSG` | Send text to a contact. |
| `0x03` | `CMD_SEND_CHANNEL_TXT_MSG` | Send channel text. |
| `0x04` | `CMD_GET_CONTACTS` | Enumerate contacts, optionally modified since a timestamp. |
| `0x05` / `0x06` | `CMD_GET_DEVICE_TIME` / `CMD_SET_DEVICE_TIME` | Read or set the device clock. |
| `0x07` / `0x08` | `CMD_SEND_SELF_ADVERT` / `CMD_SET_ADVERT_NAME` | Advertise self or change the advertised name. |
| `0x09` | `CMD_ADD_UPDATE_CONTACT` | Add or update a contact. |
| `0x0A` | `CMD_SYNC_NEXT_MESSAGE` | Dequeue the next pending message. |
| `0x0B` / `0x0C` | `CMD_SET_RADIO_PARAMS` / `CMD_SET_RADIO_TX_POWER` | Set radio parameters or transmit power. |
| `0x0D` | `CMD_RESET_PATH` | Reset a contact's learned path. |
| `0x0E` | `CMD_SET_ADVERT_LATLON` | Set advertised coordinates. |
| `0x0F` | `CMD_REMOVE_CONTACT` | Remove a contact. |
| `0x10` / `0x11` / `0x12` | `CMD_SHARE_CONTACT` / `CMD_EXPORT_CONTACT` / `CMD_IMPORT_CONTACT` | Share, export, or import contact data. |
| `0x13` | `CMD_REBOOT` | Reboot after the required confirmation body. |
| `0x14` | `CMD_GET_BATT_AND_STORAGE` | Read battery and storage usage. |
| `0x15` | `CMD_SET_TUNING_PARAMS` | Set tuning parameters. |
| `0x16` | `CMD_DEVICE_QUERY` | Negotiate protocol support and read device information. |
| `0x17` / `0x18` | `CMD_EXPORT_PRIVATE_KEY` / `CMD_IMPORT_PRIVATE_KEY` | Export or import identity key material when enabled. |
| `0x19` | `CMD_SEND_RAW_DATA` | Send an application raw-data packet. |
| `0x1A`-`0x1D` | `CMD_SEND_LOGIN` through `CMD_LOGOUT` | Manage a server connection. |
| `0x1E` | `CMD_GET_CONTACT_BY_KEY` | Look up a contact by public-key prefix. |
| `0x1F` / `0x20` | `CMD_GET_CHANNEL` / `CMD_SET_CHANNEL` | Read or write a channel slot. |
| `0x21`-`0x23` | `CMD_SIGN_START` through `CMD_SIGN_FINISH` | Stream data for identity signing. |
| `0x24` | `CMD_SEND_TRACE_PATH` | Trace a direct route. |
| `0x25` | `CMD_SET_DEVICE_PIN` | Set or clear the device PIN. |
| `0x26` | `CMD_SET_OTHER_PARAMS` | Set telemetry, location, ACK, and related preferences. |
| `0x27` | `CMD_SEND_TELEMETRY_REQ` | Send the legacy telemetry request. |
| `0x28` / `0x29` | `CMD_GET_CUSTOM_VARS` / `CMD_SET_CUSTOM_VAR` | Read or set custom variables. |
| `0x2A` | `CMD_GET_ADVERT_PATH` | Read a cached advertisement path. |
| `0x2B` | `CMD_GET_TUNING_PARAMS` | Read tuning parameters. |
| `0x32` | `CMD_SEND_BINARY_REQ` | Send an application binary request. |
| `0x33` | `CMD_FACTORY_RESET` | Factory-reset after the required confirmation body. |
| `0x34` | `CMD_SEND_PATH_DISCOVERY_REQ` | Request path discovery. |
| `0x36` | `CMD_SET_FLOOD_SCOPE_KEY` | Select scoped or unscoped flood behavior. |
| `0x37` | `CMD_SEND_CONTROL_DATA` | Send zero-hop control data. |
| `0x38` | `CMD_GET_STATS` | Read core, radio, or packet statistics. |
| `0x39` | `CMD_SEND_ANON_REQ` | Send an anonymous request. |
| `0x3A` / `0x3B` | `CMD_SET_AUTOADD_CONFIG` / `CMD_GET_AUTOADD_CONFIG` | Write or read automatic-contact policy. |
| `0x3C` | `CMD_GET_ALLOWED_REPEAT_FREQ` | Read allowed client-repeat frequency ranges. |
| `0x3D` | `CMD_SET_PATH_HASH_MODE` | Set path-hash width mode. |
| `0x3E` | `CMD_SEND_CHANNEL_DATA` | Send a channel binary datagram. |
| `0x3F` / `0x40` | `CMD_SET_DEFAULT_FLOOD_SCOPE` / `CMD_GET_DEFAULT_FLOOD_SCOPE` | Write or read the default flood scope. |
| `0x41` | `CMD_SEND_RAW_PACKET` | Queue a fully encoded raw mesh packet. |
| `0x42` | `CMD_RUN_CLI_COMMAND` | Run a local CLI command (protocol v14+). |
| `0x4A` | `CMD_EXEC_LOCAL_OTA_CONTROL` | Run one bounded local TempRadio or OTA command on a Full Companion. |
| `0x4B` | `CMD_BLE_MOTA_SOURCE` | Query, start, or stop an nRF52 Full Companion's Bluetooth-backed LoRa mOTA source. |
| `0x78`-`0x7F` | Deprecated hardware-setting aliases | Receive-only compatibility for clients shipped before command `0x42` became the canonical settings path. |

The sections below detail the most common frames. Refer to the source named
above for command bodies that are not expanded here.

`CMD_RUN_CLI_COMMAND` is followed by the local CLI text without a terminating
NUL. The device returns `RESP_CODE_CLI_REPLY` (`0x1D`) followed by the reply
text. This is separate from sending a remote on-air CLI command with
`CMD_SEND_TXT_MSG` and `TXT_TYPE_CLI_COMMAND`. The body must contain at least
one byte and must not contain an embedded NUL. An unknown command is returned
as the normal CLI reply text `Unknown command`, not as an error frame. Clients
may prefix the CLI text with any two-character correlation tag and `|` (for
example, `A7|get radio.rxgain`); the reply preserves that prefix.

Full Companion clients can send `version` through this command to receive the
untruncated build identity, for example `Companion 1.17.1.5-... (protocol 14,
build 31-Aug-2026)`. This deliberately supplements rather than changes the
20-byte legacy version field in `RESP_CODE_DEVICE_INFO`, so existing clients
keep the same frame layout.

Firmware from this fork predating the upstream `0x42` allocation used
`0x42`-`0x49` for these eight settings. This firmware accepts those values as
deprecated inbound aliases so existing clients continue to work. A one-byte
`0x42` frame is the legacy FEM-gain GET; `0x42` followed by text is the official
`CMD_RUN_CLI_COMMAND`. New clients should use `CMD_RUN_CLI_COMMAND` for all of
these settings, rather than allocating additional command bytes. For example,
send `0x42` followed by `get radio.rxgain` or `set radio.rxgain on`. The reply is
`RESP_CODE_CLI_REPLY` followed by the normal CLI reply text.

Two deprecated binary alias blocks remain receive-only for compatibility:

| Setting | Original alias | Later fork alias | GET body/reply | SET body/reply |
|---|---:|---:|---|---|
| FEM receive gain | `0x42` / `0x43` | `0x78` / `0x79` | No body; `OK, state` | One byte `0`/`1`; `OK` |
| Radio receive gain | `0x44` / `0x45` | `0x7A` / `0x7B` | No body; `OK, state` | One byte `0`/`1`; `OK` |
| WiFi power save | `0x46` / `0x47` | `0x7C` / `0x7D` | No body; `OK, mode` | One mode byte `0`-`2`; `OK` |
| Bluetooth name | `0x48` / `0x49` | `0x7E` / `0x7F` | No body; `OK, custom, name` | Zero to 31 UTF-8 bytes; `OK` |

Each pair lists GET then SET. Here `OK` is `RESP_CODE_OK`; the remaining reply
bytes have the same meanings as the CLI settings below. A bare `0x42` is the
old FEM GET, while `0x42` plus at least one text byte is
`CMD_RUN_CLI_COMMAND`. New clients must use the framed CLI form; these aliases
exist only so deployed clients do not break after a firmware update.

The equivalent framed CLI commands are:

| Setting | Commands |
|---|---|
| Radio receive gain | `get radio.rxgain`; `set radio.rxgain on|off` |
| FEM receive gain | `get radio.fem.rxgain`; `set radio.fem.rxgain on|off` |
| WiFi power save | `get wifi.powersave`; `set wifi.powersave none|min|max` |
| Bluetooth name | `get bluetooth.name`; `set bluetooth.name <name|default>` |

The framed form works over the normal binary USB, BLE, or TCP transport and
does not need the USB terminal-start token. Unsupported settings return the
same explanatory text as the local CLI.

WiFi power-save modes are:

| Value | Mode |
|---:|---|
| `0` | `min` - minimum modem sleep |
| `1` | `none` - no modem sleep |
| `2` | `max` - maximum modem sleep |

A Full Companion that runs BLE and infrastructure WiFi simultaneously rejects
WiFi mode `none` because coexistence requires modem sleep. A Full Companion
using ESP-NOW as its primary mesh radio also rejects `max`, because maximum
modem sleep can miss broadcasts that the access point cannot buffer. If an
older image saved a conflicting value, the effective mode is capped to and
reported as `min`. Device `powersaving` remains independent.

The SenseCAP Indicator Full profiles run exactly one secondary wireless
Companion transport per boot. Their active-mode constraints are:

| Indicator mode | Accepted `wifi.powersave` values |
|---|---|
| LoRa + infrastructure WiFi | `none`, `min`, `max` |
| LoRa + BLE | `min`, `max`; infrastructure WiFi is not started |
| ESP-NOW + infrastructure WiFi | `none`, `min`; `max` conflicts with primary ESP-NOW |
| ESP-NOW + BLE | `min`; infrastructure WiFi is not started and primary ESP-NOW remains active |

The Bluetooth name can be configured over USB, BLE, or TCP. Use
`set bluetooth.name default` to restore `MeshCore-<advert name>`; an empty CLI
value is rejected. (`clear` is also accepted as an alias for `default`.) A
custom name is limited to 31 valid UTF-8 bytes and takes effect after reboot.

### Bluetooth LoRa mOTA source

Protocol v14 lets a phone use an nRF52 Full Companion as the source for a
remote repeater update without a USB computer. The normal Companion service
still carries contacts, repeater login, CLI messages, and these two control
commands. The separate mOTA service carries only host-folder request/response
frames.

`CMD_EXEC_LOCAL_OTA_CONTROL` (`0x4A`) is followed by 1-174 printable ASCII
bytes. Full Companion accepts only these local command families:

```text
tempradio <freq_kHz>,<bw_kHz>,<sf>,<cr>,<minutes>
normalradio
ota ...
```

`ota folder ...` is deliberately rejected because USB and Bluetooth source
ownership must not be changed through the wrong transport. Embedded NUL, CR,
LF, other control bytes, non-ASCII bytes, empty commands, and oversized frames
return `ERR_CODE_ILLEGAL_ARG`. A recognized command replies with
`RESP_CODE_OK`, one unsigned reply-length byte, and exactly that many printable
result bytes. Shell metacharacters are rejected as well; the text is dispatched
only to the in-firmware parser and is never passed to a host shell. Firmware
without the Full Companion feature returns `ERR_CODE_UNSUPPORTED_CMD`.

`CMD_BLE_MOTA_SOURCE` (`0x4B`) has one action byte:

| Action | Meaning |
| ---: | --- |
| `0` | Read status without changing it. |
| `1` | Attach and enumerate the subscribed Bluetooth host's `.mota` catalog. |
| `2` | Detach the Bluetooth source. |

Current firmware returns eleven bytes (legacy protocol-v14 previews returned
the seven-byte prefix only):

```text
00 action flags offered_le16 advertised_le16 source_packets_sent_le32
```

Flag bit `0x01` means the encrypted GATT channel is connected and Device
Request notifications are enabled. Bit `0x02` means the Bluetooth catalog is
attached. Bit `0x04` means USB or another folder transport currently owns the
source slot. Start without a ready subscription, or while another source link
owns the slot, returns `ERR_CODE_BAD_STATE`. A non-nRF52 Full Companion returns
`ERR_CODE_UNSUPPORTED_CMD`. `source_packets_sent` is a per-attachment count of
OTA packets accepted by the Companion's LoRa transmit adapter, including
catalog/manifest traffic, data, proofs, and retries. It wraps as an unsigned
32-bit value. Clients should accept the legacy seven-byte response and display
the packet counter as unavailable.

After a successful start, the device sends the same bounded seeder frames used
by `motatool serve` on Device Request:

```text
device -> host: 'M' 'S' op args... xor(op || args)
host -> device: 'm' 's' op status payload... xor(all prior bytes)
```

Device requests are at most 11 bytes. A source response is at most 197 bytes.
The host may split one response across multiple write-with-response operations
when the negotiated ATT payload is smaller; it must preserve byte order and
must not interleave another response. Bad checksums, partial frames, overflow,
unsubscribe, loss of encryption, or disconnect fail closed. The firmware then
detaches the catalog and stops advertising its entries. USB and Bluetooth
folder sources are mutually exclusive.

A Linux reference controller and seeder is provided at
`tools/ble_mota/ble_mota_seeder.py`. It verifies every input with `motatool`
before offering it. A mobile implementation should apply the same complete
container verification before serving files.

### 1. App Start

**Purpose**: Initialize communication with the device. Must be sent first after connection.

**Command Format**:
```
Byte 0: 0x01
Bytes 1-7: Reserved (currently ignored by firmware)
Bytes 8+: Application name (UTF-8, optional)
```

**Example** (hex):
```
01 00 00 00 00 00 00 00 6d 63 63 6c 69
```

**Response**: `PACKET_SELF_INFO` (0x05)

---

### 2. Device Query

**Purpose**: Query device information.

**Command Format**:
```
Byte 0: 0x16
Byte 1: Highest companion protocol version understood by the app
```

**Example** (hex):
```
16 0E
```

**Response**: `PACKET_DEVICE_INFO` (0x0D) with device information

---

### 3. Get Channel Info

**Purpose**: Retrieve information about a specific channel.

**Command Format**:
```
Byte 0: 0x1F
Byte 1: Channel index (0 through max_channels - 1)
```

**Example** (get channel 1):
```
1F 01
```

**Response**: `PACKET_CHANNEL_INFO` (0x12) with channel details

---

### 4. Set Channel

**Purpose**: Create or update a channel on the device.

**Command Format**:
```
Byte 0: 0x20
Byte 1: Channel index (0 through max_channels - 1)
Bytes 2-33: Channel Name (32 bytes, UTF-8, null-padded)
Bytes 34-49: Secret (16 bytes)
```

**Total Length**: 50 bytes

**Channel index**:
- Slot count is build-specific. Read `max_channels` from byte 3 of
  `PACKET_DEVICE_INFO`; current profiles commonly expose 1, 8, or 40 slots.
- No slot number has an intrinsic public/private meaning.

**Channel Name**:
- UTF-8 encoded
- Maximum 32 bytes
- Padded with null bytes (0x00) if shorter

**Secret Field** (16 bytes):
- Supply the exact 16-byte channel key. A private channel normally uses a
  cryptographically random key; known public and hashtag channels use their
  defined or derived key.
- An all-zero key is not the public-channel key.

**Example** (create channel "SMS" at index 1 with secret):
```
20 01 53 4D 53 00 00 ... (name padded to 32 bytes)
    [16 bytes of secret]
```

**Note**: The 32-byte secret variant is unsupported and returns `PACKET_ERROR`.

**Response**: `PACKET_OK` (0x00) on success, `PACKET_ERROR` (0x01) on failure

---

### 5. Send Channel Message

**Purpose**: Send a text message to a channel.

**Command Format**:
```
Byte 0: 0x03
Byte 1: 0x00
Byte 2: Channel index (0 through max_channels - 1)
Bytes 3-6: Timestamp (32-bit little-endian Unix timestamp, seconds)
Bytes 7+: Message Text (UTF-8, variable length)
```

**Timestamp**: Unix timestamp in seconds (32-bit unsigned integer, little-endian)

**Example** (send "Hello" to channel 1 at timestamp 1234567890):
```
03 00 01 D2 02 96 49 48 65 6C 6C 6F
```

**Response**: `PACKET_MSG_SENT` (0x06) on success

---

### 6. Send Channel Data Datagram

**Purpose**: Send a binary datagram to a channel. Unlike channel text messages, datagrams carry no built-in sender identity and no timestamp - applications needing either must encode them inside the binary payload.

**Command Format**:
```
Byte 0:                         0x3E
Byte 1:                         Channel index (0 through max_channels - 1)
Byte 2:                         Encoded path descriptor (0xFF = flood)
Bytes 3+:                       Encoded path bytes (omitted for 0xFF)
Next 2 bytes (little-endian):   Data Type (`data_type`, uint16)
Remaining bytes:                Binary payload (variable length)
```

For a direct send, the descriptor's low six bits are the hash count and its
high two bits are the hash size minus one. Current mesh packets accept one-,
two-, or three-byte hashes; the four-byte code is reserved. The following path
therefore occupies `hash_count * hash_size` bytes; the descriptor itself is not
a raw byte count.

**Example** (flood, `DATA_TYPE_DEV`, payload `A1 B2 C3`, channel 1):
```
3E 01 FF FF FF A1 B2 C3
```

**Data Type / Transport Mapping**:
- `0x0000` (`DATA_TYPE_RESERVED`) is invalid and rejected with `PACKET_ERROR`.
- `0xFFFF` (`DATA_TYPE_DEV`) is the developer namespace for experimenting and developing apps.
- Registered application/community namespaces occupy `0x0100`-`0xFEFF`; the remaining nonzero ranges are reserved for internal or development use. See the [Registered data_type values](#registered-data_type-values) table below.

**Limits**:
- Maximum payload length is `MAX_CHANNEL_DATA_LENGTH = MAX_FRAME_SIZE - 9 = 167` bytes.
- Larger payloads are rejected with `PACKET_ERROR` (`ERR_CODE_ILLEGAL_ARG`).

**Response**: `PACKET_OK` (0x00) on success, or `PACKET_ERROR` (0x01) with one of:
- `ERR_CODE_NOT_FOUND` (2) - unknown `channel_idx`
- `ERR_CODE_ILLEGAL_ARG` (6) - invalid `path_len`, reserved `data_type` (`0x0000`), or payload larger than `MAX_CHANNEL_DATA_LENGTH`
- `ERR_CODE_TABLE_FULL` (3) - outbound send queue is full; retry later

**Inbound datagrams** are delivered to the host via `RESP_CODE_CHANNEL_DATA_RECV` (0x1B); see [Receive Channel Data Datagram](#receive-channel-data-datagram).

#### Registered `data_type` values

`data_type` is an **application identifier**, not a payload-format identifier. Each registered value identifies an application that owns its own internal payload schemas. The firmware does not inspect payload contents - `data_type` is transported opaquely.

| Value           | Constant             | Purpose                                                                                |
|-----------------|----------------------|----------------------------------------------------------------------------------------|
| 0x0000          | `DATA_TYPE_RESERVED` | Reserved; invalid on send                                                              |
| 0x0001 - 0x00FF | -                    | Reserved for internal use                                                              |
| 0x0100 - 0xFEFF | -                    | Registered application namespaces (see [number_allocations.md](number_allocations.md)) |
| 0xFF00 - 0xFFFE | -                    | Testing/development; no registration required                                          |
| 0xFFFF          | `DATA_TYPE_DEV`      | Developer/experimental namespace                                                       |

To register a new application, submit a PR adding a row to the table in [docs/number_allocations.md](number_allocations.md). Internal sub-formats within an allocated application ID are owned by that application and are not tracked in MeshCore firmware or this document.

---

### Receive Channel Data Datagram

Inbound group datagrams (radio-level `PAYLOAD_TYPE_GRP_DATA`, 0x06) are forwarded to the host as `RESP_CODE_CHANNEL_DATA_RECV` notifications.

**Frame Format** (`RESP_CODE_CHANNEL_DATA_RECV`, 0x1B):
```
Byte 0:                 0x1B (packet type)
Byte 1:                 SNR (signed int8, scaled x4 - divide by 4.0 to recover dB)
Bytes 2-3:              Reserved (clients MUST ignore)
Byte 4:                 Channel index (0 through max_channels - 1)
Byte 5:                 Path Length (actual path length when flooded, otherwise 0xFF for direct)
Bytes 6-7:              Data Type (uint16 little-endian)
Byte 8:                 Data Length
Bytes 9 .. 8+data_len:  Payload
```

**Path bytes are not forwarded**: Only `path_len` is reported in the receive frame - the path itself is not copied to the host. There are no path bytes between byte 5 and the data_type field at bytes 6-7, regardless of `path_len`.

**Path Length semantics differ between send and receive**:

| Direction | `path_len = 0xFF`               | `path_len != 0xFF`                                                                                                                           |
|-----------|---------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| Send      | Flood the network               | Direct route; the encoded path follows (low 6 bits = hash count, top 2 bits + 1 = hash size; on-wire byte count = `hash_count x hash_size`) |
| Receive   | Packet arrived via direct route | Packet was flooded; this is the encoded `pkt->path_len` field as observed (no path bytes follow)                                            |

In other words, the meaning of `0xFF` is inverted between the two directions, and on receive the field carries metadata only - never a routable path. `path_len` is an encoded byte (see `Packet::isValidPathLen` / `Packet::writePath` in `src/Packet.cpp`), not a raw byte count.

**Note**: The device may also emit `PACKET_MESSAGES_WAITING` (0x83) to notify the host that datagrams are queued; poll with `CMD_SYNC_NEXT_MESSAGE` (0x0A) to retrieve them.

**Parsing Pseudocode**:
```python
def parse_channel_data_recv(data):
    if len(data) < 9:
        return None
    snr_byte = data[1]
    snr = (snr_byte if snr_byte < 128 else snr_byte - 256) / 4.0
    channel_idx = data[4]
    path_len = data[5]
    data_type = int.from_bytes(data[6:8], 'little')
    data_len = data[8]
    if 9 + data_len > len(data):
        return None
    payload = data[9:9 + data_len]
    return {
        'snr': snr,
        'channel_idx': channel_idx,
        'path_len': path_len,
        'data_type': data_type,
        'payload': bytes(payload),
    }
```

---

### 7. Get Message

**Purpose**: Request the next queued message from the device.

**Command Format**:
```
Byte 0: 0x0A
```

**Example** (hex):
```
0A
```

**Response**: 
- `PACKET_CHANNEL_MSG_RECV` (0x08) or `PACKET_CHANNEL_MSG_RECV_V3` (0x11) for channel messages
- `PACKET_CONTACT_MSG_RECV` (0x07) or `PACKET_CONTACT_MSG_RECV_V3` (0x10) for contact messages
- `PACKET_CHANNEL_DATA_RECV` (0x1B) for channel data datagrams
- `PACKET_NO_MORE_MSGS` (0x0A) if no messages available

**Note**: Poll this command periodically to retrieve queued messages. The device may also send `PACKET_MESSAGES_WAITING` (0x83) as a notification when messages are available.

---

### 8. Get Battery and Storage

**Purpose**: Query device battery voltage and storage usage.

**Command Format**:
```
Byte 0: 0x14
```

**Example** (hex):
```
14
```

**Response**: `PACKET_BATTERY` (0x0C) with battery millivolts and storage information

---

## Channel Management

### Channel Types

1. **Public Channel**
    - Uses a publicly known 16-byte key: `8b3387e9c5cdea6ac9e5edbaa115cd72`
    - Anyone can join this channel, messages should be considered public
    - Used as the default public group chat
2. **Hashtag Channels**
    - Uses a secret key derived from the channel name
    - It is the first 16 bytes of `sha256("#test")`
    - For example hashtag channel `#test` has the key: `9cd8fcf22a47333b591d96a2b848b73f`
    - Traffic is encrypted on air, but anyone who knows or guesses the channel
      name can derive the key. Hashtag channels should not be treated as private.
    - Used as a topic based public group chat, separate from the default public channel
3. **Private Channels**
    - Uses a randomly generated 16-byte secret key
    - Messages should be considered private between those that know the secret
    - Users should keep the key secret, and only share with those you want to communicate with
    - Used as a secure private group chat

### Channel Lifecycle

1. **Set Channel**:
    - Read `max_channels` from device info, fetch those slots, and choose an
      unused slot (normally an empty name and zeroed key)
    - Generate or provide a 16-byte secret
    - Send `CMD_SET_CHANNEL` with name and a 16-byte secret
2. **Get Channel**:
    - Send `CMD_GET_CHANNEL` with channel index
    - Parse `RESP_CODE_CHANNEL_INFO` response
3. **Delete Channel**:
    - Send `CMD_SET_CHANNEL` with empty name and all-zero secret
    - Or overwrite with a new channel

---

## Message Handling

### Receiving Messages

Messages are received via the TX characteristic (notifications). The device sends:

1. **Channel Messages**:
   - `PACKET_CHANNEL_MSG_RECV` (0x08) - Standard format
   - `PACKET_CHANNEL_MSG_RECV_V3` (0x11) - Version 3 with SNR

2. **Contact Messages**:
   - `PACKET_CONTACT_MSG_RECV` (0x07) - Standard format
   - `PACKET_CONTACT_MSG_RECV_V3` (0x10) - Version 3 with SNR

3. **Notifications**:
   - `PACKET_MESSAGES_WAITING` (0x83) - Indicates messages are queued

### Contact Message Format

**Standard Format** (`PACKET_CONTACT_MSG_RECV`, 0x07):
```
Byte 0: 0x07 (packet type)
Bytes 1-6: Public Key Prefix (6 bytes, hex)
Byte 7: Path Length
Byte 8: Text Type
Bytes 9-12: Timestamp (32-bit little-endian)
Bytes 13-16: Signature (4 bytes, only if txt_type == 2)
Bytes 17+: Message Text (UTF-8)
```

**V3 Format** (`PACKET_CONTACT_MSG_RECV_V3`, 0x10):
```
Byte 0: 0x10 (packet type)
Byte 1: SNR (signed byte, multiplied by 4)
Bytes 2-3: Reserved
Bytes 4-9: Public Key Prefix (6 bytes, hex)
Byte 10: Path Length
Byte 11: Text Type
Bytes 12-15: Timestamp (32-bit little-endian)
Bytes 16-19: Signature (4 bytes, only if txt_type == 2)
Bytes 20+: Message Text (UTF-8)
```

**Parsing Pseudocode**:
```python
def parse_contact_message(data):
    packet_type = data[0]
    offset = 1
    
    # Check for V3 format
    if packet_type == 0x10:  # V3
        snr_byte = data[offset]
        snr = ((snr_byte if snr_byte < 128 else snr_byte - 256) / 4.0)
        offset += 3  # Skip SNR + reserved
    
    pubkey_prefix = data[offset:offset+6].hex()
    offset += 6
    
    path_len = data[offset]
    txt_type = data[offset + 1]
    offset += 2
    
    timestamp = int.from_bytes(data[offset:offset+4], 'little')
    offset += 4
    
    # If txt_type == 2, skip 4-byte signature
    if txt_type == 2:
        offset += 4
    
    message = data[offset:].decode('utf-8')
    
    return {
        'pubkey_prefix': pubkey_prefix,
        'path_len': path_len,
        'txt_type': txt_type,
        'timestamp': timestamp,
        'message': message,
        'snr': snr if packet_type == 0x10 else None
    }
```

### Channel Message Format

**Standard Format** (`PACKET_CHANNEL_MSG_RECV`, 0x08):
```
Byte 0: 0x08 (packet type)
Byte 1: Channel index (0 through max_channels - 1)
Byte 2: Path Length
Byte 3: Text Type
Bytes 4-7: Timestamp (32-bit little-endian)
Bytes 8+: Message Text (UTF-8)
```

**V3 Format** (`PACKET_CHANNEL_MSG_RECV_V3`, 0x11):
```
Byte 0: 0x11 (packet type)
Byte 1: SNR (signed byte, multiplied by 4)
Bytes 2-3: Reserved
Byte 4: Channel index (0 through max_channels - 1)
Byte 5: Path Length
Byte 6: Text Type
Bytes 7-10: Timestamp (32-bit little-endian)
Bytes 11+: Message Text (UTF-8)
```

**Parsing Pseudocode**:
```python
def parse_channel_message(data):
    packet_type = data[0]
    offset = 1
    
    # Check for V3 format
    if packet_type == 0x11:  # V3
        snr_byte = data[offset]
        snr = ((snr_byte if snr_byte < 128 else snr_byte - 256) / 4.0)
        offset += 3  # Skip SNR + reserved
    
    channel_idx = data[offset]
    path_len = data[offset + 1]
    txt_type = data[offset + 2]
    timestamp = int.from_bytes(data[offset+3:offset+7], 'little')
    message = data[offset+7:].decode('utf-8')
    
    return {
        'channel_idx': channel_idx,
        'timestamp': timestamp,
        'message': message,
        'snr': snr if packet_type == 0x11 else None
    }
```

### Sending Messages

Use the `SEND_CHANNEL_MESSAGE` command (see [Commands](#commands)).

**Important**: 
- The shared text envelope permits up to 160 UTF-8 bytes. For channel text,
  firmware prepends `<sender name>: ` inside that envelope, so the available
  message body is `160 - prefix_bytes` and varies with the configured name.
- Count encoded UTF-8 bytes, not Unicode characters. Split a longer message at
  valid UTF-8 boundaries.
- Include a chunk indicator (e.g., "[1/3] message text")

---

## Response Parsing

### Terminology

This document uses a spec-level naming convention (`PACKET_*`) for bytes the firmware sends back to the host. In the firmware source these same values are split across two `#define` families by purpose:

- `RESP_CODE_*` - direct replies to a command (e.g. `RESP_CODE_CHANNEL_DATA_RECV` = `PACKET_CHANNEL_DATA_RECV` = 0x1B).
- `PUSH_CODE_*` - asynchronous notifications not tied to a specific command (e.g. `PUSH_CODE_MSG_WAITING` = `PACKET_MESSAGES_WAITING` = 0x83).

Byte values are authoritative; names are aliases. When reading firmware source, `RESP_CODE_X` / `PUSH_CODE_X` correspond to this doc's `PACKET_X` of the same numeric value.

### Response types

| Value | Firmware name | Description |
|---|---|---|
| `0x00` | `RESP_CODE_OK` | Command succeeded. |
| `0x01` | `RESP_CODE_ERR` | Command failed; byte 1 is the error code. |
| `0x02` | `RESP_CODE_CONTACTS_START` | Contact enumeration started. |
| `0x03` | `RESP_CODE_CONTACT` | One contact record. |
| `0x04` | `RESP_CODE_END_OF_CONTACTS` | Contact enumeration ended. |
| `0x05` | `RESP_CODE_SELF_INFO` | Device self-information. |
| `0x06` | `RESP_CODE_SENT` | Send accepted, with route/tag/timeout data. |
| `0x07` / `0x08` | `RESP_CODE_CONTACT_MSG_RECV` / `RESP_CODE_CHANNEL_MSG_RECV` | Queued legacy-format message. |
| `0x09` | `RESP_CODE_CURR_TIME` | Current device time. |
| `0x0A` | `RESP_CODE_NO_MORE_MESSAGES` | Offline queue is empty. |
| `0x0B` | `RESP_CODE_EXPORT_CONTACT` | Exported contact bytes. |
| `0x0C` | `RESP_CODE_BATT_AND_STORAGE` | Battery and storage values. |
| `0x0D` | `RESP_CODE_DEVICE_INFO` | Protocol and build information. |
| `0x0E` | `RESP_CODE_PRIVATE_KEY` | Exported identity key, when enabled. |
| `0x0F` | `RESP_CODE_DISABLED` | Requested sensitive feature is disabled. |
| `0x10` / `0x11` | `RESP_CODE_CONTACT_MSG_RECV_V3` / `RESP_CODE_CHANNEL_MSG_RECV_V3` | Queued message with SNR fields. |
| `0x12` | `RESP_CODE_CHANNEL_INFO` | Channel slot information. |
| `0x13` / `0x14` | `RESP_CODE_SIGN_START` / `RESP_CODE_SIGNATURE` | Signing capacity or completed signature. |
| `0x15` | `RESP_CODE_CUSTOM_VARS` | Custom-variable data. |
| `0x16` | `RESP_CODE_ADVERT_PATH` | Cached advertisement path. |
| `0x17` | `RESP_CODE_TUNING_PARAMS` | Tuning parameters. |
| `0x18` | `RESP_CODE_STATS` | Requested statistics subtype. |
| `0x19` | `RESP_CODE_AUTOADD_CONFIG` | Automatic-contact policy. |
| `0x1A` | `RESP_ALLOWED_REPEAT_FREQ` | Allowed repeat-frequency ranges. |
| `0x1B` | `RESP_CODE_CHANNEL_DATA_RECV` | Queued channel datagram. |
| `0x1C` | `RESP_CODE_DEFAULT_FLOOD_SCOPE` | Default flood-scope data. |
| `0x1D` | `RESP_CODE_CLI_REPLY` | Text returned by `CMD_RUN_CLI_COMMAND`. |

### Asynchronous push types

| Value | Firmware name | Description |
|---|---|---|
| `0x80` | `PUSH_CODE_ADVERT` | Advertisement received. |
| `0x81` | `PUSH_CODE_PATH_UPDATED` | A contact path changed. |
| `0x82` | `PUSH_CODE_SEND_CONFIRMED` | A sent message was acknowledged. |
| `0x83` | `PUSH_CODE_MSG_WAITING` | One or more offline frames are waiting. |
| `0x84` | `PUSH_CODE_RAW_DATA` | Raw application data received. |
| `0x85` / `0x86` | `PUSH_CODE_LOGIN_SUCCESS` / `PUSH_CODE_LOGIN_FAIL` | Server login result. |
| `0x87` | `PUSH_CODE_STATUS_RESPONSE` | Server status response. |
| `0x88` | `PUSH_CODE_LOG_RX_DATA` | Radio receive log data. |
| `0x89` | `PUSH_CODE_TRACE_DATA` | Completed trace data. |
| `0x8A` | `PUSH_CODE_NEW_ADVERT` | Newly stored contact advertisement. |
| `0x8B` | `PUSH_CODE_TELEMETRY_RESPONSE` | Telemetry response. |
| `0x8C` | `PUSH_CODE_BINARY_RESPONSE` | Binary request response. |
| `0x8D` | `PUSH_CODE_PATH_DISCOVERY_RESPONSE` | Path-discovery response. |
| `0x8E` | `PUSH_CODE_CONTROL_DATA` | Control/discovery data. |
| `0x8F` | `PUSH_CODE_CONTACT_DELETED` | Oldest contact was deleted while making room. |
| `0x90` | `PUSH_CODE_CONTACTS_FULL` | Contact storage is full. |

### Parsing Responses

**PACKET_OK** (0x00):
```
Byte 0: 0x00
Bytes 1-4: Optional value (32-bit little-endian integer)
```

**PACKET_ERROR** (0x01):
```
Byte 0: 0x01
Byte 1: Error code (optional)
```

**PACKET_CHANNEL_INFO** (0x12):
```
Byte 0: 0x12
Byte 1: Channel Index
Bytes 2-33: Channel Name (32 bytes, null-terminated)
Bytes 34-49: Secret (16 bytes)
```

**Note**: The device returns the 16-byte channel secret in this response.

**PACKET_DEVICE_INFO** (0x0D):
```
Byte 0: 0x0D
Byte 1: Firmware Version (uint8)
Bytes 2+: Variable length based on firmware version

For firmware version >= 3:
Byte 2: Max Contacts Raw (uint8, actual = value * 2)
Byte 3: Max Channels (uint8)
Bytes 4-7: Active BLE PIN (32-bit little-endian; includes a generated session PIN)
Bytes 8-19: Firmware Build (12 bytes, UTF-8, null-padded)
Bytes 20-59: Model (40 bytes, UTF-8, null-padded)
Bytes 60-79: Version (20 bytes, UTF-8, null-padded)
Byte 80: Client repeat enabled/preferred (firmware v9+)
Byte 81: Path hash mode (firmware v10+)
```

**Parsing Pseudocode**:
```python
def parse_device_info(data):
    if len(data) < 2:
        return None
    
    fw_ver = data[1]
    info = {'fw_ver': fw_ver}
    
    if fw_ver >= 3 and len(data) >= 80:
        info['max_contacts'] = data[2] * 2
        info['max_channels'] = data[3]
        info['ble_pin'] = int.from_bytes(data[4:8], 'little')
        info['fw_build'] = data[8:20].decode('utf-8').rstrip('\x00').strip()
        info['model'] = data[20:60].decode('utf-8').rstrip('\x00').strip()
        info['ver'] = data[60:80].decode('utf-8').rstrip('\x00').strip()

    if fw_ver >= 9 and len(data) >= 81:
        info['client_repeat'] = data[80] != 0
    if fw_ver >= 10 and len(data) >= 82:
        info['path_hash_mode'] = data[81]
    
    return info
```

**PACKET_BATTERY** (0x0C):
```
Byte 0: 0x0C
Bytes 1-2: Battery Voltage (16-bit little-endian, millivolts)
Bytes 3-6: Used Storage (32-bit little-endian, KB)
Bytes 7-10: Total Storage (32-bit little-endian, KB)
```

**Parsing Pseudocode**:
```python
def parse_battery(data):
    if len(data) < 3:
        return None
    
    mv = int.from_bytes(data[1:3], 'little')
    info = {'battery_mv': mv}
    
    if len(data) >= 11:
        info['used_kb'] = int.from_bytes(data[3:7], 'little')
        info['total_kb'] = int.from_bytes(data[7:11], 'little')
    
    return info
```

**PACKET_SELF_INFO** (0x05):
```
Byte 0: 0x05
Byte 1: Advertisement Type
Byte 2: TX Power
Byte 3: Max TX Power
Bytes 4-35: Public Key (32 bytes, hex)
Bytes 36-39: Advertisement Latitude (32-bit little-endian, divided by 1e6)
Bytes 40-43: Advertisement Longitude (32-bit little-endian, divided by 1e6)
Byte 44: Multi ACKs
Byte 45: Advertisement Location Policy
Byte 46: Telemetry Mode (bitfield)
Byte 47: Manual Add Contacts (bool)
Bytes 48-51: Radio Frequency (32-bit little-endian, divided by 1000.0)
Bytes 52-55: Radio Bandwidth (32-bit little-endian, divided by 1000.0)
Byte 56: Radio Spreading Factor
Byte 57: Radio Coding Rate
Bytes 58+: Device Name (UTF-8, variable length, no null terminator required)
```

**Parsing Pseudocode**:
```python
def parse_self_info(data):
    if len(data) < 36:
        return None
    
    offset = 1
    info = {
        'adv_type': data[offset],
        'tx_power': data[offset + 1],
        'max_tx_power': data[offset + 2],
        'public_key': data[offset + 3:offset + 35].hex()
    }
    offset += 35
    
    lat = int.from_bytes(data[offset:offset+4], 'little') / 1e6
    lon = int.from_bytes(data[offset+4:offset+8], 'little') / 1e6
    info['adv_lat'] = lat
    info['adv_lon'] = lon
    offset += 8
    
    info['multi_acks'] = data[offset]
    info['adv_loc_policy'] = data[offset + 1]
    telemetry_mode = data[offset + 2]
    info['telemetry_mode_env'] = (telemetry_mode >> 4) & 0b11
    info['telemetry_mode_loc'] = (telemetry_mode >> 2) & 0b11
    info['telemetry_mode_base'] = telemetry_mode & 0b11
    info['manual_add_contacts'] = data[offset + 3] > 0
    offset += 4
    
    freq = int.from_bytes(data[offset:offset+4], 'little') / 1000.0
    bw = int.from_bytes(data[offset+4:offset+8], 'little') / 1000.0
    info['radio_freq'] = freq
    info['radio_bw'] = bw
    info['radio_sf'] = data[offset + 8]
    info['radio_cr'] = data[offset + 9]
    offset += 10
    
    if offset < len(data):
        name_bytes = data[offset:]
        info['name'] = name_bytes.decode('utf-8').rstrip('\x00').strip()
    
    return info
```

**PACKET_MSG_SENT** (0x06):
```
Byte 0: 0x06
Byte 1: Route Flag (0 = direct, 1 = flood)
Bytes 2-5: Tag / Expected ACK (4 bytes, little-endian)
Bytes 6-9: Suggested Timeout (32-bit little-endian, milliseconds)
```

**PACKET_SEND_CONFIRMED** (0x82):
```
Byte 0: 0x82
Bytes 1-4: ACK code (32-bit little-endian)
Bytes 5-8: Round-trip time (32-bit little-endian, milliseconds)
```

### Error Codes

`PACKET_ERROR` (0x01) carries a single-byte error code in byte 1. Values match the `ERR_CODE_*` constants defined in `examples/companion_radio/MyMesh.cpp`:

| Code | Constant (firmware)        | Description                                                                  |
|------|----------------------------|------------------------------------------------------------------------------|
| 1    | `ERR_CODE_UNSUPPORTED_CMD` | Unknown or unsupported command byte / sub-command                            |
| 2    | `ERR_CODE_NOT_FOUND`       | Target not found (channel, contact, message, etc.)                           |
| 3    | `ERR_CODE_TABLE_FULL`      | Internal queue or table is full - retry later                                |
| 4    | `ERR_CODE_BAD_STATE`       | Operation not valid in current device state (e.g. iterator already running)  |
| 5    | `ERR_CODE_FILE_IO_ERROR`   | Filesystem or storage I/O failure                                            |
| 6    | `ERR_CODE_ILLEGAL_ARG`     | Invalid argument (bad length, out-of-range value, reserved field, etc.)      |

**Note**: Error codes may vary by firmware version. Always check byte 1 of `PACKET_ERROR` response, and treat unknown codes as generic errors.

### Frame Handling

BLE implementations enqueue and deliver one protocol frame per BLE write/notification at the firmware layer.

- Apps should treat each characteristic write/notification as exactly one companion protocol frame
- Apps should still validate frame lengths before parsing
- Future transports or firmware revisions may differ, so avoid assuming fixed payload sizes for variable-length responses

### Response Handling

1. **Command-Response Pattern**:
   - Send command via RX characteristic
   - Wait for response via TX characteristic (notification)
   - Match the response by the expected response type; frames do not carry a
     general command sequence number
   - Handle timeout (typically 5 seconds)
   - Use command queue to prevent concurrent commands

2. **Asynchronous Messages**:
   - Device may send messages at any time via TX characteristic
   - Handle `PACKET_MESSAGES_WAITING` (0x83) by polling `GET_MESSAGE` command
   - Parse incoming messages and route to appropriate handlers
   - Validate frame length before decoding

3. **Response Matching**:
   - Match responses to commands by expected packet type:
     - `APP_START` -> `PACKET_SELF_INFO`
     - `DEVICE_QUERY` -> `PACKET_DEVICE_INFO`
     - `GET_CHANNEL` -> `PACKET_CHANNEL_INFO`
     - `SET_CHANNEL` -> `PACKET_OK` or `PACKET_ERROR`
     - `SEND_CHANNEL_MESSAGE` -> `PACKET_MSG_SENT`
     - `GET_MESSAGE` -> `PACKET_CHANNEL_MSG_RECV`, `PACKET_CONTACT_MSG_RECV`, `PACKET_CHANNEL_DATA_RECV`, or `PACKET_NO_MORE_MSGS`
     - `SEND_CHANNEL_DATA` -> `PACKET_OK` or `PACKET_ERROR`
     - `GET_BATTERY` -> `PACKET_BATTERY`

4. **Timeout Handling**:
   - Default timeout: 5 seconds per command
   - On timeout: Log error, clear current command, proceed to next in queue
   - Some commands may take longer (e.g., `SET_CHANNEL` may need 1-2 seconds)
   - Consider longer timeout for channel operations

5. **Error Recovery**:
   - On `PACKET_ERROR`: Log error code, clear current command
   - On connection loss: Clear command queue, attempt reconnection
   - On invalid response: Log warning, clear current command, proceed

---

## Example Implementation Flow

### Initialization

```python
# 1. Scan for MeshCore device
device = scan_for_device("MeshCore")

# 2. Connect to BLE GATT
gatt = connect_to_device(device)

# 3. Discover services and characteristics
service = discover_service(gatt, "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
rx_char = discover_characteristic(service, "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
tx_char = discover_characteristic(service, "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

# 4. Enable notifications on TX characteristic
enable_notifications(tx_char, on_notification_received)

# 5. Send AppStart command
send_command(rx_char, build_app_start())
wait_for_response(PACKET_SELF_INFO)
```

### Creating a Private Channel

```python
# 1. Generate 16-byte secret
secret_16_bytes = generate_secret(16)  # Use CSPRNG
secret_hex = secret_16_bytes.hex()

# 2. Build SET_CHANNEL command
channel_name = "YourChannelName"
channel_index = choose_unused_slot(max_channels)
command = build_set_channel(channel_index, channel_name, secret_16_bytes)

# 3. Send command
send_command(rx_char, command)
response = wait_for_response(PACKET_OK)

# 4. Store secret locally
store_channel_secret(channel_index, secret_hex)
```

### Sending a Message

```python
# 1. Build channel message command
channel_index = 1
message = "Hello, MeshCore!"
timestamp = int(time.time())
command = build_channel_message(channel_index, message, timestamp)

# 2. Send command
send_command(rx_char, command)
response = wait_for_response(PACKET_MSG_SENT)
```

### Receiving Messages

```python
def on_notification_received(data):
    packet_type = data[0]
    
    if packet_type == PACKET_CHANNEL_MSG_RECV or packet_type == PACKET_CHANNEL_MSG_RECV_V3:
        message = parse_channel_message(data)
        handle_channel_message(message)
    elif packet_type == PACKET_MESSAGES_WAITING:
        # Poll for messages
        send_command(rx_char, build_get_message())
```

---

## Best Practices

1. **Connection Management**:
   - Implement auto-reconnect with exponential backoff
   - Handle disconnections gracefully
   - Store last connected device address for quick reconnection

2. **Secret Management**:
   - Always use cryptographically secure random number generators
   - Store secrets securely (encrypted storage)
   - Never log or transmit secrets in plain text

3. **Message Handling**:
   - Send `CMD_SYNC_NEXT_MESSAGE` when `PUSH_CODE_MSG_WAITING` is received
   - Implement message deduplication to avoid displaying the same message twice

4. **Channel Management**:
    - Fetch all channel slots even if you encounter an empty slot
    - Ideally save new channels into the first empty slot

5. **Error Handling**:
   - Implement timeouts for all commands (typically 5 seconds)
   - Handle `RESP_CODE_ERR` responses appropriately

---

## Troubleshooting

### Connection Issues

- **Device not found**: Ensure device is powered on and advertising
- **Connection timeout**: Check Bluetooth permissions and device proximity
- **GATT errors**: Ensure proper service/characteristic discovery

### Command Issues

- **No response**: Verify notifications are enabled, check connection state
- **Error responses**: Verify command format and check error code
- **Timeout**: Increase timeout value or try again

### Message Issues

- **Messages not received**: Poll `GET_MESSAGE` command periodically
- **Duplicate messages**: Implement message deduplication using timestamp/content as a unique id
- **Message truncation**: Send long messages as separate shorter messages
