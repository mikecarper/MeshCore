# nRF52 Bluetooth DFU handoff safety

The application-to-bootloader transition has two independent hazards in the
pinned Adafruit Bluefruit `BLEDfu.cpp`. Updating the phone app alone cannot
repair a callback already installed on a radio.

## Stack transition

The original callback changes `CONTROL` to select MSP while its C frame is
still on the FreeRTOS process stack. Optimized compiler output can restore
that frame from the wrong stack before reaching the bootloader.

The private build patch uses a naked, nonreturning assembly helper. It clears
the FreeRTOS system exceptions and both external IRQ banks before the final
transition. The helper switches stacks without a C epilogue and preserves the
legacy B1/retained-peer bootloader protocol.

## Retained peer data must not overwrite the live application

Bluefruit originally constructs the bootloader's peer record directly at
`0x20007F80`, before calling `loadBondKey()`. That address belongs to the
bootloader only after the handoff; it is not reserved by the application linker.

In the reproduced Tower standard BLE image, `shared_flash_lock_storage`
occupied `0x20007F6C..0x20007FBC`. Preparing the peer record overwrote that live
mutex immediately before the bond read needed it. The phone's DFU control
write timed out while the application remained present over USB. This layout
dependency can make the same callback appear to work in one build and fail in
another board, feature profile, or compiler layout.

The callback now stages the record on its own stack. All bond, SoftDevice, and
application operations complete before the nonreturning helper masks
interrupts and copies the exact 62-byte retained ABI: 60 data bytes plus the
two-byte CRC. No application C, flash, or scheduler call follows that copy.
The patch also rejects an empty control write before inspecting its opcode.

This does not change the bond record format, erase bonds, install a
bootloader, or bypass a firmware package check. The patch changes a private
build copy, never PlatformIO's shared framework cache.

## Regression and build gates

Run the regression with the ARM compiler and Cortex-M emulator required:

```sh
MESHCORE_REQUIRE_ARM_COMPILER=1 MESHCORE_REQUIRE_DFU_EMULATOR=1 \
  python3 test/test_nrf52_ble_dfu_fix.py -v
```

The test compiles `-Os`, `-Oz`, and `-Oz` with LTO, then executes the handoff
using Unicorn. It verifies that the live retained-address contents remain
unchanged until entry into the helper, every peer write occurs with interrupts
masked, adjacent bytes are untouched, the exact peer record reaches the
bootloader, and both thread stack modes and the handler-mode exception frame
remain correct. The original unsafe stack transition still reproduces its
out-of-SRAM fault.

The compiled ELF gate rejects a changed helper, an unsafe `CONTROL` write in
the callback, or a callback that bypasses the qualified helper. The source
patch fails closed on an unknown or partially modified framework.

The ten compile/emulation regression tests passed with Unicorn 2.1.4. The
patched `Heltec_tower_v2_companion_radio_ble` build also passed the compiled
handoff, runtime RAM, application size, and UF2 address gates. These tests
qualify the transition code, not every physical board or phone/bootloader
combination.

An isolated hardware run then used this patched Tower BLE application as the
`v1.17.1.8` source for an Android 5.1.1 Bluetooth upgrade to the published
`v1.17.1.9-halo-keymind-cascade-dev-88c85108` Full Companion. The phone's
read-only bonded GATT probe identified the existing `TOWER_V2` bootloader,
native DFU completed, and the reconnected Companion confirmed the selected
firmware body hash and unchanged public identity. The existing
`OTAFIX2.4.11-preview.2` bootloader was not replaced. This is a Tower result,
not a physical qualification of all nRF52 boards. See the app's
[flashing evidence](https://github.com/mikecarper/meshcore-open/blob/codex/flashing-hardening/docs/FLASHING_HARDENING.md).

Implementation:

- [Private framework patch](../scripts/nrf52_ble_dfu_fix.py)
- [Compiled machine-code gate](../scripts/check_nrf52_ble_dfu_handoff.py)
- [Compile and emulation regression](../test/test_nrf52_ble_dfu_fix.py)
