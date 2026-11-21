#!/usr/bin/env python3
"""Build a native RAK finder image by network name or assigned slot (A/B/C)."""
import argparse
from pathlib import Path
import subprocess
import sys

from tracker_uf2 import write_tracker_uf2

EXAMPLES = Path(__file__).resolve().parents[1]
SLOTS = {"a": "findmy", "b": "findhub", "c": "sidewalk"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=str.lower, choices=(*SLOTS, *SLOTS.values()),
                        help="A = Apple Find My, B = Android Find Hub, C = Amazon Sidewalk")
    parser.add_argument("role", choices=("sensor", "companion"))
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libdeps", type=Path, help="RAK Sensor PlatformIO library directory")
    parser.add_argument("--sdk", type=Path, help="Find My SDK v2.4.2 module (A only)")
    parser.add_argument("--product-plan", type=Path, help="Private Find My product plan C file (A only)")
    args = parser.parse_args()
    network = SLOTS.get(args.network, args.network)
    if network == "findmy":
        if not args.sdk:
            parser.error("A (Find My) requires --sdk pointing to the Find My SDK v2.4.2 module")
        command = [sys.executable, str(EXAMPLES / "rak_findmy/build.py"), args.role,
                   "--sdk", str(args.sdk), "--build-dir", str(args.build_dir), "--output", str(args.output)]
        if args.product_plan:
            command += ["--product-plan", str(args.product_plan)]
        if args.libdeps:
            command += ["--libdeps", str(args.libdeps)]
        subprocess.run(command, check=True)
        return
    if args.sdk or args.product_plan:
        parser.error("--sdk and --product-plan apply only to A (Find My)")
    libdeps = args.libdeps or EXAMPLES.parent / ".pio/libdeps/RAK_WisMesh_Tag_sensor"
    build = args.build_dir.resolve()
    subprocess.run(["west", "build", "--no-sysbuild", "-b", "rak4631/nrf52840", "-d", str(build),
                    str(EXAMPLES / ("rak_" + network)), "--", "-DRAK_TAG_ROLE=" + args.role,
                    "-DMESHCORE_TAG_LIBDEPS=" + str(libdeps.resolve())], check=True)
    write_tracker_uf2(build, args.output)


if __name__ == "__main__":
    main()
