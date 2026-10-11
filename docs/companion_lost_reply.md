<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/companion_lost_reply/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Companion lost-status auto response

Development Companion builds containing this feature can automatically answer a private `Am I lost?` message with `Yes`
or `No`. You choose the saved answer. The default is `off`, and the firmware
does not infer lost status from GPS, distance, or a missing phone connection.

This is included in nRF52, ESP32 and RP2040 Companion profiles, including
T1000-E and RAK Tag. Compact STM32 Companion images omit it to preserve their
existing application and filesystem boundaries. A custom build can explicitly
enable `MESH_ENABLE_LOST_REPLY=1` when its flash budget permits; the normal
flash and RAM checks still apply.

Configure it through a directly connected local Companion CLI:

~~~text
get lost.reply
set lost.reply no
set lost.reply yes
set lost.reply off
~~~

The setting survives reboot and takes effect immediately. USB, BLE and other
local Companion transports can use framed command `0x42`. Builds with a text
terminal also accept these commands there. LoRa CLI cannot read or change this
local setting. A failed save restores the previous answer and reports an error.
Older saved preference images load the new setting as `off`.

ESP32 Companions with WebConfig also have a **Lost-status auto reply**
selection on the radio configuration page. Choose Off, Reply No or Reply Yes
and save. The control is shown only on Companion firmware with this capability.
The existing mobile app protocol needs no new message type; a native app toggle
is not added by this firmware change.

## Dog tracker example

The owner's Companion stores the tracker as a normal radio contact. Set the
answer to `no` during normal operation. If the dog is lost, set it to `yes`.
When the tracker sends `Am I lost?` privately to that reachable Companion, it
receives the selected answer, even when the owner's phone is disconnected.
The same configured answer applies to all eligible contacts on this Companion.

The opt-in [Sensor dog tracker](sensor_tracker.md) implements periodic fresh
GPS acquisition, low power sleep, and owner check-ins. It uses a tagged encrypted
request and response rather than parsing plain `Yes` or `No` text, so old answers
cannot change a newer check-in. Store the Sensor as a persistent Sensor contact
on the owner's Companion. The same saved `lost.reply` setting answers those
checks; `off` returns Unknown. A fresh accepted GPS position updates the contact
location through normal contact notifications.

Other clients can still use the plain text question. They must stay awake for
the separate private answer; a delivery ACK is not that answer. Plain text
answers have no request ID. The responding Companion must be powered on and
reachable. [Personal room mailboxes](room_services.md#sleeping-tracker-example)
provide store-and-forward mail; automatic mailbox polling is not yet implemented
in the Sensor tracker.

## Matching and traffic limits

Only an authenticated ordinary private message from a known persistent radio
contact is eligible. The contact must be an ordinary Companion/chat contact;
room-server and repeater contacts are excluded. Temporary unknown senders,
public channels, room chat posts and CLI messages do not trigger a response.

The exact phrase `am I lost` is matched without regard to ASCII letter case,
with an optional final question mark and surrounding whitespace. Extra words
or commands do not match. The reply is ordinary encrypted private text, `Yes`
or `No`, and neither answer can trigger another automatic reply.

Automatic responses are limited to one per contact per minute and one globally
per ten seconds. A bounded four-peer cache uses full public keys and the
incoming request timestamp to suppress retries. Its state clears on reboot.
The same retained request is not answered again; a fresh check uses a new
timestamp. An old retry can receive another answer after a newer request
replaces its saved timestamp or its peer slot is evicted. Queueing an answer
records the request even if later delivery fails. An unanswered poll means
unknown, and the tracker should trust replies only from its configured
Companion's full public key.

The responder leaves packet capacity for normal receive/ACK work, uses a free
ACK-tracking entry, and does not replace pending user messages or their
delivery deadlines. Busy packet or ACK capacity, or a failed send, can prevent an answer;
the tracker should retry later. A full offline app inbox does not disable the
responder. Normal message reception and notification behavior remain in place.
