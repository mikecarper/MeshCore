<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/usb_serial_backpressure/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Native USB backpressure and radio liveness

The native USB defaults for eight ESP32-S3 board families use hardware
USB Serial/JTAG (HWCDC). Nibble Zero Connect uses TinyUSB CDC for its USB and
Full Companion images so a browser can open the serial port with DTR asserted.
Retia verified a Companion self-info reply with DTR asserted on the Nibble Zero.
[Retia's hardware test](https://github.com/RetiaLLC/MeshCore/commit/00d561fcf1893162293beb12ba8d4d5a599d93cc)

ESP32-S2/S3 builds using native TinyUSB CDC (`ARDUINO_USB_MODE=0` and
CDC-on-boot) must not wait for a computer to read USB output. In the bundled
Arduino-ESP32 2.0.17 core, `USBCDC::write()` can wait indefinitely for transmit
space; its configured timeout bounds a mutex, not that wait. This can stop the
same loop that services LoRa, even while the USB connection still appears open.

## Firmware behavior

- Native CDC output uses a single FIFO attempt, not Arduino's wait-for-space
  write or flush. TinyUSB's internal short mutex operations still apply; this
  is a no-host-progress-wait guarantee, not a lock-free driver replacement.
- Functional text and diagnostics share one ordered 4 KiB queue. Diagnostics
  leave 3 KiB reserved for commands. A diagnostic backlog alone does not prevent
  command input. A stalled host may lose diagnostic records; radio work continues.
- A connected terminal that exceeds its bounded output capacity gets an explicit
  dropped-byte notice when transmission resumes. Large local file dumps and
  recent-repeater listings advance between radio service passes instead.
- File dumps stop at their initial file size and emit `-> EOF` only after the
  final accepted record. Corrupt stored lines exceeding 640 bytes are omitted
  with a visible notice. Recent-repeater output is a live, bounded-cursor view;
  incoming packets can change its ordering while it is being printed.
- Disconnects discard pending old-session text and reset the role's partial
  command/listing state. Binary/mOTA transitions suppress pending text notices.
  Bytes already transmitted cannot be recalled.
- Companion frames retain and retry short writes. An mOTA request is admitted
  only when its complete, at-most-11-byte record fits.
- nRF52, UART, and USB-Serial-JTAG behavior is not changed by this native-CDC path.

Host software must also keep reading independently of command writes. A serial
relay should start its reader before the first command, use finite write and
response deadlines, avoid discarding received packet logs, and cancel I/O during
shutdown. A response timeout must not allow a late reply to satisfy another
command: the CLI has no transaction identifiers. Updating firmware alone does
not correct an indefinite host-side serial write.

## Regression checks

Run only one PlatformIO process in the checkout at a time.

```sh
python test/test_esp32_tinyusb_nonblocking.py
python test/test_esp32_usb_session_fix.py
python test/test_esp32_tinyusb_role_hygiene.py
python test/test_esp32_tinyusb_cooperative_output.py
python test/test_esp32_usb_serial_hygiene.py
python test/test_nrf52_usb_logging_contract.py
python test/test_nrf52_usb_lifecycle.py
python test/test_common_cli_logging_transaction.py
python test/test_companion_usb_logging_reply.py
python test/test_usb_diagnostic_logging.py
pio test -e native -f test_nrf52_debug_output -f test_serial_packet_log -f test_serial_mode_switch -f test_mesh_tables
```

The first test compiles the real USB facade against a simulated 64-byte FIFO,
including stopped readers, reconnects, protocol transitions, and other-platform
fallbacks. Host simulations cannot establish that every real USB driver or
endpoint failure has recovered.

A Full Station G2 validation build with USA Cascadia radio settings and the Cascade
profile can be made using the normal build entry point. The portable `standard`
recipe preserves the deployed partition layout but omits LoRa OTA; it still has
the browser firmware uploader. The `auto`/`full` recipe instead enables the
expanded feature set and partition layout: its merged image is **not** an
app-only update for a device with the legacy layout.

```sh
MESHDEBUG_OVERRIDE=on PACKET_LOGGING_OVERRIDE=on \
  bash build.sh build-firmware Station_G2_repeater --build-profile full \
  --radio-preset usa-cascadia --profile cascade
```

Before installing on hardware, preserve the device identity, preferences, and
existing partition layout. Then verify USB command responses and repeated LoRa
logins with the relay running, paused, and stopped, including a host that leaves
USB open without reading. Check LoRa recovery separately from USB OUT recovery;
fixing transmit backpressure does not prove an unrelated OUT endpoint fault is
resolved. Do not erase or repartition the radio as part of this test.

## USB logging ownership and session recovery

On a single-port Companion, enabling USB logging parks framed USB traffic
before opening the diagnostic gate. A TCP/browser terminal retains its CLI
ownership; USB is log-only during that session. Disabling logging restores USB
traffic without accepting bytes typed during the log-only interval. A dedicated
nRF52 logging port does not park the separate Companion port.

A framed USB logging command retains its original requester and queues its
acknowledgement before changing ownership. The handoff advances without waiting
for the host: live logging stays unchanged while the complete USB reply is
backpressured. Disconnect/reset cancels that session's pending handoff; an
already-saved preference can still take effect at the next boot. BLE/WiFi replies
are never used as a fallback for a refused USB acknowledgement.

On native ESP32 TinyUSB, build-local framework hooks capture DTR and USB session
boundaries synchronously in the USB owner task, before Arduino's delayed events.
They purge old input at the boundary and reject late input while DTR is low,
without deleting a new host's first query during main-loop cleanup. An armed
old IN packet (or a racing writer) triggers immediate soft-disconnect, a minimum
20 ms detached interval, and quarantine until fresh enumeration. Shared SDK
files are not modified; a changed native-CDC descriptor fails the build closed
until the hook is reviewed. This path is specific to the pinned Arduino-ESP32
2.0.17 native CDC implementation; hardware Serial/JTAG and UART are unchanged.

Disabling diagnostics is not itself a protocol change and does not discard
functional replies from the shared ESP32 text queue. Actual Binary/mOTA changes
use the explicit terminal-output barrier. Touch and invalid-Ethernet-hostname
diagnostics also honor the runtime logging gate and bounded USB stream.

Ethernet and enabled MQTT diagnostics honor the runtime logging gate and use
the bounded USB facade. Infrastructure logging setters commit preferences
before changing live output; failed saves leave previous settings unchanged.
A successful save with a failed MQTT runtime change reports those two outcomes
separately.

ESP32 native-USB repeater and room ACL listings retain short-write suffixes and
advance one row per service pass, using their existing output buffer. Reconnect
cancels the old listing. The listing is a bounded live view, not an immutable
snapshot of clients changed during output.

Stored dumps make progress even when HWCDC falls back to 256/512-byte TX rings.
Recent-repeater headers/rows and EOF retain every unwritten suffix; a short
write never causes their accepted prefix to be retried.

nRF52 FIFO cleanup also checks for an already-armed IN packet. If one exists
at a session boundary (including a pending zero-length packet), both USB ports
briefly detach and re-enumerate: a FIFO clear alone cannot recall endpoint RAM.
Reattachment waits for the local unplug event and a 20 ms minimum interval,
without blocking the mesh loop. Dedicated CDC descriptors are added while
detached even if the host has not yet completed enumeration. These recovery
paths have native regression coverage; real host/board timing still requires
hardware qualification.

The nRF52 callback only drops the pull-up and closes producer gates. The
application loop sends the stack's unplug event afterward, so a full owner-task
event queue cannot make its consumer wait on itself. The detach interval starts
when that application-side stack detach actually occurs. A regression models a
full finite event queue as well as the separate armed endpoint packet.

## Local qualification (2026-10-03)

These are the upstream USB-session qualification results before the subsequent
USB watchdog, GPS preference, and repeater trace merge. The byte counts below
describe those earlier binaries, not the current merged source.

- All 112 USB Python regression methods passed, plus nine logging-transaction,
  Bluetooth-control, and WiFi-status methods. The native facade includes a
  threaded close-versus-writer race and an unmount-with-reset-endpoints check.
- `Xiao_S3_companion_radio_usb` built successfully with its default hardware
  Serial/JTAG transport: 880,709 flash bytes and 73,244 static RAM bytes.
- A native-TinyUSB Xiao S3 USB Companion qualification build passed with debug
  and packet logging enabled: 928,133 flash bytes and 89,312 static RAM bytes.
  Its linked ELF contains the synchronous session hooks and endpoint query.
- A RAK4631 Full Companion qualification build with two CDCs, debug/packet
  logging, and USB/BLE mOTA sources passed: 678,092 flash bytes and 154,428
  static RAM bytes. The runtime RAM and Bluetooth DFU safety checks also passed.

The two custom qualification builds used the lab profile
`909.5 MHz / 500 kHz / SF5 / CR5`. No board was flashed, and no physical
disconnect/reconnect or hardware endpoint recovery was claimed by these checks.

## ESP32 HWCDC migration and upstream findings (2026-10-04)

The board defaults change 78 native-TinyUSB recipes across Heltec E213, E290,
T190, Wireless Tracker V1.1 and V2, Station G2 and G3, and T-Beam 1W. Two G2
recipes already used HWCDC. All 80 recipes in these families select
`ARDUINO_USB_MODE=1` and `ARDUINO_USB_CDC_ON_BOOT=1`; upload touch and waiting
for a replacement upload port are explicitly disabled. Their framework stays
on PlatformIO Espressif32 6.11.0 / Arduino-ESP32 2.0.17. Existing ESP32 UART
recipes and all non-ESP32 recipes are unchanged.

HWCDC output uses the existing bounded facade, early RX/TX allocation, shared
writer exclusion, and fresh capacity check inside that exclusion. The pinned
SDK receives a build-local TX suffix/interrupt backport from upstream
[Arduino-ESP32 #12606](https://github.com/espressif/arduino-esp32/pull/12606).
KISS now prepares buffers before starting the USB driver and uses this facade.
Its debug/logging gates stay closed so diagnostics cannot split a binary frame.
A USB bus reset drops partial host commands and old queued frame suffixes while
preserving radio transmission state and settings. Radio work continues while
USB cleanup retries.

On S3, the pinned HWCDC initialization also restores RTC PHY ownership to the
hardware Serial/JTAG peripheral. A software restart after running TinyUSB can
otherwise retain ownership by the OTG controller, including an OTA update to an
HWCDC image. When that retained state is detected, initialization first briefly
detaches the old OTG pads so the host can enumerate the changed device. Ordinary
HWCDC startup does not incur that extra interval.

HWCDC does not expose the same DTR session boundary as TinyUSB. Closing a host
port while leaving USB connected is not proof of a new firmware session; a
partial command may survive that close. Bus reset/unplug cleanup and protocol
client leases must be tested separately. Hosts should rediscover the port after
the backend change: the USB product and `/dev/serial/by-id` path can change.
Espressif confirms that hardware CDC descriptors are fixed in silicon, so
firmware cannot preserve a custom TinyUSB product name.
[Arduino-ESP32 maintainer comment](https://github.com/espressif/arduino-esp32/issues/11394#issuecomment-3778833461),
[ESP-IDF maintainer comment](https://github.com/espressif/esp-idf/issues/18516#issuecomment-4316973953).

Upstream reports checked for this transition:

| Report | Documented behavior | Relevance and mitigation |
| --- | --- | --- |
| [ESP-IDF #9826](https://github.com/espressif/esp-idf/issues/9826) | S3 retains TinyUSB PHY ownership across software/watchdog restart, preventing USB Serial/JTAG operation. | Pinned HWCDC initialization restores the official S3 hardware PHY route and detaches the former OTG connection first. Initialization fixtures seed retained RTC state; the controlled physical G2 OTA transition below also passed. |
| [Arduino-ESP32 #12782](https://github.com/espressif/arduino-esp32/issues/12782) | Open report of HWCDC failing to reconnect after about two minutes idle on C3/C6/C61/S3, latest master / IDF 5.5.4. | Reported SDK differs from our pinned Arduino 2.0.17 / IDF 4.4. Synthetic tests cover idle lease expiry and reply admission, not physical enumeration on that SDK. |
| [ESP-IDF #18996](https://github.com/espressif/esp-idf/issues/18996) | Open report of S3 USB Serial/JTAG failing after repeated software resets from macOS, on IDF 6.0.2; includes XIAO S3. | Not qualified by Linux port reopen tests and not reproduced on our pinned SDK. CPU reset and host-port reopen are distinct cases. |
| [ESP-IDF #13287](https://github.com/espressif/esp-idf/issues/13287) | S3 may remain in ROM after upload when download mode was entered with physical BOOT/RESET. | Our older esptool does not offer the newer `watchdog-reset` option. Press RESET after a manual BOOT-mode upload if the application does not start. |

Espressif's [USB Serial/JTAG guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/usb-serial-jtag-console.html)
describes the fixed CDC/JTAG peripheral and its light/deep sleep limitations.
This migration uses only its serial role; it does not provide TinyUSB composite
interfaces such as MSC, HID, or a second CDC.

Additional regression commands (only one PlatformIO process at a time):

```sh
python3 -B test/test_esp32_hwcdc_recipes.py -v
python3 -B test/test_hwcdc_tx_backport.py -v
python3 -B test/test_hwcdc_write_capacity.py -v
python3 -B test/test_hwcdc_role_transport.py -v
pio test -e native_kiss_modem
```

The recipe check resolves all 821 tracked environments using PlatformIO's real
inheritance and SCons flag/unflag processing. It checks all 500 ESP32 selectors,
including all 78 migrated recipes, and the 321 non-ESP32 recipes. The transport
test compiles production Companion framing, KISS output methods, setup/loop,
and the bounded facade with the patched SDK against simulated USB registers and
RTOS queues under ASan/UBSan. It covers withheld readers, stale capacity,
partial frames, queue limits, session reset, idle leases, and cleanup retries.
The role fixture injects a confirmed session boundary; separate SDK tests
execute the bus-reset callback and the S3 retained-PHY initialization. The
setup/loop fixture uses stub transport functions to check ordering and continued
radio service rather than real peripheral recovery.
Native KISS tests also exercise the full modem's radio lifecycle during reset.
These tests do not establish physical USB timing or host compatibility for
boards that were not connected to the lab.

## Physical G2 HTTP OTA and USB transition (2026-10-05)

The latest OTA qualification used commit `95913c5301318fb738fb8f50e314a57a2dd9eb07`,
including the uploader fix from `d2983762` and the station ownership fix.
A current-source build explicitly selected TinyUSB; the target selected the
normal HWCDC backend. Both ran on the physical Station G2 with the same existing
partition table and two `0x640000` application slots. The independent audit
checked packaged application hashes against metadata and matching preserved
ELFs, the USB SDK transforms, and linked RAM requirements. Both ELFs contain
the production 30-second admitted-upload timeout, queued-response guards and
specific write-failure text; neither links the removed raw `Update.printError`
calls. This is a controlled same-source transition, not released stock 1.17.1
migration evidence.

| Artifact | App bytes | SHA-256 |
| --- | ---: | --- |
| TinyUSB observer, `95913c53` | 1,978,200 | `cf72b3ce872fb0887273022255fe5826070b5b40f5aa8057f50e90e7c92d4b72` |
| Normal HWCDC observer, `95913c53` | 1,950,824 | `aeeadb1f165164c5b01d5855918e20bb0dc37bedcb9f0a8b88f52faa4c435486` |

The AP upload used the actual multipart `/update` handler and an app-only
image. Its HTTP identity endpoint was matched to the node read over USB before
the POST. USB topology was sampled every 20 ms without serial reads or writes
across the upload/restart boundary. After 65,536 wire body bytes, Linux's
`SIOCOUTQ` on that same HTTP socket fell from 59,987 outstanding bytes to zero
in 0.554 seconds. Only then did the host pause body sends for 4.510 seconds;
there were no serial operations during that pause. TCP acknowledgement was
observed; the firmware's application receive timestamp was not directly
instrumented.

| Measured gate | Result |
| --- | --- |
| App-only multipart upload with the intentional body pause | HTTP 200, 22.559 seconds |
| Firmware-owned software restart and USB backend change | TinyUSB device 120 disappeared; a transient TinyUSB device 121 preceded hardware CDC device 122 on the same physical topology |
| First plain website `time <epoch>` after HWCDC discovery | Clock-set acknowledgement in 2,997.319 ms; strict deadline 5,000 ms |
| Extra post-restart settling, serial commands during upload, external recovery | None |
| Identity and saved runtime settings | Preserved against the original preflight |
| Admin access | Exactly the original and recovery Admin ACL entries remained |
| Core diagnostics after the upgrade | Error flags 0, uptime 4 seconds |
| Pi WiFi rollback | Original connection restored and temporary AP profile deleted; runner completed in 35.598 seconds |

The final `95913c53` hardware CDC application passed two quiet 180-second trials
with power saving enabled. There were no serial reads, writes or keepalives
during either interval. With the handle open, the first core reply took
18.551 ms and uptime advanced from 43 to 223 seconds. With the handle closed,
the first plain website clock request after reopening was acknowledged in
14.728 ms and uptime advanced from 224 to 404 seconds. No CPU restart occurred.
The preexisting radio CAD warning `0x02` remained unchanged throughout these
trials; this was not a zero-error radio test. Original power saving off, other
saved settings and both Admin ACL entries were restored and verified.

After those trials, a single plain `start ota` command used the connected
station's advertised LAN address. The actual `/update` page returned HTTP 200
in 1.365 seconds and `/update/identity` returned HTTP 200 in 0.084 seconds with
the exact G2 identity. The command reported WiFi on and WebConfig stopped.
Stopping OTA and restoring the original WebConfig were verified; no firmware
was uploaded in this endpoint probe. Uptime continued from 533 to 535 seconds,
the station remained connected at -80 dBm, and identity, original settings,
both Admins and power saving off were retained. These HTTP measurements use a
separate diagnostic bound; the first USB website request still uses 5 seconds.

Final restoration also verified the Pi's original WiFi connection, no temporary
OTA profiles or return timers, and active `mctomqtt`, `ModemManager` and
`mesh-logger` services. Each service reported exit status 0 and no restarts.
This verifies service health; MQTT packet delivery was not measured in this run.

Baseline preparation also used app-only HTTP OTA: hardware CDC `d2983762`
installed TinyUSB `95913c53` in 19.083 seconds, and its first plain clock
acknowledgement took 3,163.722 ms. Original identity, settings and both Admins
were verified before and after this reverse transition; no rescue action was
used. Its Pi WiFi rollback completed in 31.958 seconds.

The first query used the stock website's plain clock command immediately after
opening the discovered hardware CDC port. Later queries were tagged to keep
unrelated diagnostics from satisfying a response. Neither a longer command
deadline nor an arbitrary startup delay was added.

The uploader fix addresses a separate bounded-gap problem: the pinned HTTP
listener gives ordinary clients a 3-second receive timeout, which remained in
effect while an admitted multipart upload was receiving its body. OTA now
extends only that admitted request to 30 seconds after MD5 validation. Normal,
unauthorized, malformed-MD5 and concurrent requests retain ordinary admission
behavior. `send()` queues a response before it reaches the wire, so both the
upload and completion callbacks preserve an already queued error. Begin/end
errors and short writes return HTTP errors without calling raw USB prints
from the shared AsyncTCP task.

Three ASan/UBSan uploader tests passed, together with eight OTA startup tests
and fifteen WiFi lifecycle tests. The uploader fixture executes production
callbacks and the pinned AsyncTCP inactivity condition. A 4-second body pause
survives, an abandoned owner remains open at 29.999 seconds and aborts at
30 seconds, ownership is released without reboot, and a later upload completes.
Negative controls fail when the timeout extension or queued-response guards
are removed, or when raw USB error prints are restored.

Historical results remain distinct:

- The earlier same-source `d2983762` forward transition also passed its
  4.510-second upload pause: HTTP 200 in 23.211 seconds and the first website
  clock acknowledgement in 2,976.119 ms.
- Earlier legacy-firmware WebConfig/WiFi handoff attempts failed before upload
  and required USB restoration; they did not establish an OTA transition.
- An earlier controlled `e971beab` LAN attempt reset its upload socket after
  1,277,952 of 1,950,796 wire bytes, with weak LAN signal. USB stayed on TinyUSB,
  uptime continued, and an AP retry later passed. The cause of that LAN failure
  is unproven; neither a socket reset nor radio CAD error flag `0x02` establishes
  a CPU crash.
- Same-backend setup attempt 1 installed the fixed TinyUSB application over
  HTTP, but the first product-matching USB rediscovery preceded the firmware's
  delayed restart. Its version gate correctly failed while the old application
  still answered. A later read-only version check confirmed `d2983762` without
  rescue, and the measured forward transition verified that source version
  before uploading. This setup report remains failed and is not counted as
  migration or startup-latency evidence.

The earlier `d2983762` hardware CDC application passed two quiet 180-second trials
with power saving enabled. There were no serial reads, writes or keepalives
during either interval. With the handle open, the first core reply took
17.353 ms and uptime advanced 180 seconds. With the handle closed, the first
plain website clock request after reopening was acknowledged in 8.905 ms and
uptime advanced 181 seconds. Original power saving off, other saved settings
and both Admin ACL entries were restored and verified.

A normal `start ota` probe while the station was disconnected correctly
reported its fallback AP address; a Pi still on its original LAN could not
reach that separate AP. This does not qualify the LAN endpoint. Source review
then identified a missing WebConfig retry owner when MQTT is compiled in but
has no running worker. The infrastructure fix refreshes ownership from actual
MQTT worker state on the mesh loop. Constructor credential loading, browser
credential writes and the 64-hex password capability use a separate immutable
storage choice, so taking over reconnects cannot truncate or reroute saved
credentials. An active external worker retains its connection; a stopped
worker releases it for a fresh portal attempt. Six ASan/UBSan tests exercise
production role startup, connection fallback, retry, worker handoff and
credential paths, including the existing manual Companion startup behavior.
The `95913c53` OTA transition, quiet power-saving trials and normal LAN endpoint
checks passed as reported above. The current GitHub unit workflow has not yet
completed. Release 1.17.1.9 remains held and unpublished. These results qualify
this G2, Linux host and pinned framework; they do not establish physical
compatibility for every affected board or host operating system.
