<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/sx1262_agc_sleep_retention_2026-11-12/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# SX1262 AGC protection through sleep

Verified on a Mercerwood Pi Heltec V4.3 OLED on 12 November 2026. The test
image used source `28e336e2` plus this fix and a temporary local register probe.

## Fault and fix

Before the fix, register `0x08B5` changed from `0x05` to `0x04` after warm
sleep, RX duty cycling and the new scheduled receive refresh. Bit 0 enables
the AGC lockup protection. The scheduled refresh cleared it even with the
ordinary `radio.rxps` setting off.

The fix includes `0x08B5` and RX gain register `0x08AC` in the chip's sleep
retention list. Runtime gain changes and the old full AGC reset merge these
entries rather than replacing the list. Initialization checks register reads,
writes and readback. Fast profile switching and each automatic sleep cycle
do not gain additional host SPI commands.

## Hardware results

Radio: 909.950 MHz, BW 250 kHz, SF5, CR5; radio2 off; CPU power saving on.
All listed register reads succeeded.

| Check | Result |
| --- | --- |
| Warm sleep and wake | 10/10 retained `0x05` |
| RX duty cycling | 10/10 retained `0x05` |
| Old full AGC reset | 10/10 retained `0x05` |
| Hard radio recovery | 10/10 retained `0x05` |
| Runtime gain off and on | 10/10 each retained `0x05` |
| Ordinary RXPS, reduced gain | Armed, then left untouched for 10 seconds; patch `0x05`, gain `0x94` afterward |
| Ordinary RXPS, boosted gain | Armed, then left untouched for 10 seconds; patch `0x05`, gain `0x96` afterward |
| New AGC, ordinary RXPS off | Two normal 28-second refreshes during a 70-second observation; patch stayed `0x05`; success counter 3 to 5, failures 0 |

The chip reported two retained addresses: `0x08B5` and `0x08AC`.
Ordinary RXPS checks waited for `effective=armed` after noise calibration.
The ten-second RXPS windows and the 70-second AGC window contained no SPI
register probes, so the host did not wake the radio during those observations.
The original radio, RXPS, gain, power-saving and AGC settings were restored.

This verifies register retention on the tested V4.3. It is not a blocker
immunity, sensitivity or packet-loss measurement, and other boards were not
physically tested in this run. The change applies to builds that already
enable `SX126X_REGISTER_PATCH`.

## Regression coverage

`test/test_sx126x_agc_retention.py` exercises the real helpers, runtime gain
setter and old-reset helper with register loss, repeated toggles, unrelated
retention entries, full/malformed lists, every SPI failure point and silent
write failures. Timing diagnostics also verify that failed patch reads do
not cause writes. These tests run in the unit-test workflow.

The targeted host checks passed. RAK3401 no-external-sensor Full and Reduced
LoRa OTA builds also passed qualification. Private bench evidence is under
`/home/mikec/hwtest/v4-agc-retention-20261112` on the Pi.
