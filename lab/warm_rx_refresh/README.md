# Warm receive refresh

Heltec V4 builds based on `Heltec_lora32_v4` enable `MESH_WARM_RX_REFRESH=1`
and replace periodic AGC maintenance with an owned same-channel warm receive
refresh. V4 R8 and other board families keep their existing implementation.
Other supported SX1262 builds can opt in with the same flag. The fast path
also requires the existing warm-profile and complete packet-cache features.
It refuses an unacknowledged or incomplete cached receive context.

The successful operation sends SetFs (or warm XOSC standby), clears IRQ status
after checking for a completed packet, and sends checked continuous SetRx.
It does not sleep, calibrate, retune, write packet/modulation parameters, or
restart the noise-floor estimator. This is an experimental receive refresh;
it does not establish that the hardware analog AGC has been reset.

The source flag is defined for the whole build, including Dispatcher. Changing
only this helper's default macro does not enable the scheduler consistently.

The existing saved `agc.reset.interval` remains the primary interval, with the
same zero-disable behavior, four-second storage steps, 1020-second maximum,
default, and adaptive scheduling policy. `get agc` and `set agc <seconds>` are
aliases. Fixed scheduling still postpones the primary deadline after TX.
The aliases, independent timer control, and diagnostic pages below are
available in Repeater, Room Server, Sensor, and Companion text parsers.
Companion's primary alias retains its existing 0-255-second parser range.
A deferred or failed warm operation retries after 250 ms without receiving
credit for a completed refresh.

`get agc.old` and `set agc.old <seconds>` control an independent legacy full-reset
timer: zero disables it, its maximum is 86400 seconds, and it defaults to zero
(disabled) after each MCU reboot. It is RAM-only and does not change preferences.
For example, `set agc.old 600` enables a ten-minute full-reset interval.
Its first deadline is anchored when Dispatcher begins servicing the enabled
timer, and it neither follows TX postponement nor adaptive escalation. When
both timers are due, the old timer goes first and the primary retries later.
A shared recovery in the same loop defers both maintenance methods. Unsupported
wrappers have no second timer. Shared watchdog recovery keeps its existing
full-reset implementation.

The warm operation requires continuous primary LoRa RX, RXPS and radio2 off,
and no active TX, packet/FIFO owner, calibration window, receive retry,
beacon, carrier, or scanner. An RX completion racing standby keeps its FIFO.
Even a command error with a simultaneous completion preserves that unchanged
tuple for the ordinary packet reader before reentry. Command failures revoke
fast reuse; absent an unread event, existing bounded profile repair restores
receive operation and may use the normal heavier parameter/estimator path.

## Diagnostics

`get agc.status` reports the saved primary interval, invoked requests, admitted
RF transitions, deferred/failed requests, checked successful reentries, and a
persistent last failed return code. Each request has exactly one outcome.
Admission is not proof that the hardware accepted a command. A packet completing
during standby can produce both an admission and a deferral.

`get agc.timing` reports the count and last/minimum/maximum successful command
span in microseconds, pending restoration, actual transition mode, last request
reason, and total last request duration. The successful span starts immediately
before the standby/SetFs command and ends immediately after successful checked
SetRx returns. The separate request duration also includes ownership probes.

`get agc.recovery` reports ordinary receive reentries completing a pending warm
transition, their last/minimum/maximum elapsed time, failed rearm attempts, and
the persistent last failed command step. These recovery spans are separate
from successful warm refresh spans. A pending FIFO can extend a recovery span
until the reader consumes it and reenters receive.

These values are software command measurements, not independent measurements
of RF reception or physical analog outage. Getter pages are separate reads and
can straddle a maintenance event. Timing subtraction handles one 32-bit
microsecond clock wrap; individual measured spans must remain below that wrap.
Lifetime event-counter rollover is not handled by the finite lab parser.

With `MESH_AGC_TIMING_DIAGNOSTICS=1`, `get agc.old.status`, `.timing`, and `.errors`
expose the existing full-reset diagnostics, including shared recovery calls.
The compatibility names `get agc.reset.status`, `.timing`, and `.errors` still
refer to those legacy diagnostics. They do not describe the new warm method.
Disable `agc.old` explicitly when testing warm-only maintenance.
