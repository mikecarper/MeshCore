<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/lr_sleep_checks_2026-11-12/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# T1000-E and W12 sleep checks

Checked on Mercerwood Pi on 12 November 2026 using the installed applications:

| Board | Radio | Firmware |
| --- | --- | --- |
| Seeed Tracker T1000-E | LR1110 | `v1.18.0-dev-db54a6ad`, built 7 November |
| Meshnology W12 | LR2021 | `v1.18.0.2-halo-keymind-cascade-dev-feb7c43d`, built 8 November |

These chips do not use the SX1262-specific `0x08B5` workaround. No SX1262
register writes were added to their drivers. Their native gain-restoration
and sleep commands remain separate.

## Observations

Both radios stayed on their original 910.525 MHz, BW62.5, SF7, CR5 channel,
with boosted RX gain and CPU power saving on. No deliberate RF transmission,
firmware flash or recovery-service interruption was performed.

| Check | T1000-E | W12 |
| --- | --- | --- |
| New AGC, 16-second setting, 70-second observation | 3 successful refreshes; 17 packets received | 1 successful refresh in its active multi-SF mode; 17 packets received |
| AGC failure counter | 0 before and after | 0 before and after |
| Ordinary RXPS, 30 seconds after `effective=armed` | Level 8 / preamble 16; 7 packets received | Level 6 / preamble 16; 7 packets received |
| RXPS arming failure counter | Pre-existing value 1, unchanged | 0, unchanged |
| Soft/hard watchdog recoveries | 0/0, unchanged | 0/0, unchanged |

W12 multi-SF reception was temporarily disabled for the ordinary RXPS check;
its normal multi-SF mode holds RXPS in continuous receive. It was restored to
`on 5-8 radio1; single active`. T1000-E's AGC setting was restored to 0 and
W12's to 16. Original radio settings, RXPS levels/timings and gain settings
were verified afterward.

These are sleep/reception observations using ambient traffic, not a controlled
packet-loss comparison, RF blocker test or direct measurement of analog gain.
The gain setting readbacks report the configured setting. They do not prove
that either chip is immune to every gain-related hardware fault.

Private evidence: `/home/mikec/hwtest/lr-sleep-check-20261112` on the Pi.
