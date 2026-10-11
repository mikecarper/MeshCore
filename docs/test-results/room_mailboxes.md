<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/room_mailboxes/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Personal room mailbox validation

Validated on 2026-10-10 against the source changes in this commit. These checks
cover the room-side implementation, not a sleeping tracker client or a live
LoRa/Wi-Fi transfer. No lab radio was flashed for these mailbox checks.

## Behavior exercised

- One public room, separate personal mailboxes, and full authenticated owner
  identities for policy, reads, receipt confirmation and deletion.
- Closed/public/private policies, eight selected senders, reader/writer roles,
  caller and recipient bans, and permission revocation before cached replies.
- Mailbox-only suppression of public posts, topic delivery, active retries and
  public unread counts. Corrupt saved policy blocks automatic chat.
- A later dog-tracker check-in retrieving `dog lost`, resumable reads, no
  implicit acknowledgement, and an explicit durable receipt. Message text
  never executes administrative commands.
- Up to 32 owners, 16 globally queued messages, four per owner, 512 UTF-8 bytes
  per body and bounded retry receipts. Full queues preserve earlier mail.
- Exact retry, conflicting nonce reuse, retry after receipt/reboot, owner-only
  delete and administrator purge. Very old retries are not guaranteed unique
  after their retired receipt is evicted.
- Atomic bank/index publication, every tested interrupted-write boundary,
  corruption, short writes, failed receipt saves and preserved recovery data.
- AES zero-padded LoRa framing, rejection of nonzero trailing data, bounded
  route replies and rejection of oversized commands before mutation.
- Browser identity isolation, stale revision-zero creation, snapshot paging,
  delayed responses after disconnect, drafts, safe text rendering, narrow
  screens and administrator provisioning by full radio address.

## Automated checks

The mailbox store passed 3,978 scenarios on each of five filesystem API
variants (19,890 total), using AddressSanitizer, UndefinedBehaviorSanitizer and
OpenSSL SHA-256. The wire/CLI protocol passed 1,393 scenarios per variant
(6,965 total), using the firmware's actual Crypto SHA-256 implementation and
the same sanitizers. Adafruit file mocks reject default construction, matching
the actual nRF52 library.

Production room transport tests passed 18 cases. Actual JSON handlers passed
seven existing base, six existing administrator and six new mailbox groups
with real ArduinoJson and stores. The delivery scheduler, room login, receive
admission, post permissions, HTTP ownership and stock app compatibility checks
passed after updating their production-code adapters.

Chromium passed 17 existing room browser tests and nine mailbox tests, including
new radio mailbox provisioning without downloading another owner's body.
These checks are included in the unit-test workflow.

## Firmware builds

The following Room Server builds passed the normal flash and runtime RAM gates:

- `heltec_v4_room_server`
- `RAK_4631_room_server`
- `t1000e_room_server`
- `RAK_WisMesh_Tag_room_server`
- `PicoW_room_server`

The first nRF52 build exposed a non-default-constructible Adafruit file handle;
the implementation and fixtures were corrected before final builds passed.

## Stack review

The actual T1000-E ARM ELF retains the existing 8 KiB loop stack allocation.
The largest mailbox store frame is 1,320 bytes. Streamed policy reads avoid
loading the full receipt table in the delivery scheduler; write and readback
verification run sequentially rather than stacking both operations.

A representative LoRa CLI send chain has 5,632 bytes of visible frames; the
binary request path has 4,352. These estimates exclude deeper filesystem,
library, virtual-call and task overhead, and are not proven worst-case bounds.
They do not replace a live long-duration stack-watermark test.
