#!/usr/bin/env python3
"""Execute the linked nRF52 Companion crypto and bound its function stack.

Requires unicorn==2.1.4. This runs the actual ARM instructions from a release
ELF, not a host rebuild. It measures crypto call frames only: it does not
include their firmware callers, interrupts, or SoftDevice tasks, and does not
replace physical timing/transport tests or the full firmware RAM budget.
"""

import argparse
import hashlib
import json
import struct

from firmware_elf import FirmwareElf
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn import arm_const as arm


# RFC 8032 section 7.1, TEST 1 and TEST 2. Public test seeds, never device keys.
VECTORS = (
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da0"
        "85ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
)


def check(path):
    elf = FirmwareElf(path)
    if elf.unpack("H", 18)[0] != 40:
        raise ValueError("expected an ARM firmware ELF")
    if elf.symbols.get("base", (0, 0))[1] != 960:
        raise ValueError("Companion must retain the 960-byte Ed25519 base table")
    reader = "_ZN4mesh5bible9generatedL10readerDataE"
    if reader not in elf.symbols:
        raise ValueError("Companion Reader corpus is missing")
    path_cache = elf.symbols.get("_ZZN4mesh18contactPathStorageEvE4pool")
    if path_cache and elf.sections[path_cache[2]][1] != 8:  # SHT_NOBITS
        raise ValueError("Contact path cache has a flash load image; expected zero-initialized storage")
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.mem_map(0, 0x100000)
    uc.mem_map(0x20000000, 0x40000)
    for section in elf.sections:
        if section[1] != 8 and section[2] & 2 and section[5]:
            uc.mem_write(section[3], elf.section_bytes(section))
    stack_top, stop = 0x2003F000, 0x1000
    uc.mem_write(stop, b"\x00\xbe")
    state = {"min_sp": stack_top}

    def instruction(machine, _address, _size, _context):
        # Instruction-level hook includes stack space in leaf functions that
        # have no branch between reserving and releasing their stack frame.
        state["min_sp"] = min(state["min_sp"], machine.reg_read(arm.UC_ARM_REG_SP))

    uc.hook_add(UC_HOOK_CODE, instruction)

    def call(name, args):
        uc.reg_write(arm.UC_ARM_REG_SP, stack_top)
        uc.reg_write(arm.UC_ARM_REG_LR, stop | 1)
        for register, value in zip((arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
                                    arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3), args):
            uc.reg_write(register, value)
        for index, value in enumerate(args[4:]):
            uc.mem_write(stack_top + index * 4, struct.pack("<I", value))
        state["min_sp"] = stack_top
        uc.emu_start(elf.address(name) | 1, stop, timeout=10000000, count=20000000)
        if uc.reg_read(arm.UC_ARM_REG_PC) != stop:
            raise ValueError(name + " did not return within the instruction/time limit")
        if uc.reg_read(arm.UC_ARM_REG_SP) != stack_top:
            raise ValueError(name + " did not restore the stack")
        used = stack_top - state["min_sp"]
        if used > 2048:
            raise ValueError(f"{name} uses {used} stack bytes; maximum is 2048")
        return used

    seed, public, private, signature, message = (
        0x20030000, 0x20030040, 0x20030080, 0x20030100, 0x20030200)
    rows = []
    for index, vector in enumerate(VECTORS, 1):
        source, expected_public, text, expected_signature = map(bytes.fromhex, vector)
        uc.mem_write(seed, source)
        uc.mem_write(message, text or b"\0")
        key_stack = call("ed25519_create_keypair", [public, private, seed])
        if bytes(uc.mem_read(public, 32)) != expected_public:
            raise ValueError(f"RFC 8032 vector {index}: public key mismatch")
        sign_stack = call("ed25519_sign", [signature, message, len(text), public, private])
        if bytes(uc.mem_read(signature, 64)) != expected_signature:
            raise ValueError(f"RFC 8032 vector {index}: signature mismatch")
        rows.append({"rfc8032_vector": index, "keypair_stack_bytes": key_stack,
                     "sign_stack_bytes": sign_stack})
    return {
        "passed": True, "elf_sha256": hashlib.sha256(elf.data).hexdigest(),
        "base_table_bytes": elf.symbols["base"][1],
        "reader_data_bytes": elf.symbols[reader][1], "vectors": rows,
        "zero_initialized_path_cache_bytes": path_cache[1] if path_cache else 0,
        "stack_limit_bytes": 2048,
        "scope": "Crypto function frames only; excludes caller/ISR/task stacks and physical timing.",
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
