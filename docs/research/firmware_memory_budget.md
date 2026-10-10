<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/research/firmware_memory_budget/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Firmware memory checks

Every firmware environment runs `scripts/check_firmware_ram.py` against its
linked ELF before producing or uploading an image. `build.sh`, including
option 3, also requires a passing report before collecting release files.
Native host tests do not use a microcontroller RAM budget.

The check reserves room for enabled runtime allocations as well as static
data. A firmware image fitting its board's reported RAM total is insufficient:
the display, packet pool, USB, Bluetooth workers and WiFi can allocate after
startup. The T096 Full 1.17.1.5 report exposed this distinction.
MQTT uses an explicit startup minimum plus runtime connection admission. A
passing build report does not qualify its maximum TLS/WSS/outbox workload.

## What is counted

| Platform | Source of available runtime RAM |
| --- | --- |
| nRF52 | Actual `__HeapBase` and `__HeapLimit`; excludes SoftDevice, retained state, ISR stack and the dedicated 64 KiB mOTA arena where present |
| ESP32, S3, C3, C6 | Linked ESP-IDF memory-region, capability and reservation tables; only internal, byte-addressable heap counts |
| RP2040/RP2350 | `__end__` to `__HeapLimit`, according to the selected linker |
| STM32 | `_end` to `_estack`, minus `_Min_Stack_Size` |

ESP32 PSRAM, instruction-only RAM and RTC RAM never increase the internal
budget. On chips other than classic ESP32, the late-reclaimed ROM stack region
is excluded because its silicon-specific reservations are only known at boot.
Classic ESP32 additionally retains its existing 8 KiB **static DRAM** check.

The policy adds allowances for task stacks, radio packet pools, screen objects
and pixel buffers, filesystem/sensor allocations, enabled wireless stacks,
MQTT connections, OTA scratch and transient allocations. ESP32 images with
RS-232 reserve another 6,656 bytes even when UART defaults off: 2,560 bytes for
the source-bounded bridge object and allocation metadata, plus an unchanged
4 KiB for the UART driver and buffers. This
keeps runtime UART enablement in the budget beside MQTT and ESP-NOW.
T-LoRa V2.1-1.6 cannot fit all three transports: the qualified linked triple
image had 190,488 available internal bytes against 196,984 required bytes
under the earlier allocation estimates. The corrected equivalent requirement
was 193,400 bytes with the earlier flat MQTT allowance, still 2,912 bytes above
capacity. Its configured 16 KiB MQTT worker stack adds another 8 KiB under the
current startup policy; these figures describe historical linked checks.
Its ordinary release retains separate UART/ESP-NOW and MQTT/ESP-NOW Full
images; routing capacities remain unchanged.
A 160x80 ST7735
framebuffer needs 25,602 bytes. ESP32 SSD1306 uses a source-bounded 2 KiB
allowance for its fixed 1,024-byte framebuffer, driver and metadata; other
OLED and ordinary non-ESP32 allowances remain unchanged. nRF52 Full with that
color framebuffer must have at least 72 KiB available before startup allocations.
Headless and OLED devices use their own smaller totals. The JSON lists each
component and checks the largest available region against the largest planned
single allocation.

### MQTT startup minimum and runtime qualification

The `mqtt_startup_allowance` component keeps a 24,576-byte internal startup
minimum for the default 8,192-byte MQTTBridge worker stack and two active
clients. Each SDK client task defaults to 6,144 bytes and its maximum configured
receive/transmit buffers total 1,792 bytes. These stacks and buffers total
24,064 bytes for two clients, leaving 512 bytes within the existing allowance.
This is a minimum allowance, not a complete allocation bound for the clients,
transport objects, TLS records, certificate processing, WebSockets or outboxes.
The independent 16 KiB transient margin remains in the policy.

`MQTT_TASK_STACK_SIZE` above 8,192 bytes raises the startup requirement by its
actual increase and raises the required contiguous allocation when needed.
Smaller stacks and fewer runtime slots do not lower the existing allowance.
The default runtime slot array has six entries on PSRAM builds and three on
internal-only builds. The bridge can expose five active connections when PSRAM
is present and two otherwise; `MQTT_RUNTIME_SLOT_COUNT` can restrict that
capacity. A PSRAM board with unavailable PSRAM uses the two-connection cap.

The report's separate `mqtt.maximum_slot_stack_buffer_allowance_bytes` records
48,384 bytes for five exposed slots or 24,576 bytes for two, with larger worker
stacks added to either. The five-slot figure extends the minimum by 7,936 bytes
for each additional client task and buffer pair. It is not added to the startup
gate: extra clients allocate lazily, and every connection attempt must pass
runtime admission. It still excludes the maximum TLS/WSS/outbox workload.
`maximum_runtime_load_qualified` and `physical_validation_performed` are false
in these build reports. A `passed` startup check must not be presented as
qualification of five simultaneous PSRAM connections or two internal TLS
connections.

The `mqtt` report also names the production runtime admission thresholds.
Each attempt requires at least 16 KiB of DMA-capable internal free heap and a
4 KiB contiguous DMA block. A TLS attempt without PSRAM requires at least
60 KiB free and a 17,408-byte contiguous block. PSRAM allocation fallback must
leave the 16 KiB DMA reserve available. These thresholds describe free heap at
the time of admission; they are not additional linked capacity and the TLS
contiguous threshold is not a startup allocation requirement. Available linked
internal heap cannot establish how much DMA-capable heap remains or how it is
fragmented after boot. Only runtime observations can establish that.

Maximum-load qualification still needs physical boot and sustained reconnect,
publish and configuration-change tests with the selected number of brokers,
transport schemes, certificates and enabled UART/browser/OTA features. Record
DMA free heap, largest DMA block and allocation failures throughout that
workload. Host admission tests and passing ELF checks provide engineering
evidence without claiming that hardware validation has been performed.

The W12 Full MQTT profile remains unqualified: the current linked image has
182,768 internal bytes available against 198,432 required, including the
unchanged 16 KiB transient margin. Its standard repeater compiles and passes
the RAM check, but the 1,322,304-byte application exceeds the 1,310,720-byte
portable LoRa-OTA slot. Both limits also occur in the pre-stability baseline
(Full: 182,800 available against 200,480 required under the earlier OLED
estimate; standard application: 1,321,088 bytes). LR2021 compile and fault-test
coverage does not qualify either package for release.

The experimental RAK4631 combined Ethernet/UART2/adaptive-storage config adds
an explicit 2,560-byte allowance for the UART bridge and its TX semaphore.
Its Ethernet worker reserves 1,024 stack words (4,096 bytes) plus 256 bytes for
task metadata and allocator overhead. Its bounded TCP transmit queue allocates
512 bytes only while Ethernet is enabled; another 64 bytes cover allocator
overhead. The DHCP object, socket state, queue indices and SPI objects are
static and already reduce the linked heap capacity.

Ethernet and external OTA share an exclusive ownership contract. Enabling
Ethernet rejects a live or pinned transfer, releases idle external OTA buffers,
and blocks encoder, self-source, manual-staging and folder-source allocation
while Ethernet owns SPI. Disabling Ethernet parks the worker after socket/SPI
cleanup; the main loop deletes that parked task synchronously and frees its
TX queue before releasing ownership. The worker stack therefore cannot remain queued for idle-task
reclamation while OTA allocates again. The budget counts the larger of the
4,928-byte Ethernet worker/queue or 11,344-byte external OTA workspaces, with the UART
allowance alongside either mode. It keeps the 8 KiB loop stack, 63 flood rules,
and reserved 64 KiB OTA handoff arena.

The shared RAM changes now reserve the lazy folder-source buffers as well:
up to 4,096 bytes of source leaves and one 2,048-byte encoded output block,
plus allocation overhead, for the RAK4631. These can coexist with own-image
metadata and the encoder. The combined budget therefore reserves 11,344 bytes
for external OTA, or 10,304 with the device encoder disabled, against the
4,928-byte Ethernet worker/queue. Ethernet and external OTA remain exclusive.
Freeing static buffers increases available heap; their active allocation cost
still appears in the memory check.

This combined config uses a source-bounded 2 KiB SSD1306 allowance. Its named
128x64 geometry needs one 1,024-byte framebuffer, reused on later starts;
the driver itself is embedded in a linked global, measured at 156 bytes in
the ARM test ELF. Including that already-counted object again plus 32 bytes
for allocation overhead still leaves 836 bytes within the allowance.
A compile-time assertion
covers framebuffer, complete driver object and allocator overhead within that
bound. This corrects a padded allocation estimate; it does not free actual RAM.
Other images retain their existing display budgets. These are local
test configs, not a qualification of a published or physically tested image.

## Shared RAM reductions

These changes apply to every board that compiles the corresponding feature.
They retain stack sizes, radio packet pools, USB buffering, table capacities,
stored settings, OTA block sizes and source-image limits.

| Change | Scope | RAM reduction |
| --- | --- | --- |
| Repeater ACL session state | Pure repeater builds on all platforms | 36 bytes per client on 32-bit targets; 1,152 bytes for 32 clients |
| Compact recent-repeater records | Repeaters with recent history enabled | 12 to 9 bytes per record; 1,536 bytes for 512 records |
| Compact flood-rule flags and aligned counters | Generalized repeater and room rule engines | 200 to 192 bytes per rule; 504 bytes for 63 repeater rules or 248 bytes for 31 room rules |
| Streaming Merkle proof generation | All OTA roles and platforms | Removes full-leaf-table proof scratch; compressed output needs only one logical block |
| Source buffers allocated on demand | All OTA roles and platforms | Leaves allocate only the selected image's digest count; unused leaves/output return to the heap |

The ACL source-role pre-script contributes one common compiler definition for
the entire build. Room/sensor and unknown roles keep the complete session
layout. The persisted four-byte room synchronization field stays in repeater
records, so existing ACL files and their checksums remain compatible.

Recent-history timestamps retain all 32 bits using explicit byte operations,
avoiding unaligned multi-byte accesses on ARM. The table belongs to the mesh
loop; network command handlers marshal their work to that loop. These byte
operations do not provide synchronization for hypothetical concurrent users.
Flood flags remain internal; FPF files serialize each field explicitly and
never expose compiler bitfield layout. Multibyte counters remain aligned.
Rule deduplication compares configured fields explicitly, so neither live
rate-window counters nor struct padding can make different rules share a slot.

OTA leaf capacity remains controlled by `OTA_PROOFGEN_SCRATCH`, independently
of output-buffer size. The maximum source buffers are 4 KiB plus one block on
ordinary nRF52, 8 KiB plus one block with SD storage, and 16 KiB plus one block
on ESP32. Generic folder sources need no output allocation for raw DATA or
proofs; own-image raw serving retains its caller-owned compatibility buffer.
Its nRF52 allowance remains in the budget even when the device encoder is off.
Queued DATA, PROOF and MANIFEST responses pin their buffers through radio
backpressure.
Explicit cancellation or source detach releases the reloadable cache; an
unpinned cache also expires after 30 seconds. Attached source descriptors
remain advertised and reload on the next request. Allocation failure refuses
source loading safely, or falls back to raw DATA if only compression output
is unavailable. Receive reassembly and serving/inflate buffers remain separate.

On RAK4631, the OTA context's permanent allocation falls from 28,816 to 20,640
bytes, an 8,176-byte reduction before table savings. This is idle heap capacity,
not an additional 8 KiB saving on top of the active-operation savings. The RAM
policy reserves lazy buffers when active and separately checks the largest
contiguous allocation. ESP32 history uses its actual default capacities:
256 records on classic ESP32 and 2,048 on S2/S3/C-series, unless overridden.

The matched RAK4631 Full combined trial on 8 October 2026 retained all sensor
drivers, 32 ACL clients, 63 flood rules, 512 recent repeater records, the 8 KiB loop
stack and the reserved 64 KiB OTA arena:

| Linked measurement | Before shared RAM changes | After all five changes |
| --- | ---: | ---: |
| Available heap capacity before startup | 73,580 bytes | 83,300 bytes |
| Reserved startup/active allocations | 72,584 bytes | 77,104 bytes |
| Capacity beyond those reservations | 996 bytes | 6,196 bytes |
| Application flash | 585,700 bytes | 586,260 bytes |

The permanent allocations shrink by 9,720 bytes. The smaller ACL and flood
tables reduce startup allowances by another 1,656 bytes, while the new
6,176-byte folder-source allowance counts buffers when active. The resulting
capacity beyond reservations improves by 5,200 bytes; flash grows by 560 bytes.
The reports are bound to ELF hashes
`a16c02e43c12d7dadd94b92b9b80cdd22eced7f67e085b1fffb62ea39f09fcce`
and `f78110d1609334621098cb6db2039227c2f8fc9ac7f9e6ef7dc3cc63eb8d502f`,
respectively. These are linked capacity checks, not measured free heap after
boot or physical Ethernet/OTA soak results.

The five changes pass representative repeater builds for RAK4631 combined
Full, Heltec V4 (ESP32-S3), Heltec V2 (classic ESP32), and Seeed XIAO RP2040.
RAK4631 room-server and Heltec V4 sensor builds also pass while retaining
their complete ACL session layouts. Wio E5 Mini repeater compilation passes,
but linking exceeds flash capacity: the baseline already overflowed by
27,200 bytes, and these changes add 224 bytes for a 27,424-byte overflow.
That STM32 image is unqualified and is not release-ready.

Regression coverage includes unchanged ACL/FPF file bytes, full capacities,
timestamp rollover, flag combinations, ARM access widths/alignment, streamed
proofs against the existing reference algorithm, allocation failures, source
reload, queued-response lifetimes, and concurrent compressed serving/receiving.
Synthetic and representative build results are distinct from physical soak
qualification of every board.

Generate matched Full/Reduced baseline and combined configs outside the
repository without starting PlatformIO:

```sh
python3 scripts/generate_rak4631_combined_test.py
```

Run its printed commands one at a time and preserve each ELF and memory report
before the next build reuses the environment directory.

The combined prototype starts with Ethernet off after every restart. Use the
USB CLI commands `eth on`, `eth.status`, and `eth off`; `get eth` reports the
requested mode. UART2 remains independently controlled by
`set bridge.uart 2` and `set rs232.enabled on|off`. Full retains every sensor
driver; absent sensors do not require a different image.

With Ethernet on, OTA uses eligible internal-flash deltas. External full-image
staging, self-serving, manual serving buffers and OTA folders are unavailable
until Ethernet is off. Active or staged OTA work prevents a mode change.
RAK15001 and Ethernet share chip-select 26 and cannot be used together; the
prototype refuses that combination. External storage also needs a matching
bootloader that permits internal OTA while Ethernet is enabled.

TCP sends and receives use bounded register operations; a stalled peer cannot
hold the mesh loop waiting for an acknowledgement. DHCP and listener management
run in the Ethernet worker. This is synthetic coverage and a build experiment,
not a physical Ethernet/PoE soak qualification or a replacement release image.

The [small-screen message layout](../test-results/v4_pixel5_font_trial.md) retains complete
160-byte messages. Its expanded preview records add 2,816 bytes to the
startup allowance and increase the contiguous history allocation budget.
The V4 can allocate that history in PSRAM; the guard conservatively reserves
internal capacity so that PSRAM availability cannot hide a RAM shortage. A
target may replace the generic UI allowance only with
`MESH_COMPANION_SCREEN_STARTUP_BYTES`; a source-side assertion then covers its
actual startup screens plus ESP32 allocator overhead.

These are engineering allowances for supported configurations, not measured
free heap after boot or a guarantee against every future allocation failure.
Unknown platforms, unknown display drivers and missing linker metadata fail
closed. `MESH_MIN_RUNTIME_HEAP` can raise a profile's requirement; it cannot
lower the calculated requirement. Add an allocation allowance when adding a
display, transport or other substantial feature.

Wireless Paper Full keeps 350 contacts and 256 offline frames by lending the
upper 128 queue slots to its mOTA workspace during a session. An idle WiFi
mOTA listener leaves all 256 slots available. More than 128 unread frames
refuses the loan; sync messages with an app first. USB/TCP source detach or
disconnect returns all 256 slots and releases the ESP32 proof/leaf scratch
buffers. The display and simultaneous USB, Bluetooth and WiFi remain enabled.

## Release evidence and regression tests

Full Companions without PSRAM also use
[16-entry path and shared-secret caches](companion_contact_cache.md).
The optional NimBLE capacity trials retain the same RAM guards at 350
contacts and 256 normal offline frames.

Each newly built firmware has a matching `.memory.json` report. It records
the linked ELF SHA-256, available internal RAM, required RAM, largest region,
and SHA-256 hashes for the actual firmware files and capability manifest.
Packaging and resumed builds reject absent reports, failures, stale ELFs,
missing files and changed firmware. Do not reuse a report for another build.

Run PlatformIO commands sequentially in this checkout:

```sh
python3 -B test/test_firmware_ram.py
python3 -B test/test_t096_full_memory.py
python3 -B test/test_nrf52_ble_startup.py
python3 -B test/test_shared_mota_queue.py
python3 -B test/test_cascade_release_package.py
pio test -e native -f test_ota
```

Tests cover all resolved firmware environments' hooks, real ELF parsing,
allocator table formats, excluded memory, allocation failure, package/report
binding and the published T096 failing budget. Shared mOTA tests exercise
complete transfers, queue wraparound, unread-message order, source ownership,
stop/disconnect and repeated reuse. Bluetooth tests inject task and service
startup failures. The manual staging buffer also has allocation-failure and
repeated release tests. ESP32 tests run the actual WiFi mOTA listener and source
framing through complete transfers, idle polling, queue-full refusal, network
loss, CLI detach and USB/TCP ownership changes under address/leak sanitizers.

For older releases without saved ELFs, an audit can compare their ESP allocator
tables against a matching pinned SDK ELF and read reservations from the
**published application itself** using `scripts/audit_esp32_image_ram.py`.
Unrecognized layouts require another matching reference or a historical rebuild.
An audit must identify original-log/linker calculations separately from new
ELF checks and verify the published firmware hashes.

Physical validation remains necessary: boot with and without USB, pair and
exchange Bluetooth messages, visit every screen, wake with the button, enable
logging/MQTT, transfer mOTA, and monitor heap during a sustained workload.
See [memory monitoring](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MEMORY_MONITORING.md)
for runtime diagnostics.
