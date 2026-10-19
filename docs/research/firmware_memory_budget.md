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
MQTT reserve the same 49,152-byte WiFi allowance even without `WIFI_SSID` or
`WIFI_OTA_SEEDER`: saved runtime credentials can activate WiFi. Multiple WiFi
features count that allowance once; PSRAM-backed TLS does not remove it.
ESP32 images with
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
RGB565 framebuffer needs 25,602 bytes. T096 recipes instead select
`ST7735_BUFFER_COLOR_DEPTH=8`: the RGB332 canvas needs 12,801 bytes, plus
a source-bounded 1 KiB allowance for the complete sprite object and allocator
metadata. Each frame expands one 160-pixel scanline into a 320-byte stack buffer
and sends the same RGB565 wire format. The current primary UI colors remain
exact; resolution, fonts, all 32 message previews, 350 contacts, 40 channels and
the 256-frame offline queue are unchanged. Other ST7735 recipes retain the
16-bit default. A future sprite-object increase beyond the allowance fails a
C++ assertion rather than silently reducing available heap. The existing loop
stack and transient margin remain additive.

The ordinary T096 BLE FEM-on/off policy includes its receiving OTA source
scratch and full-length previews: the reported 81,474-byte requirement becomes
69,697 bytes with the smaller canvas and explicit sprite-object allowance.
The old 75,396-byte linked capacity therefore failed correctly; the check is
not bypassed or relaxed to accept it. Full Companion's independent 72 KiB
floor remains in place.

ESP32 SSD1306 uses a source-bounded 2 KiB
allowance for its fixed 1,024-byte framebuffer, driver and metadata; other
OLED and ordinary non-ESP32 allowances remain unchanged. nRF52 Full with that
color framebuffer must have at least 72 KiB available before startup allocations.
Headless and OLED devices use their own smaller totals. The JSON lists each
component and checks the largest available region against the largest planned
single allocation.

### MQTT startup minimum and runtime qualification

The `mqtt_startup_allowance` component keeps a 24,576-byte internal startup
minimum for the default 8,192-byte MQTTBridge worker stack and two active
clients. Each SDK client task is explicitly configured with a 6,144-byte stack
and priority 5 on IDF 4 and 5; its maximum configured
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
A cold SDK start requires at least 32 KiB of DMA-capable internal free heap
and an 8 KiB contiguous DMA block. This includes a separate 16 KiB allowance
for its SDK task stack/TCB, receive/transmit/reassembly buffers and transport
or configuration objects, alongside the 16 KiB AES reserve. Those ordinary
SDK allocations occur after admission and do not use the TLS allocation guard.
A retained initialized SDK handle with a stopped task requires 24 KiB free and
an 8 KiB contiguous DMA block. Its separate 8 KiB restart allowance covers the
pinned 6 KiB task stack plus TCB and allocator overhead. SDK stop retains its
receive/transmit buffers, configuration, transport objects and wrapper
reassembly buffer, so restarting that handle does not need to reserve their
first-initialization allocation again. A wrapper whose SDK initialization has
not completed still receives the full cold allowance. This distinction also
applies after WiFi loss stops the SDK tasks but keeps their handles.

All ESP32 clients use 896-byte receive, transmit and wrapper reassembly buffers
from first initialization. Applying configuration cannot grow the retained SDK
buffers or wrapper reassembly capacity, so uniform sizing permits later JWT
reconfiguration without replacing the client. This adds 1,152 bytes to a
non-JWT client's three buffers on an internal-only board compared with 512-byte
buffers. The startup allowance already uses 896-byte receive/transmit buffers,
and cold admission covers the ordinary allocations. WiFi recovery and explicit
reconfiguration continue to retain clients.

A warm reconnect keeps its running task and buffers; it requires at least
16 KiB free and a 4 KiB contiguous DMA block. The per-client SDK stack is
independent of `MQTT_TASK_STACK_SIZE`, which sizes the existing bridge worker.
A TLS attempt without PSRAM requires at least
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

The earlier W12 Full MQTT RAM failure (182,768 available against 198,432
required) and standard portable-slot overflow (1,322,304 against 1,310,720
bytes) are historical measurements, not the current expanded Full result.
The 2026-10-18 UTC recheck at `060119af8` passes with 200,640 internal bytes
available against 198,432 required, retaining the 16 KiB transient margin.
Its largest region is 167,872 bytes against 18,464 required; the packaged
2,280,072-byte application fits the expanded 6,553,600-byte OTA slots.
MQTT is enabled and all ten declared capability checks pass, without declared
reductions. This establishes linked capacity and packaged capabilities, not
physical boot/soak or maximum TLS/WSS/outbox-load qualification. It also does
not qualify an independently built compact portable-slot recipe.

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
on legacy ESP32 paths. Non-PSRAM ESP32 Full uses grouped source proofs instead:
the complete workspace is bounded to 1,536 bytes, while retaining the same
4,096-block source limit. The check still adds one output block and allocation
metadata; safety reserves and stacks are unchanged. PSRAM and other platforms
retain the legacy policy. Explicit larger custom leaf limits also keep the
legacy implementation. Generic folder sources need no output allocation for raw DATA or
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
their complete ACL session layouts. At that measurement, Wio E5 Mini repeater
compilation passed but linking exceeded flash capacity: the baseline already
overflowed by 27,200 bytes, and those changes added 224 bytes for a 27,424-byte
overflow. The newer 1.18.0.1 check below still fails; this cable-only STM32
image remains unqualified and is not release-ready.

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

RAK13800 Companion transport also uses a 1024-word worker by default. Its
existing four-frame queue stays in the linked object; it adds no raw CLI TX
ring. Reserve the worker's 4096-byte stack plus 256 bytes for its TCB and
allocation metadata in addition to OTA source workspaces: USB/BLE folder
sources can coexist with Companion Ethernet. Stack overrides raise both the
total and contiguous allocation requirements. Worker shutdown must complete
before its stack is reclaimed or the shared SPI/storage owner is released.

Full RAK Companion keeps every OTA target name while storing an eight-bit
candidate fingerprint instead of a four-byte ID for each name. A lookup
reconstructs candidate names in the existing 4096-byte heap workspace and
checks their complete SHA-256:4 IDs before returning a match. Other targets
retain their existing index. This trades bounded, infrequent display lookup
work for flash space; no target names or firmware capabilities are removed.

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

## nRF52 Companion flash savings

The common nRF52 Companion policy now retains a 960-byte Ed25519 fixed-base
table in place of the original 30,720-byte table. It evaluates the same signed
radix-16 digits with constant-time table selection, using more doublings instead
of storing 32 shifted rows. Compact SHA-512 and scalar arithmetic keep the same
outputs and wire format. The larger RAM-built table used by infrastructure
profiles is not enabled for Companions.

The policy also omits unused USB device MSC/HID/MIDI/vendor/video classes,
SparkFun's unused automatic NMEA cache and the invisible SSD1306 vendor splash.
For the exact hash-qualified CC310 archive, the domain wrapper retains P-256
for BLE Secure Connections and removes unused legacy curves. CDC, DFU, USB host,
GPS navigation, all Companion fonts, Meshtext, contacts, channels, the Reader
corpus and the 8 KiB loop stack remain available. Explicit board/user overrides
and `MESH_NRF52_COMPANION_TRIM=0` retain their requested values. Other platforms
do not receive this Companion policy.

The RAK4631 Full comparison uses the released 1.17.1.10 preview3 ELF at
`9ce2a076` and the same target with these changes and Reader chapter/range
support. Flash is initialized linked code/data; static RAM is `.data` plus
ordinary `.bss`, excluding separately reserved arenas.

| Measurement | Preview3 | With Companion trims and Reader ranges | Difference |
| --- | ---: | ---: | ---: |
| Linked flash bytes | 710,404 | 652,908 | 57,496 fewer (56.1 KiB) |
| Static RAM bytes | 157,028 | 153,876 | 3,152 fewer (3.1 KiB) |
| Linked heap capacity bytes | 78,484 | 81,636 | 3,152 more |
| UF2 space below the application ceiling | 2,048 | 59,648 | 57,600 more |

Physical RAK signing averaged 14.4 ms with the original table and 35.9 ms with
the smaller implementation. All 24 signatures per image, including SHA-512
block-boundary message lengths, matched byte for byte and verified independently.
The linked ARM emulator measures 1,928 bytes in signing versus 1,528 before;
these are crypto function frames only, excluding callers, interrupts and other
tasks. The loop stack stays at 8 KiB. A full firmware RAM check still applies.

CI runs `scripts/check_nrf52_companion_crypto.py` on the actual Full RAK ELF.
It checks the 960-byte table and retained Reader corpus, executes two RFC 8032
vectors, and rejects crypto function stack use above 2 KiB. Host tests compare
1,791 scalars and 128 keypair/signature/shared-secret cases against the original
implementation, plus scalar boundary/random and sanitizer tests. Clean Full
builds also cover T1000-E and T096. These builds are synthetic coverage for
those boards; the physical Reader/signing tests use the RAK4631.

Reader commands support `get reader 2` and `get reader 2:2-12`. Text terminals
stream at most 96 bytes per mesh loop, preserving progress under zero/short
writes. The TCP console uses one nonblocking socket-send attempt for the stream,
so a peer that stops reading cannot hold the mesh loop in WiFiClient retries.
App CLI replies remain bounded and supply a continuation command.
The physical RAK passed all 21 chapters, ranges, app-style continuation replies,
a paused USB reader exceeding the 4 KiB queue, and disconnect cancellation.

### Empty contact-path cache initialization

The contact-path pool now has an explicit runtime constructor. Its existing
member defaults still initialize every empty handle and resident entry, and
`Slot()` still resets released handles. This moves the local static pool from
initialized `.data` to zero-initialized `.bss`: the vtable pointer and repeated
`NONE`/255 slot markers no longer require a 5,780-byte flash load image. The
pool is constructed on first use by the existing main-loop contact owner.
Its layout, 390 handle slots, 16 resident paths, reference/snapshot behavior
and persistent contact records are unchanged.

The RAK4631 Full release recipe at `c0672b90`, with the full
`v1.17.1.10-halo-keymind-cascade-dev` version and commit suffix, measured:

| Measurement | Before | Runtime pool construction |
| --- | ---: | ---: |
| Linked initialized flash bytes | 716,448 | 710,908 |
| Application image including EndF | 716,528 | 710,988 |
| Linked available heap bytes | 79,388 | 79,388 |
| Required runtime heap bytes | 74,576 | 74,576 |

The net flash saving is 5,540 bytes after constructor/guard code. The image
passes the existing 712,704-byte application ceiling, capability checks, RAM
budget and DFU handoff checks. Its padded UF2 ends at `0xD3A00`, below the
unchanged `0xD4000` boundary. Sensors, BLE/USB/Ethernet, SD, logging, Meshtext,
OTA, Reader data and the 8 KiB loop stack remain enabled.

The contact-cache test compiles a real static pool and checks its zero-initialized
storage, alongside the existing sanitized eviction, snapshot, migration and
transaction tests. The compiled ARM Companion check also rejects a pool in an
initialized ELF section, so compiler promotion cannot silently restore the
flash image. Its normal linked crypto vectors and Reader retention checks
remain required.

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
python3 -B test/test_sh1106_memory.py
python3 -B test/test_t096_full_memory.py
python3 -B test/test_st7735_framebuffer.py
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
The ST7735 tests check all 256 RGB332 values, exact UI primary colors, full
160x80 scanout and byte order on both SPI APIs, the unchanged 16-bit path,
allocation failures, and retained-buffer wake/retry behavior. CI also compiles
both ordinary T096 BLE FEM variants with their real runtime-RAM gates, not only
the distinct Full Companion overlay.

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

## 1.18.0.1 known-risk matrix, 2026-10-18 UTC

The targeted recheck used `v1.18.0.1-halo-keymind-cascade-dev`,
`usa-cascade-fixed`, the Cascade profile, and release-default debug/logging
policy (`DISABLE_DEBUG=0`). PlatformIO ran sequentially; new artifacts and
logs were isolated from existing `out/` builds. Exact-identity expanded Full
profiles were selected for ESP32 infrastructure. Required nRF52 receiver
pairs built both Full and Reduced; a Reduced pass did not replace a Full pass.

Twenty-five selections passed, producing 31 verified profiles:

| Selections | Passing profiles |
| --- | ---: |
| RAK3401 and RAK4631 Full Companion | 2 |
| Wio L1 Full/BLE; T096 Full/BLE, each FEM-on/off | 6 |
| RAK4631 canonical and unified Repeater, Full/Reduced | 4 |
| RAK3401 unified and RAK13302/W25Q16 Repeater, Full/Reduced | 4 |
| MeshTower SD and T096 Repeater, Full/Reduced | 4 |
| RAK3x72, Wio-E5 Mini, Wio-E5 Dev and Tiny Relay USB Companion | 4 |
| W12 expanded Full Repeater, Room and MQTT Repeater | 3 |
| G2 Full Companion/MQTT Repeater, G3 r2 MQTT Repeater, V4 Full FEM-on | 4 |

The separate 26th selection, `wio-e5-mini_repeater`, was an explicitly
cable-only control using `--allow-no-ota`, not an eligible wireless release
target. Its baseline at `3059d7994` overflows the existing 245,760-byte app
region by 38,040 bytes. A private compact-crypto/unused-timer trial recovered
18,544 bytes but still overflowed by 19,496. That unqualified trial was not
retained. The 16 KiB filesystem, host CLI and existing features were not cut
to manufacture a pass; no failing image was published.

Three reproduced release-target failures were fixed:

- `975521645`: Wio-E5 Dev Companion, 16,468-byte overflow to 508 bytes free.
- `3059d7994`: Tiny Relay Companion, 16,424-byte overflow to 452 bytes free.
- `29787c0b2`: G3 Full MQTT, 232-byte RAM-budget deficit to 1,816 bytes above
  the required budget. The SH1106 wrapper now releases its previous BusIO
  adapter before initialization/retry. Source assertions bound its fixed
  1,024-byte framebuffer, complete display object, adapter and allocation
  metadata within 2 KiB. Only the ESP32 SH1106 estimate changes; other
  reserves remain intact. Linked available RAM remains 200,248 bytes.

The STM32 changes reuse equivalent fixed-loop crypto and omit unused Arduino
PWM/tone wrappers while retaining the low-level TIM HAL, 100 contacts, eight
channels, GPIO/ADC interfaces and 32 KiB filesystems. CI now links all four
small STM32 Companions serially at release-version length (`2ae109fea`) and
the exact G3 Full MQTT profile. Display tests cover repeated successful and
failed initialization, recovery, destruction, addressing and rendering under
ASan/UBSan, including a missing-cleanup negative control.

Passes retain their individual source SHAs (`060119af8`, `3059d7994`,
`2ae109fea`, `29787c0b2`) and file-bound memory/capability reports. The affected
G2/Wio L1 selections in this matrix and all four STM32 Companions were rebuilt
at `29787c0b2`.
The final 95 related host tests and artifact-hash checks pass.

### PR21 contact-recovery size recheck, 2026-10-18 UTC

The combined PR17–21 merge tree `a40b1f662` was linked with
`v1.18.0.1-halo-keymind-cascade-dev`, `usa-cascade-fixed`, and the Cascade
profile. All four unchanged 229,376-byte application limits and runtime RAM
gates passed. These are build results, not on-device qualification.

| USB Companion | Application bytes | Free application bytes | Static RAM bytes |
| --- | ---: | ---: | ---: |
| RAK3x72 | 228,744 | 632 | 40,220 |
| Wio-E5 Mini | 228,760 | 616 | 40,252 |
| Wio-E5 Dev | 228,884 | 492 | 40,212 |
| Tiny Relay | 228,940 | 436 | 40,220 |

The earlier 508/452-byte margins above are historical checkpoints. Continue
linking every small STM32 Companion at full release-version length: these
margins do not justify relaxing the application, filesystem or runtime-RAM
gates. Physical storage and boot checks remain in the
[hardware checklist](../test-results/hardware_validation_checklist.md#pr1721-follow-up-qualification-2026-10-18-utc).

Margins remain narrow: RAK4631 Full Companion has 932 flash bytes free;
canonical/unified RAK4631 Full Repeater have 548/540 bytes above their RAM
budgets. W12/G2/G3 Full MQTT have 2,208/2,176/1,816 bytes above their budgets.
These are linked-capacity margins, not measured running free heap.

No new feature reductions were introduced. Existing declarations remain:
RAK3401 unified has 47 flood rules, T096 Repeater has four, Reduced profiles
omit selected optional sensor drivers, and nRF52 Full Companions loan half
their 256-frame offline queue while mOTA is active. STM32 Companions remain
cable-only; nRF52 Full Companion source support is not LoRa self-update
receiver qualification. Exact storage/bootloader requirements still apply.

This is a targeted build/capacity pass, not the complete canonical release
matrix at one frozen commit, physical hardware/upgrade/soak qualification,
or publication of a 1.18.0.1 release.

### Wio-E5 Mini Repeater GCC 15 experiment, 2026-10-18 UTC

The separate compiler experiment used source `6035f6631` and the same
release recipe above. For the reported comparisons, firmware timestamps
were fixed at `18-Oct-2026` and epoch `1792281600`. The actual compiler
executables reported Arm GNU
14.2.Rel1 / GCC 14.2.1 and Arm GNU 15.3.Rel1 / GCC 15.3.1; a private
`platform_packages` override selected the latter. Production pins were not
changed. Each PlatformIO build ran alone, with a fresh private build/output
directory; no existing `out/` artifacts were replaced.

These numbers are linker overflows beyond the **245,760-byte app region**,
not successful firmware sizes. The 16,384-byte filesystem, host CLI,
logging policy, radio preset and existing feature declarations were retained.

| Matched recipe | GCC 14 overflow | GCC 15 overflow |
| --- | ---: | ---: |
| Original Mini Repeater recipe | 38,040 | 37,700 |
| Compact crypto and unused timer wrappers | 19,496 | 19,060 |
| Compact plus best tested five-flag bundle | 18,800 | 18,468 |

Here, compact means adding `ED25519_COMPACT_SHA512=1`,
`ED25519_COMPACT_SCALAR=1`, `ED25519_COMPACT_FIELD=1`,
`RADIOLIB_TONE_UNSUPPORTED` and `HAL_TIM_MODULE_ONLY`. CompactBase was
already enabled in the original recipe. These private additions were not
retained in the production Mini Repeater configuration. Their combined
savings must not be attributed to the compiler upgrade alone.

The four-flag bundle was `-fno-caller-saves -fno-tree-dominator-opts
-fno-reorder-blocks -fno-reorder-functions`. Adding
`-fno-tree-switch-conversion` produced the five-flag winner. Full-source
GCC 15 trials, all with the compact additions, measured:

| Additional tuning beyond the four-flag bundle | Overflow bytes |
| --- | ---: |
| None | 18,500 |
| `--param=max-inline-insns-single=70` | 18,556 |
| `--param=max-inline-insns-single=30` | 18,556 |
| `--param=max-inline-insns-single=15` | 18,588 |
| `-fno-inline-functions-called-once` | 27,108 |
| `-fno-jump-tables` | 18,588 |
| `-fno-tree-switch-conversion` | 18,468 |
| `-Os` instead of `-Oz`, compilation and link | 18,500 |
| `-fno-inline` | 38,116 |
| `--param=max-inline-insns-size=0 --param=inline-unit-growth=0` | 18,500 |
| `-fno-tree-loop-optimize -fno-tree-scev-cprop -fno-tree-loop-distribute-patterns` | 19,228 |
| `-mslow-flash-data` | 27,628 |

The winning GCC 15 recipe reproduced the same 18,468-byte overflow in a
second clean build. Its compiler-only improvement over the matched GCC 14
recipe is **332 bytes**; the original recipe improves by 340 bytes. The
19,572-byte recovery relative to the original GCC 14 build includes the
compact/peripheral and tuning changes, not just GCC 15. The winning linked
flash extent is 264,228 bytes, still 2,084 bytes beyond the chip's entire
256 KiB flash even before reserving filesystem space.

Actual verbose commands were checked across application, crypto library,
Arduino framework and final LTO link. Experimental compile options were
installed through the framework's late `build.framework_extra_flags.arduino`
hook, preserving board defaults and overriding its inline threshold before
library/application environments were cloned. A post-script forwarded the
same tuning to the LTO driver. The true `-Os` trial also changed
`build_unflags` to remove `-Oz`, so the inherited removal of `-Os` could not
silently invalidate that comparison.

Earlier mixed-scope post-script trials were preserved but excluded from
the full-source comparison: those options reached framework/link stages,
not the application/crypto compilation. One later link-only `-Os` control
also measured 18,500 bytes; it is not the true full-source `-Os` trial above.
No ABI-changing struct/enum options, fast-math assumptions, filesystem cuts,
or capacity-check overrides were used. Every full-firmware attempt still
failed at the flash link; none qualifies as a runtime-RAM pass, usable image,
or hardware validation. GCC 15 was not adopted and no failing image was
published.

A private Unicorn 2.1.4 harness also checked generated Cortex-M4 code for
eight standalone crypto profiles: GCC 14 compact, GCC 15 compact, the GCC 15
four-flag bundle, its five-flag winner, and the four more aggressive trials
in the last four table rows. All passed 432 positive function calls:
40 key generations, 40 signatures and 352 SHA-512 checks. Each profile used
two RFC 8032 signing vectors, three additional reference signing vectors,
and 11 SHA-512 boundary lengths in one-shot and three streaming modes.
All 16 corrupted-expected-signature/digest negative controls were rejected.

The harness checked explicit Cortex-M4 CPU-model readback, allocated-section
bounds within physical 256 KiB flash/64 KiB SRAM, read-only executable flash,
non-executable SRAM, buffer canaries, unchanged inputs, per-instruction stack
bounds, restored SP/callee-saved registers and bounded execution. Source and
ELF hashes were verified. Maximum observed standalone call frames were
3,376 bytes for GCC 14 compact, 3,368 for the GCC 15 winner and most other
GCC 15 profiles, and 2,960 with no inlining. These are not whole-application
stack/heap budgets.

This is CPU instruction emulation, not a Wio-E5 Mini board model. The harness
independently links repository keypair/sign/SHA-512 code through stable
exported wrappers, with explicit `-Oz` at link; it is not the exact full
firmware LTO image. It does not qualify signature verification, boot,
interrupts, MMIO, radio, USB, flash/filesystem drivers, sleep, concurrent
traffic, physical timing or cryptographic side channels. A passing standalone
harness does not make any overflowing full-firmware image usable.

### Mini Repeater approved-cut experiment, 2026-10-18 UTC

The follow-up used an isolated source worktree at `9b616f4d1`, with the
same fixed timestamps, release recipe, real GCC 15 executable, compact
additions and five-flag bundle described above. Only Morse/CW tools and
automated management reporting were approved feature cuts. No experimental
firmware source/configuration changes were adopted in the production branch.

Each comparison retained live USB packet logging for MQTT
(`MESH_PACKET_LOGGING=1`, compact and merged USB logging), persistent
`/packet_log` logging, the host-service bridge, normal authenticated CLI,
50 neighbours, radio2 and the 16 KiB filesystem. The existing cable-only
limitation was unchanged. The management cut also omits its shared
`data.tx` routing implementation, not ordinary request/reply statistics.
The Morse/CW cut removes carrier tools and their timer/state/direct-carrier
hooks; Morse RX was already disabled for this target.

These are conservative linked flash extents from the final `.data` load
address plus its size, measured from flash base `0x08000000`, rather than
PlatformIO's smaller sum of selected section sizes:

| Private GCC 15 recipe | Linked extent | App overflow | Saving from control |
| --- | ---: | ---: | ---: |
| Compact/tuned control, all existing features | 264,228 | 18,468 | 0 |
| Morse/CW tools off | 256,832 | 11,072 | 7,396 |
| Management reporting off | 254,280 | 8,520 | 9,948 |
| Both approved cuts | 246,908 | 1,148 | 17,320 |
| Both cuts and shared numeric GET handlers | 246,748 | 988 | 17,480 |

The two-cut result reproduced in fresh control builds. The proposed GET
helper groups 27 simple scalar keys using typed member pointers and
NUL-separated key lists; SET handlers, capability/Observer checks,
matching rules, replies and persisted layouts are unchanged. It defaults
off in the prototype and was not adopted merely for its 160-byte saving.

Further full-source tuning did not close the remaining gap:

| Additional option | Without GET helper: overflow | With GET helper: overflow |
| --- | ---: | ---: |
| None | 1,148 | 988 |
| `-mword-relocations` | 1,148 | 988 |
| `-fno-ipa-sra` | 1,844 | Not tested |
| `-fno-tree-pre` | 1,148 | Not tested |
| `-fno-ipa-ra` | 1,772 | Not tested |
| `-fno-tree-sink` | 1,348 | Not tested |
| `-fno-ipa-pta` | Not tested | 2,640 |
| `-fno-ipa-cp` | Not tested | 1,744 |
| `-fno-tree-forwprop` | Not tested | 1,404 |
| `-fno-split-wide-types` | Not tested | 3,244 |

Separate, unapproved diagnostic controls establish the tradeoffs but are
not candidates: additionally removing the host-service bridge leaves
1,620 bytes free on GCC 15 and 1,240 on GCC 14; additionally removing only
persistent packet-file logging instead leaves 828 and 384 bytes free,
respectively. Both controls keep live USB/MQTT packet logging enabled.
Neither extra feature cut was adopted.

Focused host tests exercised the actual raw RX/TX logging emitters and
packet codec, exact line framing, maximum packets, short/non-draining
serial writes, drop recovery, persistent logging callbacks and failed-TX
handling across all eight carrier/management/file-logging combinations.
Disabled-management tests verify fail-closed routing, password erasure,
no management filesystem access and retained shared Fleet/beacon context.
The full legacy/compact GET-body oracle passes 80,406 comparisons plus
fallback checks under ASan/UBSan, including distinct fields, NaN/Inf,
limits, whitespace and capability cases; four negative controls fail as
expected. All ten CLI contract tests pass. Independent source/evidence
review found no blocker, and `git diff --check` passes.

These tests use host boundary stand-ins; they do not establish physical
USB/MQTT broker integration, ARM runtime behaviour, board startup, radio
timing, sleep or sustained load. No approved configuration fits yet, so
there is no approved runtime-RAM pass or usable Mini Repeater image from
this experiment. No hardware was flashed, no failing image was published,
and existing build outputs were not overwritten. Production compiler pins,
features and capacity checks remain unchanged; the private source/tests,
configs, verbose logs and linker maps were retained for further work.

### Mini Repeater production recipe after file-logging approval

After approval to also omit persistent packet-file logging, the three gates
were adopted only for `wio-e5-mini_repeater`:
`MESH_ENABLE_CARRIER_TOOLS=0`,
`MESH_ENABLE_MANAGEMENT_REPORTING=0` and
`MESH_ENABLE_PACKET_FILE_LOGGING=0`. All three default to 1 for other
boards/roles. No compact numeric GET handler was adopted.

The production recipe retains the existing GCC 14.2 package pin, adds the
equivalent compact SHA-512/scalar/field implementations and unused Arduino
timer-wrapper trims, and applies the five measured compiler flags to both
compilation and the final LTO driver. The target-specific link hook is inert
for other environments. Existing flash and runtime-RAM checks remain active.

Canonical `build.sh build-firmware` links at release-version length
(`v1.18.0.1-halo-keymind-cascade-dev`, Cascade, USA preset,
`DISABLE_DEBUG=0`) pass:

| Canonical target | Linked flash extent | App limit | Flash headroom | Runtime RAM available / required |
| --- | ---: | ---: | ---: | ---: |
| Wio-E5 Mini Repeater | 245,376 | 245,760 | 384 | 44,560 / 32,288 |
| Wio-E5 Mini USB Companion regression control | 229,096 | 229,376 | 280 | 24,256 / 17,408 |

These are conservative complete flash extents, not PlatformIO's selected
section sums. The Repeater still reserves its original 16 KiB filesystem,
50 neighbours, radio2, authenticated administration, host-service bridge and
runtime-controlled live USB packet logging for an external MQTT bridge.
The Companion keeps its 32 KiB filesystem and existing features. GCC 15 is
not required and no toolchain upgrade was adopted.

The omitted file feature is only node-local `/packet_log` capture and
`log start|stop|erase` / local `log` dumping. Live raw RX/TX framing, USB
logging controls, statistics, watchdogs and bridge callbacks remain. Old
saved log files are left untouched rather than automatically erased.
Management-disabled routing fails closed and retains shared initialization
needed by independently enabled Fleet/beacon features.

Focused coverage now runs in CI alongside an exact cable-only Mini Repeater
build at release-version length. It covers the feature gates, default-on
behavior, raw logging and backpressure under all eight flag combinations,
shared cooperative serial output, crypto equivalence, CLI permissions and
Morse/radio recovery. Production-source and host-test review found no blocker.

Both flash margins are narrow: future changes must re-link, not assume these
images fit. These results qualify linked capacity and host behavior, not
physical USB/RF/MQTT integration, board boot/sleep or a sustained-load soak.
The Mini Repeater remains cable-only (`--allow-no-ota`); no hardware was
flashed, no release was published and existing outputs were not overwritten.

### Shared contact RAM and RAK4631 Full recheck, 2026-10-19 UTC

Companion and `simple_secure_chat` contact sort/match indexes now use `uint16_t`
when the complete table has at most 65,535 slots; larger configurations retain
`int`. Static 350-contact targets save 764 internal bytes; PSRAM targets save
96 internal and 716 external bytes; static 100-contact targets save 264 internal
bytes. Cached nRF52 record placement also saves four bytes per runtime contact.
Capacities, serialization, queues, stacks, features and fallback behavior remain.

At `aed4c22e5`, matched against `31e693ec2`, RAK4631 Full saves 2,200 static RAM
bytes and 488 actual DFU application bytes. Its application is 712,112 of 712,704
bytes (592 free); modeled RAM admission margin rises from 4,516 to 6,716. CRC
sharing and Reader packing preserve formats/text; Full features and existing SD
support remain enabled. These are linked/build capacities, not running-heap or
physical storage measurements.

Five final-source canonical packages pass across nRF52, ESP32 PSRAM, RP2040 and
STM32. RAK3x72 USB has four actual BIN bytes free, requiring unchanged hard gates.
At that revision, V3 Full fails RAM admission on both baseline and final source; its
100-contact wrapper gains 264 bytes, reducing the deficit from 21,288 to 21,024.
The direct 350-contact V3 terminal default build passes separately; it is not a
canonical Full or release-version/preset package. No hardware was flashed.
See the [qualification report](../test-results/contact_memory_2026-10-19.md) and
its source-bound artifacts, software coverage and limits.

### Heltec V3 Full bounded-source repair, 2026-10-19 UTC

At `eb9259f2a`, canonical V3 Full passes with 189,616 internal bytes available
against 183,584 required: 6,032 bytes of modeled margin. The pinned NimBLE
adapter preserves 15 saved phones and 30 CCCDs. Existing Bluedroid pairings need
one-time re-pairing. Its 100 contacts, 40 channels, 256 offline frames, MQTT slot,
Full features and stack sizes remain unchanged.

Grouped source proofs retain the same 4,096-block capacity with a lazy workspace
of 1,004 bytes, statically bounded to 1,536. The guard replaces only the old
16,384-byte leaf allocation with this bound; the output block, metadata,
transient margin and all other allowances remain additive. Legacy/PSRAM paths
and larger custom leaf limits retain the existing policy.

The clean V3 package and constrained RAK regressions pass; physical V3 testing
still awaits USB attachment. See the [V3 qualification report](../test-results/heltec_v3_full_memory_2026-10-19.md)
for artifact hashes, tests, upgrade behavior and hardware limits.
