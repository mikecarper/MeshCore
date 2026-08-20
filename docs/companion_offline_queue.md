# Companion Offline Message Queue

Companion firmware keeps received channel data, channel messages, and direct
messages in one pending queue until a Companion client requests them with the
sync-next-message command. This is volatile RAM, not message history in flash.
A reboot clears it.

## Default capacities

| Platform or memory profile | Pending frames |
| --- | ---: |
| ESP32 with configured PSRAM | 512 |
| ESP32 without PSRAM | 256 |
| nRF52840 | 256 |
| RP2040 | 256 |
| STM32 | 16 |
| Known constrained classic ESP32 target override | 128 |
| Constrained Full ESP32 fallback | 16 |

An explicit target `OFFLINE_QUEUE_SIZE` overrides the platform default. The
Heltec V2 and TLora V2 Full Companion profiles, for example, use 16 frames so
their combined WiFi, BLE, and LoRa mOTA image retains enough internal DRAM.
Standard, logging, MQTT, and Cascade build overlays retain the selected target
capacity; they do not silently shrink the queue.

Each queue slot currently costs 177 bytes. A 256-frame queue reserves 45,312
bytes, while a 512-frame queue reserves 90,624 bytes. There is no 256-frame
protocol limit: the queue length and indexes can represent 512 or more. The
practical limit is available RAM and the heap and stack headroom required by
the transports and display.

On ESP32 boards marked with `BOARD_HAS_PSRAM`, the queue is allocated from
PSRAM before WiFi and BLE start. A failed 512-frame allocation retries at 256,
then 128, and finally uses a 16-frame internal fallback. Full Companion prints
the capacity actually allocated in its startup memory line as
`offline_queue=<frames>`.

## Full queue behavior

The capacity is shared across Public, other channels, channel data, and direct
messages. It is not a per-channel count. When the queue is full, firmware
replaces the oldest queued channel frame so newer traffic can still arrive. If
the full queue contains only direct messages, a new frame is dropped rather
than deleting a direct message.

Queue order is preserved. Removal uses a ring index, so delivering one pending
message no longer copies every remaining frame; only the less-common removal
of an old channel frame from a full queue may shift entries.

Capacity is selected when firmware is compiled. There is no CLI or Companion
protocol setting to resize it at runtime.
