<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/test-results/browser_multihop_ota_2026-11-10/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Browser multihop LoRa OTA, 10 November 2026

Status: **Two differential updates installed and verified on a RAK3401.**
This report qualifies the named laboratory builds and browser fixes. It does
not qualify the final 1.18.0.2 release or its complete build matrix.

## Test path and scope

The fixture used the following routed path on Mercerwood Pi:

```text
Browser -> SSH serial shim -> Heltec V4.2 source
    -> LoRa at 909.500 MHz -> Heltec V4.3 relay
    -> ESP-NOW -> SenseCAP Indicator relay
    -> LoRa at 908.500 MHz -> RAK3401 target
```

The two LoRa legs used 500 kHz bandwidth, SF5, coding rate 4/5, and a
128-symbol preamble. Configured TX power was -9 dBm. Antennas were attached.
ESP-NOW emulated the intermediate hop between separate LoRa frequencies.

The browser ran a local copy of the OTA page with an SSH serial shim and a
laboratory-only exception to the shared-frequency preflight check. Therefore
this test does not validate native browser Web Serial or the production
shared-frequency preflight. The production setup still requires one common
temporary LoRa frequency; no ESP-NOW-specific setup was added to that flow.

Authenticated routed management commands were verified before transfer.
Successful management replies alone were not counted as firmware transfer.
Download completion, installation, and post-reboot image verification were
checked separately. The test packages were unsigned.

## Builds and browser fixes

The source used a Heltec V4 Repeater build from `2a30b7ed5` on the spare V4.2.
The two relays used existing dedicated bridge test builds. The first target
baseline was the reduced RAK3401 OTA application from `298963f9`.

Before the larger transfer, the target received a reduced application from
`0027b0d9d`, labelled `v1.18.0.4-multihop-hil`, using application-only USB DFU.
This enabled the new read-only bootloader RAM diagnostic. It preserved the
installed bootloader and provided the exact base image for the second delta.

The browser changes exercised here include catalog pagination (`4170220b7`)
and the minute-boundary clock-read retry (`8bb380aa7`), together with the
bootloader RAM capability check introduced in `0027b0d9d`. Test version labels
identify bench images; they are not published release versions.

## Confirmed results

| Differential payload | Target-confirmed blocks | Installed test version | Reported image hash prefix |
| --- | --- | --- | --- |
| 2,310 bytes | 3/3, 100% | 1.18.0.3 | `8B893821F9A8B12D` |
| 51,352 bytes | 51/51, 100% | 1.18.0.5 | `08F5AC1A304CCBF1` |

Both destination images were built from `2a30b7ed5` for the same reduced
RAK3401 OTA identity and flash layout. The larger download completed in
155 seconds according to the target. This timing excludes package generation,
route checks, installation, and post-reboot verification.

For both updates, the browser confirmed the expected running firmware after
reboot. An independent USB read then confirmed the installed version and
reported image hash. The bootloader remained
`OTAFIX2.4.12-preview.142`, with CRC `FC79EAE0` unchanged. After the larger
update it reported apply ABI 3 and success code `blrc:B8`.

The target had no active download or staged update after verification. The
source's temporary OTA route, peer session, and browser-backed folder were
off, and the serial ports were released. A separately cached source catalog
entry remained; this was not an attached browser folder or an active update.

## Faults reproduced and corrected

The initial attempt failed with "Target did not discover the selected
package" before any download or installation. A direct target read proved
that the selected package was already on page 2 of `ota ls`; page 1 contained
the two relay images. The browser had searched only page 1. After the fix,
the browser searched the advertised pages, found the package, and completed
the three-block update. The search remains bounded and validates page
numbers and complete package IDs.

The larger generated delta required proof of the installed bootloader's
hybrid retained-RAM support. The browser queried the routed target and
received:

```text
OTA RAM: hybrid=1 abi=1 record=72 arena=65536
```

This readout comes from the installed bootloader capability marker, rather
than a board-name or version assumption. The exact response passed the
browser gate for the 51,352-byte package, which then transferred and installed
successfully. Unknown or unsupported capability still stops that path.

A separate preparation failure was traced to a minute-only clock display:
the source was about one to two seconds behind, but briefly still displayed
the previous minute. Bounded read-only retries resolved this boundary case
without widening the accepted clock limits. A downgrade candidate was also
rejected during preparation; the subsequent larger test used the newer
1.18.0.5 label and retained the version check.

Progress messages were corrected to distinguish route checks, source
preparation, package discovery, and target-confirmed download progress.
Reading firmware bytes at the source is not presented as proof that the
target received them.
