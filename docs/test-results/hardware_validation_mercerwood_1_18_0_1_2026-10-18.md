<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/hardware_validation_mercerwood_1_18_0_1_2026-10-18/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Mercerwood 1.18.0.1 hardware campaign, 2026-10-18 UTC

This campaign is in progress. It is not release approval. Results below bind
specific images and physical devices; successful compilation and older bench
results do not qualify unexecuted hardware cases.

After the operator reset the V4, its exact USB by-id and physical-path endpoints
were present at 15:09 UTC. A fresh Full ec9ebc09 binary identity snapshot passed.
Authoritative original-data restoration and fresh binary/62-field comparisons
subsequently passed. The owned host diagnostic bond was removed and original
services were active. Full logging-on remains failed/unqualified; the staged
MQTT/WSS soak has not launched while upstream changes are integrated.

## Firmware and preservation

The initial eight-image Full matrix built successfully from clean source
`652289ee823bbed22918fe584f9d49ccb2ec230f`, with release-length version
`v1.18.0.1-halo-keymind-cascade-dev-652289ee`: V4 Room Server, V4.3 Companion
FEM-off, RAK4631, T096 FEM-on, MeshPocket, T1000-E, SenseCAP Indicator LoRa,
and Xiao S3 WIO. Room fixes were separately built and readback-verified at
`d3e05abce308b52261e86c9297029b4bce3cc4a1` and
`d52e4e93475e0960328131e2e65cc6b992aa4dbb`. The diagnostic-enabled Full
V4.3 FEM-off and Indicator LoRa images built successfully at
`ec9ebc0914d61a02fb21f23444768ec5f847e7ec`.
The later MeshTower V2 SD Full build and transfer are recorded separately below.

Before ESP32 application changes, complete private flash backups were made
through the pinned real flasher's identity/reset/finish path. Application-only
writes checked silicon MAC, USB identity, capacity, active OTA slot, image
integrity and full application readback. Partition tables and OTA metadata
were verified unchanged. Original NVS/filesystem restoration is a separate
operation; an application-only flash does not restore settings changed by tests.
The Indicator retains its existing noncanonical 8 MiB partition table.

nRF52 updates used application-only DFU. The RAK3401 service radio and
Indicator RP2040 display coprocessor were excluded. No root USB hub power
operation or concurrent PlatformIO process was used.

The flashed 652289ee artifacts below were built from source
`652289ee823bbed22918fe584f9d49ccb2ec230f`. USB identity labels are the first
12 hexadecimal characters of SHA256 of the UTF-8 USB serial as reported;
the serial-less CH340 Indicator was bound privately by its physical USB path
and public device identity. These hashes bind the executed images, not
unexecuted release qualification. ESP32 hashes cover application-only BINs;
nRF52 hashes cover the application DFU ZIPs actually transferred.

| Target | Source | USB serial hash | Artifact | SHA256 |
| --- | --- | --- | --- | --- |
| `heltec_v4_room_server` | `652289ee8` | `af0a529cb0cc` | Application BIN | `4e370dd285813ce330aae13a9a8891795145d3bbb7adef84ff4dbb19926cb677` |
| `RAK_4631_companion_radio_full` | `652289ee8` | `b11fbd38286f` | Application DFU ZIP | `64940ae543491831a20da2176e50e44a1519917bd42c4615601ddc0c7bf82364` |
| `Heltec_t096_companion_radio_full_femon` | `652289ee8` | `ab7bc0653271` | Application DFU ZIP | `0a44dbc7a39fb65007713aa61c004b8bb790035c1d6c343ba150a0a77f855dfa` |
| `Mesh_pocket_companion_radio_full` | `652289ee8` | `c5ba0587de70` | Application DFU ZIP | `eb13c05521f2545719db809c5367ed31de22e9aeadc0c0992366a6ab770570c5` |
| `t1000e_companion_radio_full` | `652289ee8` | `5244f0b64696` | Application DFU ZIP | `7a6003afa50720e2c97cfbb625692143f66b4e7de063de63106fecd5ff4fc5fb` |
| `SenseCapIndicator-LoRa_companion_radio_full` | `652289ee8` | No USB serial | Application BIN | `6cb1405fb2a6c3f4889506f184751d7576ad23159c303f84c054e4ad6b124b03` |
| `Xiao_S3_WIO_companion_radio_full` | `652289ee8` | `b00e0958f2ff` | Application BIN | `59c62ff6d985aa42740b6c26dcc30efe50c04b747d94d566e4ca4a8cd1e42e47` |
| `Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors` | `652289ee8` | `030f24857801` | Application DFU ZIP | `0d20f4486023a13427eb09087da70aacac5562848371a919631ab6a76f7888d1` |

The same V4's subsequent Room application hashes were
`54962d21088f1c87c3956620181a3499102e074d2e28791003d1b0479f64c7af`
for d3e05abc and
`7753f5f4161a6631d7f3abe44a99d70d5b892a94ad3cb69d313d54e44c608c87`
for d52e4e93. Their archived exact ELF hashes are respectively
`57d5b38fcc454d1ccc05763d3d8c6fa047904c42ca761be3a0393f14e799d1eb`
and `5db0fb151ee388ffab66ccce11b0d137907ff6450530f7812d35383c8c6aa785`.

After the Room campaign, the V4.3 Full FEM-off ec9ebc09 application passed
identity-bound application-only flashing and complete readback. Its 2,545,080-byte
BIN SHA256 is
`cb1e167a222e7a8d0ca253f7e3272561a29ba6753f4173eb89c79405b2bdad2f`.
The separate restoration then wrote only the original NVS and SPIFFS partitions
from the pinned pre-campaign full backup. Both partitions matched their original
bytes on readback; the active application, partition table and OTA metadata
were independently verified unchanged before runtime handoff.

A fresh Full snapshot matched all original `SELF_INFO` bytes, complete contact
records and all 40 canonical channels. Returned `DEVICE_INFO` settings matched
after excluding only the runtime active PIN and independently verified new
build/version metadata. There were no new core error flags. This qualifies
the original data readback and those returned settings, not every unexposed
preference or battery-backed cold boot. At that restoration checkpoint, the Full
runtime had WiFi off, an unset SSID, MQTT enabled but not running, and unavailable
ESP-NOW; temporary test changes require their own restoration. Later native-console
tests enabled power saving and USB logging and disabled MQTT. Those flags were
unrestored at 09:22 UTC, then passed identity-bound BLE restoration and readback
at 09:41:23: power saving off, USB logging off and MQTT on, with WiFi off and
MQTT not running. After USB returned, both original NVS/SPIFFS partitions again
matched the backup on readback; the active application, table and OTA metadata
were unchanged. Fresh binary and all 62 returned CLI fields matched the original
baseline. The diagnostic's owned host BLE bond was removed after restoring the
original device data. This establishes those readbacks and returned settings,
without extending the earlier cold-boot or unexposed-runtime qualification.

## Executed Companion checks

| Physical hardware | Candidate | USB queries/reopens | Owned contact add/update/delete | Add/delete survive warm reboots | BLE bonded hold/reconnect |
| --- | --- | --- | --- | --- | --- |
| RAK4631 | Full 652289ee | PASS: 100/10 | PASS | PASS | PASS: two 10-second sessions |
| T096 | Full FEM-on 652289ee | PASS: 100/10 | PASS | PASS | PASS: two 10-second sessions |
| MeshPocket | Full 652289ee | PASS: 100/10 | PASS | PASS | PASS: two 10-second sessions |
| T1000-E | Full 652289ee | PASS: 100/10 | PASS | PASS | FAIL/unqualified: four initial attempts; see 19 October follow-up below |
| Xiao S3 WIO | Full 652289ee | PASS: 100/10 | Not attempted: 350 occupied contacts | Not tested | Not tested |
| SenseCAP Indicator | LoRa Full 652289ee | Identity/readback and USB WiFi/BLE mode cycle PASS | Not tested | Not tested | Not tested |

Warm tests used a confirmed-absent random contact, verified complete readback,
performed the shipping guarded reboot, verified persistence, removed only the
recognized owned record, then rebooted and verified original state. They do
not prove cold boot. Display boards legitimately regenerate the active BLE
PIN when the saved PIN selects that policy; comparisons exclude only those
four runtime bytes and retain the private raw frames. Original contacts,
canonical channels, public identity, name and the other returned
`SELF_INFO`/`DEVICE_INFO` settings fields are checked. This comparison does
not inspect every persisted preference.

The Xiao retained all 350 contact keys and all 40 channels. A known contact's
advertisement and modification timestamps advanced during live mesh traffic;
this was not a contact edit by the runner. No existing Xiao contact was removed
to make room for a test record.

The Indicator LoRa Full mode cycle passed on 2026-10-18 UTC. USB checks
verified the exact board, complete candidate version and public identity,
then proved warm reboots into WiFi and BLE modes with the expected selected,
boot and live service states. The original BLE selector was restored and
another warm reboot verified. All 40 channels, the empty contact baseline,
returned binary settings, saved radio/RXPS/radio2 configuration and queried
Bluetooth preferences matched the original state, with no new core error
flags. The runner opened seven USB sessions and issued 510 framed requests;
it issued no RF message commands or partition/RP2040 writes. These checks do
not qualify an external TCP or bonded BLE session, font download/retention,
fresh NTP, visible rendering or cold boot.

The ESP-NOW Full build at `ec9ebc091` failed the mandatory internal RAM gate:
233,688 bytes available versus 251,168 required, a 17,480-byte shortfall.
Its native 480px WiFi canvas policy remains unchanged. Both ESP-NOW by
WiFi/BLE combinations remain unqualified; the normal Reduced ESP-NOW USB
image lacks those secondary transports and cannot substitute for their
Full acceptance.

T1000-E BLE initially resolves services and returns identity/core data, and
one attempt returned all five read-only getters. Later writes/reads fail with
backend service-discovery, ATT `0x0e`, or timeout errors. USB remains healthy.
The probe's final `disconnected` event is cleanup, not proof of which side
terminated a connection. These observations do not establish a root cause.

A fourth existing-bond trial captured HCI traffic. It completed encryption and
service discovery at MTU 247 and validated the complete version and public key.
The `get name` ATT Write Request (`0x12`) received its Write Response (`0x13`),
then no reply notification appeared. About 2.01 seconds later the controller
reported Connection Timeout (`0x08`), matching the negotiated two-second
supervision timeout and preceding the client's eight-second reply deadline.
No host HCI Disconnect command appeared in the capture. This trial therefore
lost the link before client cleanup. BLE uptime was 2720 seconds and later
USB uptime was 2993 seconds, consistent with the 272.70-second host interval;
both reported zero core error flags. This excludes a full MCU reboot in that
observed window, without identifying a firmware or RF cause. The post-trial
BlueZ state was disconnected and both owned processes closed. Negotiated link
parameters were a 30 ms interval, latency four and 2000 ms supervision timeout;
no PHY/data-length-change event appeared in the saved capture.

A separate diagnostic build enabled the existing RAM trace and disabled debug
printing, retaining the same source, version and connection parameters. Its
application DFU ZIP SHA256 is
`713765d9145db2aec03059a87280a065d4ae68e1a23ee4dd56a4d16f37b4f8c1`;
the exact ELF SHA256 is
`18442324b87deef9af658d2997b668ee10912929af4be9bacf9c473bc70d2169`.
This trial failed during GATT setup before any Companion request was sent.
The MCU initiated its documented no-app-data recovery exactly 15 seconds after
security completed. All 14 recorded stack dispatches exited, with a maximum
2.93 ms dispatch and 103 ms main-loop gap. All 62 trace records were retained,
with no ring wrap or recorded contention drops. HCI recorded the peripheral
termination and no host Disconnect command. This is a different failure stage
from the shipping post-getter supervision timeout; concurrent host Chromium
startup is a possible, unproven confounder. It does not identify a production
firmware fix. No old post-USB core metrics were reused for this trial.

The original shipping application was then restored through application-only
DFU. Identity, complete contact records, all 40 channels and all returned device
settings matched the immediately preceding baseline, including the active PIN;
core error flags were zero. An explicit read confirmed trace support absent.
The diagnostic image does not count as shipping BLE qualification.

GPS was enabled for a bounded 180-second local-only T1000-E telemetry window.
No location appeared. The exact GPS switch was restored, sync cadence was
unchanged, and packet TX counters did not advance. Outdoor acquisition,
fresh-fix age, stale/no-fix behavior and sleep current remain unqualified.

### T1000-E BLE follow-up, 2026-10-19 UTC

The unchanged Full `652289ee` application passed a fresh five-second bonded
session at 07:00 UTC and three consecutive 30-second held/reconnect sessions
at 07:04–07:07 UTC. Each session checked the board, complete firmware version,
USB-associated public identity, five read-only getters and advancing uptime.
The existing bond remained, host cleanup completed, and a subsequent USB
snapshot reported zero core error flags. The earlier timeout was not reproduced.

Source review separately found a use-after-free in the pinned Bluefruit
dispatcher: disconnect handling deleted its `BLEConnection`, then role routing
read `getRole()` through the freed pointer. Commit
`dc17f507dd0657cfd37dfe73e7634d6b81166f64` caches the role before deletion and
refreshes it when a CONNECTED event allocates a replacement connection.
The production-source ASan/UBSan regression reproduces the original memory
error and passes the corrected peripheral and central paths, callback ordering,
handle replacement and repeated reconnects. All 36 nRF52 BLE host tests passed.
This removes the disconnect dispatcher's memory-safety defect; it does not
establish the cause of the earlier on-air supervision timeout.

The shipping Full T1000-E and RAK4631 targets built successfully at that commit,
including their capability and runtime-RAM gates. The T1000-E application DFU
ZIP SHA256 is
`aa02aadad102201b30489efc80a044f98af3b9303630b4832ee21c65c3a93b40`;
its exact ELF SHA256 is
`6076d25e4c4ecadea8552c1bb56a97865b3369c1062d4a040e90d33368f9d214`.
The candidate booted through identity-bound application-only DFU and passed
three 30-second held/reconnect application sessions at 07:13–07:15 UTC, with
the same identity, getter and uptime checks. Before and after the BLE trial,
USB snapshots matched the pre-flash public identity, complete contact record,
all 40 channels and returned `SELF_INFO`/`DEVICE_INFO` settings, excluding only
the independently verified build/version metadata. Core error flags were zero.
The existing bond remained and owned probe/capture processes closed.

These successful application sessions include connection retries; they are
not evidence of uniformly reliable connection establishment. The baseline
capture contains six `0x3e` disconnects before encryption, and the candidate
contains two, each about 154–179 ms after a connection-complete event.
All three completed sessions in each trial encrypted successfully. Baseline
sessions two and three, and candidate session one, ended with `0x22` about
1.97 seconds after an explicit host HCI Disconnect request. No `0x22` occurred
during the completed getter/hold traffic; the other completed sessions ended
with `0x16`. Both captures recorded zero drops and no remaining target handle.
Neither held trial reproduced the earlier `0x08` supervision timeout. Longer
soak, phone-specific testing, establishment retries and host-requested
disconnect completion remain open; these captures do not assign their cause
to the peripheral or host controller.

The exact original `652289ee` application ZIP was restored through
application-only DFU. A fresh five-second bonded session also passed after
restoration. USB snapshots before and after that session matched the original
complete version, all `SELF_INFO`/`DEVICE_INFO` bytes, full contact record and
all 40 channels, including the active PIN; core error flags were zero. BlueZ remained paired,
bonded and disconnected, and the Bluetooth service remained active. No pairing
or preference setters were used; DFU transferred only the application. Raw HCI
and snapshots remain private; these comparisons do not inspect every unexposed
preference.

## MeshTower V2 SD

The MeshTower V2 SD Full image from `652289ee8` passed identity-bound,
application-only USB DFU and initial SD mount/list/space checks. All 16 returned
getter checks matched the original journals, including public identity,
radio/power/logging settings, `OTAFIX2.4.11-preview.2`, and InternalFS geometry
at `0xED000+28 KiB`. Ten consecutive SD space/list/core triplets completed all
30 replies: uptime advanced from 119 to 125 seconds, core error flags and
queue length remained zero, and the original directory metadata and reported
32.0 KiB used/959.6 MiB free stayed unchanged. The directory contained the same
single 4-byte `/mota/cache.off` marker; no file contents were exported. Original
firmware had error flags 2 before the update; the new boot is evaluated
separately. These checks qualify returned values and read-only SD access only.
They do not qualify card-absent fallback, cold initialization, interruption or
power-loss recovery, LoRa OTA transfer/install/serving, or a bootloader update.
No SD format/erase or deliberate LoRa OTA transmission was performed.

## Room authentication and browser

On the original 652289ee Room image, actual LAN requests from two separately
bound source addresses passed per-address failure isolation, shared-budget
exhaustion/refill, existing-session access, no token refund on success, and
post-window recovery. The Room password checks passed duplicate-password
suppression, five-unique-failure admission, the global 30-second window,
nonempty admin/guest rejection during lockout, and continued public blank
read-only access. Public guest wire role is `0`; role `1` denotes explicit
ACL read-only permission. An earlier runner used the wrong expectation;
its failed sample is retained separately.

The complete two-source-address LAN suite was repeated on d52e4e93 and passed
all WebConfig and Room cases in 161.67 seconds, with no temporary lockout left.
These network checks use guarded timing windows, not exact millisecond
boundary measurements.

The actual saved-network recovery AP passed anonymous protected-config denial,
wrong-password rejection, existing-admin login/config read and logout. Status
required `needs_setup=false` and `needs_password=false`, distinguishing recovery
from initial password setup. The Pi used an owned temporary WLAN profile while
Ethernet management/default route stayed unchanged, then restored its original
WLAN profile and deleted only the temporary profile.

The real Room browser also passed throttle-error polling, Connect retry and
draft preservation over this AP. The first AP browser attempt was an inconclusive
harness failure: the portal's favicon redirect was outside the strict request
allowlist. A narrow setup-mode redirect-chain check fixed that observer; the
second run passed with native fetch unchanged and zero compose/write requests.
Its unused icon follow-up failed outside the SSH-forwarded origin and is
reported separately from completed API response bodies. The AP BSSID was
measured, and the observed AP was open while protected settings still required
the saved admin password. AP two-source throttle testing remains unexecuted.

An actual Room LoRa trial verified a temporary matching
910.525 MHz/62.5 kHz/SF7/CR5 profile before transmission. Shared guest-password
login returned non-admin RW2 permission, denied admin-only `ver`, and returned
the board index. The next `mail settings` request had local send admission but
no decoded reply before its 35-second deadline; no delivered reply was discarded
by the runner's type/prefix matching. This is an incomplete RF request/reply
trial, not a demonstrated mailbox authorization fault. Source inspection confirms
active sessions may read their own settings. Source contacts/returned settings,
logout and the Room's original saved/temporary radio state were restored.

Post-trial counters showed 18 received packets (14 flood/four direct), six
transmits, one receive error and no outbound packet, with continuous MCU uptime.
This establishes reception on the temporary matching profile. It does not
identify which packet was lost or qualify mailbox mutation/retry behavior.

A separate admin trial with an explicit two-second gap after each peer request
passed login, exact version/board reads, complete mailbox settings, self-send,
body readback, acknowledgement and both exact request replays. It required an
empty own inbox and provisioned only its unchanged closed policy. The first
replay returned the original message ID; replay after acknowledgement also
returned that ID without re-enqueueing. Final reads verified an empty queue and
the original mode, delivery and complete allow list. Source contacts/returned
settings, logout and the Room radio lease were restored. This qualifies the
explicitly paced live-session sequence, not unpaced reliability or durable
replay across reboot/cache eviction. Owner/revision/retired-receipt metadata
persisted until the separate original-filesystem restoration described above;
policy preservation alone does not mean byte-identical filesystem contents.
The earlier incomplete trial remains a failed sample.

Actual Chromium/native-fetch testing exposed a loop-task stack overflow while
loading `board.index`. The saved core matches the 652 Room ELF and shows
`processRoomRequest -> loadRoomBoard -> recover -> filePresence -> snprintf`
reaching the stack-bottom watchpoint. Browser 202/429 response bodies also
were not consumed under their request deadline.

Commit d3e05abc separates operation scratch frames and drains response bodies
under a bounded deadline. Actual browser runs with an empty board and with
an owned 2048-byte UTF-8 article both passed login, automatic throttle-error
polling, production Connect retry, preserved unsent drafts and unchanged
boot/browser identity. Every completed 202 response in these runs was drained.

A subsequent populated-board stress run completed five whole article reads,
then triggered another panic during the sixth. Its fresh core positively
matches d3e05abc, reaches the loop stack bottom, and reconstructs the populated
index/filesystem/flash-cache/context-switch chain. The initial fix therefore
does **not** qualify repeated populated-board operations. Further stack
reduction and live retesting were required.

Commit d52e4e93 clears index state in place, recovers directly into the output,
uses a checked heap allocation for web index metadata, and separates read/write
scratch frames. Host fault and allocation-failure checks pass, including
preserving an existing 2 KiB article on allocation failure. Stack-frame analysis
shows a substantial reduction, but its estimated margin is not a hardware pass.
The original owned article was recovered through exact title/version/content
checks into a new private intent after flashing this candidate. Twenty complete
live index/article reads passed with unchanged content, boot identity and
continuous uptime. This qualifies the reproduced repeated-read case;
original-filesystem restoration was verified separately after the Full handoff.

The populated-board Chromium check also passed on d52e4e93: exact served HTML,
real admin form login, visible automatic throttle-error polling, Connect retry,
and unchanged unsent draft and Room boot identity. It used unmodified native
fetch, observed zero compose/write requests, and closed its owned browser.

The later CLI preflight showed sticky error bit `8`
(`ERR_EVENT_RADIO_WATCHDOG`), versus zero shortly after the d52e4e93 boot.
At observation the radio reported RX, zero received packets/errors, one direct
transmit, no outbound packet, and noise floor -73 dBm. There was no MCU uptime
regression. The HTTP/content pass does not establish absence of radio recovery
events.
Source inspection shows this Full Room build enables the five-minute
successful-RX-silence watchdog even when MQTT is off. Its flag is set before
the soft re-arm outcome is known. With zero received packets, that policy is
consistent with the observed transition; it is not proof that HTTP caused a
fault. Here, with `recv=0`, `last_rx=0s ago` is the formatter's zero-timestamp
fallback, not recent-RX evidence.

## Native official USB console

The unmodified official `console.js` from commit
`45a5257bf792b11a83f4c51c18d4f312addfb677`, SHA256
`107d3ad867915aea8b2454c324327397a0f9e2f0469656046054011bc64475f9`,
ran through Chromium's actual Linux Web Serial on the d52e4e93 V4 Room image.
Power saving was on; WebConfig WiFi, MQTT and ESP-NOW were off.

With USB logging off, 600.35 seconds of uninterrupted idle transmitted zero
commands/keepalives. The first complete CRLF reply arrived in 303.2 ms; after
close/reopen, the reply arrived in 35.7 ms. MCU uptime advanced continuously
and the preexisting sticky error flags remained `8`. Native streams, owned
Chromium and the original TTY settings were all closed/restored. This is a
console lifecycle pass, not a current-consumption measurement.

With USB logging on, the same image also passed 600.38 seconds with zero
commands/keepalives. The first reply took 164.5 ms and the reopened reply took
25.2 ms. Uptime remained continuous, sticky flags stayed `8`, and all streams,
owned Chromium and original TTY settings closed/restored. Power saving and USB
logging were then restored to their original off values. Earlier
startup/framing harness failures remain separate failed samples and do not
qualify idle. These passes bind the Room image; final Full Companion remains
a separate role-specific qualification.

The ec9ebc09 Full Companion image subsequently passed logging-off trial 03:
600.444 seconds, zero idle writes, a 275 ms first reply and an 18.1 ms reopened
reply. Uptime advanced from 1284 to 1885 to 1888 seconds; error flags stayed
zero. Both closes confirmed return to binary mode, and streams, owned browser
and TTY settings closed/restored.

Full logging-on trial 01 started at 09:02:25 UTC with baseline uptime 2076
seconds and zero core error flags. Its nominal 600-second idle began around
09:03:30 UTC. At 09:11:13 the kernel reported the target hub port as
`disabled by hub (EMI?), re-enabling...`; the parenthetical is the kernel's
tentative wording, not a demonstrated EMI cause. Target USB path `1-1.2.1.2`
disconnected at 09:11:14, followed by repeated descriptor/address errors `-71`.
The kernel attempted a port power cycle at 09:11:15 and reported inability to
enumerate at 09:11:16. Both the bound by-id and physical-path endpoints remained
absent at 09:22.

The run ended at 09:13:42 with `FileNotFoundError` while TTY cleanup reopened
the old path. That cleanup error masked the primary trial failure, which cannot
be reconstructed from the old safe report. No post-idle reply, reopen check or
qualified idle interval was obtained. USB recovery was still in progress at
the 09:22 checkpoint; no firmware, reset or physical-fault cause was proven.
The passed Room logging-on case does not qualify this Full image.

A subsequent identity-guarded, reset-only request to the exact parent hub's
port 2 returned success. Port status changed from `0x101` to `0x103` after two
seconds, with zero change bits: connected, powered and then enabled. The target
still did not enumerate; the sibling T096 was unchanged. No hub power change or
broader hub reset followed. A physical V4 RESET was requested from the operator.
ModemManager was restored to active at 09:24:34 UTC; all four checked host
services were active at that checkpoint.

The first read-only BLE diagnostic passed exact board, complete ec9ebc09 version
and public-key checks. At approximately 09:35 UTC, core uptime advanced from
3959 to 3961 seconds with zero error flags, versus the pre-idle 2076 seconds.
This establishes continued MCU uptime through the USB disappearance rather than
an MCU reset in that interval; it does not establish a working USB connection.

The live USB watchdog reported Auto, stage 2, two recovery attempts and zero
reboots. Its persisted last event was re-enumeration, sequence 2, uptime 2532
seconds, reason `0x08` (`CLIENT_INACTIVE`). Its RTC epoch is advisory and does
not date this incident reliably. Offline extraction of the exact original
SPIFFS active namespace found no `usb_wdg`, `usb_wdg.bak` or `usb_wdg.tmp` files.
The journal therefore was not imported by the original-data restoration.
Policy stage is volatile and starts at zero, so the evidence establishes two
current-boot USB recovery attempts. These are requested actions, not proof of
successful repair or proof that they caused the observed kernel errors. The
later sticky TX-stall observation is separate from the event's earlier reason.

Source tracing confirms actual single-TTY ASCII `stats-core` renews the
900-second logging-client lease; framed USB/BLE stats and CLI getters do not.
Natural expiry after the fresh pre-idle ASCII poll cannot explain a fault
starting roughly 97 seconds later. A bus-reset/session-generation change,
observed host/reader loss or logging-off can clear that lease earlier. No
pre-failure evidence selects one of these paths. This is watchdog-progress
evidence, without a firmware or physical-fault cause assignment.

BLE flag-restoration trial 01 passed at 09:41:23 UTC. Each of the three original
flag values was acknowledged and read back immediately and again at the end;
WiFi off and MQTT not running also matched. Uptime advanced from 4325 to 4333
seconds with zero error flags, and owned BLE cleanup passed. This restores those
flags, not the whole saved-state image or the new diagnostic bond.

Guarded warm-restart trial 01 then passed exact identity and original-flag gates,
durably recorded intent, sent the shipping `0x13` reboot request and observed
disconnect. Pre-request uptime was 4556 seconds with zero error flags; owned
BLE cleanup passed. USB was still absent at 09:45:07 and the kernel had no new
entries.

The post-restart read-only trial 01 passed exact identity with uptime 34→36
seconds and zero core error flags, proving the deliberate warm reboot. It did
not pair or create another bond. Persisted power saving off, logging off, MQTT
on, WiFi off and MQTT not running all matched, and every owned cleanup check
passed. The last watchdog journal was unchanged: re-enumeration, reason `0x08`,
uptime 2532 seconds and sequence 2 refer to the previous boot, not a new event.

By 09:48 UTC all four checked host services, including ModemManager, were active;
USB was still absent. No additional software or hub recovery is planned. The
operator was asked to unplug and reconnect only the V4 USB cable; physical
reconnection and verified USB recovery were pending at that checkpoint.
Battery-backed USB reconnection does not qualify cold boot. The new diagnostic
BLE bond was retained while authoritative restoration awaited USB return.

After the operator reset the V4, its exact USB by-id and physical-path endpoints
were verified present at 15:09 UTC. A fresh identity-bound binary snapshot passed
the Full ec9ebc09 board/version/public-identity gates: uptime advanced 481→482
seconds, core error flags and queue length were zero, contacts were empty and
all 40 canonical channel records were read. That snapshot did not verify the
live application flash hash. The Pi had been up approximately seven minutes at
session start; neither that observation nor battery-backed USB return verifies
a V4 cold boot.

The authoritative original NVS/SPIFFS restoration then passed complete readbacks,
with the active ec9 application, partition table and OTA metadata unchanged.
A fresh binary snapshot passed with uptime 85→86 seconds and zero core errors;
comparison matched original identity, complete contacts and all 40 channels.
All 62 returned CLI fields and the original power/logging/MQTT flags matched.
Only the owned diagnostic host BLE bond was removed; all four original checked
services, including ModemManager, were active afterward. USB return and restored
data do not qualify the failed Full logging-on case. No cause or firmware fix is
established, and WSS remains unstarted while upstream changes are integrated.

For Full, `tools/hil/official_console_acceptance.py` uses explicit
`--terminal-mode companion`. It sends the shipping terminal-start control
through the unmodified official module on each open and confirms terminal-stop
on each logging-off close. Those four setup writes are counted separately from
the zero writes during idle. Full's labeled logging-status reply is checked
against the exact live mode. An earlier attempt omitted this handshake and
timed out before idle; another failed during owned Chromium startup before
serial opened. Both remain separate failed harness samples.

## Shipping Morse results

The unchanged Full RAK4631/MeshPocket images were tested at 902.1 MHz with
requested chip TX power at most -9 dBm, three different messages, 25/60 WPM,
both directions and two repeats. No HIL threshold, timing or sampling override
was enabled. All 24 decoded messages mismatched. Thirty-second no-transmitter
controls produced 20 characters on RAK and four on Pocket.

Wanted peaks across all 24 trials ranged from -50.5 to -46.0 dBm; the 25 WPM
subset ranged from -50.5 to -47.5 dBm. Maximum observed valid sample gaps were
2-3 ms. Quiet RSSI excursions reached -110.5 dBm on RAK and
-92.5 dBm on Pocket, above their roughly -126/-125 dBm floors. This supports
an automatic-detection qualification failure, without identifying external RF
interference versus receiver-generated excursions. The reported `bad` counter
is consecutive read failures, not a cumulative guarantee of no SPI errors.
All decoded capture lengths matched their firmware headers and reached the
end marker, excluding host paging truncation in these trials. Source inspection
shows automatic squelch can admit sustained quiet excursions and corrupt mark
or gap boundaries; RSSI extrema alone do not establish those pulse durations.

A separate five-minute Pocket quiet capture expired automatically after
300.40 seconds, retained seven false characters, and returned the same capture
on repeated firmware paging. Lifecycle/retrieval passed; silence detection
failed. Original settings were read back after both campaigns. Historical
successful HIL fixed-threshold trials do not qualify the shipping detector.

## Host checks and CI

The production Room fix passed the complete workflow at d52e4e93
([run 39107275083](https://github.com/mikecarper/MeshCore/actions/runs/39107275083)).
A later tooling run exposed a host fixture that invoked real `fuser` against
its fake device path. The stricter ownership guard correctly refused that
diagnostic error. Commit 7a5a7993 makes the no-owner fixture deterministic and
retains real refusal tests for busy/error cases; its complete workflow passed
([run 39109551854](https://github.com/mikecarper/MeshCore/actions/runs/39109551854)).
The later AP browser-observer workflow also passed at `5fd33a230`
([run 39110179910](https://github.com/mikecarper/MeshCore/actions/runs/39110179910));
both that run and the preceding 7a5a7993 run completed with all jobs green.
The native-console cleanup follow-up preserves the primary JS/transport
failure separately from browser/TTY cleanup failures, reports missing or
replaced endpoints, and cannot qualify a cleanup failure. The native-console
and Linux TTY suites passed 35 offline checks. The follow-up workflow at
`76220d5d7` completed successfully with all eleven jobs green
([run 39115389840](https://github.com/mikecarper/MeshCore/actions/runs/39115389840)).
The later recovery-report workflow at `7b41d2893` also completed successfully
with all eleven jobs green
([run 39116894845](https://github.com/mikecarper/MeshCore/actions/runs/39116894845)).
Those CI passes do not rerun or qualify the failed hardware case.
Documentation route/link checks also passed in the CI-pinned renderer environment.

## Remaining release gates

- Investigate remaining T1000-E connection establishment retries and `0x22`
  completion after host-requested disconnects; the 19 October follow-up fixes
  disconnect memory safety and passes bounded application sessions, without
  closing BLE reliability qualification or explaining the earlier `0x08`.
  Retain repeated populated-board stress as a regression gate for subsequent
  Room candidates.
- Retain actual LoRa receive evidence alongside the Room HTTP results; the
  quiet-radio watchdog flag is not a successful-RX or recovery-outcome counter.
- Diagnose shipping Morse noise rejection on a controlled RF setup.
- Execute the real MQTT/WSS soak with broker PUBACK and heap evidence; Full
  V4 OLED has one runtime slot despite six saved configurations. Commit
  ec9ebc091 exposes the existing read-only diagnostic counter on Full. The
  recovery-guarded soak is staged and pinned but **not launched**.
- Diagnose the failed Full logging-on idle case before another attempt. Original
  data-partition readbacks, fresh binary/62-field comparisons and diagnostic-bond
  cleanup passed after USB return. Retain the Full logging-off PASS and the two
  Room logging-mode passes as separate results. Requalify the final source after
  integrating upstream changes.
- Complete two-source recovery-AP throttle checks beyond the passed existing-admin
  authentication and browser cases. Execute migration credentials and both source
  OTA slots, actual browser upload, interruption/recovery, privileged replay
  persistence and transient guest churn.
- Execute contact-storage fault cases, LoRa full/delta OTA and SD recovery.
- Complete Indicator external WiFi/BLE endpoint, font, NTP and visible-rendering
  checks beyond the passed LoRa mode/boot-state cycle. Resolve the ESP-NOW Full
  build RAM gate before qualifying its two secondary transport combinations.
- Obtain physical power-removal and outdoor GPS assistance. USB reset and
  software reboot do not establish battery-backed cold boot or sleep current.
- C6, STM32 and standalone RP2040 LoRa hardware are absent from this bench;
  the Indicator RP2040 peripheral is not a substitute.
- Freeze the final source, run the canonical release build/package matrix and
  finish release notes. This campaign binds several exact development images.

Private raw snapshots, flash images, credentials, BLE PINs, inbox traffic and
cores remain outside the repository in the dated campaign directories.
