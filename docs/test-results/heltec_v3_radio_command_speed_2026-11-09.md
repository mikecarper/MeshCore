<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/heltec_v3_radio_command_speed_2026-11-09/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# V3 resident radio commands — 9 November 2026

Heltec V3 enables the additional RadioLib and Arduino SPI code
placement [measured on V4](heltec_v4_radio_command_speed_2026-11-09.md) for Heltec V3. This extends the
[earlier V3 retune improvement](heltec_v3_radio2_switch_speed_2026-11-09.md).
The extra internal RAM cost is **2,704 bytes**. All existing contacts,
queue frames, advert records, OTA workspace and features are retained; V3
still has no PSRAM. SPI remains 8 MHz by default and power-saving policy is
unchanged.

**Not release-qualified.** The initial RF test received 234/240 packets. A focused
comparison received 199/200 on this image versus 200/200 on the earlier image.
That sample does not establish the cause or rule out a reception regression.
The implementation was promoted to `keymindCascade` at the user's request with
these results recorded. The speed improvement is enabled by default; the
packet-loss qualification remains unfinished.

## Fresh before/after measurements

One physical V3, Full Companion, identical source except for enabling the two
resident-command/SPI build flags. Both builds use the earlier resident retune
path and the same RTC storage allocation. CPU clocks were selected explicitly
with power saving off. AGC was 16 seconds and old maintenance off. Each profile has two quiet 20-second windows with no CLI getters inside.
Values are weighted means from admission through receive restart.

| CPU MHz | Change | Earlier fast path | Additional resident commands | Improvement |
|---|---|---:|---:|---:|
| 240 | Frequency only | 588 us | **383 us** | 35% |
| 240 | Frequency + modem | 713 us | **499 us** | 30% |
| 80 | Frequency only | 781 us | **570 us** | 27% |
| 80 | Frequency + modem | 965 us | **744 us** | 23% |

No switch-error increments occurred in these windows. At 240 MHz, cache misses
fell from approximately 176/182 to 72/73 per switch for frequency/mixed changes.
That counter covers both CPUs, so it is supporting evidence, not per-task attribution. The selected command,
GPIO and SPI function groups were verified in IRAM in every final ELF. Radio
commands, waits, locks, receive preservation and recovery are unchanged.

Timing used 906.525/907.525 MHz, BW62.5/SF7/CR5/preamble32 for matching profiles,
and BW500/SF5/CR5/preamble200 for the mixed secondary. Timing windows did not
transmit RF probes. Private timing counters were omitted from normal firmware.

## Normal builds and packet checks

| Full image | Internal RAM available | Required including reserve | Remaining margin |
|---|---:|---:|---:|
| Companion | 187,928 | 185,632 | 2,296 |
| Combined MQTT Repeater | 210,416 | 208,820 | 1,596 |
| Combined MQTT Room | 217,360 | 202,528 | 14,832 |

All three builds passed capability, partition-size and RAM gates. These are
build-budget figures, not measured maximum network-load headroom. Repeater and
Room were not physically tested here.

The initial zero-loss matrix **failed**: normal firmware received **234/240 packets** across eight windows: 30 packets per
profile, at both 80 and 240 MHz, with matching and mixed modem settings. Payloads
alternated 40/160 bytes, with no automatic RF retries. The fast mixed secondary
used a **512-symbol preamble**; the matching profiles used 32. This does not
qualify shorter fast-profile preambles. Individual counts and any failures are
preserved in the [machine-readable report](heltec_v3_radio_command_speed_2026-11-09.safe.json). There were no
corrupt payloads, duplicates or switch-error increments; RX counters agreed with
the missing packet counts. **Packet-loss qualification remains open.**
A focused comparison repeated only the affected frequency-only settings (80 MHz
secondary and 240 MHz primary), 100 packets each, on this normal image and the
previous normal `v1.18.0.2-v3-fast-9303dc51`. Those additional results are recorded
separately below; they do not replace the original losses or establish their cause.

| Image | CPU MHz | Receive profile | Received / sent |
|---|---:|---|---:|
| v1.18.0.2-v3-radio-fast-8fc27534 (Build: 09-Nov-2026) | 80 | Secondary | 100/100 |
| v1.18.0.2-v3-radio-fast-8fc27534 (Build: 09-Nov-2026) | 240 | Primary | 99/100 |
| v1.18.0.2-v3-fast-9303dc51 (Build: 09-Nov-2026) | 80 | Secondary | 100/100 |
| v1.18.0.2-v3-fast-9303dc51 (Build: 09-Nov-2026) | 240 | Primary | 100/100 |

The cumulative switching maximum during the initial RF traffic was **3,449 us**; faster
averages do not remove occasional scheduler/interrupt delays.

The test V3 is left on experimental Full Companion `v1.18.0.2-v3-radio-fast-8fc27534`, with the original
identity and saved/runtime settings verified. The XIAO S3 witness's settings
were restored. Every flash verified the partition table, wrote only the
application and read back identical bytes. The final normal image was reinstalled
after the baseline comparison and its settings checked; previously completed
RF windows were not rerun. Reserved recovery radios were untouched.

Host validation: 21 focused driver/storage tests, seven documentation-link tests
and the full build-profile suite passed. These do not override the failed RF
qualification above.
