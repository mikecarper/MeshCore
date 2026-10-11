<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/companion_lost_reply/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Companion lost-status response validation

Validated on 2026-10-10. These are production-path host tests, browser tests
and firmware builds. No lab radio was flashed for this feature; this report
does not qualify a sleeping tracker client or a live LoRa exchange.

## Automated checks

The responder test executes the actual private receive callback, message
composition/send functions, ACK handling and local CLI policy. Seven compiled
variants cover normal and shared one-key DM queues, a text terminal, explicit
STM32 enablement, default STM32 exclusion and explicit exclusion elsewhere.
AddressSanitizer and UndefinedBehaviorSanitizer checks passed.

Coverage includes exact question matching, Yes/No loop avoidance, known full
identities, temporary/unknown contacts, room/channel/CLI exclusions, a full
offline inbox, duplicate requests, cooldowns, clock wrap, busy packet/ACK
capacity, send failures and preservation of pending user messages/deadlines.
Queueing the response preserves the shared send timer. ACK reception retains
the existing shared-timer behavior; independent per-message expiry continues
to protect pending user deliveries.
Excluded builds have no responder state, automatic send, successful setting
command, save or WebConfig capability.

Saved preference tests cover all three values, all 256 stored byte values,
legacy images loading off, reboot, atomic save failures, recovery copies and
runtime adapter preservation. Existing CLI, delivery, inbox, notifications
and preference regression suites passed after updating adapters for the new
append-only preference byte.

WebConfig tests execute the actual JSON and settings handlers with
ArduinoJson. Chromium exercises Off -> No -> Yes -> Off, Companion-only
visibility and the absence of the control on infrastructure roles. Existing
browser, authorization, secret masking and generated asset checks passed.
Both new suites are included in the unit-test workflow.

## Capacity and reliability

The responder cache is 184 bytes on ARM and retains four complete public keys.
The saved preference uses an existing padding byte in the tested ARM preference
object and one new byte in its explicit storage image. No heap allocation is
introduced by the responder.

Compact STM32 Companion images exceeded their existing application boundary
when the responder was included. Those default to `MESH_ENABLE_LOST_REPLY=0`,
preserving their existing features and filesystem layout. Explicit opt-in is
tested synthetically and remains subject to the normal capacity checks.

The ordinary RAK WisMesh Tag BLE queue uses 240 frames, retaining 350 contacts
and 40 channels and saving 2,832 bytes. Source comparison showed that its prior
256-frame recipe was already below the current runtime RAM allowance before
the new 184-byte responder cache. Full retains its 256-frame shared queue.
Sanitized tests exercise real FIFO wrapping, downloads and held-DM isolation
at 240 slots, plus the production Full override.

Queueing an answer suppresses that request even if later delivery fails. A
tracker needs a fresh timestamp for a later poll, must stay awake for the
private answer, and must authenticate its configured Companion. Plain Yes/No
answers do not contain a poll ID, so delayed replies cannot be precisely
correlated with a newer poll. An unanswered poll means unknown.

## Firmware build qualification

All ten final builds passed flash and internal RAM policy checks. Compact
STM32 and both Full targets were also packaged using the normal
`v1.17.1.9-halo-keymind-cascade-dev` label. Ordinary targets used their normal
PlatformIO recipes. Full nRF52 UF2 boundaries and applicable Bluetooth DFU
handoff checks passed. These are local qualification artifacts, not new
published release assets.

| Companion target | Responder | Flash used / limit (bytes) | Link-time internal RAM available / required (bytes) |
| --- | --- | --- | --- |
| RAK 3x72 USB | Excluded | 228,924 / 229,376 | 24,728 / 17,408 |
| Tiny Relay USB | Excluded | 228,944 / 229,376 | 24,728 / 17,408 |
| Wio-E5 Mini USB | Excluded | 228,888 / 229,376 | 24,704 / 17,408 |
| Wio-E5 USB | Excluded | 229,008 / 229,376 | 24,736 / 17,408 |
| T1000-E Full | Included | 607,480 / 708,608 | 87,064 / 44,864 |
| RAK WisMesh Tag Full | Included | 649,408 / 712,704 | 82,112 / 44,864 |
| T1000-E USB | Included | 426,756 / 708,608 | 48,248 / 38,944 |
| Pico W USB | Included | 640,180 / 1,568,768 | 101,712 / 17,408 |
| Heltec V4 BLE | Included | 1,550,217 / 6,553,600 | 263,448 / 118,560 |
| RAK WisMesh Tag BLE | Included | 459,564 / 712,704 | 46,316 / 44,864 |

The STM32 historical-size table uses byte entries while retaining a 32-bit
input file size; exhaustive tests reject oversized aliases and partial
images. Tiny Relay and Wio-E5 use the size option already used by neighboring
STM32 recipes. Existing terminal help was combined into fewer writes while
retaining its text, order and 256-byte maximum per write. Supported images
also show the new lost-reply commands. Contacts, channels and filesystem
boundaries are unchanged on those compact boards.
