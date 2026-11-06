<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/research/otafix_rak_provenance/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# RAK OTAFIX release provenance

The [RAK inventory](../_data/otafix_rak_inventory.json) records published
RAK3401 and RAK4631 ZIPs, their release hashes, bootloader identity records,
capabilities and source commits. It is evidence for selecting a matching
profile, not a list of approved emulators.

On 6 November 2026, the release ZIPs were downloaded and their SHA-256 digests
checked against GitHub's asset metadata. The Legacy DFU package CRC and
bootloader manifest identity CRC were checked against the contained bytes.
The separate published 2.4.11 and 2.4.12 manifest checksums were also checked.
For all 19 listed profiles, the ZIP bytes matched the corresponding downloaded
GitHub Actions build artifact from the exact-tag run:

| Version | Exact source commit | Build run | Matching RAK ZIPs |
| --- | --- | --- | ---: |
| 2.4.3 | `243b061f60130b8f2d0b930af6c49955be8ca537` | [33023653687](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/actions/runs/33023653687) | 3 |
| 2.4.6 | `250559c2cc7087066e9920fbeafbca5aacba0ea4` | [33992368624](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/actions/runs/33992368624) | 5 |
| 2.4.9 | `46d89a6f550b97053d6a44f55f41d371ec19d68c` | [36355397235](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/actions/runs/36355397235) | 7 |
| 2.4.11 | `c63b00d88ea9322c9d1c73a8dd505550f479c5fe` | [37066565552](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/actions/runs/37066565552) | 2 |
| 2.4.12 | `28e098afa330aa7c5507404b00213e9e4a06ba41` | [39904580496](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/actions/runs/39904580496) | 2 |

The two 2.4.9 adaptive ZIPs were renamed to include
`NO_LORA_BOOTLOADER_UPDATE` after their builds; the file content was unchanged.
The 2.4.11 release notes also link a separate run for a different unprefixed
tag and commit; that run is not the evidence in this table.

An independent rebuild of both 2.4.12 RAK profiles used the exact tagged
source and root submodule commits, Arm GNU 15.3.Rel1 and
`SOURCE_DATE_EPOCH=1792732400`. Both 40 KiB bootloader regions matched their
published images byte for byte:

- RAK3401: `57df468b525fe6ba3c1f6c756b9a85ad3875e845f080671201411e8044da05c6`
- RAK4631: `ec860fb46697ac4b458f4b961dec69b428380be1ce31a226cff60298f3281545`

All three uncompressed Legacy DFU ZIP members matched as well. The ZIP archives
differed: their entry timestamps recorded their respective build times. The
source-tag relationship for the older releases above rests on the matching
Actions artifacts; no independent rebuild was performed for them.

The 2.4.12 workflow requires an exact clean tag, builds each board using the
board-qualified Arm GNU 15.3.Rel1 compiler, and sets `SOURCE_DATE_EPOCH` to
`1792732400`. The matching Actions artifacts establish what that run produced;
they do not by themselves prove a separately compiled emulator reproduces the
bootloader's behavior. Bootloader version alone is insufficient for identity:
board, device name, manifest CRC and capability record must also match.
