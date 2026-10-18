<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/carrier_wave/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Carrier-wave RF checks

The constrained `wio-e5-mini_repeater` omits Morse and CW tools to fit its
existing application region. Its packet radio and radio2 are unchanged.
Other boards keep their existing carrier-tool support.

## TX-only Morse messages

Morse TX mode keys an unmodulated carrier into dots and dashes. It does not send
LoRa packets or automatically retransmit channel chat or
private messages. **The default is 902.100 MHz at 25 words per minute (WPM).**
Nothing transmits until an explicit `morse send` command is accepted.

| Command | Action |
| --- | --- |
| `morse send TEST DE N0CALL` | Send the text once using the saved Morse frequency and speed. |
| `morse stop` | Stop Morse immediately and restore packet reception. |
| `get morse` | Show idle/active state, frequency, WPM and remaining time. |
| `get morse.freq` | Show the Morse TX frequency in MHz. |
| `set morse.freq 902.100` | Save the separate Morse TX frequency, rounded to 1 kHz. |
| `get morse.wpm` | Show the saved sending speed. |
| `set morse.wpm 12` | Slow transmission for easier hand decoding; supported range is 1–60 WPM. |

The default 25 WPM can be changed; for an easy-to-copy test, set 10–12 WPM.
Standard timing uses one unit for a dot, three for a dash, one between elements,
three between letters and seven between words, with a dot of `1200 / WPM`
milliseconds. Letters are sent case-insensitively. Messages are bounded to
80 ASCII characters, must contain supported Morse characters, and must fit
within 180 seconds at the chosen speed. Supported punctuation is
`. , ? ' ! / ( ) & : ; = + - _ " $ @`; tabs, newlines and non-ASCII text are
rejected. Invalid messages are rejected before
the transmitter is keyed. There is no automatic callsign insertion.

The Morse frequency is independent of `radio`, `tempradio`, `radio2` and
`tempradio2`. Before sending, the firmware rejects a frequency that overlaps
either packet-radio profile, including a guard outside its occupied bandwidth.
It does **not** automatically find another channel or change your mesh settings.
Choose another `morse.freq` if 902.100 MHz overlaps either profile. A frequency
accepted as a saved value must still be supported by the actual radio at send
time. Morse frequency/speed changes are rejected while Morse or CW is active.

There is one physical transceiver: packet reception and transmission pause
throughout the message, including the spaces between dots and dashes. Normal
receive/profile scanning is restored afterward. Use local USB/BLE for immediate
status and stopping; a reply to a remote LoRa CLI command waits until the
message finishes. Changing either packet profile during Morse can abort it to
preserve the frequency-separation safety checks.

An unmodulated carrier has no selectable LoRa bandwidth. This uses native CW
on SX1262/SX1268/LLCC68/STM32WLx and LR1110/LR1121/LR2021 radios, and
zero-deviation FSK CW on SX1276-family radios with reset/recovery support, rather
than pretending the narrowest receive filter sets the transmitted bandwidth.
Morse sidebands depend on keying speed and transmitter edges. This is not a
guarantee of a particular occupied RF bandwidth or freedom from interference.
Unsupported radios reject the command without beginning transmission.
The chips' configured TX ramps use 3400 microseconds on the SX126x-family and
304 microseconds on the LR-family to reduce keying splatter. These are driver
settings, not measured occupied-bandwidth results. Direct FEM gain changes are
blocked while Morse owns the radio.

Frequency and speed are saved in a separate checked `/morse_prefs` file;
identity, contacts and existing radio preferences are unchanged. An active
message is never restored after reboot. TX uses the board's existing `set tx`
power setting and hardware power limits.
Unreadable or corrupt saved Morse settings without a usable backup block
sending rather than silently
transmitting on the default frequency. After storage recovers, verify and save
`morse.freq` (and the desired WPM); a verified save makes sending available again.

Use an appropriate antenna or dummy load, lawful frequency/power and any
required operator identification. The default frequency is a convenience, not
permission to transmit or a claim that the channel is clear. Check the local
band plan and listen first.

## Timed Morse reception and retained captures

Supported radios can temporarily listen for keyed carriers and decode the
power envelope into text. This is a separate manual session, not another
MeshCore packet channel or an authenticated sender. Nothing decoded is
automatically transmitted, relayed or inserted into a channel/DM inbox.

| Command | Action |
| --- | --- |
| `morse listen 1` | Listen for one minute at the saved `morse.freq`, using `morse.wpm` as the timing hint. |
| `morse listen 0.5` | Listen for 30 seconds. Fractions are allowed. |
| `morse listen 5` | Listen for the maximum five minutes, then restore packet reception. |
| `morse stop` | End TX or listening early. A received capture is retained. |
| `get morse` | Show TX/RX state, actual RX filter bandwidth, remaining time and capture length/truncation. |
| `morse stats` | Show retained receiver sample counts, noise/min/max/last power, timing unit, largest sample gap and read failures. |
| `morse read` | Read the first retained capture page. |
| `morse read 128` | Read beginning at character offset 128; use the offset returned by your previous page. |
| `morse clear` | Clear the retained capture when no Morse session is active. |

Listening is bounded to **1–300 seconds** for every role. Keep the first
0.5 seconds quiet while the receiver learns its noise floor; start the sending
radio at least one second after listening begins. Start with matching WPM on
the sender and receiver. Noise, fading, interference and timing mismatch can
produce errors; decoded text has no sender identity or integrity check.

The receiver uses a continuous native FSK receive filter and live received
signal strength (RSSI) to follow the carrier's on/off envelope. It does not
transmit or require a packet preamble, sync word, CRC or direct-data pin:

| Radio family | Current Morse RX filter |
| --- | ---: |
| SX1276-family | 2.6 kHz |
| SX1262/SX1268/STM32WLx, LLCC68 | 4.8 kHz |
| LR1110/LR1121, LR2021 | 4.8 kHz |

Small-flash STM32 production builds disable Morse reception by default
(`MESH_ENABLE_MORSE_RX=0`) because the receiver does not fit alongside their
existing features. Morse TX, frequency/WPM settings and stopping remain
available. Their listen/read/stats/clear commands return an unsupported-RX
error. The STM32 filter above describes an explicitly enabled custom build,
not RX support in those default production images.

`get morse` reports the actual configured bandwidth; this is not a measured
selectivity or decoder-speed guarantee. Unsupported hardware rejects listening
without changing the radio. A board also needs a proven complete LoRa-modem
restoration path before native FSK RX is enabled. Narrow filters require
sufficiently aligned oscillator frequencies. Passive listening may use either packet profile's
frequency because it does not transmit; TX overlap protections still apply.
FEM and saved Morse settings cannot be changed while a session owns the radio.

Morse sessions defer periodic sensor/GPS processing, screen redraws and
background storage work so they do not interrupt envelope timing. Pending
work and its deadlines are retained and resume when the session ends. Buttons,
command connections, watchdogs and battery shutdown checks remain active.
`morse stats` reports `gap` in milliseconds: the largest observed gap between
receiver samples, including unavoidable safety work. Large gaps can limit
reliable speed even when the RF signal is strong.

Repeater, Room, Sensor and Companion roles retain a bounded **4096-character
RAM capture** after their normal packet receiver resumes. A successful new
listening session replaces it; a failed start does not. `morse clear` or a
reboot removes it. If the capture fills, listening stops early and
`truncated=1`/capture-quota status is shown instead of continuing to discard
incoming text. This does not change node keys, ACLs, stored radio
settings or other files.

Capture replies fit one CLI response and begin with a page header such as
`> 0000/0250 next=0125 truncated=0 ` followed by text. Send `morse read 125` to
continue at the returned offset; `next=END` means the last page. Exact page
length depends on the requesting transport's reply capacity, so follow `next`
rather than assuming a fixed page size. Captures remain readable until you
start another listening session or clear them.

A Full Companion's owned buffered USB ASCII terminal or browser terminal shows
decoded progress as `[Morse RX] ...`, paused while USB input is being edited or
an earlier reply is draining. Complete records use that terminal's existing
ordered output queue. Raw UART/HWCDC/TCP terminals use `get morse`/`morse read`
instead of unsolicited progress. Binary Companion USB/BLE frames never receive
raw text.
Companion apps and other clients can request the same capture through their
CLI using `morse read`; it is not presented as an authenticated chat message.
There is no automatic LCD/inbox injection.

One transceiver cannot listen for Morse and MeshCore LoRa simultaneously.
During listening, normal packet reception/transmission is paused. An authorized
remote `morse listen` first queues its reply on the existing packet-radio
settings and starts only after the exact reply copies finish transmitting,
with at least one successful transmission. It does not infer that the remote
client received the reply. Failed queue admission/all-copy failure, permission
revocation, changed Morse settings or a 30-second handoff deadline cancel the
request without starting RX. A briefly busy radio can retry admission for up
to 10 seconds after reply transmission, within that same handoff deadline.
For remote captures, send `morse listen 1`, start the Morse sender after the
queued reply and initial quiet learning interval, wait beyond the selected
minute (plus any admission delay), then request
`get morse` and `morse read` over LoRa. Use a local console to stop early.
Hardware recovery failures hold exclusive ownership until the receiver can
be safely restored; `get morse` reports recovery pending.

Lab images compiled with `MESH_MORSE_HIL=1` additionally provide
`morse trace [offset]`. A page begins `> offset/length t=total n=next x=wrapped`
and contains up to eight `elapsed_ms,rssi_dbm,flags;` records. `n=END` ends the
retained window; `x=1` means earlier entries were overwritten. Offsets address
the oldest of the latest 512 retained records; retrieve after stopping or
check that `t` stays unchanged between pages. Flags are raw key 1, committed
key 2, debounce candidate 4 and noise learning 8. This diagnostic command and
its trace storage are absent from ordinary production builds.

## Continuous carrier checks

`cw` and `cw2` transmit a continuous, unmodulated carrier at the selected
profile's frequency and the configured TX power. They are available in the
shared ASCII CLI on SX1276-family, SX1262-family, and LR1110 builds, including repeaters,
Companions, room servers, and sensors that use that CLI.

| Command | Action |
| --- | --- |
| `cw on` | Use the active primary frequency for 10 seconds. |
| `cw on 2.5` | Use the active primary frequency for 2.5 seconds. |
| `cw2 on 0.75` | Use the active second profile for 0.75 seconds. |
| `cw off` or `cw2 off` | Stop the carrier immediately. Either name stops it. |
| `get cw` or `get cw2` | Show which carrier is active and its remaining seconds. |

`set cw ...` and `set cw2 ...` are also accepted. Durations range from
0.001 to 60 seconds, rounded to milliseconds. The deadline runs in the main
loop, so it is not a precision pulse generator or a hardware cutoff.

`cw2` requires an active `radio2` or `tempradio2` profile in `rxtx` mode.
An `rx` or disabled second profile cannot transmit a carrier. `cw` uses
`tempradio` when that is the current primary profile. There is one physical
transmitter: a carrier uses one profile at a time and ignores the cross-TX
setting. Stop it before selecting the other carrier. Repeating `on` on the
same profile restarts the duration.

Packet transmission, reception, profile scanning, CAD, and receive recovery
checks pause during the carrier. The firmware keeps the MCU awake to service
the deadline, controls the board's external amplifier, and resumes normal
reception and profile scanning afterward. It rejects entry during a packet
or a busy radio. Profile retuning waits until the carrier stops; replacement
or expiry of the active second profile stops its carrier. Nothing about CW
is saved across reboot.

The normal `set tx <dBm>` command also works during CW for meter sweeps. It
keeps the board's power limits and PA calibration, resumes the carrier after
changing power, and does not extend the CW timeout. TX power retains its usual
saved behavior. SX1276 temporarily uses FSK with zero frequency deviation,
then rebuilds the LoRa configuration before resuming reception.

Use the local USB or BLE console for immediate replies and manual stopping.
An on-air CLI reply must wait until the carrier ends because LoRa packets
cannot transmit during the test.

A second radio on the same frequency can detect the carrier by measuring
**live signal strength** (RSSI, short for received signal strength indicator):
how much radio energy the receiver sees right now, even when it cannot decode
a packet. A less-negative reading in dBm means a stronger signal. Compare it
before, during, and after transmission. The normal `stats-radio` command shows
the **last received packet's** RSSI, not this live reading, so it cannot by
itself confirm a continuous carrier. CAD and packet counters are not carrier
detectors either. A rising live reading confirms RF activity, not calibrated
output power, frequency accuracy, antenna match, or SWR. Afterward, exchange
ordinary LoRa packets to verify both RX and TX recovery.

The reproducible bench receiver that provides this live reading is
`tools/hil/cw_observer.cpp`, built with `tools/hil/cw_observer.ini` on a
separate RAK4631. It never transmits on its own.
See the [hardware validation](test-results/carrier_wave_validation.md) for measured results,
reproduction steps, and the tested preamble settings.
