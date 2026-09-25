#!/usr/bin/env python3
"""Build and stage a complete MeshCore release locally, without publishing it."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from build_esp32_partition_migration import bundle_release, verify_archive
from package_esp32_partition_migration import BOARDS
from package_cascade_release import category, collect_artifacts


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "v1.17.1.7-halo-keymind-cascade-dev"
RADIO = {"frequency_mhz": 910.525, "bandwidth_khz": 62.5,
         "spreading_factor": 7, "coding_rate": 5}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_logged(command: list[str], log: Path, environment: dict[str, str]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    print("Running: " + " ".join(command), flush=True)
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            output.write(line)
            output.flush()
            print(line, end="", flush=True)
        if process.wait():
            raise RuntimeError(f"build failed; see {log}")


def full_image_pattern(target: str, version: str, short_source: str) -> str:
    return f"{target}-full-*-{version}-{short_source}.bin"


def migration_targets() -> list[str]:
    return sorted({spec["target"] for spec in BOARDS.values()})


def migration_bridges() -> list[str]:
    return sorted({name for spec in BOARDS.values()
                   for name in (spec.get("wifi_bridge"), spec.get("lora_bridge"),
                                spec.get("expander_bridge")) if name})


def build_migration_artifacts(work: Path, version: str, short_source: str,
                              jobs: int, environment: dict[str, str]) -> None:
    for target in migration_targets():
        matches = list(work.glob(full_image_pattern(target, version, short_source)))
        if len(matches) == 1:
            continue
        if matches:
            raise ValueError(f"{target}: multiple Full images in {work}")
        run_logged([
            "bash", "build_legacy.sh", "build-firmware", target,
            "--full-exact", "--firmware-version", version,
            "--radio-preset", "usa-cascade-fixed", "--profile", "cascade",
            "--require-ota", "--resume",
        ], work / "build-logs" / f"migration-full-{target}.log", environment)

    # PlatformIO has a shared build tree. The matrix and exact Full builds have
    # finished and released this lock before any utility build starts.
    lock_path = ROOT / ".pio" / "build-sh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for bridge in migration_bridges():
            log = work / "build-logs" / f"migration-bridge-{bridge}.log"
            print(f"Building migration utility {bridge}; log: {log}", flush=True)
            with log.open("w", encoding="utf-8") as output:
                result = subprocess.run(["pio", "run", "-e", bridge, "-j", str(jobs)],
                                        cwd=ROOT, env=environment,
                                        stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f"migration utility {bridge} failed; see {log}")


def stage_release(work: Path, destination: Path, version: str,
                  source: str) -> None:
    if destination.exists():
        raise FileExistsError(f"release already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    short_source = source[:8]
    tooling_source = git("rev-parse", "HEAD")
    artifact_version = f"{version}-{short_source}"
    records = collect_artifacts(work, artifact_version)
    if not records:
        raise ValueError("firmware matrix produced no qualified artifacts")

    with tempfile.TemporaryDirectory(prefix=".staging-", dir=destination.parent) as temp:
        staging = Path(temp)
        firmware = staging / "firmware"
        firmware.mkdir()
        entries = []
        for record in records:
            manifest = record["manifest"]
            group = category(record)
            group_dir = firmware / group
            group_dir.mkdir(exist_ok=True)
            files = []
            for path in record["files"]:
                target = group_dir / path.name
                if target.exists():
                    raise ValueError(f"duplicate release artifact: {target}")
                shutil.copy2(path, target)
                files.append(str(target.relative_to(staging)))
            entries.append({"target": manifest["target"], "artifact_target": manifest["artifact_target"],
                            "platform": manifest["platform"], "profile": manifest["build_profile"],
                            "files": files})

        migrations = staging / "esp32-partition-migration"
        migrations.mkdir()
        with tempfile.TemporaryDirectory(prefix="migration-package-", dir=staging) as temp_packages:
            package_command = [sys.executable, "-B", "scripts/package_esp32_partition_migration.py",
                               "--build-dir", str(work), "--output-dir", temp_packages,
                               "--version", version, "--source", short_source]
            package_log = work / "migration-packaging.log"
            with package_log.open("w", encoding="utf-8") as output:
                result = subprocess.run(package_command, cwd=ROOT, stdout=output,
                                        stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f"migration packaging failed; see {package_log}")
            for board in BOARDS:
                packages = list(Path(temp_packages).glob(
                    f"{board}-{version}-{short_source}-migration.zip"))
                if len(packages) != 1:
                    raise ValueError(f"missing migration package: {board}")
                verify_archive(packages[0], board, version, short_source)
                shutil.copy2(packages[0], migrations / packages[0].name)
        bundle_release(staging, migrations, list(BOARDS), version,
                       short_source, "usa-cascade-fixed", "cascade")

        manifest = {
            "format": "meshcore-local-release-v1",
            "source_commit": source,
            "release_tooling_commit": tooling_source,
            "firmware_version": version,
            "radio": RADIO,
            "profile": "cascade",
            "publication": "local-only",
            "firmware_target_count": len(entries),
            "migration_board_role_count": len(BOARDS),
            "firmware": entries,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
        (staging / "README.md").write_text(
            f"# MeshCore local release {version}\n\n"
            f"Firmware source commit: `{source}`. Release tooling commit: `{tooling_source}`.\n"
            "USA Cascade: 910.525 MHz, BW 62.5 kHz, SF7, CR5.\n"
            "This directory is a local verification release and was not uploaded to GitHub.\n\n"
            "Select firmware by exact board, radio, storage, and role. ESP32 merged images install\n"
            "bootloader, partition table, and application over USB. Existing 1.25 MiB ESP32\n"
            "layouts require their exact board/role package under esp32-partition-migration\n"
            "before a Full image can use the expanded layout. nRF52 bootloader updates require\n"
            "the matching OTAFIX board/storage image before application firmware.\n",
            encoding="ascii")
        files = sorted(path for path in staging.rglob("*") if path.is_file()
                       and path.name != "SHA256SUMS.txt")
        (staging / "SHA256SUMS.txt").write_text(
            "".join(f"{digest(path)}  {path.relative_to(staging)}\n" for path in files),
            encoding="ascii")
        staging.rename(destination)
    print(f"Local release ready: {destination}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-version", default=DEFAULT_VERSION)
    parser.add_argument("--resume", action="store_true", help="reuse qualified output from an interrupted run")
    parser.add_argument("--pio-jobs", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+){3}-halo-keymind-cascade-dev", args.firmware_version):
        parser.error("expected vMAJOR.MINOR.PATCH.BUILD-halo-keymind-cascade-dev")
    if args.pio_jobs < 1:
        parser.error("--pio-jobs must be positive")
    source = git("rev-parse", "HEAD")
    short_source = source[:8]
    if git("status", "--porcelain", "--untracked-files=normal") and not args.dry_run:
        parser.error("commit or remove worktree changes before building a versioned release")
    label = f"{args.firmware_version}-{short_source}"
    work = ROOT / ".releases" / f".build-{label}"
    destination = ROOT / ".releases" / label
    if destination.exists():
        parser.error(f"local release already exists: {destination}")
    if work.exists() and any(work.iterdir()) and not args.resume and not args.dry_run:
        parser.error(f"work directory already has files: {work}; pass --resume to reuse them")
    print(f"Source: {source}")
    print(f"Firmware targets: canonical matrix; migration roles: {len(BOARDS)}; "
          f"migration utilities: {len(migration_bridges())}")
    print(f"Output: {destination}", flush=True)
    if args.dry_run:
        return
    work.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["OUTPUT_DIR"] = str(work)
    run_logged([
        "bash", "build_legacy.sh", "build-firmwares-logging-matrix",
        "--firmware-version", args.firmware_version,
        "--radio-preset", "usa-cascade-fixed", "--profile", "cascade",
        "--require-ota", "--skip-kiss", "--resume",
    ], work / "release-build.log", environment)
    build_migration_artifacts(work, args.firmware_version, short_source,
                              args.pio_jobs, environment)
    stage_release(work, destination, args.firmware_version, source)


if __name__ == "__main__":
    main()
