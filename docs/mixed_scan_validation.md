# Mixed-bandwidth two-channel reception test

V4 transmitter on MercerWoodMesh; XIAO ESP32-S3/Wio SX1262 receiver. Channel 0: 909.5 MHz, SF7/62.5 kHz, preamble 32. Channel 1: 910.5 MHz, 500 kHz, SF and preamble below. Both use CR4/5, explicit header, CRC and 64-byte test payloads. Fast RX-preamble scanning uses four symbols of the active profile per visit, not four simultaneous channels or hardware CAD.

Each setting starts with a five-packet-per-channel continuous-RX baseline. Scanning sends 50 packets per channel in shuffled order with random host delays. Only CRC-valid, payload-validated, sequence-matched packets count. Any scan loss escalates the 500 kHz preamble by 16, capped at 128. SF9 and SF8 start at 32; SF7 starts at 48. An additional SF8/40 test was requested after SF8/48; its firmware adds acceptance of preamble 40 without changing radio timing or scanning behavior.

| 500 kHz SF | Preamble | Mode | SF7/62.5 received | 500 kHz received | Mean scan cycle |
|---|---:|---|---:|---:|---:|
| SF9 | 32 | Fixed baseline | 5/5 | 5/5 | — |
| SF9 | 32 | Scan | 50/50 | 50/50 | 14.676 ms |
| SF8 | 32 | Fixed baseline | 5/5 | 5/5 | — |
| SF8 | 32 | Scan | 50/50 | 49/50 | 12.628 ms |
| SF8 | 48 | Fixed baseline | 5/5 | 5/5 | — |
| SF8 | 48 | Scan | 50/50 | 50/50 | 12.629 ms |
| SF8 | 40 | Fixed baseline | 5/5 | 5/5 | — |
| SF8 | 40 | Scan | 50/50 | 49/50 | 12.630 ms |
| SF7 | 48 | Fixed baseline | 5/5 | 5/5 | — |
| SF7 | 48 | Scan | 50/50 | 50/50 | 11.603 ms |

These are strong-signal, sequential-transmission lab results. Successful reception of 50 packets is not proof of zero loss over months. The receiver remains on one channel through an entire detected packet, so overlapping traffic on the other channel can be lost. Scan-cycle measurements exclude time occupied by packet reception; USB command handling can add timing outliers.

Machine-readable counts, timings and firmware/source hashes: [mixed_scan_results.json](../tools/hil/cad_scan/mixed_scan_results.json).

After testing, both boards were restored to their original B soak firmware using
app-only flashes. Version and normal radio settings were verified on both boards;
Wi-Fi was connected and MQTT reported `custom (ok)`. Both persistent soak loggers
were restarted in phase `B-post-CAD-soak`, marking the intentional uptime reset.
