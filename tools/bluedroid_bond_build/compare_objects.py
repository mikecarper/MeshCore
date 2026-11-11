"""Check an *unmodified* SDK object against a source rebuild before patching.

Install pyelftools in the build environment. This does not certify the SDK, the
patch, whole-firmware ABI compatibility, or runtime behavior. It rejects differences
in allocated bytes, relocations, and defined global/weak ELF symbols, while allowing
debug/path differences and generated function-name numeric suffixes.
"""
import argparse
from collections import Counter
import re
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


def norm(name):
    return re.sub(r"^(\.rodata\.__func__|\.rodata\.__FUNCTION__)\$\d+$", r"\1$#", name)


def alloc_sections(elf):
    return Counter(
        (norm(section.name), section["sh_type"], section["sh_flags"], section["sh_size"],
         section["sh_addralign"], b"" if section["sh_type"] == "SHT_NOBITS" else section.data())
        for section in elf.iter_sections() if section["sh_flags"] & 2
    )


def symbol_name(elf, symbol):
    if symbol["st_info"]["type"] == "STT_SECTION" and isinstance(symbol["st_shndx"], int):
        return "section:" + norm(elf.get_section(symbol["st_shndx"]).name)
    return symbol.name


def relocations(elf):
    result = Counter()
    for section in elf.iter_sections():
        if not isinstance(section, RelocationSection):
            continue
        target = elf.get_section(section["sh_info"])
        if not target["sh_flags"] & 2:
            continue
        symbols = elf.get_section(section["sh_link"])
        for rel in section.iter_relocations():
            symbol = symbols.get_symbol(rel["r_info_sym"])
            result[(norm(target.name), rel["r_offset"], rel["r_info_type"],
                    rel.entry.get("r_addend"), symbol_name(elf, symbol))] += 1
    return result


def exported_symbols(elf):
    symbols = elf.get_section_by_name(".symtab")
    if symbols is None:
        raise ValueError("object has no symbol table")
    result = Counter()
    for symbol in symbols.iter_symbols():
        if symbol["st_info"]["bind"] not in ("STB_GLOBAL", "STB_WEAK"):
            continue
        index = symbol["st_shndx"]
        section = norm(elf.get_section(index).name) if isinstance(index, int) else index
        result[(symbol.name, symbol["st_info"]["bind"], symbol["st_info"]["type"],
                symbol["st_other"]["visibility"], section, symbol["st_value"], symbol["st_size"])] += 1
    return result


def compare(stock, rebuilt):
    with open(stock, "rb") as first, open(rebuilt, "rb") as second:
        original, candidate = ELFFile(first), ELFFile(second)
        header = lambda elf: (elf.elfclass, elf.little_endian, elf["e_machine"], elf["e_type"], elf["e_flags"])
        for name, fn in (("ELF header", header), ("allocated sections", alloc_sections),
                         ("relocations", relocations), ("global symbols", exported_symbols)):
            if fn(original) != fn(candidate):
                raise ValueError(f"{name} differ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_object")
    parser.add_argument("rebuilt_object")
    args = parser.parse_args()
    try:
        compare(args.stock_object, args.rebuilt_object)
    except (OSError, ValueError) as exc:
        print(f"Object equivalence rejected: {exc}", file=sys.stderr)
        return 1
    print("Allocated sections, relocations, and exported symbols match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
