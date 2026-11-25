<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/research/nrf52_bluetooth_debug/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# nRF52 Companion Bluetooth diagnostic firmware

The T1000-E diagnostic image is Full Companion firmware with the normal radio,
sensor, pairing and recovery settings. Diagnostics are enabled with
MESH_NRF52_BLE_TRACE=1. Ordinary builds compile them out. Diagnostic builds
reserve about 6.3 KB of RAM for a 256-event ring and counters.

The trace records connection timing, security results, UART notification
subscriptions, app receive/transmit lengths, queue overflow, local disconnect
requests and each recovery trigger. It brackets Bluefruit's real event
dispatcher so a handler that stalls can be distinguished from an event that
never reached the framework. Maximum dispatch duration and Companion loop
gaps are also reported. Callbacks write bounded RAM records without serial
printing or allocation. PINs, keys, peer addresses and message contents are
not stored.

## Capture a phone failure

1. Install the diagnostic UF2 for your exact board, or use its application DFU
    ZIP. This does not require replacing the bootloader.
2. Connect with the phone normally and reproduce the failure. Keep the node
    powered. Do not forget the Bluetooth device, reboot, or factory reset
    before collecting the trace.
3. Connect the node's USB cable to a computer and close other serial clients.
    Install Python and pyserial if needed.
4. Export the diagnostic report:

    ~~~sh
    python3 scripts/capture_nrf52_ble_debug.py --port /dev/ttyACM0 --output bluetooth-report.json
    ~~~

    On Windows use the correct COM port instead of /dev/ttyACM0. The exporter
    enters the Full Companion USB terminal at 115200 baud, reads diagnostics,
    then returns it to binary mode. It does not reset or change settings.

5. Send the JSON report with the approximate failure time and what the phone
    displayed. Export soon after failure because old events roll out of the
    ring. Reboot or loss of power clears the RAM history.

If the Pi's attached T1000-E is used, its serial identity is
34A9141999729D5D. Identify the physical board before flashing; an upstream
application may have the misleading USB product name T1000-E-BOOT.

## Manual USB commands

- get bluetooth.trace: format, latest sequence, capacity and dropped writes.
- get bluetooth.trace.state: advertising, connection, security, UART
  notification state, MTU, app traffic and queues.
- get bluetooth.trace.stack: actual running SoftDevice firmware ID and SDK
  query status. This is a live query, independent of build and bootloader labels.
- get bluetooth.trace.advertiser: live active/idle advertising set counts,
  status, own advertising address and privacy mode. The cached framework flag
  is also included for comparison. No peer addresses or IRKs are requested.
- get bluetooth.trace.identity: own GAP name/address and payload lengths.
- get bluetooth.trace.advertising: bounded copies of the public advertising
  and scan-response payloads, up to 31 bytes each.
- get bluetooth.trace.stats: cumulative connects, disconnects, secured
  sessions, RX/TX operations and last disconnect reason/time.
- get bluetooth.trace.timing: uptime, maximum loop gap, maximum stack
  dispatch time and any currently executing event.
- get bluetooth.trace N: one record by sequence.

The debug commands are restricted to local Companion transport requests and
are not exposed through remote mesh management. Replies and raw records are
decoded by the exporter. Sequence gaps and overwritten records are reported.
Diagnostic contention is counted and dropped rather than blocking BLE.
The exporter retains unsupported-command replies from older format-2 images;
missing newer readbacks do not prevent export of their event rings.

## Reading the result

| Evidence | Stage identified |
| --- | --- |
| No stack connection event; advertising remains active | Connection has not reached the application's Bluetooth event layer. |
| Connection but security error or timeout | Pairing or encryption setup. |
| Secured link but UART notifications disabled | Notification subscription or restoration. |
| Secured link with no app RX; secured_no_app_watchdog | App never opened its Companion UART session; the 15-second recovery fired. |
| App RX followed by blocked/partial TX | Reply delivery; the report identifies the queue/watchdog result. |
| Dispatch entry with no matching exit and increasing age | Software stack handler may be stuck; correlate with a later snapshot. |

Reason 0x08 is connection timeout, 0x16 is local termination and 0x3e
is connection establishment failure. Do not confuse decimal 22 (0x16)
with hexadecimal 0x22 (link-layer response timeout).

The report can identify the failing software stage, but cannot guarantee the
physical cause. No connection event alone cannot separate a central
controller problem, RF interference and a peripheral controller problem.
Dispatch timing starts after the SoftDevice delivers an event; it does not
measure radio interrupt latency. A bench failure is not proof of the same
failure on an iPhone. Use the diagnostic image on the affected node.

## Build

Run one PlatformIO command at a time in this checkout:

~~~sh
PLATFORMIO_BUILD_FLAGS='-DMESH_NRF52_BLE_TRACE=1 -DBLE_DEBUG_LOGGING=0 -g3' \
OUTPUT_DIR=out/t1000e-ble-debug \
bash build.sh build-firmware t1000e_companion_radio_full \
  --firmware-version v1.18.0.1-halo-keymind-cascade-dev-ble-debug --full
~~~

The Bluefruit dispatch hooks are applied to private build copies of framework
sources. The shared PlatformIO framework cache is not modified. Retain the
ELF alongside the UF2/ZIP for future SWD inspection if hardware access becomes
available. Do not halt the CPU during a Bluetooth connection: that disrupts
SoftDevice radio timing and invalidates the test.
