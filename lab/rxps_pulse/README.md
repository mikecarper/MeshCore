# Periodic RXPS receive pulse

On opted-in Heltec V4 builds the saved primary `agc.reset.interval` now drives
one RXPS pulse, replacing the former FS/XOSC refresh. The saved byte-times-four
format, interval aliases and fixed/adaptive scheduler are unchanged. Other
wrappers retain the legacy method. `agc.old` is an independent RAM-only timer,
disabled at boot. No preference schema is added.

The pulse does not use radio2's fast caches. It checks an idle primary LoRa
receiver, performs RC standby and ordinary full receive staging, and sends
SetRxDutyCycle. The MCU continues serving its loop while the initial RX window
runs. All ordinary radio SPI entry points defer while the lease owns the radio.
BUSY and DIO are sampled as GPIO only; diagnostics read cached memory. There is
no NSS wake or SPI poll during the expected retained-sleep window.

A witness requires post-arm BUSY low, a subsequent high no earlier than the
programmed RX period minus 2000 us, and a natural low. The sampled high span
must be at least 250 us and at most the compensated request plus 2000 us.
These tolerances bound loop observation uncertainty; they do not characterize
analog power or RF availability. Missed/late edges are failures, never proof
that a shorter sleep worked. The listen deadline includes the cached maximum
packet airtime, extended duty-cycle listen and a 2 s allowance, capped at 60 s.
An exceptional timeout wake is separately counted and cannot earn success.

After natural wake, the driver checks RC standby, nonclearing IRQ status, RTC
stop at 0x0902 and event clear at 0x0944, full LoRa staging, continuous SetRx and
GetStatus RX mode. Every original transport error is retained. Completed FIFO
ownership preempts reentry; a preamble/header appearing during standby is
reported as an aborted partial-frame attempt. No packet clear is used to claim
a successful maintenance cycle. Bounded failed recovery hands control back to
the existing repair path. A consumed failure waits the configured interval;
unadmitted owners retry at 250 ms.

Dual-profile scanning uses a bounded extension of an idle primary visit. The
lease snapshots both profile generations, the applied primary generation and
preamble, and the original visit clock. The scanner remains paused through
Packet phase until the FIFO owner consumes the packet and RX reentry completes.
A changed pair cancels maintenance credit, retains the completed FIFO on its
old physical profile, and leaves the normal profile refresh path due. Successful
or recovered reentry resumes the original visit deadline, allowing an overdue
secondary visit immediately. A maintenance operation is not a profile-switch
timing sample and does not change saved parameters or automatic preambles.

The full primary RX window and 500 us sleep margin are retained. This trades
occasional additional secondary absence for real RXPS maintenance; it cannot
guarantee reception with short secondary preambles. The lease span includes
primary listening and must not be called analog off time. A fixed scheduler
with MCU power saving off can retry a wrong-profile wait at 1 ms; adaptive sleep
and all other busy-owner retries keep the existing 250 ms policy. The saved
interval is not proof that a pulse was admitted or completed.

The shared timing policy leaves 500 us of headroom above the fastest nonzero
timer setting. `RxpsPulseTiming.h` adds one minimum timer tick plus the 500 us
buffer rounded up to whole ticks, then includes the actual driver transition
allowance. Backends must supply their resolved oscillator delay, timer clock
and register limit. Invalid or overflowing timing is rejected before arming.
The policy applies to future pulse backends as they are implemented; it does
not enable an unsupported pulse state machine or change ordinary RXPS levels.

SX126x uses 64 kHz timer ticks. Its policy duration is 33 ticks (515.625 us),
which is exactly 500 us above the one-tick minimum. With the current 1600 us
TCXO plus 1000 us transition allowance, the integer request is 3116 us. A
crystal-based SX126x instead requests 1516 us; a configured 5000 us TCXO requests
6516 us. The default is resolved after initialization and again before each
pulse, so oscillator fallback cannot keep another mode's absolute duration.
The helper also supports other timer clocks: at 32768 Hz the same margin
rounds upward to 18 ticks. This arithmetic is not a hardware qualification.

For comparison, with the V4 transition allowance, 6554/4000/3000 us lab requests
encode 253/89/25 RTC ticks. The programmed RX window
is recalculated from actual SF, bandwidth and preamble using the existing RXPS
preamble/header timer guard. Unsupported geometry stays in continuous RX and
records a failed bounded attempt. Neither an arm ACK nor a saved preference is
proof that the hardware completed a sleep cycle.

## Cached pages

- `get agc.status`: version 2, method rxps, saved interval, request/admit/defer/fail/ok
  and persistent failure code. One admitted pending attempt can be outstanding.
- `get agc.timing`: successful total admission-to-checked-RX samples and
  pending/phase. The total includes the initial listening window.
- `get agc.recovery`: failed-attempt spans, failed rearm count and persistent
  first error step from the latest failed attempt.
- `get agc.pulse`: requested RX/sleep, encoded ticks, witnessed cycles, aborts
  and misses. RX geometry is historical until the next admission.
- `get agc.sleep`: sampled high/wake counts, accepted high-span samples,
  cancellations and exceptional forced wakes.
- `get agc.checks`: post-arm low observations, early highs, successful RTC
  cleanup, checked RX-mode observations and nonclearing IRQ reads.
- `get agc.scan`: current dual/lease flags; cumulative dual admissions,
  wrong-profile deferrals, stale-context cancellations and completed leases;
  last/maximum extended-primary-visit spans, including failed recovery.

Pages are non-atomic snapshots. Timing values are last/min/max, not sums or
complete duration distributions. `ok` requires a real witness and checked
continuous reentry; a later successful attempt does not clear failure history.
The lab-only `agc.test.sleep_us` selector accepts 6554, 4000, 3116, 3000 or 2616 while idle.
The V4 minimum 2616 us request encodes one RTC tick without reducing the TCXO allowance.
It changes no saved interval and triggers no pulse. Production has no setter.
