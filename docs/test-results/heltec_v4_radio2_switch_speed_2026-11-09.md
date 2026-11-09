# Heltec V4 radio2 switching speed — 9 November 2026

The existing SX1262 driver is retained. Keeping its short retune path in internal
instruction RAM removes much of the flash-cache delay seen between ordinary
application-loop iterations. It costs about **5 KiB of internal RAM**, preserves
the radio commands and receive/error/rollback checks, and is enabled for V4 and
V4 R8 builds. Other boards keep their existing code placement.

## Measured switching time

Physical Heltec V4.3 OLED, Full Companion, default fast-driver features enabled.
Values are mean microseconds from admission through receive restart. Each row
uses two 20-second windows per profile unless noted.

| Configuration | Frequency only | Frequency and modem |
|---|---:|---:|
| Previous driver, 240 MHz CPU / 8 MHz SPI | 854 | 985 |
| Control: new literal placement, resident code off | 837 | 968 |
| Resident code on, 240 MHz / 8 MHz | **577** | **702** |
| Resident code on, 80 MHz / 8 MHz, one window | 771 | 952 |
| Resident code on, 240 MHz / 8.889 MHz, one window | 570 | 697 |

The controlled improvement is **31% / 27%**. Instruction-cache misses per switch
fell from 300 to 174 for frequency-only changes and from 309 to 178 for mixed
changes. The hardware counter covers both CPUs, so it is supporting evidence,
not per-task attribution. ELF inspection verifies the selected driver functions
reside in IRAM. Changing the V4 from HSPI to FSPI did not improve timing; normal
bus ownership remains intact.

Both profiles used isolated 906.525/907.525 MHz bench channels. Frequency-only
used BW62.5/SF7/CR5/preamble32 on both. Mixed changes used
BW500/SF5/CR5/preamble200 on the secondary. No CLI getters ran inside the quiet
20-second windows; all stage sums and counts were read before and afterward.
There were no switch-error increments. These are whole-switch averages, not
claims about minimum latency or receiver sensitivity. The older October result
used a different role; this investigation does not identify a first bad commit.

## Memory and power tradeoffs

The matched diagnostic builds use 5,136 more internal bytes with resident code.
240 MHz remains faster than 80 MHz, at higher CPU power consumption; this test
measured timing, not current draw. The roughly 1% SPI improvement is small and
was measured in only one window per profile. Default SPI remains 8 MHz.

Full V4 repeaters use their onboard PSRAM for the existing complete scope and
advert tables. This is the existing required-PSRAM allocation/recovery path,
not a table-capacity reduction. It removes **36,828 bytes of internal fallback
reservation**; it does not claim that this much additional data moved at runtime,
since healthy boards already preferred PSRAM. The Full V4 Repeater now has
234,632 internal bytes available against a 201,652-byte requirement, including
the existing transient margin. Missing or failed PSRAM stops LoRa startup while
keeping USB recovery available. Standard profiles and other roles retain their
existing PSRAM policy. The physical Companion reported about 1.9 MB free PSRAM.

## Validation

- Driver, receive-preservation, failure/rollback, IRQ, clock, RXPS and diagnostic
  tests: 82 passed. PSRAM allocation/recovery, build selection and RAM tests:
  87 passed, including missing-memory and allocation-failure cases.
- Full V4 Companion, Full V4 Repeater, Full V4 Room and V4 R8 TFT Full Repeater
  build checks pass. The R8 check used the stricter internal-fallback budget
  before required-PSRAM selection was extended to R8.
- Normal firmware received **120/120** unique probes, alternating 40/160-byte
  payloads, with 30 packets on each frequency at both 80 and 240 MHz. No retries,
  duplicates or corrupt payloads; per-profile RX counters confirmed delivery.
  During this campaign the cumulative switch maximum reached **3,646 us**
  (at 80 MHz), so the timing averages are not a worst-case latency guarantee.
  The 240 MHz block inherited that cumulative maximum; it is not a separate
  maximum measurement for that clock.
- Physical packet results and final restoration are recorded in the accompanying
  [machine-readable report](heltec_v4_radio2_switch_speed_2026-11-09.safe.json).
  Physical testing covers one V4 OLED; infrastructure roles and R8 TFT were
  build-checked, not physically qualified in this campaign.

Timing used private, compile-time HIL counters. The final packet-test image is
normal firmware without those counters or diagnostic commands. Tests preserve
identity and saved settings and use only application-partition writes. The
reserved recovery radios and their services were not touched.
