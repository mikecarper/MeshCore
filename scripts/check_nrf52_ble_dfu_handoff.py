#!/usr/bin/env python3
"""Reject nRF52 ELFs with an unsafe compiled buttonless Bluetooth DFU handoff."""

import argparse
from pathlib import Path
import struct
import sys


SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path("scripts").resolve()
sys.path.insert(0, str(SCRIPT_DIR))
from firmware_elf import FirmwareElf


HELPER_NAME = "mesh_nrf52_dfu_jump"
# Assembled Cortex-M4/Thumb helper. These instructions are entirely local: no
# relocation or compiler-generated C stack access can change with image layout.
# The retained peer is copied with interrupts masked, after the last call into
# the live application. Post-transition stack writes only construct the explicit
# handler exception frame. The FreeRTOS thread path performs no stack access.
HELPER_CODE = bytes.fromhex(
    "72b60b4647f68071c2f200013e2213f801cb01f801cb013af9d1"
    "eff305830168426881f30888002080f3148880f3118880f31388"
    "bff34f8fbff36f8f002b03d16ff0000e62b6104788b0009001900290"
    "039004906ff00000059006924ff0045007906ff0060e62b67047"
)


def branch_targets(code, start):
    """Decode direct Thumb-2 BL/B.W targets without depending on objdump."""
    for offset in range(0, len(code) - 3, 2):
        first, second = struct.unpack_from("<HH", code, offset)
        if first & 0xF800 != 0xF000 or second & 0xD000 not in (0x9000, 0xD000):
            continue
        sign = (first >> 10) & 1
        i1 = 1 ^ ((second >> 13) & 1) ^ sign
        i2 = 1 ^ ((second >> 11) & 1) ^ sign
        immediate = (sign << 24) | (i1 << 23) | (i2 << 22)
        immediate |= (first & 0x3FF) << 12 | (second & 0x7FF) << 1
        if sign:
            immediate -= 1 << 25
        yield start + offset + 4 + immediate


def writes_control(code):
    for offset in range(0, len(code) - 3, 2):
        first, second = struct.unpack_from("<HH", code, offset)
        if first & 0xFFF0 == 0xF380 and second == 0x8814:
            return True
    return False


def check_firmware(path):
    elf = FirmwareElf(path)
    callbacks = [name for name in elf.symbols
                 if "bledfu_control_wr_authorize_cb" in name]
    if not callbacks and HELPER_NAME not in elf.symbols:
        return False  # A firmware without the DFU service has no handoff to gate.
    if HELPER_NAME not in elf.symbols:
        raise ValueError("Bluetooth DFU callback lacks the safe stack-transition helper")
    address, size, _ = elf.symbols[HELPER_NAME]
    address &= ~1  # ELF Thumb function values carry the low bit.
    if size != len(HELPER_CODE) or elf.read(address, size) != HELPER_CODE:
        raise ValueError("Bluetooth DFU helper differs from the qualified assembly")
    if not callbacks:
        raise ValueError("Bluetooth DFU helper is present without its control callback")
    for name in callbacks:
        start, size, _ = elf.symbols[name]
        start &= ~1
        if not size:
            raise ValueError("Bluetooth DFU callback has no inspectable machine code")
        code = elf.read(start, size)
        if writes_control(code):
            raise ValueError("Bluetooth DFU callback changes CONTROL inside a C stack frame")
        if address not in branch_targets(code, start):
            raise ValueError("Bluetooth DFU callback does not branch to the safe helper")
    return True


def register_platformio(env):
    def check(source, target, env):
        path = Path(env.subst("$BUILD_DIR/${PROGNAME}.elf"))
        try:
            enabled = check_firmware(path)
        except (OSError, ValueError, KeyError, struct.error) as error:
            print(f"nRF52 Bluetooth DFU qualification failed: {error}", file=sys.stderr)
            return 2
        print("nRF52 Bluetooth DFU: " + ("safe compiled handoff PASS" if enabled
                                          else "service not linked"))
        return 0

    env.AddPostAction("$BUILD_DIR/${PROGNAME}.elf", check)
    for alias in ("checkprogsize", "upload"):
        env.AddPreAction(env.Alias(alias), check)
    for suffix in ("bin", "hex", "uf2", "zip"):
        env.AddPreAction("$BUILD_DIR/${PROGNAME}." + suffix, check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    try:
        enabled = check_firmware(args.elf)
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.exit(1, f"nRF52 Bluetooth DFU qualification failed: {error}\n")
    print("nRF52 Bluetooth DFU: " + ("safe compiled handoff PASS" if enabled
                                      else "service not linked"))


try:
    Import("env")  # noqa: F821 -- PlatformIO/SCons
except NameError:
    if __name__ == "__main__":
        main()
else:
    register_platformio(env)  # noqa: F821
