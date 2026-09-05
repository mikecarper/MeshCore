# Classic ESP32 image memory budget

Classic ESP32 has 320 KiB of internal DRAM, but at most 160 KiB can hold
statically allocated data. The remaining DRAM is available only through the
runtime heap. Bluetooth, tracing, and SDK reservations further constrain the
usable region. See Espressif's [memory types documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/memory-types.html#dram-data-ram).

An image can therefore approach its static limit while PlatformIO reports
less than 40% of the 320 KiB total in use. PSRAM does not expand the region used
by ordinary internal `.data` and `.bss` allocations.

## Build enforcement

Every classic ESP32 environment inheriting `esp32_base` runs
`scripts/check_esp32_dram.py`. The release builder also adds the script when
an ESP32 profile replaces the inherited extra scripts. The check runs before
image generation, merging, and uploading, including cached `nobuild` uploads.
It requires the ELF and linker map to be present for those cached operations.
The check script must load after `merge-bin.py` so the merge target retains its
pre-action. A merge also requires the bootloader/partition flash layout;
`nobuild` cannot create a bootable merged image when that layout is absent.

The check reads `dram0_0_seg` from the final linker map. SDK reservations are
already reflected in that region; Bluetooth's reservation is not subtracted
a second time. The occupied span runs from the region origin to `_heap_start`,
including `.data`, `.bss`, `.noinit`, and alignment gaps. The allowed region is
the smaller of the linker's region and 160 KiB.

Builds must leave at least **8 KiB free within that static region**. This is
an additional project margin against near-full images, not an Espressif
hardware limit or a prediction of free heap. A profile may require a larger
margin with `custom_esp32_static_dram_reserve` in bytes; values below 8192 are
rejected. Missing or unrecognized map boundaries also fail the check.

For the Heltec V2 build used to investigate the repeater report:

| Shared repeater defaults | Static span | Static region | Remaining | Result |
| --- | ---: | ---: | ---: | --- |
| Previous 255 scope slots | 124,020 B | 124,580 B | 560 B | Rejected |
| Tuned 31 scope slots | 108,308 B | 124,580 B | 16,272 B | Passed |

These measurements remove the board's existing scope-capacity override to
exercise the shared defaults. The normal Heltec V2 profile already used 31
scope slots. Exact sizes vary with build flags and SDK versions.

To inspect an existing classic ESP32 build:

```sh
python3 scripts/check_esp32_dram.py .pio/build/Heltec_v2_repeater/firmware.map
python3 test/test_esp32_dram.py
```

ESP32-S2, S3, and C-series chips have different memory maps and do not inherit
this classic ESP32 check.

## Boot versus running memory

The build gate checks static placement needed to load and start the image.
It never counts heap-only RAM toward that static budget. Allocations made with
`new` or `malloc` in global constructors consume heap and are not included in
the static span. Moving a table there changes its placement, not total RAM
consumption.

Successful build checks do not prove that every runtime configuration will
boot. Hardware validation must still cover startup allocations and the enabled
Wi-Fi/Bluetooth services, recording minimum free internal heap and the largest
free internal block. Available heap changes during initialization; for example,
the Arduino 2.x repeater releases unused Bluetooth controller RAM in
`initArduino()`, after global constructors have run.
