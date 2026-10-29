<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/hardware_validation_mercerwood_1_18_0_2_2026-10-28/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Mercerwood 1.18.0.2 candidate checks, 28 October 2026

Status: **COMPLETED WITH UNRESOLVED RESULTS**. This finite campaign covers **15 radios**; the
Indicator's separate RP2040 display coprocessor is excluded from radio flashing.
S3 remains unqualified; T1000-E/T096 BLE and Tower RF misses remain unresolved.
This is not release approval. Results retain the source
actually measured; a later build does not inherit an earlier physical pass.
The [machine-readable evidence summary](hardware_validation_mercerwood_1_18_0_2_2026-10-28.safe.json)
records the source and artifact hashes.

| Board | Role | Measured source | USB / warm reboot | BLE | Zero-hop LoRa |
| --- | --- | --- | --- | --- | --- |
| Seeed T1000-E | Full Companion | 226 | PASS | FAIL, first USB-release phase; second not attempted | PASS, three rounds each way |
| RAK WisMesh Tag | Full Companion | 226 | PASS | PASS, three clean sessions | PASS as observer for all three pairs |
| Heltec T096, FEM on | Full Companion | 226 | PASS | Three payload sessions; strict FAIL (`0x3e`) | PASS, three rounds each way |
| Heltec MeshPocket | Full Companion | 226 | PASS | PASS, three clean sessions | PASS, three rounds each way |
| Heltec V4.3, FEM off | Full Companion | C9 | PASS | PASS with restored original PIN, after host bond repair | PASS, separate cleanup-comparison review |
| Heltec V3 | Repeater | C9 | PASS | Not applicable | PASS with discovery temporarily enabled |
| LilyGo T-Deck | Repeater | C9 | PASS | Not applicable | PASS with discovery temporarily enabled |
| SenseCAP Indicator | Repeater | C9 | PASS, after 29.3-second startup | Not applicable | PASS with discovery temporarily enabled |
| Heltec V4.2 | Repeater | C9 | PASS | Not applicable | PASS, three discovery responses |
| Heltec MeshTower V2 SD | Repeater | 70C7 | PASS, including automatic emergency radio sleep | Not applicable | Unresolved missing responses; RXPS-off trial also incomplete |
| SolarXiao MPPT 33S | Repeater | 70C7 | PASS | Not applicable | PASS, three discovery responses |
| RAK3401 | Repeater / MQTT collector | 70C7 | PASS | Not applicable | PASS after warm preparation, with discovery temporarily enabled |
| Seeed XIAO nRF52840 | Repeater | 70C7 | PASS | Not applicable | Three responses pass; restoration confirmed separately |
| RAK4631 | Repeater | 70C7 | PASS; legacy getter limits below | Not applicable | PASS after warm preparation, three discovery responses |
| Seeed XIAO ESP32-S3 WIO | Observer Repeater target | Original retained | Unqualified; no programmer response | Not applicable | Unqualified |

C9 is [`c9acec474`](https://github.com/mikecarper/MeshCore/commit/c9acec474bb9617d4f92dbdc0401765701989288).
70C7 is [`70c7d5b47`](https://github.com/mikecarper/MeshCore/commit/70c7d5b478b19f16d232389779ca455e73be7eba).
226 is [`226ccc332`](https://github.com/mikecarper/MeshCore/commit/226ccc332d29e78c3dc69cdb70a228baca403711).
70C7 changes the nRF52 emergency CPU idle backend; 226 changes the nRF52
Companion BLE lifecycle. V3 and V4.2 report the observer suffix in their runtime
version. The RAK4631 hardware result is for its 70C7 Repeater image; the separate
Full Companion capacity build and uninstalled S3 Observer build are described below.

## Measurements

USB qualification requires **100 query cycles, ten fresh reopens and one
verified warm reboot**, with zero core flags. Companion checks cover owned
contact creation/persistence/removal, natural expiry of a one-minute RAM-only
radio2 lease, and restoration of the original secondary profile/automatic owner.
Transmit-power and saved Bluetooth PIN setters require acknowledgement,
readback, reopen and restoration. Actual RF power and an active PIN changing
before restart were not measured.

BLE requires three authenticated thirty-second sessions, the USB-bound public
identity/device information and a clean HCI lifecycle. Payload delivery and
strict HCI acceptance are separate results. T1000-E uses target-only USB host
deauthorization; USB power remains uncommanded, so this is not a battery-only
or physical cable-unplug test.

On 226, Tag and MeshPocket pass both payload and strict HCI checks. T096 passes
all three payload sessions and encryption succeeds three times, but its trace
has an extra connection ending with `0x3e`; strict acceptance therefore fails.
No missing-key marker or host Bluetooth-service crash was observed in that
trial, and its cause is unassigned.

T1000-E's 226 trial actually released USB host ownership and waited ten seconds,
leaving host WiFi unchanged. Its first phase completed no payload session:
GATT reported `Unlikely Error (0x0e)`, with HCI disconnect reasons `0x3e` and
`0x13`. Encryption succeeded once; no missing-key marker or host daemon crash
was observed. The second phase was not attempted after that failure. USB
enumeration, identity, preferences, contacts, channels and uptime continuity
were verified afterward, with zero core flags and queue length. No further
BLE/RF retries were made.

On C9, the T1000-E completed three payload sessions in its normal-host-WiFi trial,
but the controller recorded an extra connection and disconnect reasons
`0x3e` / `0x22`. A controlled WiFi-quiet trial completed no payload sessions,
with `0x3e` / `0x08`; host WiFi and USB authorization were restored. Quieting
WiFi did not establish a remedy. A separate radio2-off diagnostic found no
unique matching advertisement within its bounded scan, so it attempted no
connection and captured no authentication failure under that condition.

The V4.3 initially had a host/device bond mismatch. Target-only host bond repair
later yielded three clean thirty-second sessions with the original saved PIN
restored. A temporary-PIN phase failed while BlueZ 5.66 crashed during
notification setup; that failure remains recorded. Identity, settings,
contacts, channels and unrelated host bonds were checked afterward. This is
a conditional C9 pass; no firmware remedy for the host crash is claimed.

RF trials use shipping firmware, zero-hop controls, fresh nonces, full peer
identity and physical TX/RX counters. Live control pushes leave the offline
message queue untouched. Bench RF/TX settings and owned awake holds are
restored. Repeater discovery checks the radio2 extension within the existing
four-per-two-minute limiter.

The fresh 226 Tag star completed **all 18 legs**: three exchanges in each
direction with T1000-E, T096 and MeshPocket. Every leg matched the identity,
nonce and physical packet counters, with zero receive errors; the slowest took
**0.524 seconds**. Original radio/TX settings, binary preferences, contacts and
channels were restored, with no offline-queue reads or RF retries. Earlier C9
exchanges remain separate history.
V3, T-Deck, Indicator and RAK3401 had discovery hidden by their saved advert
settings. Their conditional trials temporarily enabled discovery on the
isolated bench profile and restored the original values. A missing response
while discovery was disabled is not a failed response from an eligible node.

The Tower returned two of three baseline requests, one of two quiet, paced
requests, and one of two requests with RX power saving disabled. Missing
responses remain unresolved; disabling RXPS did not establish a remedy.
Original settings and its automatic emergency mode with no awake hold were
restored after each measured trial.

Separate reviews accepted the original V4.3 six exchanges and XIAO nRF52840
three responses: their harness failures compared changing timing/countdown or
live battery fields as saved settings. Read-only checks confirmed restoration;
no RF was repeated. Original failures remain recorded. Preflight formatter/type
errors sent no requests. A manual V3 diagnostic exposed an unsafe host transport
fallback; recovery completed and the harness now requires an explicit reviewed
transport before opening a port.

## Production fixes

The Tower reported 0% battery and entered automatic emergency radio sleep.
The first candidate's ten-second CPU idle slices starved USB cleanup/reply
draining: queries and three reopens passed, but the fourth received no reply.
Its original application and queried configuration were restored before the fix.

70C7 caps CPU idle slices at **100 ms while a USB host is enumerated**, retaining
the ten-second detached limit. It does not wake LoRa or change the battery
policy. On the Tower, automatic mode, reported 0%, sleeping radio and no awake
override were verified before and after USB stress, and again after warm reboot.
All **213 completed commands** responded, with a maximum measured latency of
**1.0952 seconds**. The backend's five tests and runtime's thirteen tests pass
with address and undefined-behavior sanitizers. Actual battery state of charge
and current draw were not measured.

Earlier [emergency-power checks](emergency_power_2026-10-28.md) retain their
original source; this campaign adds the automatic-entry USB regression/retest.

A host regression reproduced a BLE connection stuck after USB disabled/re-enabled
Bluetooth while a callback was delayed. 226 rejects stale physical sessions,
uses bounded disconnect recovery and preserves a replacement connection when
an old disconnect arrives. **25 focused native tests passed**, covering delayed
callbacks, normal sessions, retry timing and advertising recovery. Four Full
nRF52 Companion packages pass capability checks with unchanged linked RAM
admission. All four pass their new 100-query, ten-reopen and warm-reboot checks;
all 18 new RF legs also pass. Tag/Pocket BLE passes and T096's strict lifecycle
exception are reported separately above. The earlier T1000-E failures remain
causally unassigned.

## Emergency scheduling and collector

On 70C7, SolarXiao MPPT 33S and RAK3401 completed a real one-minute awake lease,
two accelerated past-RTC hourly wake boundaries and manual-mode persistence
through a verified warm reboot. Independent advertisements proved timer wake
after approximately **22 seconds without a target USB write** at each boundary.
The CPU remained connected to an enumerated USB host; these are RF scheduling
measurements, not detached-host or current-consumption measurements.

Solar's first manual-reboot check closed CDC before the command was consumed.
Its successful lease/timer-wake observations remain separate from that failed
axis. The corrected single-write driver holds CDC until reset or 1.5 seconds;
a focused follow-up proved reduced uptime and manual persistence without
repeating the wake trials. RAK3401 completed the corrected full trial.

Present UTC was restored before original RF settings, and owned awake holds
were revoked. A separate full-body read-only comparison verified **two Solar
ACL entries and one RAK3401 ACL entry**, available saved settings and trusted
OTA signer lists. Earlier first-line-only ACL counts are superseded by that
complete comparison. Solar's observed CAD flag and the quiet watchdog flags
on some boards remain in the preparation history; warm preparation is not
counted as a clean long-duration run.

The collector's five configuration/unit digests, original active state and
serial ownership were restored. One owned RAW packet was observed in its
journal with receive metadata and a successful local MQTT publish call. Receipt
at an external broker was not measured.

## Builds and limits

The Indicator's first candidate readiness check received no reply within six
seconds. Its original application and data were restored and verified before
one controlled trial of the same candidate. That trial received the exact
version and original identity after **29.3055 seconds**, then passed the unchanged
100-query / ten-reopen / warm-reboot qualification with zero core flags. The
startup wait is preparation, not extra accepted query cycles; the original
failure and each unsuccessful readiness probe remain recorded.

The initial **15 board plans / 20 packaged profiles** pass RAM/capability checks,
including Full/Reduced images for five nRF52 Repeaters. Four replacement Full
Companion packages were checked for 226. The provisional XIAO ESP32-S3 Companion
target is superseded by an uninstalled Observer Repeater build. PlatformIO
ran serially. Full application installs omit bootloader, SoftDevice, filesystem
and partition-table images. ESP32 recovery restored original applications and
NVS/SPIFFS snapshots with readback verification.

An additional RAK4631 **Full Companion** build at `f6034e1f`, using the CI library
versions and maximum development-version length, passes capacity checks with
a **712,652-byte packaged application in 712,704 bytes: 52 bytes free**. Its final
change shortens Bluetooth startup diagnostics. This is a build-only result;
that Companion image was not installed on the tested Repeater. The S3 Observer
Repeater package at `5a72a991` also passes build checks and remains uninstalled.

The user explicitly made original-image backups optional for these test radios.
RAK4631 therefore received its verified 70C7 application through working CDC
DFU without an original rollback image. It passed 100 queries, ten reopens,
warm reboot, exact candidate image/identity checks and current-RTC readiness;
its final core flags and queue were zero. Seven available original settings
and the OTA signer list matched. Its older firmware returned `??:` for password,
ACL and several newer getters, so their preservation cannot be established
from those queries. The original comparison failure remains recorded; a narrow
legacy-error classification review and fresh readiness check accepted the
completed measurements without reflashing or repeating them. Bootloader
version text changed from its numeric fallback to `OTAFIX2.4.12`; no bootloader
image was written, and a before/after CRC-equality claim is not made.

RAK4631's later RF preflight refused a latched idle watchdog flag `8` before
sending any request. One documented warm reboot reduced uptime from 2,545 to
4 seconds; the unchanged 70C7 image then returned all three exact discovery
responses to the 226 Tag observer, including the matching radio2 extension.
Fifteen available current settings, one current ACL entry and OTA signers
matched across that warm preparation. This does not establish the unavailable pre-upgrade password/ACL
state or long-duration idle reliability.

XIAO ESP32-S3 did not answer application probes or the programmer after two
physical ROM-entry attempts. No firmware was written. A MAC-bound DHCP/neighbour
lookup found no address, so no HTTP identification probe was made. It remains
unqualified; an enumerated USB device alone does not prove a working application.

The remaining release gates are the Tower's missing discovery responses,
T096's extra BLE connection, T1000-E's failed BLE phase and the unqualified
S3. Successful checks on other interfaces do not clear those results.

Read-only OTA readiness is distinct from an actual LoRa firmware transfer and
installation. These checks do not establish range, calibrated transmit power,
display pixel quality, cold-power recovery, phone interoperability, current
savings, long-duration reliability or every firmware feature.
