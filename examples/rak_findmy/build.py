#!/usr/bin/env python3
"""Build and verify an application-only UF2 for the RAK WisMesh Tag."""
import argparse
from pathlib import Path
import subprocess
import sys

APP = Path(__file__).resolve().parent
ROOT = APP.parents[1]
sys.path.insert(0, str(APP.parent / "rak_location"))
from tracker_uf2 import write_tracker_uf2


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
    write_tracker_uf2(build, args.output)


if __name__ == "__main__":
    main()
