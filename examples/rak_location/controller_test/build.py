#!/usr/bin/env python3
"""Build the three-library RAK controller test in a Sidewalk v1.2.0 NCS workspace."""
import argparse
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent))
from tracker_uf2 import write_tracker_uf2


def prepare_sdk(checkout, destination):
    # Read only the dependency source from the release tag. Leave the user's
    # SDK checkout, provisioning files, product plan and examples untouched.
    data = subprocess.check_output([
        "git", "-C", str(checkout), "archive", "v2.4.2", "--",
        "CMakeLists.txt", "Kconfig.fmn", "LICENSE", "include", "src", "zephyr",
    ])
    with tarfile.open(fileobj=BytesIO(data)) as archive:
        for item in archive:
            if not item.isfile():
                continue
            path = (destination / item.name).resolve()
            if not path.is_relative_to(destination):
                raise ValueError("Unexpected SDK archive path")
            content = archive.extractfile(item).read()
            if path.suffix in (".c", ".h"):
                content = content.replace(b"zephyr/net/buf.h", b"zephyr/net_buf.h")
                content = content.replace(b"zephyr/random/rand32.h", b"zephyr/random/random.h")
                content = content.replace(b"BT_LE_ADV_OPT_CONNECTABLE", b"BT_LE_ADV_OPT_CONN")
            if item.name == "src/fmna_version.c":
                content = content.replace(b"#include <pm_config.h>",
                                          b"#ifdef CONFIG_FMNA_UARP\n#include <pm_config.h>\n#endif")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("sensor", "companion"))
    parser.add_argument("--findmy-sdk", type=Path, required=True, help="Find My SDK Git checkout with tag v2.4.2")
    parser.add_argument("--build-dir", type=Path, required=True, help="Build workspace for this role")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libdeps", type=Path, help="RAK Sensor PlatformIO library directory")
    parser.add_argument("--native-pings", action="store_true", help="Run the three native SDKs together")
    parser.add_argument("--product-plan", type=Path, help="Apple product plan C source for native pings")
    args = parser.parse_args()
    if args.product_plan and not args.native_pings:
        parser.error("--product-plan requires --native-pings")
    build = args.build_dir.resolve()
    sdk = build / "sdk-findmy-v242-port"
    prepare_sdk(args.findmy_sdk, sdk)
    libdeps = args.libdeps or APP.parents[2] / ".pio/libdeps/RAK_WisMesh_Tag_sensor"
    firmware = build / ("native-firmware" if args.native_pings else "firmware")
    options = ["-DRAK_NATIVE_PINGS=" + ("ON" if args.native_pings else "OFF")]
    if args.native_pings:
        options.append("-DRAK_FINDMY_PRODUCT_PLAN=" +
                       (str(args.product_plan.resolve()) if args.product_plan else ""))
    subprocess.run(["west", "build", "--no-sysbuild", "-b", "rak4631/nrf52840", "-d", str(firmware),
                    str(APP), "--", "-DRAK_TAG_ROLE=" + args.role, "-DZEPHYR_EXTRA_MODULES=" + str(sdk),
                    "-DMESHCORE_TAG_LIBDEPS=" + str(libdeps.resolve()), *options], check=True)
    write_tracker_uf2(firmware, args.output, app_limit=0xb9000 if args.native_pings else 0xc1000)


if __name__ == "__main__":
    main()
