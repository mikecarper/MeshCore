# Amplified E22 module selection

The module marking matters. New M33S profiles and SolarXiao 33S limit the
radio chip drive to 8 dBm; existing Ikoka Nano/Stick 33 dBm profiles retain
their 9 dBm ceiling. These are amplifier input levels, not radiated output.
Do not select an ordinary 22 dBm or M30S image for an M33S module. Detection
of SX1262 versus SX1268 cannot identify an external amplifier or its safe
input level.

These dedicated hardware prefixes keep the drive limit in every published
role, including Companion and KISS. Repeater and Room Server releases retain
the matching Full/Reduced sensor and LoRa OTA choices where supported.

| Carrier wiring | 900 MHz M33S prefix | 400 MHz M33S prefix |
| --- | --- | --- |
| SolarXiao | `solarxiao_33S` | `solarxiao_400M33S` |
| Ikoka Nano nRF52 | `ikoka_nano_nrf_33dbm` | `ikoka_nano_nrf_400m33s` |
| Ikoka Stick nRF52 | `ikoka_stick_nrf_33dbm` | `ikoka_stick_nrf_400m33s` |
| Ikoka Handheld nRF52 | `ikoka_handheld_nrf_e22_33dbm` | `ikoka_handheld_nrf_e22_400m33s` |
| ProMicro nRF52840 | `ProMicro_m33s` | `ProMicro_400m33s` |
| XIAO nRF52840 | `Xiao_nrf52_m33s` | `Xiao_nrf52_400m33s` |
| Generic E22 ESP32 pinout | `Generic_E22_m33s` | Same image, detected SX1268 |
| Meshadventurer ESP32 pinout | `Meshadventurer_m33s` | Same image, detected SX1268 |

The existing Ikoka Nano/Stick 900 MHz M33S images retain their 9 dBm ceiling.
All newly added M33S prefixes use the more conservative 8 dBm default and
ceiling. Saved settings above the ceiling are clamped before hardware writes.

The named 400 MHz profiles start at 433 MHz and are separate from 900 MHz
application/OTA identities. USA Cascade release selection does not replace
their default with 910.525 MHz. The automatic ESP32 profiles detect the radio
family, start at 433 MHz for SX1268, and retain their
M33S drive ceiling on either chip. Set the frequency for your local network
and permitted band after installation.

The bootloader is shared only where the MCU, flash wiring and application
layout match. The firmware picker maps the ProMicro profiles to the existing
ProMicro loader, and the XIAO-based profiles to the XIAO loader. Application
hardware and MOTA target IDs remain distinct; sharing a bootloader does not
make an M30S application safe for M33S hardware.

MTL-1, MTL-2 and Ebytastic installations need their actual wiring checked
against the selected profile. These are matching radio-pin profiles, not a
claim that every carrier's display, GPS, battery monitor or other peripheral
is qualified. For example, a ProMicro radio pin match does not establish
Ebytastic peripheral compatibility.

Module and carrier references:

- [SolarXiao maker instructions](https://github.com/wehooper4/Meshtastic-Hardware/blob/main/XaioSeries/SolarXaio/readme.md)
- [Ikoka Handheld module choices](https://github.com/ndoo/ikoka-handheld-meshtastic-device#lora-module)
- [MTL-2 hardware](https://github.com/kt314-sh/MTL-2)
- [Ebytastic hardware](https://github.com/Sobollion/Ebytastic)

A source-tree fix protects devices only after matching updated firmware is
installed. A low-output report alone does not establish amplifier damage.
