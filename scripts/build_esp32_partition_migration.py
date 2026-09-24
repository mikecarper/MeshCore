#!/usr/bin/env python3
"""Build verified in-place Wi-Fi and LoRa ESP32 partition-migration ZIPs.

Run in Linux/WSL with PlatformIO installed. PlatformIO steps are deliberately
serial: build.sh cleans the shared .pio/build tree between Full targets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

from package_esp32_partition_migration import BOARDS


ROOT = Path(__file__).resolve().parents[1]


def git_output(*args: str) -> str:
    # This Windows checkout uses core.autocrlf=input. WSL often lacks that
    # global setting and would misreport unchanged CRLF files as dirty.
    return subprocess.check_output(
        ["git", "-c", "core.autocrlf=input", *args], cwd=ROOT, text=True
    ).strip()


def build_steps(boards: list[str], version: str, radio_preset: str,
                profile: str, jobs: int) -> list[tuple[list[str], bool]]:
    steps: list[tuple[list[str], bool]] = []
    for name in boards:
        steps.append(([
            "bash", "build.sh", "build-firmware", BOARDS[name]["target"],
            "--full-exact", "--firmware-version", version,
            "--radio-preset", radio_preset, "--profile", profile,
            "--require-ota", "--resume",
        ], True))
    # Full builds can clear .pio/build; every bridge must be built afterwards.
    for name in boards:
        for key in ("wifi_bridge", "lora_bridge"):
            steps.append((["pio", "run", "-e", BOARDS[name][key],
                           "-j", str(jobs)], False))
    return steps


def verify_archive(path: Path, board: str, version: str, source: str) -> None:
    with zipfile.ZipFile(path) as archive:
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f"{path}: damaged ZIP entry {damaged}")
        manifest = json.loads(archive.read("manifest.json"))
        if (manifest["board"] != board or manifest["firmware_version"] != version
                or manifest["source_commit"] != source):
            raise ValueError(f"{path}: package identity does not match recipe")
        for filename, expected in manifest["files"].items():
            data = archive.read(filename)
            if (len(data) != expected["bytes"]
                    or hashlib.sha256(data).hexdigest() != expected["sha256"]):
                raise ValueError(f"{path}: invalid {filename}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="firmware release version")
    parser.add_argument("--radio-preset", required=True,
                        help="radio preset passed to build.sh")
    parser.add_argument("--profile", choices=("default", "cascade"),
                        default="default", help="embedded runtime profile")
    parser.add_argument("--board", action="append", choices=tuple(BOARDS),
                        help="repeat to select boards (default: all supported boards)")
    parser.add_argument("--jobs", type=int, default=4,
                        help="workers within each serial PlatformIO build")
    parser.add_argument("--output-root", type=Path,
                        help="release folder (default: .releases/esp32-expanded-<commit>)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the build order without writing files")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if os.name == "nt" and not args.dry_run:
        parser.error("run this recipe inside Linux/WSL, where bash and pio are available")

    source = git_output("rev-parse", "--short=8", "HEAD")
    boards = list(dict.fromkeys(args.board or BOARDS))
    requested_root = args.output_root or Path(".releases") / f"esp32-expanded-{source}"
    output_root = (requested_root if requested_root.is_absolute()
                   else ROOT / requested_root).resolve()
    build_dir = output_root / "builds"
    package_dir = output_root / "packages"
    steps = build_steps(boards, args.version, args.radio_preset, args.profile, args.jobs)
    for command, full in steps:
        prefix = f"OUTPUT_DIR={shlex.quote(str(build_dir))} " if full else ""
        print(prefix + shlex.join(command), flush=True)
    print(f"Package {', '.join(boards)} into {package_dir}", flush=True)
    if args.dry_run:
        return

    if git_output("status", "--porcelain", "--untracked-files=normal"):
        parser.error("commit or remove working-tree changes before building a versioned package")
    for executable in ("bash", "pio"):
        if not shutil.which(executable):
            parser.error(f"{executable} is unavailable in this environment")

    output_root.mkdir(parents=True, exist_ok=True)
    for command, full in steps:
        environment = os.environ.copy()
        if full:
            environment["OUTPUT_DIR"] = str(build_dir)
        subprocess.run(command, cwd=ROOT, env=environment, check=True)

    # Package in a fresh directory so an interruption never leaves a partial
    # release ZIP at the final path. Existing identical ZIPs are reusable.
    with tempfile.TemporaryDirectory(prefix="migration-package-", dir=output_root) as temporary:
        staging = Path(temporary) / "packages"
        command = [
            sys.executable, "-B", "scripts/package_esp32_partition_migration.py",
            "--build-dir", str(build_dir), "--output-dir", str(staging),
            "--version", args.version, "--source", source,
        ]
        for name in boards:
            command.extend(("--board", name))
        subprocess.run(command, cwd=ROOT, check=True)
        archives = sorted(staging.glob("*.zip"))
        if len(archives) != len(boards):
            raise ValueError("packager did not produce one ZIP per selected board")
        package_dir.mkdir(parents=True, exist_ok=True)
        for name in boards:
            matches = [path for path in archives if path.name.startswith(name + "-")]
            if len(matches) != 1:
                raise ValueError(f"expected exactly one {name} ZIP")
            staged = matches[0]
            verify_archive(staged, name, args.version, source)
            destination = package_dir / staged.name
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).digest() != \
                        hashlib.sha256(staged.read_bytes()).digest():
                    raise ValueError(f"existing package differs: {destination}")
            else:
                staged.replace(destination)
            print(f"Ready: {destination}", flush=True)


if __name__ == "__main__":
    main()
