# Shared radio/radio2 chirp timing and preamble warnings

Implemented in the `keymindCascade` working tree, 2026-09-14. The initial
4.1/4000-us implementation record below is retained as history; the
[4.6/300-us policy update](#automatic-46-chirp--300-us-policy-update) is now current.
No radios were flashed or RF experiments started for either function-only change.

## Initial implementation scope

- One portable timing function in `src/RadioProfiles.h` calculates both profiles
  from their own SF/BW symbol times, optional dwell times and explicit budgets.
- The production dwell minimum is now 4.1 chirps on both profiles, rounded up to
  a whole microsecond. The slow visit changed from 4.8 to 4.1; the old four-chirp
  fast minimum changed to 4.1. The existing asymmetric scheduler may allocate
  a longer fast-channel visit from the slow preamble's return deadline.
- Automatic preambles and diagnostics share the same requirement calculation.
  The hot retune path computes only the requested profile, not a full diagnostic
  structure or extra hardware reads.
- Round recommendations upward to multiples of eight, minimum 32. Preserve
  the prior measured 88-symbol SF8/500-versus-SF7/62.5 floor.
- Warn by profile name with the recommended preamble when the requirement is
  above 32 or above an explicitly selected short value. Successful radio-setting
  replies include `WARN recommended preamble: radio=...` and/or `radio2=...`.
  Existing explicit values are not overwritten.
  Existing unsafe-airtime and infeasible-return-deadline rejection stays intact.
- `get radio2.timing` / `get radio.timing` is read-only. It reports both dwell
  values and requirements in `radio,radio2` order. Settings/getters and scheduling
  acknowledgments include warnings; successful primary-setting responses use a
  preview of the requested SF/BW, not stale live settings.

## Evidence and limits

The [five-radio single-pass bench](four_fixed_tx_single_pass_validation.md)
received 392/400 at 5.1 chirps. The [longer-dwell repeats](four_fixed_tx_dwell_validation.md)
scored 342/348 before the user stopped 6.1, and 395/400 at 7.7. They do not
establish any lossless four-channel dwell. They also do not justify treating
one full sweep fitting inside the preamble as proof of acquisition.

The function retains the earlier production two-profile policy: two return
opportunities on the slower channel, blind time plus sixteen acquisition
symbols on the faster channel. Its 4.1 floor is the requested policy boundary,
not an experimentally established detection minimum. This change requires RF
qualification before describing it as a reliability improvement.

Single-pass V4 switching peaked at 804 us including post-reception cache refresh.
Indicator reached 8428 us in the prior receive-only study. The pure function can
model either allowance. Per the follow-up request, runtime scheduling now uses
a nominal 600 us switch / 4000 us loop budget; advisory calculations raise the
switch allowance if the observed maximum exceeds 600 us. The 0.6 ms nominal
value does not cover all measured switches. They do not dynamically retune the production
scheduler or silently lengthen already configured preambles.

See [the formula and CLI examples](radio_profiles.md#chirp-calculator-and-warnings).

## Verification

- Portable C++ property sweep: 1,600 SF/BW pairs (SF5 through SF12, five bandwidths
  on each side), minimum dwell, rounding, automatic/diagnostic consistency and
  the two-return deadline for accepted tuples.
- Exact SF10/125 symbol duration and 4.1 floor; SF6/125 comparison using 1000 us
  versus 9000 us switching; reversed profile order; invalid/non-finite inputs;
  explicit long/short preambles; advisory-only overrun handling.
- Production CLI tests cover warnings before delayed publication, named profiles,
  both timing aliases, single-profile off, explicit short overrides, saved versus
  temporary primary settings, read-only enforcement and bounded reply buffers.
- Production scan/transition harness retains packet ownership, busy deferral,
  failed-retune rollback, receive restoration and oscillator-policy checks.
- Native `test_trace_retry`: 63/63 tests passed, including the updated automatic
  preamble expectations and profile routing/retry/scheduling tests.

- Host regression suites: 22/22 tests passed across chirp math, profile CLI,
  scan transitions, setting contracts, temporary-reply delivery, STM32 float
  formatting, local/LoRa CLI access and repeater timing integration. The CLI
  fixtures include the new warning method. Windows MinGW runs the local-access
  assertions without unavailable ASan/UBSan runtimes; Linux retains those flags.
- Full `heltec_v4_repeater` build after the 0.6 ms follow-up: passed;
  RAM 125,344 bytes, flash 1,597,789 bytes, runtime-RAM checks passed.
- Full `heltec_v4_companion_radio_usb` build after the follow-up: passed;
  RAM 62,460 bytes, flash 828,589 bytes, runtime-RAM checks passed.
- The rerun passed all 22 host tests and 63 native cases, including actual
  primary-setting reply dispatch (local and LoRa), requested-tuple previews,
  a recommended 32-symbol preamble for an explicit 16-symbol override, and
  the nominal 600 us allowance. The 4.1-symbol floor and 4000 us loop margin
  remain unchanged.
- `git diff --check`: passed. No firmware was uploaded and no commit or push
  was made for this change.

## Automatic 4.6-chirp / 300-us policy update

After the separate [4.6/0.3 ms hardware test](pair_4p6_300us_validation.md), the
user requested this policy in the shared production function. That sample was
197/200 (slow 97/100, fast 100/100), not evidence of lossless operation.

- Slow-profile dwell is now `ceil(4.6 * symbol_us)`; the fast floor remains
  `ceil(4.1 * symbol_us)`. Slow/fast is selected by symbol duration, not profile
  index (primary wins equal-symbol ties). Single-profile mode is unchanged.
- Keep 600 us per switch and use 300 us of loop reserve. Allocate the fast
  visit from half the configured slow preamble duration minus the slow visit,
  two switches and the reserve. The reserve is not an added delay.
- The pure calculator, automatic preamble calculation, real scheduler and
  acceptance checks all use the same role-specific rounded minima. Advisory
  warnings still account for measured switch overruns without rewriting
  explicit preambles or persisted configuration.
- SF7/62.5 + SF8/500 with a slow preamble of 32 yields **9,421 us slow and
  21,847 us fast**, matching the tested plan, in either profile order.
- Preserve unsafe-airtime/short-slow-preamble rejection, eight-symbol rounding,
  the 32-symbol recommendation minimum, and the historical SF8/500 88-symbol
  floor. For SF7/500 paired with SF7/62.5, the automatic fast preamble changes
  from 72 to 64 because the reserve is smaller. Explicit 32 remains explicit,
  with the existing short-override warning.

Verification covers 1,600 SF/BW pairs with exact role-dependent rounding and
fast-dwell arithmetic, profile reversal, equal-symbol ties, larger explicit
slow preambles, infeasible short slow preambles, single-profile off, long
explicit visits, and advisory-only switch overruns. Production CLI checks
assert the actual `4.60,42.67` chirp readout, `loop=300us`, warning values and
unchanged explicit 32 on the tested SF8/500 pair. The frozen HIL plan now
checks equality against the production 4.6/300-us allocation.

- Host production regression suites: 15/15 passed.
- Native production radio/routing/retry suite: 63/63 passed.
- HIL host regression suites: 64/64 passed.
- Full V4 repeater build: passed; RAM 125,344 bytes, flash 1,597,881 bytes.
- Full V4 USB companion build: passed; RAM 62,460 bytes, flash 828,657 bytes.
  Both passed runtime-RAM checks; builds ran sequentially in one process.
- Scoped `git diff --check`: passed.
- No hardware upload, commit or push for this policy update.
