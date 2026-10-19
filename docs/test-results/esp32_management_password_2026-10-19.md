<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/esp32_management_password_2026-10-19/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# ESP32 management password hardware check, 2026-10-19

**PASS on one physical XIAO S3 WIO Full Companion at Mercerwood.** The same
admin password worked for the setup and OTA WiFi hotspots, WebConfig login,
and authenticated WiFi firmware upload at source `036fb477`. Password changes
through the production browser, automatic reauthentication, and persistence
after reboot and OTA passed. The original firmware/data and the Pi's networking
were restored and verified. This does not qualify every ESP32 board, firmware
role, migration path, or the release.

A [sanitized result record](esp32_management_password_2026-10-19.json) preserves
the source/artifact hashes and check outcomes without credentials or device IDs.

## Candidate and recovery baseline

| Item | Recorded value |
| --- | --- |
| Physical board | XIAO S3 WIO |
| Firmware target | `Xiao_S3_WIO_companion_radio_full` |
| Source | [`036fb47717774a1fb13e824175462ad4442d00c7`](https://github.com/mikecarper/MeshCore/commit/036fb47717774a1fb13e824175462ad4442d00c7) |
| Candidate version | `v1.18.0.1-halo-keymind-cascade-dev-036fb477` |
| Application BIN size | 2,271,096 bytes |
| Application BIN SHA-256 | `2419a991b7421b8444b0f39e4e941a60f9d4e5bcfb3a187ca77a0a2d41d31dcf` |
| Pretrial full-flash backup size | 8,388,608 bytes |
| Pretrial full-flash backup SHA-256 | `9517ff0caa61ae5ae2be95d4dfd2ab28a58e979eb1c7607b857055b9fb5f1445` |

The pretrial full-flash image and raw hardware evidence remain private. The
independent restoration readback matched this full-flash hash before runtime
handoff. Candidate application and full-flash backup hashes identify different
artifacts.

## Physical results

| Check | Observed result |
| --- | --- |
| Eight-character printable ASCII password | WPA-protected setup AP was usable with the admin password. |
| Fifteen-character ASCII password containing spaces and punctuation, saved through the API | Protected AP operation passed with the changed credential. |
| Five-character password | Setup AP became open; configuration still required the admin HTTP login. |
| Return to a twelve-character printable ASCII password | Protected AP operation passed again. |
| Session rotation | Old HTTP cookies were rejected; the originating operation retained its scoped save receipt. |
| Warm reboot persistence | The new password remained usable after an actual Binary Companion reboot. |
| Production browser password form | Login, exactly one password save, scoped receipt, automatic reauthentication, and actual protected-AP rejoin passed with the new password. |
| OTA hotspot and HTTP authentication | The same current admin password joined the protected OTA AP and authorized HTTP Basic as username `admin`. Anonymous, wrong and previous credentials returned HTTP 401 on `/`, `/update` and `/log`. |
| Empty OTA requests | Anonymous POST returned HTTP 401; authenticated empty POST returned HTTP 400 before image installation. |
| Real WiFi application upload | One raw application upload sent exactly 2,271,096 pinned bytes; HTTP 200 and the exact install/reboot response were received. |
| Post-OTA USB | Candidate full version and public identity matched; 350 contacts, 40 channel slots and zero core error flags were retained. |
| Post-OTA password persistence | The browser-saved password joined the protected setup AP and authenticated WebConfig after OTA; the previous password returned HTTP 401. |
| Alternate OTA slot | CRC-selected slot changed from original `app0` at `0x10000` to `app1` at `0x340000`; its application bytes matched the candidate SHA-256. |

These are real-board association, HTTP API and production browser password
results. Chromium used the normal home-page loader and the decoded loader/UI
bodies matched the candidate's embedded hashes; native fetch and production
handlers were unchanged. One new-password login was observed, and the browser
was closed after success. This does not establish browser firmware-upload
completion behavior. The reboot was a software warm reboot; it does not
establish power-removal persistence or a battery-only cold start.

The expected policy is documented in [WiFi setup](../WiFi.md),
[Full Companion](../companion_radio_full.md), and the
[password command](../cli_commands.md#view-or-change-this-nodes-admin-password).
Password classes are recorded here without exposing the test secrets,
network names, BSSIDs, cookies, or private device identity.

## Original device and host state restored

The original full `app0` and `app1` partitions, NVS and SPIFFS were restored,
with complete readback verification after every write. Original OTA metadata
was restored last. Bootloader, partition table and coredump bytes were unchanged.
An independent 8,388,608-byte readback then matched every byte and the SHA-256 of
the fresh pretrial backup, before the firmware was started again.

The separate normal-boot check passed on the original `652289ee` build:
complete SELF_INFO and DEVICE_INFO records, full CLI version, all 350 raw contact
records, and canonical contents of all 40 channel slots matched the baseline.
Core error flags were zero and the USB session closed normally. Whole-flash
equality is a preboot observation; normal firmware may write flash after boot.

The Pi's original WLAN profile was restored by its recorded UUID. All 12 owned
temporary profiles were removed. The original Ethernet connection, default
routes and selected Internet route remained unchanged. No factory reset or
bootloader/partition-table/coredump write was used.

The same candidate version was used for the initial USB installation and the
WiFi upload. Installation is established by the success response and the
independent alternate-slot application readback, rather than by a version
string alone. The authenticated `/log` request returned HTTP 404 because no
packet log was present; unauthenticated access was rejected before that check.

## Retained failed samples and limits

Early attempts did not count as passes. An incorrect identity offset in the
test manifest was rejected before HTTP mutation. The first browser attempts
hit a startup deadline or requested the versioned UI asset without its required
version token; the completed browser run used the normal production loader.
A separate initial HTTP connection failed before browser startup; a fresh AP
scan and guarded rejoin restored access.

A previous WiFi key did not associate within the 20-second wait, but
NetworkManager reported a timeout rather than the test's expected wrong-key
error. That sample establishes only bounded non-association. The HTTP old-key
rejections and successful new-key associations above are separate observations.
This campaign does not establish uninterrupted WiFi operation or cold-boot
behavior.

A scripted raw upload does not by itself qualify the unchanged browser
uploader. The broader [ESP32 browser OTA release gates](hardware_validation_checklist.md#esp32-web-console-and-browser-ota-release-gates)
remain separate, as do two-source recovery-AP login throttling, infrastructure
roles, partition migration, and other hardware families.
