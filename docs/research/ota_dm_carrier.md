<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/research/ota_dm_carrier/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Direct-message OTA carrier research

This is a design and an unwired codec experiment. It does not enable a new OTA
transport, encrypt a packet, negotiate a session, or make an update work over
direct messages.

Existing OTA messages use `PAYLOAD_TYPE_OTA`. For nonzero OTA hop limits they
are flooded; the administrative route does not constrain the data path. Existing
direct forwarding can carry a packet along a path, but bare OTA does not identify
its final recipient or provide a return path. Generic direct relays also suppress
previously seen packet payloads, which conflicts with identical OTA retries.

## Frame and memory prototype

`OtaDmEnvelope.h` is an isolated experiment, not an agreed wire protocol. It
accepts a caller-supplied 32-byte session authentication key; it does not derive
or authenticate that key. It uses HMAC-SHA256 truncated to 16 bytes over a domain
label, both complete public keys, and each frame. Each frame includes a nonzero
64-bit session ID, a 32-bit transmission sequence, a 32-bit logical message ID,
total length, fragment index and length. A retry needs a fresh sequence while
retaining the logical message ID. The receiver authenticates each frame before
using four bounded reassembly slots, and rejects reused sequences and completed
message IDs within a 64-entry sliding window. Sequence and message IDs must not
wrap; a new authenticated session is required. A session ID must never be reused
with the same session key. Expired incomplete messages can be retransmitted with
new sequence numbers. The prototype keeps state in RAM and therefore cannot by
itself prevent replay after a restart or renegotiation.

The current `createDatagram` admission permits at most 167 plaintext bytes. The
experimental 25-byte header and 16-byte tag leave 126 bytes per fragment, so an
existing OTA payload of at most 184 bytes needs at most two encrypted DM packets.
The four-slot prototype consumes roughly 800 bytes plus hashing and caller
buffers; it has not been qualified on constrained builds or under realistic
radio pacing. It accepts zero padding from Mesh decryption only when the padding
length and every padding byte are valid.

## Work required before use

- Negotiate explicit endpoint support and direction-specific paths over an
  authenticated exchange. Both endpoints must identify the expected full peer
  keys, agree on radio profile, use fresh session IDs, and fail closed on
  unsupported devices; relays need compatible direct-forwarding behavior.
- Use a reviewed, standard nonce-based authenticated-encryption construction
  with separately derived session keys and defined restart/replay behavior. The
  current Mesh DM encryption applies AES independently to blocks and has a
  two-byte transport MAC; the prototype's extra HMAC does not hide repeated
  plaintext patterns and is **not** a replacement for encrypted transport.
- Add bounded outgoing queues and feedback so a split message is considered
  delivered only when its fragments are acknowledged or recovered. Preserve
  OTA request/proof retries, backpressure, routing and radio-profile affinity.
  Prevent replayed or interleaved messages from mixing sessions or recipients.
- Define upgrade and downgrade rules. Legacy OTA nodes cannot consume this
  envelope. Never silently substitute flooding when a direct-message transfer
  was requested; surface incompatible endpoints before transmission.
- Test real key derivation, ciphertext and replay behavior using independent
  implementations, packet loss, resets, queue exhaustion, radio switching,
  constrained-memory builds and multi-hop radios. Assess the effect of extra
  fragments and acknowledgments on airtime and power.

## Separate AEAD experiment

`OtaDmAead.h` is another unwired prototype, not a negotiated format. It uses
the existing MIT-licensed rweather Crypto 0.4.0 `HKDF<SHA256>` and
`ChaChaPoly` implementations. It derives a direction-specific key from a
caller-provided shared secret, an authenticated fresh 128-bit session ID and
the ordered full public keys. A 96-bit nonce is constructed from a per-instance
32-bit counter; a sender refuses to continue after it wraps. Header and full
ordered public keys are authenticated; a 16-byte tag must verify before any
plaintext is returned. A receiver keeps a 64-packet in-memory replay window.
The 33-byte header and 16-byte tag leave 118 bytes per packet, so an existing
184-byte OTA message takes two packets.

`test/test_ota_dm_aead.py` checks the actual rweather library against independent
PyCryptodome HKDF/ChaCha20-Poly1305, byte tampering, wrong directions, replay,
retries and padding. It does not measure target hardware. The caller is
responsible for securely authenticating negotiation and ensuring each session ID
is fresh across all instances and reboots. Creating two sender instances with
the same key, direction and session ID would reuse nonces. Until that lifecycle,
key agreement, reassembly and actual radio transport are designed and reviewed,
this experiment must not be used for updates.
