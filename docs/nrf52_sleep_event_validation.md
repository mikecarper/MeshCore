# nRF52 idle-sleep regression

## Cause

The MCU could repeatedly enter the board's sleep function without actually
sleeping. Three operations could leave the Cortex-M event register set:

1. The pinned Adafruit core yields after every Arduino `loop()`. Its FreeRTOS
   yield issues `SEV`.
2. The old board sleep function queried SoftDevice state immediately before
   waiting. In the measured SoftDevice-off case this SVC recreated the event.
   Moving only the scheduler-event clear was therefore insufficient.
3. Adafruit CDC `available()` yields when its receive FIFO is empty. A USB
   client that never asserted DTR but established an RX session could therefore
   recreate an event on every idle console poll.

With SoftDevice enabled there is an additional, separate application-interrupt
history. The core's PendSV yield updates it. Clearing the ARM event register
does not acknowledge that history, so the next `sd_app_evt_wait()` still
returns immediately. This follows from the public SDK contract and the pinned
S140 6.1.1/7.3.0 interrupt-forwarder/wait code, not just the ARM event model.
The failed SoftDevice-on captures below had another blocker as well.

There was also a disabled but pending CryptoCell IRQ42 after BLE startup.
SoftDevice's documented disabled-pending-IRQ rule prevents sleep in this
state, regardless of the event-register/history fixes. The pinned CC310
library polls rather than enabling that interrupt; the pinned BSP already
clears its pending bit in its own idle wait. The application-owned wait had
missed that cleanup.

This is distinct from the existing sleep-policy guards. A large eligible time
or a high number of wait calls does not establish CPU sleep residency.

## Fix and ordering

All four nRF52 role loops now begin with `prepareSleepCycle()`. It queries the
actual SDK SoftDevice state and USB power status. When SoftDevice is active,
preparation forces a real application PendSV with `taskYIELD()`, checks that it
was serviced in an unmasked application thread, and acknowledges its SDK
history with one early `sd_app_evt_wait()`. A bare `SEV` cannot guarantee that
this SDK call returns: the SDK can internally wait again without an application
interrupt. The real PendSV is required. This ordering depends on the pinned
core: the other SDK wait caller is the lower-priority idle task, which cannot
consume that acknowledgement while the sketch task is ready.
Preparation rejects exception context, masked interrupts, a suspended/stopped
scheduler, a still-pending PendSV, or an invalidated cycle instead of trying
that early SDK acknowledgement without proof.

Preparation then clears the stale ARM event with `SEV; WFE`, before servicing
work or checking sleep predicates. A current
loop's USB-power snapshot also avoids another state-query SVC during UI work.

On chips with CryptoCell, preparation also clears that specific pending IRQ
only when its interrupt is disabled and the SDK is not in a critical region.
It never sweeps other pending bits or clears an enabled CryptoCell owner's
interrupt. Clearing NVIC pending does not discard the polling crypto result.

The snapshot is valid for one loop and at most one sleep attempt. Both
application-task `Bluefruit.begin()` call sites invalidate it before startup,
including partial or failed startup. Invalid, missing or failed preparation
declines sleep. The existing SDK/direct-register USB access policy is retained.
SoftDevice startup remains owned by the application task; the cache is not
permission to add an asynchronous SDK enable/disable path racing a wait.

Primary USB RX polling now uses the native CDC0 FIFO query, with the existing
session checks both before and after that query. It does not change DTR policy,
CDC1 logging ownership, or the facade's blocking-read/write protections.

The late wait remains a single `sd_app_evt_wait()` when SoftDevice is enabled,
or the existing `WFE; SEV; WFE` sequence when disabled. There is no late
`SEV; WFE; WFE` double-wait: that could discard the only radio IRQ arriving
after the caller checked for work. No blanket IRQ clearing or SEVONPEND change
was added.

## Controlled hardware evidence

The test device was a RAK3401/nRF52840 with an external RAK13302 SX1262 on
Mercerwood. Tests used 908.5 MHz, BW250, SF9, CR5, power saving on, manual
RXPS 30000/10000 us, AGC 0, TX power 2, no forwarding/adverts and USB watchdog
off. A single private benchmark binary allowed changes without reflashing
between the decisive controls. Benchmark controls are not production features.

| Control | Capture | Time-weighted sleeping observations |
| --- | --- | --- |
| Original behavior | 30 s | 0% |
| Early clear only | 30 s | 0% |
| Non-yielding USB poll only | 30 s | 0% |
| Early clear plus USB poll, still with late state SVC | 30 s | 0% |
| No late SVC, non-yielding USB poll, without early clear | 30 s | 0% |
| Early clear, no late SVC, non-yielding USB poll | 90 s | 76.7% |
| Never-DTR client held open, old empty USB poll | 30 s | 0% |
| Same client held open, non-yielding USB poll | 30 s | 82.8% |

The first production candidate restored sleeping observations with SoftDevice
off (6522/8331 samples, about 78% over 90 s), but not with SoftDevice on
(0/8344 samples with a never-DTR client over 90 s; 0/2776 with the client closed
over 30 s). This negative result exposed the separate SDK history and is why
the final preparation also acknowledges a real PendSV before servicing work.
The second candidate added that acknowledgement but still did not sleep with
SoftDevice on. A breakpoint then confirmed the SDK branch, the actual S140
6.1.1 vector, and disabled IRQ42 pending (`ISER1=0x81`, `ISPR1=0x400`). Clearing
only that bit, with the same second binary and no configuration change,
restored 1911/2775 sleeping observations over 30 s. That control justified the
specific disabled-CryptoCell cleanup, rather than a speculative IRQ change.

The final, normal Full repeater artifact was then tested without benchmark
controls or a DTR bypass. Its application readback matched the packaged image
byte-for-byte (SHA-256
`8f6a1654f95d0179dad358d1fc68887eaacc41f4c60281a23099482e1f44af94`).

| Final production test | Capture | Sleeping samples | Weighted estimate |
| --- | --- | --- | --- |
| SoftDevice off, USB terminal closed | 90 s | 6526/8310 | 78.3% |
| SoftDevice on after OTA start/stop, never-DTR client held open | 90 s | 6077/8341 | 73.0% |
| SoftDevice on, client closed | 30 s | 1943/2776 | 70.0% |

The client received valid CLI replies both before and after the capture.
The final frozen SDK-on diagnostics had 281272 completed late waits, only
12 short returns, and zero vetoes/errors. Their accounting window was longer
than the capture; those counters are not its sleep-residency measurement.
All final samples had the same valid debugger/clock state described below.

The never-DTR client successfully issued `get powersaving` and held the same
file descriptor open across both captures. Early clearing and removal of the
late state SVC were held constant in that pair, isolating the USB poll change.
A separate unsafe late-double-wait
control also slept, but was rejected because of the lost-work race above.

Samples read DHCSR S_SLEEP at jittered 7-13 ms intervals. All causal captures
had C_DEBUGEN set, HFCLKSTAT 0x10001 and DCDC baseline 1, without sampled halt,
step, mask, lockup or reset flags. All timestamps were monotonic with no gap
over 20 ms. Percentages weight each sampled state until the next timestamp;
they are point-observation estimates, not exact sleep-residency accounting.
Zero sleeping observations does not exclude extremely short sleeps. Different
calibration phases can contribute to variation between otherwise fixed captures.

## Limits

There was no current meter. These results establish the CPU-sleep regression,
not a measured mA reduction or a complete explanation of the T096/Wio L1
screenshots. SWD and USB alter the power/clock environment. Radio RXPS, FEM/LNA,
GPS, screen, regulator and USB clock loads require matched electrical tests.
In particular the T096 FEM/LNA default differs from EasySkyMesh; this RAK
cannot measure that board-specific load. No RF delivery or long battery-life
claim follows from these captures.

See [power-saving diagnostics](power_saving_diagnostics.md) for the permanent
on-demand CLI counters. Counters remain off by default. Wait-call counters
cover the late eligible wait, not the guaranteed-nonblocking early SDK
acknowledgement. Preparation failures are reported through the board error or
veto path instead.
