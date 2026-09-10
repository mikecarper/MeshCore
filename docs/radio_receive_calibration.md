# Radio receive calibration and recovery

Noise calibration collects 64 idle RSSI samples at least 50 ms apart. A quiet
block takes about 3.2 seconds. Both continuous RX and temporary RXPS calibration
windows allow up to 10 seconds; a partial block times out without publishing.
Periodic requests coalesce while a block is active. RXPS resumes after a complete
block, including a held block, or at timeout. Receive and transmit activity are
never interrupted just to finish calibration.

The block estimate is the median, retaining fractional dBm. A published update
uses 25% of the previous floor and 75% of the new median, with a -120 dBm lower
bound. Initial calibration and explicit gain/tuning changes seed a new baseline.
The old published value remains available while collecting. An AGC reset drops
partial samples while retaining a valid baseline for channel checks.

A median more than 15 dB above the established floor is held twice. The third
consecutive complete high block is accepted, allowing recovery after a permanent
ambient rise. A normal or quieter complete block clears the hold. Partial
blocks do not advance it. There is no admission threshold based on the old
floor, so an old low baseline cannot reject every sample from a higher floor.

The median handles minority outliers. If interference occupies most accepted
samples, the median eventually represents that energy; it cannot prove whether
the energy is ambient noise or sustained traffic. The rise hold delays that
change but does not distinguish the two. Busy-channel completion and recovery
times therefore depend on traffic. Longer sampling also spends more time in
continuous RX during calibration than the previous rapid 64-sample average.

CAD has a bounded wait, stops RX duty cycling before scanning, and re-arms the
configured receive mode on success, busy detection, or error. A completed or
currently receiving packet owns the radio and blocks CAD. A packet interrupt
arriving during re-arm is retained. Failed re-arm and a newly arrived packet
both defer transmission.

SX126x wrappers additionally verify the chip's operating mode at most once per
10 seconds while continuous RX is expected. RXPS sleep, busy SPI, pending packets,
and invalid status reads do not count as non-RX evidence. Two confirmed non-RX
observations request soft recovery; a third uses the existing hard recovery path
where supported. An observed healthy RX state clears the sequence. Recovery
preserves tuning, TX power, receive gain, and the operator's power-saving intent.
The existing radio liveness and RXPS watchdogs remain active for other failures.

The status read uses the two-byte GetStatus transaction. The pinned RadioLib
getStatus() helper requests zero copied data bytes, so its return value is not
usable for this mode check. The dedicated read restores the normal SPI command
layout afterward and does not wake a duty-cycling radio to inspect its mode.

Validation lives in `test_noise_floor_estimator`, `test_rx_power_saving`,
`test_radio_receive_contract.py`, and `test_sx126x_receive_mode.py`.
