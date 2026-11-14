# Additional nRF52 bootloader previews

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

ThinkNode M4, ThinkNode M8, and Wio WM1110 Dev Kit are not covered by these
images. Their board-specific bootloader identity or recovery path remains
unresolved. Do not substitute a bootloader intended for another board.
