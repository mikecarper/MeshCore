<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/companion_offline_queue/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

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
| nRF52840 with at most 300 contact slots | 256 |
| nRF52840 with more than 300 contacts and the contact cache (non-mOTA-shared queue) | 256 total: up to 15 held DMs use slots until accepted |
| RAK WisMesh Tag ordinary BLE Companion | 240 total: up to 15 held DMs use slots until accepted |
| nRF52840 with more than 300 contacts but no contact cache | 208 ordinary frames plus a separate 15-DM queue |
| nRF52 Full Companion with the memory correction | 256 normally; 128 while mOTA owns shared storage |
| RP2040 | 256 |
| STM32 | 16 (one-key DM holding is omitted on flash-constrained STM32WL builds) |
| Known constrained classic ESP32 target override | 128 |
| Meshadventurer Full Companion | 16 |
| Constrained Full ESP32 fallback | 16 |

An explicit target `OFFLINE_QUEUE_SIZE` overrides the platform default. The
Heltec V2 and TLora V2 Full Companion profiles, for example, use 16 frames so
their combined WiFi, BLE, and LoRa mOTA image retains enough internal DRAM.
Meshadventurer SX1262 and SX1268 Full Companion use 16 frames together with 100
contacts and 30 group channels; their ordinary transport-specific images keep
128 frames and 40 channels.

The [nRF52 Full memory correction](old-releases/1.17.1.5.md#t096-full-companion-bluetooth-and-menu-freeze-report)
applies queue sharing to every nRF52 Full Companion. It keeps 256 frames
normally, retaining each board's contacts, channels, and all
Full transports. The upper 128 slots temporarily hold the mOTA context when
a source or TempRadio discovery session starts. Stopping or disconnecting the
USB/Bluetooth source returns all 256 slots; a discovery-only session returns
them when TempRadio ends. This shares a fixed memory region and avoids heap
fragmentation from resizing.

Existing unread messages retain their order. If more than 128 frames are
pending, mOTA refuses the loan and asks you to sync messages with a Companion
app first. While the loan is active, the overflow policy below applies at 128
frames. The original `26303793` 1.17.1.5 builds reserve the queue and mOTA state
separately. Sharing recovers about 19 KiB on nRF52 Full. The ST7735 color-display
Full profiles require at least 72 KiB of heap space at link time; OLED and
headless profiles use their own [memory budgets](research/firmware_memory_budget.md).

Standard, logging, MQTT, and Cascade build overlays retain the selected target
capacity. On cache-enabled high-contact nRF52 Companions, including the RAK4631
USB and BLE profiles, ordinary messages and consent-pending DMs share one
256-frame RAM pool. With no held DMs, all 256 slots are available to ordinary
messages; with 15 held DMs, up to 241 are. The app sees only ordinary messages
until a sender is accepted. The oldest held DM rolls off when a sixteenth
decryptable DM arrives. The shared pool is still volatile and clears on reboot.
Uncached high-contact profiles keep the conservative 208-frame default and
separate held queue. An explicit target `OFFLINE_QUEUE_SIZE` still takes
precedence.

The ordinary RAK WisMesh Tag BLE profile uses 240 frames to retain the runtime
RAM reserve for Bluetooth, filesystem buffers, and the main task. It keeps
350 contact slots and 40 group channels. All 240 slots are available when no
DMs are held; 15 held DMs leave 225 ordinary slots. This saves 2,832 bytes
compared with 256 frames. The Tag Full Companion retains 256 frames normally
and 128 while mOTA borrows queue storage.

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
