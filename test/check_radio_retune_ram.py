"""Verify switching code in the final firmware ELF. Run with PlatformIO's Python."""

import argparse
import sys

from elftools.elf.elffile import ELFFile


PROGRAM = {"programProfile": "_ZN15RadioLibWrapper14programProfileERKN4mesh18RadioProfileParamsEt"}
FUNCTIONS = {
    **PROGRAM,
    "startRecv": "_ZN15RadioLibWrapper9startRecvEv",
    "beginReconfigure": "_ZN15RadioLibWrapper16beginReconfigureEv",
    "endReconfigure": "_ZN15RadioLibWrapper14endReconfigureEb",
    "tuneProfile": "_ZN15RadioLibWrapper11tuneProfileEh",
}


def check(path, check_controller):
    functions = FUNCTIONS if check_controller else PROGRAM
    with open(path, "rb") as stream:
        elf = ELFFile(stream)
        arm = elf["e_machine"] == "EM_ARM"
        expected = ".data" if arm else ".iram0.text"
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            raise ValueError("missing symbol table")
        loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        found = set()
        for symbol in symtab.iter_symbols():
            if symbol["st_info"]["type"] != "STT_FUNC" or not symbol["st_size"]:
                continue
            # LTO may emit specialized copies with a suffix.
            name = symbol.name.partition(".")[0]
            if name not in functions.values():
                continue
            section = elf.get_section(symbol["st_shndx"]).name
            start = symbol["st_value"] & ~1 if arm else symbol["st_value"]
            end = start + symbol["st_size"]
            if section != expected:
                raise ValueError(f"{symbol.name} at {start:#x} in {section}, expected {expected}")
            # ARM startup copies flash-backed .data to SRAM. ESP boot images
            # contain a load segment for IRAM separate from mapped flash text.
            if not any(s["p_vaddr"] <= start and end <= s["p_vaddr"] + s["p_filesz"]
                       and (not arm or (0x20000000 <= start < 0x21000000
                                        and s["p_paddr"] < 0x20000000))
                       for s in loads):
                raise ValueError(f"{symbol.name} at {start:#x} has no initialized RAM segment")
            found.add(name)
        missing = [label for label, name in functions.items() if name not in found]
        if missing:
            raise ValueError(f"missing functions: {', '.join(missing)}")
        print(f"{path}: {', '.join(functions)} verified in {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", action="store_true",
                        help="also require the complete switching controller in RAM")
    parser.add_argument("firmware", nargs="+", metavar="FIRMWARE.elf")
    args = parser.parse_args()
    for path in args.firmware:
        try:
            check(path, args.controller)
        except (OSError, ValueError) as error:
            sys.exit(f"{path}: {error}")
