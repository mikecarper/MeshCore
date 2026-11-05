<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/browser_radio_defaults/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Browser-editable primary radio defaults

Keymind applications built with this feature retain one 64-byte defaults record
in application flash. A compatible flasher can customize an **unsigned** image
before installing it. This does not replace stored settings on an ordinary
update. Use a fresh installation or factory reset to adopt different defaults;
use the radio's USB settings commands to change an already configured node.

The space-limited RAK 3x72, Wio E5, and Tiny Relay USB Companion builds, and
the Wio E5 Mini Repeater and USB Companion builds, omit the embedded defaults
record. Configure their radio settings over USB instead.
Their normal saved settings continue to work across reboots and updates.

The record changes only the primary LoRa frequency, bandwidth, spreading factor,
coding rate, optional TX power, and preamble. It does not alter identity, regions,
secondary radio, Bluetooth, Wi-Fi or other preferences. Companion, Repeater,
Room Server and Sensor share the decoder. The shipped record is disabled, so
unmodified firmware retains its existing compile-time defaults.

## Version 1 wire format

All multibyte integers are little-endian. The record is exactly 64 bytes; do not
use a native C/C++ struct to serialize it.

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 16 | ASCII `MCRDEFAULTS0001` followed by NUL |
| 16 | 2 | Format version: `1` |
| 18 | 2 | Record length: `64` |
| 20 | 4 | Flags: bit 0 enables the record; bit 1 enables TX override |
| 24 | 4 | Primary frequency in Hz |
| 28 | 4 | Bandwidth in Hz |
| 32 | 1 | Spreading factor |
| 33 | 1 | Coding-rate denominator |
| 34 | 1 | Signed two's-complement TX dBm; zero without TX override |
| 35 | 1 | Reserved: zero |
| 36 | 2 | Preamble symbols; zero selects the existing automatic policy |
| 38 | 22 | Reserved: all zero |
| 60 | 4 | IEEE CRC32 of bytes 0–59, initial/final XOR `0xffffffff` |

The disabled template has flags and all bytes 24–59 zero. Its CRC is
`0x893773a6` (on-wire `a6 73 37 89`). Unknown versions, length, flags, reserved
values, corrupt CRCs and unsupported tuples are ignored as a whole; firmware
does not partially adopt a record. TX override without the enable flag is invalid.

Enabled records require frequency 150–2500 MHz, SF 5–12, CR 5–8, preamble zero
or 8–65528 symbols, and one of these bandwidths in Hz:

`7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000`.

An explicit TX override must be −9 through 40 dBm. The active board's existing
power bounds may clamp it further. Existing radio-family validation can reject
a generally valid tuple (for example SF5 on SX127x or 31.25 kHz on LR1110).
Dual-radio/preamble feasibility and enabled RX power-saving timing checks still
apply. Frequency is converted through the existing floating-point radio API;
the hardware's normal frequency resolution is unchanged.

## Startup and persistence

The decoder reads const flash storage through a `const volatile` byte pointer,
rather than compiler-folded initializers. This keeps the storage genuinely
read-only while requiring live reads even with LTO. The single record is in
`.rodata.mesh_radio_defaults`; only
applications that reference the startup hooks retain it with linker GC.

Embedded defaults are considered only after preference loading and before the
first normal radio apply/mesh loop. Every existing, backup, temporary or pending
radio-preference file prevents seeding, including files from another supported
role. A metadata/read failure is not proof of a fresh installation. Companion
also refuses volatile/unavailable storage or uncertain secondary authority.

Accepted fresh values are saved through the existing checked preference writers.
Infrastructure keeps tuple and preamble together in `/com_prefs`. Companion uses
its existing `/new_prefs` tuple and `/radio_profiles` preamble stores; a failed
seed restores both targets. If that compensation cannot be verified, RF stays
paused for the boot and USB reports the storage problem. No preference wire
offsets change. Saved settings, including a saved automatic preamble, remain
authoritative on later boots and firmware updates.

## Flasher requirements

Validate the original container first. Find exactly one complete supported
record in the application body—not a bootloader, erase utility, or unrelated
data segment. Refuse missing/duplicate markers, signed/encrypted images, or
ambiguous multipart installations. Patch a fresh copy, recalculate its record
CRC, and repair all relevant outer integrity fields before existing package
validation runs again. These can include ESP image checksum/appended SHA-256,
EndF body hash, nRF DFU DAT/manifest payload CRC, ZIP CRCs, and the flasher's own
SHA-256 binding. Never bypass those checks or describe customized bytes as the
original downloaded hash. Do not modify the build identity/version text.

Hardware-free regression tests:

```sh
python -B test/test_browser_radio_defaults.py -v
```

They cover the portable decoder, disabled linked template and unique marker,
fresh-store admission, and startup/save/rollback behavior without a radio.
