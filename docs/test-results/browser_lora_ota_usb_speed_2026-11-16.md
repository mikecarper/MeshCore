# Browser LoRa OTA USB speed test

On 16 November 2026, a W12 Companion connected by native Web Serial served a
35,184-byte, 18-block synthetic nRF52 delta to a RAK3401 over a direct LoRa
link. Both radios used 908.5 MHz, 500 kHz bandwidth, SF5, CR5, and a
128-symbol preamble. The patch was validated against the installed image,
staged only, then canceled; it was not installed.

| Browser source queue | Target download timer | Outgoing frames | Concurrent queued duplicate requests |
| --- | ---: | ---: | ---: |
| Baseline | 65 s | 223 | 0 |
| Exact-request coalescing | 74 s | 223 | 0 |

Both runs sent 37,021 radio-message bytes. The measurements show no speedup
on this clean link; the difference in elapsed time is normal run-to-run
variation and does not establish a regression or improvement. The baseline
target rate was about 541 bytes/s of staged update payload. Browser transfer
initiation through completion took 101 seconds in that run, including
discovery but excluding preparation.

In a separate simulated retry storm using the browser's actual packet
generator, nine identical requests queued during an active burst resulted in
130 data/proof frames with the original queue and 26 with coalescing. The test
also confirmed that a queued follow-up repaired a deliberately lost fragment
and that a later selective retry was still served. This result demonstrates
the conditional benefit when exact duplicate requests accumulate; it does not
establish that they occurred in another transfer.

After both physical runs, the staged update was cleared, the installed target
firmware remained unchanged, and both radios' temporary settings and OTA hop
settings were restored. Other boards, radio profiles, mesh traffic, and packet
loss may produce different results.
