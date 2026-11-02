<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/rxps_recovery_2026-11-02/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# RX power-saving recovery — 2 November 2026 UTC
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/rxps_recovery_2026-11-02/">View this page on MeshCore Docs</a>.</p>

**Software validation passes; physical LR1121 validation remains pending.**
The shared RadioLib receive wrapper now recovers from failed RXPS arming with
scheduled retries, while retaining ordinary RX between attempts. This replaces
the permanent cutoff after three consecutive failures. It is a recovery and
diagnostic change, without a new RF timing profile or a confirmed LR1121 fix.

Failures schedule another attempt after 5 seconds, 15 seconds, 60 seconds,
then 5 minutes for subsequent failures. An idle node retries without needing
packet traffic. The deadline is an MCU maintenance wake, separate from failed
RX startup and pending packets; it does not prevent tracker suspension or
extend the adaptive AGC retry cadence. Delays remain bounded across clock
rollover and after long periods with another radio owner active.

Automatic attempts use the existing reconfiguration admission checks. They
defer for BUSY, TX, a pending IRQ or an ongoing receive, without incrementing
the failure streak. Profile scanning, calibration, retained packet metadata,
carrier/Morse/beacon ownership and disabled receive directions retain their
ownership. A new IRQ check also preserves a packet that completes during the
activity probe, before standby is issued. A refused attempt schedules a short
later check instead of leaving an overdue wake in a polling loop.

Reapplying the same enabled RXPS setting during an arm-error fallback now
requests a fresh attempt through those same checks. A healthy unchanged setting
remains a no-op. The saved settings layout is unchanged. Cumulative failures
remain available after a successful arm; the consecutive streak and countdown
reset on success.

`get radio.rxps.status` reports `retry=<seconds>s,streak=<count>` during error
recovery. Intentional pauses use `hold=radio2`, `hold=calib` or `hold=timing`.
A wrapper outside RX reports `effective=inactive` rather than promising
continuous reception. The older Companion getter also distinguishes an error
fallback from a deliberately continuous fast tuple. Status is read-only and
bounded within the existing prefixed reply buffers. `armed` describes accepted
commands and wrapper state, not independent proof of RF reception.

The implementation is based on
[`42e84b135`](https://github.com/mikecarper/MeshCore/commit/42e84b135655dc20e3e7e922d2ab3881f9c9bb6e)
plus the working-tree changes recorded by source hashes in the
[sanitized evidence](rxps_recovery_2026-11-02.safe.json). Test artifacts use
`v1.18.0.2-rxps-recovery-test-42e84b13`; the commit suffix alone does not identify
the uncommitted implementation.

Validation completed with one PlatformIO process at a time:

- **95 host tests passed**, including executed production retry, loop,
  reconfiguration, scanner, ownership, receive-retry, diagnostics and Universal
  radio forwarding paths. The new runtime suite includes eight executable
  negative controls. Sanitized C++ harnesses exercised failed arm/fallback,
  autonomous idle recovery, BUSY and IRQ races, same-setting retry, normal and
  wrapped deadlines, more than half a clock-wrap of ownership suspension,
  saturated streaks and bounded status replies.
- Native RXPS calculator, CLI and recovery tests passed; the exact case count
  and log hash are in the evidence.
- **Waveshare LR1121 Full Repeater** built and passed all 15 capability checks,
  expanded-partition size checks and RAM admission: 242,248 internal bytes
  available against 230,288 required.
- **Muzi Full Companion** built with the SX1262/LR1121 selection wrapper and
  passed all 17 capability checks, ZIP/UF2 address checks and RAM admission:
  88,916 internal bytes available against 59,200 required.

No board was flashed and no radio, battery, contact or host-adapter settings
were changed. Earlier build-argument preflights and incomplete host-harness
adapters were corrected before the successful final checks; their private
logs are retained and are not hardware results.

The LR1121's existing TCXO delay, capture assumptions, native duty-cycle mode
and wire preambles remain unchanged. A CAD-based replacement would require
qualified detection thresholds and packet/current measurements; it was not
enabled. See [Semtech's native duty-cycle interface](https://github.com/Lora-net/SWDR001/blob/master/src/lr11xx_radio.h)
and the [earlier LR1121 validation scope](lr1121_rxps_2026-10-27.md).
Next physical validation should compare RXPS off, conservative and balanced
against a fixed sender/profile, count packets across varied transmission
phases, measure average current, and retain status/watchdog observations
before and after RX/TX transitions.
