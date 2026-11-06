#!/usr/bin/env python3
"""Build verified in-place Wi-Fi, USB and LoRa ESP32 partition-migration ZIPs.

Run in Linux/WSL with PlatformIO installed. PlatformIO steps are deliberately
serial: build.sh cleans the shared .pio/build tree between Full targets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

from package_esp32_partition_migration import BOARDS, USB_BRIDGE_PROFILES, resolve_pio_build_dir
from esp32_migration_assets import standalone_assets
from esp32_migration_expanders import build_expander_candidates


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
    built_bridges: set[str] = set()
    for name in boards:
        for key in ("wifi_bridge", "usb_bridge", "lora_bridge", "expander_bridge"):
            bridge = BOARDS[name].get(key)
            if bridge and bridge not in built_bridges:
                steps.append((["pio", "run", "-e", bridge,
                               "-j", str(jobs)], False))
                built_bridges.add(bridge)
    return steps


def usb_build_steps(jobs: int) -> list[list[str]]:
    """Build only the complete USB utility set, serially, without any Full app."""
    return [["pio", "run", "-e", environment, "-j", str(jobs)]
            for _, environment in sorted(USB_BRIDGE_PROFILES.items())]


def build_usb_only(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.board or args.role:
        parser.error("--usb-only builds all six chip/capacity utilities; do not select a board/role")
    source = git_output("rev-parse", "--short=8", "HEAD")
    requested_root = args.output_root or Path(".releases") / f"esp32-usb-bridges-{source}"
    output_root = (requested_root if requested_root.is_absolute()
                   else ROOT / requested_root).resolve()
    output_file = output_root / "meshcore-usb-partition-bridges.zip"
    steps = usb_build_steps(args.jobs)
    for command in steps:
        print(shlex.join(command), flush=True)
    print(f"Package all six USB utilities into {output_file}", flush=True)
    if args.dry_run:
        return
    if not shutil.which("pio"):
        parser.error("pio is unavailable in this environment")
    environment = os.environ.copy()
    try:
        utility_build_dir = resolve_pio_build_dir(environment=environment, project_dir=ROOT)
    except ValueError as error:
        parser.error(str(error))
    environment["PLATFORMIO_BUILD_DIR"] = str(utility_build_dir)
    for command in steps:
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
    # Unlike exact Full release packages, this independent utility set records
    # its actual dirty state and runtime-source hashes instead of pretending
    # its binaries came from an older Full app's release commit.
    subprocess.run([
        sys.executable, "-B", "scripts/package_usb_partition_bridges.py",
        "--pio-build-dir", str(utility_build_dir), "--output-file", str(output_file),
    ], cwd=ROOT, env=environment, check=True)


def verify_archive(path: Path, board: str, version: str, source: str, *,
                   full_image: Path | None = None) -> None:
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
        if full_image is not None and archive.read("full-application.bin") != full_image.read_bytes():
            raise ValueError(f"{path}: migration application differs from the exact published Full image")


def bundle_release(output_root: Path, package_dir: Path, boards: list[str],
                   version: str, source: str, radio_preset: str,
                   profile: str) -> Path:
    """Atomically publish the complete, exact-source migration set."""
    packages = []
    for board in boards:
        matches = list(package_dir.glob(f"{board}-{version}-{source}-migration.zip"))
        if len(matches) != 1:
            raise ValueError(f"release is missing {board}")
        verify_archive(matches[0], board, version, source)
        data = matches[0].read_bytes()
        packages.append((matches[0].name, data))
    manifest = {
        "format": "meshcore-esp32-partition-migration-release-v1",
        "scope": "configured-legacy-layout-board-role-recipes",
        "historical_firmware_binaries_included": False,
        "source_commit": source,
        "firmware_version": version,
        "radio_preset": radio_preset,
        "profile": profile,
        "boards": boards,
        "board_roles": {name: BOARDS[name]["role"] for name in boards},
        "roles": sorted({BOARDS[name]["role"] for name in boards}),
        "packages": {name: {"bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest()}
                     for name, data in packages},
    }
    archive = output_root / f"esp32-partition-migration-{version}-{source}-release.zip"
    with tempfile.NamedTemporaryFile(prefix="migration-release-", suffix=".zip",
                                     dir=output_root, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED) as result:
            result.writestr("release-manifest.json", json.dumps(manifest, indent=2) + "\n")
            for name, data in packages:
                result.writestr(name, data)
        with zipfile.ZipFile(temporary_path) as result:
            if result.testzip() or set(result.namelist()) != {"release-manifest.json"} | {
                    name for name, _ in packages}:
                raise ValueError("release ZIP failed integrity check")
        if archive.exists():
            if hashlib.sha256(archive.read_bytes()).digest() != \
                    hashlib.sha256(temporary_path.read_bytes()).digest():
                raise ValueError(f"existing release differs: {archive}")
        else:
            temporary_path.replace(archive)
    finally:
        temporary_path.unlink(missing_ok=True)
    return archive


def publish_migration_outputs(staging: Path, package_dir: Path, build_dir: Path,
                              output_root: Path, boards: list[str], version: str,
                              source: str, radio_preset: str, profile: str) -> list[Path]:
    """Validate the complete selection before atomically exposing any new file.

    Files are staged on the output filesystem. Exclusive hard links prevent an
    existing or concurrently created result from being overwritten; a failed
    publication removes only files created by this invocation.
    """
    if not boards or len(boards) != len(set(boards)):
        raise ValueError("select distinct migration boards")
    archives = sorted(staging.glob("*.zip"))
    if len(archives) != len(boards):
        raise ValueError("packager did not produce one ZIP per selected board")
    if package_dir.is_symlink() or (package_dir.exists() and not package_dir.is_dir()):
        raise ValueError("migration package destination is not a directory")
    direct_staging = staging / ".standalone"
    direct_staging.mkdir()
    plan: dict[Path, Path] = {}

    def add(destination: Path, candidate: Path) -> None:
        if destination in plan:
            raise ValueError(f"duplicate migration output: {destination.name}")
        plan[destination] = candidate

    for name in boards:
        if name not in BOARDS:
            raise ValueError(f"unknown migration board: {name}")
        staged = staging / f"{name}-{version}-{source}-migration.zip"
        if staged not in archives:
            raise ValueError(f"expected exactly one {name} ZIP")
        target = BOARDS[name]["target"]
        full_matches = list(build_dir.glob(f"{target}-full-*-{version}-{source}.bin"))
        if len(full_matches) != 1:
            raise ValueError(f"expected one published Full application for {name}")
        full_image = full_matches[0]
        verify_archive(staged, name, version, source, full_image=full_image)
        direct = standalone_assets(staged, name, target, version, source, full_image)
        add(package_dir / staged.name, staged)
        for filename, content in direct.items():
            candidate = direct_staging / filename
            if candidate.exists():
                raise ValueError(f"duplicate standalone migration asset: {filename}")
            candidate.write_bytes(content)
            add(package_dir / filename, candidate)

    if set(boards) == set(BOARDS):
        bundle_dir = staging / ".bundle"
        bundle_dir.mkdir()
        bundle = bundle_release(bundle_dir, staging, boards, version, source,
                                radio_preset, profile)
        add(output_root / bundle.name, bundle)

    for destination, candidate in plan.items():
        if destination.is_symlink():
            raise ValueError(f"migration output is a symbolic link: {destination}")
        if destination.exists() and (not destination.is_file() or
                hashlib.sha256(destination.read_bytes()).digest() !=
                hashlib.sha256(candidate.read_bytes()).digest()):
            raise ValueError(f"existing migration output differs: {destination}")

    package_existed = package_dir.exists()
    package_dir.mkdir(parents=True, exist_ok=True)
    if package_dir.is_symlink():
        raise ValueError("migration package destination is a symbolic link")
    created: list[tuple[Path, int, int]] = []
    try:
        for destination, candidate in plan.items():
            if destination.is_symlink():
                raise ValueError(f"migration output is a symbolic link: {destination}")
            if destination.exists():
                if (not destination.is_file() or
                        hashlib.sha256(destination.read_bytes()).digest() !=
                        hashlib.sha256(candidate.read_bytes()).digest()):
                    raise ValueError(f"concurrent migration output differs: {destination}")
                continue
            try:
                os.link(candidate, destination)
            except FileExistsError:
                if (destination.is_symlink() or not destination.is_file() or
                        hashlib.sha256(destination.read_bytes()).digest() !=
                        hashlib.sha256(candidate.read_bytes()).digest()):
                    raise ValueError(f"concurrent migration output differs: {destination}") from None
            else:
                stat = destination.stat()
                created.append((destination, stat.st_dev, stat.st_ino))
        for destination in plan:
            print(f"Ready: {destination}", flush=True)
    except BaseException:
        for destination, device, inode in reversed(created):
            try:
                stat = destination.lstat()
                if stat.st_dev == device and stat.st_ino == inode:
                    destination.unlink()
            except FileNotFoundError:
                pass
        if not package_existed:
            try:
                package_dir.rmdir()
            except OSError:
                pass
        raise
    return list(plan)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="firmware release version")
    parser.add_argument("--radio-preset",
                        help="radio preset passed to build.sh")
    parser.add_argument("--profile", choices=("default", "cascade"),
                        default="default", help="embedded runtime profile")
    parser.add_argument("--board", action="append", choices=tuple(BOARDS),
                        help="repeat to select board/role recipes (default: all configured recipes)")
    parser.add_argument("--role", choices=("repeater", "room-server", "sensor"),
                        help="build every configured board for this role")
    parser.add_argument("--jobs", type=int, default=4,
                        help="workers within each serial PlatformIO build")
    parser.add_argument("--output-root", type=Path,
                        help="release folder (default: .releases/esp32-expanded-<commit>)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the build order without writing files")
    parser.add_argument("--list-boards", action="store_true",
                        help="print configured board/role keys and exit")
    parser.add_argument("--usb-only", action="store_true",
                        help="build/package all six independent USB utilities only; "
                             "no Full app, version or radio preset is needed")
    args = parser.parse_args()
    if args.list_boards:
        for name in BOARDS:
            print(name)
        return
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if os.name == "nt" and not args.dry_run:
        parser.error("run this recipe inside Linux/WSL, where bash and pio are available")
    if args.usb_only:
        build_usb_only(args, parser)
        return
    if not args.version or not args.radio_preset:
        parser.error("--version and --radio-preset are required for a build")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]*", args.version):
        parser.error("--version must be a filename-safe version token")
    if args.board and args.role:
        parser.error("choose --board or --role, not both")

    source = git_output("rev-parse", "--short=8", "HEAD")
    if args.role:
        boards = [name for name, spec in BOARDS.items() if spec["role"] == args.role]
    else:
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
    if set(boards) == set(BOARDS):
        print(f"Bundle all configured board/role recipes (not historical binaries) in {output_root}", flush=True)
    if args.dry_run:
        return

    if git_output("status", "--porcelain", "--untracked-files=normal"):
        parser.error("commit or remove working-tree changes before building a versioned package")
    for executable in ("bash", "pio"):
        if not shutil.which(executable):
            parser.error(f"{executable} is unavailable in this environment")

    environment = os.environ.copy()
    try:
        utility_build_dir = resolve_pio_build_dir(environment=environment, project_dir=ROOT)
    except ValueError as error:
        parser.error(str(error))
    environment["PLATFORMIO_BUILD_DIR"] = str(utility_build_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    for command, full in steps:
        build_environment = environment.copy()
        if full:
            build_environment["OUTPUT_DIR"] = str(build_dir)
        subprocess.run(command, cwd=ROOT, env=build_environment, check=True)
    evaluation_dir = build_expander_candidates(
        boards, args.version, args.jobs, ROOT, utility_build_dir, environment,
        output_root / "expander-evaluations")

    # Package in a fresh directory so an interruption never leaves a partial
    # release ZIP at the final path. Existing identical ZIPs are reusable.
    with tempfile.TemporaryDirectory(prefix="migration-package-", dir=output_root) as temporary:
        staging = Path(temporary) / "packages"
        command = [
            sys.executable, "-B", "scripts/package_esp32_partition_migration.py",
            "--build-dir", str(build_dir), "--output-dir", str(staging),
            "--pio-build-dir", str(utility_build_dir),
            "--expander-evaluation-dir", str(evaluation_dir),
            "--version", args.version, "--source", source,
        ]
        for name in boards:
            command.extend(("--board", name))
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        publish_migration_outputs(staging, package_dir, build_dir, output_root,
                                  boards, args.version, source,
                                  args.radio_preset, args.profile)


if __name__ == "__main__":
    main()
