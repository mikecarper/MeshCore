<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/w12_multisf_lab_2026-11-04/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# W12 multi-SF lab checks, 2026-11-04

These are short, close-range checks, not packet-delivery guarantees. A W12 and
one RAK4631 feeder used isolated test frequencies: primary 904.525 MHz at
62.5 kHz and radio2 904.850 MHz at 250 kHz, both CR5. Bursts used varied
intervals. CPU sleep and current were not measured with USB connected.

| Experiment | Preamble | Result |
| --- | --- | --- |
| Single frequency, receive SF5–8, transmit SF7 | 128 symbols | 10/10 received at each SF; an SF7 advert was observed and 5/5 SF8 packets arrived after TX |
| Single frequency, receive SF6–9, transmit SF7 | 128 | 5/5 SF9 packets received |
| Single frequency, receive SF7–10, transmit SF7 | 128 | 5/5 SF10 packets received |
| Sequential PRAM: multi-SF primary, fixed SF7 radio2 | 128 | 5/5 at each primary SF5–8 and 5/5 on radio2 |
| Sequential PRAM: multi-SF primary, fixed SF7 radio2 | 32 | 10/10 primary SF5, 5/5 primary SF8, 10/10 radio2 |
| Sequential PRAM: fixed SF7 primary, multi-SF radio2 | 32 | 5/5 primary SF7, 8/10 radio2 SF5, 5/5 radio2 SF8 |
| Sequential PRAM: fixed SF7 primary, multi-SF radio2 | 128 | 10/10 radio2 SF5 |

The sequential TX experiment sent an SF7 advert that the feeder received,
then received 5/5 primary SF8 packets. Its final all-SF CAD guard was tested
for busy and error handling in native tests, but not against a controlled busy
RF channel. It may drop a packet at the final busy check rather than retrying.
It is a lab build, not a production setting.

With existing side detectors and conservative RXPS, SF7 received 10/10 while
SF8 received 2/10, 3/10, and 3/10 in three bursts; SF9 received 0/10. Increasing
the RX window to 200 ms gave 8/10 SF9. The W12 safety change holds RXPS in
continuous receive while compatible extra-SF detectors are active and restores
the saved RXPS setting when they clear. With the hold, two SF8 bursts received
9/10 each. Short tests cannot establish a delivery rate.

Parallel multi-SF CAD prototypes were not reliable in this setup. The
one-symbol any-signal variant received 7/10 SF7 and 3/10 SF8 on the multi-SF
profile, and the fixed radio2 side received 6/10 at a 32-symbol preamble.
Longer matched preambles did not resolve the losses. The receive-only
sequential prototype is on `codex/w12-pram-slot-lab`; the supervised TX
prototype is on `codex/w12-pram-tx-lab`. Neither is enabled in production.
