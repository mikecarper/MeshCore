<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_v4_rxps_half_ms_margin_2026-11-05/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# V4 RXPS minimum plus 0.5 ms margin - 2026-11-05

The common timing rule now adds 500 us above each supported radio's fastest
nonzero timer setting, rounding upward to whole device ticks and including the
actual driver transition allowance. The tested V4 resolves to 3116 us and
33 sleep ticks. Ordinary RXPS levels and unsupported pulse backends are unchanged.

Firmware source: `fb8915f665f86a9bf5a684bd3958cb903482dace`.
Reference source: `d664d89df66bdacc1319900a2e2d152fa31b9dc9`.
The observer build and 65 focused host test groups passed.

Four V4.3 receivers formed two fixed Keymind/reference pairs. The filtered V4.2
was excluded. Each pair received the same 120 source transmissions in three
40-packet blocks. Shared settings were 904.525 MHz, BW500, SF12, CR4/5,
preamble 16 and FEM on. Keymind candidates used CPU 160 MHz, SPI 8 MHz and
normal buffering; reference CPU/SPI settings were not queried or controlled.
The XIAO ESP32-S3 source read
back requested TX -9 dBm. Full RXPS and the independent legacy reset timer
were off; primary refresh ran once per 60 seconds only in the middle block.

| Pair / firmware | OFF before | Refresh 60 s | OFF after | Total |
| --- | ---: | ---: | ---: | ---: |
| AB Keymind | 37/40 | 40/40 | 40/40 | 117/120 |
| AB reference | 33/40 | 35/40 | 35/40 | 103/120 |
| CD Keymind | 25/40 | 38/40 | 37/40 | 100/120 |
| CD reference | 25/40 | 31/40 | 28/40 | 84/120 |

All 480 receiver windows passed the independent raw-log audit with zero unknown
or late attribution. Both frozen audits and analyses were independently replayed
from the fetched records and matched exactly. In the active refresh blocks,
Keymind decoded 78/80 and the references 66/80; these are paired observations,
not proof that the margin caused a sensitivity improvement.

Three quiet cycles plus 12 new cycles during the paired tests completed with
natural wakes, checked RTC handling and verified RX reentry. No admitted pulse
failed. No forced wake or recovery was needed. Four requests on A were deferred by
idle ownership across quiet and RF testing. Five pulses per pair were counted
within the middle status/timing snapshots; a sixth straddled the ending getter
batch or transition. OFF-after had no additional refresh activity.

Sampled BUSY high was 3041-3082 us. Full software operations were 126050-127523 us,
including about 119563 us of receive listening. These measurements do not
establish calibrated RF downtime or analog AGC state.

Middle-block median packet RSSI/SNR remained different across physical boards:
AB Keymind/reference was -32/-37 dBm and 4.5/3.25 dB; CD was -46/-43 dBm and
2.75/3.25 dB. Cached noise-floor medians were -59/-66 dBm for AB and -59/-91 dBm
for CD. Those display offsets did not predict which receiver decoded more.
This was an uncontrolled antenna comparison, not a calibrated weak-signal study.

A began its first OFF block after three quiet cycles; D began from a fresh
normalized boot. Delivery stayed higher in OFF-after. Gain/history carryover
and reference drift prevent treating the stages as an isolated reversible effect.

Both temporary observer watchdog settings were restored to their original
5 minutes. Source and quiet-helper settings were restored, recovery services
and radio owners were preserved, and a fresh authenticated SSH command over
LoRa passed in 125.926 seconds with no LAN fallback. The owned reservation was
removed and the shared helper's original mode restored. The ordinary working
checkout's existing tracked changes were preserved.

The matching `.safe.json` records the selected metrics, limitations and evidence
hashes without device keys or host credentials.
