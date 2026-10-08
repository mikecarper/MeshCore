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
RS-232 reserve another 8 KiB even when UART defaults off: 4 KiB for the
source-bounded bridge object and 4 KiB for the UART driver and buffers. This
keeps runtime UART enablement in the budget beside MQTT and ESP-NOW.
T-LoRa V2.1-1.6 cannot fit all three transports: the qualified linked triple
image had 190,488 available internal bytes against 196,984 required bytes.
Its ordinary release retains separate UART/ESP-NOW and MQTT/ESP-NOW Full
images; the memory policy and routing capacities remain unchanged.
A 160x80 ST7735
framebuffer needs 25,602 bytes; an OLED allowance is 4 KiB. nRF52 Full with that
color framebuffer must have at least 72 KiB available before startup allocations.
Headless and OLED devices use their own smaller totals. The JSON lists each
component and checks the largest available region against the largest planned
single allocation.

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
4,928-byte Ethernet worker/queue or 5,168-byte external OTA workspaces, with the UART
allowance alongside either mode. It keeps the 8 KiB loop stack, 63 flood rules,
and reserved 64 KiB OTA handoff arena.

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
