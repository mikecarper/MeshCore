# SF5 / 250 kHz: four-channel dwell sweep

Measured 2026-09-14. **No tested dwell from 4.1 through 8.1 chirps passed.**
Each setting stopped at its first invalid reception or timeout; the sweep
stopped at the requested 8.1 limit. None reached 100 packets per channel.

## Setup

- XIAO ESP32-S3/WIO SX1262, COM31, receiver; SenseCAP Indicator ESP32-S3,
  COM28, reference TX. Radios remain in the user's separated positions.
- XIAO boosted RX **off**, verified actual gain register 0x94 before tests
  and in every final scan status. Indicator 0x96, unchanged as transmitter.
- Four centers: 909.500, 909.750, 910.000, 910.250 MHz. SF5, bandwidth 250 kHz,
  CR4/5, 32 programmed preamble symbols, 16 synthetic bytes, -9 dBm.
- Production warm fast RX and unchanged-modulation cache enabled, buffered
  8 MHz SPI. Existing packet guards and initialization unchanged.
- Requested grid 4.1 to 8.1 in 0.5-symbol steps. Each setting starts fresh,
  shuffles channel order with seed 606125 and varies arrival timing without
  synchronization to RX visits. Both boards reboot between settings.
- 100 packets/channel maximum; stop that setting on first failure, then move
  to next dwell. No retransmission of a failed probe. Stop the entire sweep
  on first complete 400/400 result or after 8.1; abort on fixture errors.
- Extra IRQ polling and packet traces disabled to avoid adding diagnostic
  overhead to sub-millisecond visits. RX mode, modem software cache, gain,
  SPI command counts, timing and packet identity checks remain enabled.

```text
python tools/hil/profile_switch_dwell.py --receiver COM31 --sender COM28 --output tools/hil/sf5_250_dwell_normal_results.json
```

## Results

"Received" means correct sequence, payload and reported receive channel.
Each row is a first-failure sample, not a 100-packet/channel PER measurement.

| Chirps | Requested dwell | Received / attempted | First failure | Mean retune |
| --- | ---: | ---: | --- | ---: |
| 4.1 | 525 us | 1 / 2 | channel mismatch | 458.122 us |
| 4.6 | 589 us | 0 / 1 | timeout | 450.787 us |
| 5.1 | 653 us | 0 / 1 | timeout | 450.667 us |
| 5.6 | 717 us | 0 / 1 | timeout | 452.270 us |
| 6.1 | 781 us | 2 / 3 | timeout | 452.991 us |
| 6.6 | 845 us | 0 / 1 | timeout | 451.082 us |
| 7.1 | 909 us | 0 / 1 | timeout | 453.796 us |
| 7.6 | 973 us | 0 / 1 | timeout | 453.568 us |
| 8.1 | 1,037 us | 4 / 5 | timeout | 457.886 us |

All 16 requested transmissions returned success. Seven met the full receive
checks. Of nine failures, one returned a matching synthetic sequence on a
different reported receive channel and eight timed out. Every timeout run
also recorded one `rx_errors` increment. These are receive/read/decode errors,
not retune hardware faults; without an IRQ trace their exact cause and
association with the expected probe cannot be reconstructed.

All **64,507 timed hops** passed RX-mode/software-cache checks with zero
retune failures. Weighted mean switch time was **452.739 us**, excluding
listening dwell and full application scheduling. Each hop programmed a
frequency. Modulation rewrites occurred only when the prior context needed
re-establishing, rather than on every frequency-only hop.

The 4.1 second probe was transmitted for channel 2 (910.000 MHz), but its
sequence was returned while the receiver reported channel 3 (910.250 MHz),
16 bytes, RSSI -66 dBm, SNR 0.5 dB. It therefore failed the channel check;
the saved `valid=false` also does not independently certify all payload bytes.
No synchronized RF/frequency-register capture was taken, so this is evidence
of wrong-channel reporting/reception, not proof of its physical mechanism.
Successful correct-channel probes read -42 to -41 dBm, SNR 7.8–8.8 dB.

## Interpretation

SF5/250 did **not** establish a solution to the close-range problem, and
increasing dwell alone did not produce a clean four-channel result. The
wrong-channel sequence and receive errors prevent interpreting this as a
pure minimum-dwell/acquisition test. It is not evidence that 8.1 is a reliable
setting merely because four probes passed before its miss.

There is also a timing tradeoff: a symbol lasts 128 us, so 32 programmed
preamble symbols occupy only **4.096 ms**. A ~453 us retune costs about
**3.54 symbols**. At 4.1, four nominal dwells plus four such retunes consume
about 3.91 ms before other loop costs; at 8.1 they consume about 5.96 ms.
Longer visits give acquisition more uninterrupted time but increase the time
spent away from each channel. This arithmetic motivates testing a longer
preamble or fewer channels separately; it does not prove the cause of any
particular failure. No such follow-up was transmitted after the 8.1 limit.

Unlike the [earlier separated SF6 run](separated_radio_modulation_cache_validation.md),
this changes SF, bandwidth, absolute preamble duration and diagnostic polling.
The RF settings also leave channel spacing unchanged at 250 kHz. It is not a
matched comparison isolating receiver sensitivity or the modulation cache.

## Provenance and handoff

Base `0e5955f889788a4317863e731b38ffe09e3d5441` on `keymindCascade`, plus
uncommitted production cache and HIL changes. RadioLib 7.7.1 at
`187ef24791c3d844939b2be13a68bd890bd04e4c`; Espressif32 6.11.0 / Arduino
ESP32 2.0.17. Both HIL builds and verified uploads succeeded. All **39 selected
host/native tests** passed, including bandwidth/dwell calculation and bounded
sweep stopping. No commit or push was requested for this work.

Tested firmware SHA-256:

- XIAO: `6ba7b4c6785ac11eb8a4ecd2f1148f4f55ce866c1cb172776757fe0a95047da9`
- Indicator: `3672ca330d4b2d3e8efa39edf2dbc3b27b8804dbb9a98f29267c85c41972a028`

Raw [manifest](../tools/hil/sf5_250_dwell_normal_results.json) references all
nine per-dwell captures. SHA-256 values (all files under `tools/hil`):

| File suffix after `sf5_250_dwell_normal_results` | SHA-256 |
| --- | --- |
| `.json` | `aaf00ef8531cb2c5c7e4dfb78d3b8b03ce61d9afe8323fb33e70be09656adf54` |
| `_4p1.json` | `a9c6baf0bece79c9b9c96bfa97e3cd9932e0c04fc76b43f61ed350512306341a` |
| `_4p6.json` | `63863b55108374011afbe555bfeb97034b010e194cdad83a212d5d286d1992fe` |
| `_5p1.json` | `ff1d2927365e3634984bc0b843a09f2934c5dcac49b60b862ff4db030e0f7e74` |
| `_5p6.json` | `33c91687d0b61dd83e2a08d8358b43f21fa27f9e80f9503feedeb657453a7efd` |
| `_6p1.json` | `5baa8d1bfcf0ccc947c92d563dc555802ac599ce1e8d260a623964936a99da4e` |
| `_6p6.json` | `90f6b4d7785add771e30da7dfa78672fd75ad13d8a5965c21ec1a37e15d8dabb` |
| `_7p1.json` | `eb99afd932a5579c765f8a6c0a18db4bacc317829b0654d11fbf2f569c3a8618` |
| `_7p6.json` | `bdb88da82437fa6e01ad90ab927d364b4d67aaadbac7597c3aacb9799b053211` |
| `_8p1.json` | `404ee179e4c10333bd6fddae9f0faa9da81f6193dcb5fff6d9bd37a466c319a3` |

Both boards were rebooted after the sweep and answered ready with correct
HIL capabilities; XIAO gain remained 0x94. Per user request, **HIL firmware is
left installed**, idle with no autonomous test transmissions. Private original
flash backups remain available. The RAK4631, Indicator RP2040 and unrelated
S3 soak work were not modified. The temporary build overlay was removed.
