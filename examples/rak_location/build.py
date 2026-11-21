#!/usr/bin/env python3
"""Build an application-only Sidewalk or Find Hub image in the active Nordic SDK."""
import argparse
from pathlib import Path
import subprocess

from tracker_uf2 import write_tracker_uf2

EXAMPLES = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", choices=("sidewalk", "findhub"))
    parser.add_argument("role", choices=("sensor", "companion"))
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libdeps", type=Path, help="RAK Sensor PlatformIO library directory")
    args = parser.parse_args()
    libdeps = args.libdeps or EXAMPLES.parent / ".pio/libdeps/RAK_WisMesh_Tag_sensor"
    build = args.build_dir.resolve()
    subprocess.run(["west", "build", "--no-sysbuild", "-b", "rak4631/nrf52840", "-d", str(build),
                    str(EXAMPLES / ("rak_" + args.network)), "--", "-DRAK_TAG_ROLE=" + args.role,
                    "-DMESHCORE_TAG_LIBDEPS=" + str(libdeps.resolve())], check=True)
    write_tracker_uf2(build, args.output)


if __name__ == "__main__":
    main()
