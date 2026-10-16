<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/radio_receive_calibration/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Radio receive calibration and recovery

Noise calibration collects 64 idle RSSI samples at least 50 ms apart. A quiet
block takes about 3.2 seconds. Both continuous RX and temporary RXPS calibration
windows allow up to 30 seconds; a partial block times out without publishing.
Periodic requests coalesce while a block is active. RXPS resumes after a complete
block, including a held block, or at timeout. Receive and transmit activity are
never interrupted just to finish calibration.

A calibration request waiting for successful RX does not keep the MCU awake.
Repeated failed receive starts retry after 20, 40, 80, 160, 320, 640, and 1280 ms,
then at most once every 2000 ms. Only the repeated idle fallback is paced:
packet/TX completion and explicit retune/CAD restoration still rearm promptly.
Calibration and recovery requests retain the failed-start backoff; successful
RX or a new radio session clears it. A retry blocked by BUSY or an RX owner
waits 2000 ms without issuing another RX command. The Dispatcher includes this
retry deadline in its wake query, and deferred adaptive AGC joins a later RX
retry instead of forcing 50 ms wakeups throughout a persistent fault.

In single-profile RXPS, routine refreshes wait at least 60 seconds after the
last completed or timed-out block before requesting another continuous-RX
window. Both periodic maintenance and passive retry checks use this cooldown;
retries do not restart a partially collected block. With RXPS off, or when the
selected tuple requires continuous-RX fallback, the existing two-second
maintenance tick and passive-probe age check are unchanged. Startup, explicit
gain/tuning changes, AGC resets, and radio recovery still request a fresh block
promptly. Dual-profile scanning retains
its separate refresh policy.

Frequent configured AGC resets can still dominate calibration time: Cascade
repeater builds start with an eight-second AGC interval, and MQTT observers
use 28 seconds. With device power saving on, their adaptive AGC schedule backs
off separately, up to 60 seconds; see [AGC reset settings](../cli_commands.md).
AGC resets still bypass the routine noise-floor cooldown. After
a timed-out startup block with no valid floor, the next routine RXPS attempt
also waits 60 seconds; passive retries retain their existing bounded busy
escape while waiting for a valid baseline.

The longer RXPS cooldown trades a slower response to ambient noise changes for
more time in duty-cycle RX. It does not reduce sample count, shorten settling,
or put the radio to sleep between samples. A large upward change still needs
three complete high blocks to pass the rise hold. No battery-life improvement
has been measured for this scheduling change.

The block estimate is the 12.5th percentile: the average of the eighth and
ninth sorted samples, retaining fractional dBm. A published update uses 25% of
the previous floor and 75% of the new estimate, with a -120 dBm lower bound. Initial calibration and explicit gain/tuning changes seed a new baseline.
The old published value remains available while collecting. An AGC reset drops
partial samples while retaining a valid baseline for channel checks.

An estimate more than 15 dB above the established floor is held twice. The third
consecutive complete high block is accepted, allowing recovery after a permanent
ambient rise. A normal or quieter complete block clears the hold. Partial
blocks do not advance it. There is no admission threshold based on the old
floor, so an old low baseline cannot reject every sample from a higher floor.

Hardware captures with sustained different-SF/BW traffic showed the median and
lower quartile following the interferer. The 12.5th percentile retained the
quiet background in those captures. It needs at least nine background samples
out of 64 to avoid higher-valued traffic entirely. Nearly continuous
interference or enough low outliers can still mislead it. The rise hold delays
large changes but cannot identify their source.

RSSI sampling waits 7 ms on SX1262, SX1268, and LLCC68 builds, or 20 ms on other
families, after every receive re-arm, including CAD and TX completion, to avoid
unsettled frontend readings. The ten-second trial window often failed to
complete during heavy traffic with that settling guard; the
30-second cap allows more idle opportunities. Partial blocks still time out
without publishing. Longer collection spends more time in continuous RX when
RXPS is enabled. A quiet minimum is about 3.2 seconds; real completion time
also depends on packet activity and main-loop scheduling.

On radios with a mode probe, each eligible RSSI sample first checks that the
chip has not silently left RX. Confirmed standby/non-RX discards the partial
block without changing the published floor. Rejected attempts are also spaced
50 ms apart. BUSY and packet ownership block sampling; families without a mode
probe retain their existing RSSI path.

CAD has a bounded wait, stops RX duty cycling before scanning, and re-arms the
configured receive mode on success, busy detection, or error. A completed or
currently receiving packet owns the radio and blocks CAD. A packet interrupt
arriving during re-arm is retained. Failed re-arm and a newly arrived packet
both defer transmission.

SX126x wrappers additionally run a recovery watchdog at most once per
10 seconds while continuous RX is expected. RXPS sleep, busy SPI, pending packets,
and invalid status reads do not count as non-RX evidence. Two confirmed non-RX
observations request soft recovery; a third uses the existing hard recovery path
where supported. An observed healthy RX state clears the sequence. Recovery
preserves tuning, TX power, receive gain, and the operator's power-saving intent.
The existing radio liveness and RXPS watchdogs remain active for other failures.

The radio liveness watchdog measures time without a successfully received
packet. Only a positive `recvRaw()` result restarts its observation window;
TX completion, CAD interrupts, and failed packet reads do not. Reception counts
even if application parsing rejects the packet or the packet pool is full.
The check runs after pending RX is drained and TX completes, before the next
queued transmission, so sustained outbound traffic cannot postpone recovery.

The default silence thresholds are 30 minutes for a soft RX/AGC re-arm and
12 hours for a radio-only hard recovery where supported. MQTT observer
`radio.watchdog` settings can override the soft interval. A successful hard
recovery starts a fresh observation window to limit repeated resets on a quiet
mesh; a failed hard recovery remains escalated and retries after 30 seconds.
Targets with `RADIO_LIVENESS_SOFT_ONLY` repeat soft recovery at the soft interval
because their radio cannot be reset independently. These recoveries do not
reboot the MCU. The repeater's `rx.watchdog` is a separate whole-board reboot
watchdog, enabled by default with a 24-hour sliding deadline since the last
received packet passed MeshCore parsing. Its timer is updated before receive
delays and routing filters; raw LoRa reads rejected by the parser do not count.
MeshCore parsing checks the packet structure, not sender authentication or
decryption of traffic addressed to other nodes. Temporary
radio windows of at least 12 hours use a 12-hour deadline; shorter windows
disable that reboot watchdog until the window ends. The temporary timing
overrides are held in RAM and do not replace saved normal-mode preferences.

The status read uses the two-byte GetStatus transaction. RadioLib 7.8.1 also
reads the response byte directly; the dedicated probe additionally preserves
the existing SPI layout, checks transport errors, and avoids waiting on BUSY.
It does not wake a duty-cycling radio to inspect its mode.

Validation lives in `test_noise_floor_estimator`, `test_rx_power_saving`,
`test_radio_liveness`, `test_radio_receive_contract.py`, and
`test_sx126x_receive_mode.py`, and `test_radio_receive_retry.py`. These host
checks do not establish battery-life or hardware fault-recovery results.
