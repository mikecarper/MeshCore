#!/usr/bin/env python3
"""Check linked ARM Companion OTA queue ownership without touching hardware.

Requires unicorn==2.1.4. Executes the actual acquire/release callbacks starting
from the ELF's initialized globals and empty queue. Watch the fixed SD handoff
RAM through repeated construction/destruction: source-only archive stores must
never write it, even though ordinary linkers put application globals there.
This checks memory ownership, not radio/USB/SD hardware behavior.
"""

import argparse
import hashlib
import json
import struct

from firmware_elf import FirmwareElf
from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS,
                     UC_HOOK_MEM_WRITE)
from unicorn import arm_const as arm


AUTH_START, AUTH_END = 0x20006008, 0x20006050


def check(path):
    elf = FirmwareElf(path)
    if elf.unpack("H", 18)[0] != 40:
        raise ValueError("expected an ARM firmware ELF")

    def callback(fragment):
        matches = [name for name in elf.symbols if fragment in name]
        if len(matches) != 1:
            raise ValueError(f"expected one linked {fragment} callback, found {len(matches)}")
        return matches[0]

    acquire = callback("acquireOfflineQueueForOta")
    release = callback("releaseOfflineQueueFromOta")
    owner = elf.address("the_mesh")
    machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    machine.mem_map(0, 0x100000)
    machine.mem_map(0x20000000, 0x40000)
    machine.mem_map(0xE000E000, 0x1000)
    for section in elf.sections:
        if section[1] != 8 and section[2] & 2 and section[5]:
            machine.mem_write(section[3], elf.section_bytes(section))
    # Full Companion callbacks execute softfp instructions while constructing
    # the real OtaContext. Enable Cortex-M4F CP10/CP11 just as startup does.
    machine.mem_write(0xE000ED88, struct.pack("<I", 0x00F00000))
    machine.reg_write(arm.UC_ARM_REG_FPEXC, 0x40000000)
    stack_top, stop = 0x2003F000, 0x1000
    machine.mem_write(stop, b"\x00\xbe")
    before = bytes(machine.mem_read(AUTH_START, AUTH_END - AUTH_START))
    violations = []

    def write_hook(uc, _access, address, size, _value, _context):
        if address < AUTH_END and address + size > AUTH_START:
            violations.append((uc.reg_read(arm.UC_ARM_REG_PC), address, size))
            uc.emu_stop()

    machine.hook_add(UC_HOOK_MEM_WRITE, write_hook)

    def call(name):
        machine.reg_write(arm.UC_ARM_REG_SP, stack_top)
        machine.reg_write(arm.UC_ARM_REG_LR, stop | 1)
        machine.reg_write(arm.UC_ARM_REG_R0, owner)
        try:
            machine.emu_start(elf.address(name) | 1, stop, timeout=10000000, count=2000000)
        except UcError as error:
            raise ValueError(f"{name}: ARM execution failed: {error}") from error
        if violations:
            pc, address, size = violations[0]
            raise ValueError(f"{name}: write to SD auth/application RAM at {address:#x} "
                             f"({size} bytes), instruction {pc:#x}")
        if machine.reg_read(arm.UC_ARM_REG_PC) != stop:
            raise ValueError(name + " did not return within the instruction/time limit")
        if machine.reg_read(arm.UC_ARM_REG_SP) != stack_top:
            raise ValueError(name + " did not restore the stack")
        if bytes(machine.mem_read(AUTH_START, AUTH_END - AUTH_START)) != before:
            raise ValueError(name + " changed SD auth/application globals")
        return machine.reg_read(arm.UC_ARM_REG_R0)

    workspace = None
    for _cycle in range(4):
        pointer = call(acquire)
        if not 0x20006050 <= pointer < stack_top:
            raise ValueError(f"OTA workspace pointer outside application RAM: {pointer:#x}")
        if workspace is not None and workspace != pointer:
            raise ValueError("shared OTA workspace address changed across release/acquire")
        workspace = pointer
        if call(acquire) != workspace:
            raise ValueError("already-acquired OTA workspace is not idempotent")
        call(release)
        call(release)
    return {
        "passed": True,
        "elf_sha256": hashlib.sha256(elf.data).hexdigest(),
        "queue_lifetimes": 4,
        "workspace_address": hex(workspace),
        "protected_sd_auth_range": [hex(AUTH_START), hex(AUTH_END)],
        "scope": "Linked ARM empty-queue OTA acquisition/release; no hardware qualification.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.elf), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"FAILED: {error}\n")


if __name__ == "__main__":
    main()
