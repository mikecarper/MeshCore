<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/room_services/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Room history, moderation, and information board

Room Server firmware keeps one room per radio. Existing app login, chat,
catch-up, and Access Control formats remain supported. The information board
and local Wi-Fi chat use the browser page below; the stock mobile app does not
have an information-board screen.

## Optional persistent chat history

History uses RAM by default. From a local console or authenticated remote
administrator session:

~~~text
get room.history
set room.history on
set room.history off
room.history.clear
~~~

Enabling persistence first saves the current RAM ring. The newest **32 posts**
then survive reboot. Disabling retains the archive and stops disk writes; the
next enable replaces it with the current RAM ring. Clearing removes disk and
RAM posts, leaving the topic and information board alone.

Accepted posts append 192-byte records. At 64 records the journal compacts to
the newest 32. Its maximum size is 12,300 bytes; compaction temporarily also
needs a verified snapshot of about 6.2 KB. This is bounded recent history,
and its filesystem records are not encrypted at rest.

A post is saved before the server sends its ACK or advances the live ring,
retry cache, or sender replay timestamp. Full storage and unsuccessful writes
retain the previous chat and withhold the ACK. A recoverable incomplete or
corrupt final append keeps its verified earlier records. Corruption before
later complete records preserves the entire journal and blocks replacement.
Unreadable, unsupported, or corrupt primary metadata is also preserved until
storage is repaired.

A reboot after storage completes but before the sender receives the ACK can
recover a valid post without confirming it to the sender. Exactly-once delivery
across that crash boundary is not guaranteed.

Catch-up timestamps stay increasing across restored history and backward clock
corrections without setting the radio's RTC from archived posts. New posts use
a six-second monotonic delivery delay; restored posts can be delivered
immediately. Set the clock correctly for useful displayed dates.

The Companion also withholds a room-message ACK when its receive queue cannot
retain the message. Rejected delivery does not advance catch-up, schedule
receipt persistence, or emit a new-message notification.

## Saved ACL roles and moderation

An operator's nonzero `setperm` assignment survives reboot and is protected
from automatic room-client eviction. Assigned read-only and read/write users
appear in Access Control alongside administrators. Automatic guest-password
read/write sessions remain transient; password-created administrators remain
retained. The existing permission byte is unchanged.

ACL updates use the existing delayed settings save. Allow at least five seconds
after `setperm` before removing power so the assignment reaches storage.

~~~text
setperm <complete-64-hex-public-key> 1
setperm <complete-64-hex-public-key> 2
setperm <complete-64-hex-public-key> 3
setperm <complete-64-hex-public-key> 0
room.ban <complete-64-hex-public-key>
room.unban <complete-64-hex-public-key>
get room.bans
get room.bans 2
set room.post.rate 12
set room.poll.rate 60
get room.post.rate
get room.poll.rate
~~~

Roles are `1` read-only, `2` read/write, and `3` administrator; `0` removes
the assignment. Guest-password login cannot demote an explicit administrator
or upgrade an explicit read-only user. Empty passwords are not enabled
credentials; public passwordless read-only login still requires its existing
setting.

The persistent ban list accepts up to 32 complete identities, never names or
prefixes. It excludes new logins and existing sessions, including posts,
requests, path updates, ACKs, and pushes. It leaves existing posts and ACL
assignments intact. Use another authorized administrator or a local console
to undo a ban on your own identity.

Rates are per identity per shared monotonic minute. `0` disables a limit
(the default); the maximum is 65,535. The post limit covers new chat posts.
The poll limit covers room requests, including keep-alive, status, telemetry,
ACL and board reads, and authenticated browser operations.
Login does not reset counts. Exact accepted retries are not charged again.
Old keep-alive retries cannot rewind catch-up or cancel a newer pending push.
Rejected posts do not enter the retry cache.

Changing to a different identity creates a different user. These controls
complement forwarding and airtime policies.

## Your own return routes

Room readers, writers and administrators can use a private CLI request to
inspect or change the room's route back to their own radio:

~~~text
get outpath
get outpath path
set outpath A1B2C3,D4E5F6
set outpath path
set outpath direct
set outpath clear
set outpath flood
get altpath
set altpath 71CE82,BA09F0
set altpath direct
set altpath clear
~~~

Each hop is 2, 4 or 6 hexadecimal characters; all hops in a path use the same
width. `direct` means zero repeaters. `clear` enables normal discovery again.
`outpath flood` forces flood replies until you change that selection. The
first command word is case-insensitive, and an optional Companion CLI prefix
is preserved in the reply.

These commands always affect the authenticated requesting identity. They
cannot select another user's public key, change room settings, grant roles,
read passwords or run other administrative commands. Active public read-only
sessions can manage their own routes too. Bans still block access, and management
roles alone do not grant room-user command access.

Commands require encrypted CLI message framing. Typing them as ordinary room
chat posts sends chat text instead. Use a CLI-capable client; this feature does
not grant administrator status or add a non-admin command screen to the mobile
app. No new protocol message type is required.

`get outpath path` reports the reciprocal route observed after the most recent
flood login. It can return `> path pending` during its one-minute observation
window. The read does not change the selected route; ordinary room discovery
still learns a live route automatically. A route explicitly selected while
waiting is protected from the delayed login PATH. `set outpath path` selects
the observed route, or leaves the selection unchanged if none is available.

When the primary and alternate routes are valid and different, chat, topic
updates and CLI/data replies send one encrypted copy along each route. The
primary owns ACK tracking, catch-up and retries; the alternate has no separate
retry loop. Identical routes send one copy. A missing packet for the alternate
does not prevent primary delivery. A flood primary does not send an alternate
copy; `altpath flood` also disables that secondary copy, so prefer
`set altpath clear` when removing it. Keep-alive and post ACKs retain their
existing primary-route behavior.

Routes are saved for already-retained ACL entries, including explicit readers
and writers and password-created administrators. Ordinary guest-password and
public-reader sessions keep route selections only for the current session;
changing a route never makes their permissions permanent. Failed saves restore
the previous route and report an error. Repeating an unchanged selection or an
exact request retry does not cause another settings write.

## Choose where chat catch-up starts

Room readers, writers and administrators can skip older unread messages for
their own radio using the same encrypted CLI request framing as route commands:

~~~text
get room.catchup
room.catchup keep 20
room.catchup keep 0
room.catchup before 2026-10-10
room.catchup before 2026-10-10T18:30:00Z
room.catchup before 1791655200
~~~

`keep 20` leaves the newest 20 unread retained messages to deliver and skips
the older backlog. `keep 0` skips all currently retained unread chat. Own posts
do not count toward unread messages. A date skips unread messages strictly
older than that cutoff; date-only values mean midnight UTC. A numeric cutoff
is a Unix timestamp in seconds. A count larger than the backlog is a no-op.

Skipping advances only the requesting user's delivery cursor. It never deletes
room history, rewinds a cursor or suppresses another user's delivery. A future
date advances only through actual retained messages; new messages remain
deliverable. Pending delivery and retries for a skipped post are cancelled;
an outstanding topic remains pending. Delayed ACKs, routine keep-alives and
normal reconnects cannot restore the skipped backlog. A copy already queued
or transmitted can still arrive; skipping stops subsequent catch-up delivery.

Saved ACL users persist an advanced catch-up cursor through the existing
filtered contacts store. Failed saves restore delivery state and report an
error. Transient sessions retain changes only for the current session. History
retention remains 32 posts; this command cannot recover older discarded chat.

## Administrator controls for any radio user

From a local console or authenticated administrator CLI session, use a user's
complete 64-character public key to edit either route or their catch-up position:

~~~text
room.user <complete-64-hex-public-key> outpath A1B2C3,D4E5F6
room.user <complete-64-hex-public-key> altpath 71CE82,BA09F0
room.user <complete-64-hex-public-key> altpath clear
room.user <complete-64-hex-public-key> keep 20
room.user <complete-64-hex-public-key> before 2026-10-10T18:30:00Z
~~~

Names and shortened keys cannot select a user. Administrators can change any
existing radio user's routes without changing that user's role. Route and
catch-up persistence follows the same rules as the user's own commands.
The browser dashboard below provides activity, delivery and route details for
all radio users; these views require administrator credentials.

## Local Wi-Fi chat on ESP32

ESP32 Room Server images with WebConfig have an offline browser room page.
It is **disabled by default** and uses the existing WebConfig listener:

~~~text
set room.web on
start webconfig
~~~

Open `http://<reported-radio-IP>/room`. Without a usable router, use
`start webconfig ap`, connect the phone to its access point, and append
`/room` to the reported address. Existing setup AP time limits and Wi-Fi/OTA
ownership rules apply. Starting OTA shuts down this listener. Compact images
and boards without WebConfig retain LoRa chat and CLI access.

Enter the guest or administrator room password, or leave it empty for
explicitly enabled public read-only access. Readers cannot post or edit
notices; only administrators can publish or delete them. Each operation
rechecks credentials and bans. Use private Wi-Fi: the local page uses HTTP,
and network access alone does not grant administrator permission.

The browser generates a secret token and its own moderation identity. It
does not import a radio private key or inherit a claimed radio key's ACL role.
The Access and history panel shows the complete browser identity for banning.
Posts share the LoRa chat history and include the display name; combined name
and text must fit 151 UTF-8 bytes.

Passwords stay in page memory. The token, sequence and display name are saved
locally; use one active room tab per browser identity. Interrupted submissions
retry the same sequence and body, then poll the owned result. Retry floors
are bounded to twenty minutes and the current boot. The page stops retrying
after twenty seconds and preserves an unconfirmed draft. After a restart or
uncertain result, refresh and check before submitting again.

Authentication, storage and radio work run on the mesh loop. HTTP callbacks
only admit a bounded request and return its owned result. No arbitrary CLI
operation is exposed. Disable room access with `set room.web off`; stop the
portal with `stop webconfig`.

### Wi-Fi room administration

Connect to `/room` with the room administrator password and open **Room
administration**, or visit `http://<reported-radio-IP>/room#admin-panel`.
This management area offers:

- Radio users in pages of two, with saved roles, time since activity, unread
  retained posts, current delivery and failure counts.
- A selected user's primary, alternate and observed routes, catch-up cursor,
  pending chat or topic, and remaining ACK wait. **Heard this boot** records
  authenticated activity; it does not prove that a radio is currently reachable.
- Editing primary and alternate routes, keeping the newest N unread messages,
  or skipping chat older than a chosen date. Browser date inputs use local time
  and send the corresponding Unix timestamp.
- Assigning or removing saved access, blocking and unblocking full identities,
  editing the topic, enabling persistent history, and changing post/poll limits.
- Optional refresh every 15 seconds. Unsaved route edits remain in place while
  live status refreshes.

The dashboard rechecks administrator credentials for each operation and
rejects a write from an earlier boot. Route, catch-up, access, topic and history
edits include the loaded value; a changed value requires a refresh instead of
silently overwriting someone else's change. Each setting saves independently.
Access changes use the existing delayed ACL save; the page reminds you to leave
power on for at least five seconds. Removing a saved assignment does not ban
guest-password access; use **Block** to deny that identity altogether.

Readers and writers cannot retrieve these radio-user activity/delivery views
or management operations. Losing administrator access clears cached management
data from the page. User names, messages and routes are rendered as text.

**Choose chat shown in this browser** filters the local display to the newest
N retained messages or a chosen start date. It changes only the browser view;
it does not move a radio user's cursor or delete posts. The administrator's
selected-user catch-up controls do change that radio user's delivery position.

## Information board

Eight persistent article slots have titles up to 63 UTF-8 bytes and bodies up
to 2,048 bytes. Index revisions and article versions survive reboot. Bodies
use two bounded file banks per slot, streamed verification, and atomic index
publication.

Available filesystem space determines how much fits. Small internal nRF52
filesystems cannot hold every article at maximum size alongside persistent
history and settings. Leave space for the history snapshot and article/index
replacement; a full filesystem rejects the change and retains existing data.

In `/room`, refresh the notice index and select an article to download.
Unselected bodies are not pushed or downloaded. Chunks are at most 128 bytes;
completed chunks remain available within the page after a lost connection.
A new article version starts a new transfer. The administrator editor checks
the version of the loaded article before saving to prevent lost edits.

Small notices also support administrator/local CLI management:

~~~text
room.board.put 1 Trail conditions|Bridge closed until Saturday.
get room.board
get room.board 1
get room.board.read 1 <version> 0
room.board.del 1 <version>
~~~

Index arguments are zero-based entry cursors; follow the returned `next=`.
Long titles can reduce a reply to one entry. Put uses `TITLE|BODY` and still
obeys the command transport's total length limit; use the browser for a full
2 KB article. Read replies contain bounded Base64 and ID, version, offset,
total length and byte count. Continue at offset plus count. Deletion requires
a current nonzero version; reusing a deleted slot assigns a newer version.

### LoRa board read protocol

Logged-in readers, including read-only guests, can use encrypted
`PAYLOAD_TYPE_REQ` requests to the existing single room. The usual four-byte
tag precedes these payloads and is echoed before responses. Multi-byte
integers are little-endian.

| Operation | Payload after tag |
| --- | --- |
| Index | `0a 00` + expected revision `u32` + entry cursor `u8` |
| Article | `0a 01` + article ID `u8` + expected version `u32` + offset `u16` |

First index page: revision `0`, cursor `0`. Subsequent pages require the
returned revision. Response: subtype, operation, status, revision `u32`,
total count, start cursor, next cursor, then up to two complete entries.
Each entry is ID `u8`, version `u32`, body length `u16`, title length `u8`
and title bytes. Continue while the next cursor is less than the total count;
the index is complete when the next cursor equals the total count. An empty
board returns total count `0` and next cursor `0`.

Article response: subtype, operation, status, ID `u8`, version `u32`,
offset `u16`, total body length `u16`, chunk length `u8`, then raw bytes.
The route's encrypted reply capacity may reduce the chunk. Continue at offset
plus length; offset equal to total returns successful EOF. Never combine
different versions.

Statuses: `0` success, `1` invalid, `2` storage unavailable, `3` write
failure, `4` not found, `5` stale version. A capacity too small for a full
header may return only subtype, operation, and invalid status. On stale
revision/version, reload the index and restart the affected transfer.

## Regression coverage

Production handlers and stores are tested across ESP32, nRF52, STM32, Pico
and generic filesystem APIs, including full storage, corruption, interrupted
publication, queue rejection, ACL reboot/eviction, quotas, clock correction
and allocation failure. ARM checks verify the new counters fit the existing
client union.

The pinned official app JavaScript consumes real firmware room frames and
login responses. Chromium executes the bundled browser page with real backend
response fixtures, testing untrusted text, lost replies, draft preservation,
selective reads, resume and UTF-8 limits. These browser fixtures do not claim
a physical LoRa/Wi-Fi transfer.
