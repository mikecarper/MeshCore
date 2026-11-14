<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/nrf52_bootloader_previews/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Additional nRF52 bootloader previews

[Download the matching MeshCore firmware](https://github.com/mikecarper/MeshCore/releases/tag/nrf52-bootloader-preview-v1.18.0.2-9d961a19)
for your exact board, role, and sensor variant. These same firmware images are
also included in the 1.18.0.2 release. The bundled bootloader remains a preview.

[OTAFIX 2.4.13-preview.1](https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/v0.11.0-OTAFIX2.4.13-preview.1)
has exact-board images for Heltec Mesh Solar, LilyGo T-Echo Card and T-Impulse
Plus, Seeed MeshTracker X1, Nano G2 Ultra, and Muzi Base Uno and Duo. The two
Muzi models use the same bootloader image. These builds passed software checks;
they have not been tested on those physical devices by this project.

Repeater and Room firmware variants labelled `with-bootloader-2.4.13-preview.1`
carry the matching bootloader. After startup they check once for a compatible
installed bootloader and update a recognized older one. They preserve the
ordinary firmware variants, and the exact-board and flash-layout checks remain
in force. An update can require SWD recovery if power is lost or hardware
behavior differs from the board sources.

The automatic check recognizes specific published factory images for Solar,
T-Echo Card, T-Impulse Plus, X1, and Nano G2 Ultra. It does not recognize every
possible factory revision. A matching factory bootloader hash has not been
established for Muzi Base, so its stock bootloader is not automatically
replaced. An unrecognized or recovery bootloader is left untouched; the
separate preview files and board-specific recovery route are still available.
Use `ota bootloader embedded status` and `get bootloader.ver` to check the
result on a compatible firmware build.

For Muzi Base Uno and Duo, the manufacturer's published bootloader source
supports the separate `update-muzi_base_bootloader-..._mbr.uf2` updater. To try
it, double-reset to show the `muzi-Base` USB drive, copy the exact Muzi updater
from the OTAFIX preview release, and let it finish. Then reinstall the matching
MeshCore firmware UF2 if needed. This route has not been physically tested on
these boards; it may require an application reinstall, and a failed bootloader
update can require SWD recovery.

ThinkNode M4, ThinkNode M8, and Wio WM1110 Dev Kit are not covered by these
images. Their board-specific bootloader identity or recovery path remains
unresolved. Do not substitute a bootloader intended for another board.
