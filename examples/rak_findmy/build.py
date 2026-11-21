#!/usr/bin/env python3
"""Build and verify an application-only UF2 for the RAK WisMesh Tag."""
import argparse
from pathlib import Path
import struct
import subprocess
import sys

APP = Path(__file__).resolve().parent
ROOT = APP.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_nrf52_uf2 import check_uf2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("sensor", "companion"))
    parser.add_argument("--sdk", required=True, type=Path, help="Nordic Find My SDK v2.4.2 module")
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--product-plan", type=Path, help="private product plan C file")
    parser.add_argument("--libdeps", type=Path, help="PlatformIO library directory for a RAK Tag role")
    args = parser.parse_args()
    build = args.build_dir.resolve()
    options = [
        "-DZEPHYR_EXTRA_MODULES=" + str(args.sdk.resolve()),
        "-DRAK_TAG_ROLE=" + args.role,
        "-DRAK_FINDMY_PRODUCT_PLAN=" + (str(args.product_plan.resolve()) if args.product_plan else ""),
        "-DMESHCORE_TAG_LIBDEPS=" + str((args.libdeps or ROOT / ".pio/libdeps/RAK_WisMesh_Tag_sensor").resolve()),
    ]
    subprocess.run(["west", "build", "-b", "rak4631_nrf52840", "-d", str(build),
                    str(APP), "--", *options], check=True)
    binary = build / "zephyr/zephyr.bin"
    image = binary.read_bytes()
    base, settings = 0x26000, 0xD2000
    stack, reset = struct.unpack_from("<II", image)
    if not (0x20000000 < stack <= 0x20040000 and stack % 8 == 0
            and reset & 1 and base <= (reset & ~1) < base + len(image) <= settings):
        raise SystemExit("Invalid RAK Tag application vectors or flash bounds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "bin/uf2conv/uf2conv.py"), str(binary),
                    "-c", "-b", hex(base), "-f", "0xADA52840", "-o", str(args.output)], check=True)
    start, end, blocks = check_uf2(args.output, app_end=settings)
    print(f"{args.role} UF2 verified, {blocks} blocks at {start:#x}..{end:#x}")


if __name__ == "__main__":
    main()
