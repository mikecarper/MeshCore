# USB keyboard console

Experimental Full Companion builds can show a command box at the bottom of a
screen when a USB keyboard is connected. Press a navigation button (or
tap the screen) to look for a keyboard for 30 seconds. The USB host stays on
while a keyboard is attached and for 30 seconds after it is disconnected.
Enter runs the normal Companion
terminal command; the most recent replies appear above the box. Escape hides or
shows the box without losing the draft; while hidden, keys other than Escape
are ignored. Arrow keys edit
the line and scroll replies; Home, End, Delete, Backspace and Ctrl-U also work.
The keyboard layout is US English. E-paper redraws are throttled.

These experimental profiles require testing with the powered adapter and
keyboard on each board:

| Board | Build target |
| --- | --- |
| Heltec V4.2/V4.3 with OLED, FEM on | `heltec_v4_2_v4_3_companion_radio_full_keyboard` |
| Heltec V4.3 with OLED, FEM off | `heltec_v4_3_companion_radio_full_keyboard` |
| Heltec V4 R8 with OLED | `heltec_v4_r8_companion_radio_full_keyboard` |
| Heltec V4 with TFT expansion | `heltec_v4_tft_companion_radio_full_keyboard` |
| Heltec V4.3 with TFT expansion | `heltec_v4_3_tft_companion_radio_full_keyboard` |
| Heltec V4 R8 with TFT expansion | `heltec_v4_r8_tft_companion_radio_full_keyboard` |
| Heltec Vision Master T190 | `Heltec_T190_companion_radio_full_keyboard` |
| Heltec Vision Master E213 | `Heltec_E213_companion_radio_full_keyboard` |
| Heltec Vision Master E290 | `Heltec_E290_companion_radio_full_keyboard` |
| LilyGO T-Deck | `LilyGo_TDeck_companion_radio_full_keyboard` |

Build one explicitly with `bash build.sh build-firmware <target>`. These
profiles are excluded from the normal release matrix until hardware-qualified.
The T190 needs its optional LoRa module for mesh operation.
The T-Deck profile adds an external USB keyboard; its built-in keyboard is unchanged.
The Wio Tracker L2 needs separate USB-C power-role verification before it can
be added.

Use a wired USB HID boot-protocol keyboard and a **powered USB-C OTG adapter**
that supplies 5 V to both the board and keyboard while passing keyboard data to
the board. Adapter behavior varies; the board schematics do not establish that
any particular adapter works. USB hubs and non-boot-protocol keyboards are not
supported by this build. The V4 OLED can show four compact output lines and
the command box.

The ESP32-S3 has one native USB PHY. A keyboard profile dedicates that port to
the keyboard for the whole boot, so USB Companion, USB logging, and the USB OTA
folder source are unavailable. Bluetooth and Wi-Fi remain available. To go
back to ordinary USB operation, enter the board's ROM download mode with BOOT
and RESET, then flash a normal Companion image.

The USB host prevents light sleep while it is scanning or a keyboard is
connected. After the window expires, normal power-saving behavior resumes.
Firmware cannot switch off 5 V supplied separately by the adapter, so the
adapter or an attached keyboard may still draw power.
