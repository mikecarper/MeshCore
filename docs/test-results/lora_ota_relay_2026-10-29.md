<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/lora_ota_relay_2026-10-29/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# LoRa OTA through one relay, 29–30 October 2026

An isolated one-hop LoRa transfer completed using ESP-NOW only for laboratory
control. The production menu also completed a fresh installation with LoRa
administration and ESP-NOW disabled on every participant. The shared-channel
capture limits below distinguish these two checks.

## Fixture

| Board | Role | LoRa frequency |
| --- | --- | --- |
| Heltec V4.3 | USB source | 909.5 MHz |
| Heltec V4.2 | Relay | Primary 909.5 MHz; radio2 908.5 MHz |
| Heltec V3 | Target | Saved 907.5 MHz; temporary 908.5 MHz |
| T1000-E and Mesh Pocket | Passive RF observers | Input and output channels |

Both transfer channels used 500 kHz bandwidth, SF5, CR5 and preamble 128. The
relay had repeating and crossing enabled, with ESP-NOW disabled. Source and
target kept radio2 and crossing off. Their existing ESP-NOW link carried native
ACL administration; bridge filters excluded OTA payloads in both directions.

## Completed trials

| Trial | Result |
| --- | --- |
| Full package | Intentionally stopped at 58/1,047 accepted blocks; no installation |
| 213,348-byte differential package | 104/104 blocks complete at 834.831 seconds from controller start; no installation |
| 461-byte CFC differential package | Version/build-date change from `v1.18.0.2` to `v1.18.0.3`; installed a 2,142,104-byte application |

The 213 KB trial stopped after completion because `ota dev verify` cannot verify
ESP32 flash-backed staging through its memory-buffer diagnostic. This was not
a remote permission failure. The recorded trial remains failed; a completed
download is not proof of installation.

The small menu update installed body `63D3F15F24AE2BA0`, confirmed over USB.
Its first post-reboot menu check failed because the native ACL return PATH was
volatile. The transport now re-establishes that PATH after a reset without a
password or routine repeated bootstrap traffic. Menu recovery subsequently
finished with `install_verified=true` and restored all owned settings.

## Independent relay evidence

For the 104-block differential, observers captured **1,339 distinct identical
DATA payloads** on 909.5 MHz with path count zero and on 908.5 MHz with path count
one. Every output path matched the relay's independently checked prefix. All
104 block indices were represented. Disabling relay crossing stalled the same
transfer; re-enabling it resumed progress. The target's 907.5 → 908.5 MHz change
was acknowledged over ESP-NOW and confirmed by transfer progress.

The full-package trial had 777 matching DATA payloads. Neither capture had
parser or recorded path-metadata mismatches.

The capture contains legacy DATA for 103 differential blocks and a compressed
v2 tail for block zero. Full-sized v2 frames are absent from this capture, so
these counts do not measure all transmitted v2 packets. Package size savings
must not be described as an entirely compressed OTA wire transfer.

## Fresh laboratory menu run

**PASS:** the fresh run from the `app0` baseline completed download, installation,
reboot and exact runtime verification. The saved record ended at `finished` with
`install_verified=true`, no recovery errors, both endpoint leases off and the
original endpoint frequencies restored. USB independently confirmed the expected
version and body hash.

## LoRa administration and fresh menu installation

**PASS:** Heltec V4.3 → Heltec V4.2 → Heltec V3 used 909.5 MHz, 500 kHz, SF5,
CR5 and preamble 128. ESP-NOW, radio2 and crossing were off on every participant.
The USB source used an explicit three-byte relay prefix for authenticated CLI
requests and the reverse return route. The menu opened and restored all three
primary OTA windows, RXPS and OTA policies, and the relay's repeating setting.

The first run installed the earlier package and restored settings, but its
capture contained no DATA. A second run used a newly compiled `v1.18.0.4` image
and new package ID `434E4672`, excluding reuse of the earlier package. The
377-byte differential contained 163 payload bytes and reconstructed the full
2,142,104-byte application exactly on the host. The menu verified the new body
`356D1F43B1B5E15A` after reboot and finished without recovery errors. USB
independently confirmed that body and version. All leases were off, the source
native path was off and its host folder was detached afterward.

The fresh capture contains the new DATA block on the source's zero-hop path.
Its v2 stream inflated from 156 to 163 bytes. Relay copies were captured for
discovery, manifest and proof messages, but no matching DATA relay copy was
captured. These co-located nodes can hear the source directly: this run proves
fresh LoRa delivery and routed administration, not that its DATA required the
relay. The separate two-frequency trial above supplies the isolated DATA relay
evidence. Neither result is a multi-relay hardware qualification.

The measured USB source used `5b01060d8` plus the native path change committed
as `925f37a44`; its running body was `463F0AD2C11A7271`. The target's small test
updates changed version/build metadata in the same application code. They
exercise transfer, full-image reconstruction, application-slot installation
and reboot rather than functional changes in the receiver application.

## Scope and handoff

The isolated fixture required a preconfigured dual-profile LoRa relay,
ESP-NOW control link, ACLs and OTA exclusion rules. ESP-NOW was a test aid for
changing frequencies and reading status; it is not a production requirement.
The production menu instead uses existing ACL grants and an already working
LoRa administration route. It keeps the saved channel and manages relay OTA
windows. Only one physical relay was tested; selectable larger hop limits do
not establish hardware coverage for longer routes.

After testing, all three infrastructure boards were flashed with normal
`v1.18.0.2` firmware from `925f37a44`. Their running versions and application
body hashes matched the built images. They were left with RF, repeating,
radio2, crossing and ESP-NOW off, with no temporary leases. Both passive
observers returned to their original radio settings and transmitted no packets.
The [evidence summary](lora_ota_relay_2026-10-29.safe.json) records the final
firmware hashes and qualified outcomes without device identities.

Offline checks passed on Windows and Linux: **287 wizard tests and 21 relay
controller tests per OS**. Native routing tests also passed on Windows and
under ASan/UBSan on Linux. Seven hosted-document tests and the Windows JavaScript
link harness passed. These checks supplement the hardware evidence above.
