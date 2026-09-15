# SF6/125 four-channel post-switch settling sweep

Measured 2026-09-14. **No setting from +0.0 through +1.0 ms passed 400/400.**
The requested delay was applied after BUSY went low without consuming the
subsequent 5.1-symbol listening visit. Each run stopped on its first miss; the
sweep would have stopped at the first 400/400 pass, but reached its nominal
timing limit instead. No RF probes were sent beyond that limit.

## Method and timing budget

- XIAO ESP32-S3/WIO SX1262 COM31 receiver, SenseCAP Indicator ESP32-S3 COM28
  transmitter, unchanged user-separated positions. XIAO boosted gain off,
  actual register 0x94 verified; Indicator 0x96. Original backups retained.
- SF6 / 125 kHz / CR4/5, four centers 909.500/909.750/910.000/910.250 MHz,
  32-symbol preamble, 16-byte synthetic packets, -9 dBm.
- Warm fast RX, buffered 8 MHz SPI, unchanged-modulation reuse enabled.
  Same XIAO image for every delay; unchanged Indicator reference image.
- 100 packets/channel requested, balanced shuffled channel order (seed 606125),
  varied unsynchronized arrival timing. Both HIL boards reboot between delays.
- No extra IRQ polling or packet traces; ordinary ownership/preamble guards
  remain enabled. No RF retries, shorter channel count or altered preamble.

At SF6/125, a symbol is 512 us. The requested listening visit rounds up to
ceil(5.1 * 512) = 2,612 us; 32 programmed preamble symbols last 16,384 us.
Using the user's nominal 453 us retune, the extra per-hop budget is:

`16,384 / 4 - 2,612 - 453 = 1,031 us`

Thus the 100 us grid runs from 0 through 1,000 us; 1,100 us would exceed the
nominal full-sweep calculation. Sync/header time is not spent as extra scan
budget. This is not a worst-case scheduling or acquisition guarantee.

The delay is **HIL-only**: after a successful guarded production retune and
BUSY-low observation, the CPU waits without SPI/IRQ operations, then starts a
separate 2,612 us listening clock. The SX1262 is already receiving throughout
that delay and may acquire a packet. This tests extra RX occupancy/settling,
not a pre-SetRx delay or a period with RX disabled. Ordinary RX restarts after
processing packets reset the visit clock without another channel delay.
Zero delay preserves the previous production RX-start clock.

An initial receive-only, 300 ms smoke check at +1,000 us measured 74 hops,
minimum applied delay 1,000 us and minimum subsequent dwell 2,612 us. Its
17 clean full cycles averaged 16,235.941 us, maximum 16,260 us. No RF probe
was transmitted in this check. The longer packet experiments below capture
more scheduling variability, including host command handling.

## Results

All times in the last two columns are measured means, not target values.
"Received" is successes before the next probe timed out; every row ended in
one timeout. These first-failure samples do not estimate comparable PERs.

| Added delay | Received before miss | Switch + delay | Clean full cycle |
| ---: | ---: | ---: | ---: |
| 0.0 ms | 44 | 0.452381 ms | 12.256886 ms |
| 0.1 ms | 4 | 0.548387 ms | 12.669976 ms |
| 0.2 ms | 34 | 0.650424 ms | 13.074105 ms |
| 0.3 ms | 53 | 0.753410 ms | 13.485859 ms |
| 0.4 ms | 47 | 0.850830 ms | 13.868454 ms |
| 0.5 ms | 66 | 0.952289 ms | 14.273736 ms |
| 0.6 ms | 14 | 1.050333 ms | 14.671156 ms |
| 0.7 ms | 21 | 1.151351 ms | 15.076094 ms |
| 0.8 ms | 63 | 1.255113 ms | 15.482272 ms |
| 0.9 ms | 19 | 1.352843 ms | 15.880361 ms |
| 1.0 ms | 25 | 1.452750 ms | 16.283488 ms |

All **401 transmissions** returned success: **390 valid receptions and 11
timeouts**, spread across the eleven independently stopped settings. None
completed 100/100 on each channel. All **52,609 timed hops** passed RX-mode
and software-cache checks, with one observed frequency write per hop and zero
retune failures. Each positive-delay hop waited at least the requested time;
every run's minimum subsequent idle dwell was 2,612 us. No receive-error
counter increment or device error was reported. Successful RSSI was -42 to
-40 dBm and SNR 9.5–11.8 dB.

`switch` measures production retune entry through BUSY-low observation;
`settling` measures the following wait, including timer overhead;
`switch_and_settle` combines them. Even zero requested delay records roughly
1–2 us of timer/bookkeeping overhead in the combined boundary. Listening
dwell and full application scheduling are not included in retune time.

`idle_cycle` measures consecutive channel-0 return times, excluding initial
cycles and cycles containing packet/guard-held visits. It still includes loop
and USB work encountered during otherwise unheld visits. At +1.0 ms its mean
is 100.512 us inside the preamble budget, but its **maximum was 17.475 ms**,
exceeding 16.384 ms. At +0.9 ms the observed maximum was 16.153 ms. Therefore
the last grid point fits only the nominal/mean calculation, not every observed
full cycle. Legitimate packet holds can extend scan timing further.

## Conclusion and limits

Extra post-switch settling through the available timing budget did not
establish loss-free four-channel reception. The 66 successes at +0.5 ms are
not evidence that it is an optimum; the results are small, first-failure
samples with different uncontrolled RF arrival phases. The added delay also
extends total RX occupancy, so any improvement would not isolate analog
settling from longer acquisition time.

Every failure here was a timeout without a reported receive/decode error.
Without an IRQ trace or synchronized independent RF capture, that does not
prove absence of a preamble/header indication or identify the precise cause.
It does not justify changing production packet guards or adding a permanent
settling penalty. No production default was changed by this experiment.

## Reproduction, provenance and handoff

```text
python tools/hil/profile_switch_settling.py --receiver COM31 --sender COM28 --output tools/hil/sf6_125_settling_normal_results.json
```

See [harness instructions](../tools/hil/profile_switch_README.md#post-switch-settling-delay-sweep-sf6125).
Base `0e5955f889788a4317863e731b38ffe09e3d5441` on `keymindCascade` plus local
changes. RadioLib 7.7.1 at `187ef24791c3d844939b2be13a68bd890bd04e4c`,
Espressif32 6.11.0 / Arduino ESP32 2.0.17. Both HIL targets compiled; only
XIAO needed an upload for the new receiver-side delay. All **45 selected
host/native tests** passed, including delay/visit separation, packet restart,
timer wraparound, clean-cycle accounting, bounded grid and first-pass stop.

Actually loaded firmware SHA-256:

- XIAO: `a0dbc113dcb297be34e22c0bc058db71c4108659e1e289679ecdbfc5c0009f8e`
- Indicator, unchanged from SF5/250 sweep:
  `3672ca330d4b2d3e8efa39edf2dbc3b27b8804dbb9a98f29267c85c41972a028`

Raw [manifest](../tools/hil/sf6_125_settling_normal_results.json) and per-delay
captures are preserved byte-for-byte. SHA-256, file suffixes after
`tools/hil/sf6_125_settling_normal_results`:

| Suffix | SHA-256 |
| --- | --- |
| `.json` | `41a20c3d5b809cd225772980fbe0d305c14391659f10d685bf78f8bdf326e61c` |
| `_0us.json` | `47a73a35ed72ebc61b88bfe315e86bf7e15ba1d2d93e68ce92ce681e1ef73cef` |
| `_100us.json` | `b585ea01f605bc876da86ba2a35fbe6e7cc6543eb5e827ba190c51dca10dcb85` |
| `_200us.json` | `6ce378cd079eefae8450c7f5672281095b9b99e28e0c7920f024a4396606bb40` |
| `_300us.json` | `d93e7a258dddb6731c030e395e09c3f06b4d5988638cc85dc5cd0745256c6202` |
| `_400us.json` | `56bfe4cb7cbab2a660a232a2bf85b9bb87221ffade7c9bbce6d1116e6c30fcbf` |
| `_500us.json` | `fffb6c33a506f537913084fc4f5cc9f8009a4f0f61e4f290dd81a7409f53d1bd` |
| `_600us.json` | `7bc6dd41f0079f37bdbf77fc48c4113e26589bcce704850b106c33d057e34659` |
| `_700us.json` | `54170f5b63bbe8ffa231852bf892ea55d8579c33631775f29fb7c127197afb2a` |
| `_800us.json` | `f7c53375b29764b4369b84e8ac3244aa37e385c8738a9dfd7f3257d85a64a705` |
| `_900us.json` | `56bc3691f2046cafcf2115b2f6b7bd164e9c9039733ea51028db292deea10230` |
| `_1000us.json` | `f708af37caee8f689f2436567382458d63aba1269e8306ac6e4d0079677b9115` |

Both boards rebooted to idle HIL after testing and answered ready. XIAO gain
remained 0x94 and settling policy was returned to zero. **Test firmware stays
installed**, per user request; no restore, commit or push was performed this
turn. RAK4631, Indicator RP2040 and unrelated S3 soak work were untouched.
The temporary build overlay was removed; no autonomous test TX remains.
