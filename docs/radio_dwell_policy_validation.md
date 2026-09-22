# Automatic dwell policy: 4.6 chirps on both profiles

Historical validation record: the nominal 600 us switch allowance below has
been replaced by the activation self-test and a 10% guarded measured budget.
See [radio profiles](radio_profiles.md#preamble) for current behavior.

The shared `RadioProfiles` function uses a 4.6-symbol minimum for both logical
profiles of one physical transceiver, rounded upward to a whole microsecond.
The slower profile is selected by symbol duration, with primary winning ties.
The fast visit receives the remaining half-slow-preamble budget, subject to
the same 4.6-symbol minimum. The nominal allowances are 600 us per switch and
300 us per cycle, not added delays.

The scheduler, automatic preambles, configuration acceptance and diagnostic
calculator use the same minima. Preserve explicit preambles, safe-airtime
validation, too-short slow-preamble rejection, recommendation rounding to
multiples of eight (minimum 32), and the existing SF8/500 versus SF7/62.5
88-symbol recommendation floor. Measured switching overruns raise advisory
warnings without rewriting configured values. Single-profile RX still does
not perform a scan.

For SF7/62.5 plus SF8/500 with slow preamble 32, the allocation remains
9,421 us slow and 21,847 us fast, in either profile order. The fast visit
already exceeds 4.6 fast symbols. Raising the fast minimum matters when
less budget is available: equal SF7/500 profiles with slow preamble 30 no
longer fit, because only 1,162 us would remain versus the new 1,178 us floor.
Slow preamble 32 fits with a 1,418 us fast visit.

## Hardware context

The preceding V4 RX / fixed RAK4631 and T1000-E TX bench used explicit
32-symbol preambles on both profiles, 100 stationary controls per profile
and a complete 100/profile scan. Controls passed 100/100 each; scanning
received slow 97/100 and fast 100/100 (197/200 total). Switching averaged
582 us and peaked at 827 us. Successful-packet mean SNR was +12.27/+13.47 dB.
No RF retry or USB result replay was used. Radios were left idle.

That test used the same 9,421/21,847 us windows, so the higher fast floor
does not change this particular allocation. It does not establish lossless
performance or qualify other SF/BW combinations. Raw capture SHA-256:
`1c2e0eae3f304fcaeb6addfeef4f2f09a3c2bec8d78c60d3f151c952d0f6d5f5`.
No additional RF run is required to apply the user-requested policy change.

## Software verification

The portable C++ test sweeps 1,600 SF/BW pairs and checks both 4.6-symbol
minima, exact fast-window allocation, preamble/diagnostic consistency and
accepted return deadlines. It also covers reversed/equal symbol times,
explicit preambles, newly infeasible short settings, long requested visits,
invalid parameters and advisory-only switching overruns. CLI tests exercise
warnings, both timing aliases, saved/temporary configuration, persistence
and bounded replies. Native integration covers routing, retries and packet
ownership alongside the timing model.

Verification completed on 2026-09-14 (local date):

- Exact commit snapshot: 24/24 host production tests passed, including primary
  CLI settings, shared timing, command dispatch, persistence and reply checks.
- Native radio/routing/retry integration: 63/63 passed.
- Existing HIL host regressions: 64/64 passed in the working checkout.
- Both firmware builds passed from the isolated commit snapshot, without the
  unrelated local lab/Wi-Fi/extra modulation-cache edits: V4 repeater RAM
  125,336 bytes / flash 1,597,497 bytes; USB companion RAM 62,452 bytes /
  flash 828,257 bytes. Both passed runtime-RAM checks.
- Staged whitespace and documentation-link checks passed. No radios were
  flashed for this function update.
