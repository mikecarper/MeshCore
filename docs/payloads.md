# Payload Format

Inside each [MeshCore Packet](./packet_format.md) is a payload, identified by the payload type in the packet header. The types of payloads are:

* Request, response, and plain text (`0x00`-`0x02`).
* Acknowledgment and node advertisement (`0x03`-`0x04`).
* Group text and group datagram (`0x05`-`0x06`).
* Anonymous request and returned path (`0x07`-`0x08`).
* Trace, multipart, control, and OTA (`0x09`-`0x0C`).
* Custom raw data (`0x0F`).

This document describes the shared payload envelopes implemented by the core.
Application-specific request, response, control, and custom bodies can add their
own formats.

NOTE: all 16 and 32-bit integer fields are Little Endian.

## Important concepts:

* Node hash: the first byte of the node's public key

## Node advertisement
This kind of payload notifies receivers that a node exists, and gives information about the node

| Field         | Size (bytes)    | Description                                              |
|---------------|-----------------|----------------------------------------------------------|
| public key    | 32              | Ed25519 public key of the node                           |
| timestamp     | 4               | unix timestamp of advertisement                          |
| signature     | 64              | Ed25519 signature of public key, timestamp, and app data |
| appdata       | rest of payload | optional, see below                                      |

Appdata

| Field         | Size (bytes)    | Description                                           |
|---------------|-----------------|-------------------------------------------------------|
| flags         | 1               | specifies which of the fields are present, see below  |
| latitude      | 4 (optional)    | decimal latitude multiplied by 1000000, integer       |
| longitude     | 4 (optional)    | decimal longitude multiplied by 1000000, integer      |
| feature 1     | 2  (optional)   | reserved for future use                               |
| feature 2     | 2  (optional)   | reserved for future use                               |
| name          | rest of appdata | name of the node                                      |

Appdata Flags

| Value  | Name           | Description                           |
|--------|----------------|---------------------------------------|
| `0x01` | is chat node   | advert is for a chat node             |
| `0x02` | is repeater    | advert is for a repeater              |
| `0x03` | is room server | advert is for a room server           |
| `0x04` | is sensor      | advert is for a sensor server         |
| `0x10` | has location   | appdata contains lat/long information |
| `0x20` | has feature 1  | Reserved for future use.              |
| `0x40` | has feature 2  | Reserved for future use.              |
| `0x80` | has name       | appdata contains a node name          |

## Acknowledgement

An acknowledgement that a message was received. Note that for returned path messages, an acknowledgement can be sent in the "extra" payload (see [Returned Path](#returned-path)) instead of as a separate acknowledgement packet. Current `CLI_DATA` commands do not cause acknowledgement responses, neither discrete nor extra; their text reply is the application-level result. Repeaters still ACK the legacy plain-text form before processing it.

Repeater remote CLI keeps one volatile copy of the most recently completed
reply, keyed by the authenticated sender, request timestamp, and command text.
Repeating that same logical request re-sends the text reply without executing
the command again. A retry must therefore preserve the original timestamp and
command text. The cache is cleared by reboot and replaced by the next completed
remote command; commands that intentionally produce no text reply remain
silent.

| Field    | Size (bytes) | Description                                                |
|----------|--------------|------------------------------------------------------------|
| checksum | 4            | CRC checksum of message timestamp, text, and sender pubkey |


## Returned path, request, response, and plain text message

Returned path, request, response, and plain text messages are all formatted in the same way. See the subsection for more details about the ciphertext's associated plaintext representation.

| Field            | Size (bytes)    | Description                                          |
|------------------|-----------------|------------------------------------------------------|
| destination hash | 1               | first byte of destination node public key            |
| source hash      | 1               | first byte of source node public key                 |
| cipher MAC       | 2               | MAC for encrypted data in next field                 |
| ciphertext       | rest of payload | encrypted message, see subsections below for details |

### Returned path

Returned path messages provide a description of the route a packet took from the original author. Receivers will send returned path messages to the author of the original message.

| Field       | Size (bytes) | Description                                                                                                          |
|-------------|--------------|----------------------------------------------------------------------------------------------------------------------|
| path descriptor | 1        | low 6 bits are the hash count; high 2 bits encode hash size minus one                                                |
| path        | count × size | encoded node-hash prefixes, each 1-3 bytes; the four-byte code is reserved                                           |
| extra type  | 1            | low nibble is the bundled payload type, such as acknowledgment or response; high nibble is reserved                 |
| extra       | rest of data | extra, bundled payload content, follows same format as main content defined by this document                         |

### Request

| Field        | Size (bytes)    | Description                              |
|--------------|-----------------|------------------------------------------|
| timestamp    | 4               | sender time (unix timestamp)             |
| request data | rest of payload | application-defined request payload body |

For the common chat/server helpers in `BaseChatMesh`, the current request type values are:

| Value  | Name      | Description                                        |
|--------|-----------|----------------------------------------------------|
| `0x01` | get stats | get stats of repeater or room server               |
| `0x02` | keepalive | keep-alive request used for maintained connections |

#### Get stats

Gets information about the node, possibly including the following:

* Battery level (millivolts)
* Current transmit queue length
* Current free queue length
* Last RSSI value
* Number of received packets
* Number of sent packets
* Total airtime (seconds)
* Total uptime (seconds)
* Number of packets sent as flood
* Number of packets sent directly
* Number of packets received as flood
* Number of packets received directly
* Error flags
* Last SNR value
* Number of direct route duplicates
* Number of flood route duplicates
* Number posted (?)
* Number of post pushes (?)

#### Get telemetry data

Not defined in `BaseChatMesh`. Sensor- and application-specific request payloads may be implemented by higher-level firmware.

#### Get Telemetry

Not defined in `BaseChatMesh`.

#### Get Min/Max/Ave  (Sensor nodes)

Not defined in `BaseChatMesh`.

#### Get Access List

Not defined in `BaseChatMesh`.

#### Get Neighbors

Not defined in `BaseChatMesh`.

#### Get Owner Info

Not defined in `BaseChatMesh`.


### Response

| Field   | Size (bytes)    | Description                       |
|---------|-----------------|-----------------------------------|
| content | rest of payload | application-defined response body |

Response contents are opaque application data. There is no single generic response envelope beyond the encrypted payload wrapper shown above.

### Plain text message

| Field              | Size (bytes)    | Description                                                                       |
|--------------------|-----------------|-----------------------------------------------------------------------------------|
| timestamp          | 4               | send time (unix timestamp)                                                        |
| txt_type + attempt | 1               | upper six bits are txt_type (see below), lower two bits are attempt number (0..3) |
| message            | rest of payload | the message content, see next table                                               |

txt_type

| Value  | Description               | Message content                                                          |
|--------|---------------------------|--------------------------------------------------------------------------|
| `0x00` | plain text message        | the plain text of the message                                            |
| `0x01` | CLI command               | the command text of the message                                          |
| `0x02` | signed plain text message | first four bytes is sender pubkey prefix, followed by plain text message |

For a room post, companion firmware uses its own monotonic clock for the
on-air timestamp and preserves that timestamp across application retries. Room
servers track post timestamps separately from login, request, and CLI traffic.
They also remember recent accepted posts by sender, timestamp, and text: an
exact retry is ACKed again without storing a duplicate, while stale or
same-timestamp mismatches are rejected.

## Anonymous request

| Field            | Size (bytes)    | Description                               |
|------------------|-----------------|-------------------------------------------|
| destination hash | 1               | first byte of destination node public key |
| public key       | 32              | sender's Ed25519 public key               |
| cipher MAC       | 2               | MAC for encrypted data in next field      |
| ciphertext       | rest of payload | encrypted message, see below for details  |

### Room server login

| Field          | Size (bytes)    | Description                                                                   |
|----------------|-----------------|-------------------------------------------------------------------------------|
| timestamp      | 4               | sender time (unix timestamp)                                                  |
| sync timestamp | 4               | sender's "sync messages SINCE x" timestamp                                    |
| password       | rest of message | password for room                                                             |

### Repeater/Sensor login

| Field          | Size (bytes)    | Description                                                                   |
|----------------|-----------------|-------------------------------------------------------------------------------|
| timestamp      | 4               | sender time (unix timestamp)                                                  |
| password       | rest of message | password for repeater/sensor                                                  |

### Repeater - Regions request

| Field          | Size (bytes) | Description                  |
|----------------|--------------|------------------------------|
| timestamp      | 4            | sender time (unix timestamp) |
| req type       | 1            | 0x01 (request sub type)      |
| reply path len | 1            | path len for reply           |
| reply path     | (variable)   | reply path                   |

### Repeater - Owner info request

| Field          | Size (bytes) | Description                  |
|----------------|--------------|------------------------------|
| timestamp      | 4            | sender time (unix timestamp) |
| req type       | 1            | 0x02 (request sub type)      |
| reply path len | 1            | path len for reply           |
| reply path     | (variable)   | reply path                   |

### Repeater - Clock and status request

| Field          | Size (bytes) | Description                  |
|----------------|--------------|------------------------------|
| timestamp      | 4            | sender time (unix timestamp) |
| req type       | 1            | 0x03 (request sub type)      |
| reply path len | 1            | path len for reply           |
| reply path     | (variable)   | reply path                   |


## Group text message

| Field        | Size (bytes)    | Description                                  |
|--------------|-----------------|----------------------------------------------|
| channel hash | 1               | first byte of SHA256 of channel's shared key |
| cipher MAC   | 2               | MAC for encrypted data in next field         |
| ciphertext   | rest of payload | encrypted message, see below for details     |

The plaintext contained in the ciphertext matches the format described in [plain text message](#plain-text-message). Specifically, it consists of a four byte timestamp, a flags byte, and the message. The flags byte will generally be `0x00` because it is a "plain text message". The message will be of the form `<sender name>: <message body>` (eg., `user123: I'm on my way`).

The sender name is unverified message text. Group messages contain no sender
signature, so any channel-key holder can choose any sender name.

## Group datagram

| Field        | Size (bytes)    | Description                                  |
|--------------|-----------------|----------------------------------------------|
| channel hash | 1               | first byte of SHA256 of channel's shared key |
| cipher MAC   | 2               | MAC for encrypted data in next field         |
| ciphertext   | rest of payload | encrypted data, see below for details        |

The data contained in the ciphertext uses the format below:

| Field     | Size (bytes)    | Description                                              |
|-----------|-----------------|----------------------------------------------------------|
| data type | 2               | Identifier for type of data. (See number_allocations.md) |
| data len  | 1               | byte length of data                                      |
| data      | rest of payload | (depends on data type)                                   |


## Control data

| Field        | Size (bytes)    | Description                                |
|--------------|-----------------|--------------------------------------------|
| flags        | 1               | upper 4 bits is sub_type                   |
| data         | rest of payload | typically unencrypted data                 |

### DISCOVER_REQ (sub_type)

| Field        | Size (bytes)    | Description                                  |
|--------------|-----------------|----------------------------------------------|
| flags        | 1               | 0x8 (upper 4 bits), prefix_only (lowest bit) |
| type_filter  | 1               | bit for each ADV_TYPE_*                      |
| tag          | 4               | randomly generate by sender                  |
| since        | 4               | (optional) epoch timestamp (0 by default)    |

### DISCOVER_RESP (sub_type)

| Field        | Size (bytes)    | Description                                |
|--------------|-----------------|--------------------------------------------|
| flags        | 1               | 0x9 (upper 4 bits), node_type (lower 4)    |
| snr          | 1               | signed, SNR*4                              |
| tag          | 4               | reflected back from DISCOVER_REQ           |
| pubkey       | 8 or 32         | node's ID (or prefix)                      |


## Trace

Trace packets use direct routing. Their normal direct-path header is empty;
instead, the intended route follows a fixed nine-byte trace header in the
payload. Each forwarding node appends its received SNR multiplied by four to
the packet's route-accumulator field.

| Field       | Size (bytes) | Description |
|-------------|--------------|-------------|
| tag         | 4            | Sender-selected trace identifier. |
| auth code   | 4            | Application-defined authentication/correlation value. |
| flags       | 1            | Low two bits encode the route hash size. Current senders use the legacy `1 << value` interpretation (1, 2, 4, or 8 bytes); receivers also accept the packed 1-4-byte interpretation where it is unambiguous. |
| route       | rest         | Concatenated node-hash prefixes for the requested direct route. |

At the destination, the application receives the tag, auth code, flags,
accumulated SNR bytes, and original route bytes. Trace therefore differs from a
normal direct packet whose path is carried entirely in the packet route field.

## Multipart

The first payload byte identifies the inner payload and how many packets remain:

| Field | Size (bytes) | Description |
|---|---|---|
| remaining + type | 1 | Upper nibble is the remaining-packet count; lower nibble is the inner payload type. |
| inner data | rest | Data for the inner payload type. |

The core currently creates and consumes multipart acknowledgments. For that
form, the low nibble is `0x03` and the next four bytes are the acknowledgment
CRC. Other multipart inner types are reserved for application or future use.

## OTA

An OTA payload contains one OTA protocol message and is normally sent by flood.
Its message types, integrity fields, and transfer state are defined in the
[OTA-over-LoRa protocol](ota_protocol.md). Builds without OTA support can still
relay an opaque OTA packet when their routing policy permits it.


## Custom packet

Custom packets have no defined format.
