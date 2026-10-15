# LoRa OTA tools

`lora_ota.py` is the existing Companion-based update runner. The separate
`mota_kiss_direct.py` tool serves one verified `.mota` file through a **full KISS
modem** on logical radio port `0` or `1`. It does not use the Companion raw-packet
command or the Ethernet-only KISS endpoint. See the [mOTA tooling](../mota/README.md)
for container creation and the [OTA protocol](../../docs/ota_protocol.md) for the
wire format.

## KISS direct source

The default invocation is **read-only probing; no RF transmission**. Serving
requires explicit `--serve`, an exact USB serial, a logical port, and an expected
frequency. An optional `--expect-public-key` adds the modem's exact 64-hex-character
public identity check. Use exclusive access to the modem; stop other serial users
first.

The tool never tunes either radio, changes TX power, or clears temporary profiles.
Configure both ends beforehand. Radio port `1` must already be RxTx with the
MeshCore protocol, not Ethernet or Meshtext. Active temporary radio2 leases are
rejected, including when their frequency matches the saved setting. Identity and
frequency checks precede optional pacing changes; the selected profile is checked
again before each RF frame. Unknown required getters fail closed. Production
readback is not an atomic RF lock: prevent other controllers from changing the
modem while serving.

From the repository root, install `pyserial`, `cryptography`, and the pinned build
compressor dependencies (`python -m pip install -r requirements-build.txt`). For
example, **after configuring the modem for the lab profile**:

```text
python tools/lora_ota/mota_kiss_direct.py --port COM6 --expect-serial 0001 --logical-port 1 --expected-frequency 909.5 --require-lab-lock
```

To explicitly enable request-driven serving of one file for five minutes:

```text
python tools/lora_ota/mota_kiss_direct.py --port COM6 --expect-serial 0001 --logical-port 1 --expected-frequency 909.5 --require-lab-lock --serve --mota firmware.mota --zero-pacing --duration 300
```

Replace the port, exact USB serial and file with the intended device and package.
`--zero-pacing` explicitly sets KISS TXDELAY=0, PERSIST=255 and SLOTTIME=1 **only
after preflight succeeds**. Omit it to retain existing modem pacing. Add
`--advertise` only if one initial discovery advert is wanted; otherwise the source
only replies to receiver requests. The source requires full KISS protocol 2+ and
successful `F8 TX_DONE` notifications.

On the receiving node, open a matching temporary MeshCore radio window and set
`ota config hops 0` for direct negotiation. The source modem uses its configured
KISS profile; it does not need a temporary lease. With automatic fetching off,
run `ota ls` on the receiver **while the source is serving** to request its
catalogue, then `ota pull <eight-digit-ID> flash`. Re-run `ota status` to confirm
the verified download is ready before deliberately requesting installation.
Keep `ota config autoinstall off` during tests. A seeder-only Companion cannot
stage firmware to its own flash; use an OTA receiver build or its supported
folder destination instead.

### Using `tempradio2` on the receiver

The KISS source may use its saved radio2 configuration on logical port `1`, while
the receiving repeater uses an actual `tempradio2` lease. These are independent
requirements: the source's temporary-lease rejection does not prohibit the
receiver's temporary window.

For the V3-to-V4 lab setup, leave radio1 unchanged and configure the infrastructure
receiver as follows before starting discovery:

```text
ota config autofetch off
ota config autoinstall off
ota config hops 0
set tx.reply auto
set radio2.cross auto
set tempradio2 909.5,500,5,5,rxtx,30,256
get tempradio2
get radio2.status
```

Wait for the temporary profile and timing self-test to become active, then use
`ota ls` and `ota pull` while the matching source is serving. `tx.reply auto`
selects the temporary profile for ordinary discovery replies; infrastructure's
explicit `both` policy can otherwise request a copy on radio1 too. Direct transfer
requests remain bound to the negotiated physical profile.

The explicit 256-symbol preamble above is a tested lab value, **not a universal
minimum**. When radio1 remains on slow USA settings, the receiver must spend time
listening there; fast retuning alone does not make a 32-symbol radio2 preamble
sufficient. Check both ends' timing/recommended-preamble readbacks. Lab images
must retain the immutable 909.5-MHz TX guard so the primary USA profile stays
receive-only. Do not change or end the temporary profile during a transfer;
expiry/replacement fails the bound session closed rather than moving it to
radio1.

### Direct capacity and fallback

Negotiated, zero-hop direct OTA uses `F2 00` followed by the OTA message: **255 RF
bytes maximum, with up to 240 bytes of DATA** after OTA framing and stream identity.
KISS escaping exists only on the host cable. No Ethernet header, forwarding path
or enlarged normal MeshCore packet is added. A raw 2 KiB block needs nine DATA
frames instead of twelve normal v2 frames; a 1 KiB block needs five instead of six.

Discovery remains ordinary MeshCore OTA. A capable receiver negotiates direct
framing; older or routed receivers keep normal framing (171-byte v2 or 160-byte
legacy DATA). This source answers both; it never forwards the new direct frames.
The receiver owns negotiation, timeout fallback and missing-fragment requests.
An unanswered initial direct probe falls back immediately at its adaptive
deadline. Once direct DATA has confirmed support, one entirely missed later
flight gets a same-mode sparse retry before falling back. A locally rejected
request does not consume that retry; valid direct DATA resets it.

Before opening the modem, the source verifies container structure, Merkle data,
the full-image hash where applicable, and any embedded signature, then caches per-block raw DEFLATE using
**Google Zopfli, 1,000 iterations**. Compression is used only when smaller than the
raw block. Encoded stream identifiers and Merkle proofs retain block integrity;
the receiver still enforces its normal target, approval and installation checks.
A delta's reconstructed final-image hash must be checked by the receiver against
its installed base; this tool does not apply the delta on the host.
Container verification is not a replacement for the receiver's trusted-signer
policy.

### Completion and adaptive pacing

Only one DATA transmission is outstanding. The next RF frame waits for actual
`F8 TX_DONE`, **not a queue-acceptance ACK**. TX_DONE confirms the local radio
completed its transmission, not that the receiver accepted, verified or installed
it. A timeout or failed TX ends this source attempt without hidden RF retries;
receiver request masks provide resumable missing-fragment recovery.

Pace starts at 1.0. Sparse valid request masks reduce it to 0.5 and then, on loss
in a distinct block, 0.25; duplicate requests do not compound the reduction.
Sixteen forward clean blocks recover 0.1. `--minimum-pace` selects a floor of
`0.25`, `0.5` or `1`. Only a pace below 1 adds airtime-scaled quiet gaps. Legacy
proof replies also leave a request-turnaround gap; negotiated direct trains may
send their proof immediately after the last completed DATA transmission. The final
JSON reports actual RF bytes, completed versus attempted TX frames, retries,
effective pace, elapsed time and explicitly estimated airtime.

### Lab safety

All lab transmissions use **909.5 MHz, 500 kHz, SF5, CR5**. Always pass
`--require-lab-lock` during hardware tests: it requires the compiled 909.5 MHz TX
lock and verifies that complete PHY tuple. Keep any primary USA profile RX-only;
do not transmit tests on it. This flag requires the lab-only read-only guard
getter; omit it only for deliberate production use with the explicitly selected
legal frequency and matching radio configuration.

Host-only regression tests, without opening USB or transmitting:

```text
python test/test_mota_kiss_direct.py -q
```

### Measured qualification (2026-10-15)

#### Primary temporary radio (KISS port 0)

A Heltec V3 full KISS source sent a 64 KiB transport-only container to a Heltec V4
repeater on the MercerWoodMesh Pi, using 909.5 MHz / 500 kHz / SF5 / CR5 at 0 dBm.
These runs used the receiving node's primary `tempradio`; radio1 was not also
listening on USA. All 32 2-KiB blocks reached Merkle-verified completion in each run:

| Transfer | DATA frames | Elapsed | Verified payload rate |
|---|---:|---:|---:|
| Normal v2, after direct-probe fallback | 384 | 47.1 s | 11.1 kbps |
| Direct KISS, raw | 288 | 34.3 s | 15.3 kbps |
| Direct KISS, Zopfli-1000 | 248 | 30.6 s | 17.1 kbps |

The raw direct run transmitted 256 full 255-byte RF frames and needed no DATA
retransmissions. A separate compressed run deliberately omitted one DATA frame;
the receiver requested the missing fragment, verified all blocks, and the sender
reduced its pace to 0.5 before recovering to 0.6. The source also avoids a former
50-ms serial read-fill wait on every tiny modem response.

These are application goodput figures including host control/status polling,
not PHY rates; the normal-mode run includes the initial fallback timeout.
Compression gains depend on the image. The fixture contained the first 64 KiB
of a real application, **not a complete installable firmware**: it was staged and
verified, then cancelled, never installed. Final V4 Full repeater, RAK3401 reduced
OTA and RAK4631 Full Companion builds passed their build/RAM guards; nRF52 was
compiled and host-tested, not physically transferred in this qualification.

#### Secondary temporary radio (KISS port 1)

The same V3 source and V4 receiver also passed actual `tempradio2` testing with
both primary radios left on USA 910.525 MHz / 62.5 kHz / SF7 / CR5, receive-only
under the immutable lab TX guard. Radio2 used 909.5 MHz / 500 kHz / SF5 / CR5,
0 dBm and preamble 256; the receiver used `tx.reply auto` and `radio2.cross auto`.
Each run staged and Merkle-verified the same 64 KiB fixture, without installation:

| Transfer | Elapsed | Verified payload rate | Radio1 RX / TX during run |
|---|---:|---:|---:|
| Direct raw, one entire later response flight deliberately omitted | 78.3 s | 6.7 kbps | 21 / 0 |
| Direct Zopfli-1000, no injected loss | 72.6 s | 7.2 kbps | 33 / 0 |
| Normal v2, initial direct probe deliberately ignored | 114.2 s | 4.6 kbps | 40 / 0 |

The missed-flight test dropped exactly 18 DATA and two PROOF frames after five
committed blocks. Before the recovery fix, the identical omission caused a
240-to-171 downgrade; after the fix, all 297 transmitted DATA frames stayed in
direct geometry and all blocks verified. Ambient packet loss and pacing varied,
so individual timings are observations, not a repeatable throughput guarantee.
All tests recorded zero primary-profile TX increase. These figures include
primary-channel listening, recovery, adaptive quiet gaps and status polling;
they must not be substituted for the port-0-only figures above. The V4 Full
repeater and RAK3401 reduced-sensor OTA rebuilds passed, as did all 163 native
OTA core tests, including sanitizers, confirmed-empty retry admission, clock
rollover and session expiry. This radio2 hardware qualification was ESP32-only;
the nRF52 target was compiled, not physically transferred in this run.
