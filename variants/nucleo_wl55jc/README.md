# Nucleo WL55JC1

Build `pio run -e nucleo_wl55jc_kiss_modem` for the integrated STM32WL radio and
ST-LINK virtual serial port. The variant retains the core's front-end RF switch
mapping, 1.7 V TCXO setting, and active-high blue TX LED.

The imported upstream Repeater, Companion and Room environments are deferred
until they fit this fork's 224 KiB application budget. The Room build exceeded
flash by 59,196 bytes after GCC 14 / `-Oz` / LTO optimization. No application or
filesystem boundary was enlarged to accommodate it.

Compilation does not establish radio performance or hardware compatibility;
no board was flashed during this integration.
