"""Execute functions from a linked nRF52840 image with simulated peripheral calls."""
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)

REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)


class ArmImage:
    stop = 0xff000

    def __init__(self, filename):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
        self.cpu.mem_map(0, 0x100000)
        self.cpu.mem_write(0, b"\xff" * 0x100000)
        self.cpu.mem_map(0x20000000, 0x40000)
        with open(filename, "rb") as file:
            elf = ELFFile(file)
            for segment in elf.iter_segments():
                if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                    self.cpu.mem_write(segment["p_vaddr"], segment.data())
            self.symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}

    def hook(self, name, action):
        address = self.symbols[name] & ~1
        self.cpu.hook_add(UC_HOOK_CODE, lambda *_: action(), begin=address, end=address)

    def arg(self, n):
        if n < len(REGS):
            return self.cpu.reg_read(REGS[n])
        return self.word(self.cpu.reg_read(UC_ARM_REG_SP) + 4 * (n - len(REGS)))

    def word(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def put_word(self, address, value):
        self.cpu.mem_write(address, struct.pack("<I", value))

    def ret(self, value):
        self.cpu.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
        self.cpu.reg_write(UC_ARM_REG_PC, self.cpu.reg_read(UC_ARM_REG_LR))

    def call(self, name, *args):
        stack = 0x2003ff00
        self.cpu.reg_write(UC_ARM_REG_SP, stack)
        self.cpu.reg_write(UC_ARM_REG_LR, self.stop | 1)
        for i, value in enumerate(args):
            if i < len(REGS):
                self.cpu.reg_write(REGS[i], value)
            else:
                self.put_word(stack + 4 * (i - len(REGS)), value)
        self.cpu.emu_start(self.symbols[name] | 1, self.stop, count=20000)
        assert self.cpu.reg_read(UC_ARM_REG_PC) == self.stop, f"{name} did not return"
        return self.arg(0)
