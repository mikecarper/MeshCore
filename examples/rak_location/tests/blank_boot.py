#!/usr/bin/env python3
"""Run the ARM firmware's application entry with erased locator storage and simulated drivers.

Usage: python blank_boot.py {sidewalk,findhub} build/zephyr/zephyr.elf
Requires pyelftools and unicorn 2.1.4. This checks the app-to-RTOS boundary.
"""
import argparse
import errno
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)


def check(network, filename, flash_error=False):
    cpu = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    cpu.mem_map(0, 0x100000)
    cpu.mem_write(0, b"\xff" * 0x100000)
    cpu.mem_map(0x20000000, 0x40000)
    with open(filename, "rb") as file:
        elf = ELFFile(file)
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                cpu.mem_write(segment["p_vaddr"], segment.data())
        symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}

    def hook(name, action):
        address = symbols[name] & ~1
        cpu.hook_add(UC_HOOK_CODE, lambda *_: action(), begin=address, end=address)

    def return_from_call(value):
        cpu.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
        cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    def word(address):
        return struct.unpack("<I", cpu.mem_read(address, 4))[0]

    page = 0xd1000 if network == "sidewalk" else 0xc8000
    pages_read = []
    workers = []

    def read_flash():
        area = cpu.reg_read(UC_ARM_REG_R0)
        address = word(area + 4) + cpu.reg_read(UC_ARM_REG_R1)
        size = cpu.reg_read(UC_ARM_REG_R3)
        pages_read.append((address, size))
        if not flash_error:
            cpu.mem_write(cpu.reg_read(UC_ARM_REG_R2), bytes(cpu.mem_read(address, size)))
        return_from_call(-errno.EIO if flash_error else 0)

    def create_thread():
        workers.append(cpu.reg_read(UC_ARM_REG_R3))
        return_from_call(cpu.reg_read(UC_ARM_REG_R0))

    def unexpected_finder():
        raise AssertionError("Finder initialization on an empty or unreadable credential page")

    hook("z_impl_device_is_ready", lambda: return_from_call(1))
    hook("adc_nrfx_channel_setup", lambda: return_from_call(0))
    hook("flash_area_read", read_flash)
    hook("z_impl_k_thread_create", create_thread)
    hook("sid_platform_init" if network == "sidewalk" else "rak_findhub_start", unexpected_finder)
    if network == "sidewalk":
        hook("z_impl_k_mutex_lock", lambda: return_from_call(0))
        hook("z_impl_k_mutex_unlock", lambda: return_from_call(0))

    stop = 0xff000
    cpu.reg_write(UC_ARM_REG_SP, 0x2003fff0)
    cpu.reg_write(UC_ARM_REG_LR, stop | 1)
    cpu.emu_start(symbols["main"] | 1, stop, count=20000)
    assert cpu.reg_read(UC_ARM_REG_PC) == stop, "Application entry did not return"
    assert cpu.reg_read(UC_ARM_REG_R0) == 0
    assert pages_read == [(page, 4)], pages_read
    assert workers == [symbols["_ZN12_GLOBAL__N_16workerEPvS0_S0_"] | 1], workers
    err = "last_error" if network == "sidewalk" else "startup_err"
    assert word(symbols[err]) == (-(errno.EIO if flash_error else errno.ENODATA) & 0xffffffff)
    print(f"PASS {filename}: {'unreadable' if flash_error else 'blank'} page at {page:#x}; "
          "finder idle; MeshCore worker scheduled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", choices=("sidewalk", "findhub"))
    parser.add_argument("elf")
    args = parser.parse_args()
    for read_error in (False, True):
        check(args.network, args.elf, read_error)
