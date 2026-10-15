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
from package_esp32_partition_migration import BOARDS, resolve_pio_build_dir
from package_cascade_release import category, collect_artifacts, nrf52_sensor_profile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "v1.17.1.9-halo-keymind-cascade-dev"
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


def capacity_rejected_attempt(manifest_path: Path, version: str, source: str) -> dict:
    """Recognize only the successful qualification followed by portable-size rejection."""
    work = manifest_path.parent
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("manifest is not a regular non-symlink file")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    target = manifest.get("target")
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", target):
        raise ValueError("invalid target identity")
    artifact_version = f"{version}-{source[:8]}"
    stems = (f"{target}-{artifact_version}", f"{target}-ota-{artifact_version}")
    if manifest_path.name not in {stem + ".capabilities.json" for stem in stems}:
        raise ValueError("manifest filename does not match target/version/source")
    stem = manifest_path.name.removesuffix(".capabilities.json")
    # Even an unfamiliar sidecar can contain firmware or other failed-build
    # evidence. Never consume such a package merely because its bin is absent.
    siblings = list(work.glob(stem + "*"))
    if siblings != [manifest_path] or list(work.rglob(stem + "*")) != [manifest_path]:
        raise ValueError("manifest is not the sole same-stem output")
    recipe = manifest.get("build_recipe")
    checks = manifest.get("verification")
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2
            or manifest.get("platform") != "ESP32_PLATFORM"
            or manifest.get("build_profile") != "standard"
            or manifest.get("artifact_target") != target
            or manifest.get("verified") is not True
            or manifest.get("ota_update_verified") is not True
            or manifest.get("ota_update_evidence") !=
               "firmware fits both OTA application slots; otadata present"
            or not isinstance(checks, list) or not checks
            or any(not isinstance(check, dict) or check.get("present") is not True
                   for check in checks)
            or not isinstance(recipe, dict) or set(recipe) != {"schema_version", "sha256"}
            or type(recipe.get("schema_version")) is not int or recipe["schema_version"] != 1
            or not isinstance(recipe.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", recipe["sha256"])):
        raise ValueError("qualification or original recipe binding is incomplete")
    log = work / "build-logs" / f"{target}-standard.log"
    if log.parent.is_symlink() or log.is_symlink() or not log.is_file():
        raise ValueError("original standard log is missing or unsafe")
    log_raw = log.read_bytes()
    lines = log_raw.decode("utf-8").splitlines()
    overflow = re.fullmatch(
        r"ESP32 app image is (\d+) bytes, exceeding portable LoRa-OTA "
        r"slot 0x10000\.\.0x150000 \((\d+) bytes\) by (\d+) bytes",
        lines[-2] if len(lines) >= 2 else "")
    marker = (f"DEFERRED: {target} (standard) exceeds the portable OTA slot; "
              "the expanded FULL pass is required.")
    version_line = f'    -DFIRMWARE_VERSION="{artifact_version}"'
    qualification = re.compile(
        r"Verified \d+ capability marker\(s\); manifest: " + re.escape(str(manifest_path)))
    if (not overflow or lines[-1] != marker or version_line not in lines
            or not any(qualification.fullmatch(line) for line in lines[:-2])):
        raise ValueError("original log does not prove this exact portable-size rejection")
    size, limit, excess = map(int, overflow.groups())
    if limit != 0x140000 or size <= limit or excess != size - limit:
        raise ValueError("portable overflow measurements are inconsistent")
    return {"target": target, "manifest": manifest_path.name,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "original_log": str(log.relative_to(work)),
            "original_log_sha256": hashlib.sha256(log_raw).hexdigest(),
            "build_recipe": recipe, "image_bytes": size,
            "portable_limit_bytes": limit, "excess_bytes": excess,
            "reason": "portable_slot_overflow"}


def archive_capacity_rejected_attempts(work: Path, version: str, source: str,
                                      *, project_root: Path = ROOT,
                                      archive_parent: Path | None = None) -> dict:
    """Preserve proven manifest-only rejections outside recursive package discovery."""
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("invalid release source commit")
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+){3}-halo-keymind-cascade-dev", version):
        raise ValueError("invalid release firmware version")
    work = Path(os.path.abspath(work))
    parent = Path(os.path.abspath(archive_parent or work.parent))
    if (work.resolve() != work or not work.is_dir() or parent.resolve() != parent
            or parent == work or work in parent.parents):
        raise ValueError("unsafe work/archive location")
    lock_path = project_root / ".pio" / "build-sh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.parent.resolve() != lock_path.parent or lock_path.is_symlink():
        raise ValueError("unsafe build lock location")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("active build owns the checkout; no attempts were archived") from error
        candidates = []
        preserved = []
        for manifest in sorted(work.glob("*.capabilities.json")):
            try:
                candidates.append(capacity_rejected_attempt(manifest, version, source))
            except (ValueError, OSError, TypeError, AttributeError) as error:
                preserved.append({"manifest": manifest.name, "reason": str(error)})
        if not candidates:
            return {"archived": [], "preserved": preserved, "archive": None}
        # Complete the scan and recheck every candidate before any file moves.
        for record in candidates:
            if capacity_rejected_attempt(work / record["manifest"], version, source) != record:
                raise ValueError("capacity attempt changed during archival scan")
        archive = Path(tempfile.mkdtemp(
            prefix=f".capacity-rejected-{version}-{source[:8]}-", dir=parent))
        (archive / "manifests").mkdir()
        (archive / "logs").mkdir()
        for record in candidates:
            original = work / record["original_log"]
            copy = archive / "logs" / original.name
            shutil.copy2(original, copy)
            if digest(copy) != record["original_log_sha256"]:
                raise ValueError("original log changed while preserving rejection evidence")
        index = {"format": "meshcore-capacity-rejected-attempts-v1",
                 "source_commit": source, "firmware_version": version,
                 "work_directory": str(work), "state": "prepared",
                 "attempts": candidates, "moved_manifests": []}
        def save_index() -> None:
            temporary = archive / ".index.json.tmp"
            temporary.write_text(json.dumps(index, indent=2, sort_keys=True,
                                             ensure_ascii=True) + "\n", encoding="ascii")
            temporary.replace(archive / "index.json")
        save_index()
        try:
            for record in candidates:
                manifest = work / record["manifest"]
                if capacity_rejected_attempt(manifest, version, source) != record:
                    raise ValueError("capacity attempt changed before archival move")
                manifest.rename(archive / "manifests" / manifest.name)
                index["moved_manifests"].append(manifest.name)
                save_index()
        except (ValueError, OSError):
            index["state"] = "interrupted"
            save_index()
            raise
        index["state"] = "archived"
        save_index()
        return {"archived": candidates, "preserved": preserved, "archive": str(archive)}


def full_image_pattern(target: str, version: str, short_source: str) -> str:
    return f"{target}-full-*-{version}-{short_source}.bin"


def migration_targets() -> list[str]:
    return sorted({spec["target"] for spec in BOARDS.values()})


def migration_bridges() -> list[str]:
    return sorted({name for spec in BOARDS.values()
                   for name in (spec.get("wifi_bridge"), spec.get("usb_bridge"), spec.get("lora_bridge"),
                                spec.get("expander_bridge")) if name})


def build_migration_artifacts(work: Path, version: str, short_source: str,
                              jobs: int, environment: dict[str, str],
                              pio_build_dir: Path | None = None) -> None:
    utility_build_dir = resolve_pio_build_dir(
        pio_build_dir, environment=environment, project_dir=ROOT)
    environment = environment.copy()
    environment["PLATFORMIO_BUILD_DIR"] = str(utility_build_dir)
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


def select_ordinary_full_records(records: list[dict]) -> list[dict]:
    """Keep one ESP32 Full OTA identity per board/role in the public matrix.

    Explicit alternate images and old partition identities still belong in
    their separately built migration packages or direct --full-exact builds.
    This also removes stale sibling artifacts when a matrix build is resumed.
    nRF52 Full/reduced sensor OTA records always pass through independently,
    even when their logical target ID and storage layout are the same. The
    MeshTower SD primary replaces stale internal-only artifacts on resume;
    those files must never be relabeled with the primary's different OTA ID.
    """
    # Resumed directories can contain portable or transport-specific images
    # from older release policies. They are recovery inputs, not ordinary
    # ESP32 choices. Full qualification failures must fail the release rather
    # than silently substitute one of those smaller images. KISS is a distinct
    # host-modem role and the ordinary release command skips it separately.
    def ordinary_record(record: dict) -> bool:
        manifest = record["manifest"]
        if manifest["target"].lower() == "rak_4631_repeater_rak15002_sd_seeder_only":
            # An exact opt-in, source-only hardware trial is not a qualified
            # self-updating nRF52 release, including when resuming old output.
            return False
        if manifest["platform"] != "ESP32_PLATFORM":
            return True
        target = manifest["target"].lower()
        if "kiss" in target:
            return True
        if ("_lora_ota_no_external_sensors" in target
                or "terminal_chat" in target
                or "partition_migrator" in target
                or "partition_expander" in target
                or "partition_legacy_seed" in target
                or target.endswith(("_legacy_partition_test", "_sim"))
                or target.startswith(("profile_switch_", "profile_fixed_"))
                or target == "profile_four_tx_v4_rx"):
            return False
        if (("companion" in target or "comp_radio" in target)
                and "companion_radio_full" not in target):
            return False
        if (re.search(r"companion_radio_.*_ps(?:_|$)", target)
                or ("companion_radio_" in target and target.endswith("_femoff"))
                or target.startswith(("station_g2_logging_", "station_g3_esp32_logging_"))):
            return False
        return manifest["build_profile"] == "full"

    records = [record for record in records if ordinary_record(record)]

    tower_primary = "heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors"
    tower_legacy = {
        "heltec_tower_v2_repeater",
        "heltec_tower_v2_repeater_lora_ota_no_external_sensors",
    }
    targets = {record["manifest"]["target"].lower() for record in records}
    if targets & tower_legacy and tower_primary not in targets:
        raise ValueError("MeshTower V2 release requires the SD primary; old internal-only "
                         "artifacts cannot substitute for its distinct OTA identity")

    mke_primary = "mke_s3_repeater"
    mke_legacy = {mke_primary + "_bridge_rs232", mke_primary + "_bridge_espnow"}
    mke_bridges = {"bridge.rs232", "bridge.espnow"}

    def qualified_full(manifest: dict) -> bool:
        return (manifest["platform"] == "ESP32_PLATFORM"
                and manifest["build_profile"] == "full"
                and manifest.get("verified") is True)

    def proven_espnow(manifest: dict) -> bool:
        return (qualified_full(manifest)
                and "bridge.espnow" in (manifest.get("capabilities") or [])
                and any(check.get("capability") == "bridge.espnow"
                        and check.get("present") is True
                        for check in manifest.get("verification") or []))

    def proven_rs232(manifest: dict) -> bool:
        return (qualified_full(manifest)
                and "bridge.rs232" in (manifest.get("capabilities") or [])
                and any(check.get("capability") == "bridge.rs232"
                        and check.get("present") is True
                        for check in manifest.get("verification") or []))

    def role_base(target: str) -> str:
        base = target.rstrip("_").lower()
        if base.endswith("_observer_mqtt"):
            base = base[:-len("_observer_mqtt")]
        return base

    # These normal repeaters already supply a UART. Full MQTT source
    # substitution must never remove it while retiring the dedicated image.
    # TLora cannot fit all three transports; its normal and observer Full
    # images remain separate, and only its normal image requires UART.
    uart_full_targets = {"heltec_v3_repeater", "heltec_wsl3_repeater",
                         "rak_3112_repeater", "lilygo_tlora_v2_1_1_6_repeater"}
    tlora_uart_target = "lilygo_tlora_v2_1_1_6_repeater"

    def requires_uart_full(target: str) -> bool:
        base = role_base(target)
        return (base in uart_full_targets
                and (base != tlora_uart_target or target.rstrip("_").lower() == base))

    combined_rs232 = {role_base(record["manifest"]["target"]) for record in records
                      if proven_rs232(record["manifest"])} & uart_full_targets

    # A resumed build directory can still contain old normal or dedicated
    # bridge images. The canonical Full image must prove both runtime bridges
    # even when no stale legacy image happens to remain beside it.
    mke_full = [record["manifest"] for record in records
                if record["manifest"]["target"].lower() == mke_primary
                and record["manifest"]["build_profile"] == "full"]
    if mke_full or targets & mke_legacy:
        if not mke_full or any(
            not qualified_full(manifest)
            or not mke_bridges <= set(manifest.get("capabilities") or [])
            or not mke_bridges <= {
                check["capability"] for check in manifest.get("verification") or []
                if check.get("present") is True
            }
            for manifest in mke_full
        ):
            raise ValueError("MKE S3 release requires a qualified combined repeater "
                             "with verified RS232 and ESP-NOW bridges; old normal or "
                             "legacy bridge artifacts cannot replace that canonical image")

    # A dedicated ESP-NOW image is redundant only when the matching normal or
    # observer Full image really advertises that capability. Filename suffixes
    # alone previously suppressed V4 TFT's only ESP-NOW implementation.
    combined_espnow = set()
    for record in records:
        manifest = record["manifest"]
        if not proven_espnow(manifest):
            continue
        base = manifest["target"].rstrip("_").lower()
        if base.endswith("_observer_mqtt"):
            base = base[:-len("_observer_mqtt")]
        if base.endswith("_repeater"):
            combined_espnow.add(base)

    chosen: dict[str, tuple[int, dict]] = {}
    passthrough: list[dict] = []
    for record in records:
        manifest = record["manifest"]
        if manifest["target"].lower() in tower_legacy | mke_legacy:
            continue
        legacy_base = manifest["target"].rstrip("_").lower()
        if (manifest["platform"] == "ESP32_PLATFORM"
                and legacy_base.endswith("_repeater_bridge_espnow")
                and legacy_base[:-len("_bridge_espnow")] in combined_espnow):
            # Resume may contain portable legacy images as well as Full ones.
            # Retire them only after the matching combined driver is proven.
            continue
        if (manifest["platform"] == "ESP32_PLATFORM"
                and legacy_base.endswith("_repeater_bridge_rs232")
                and legacy_base[:-len("_bridge_rs232")] in combined_rs232):
            continue
        if manifest["platform"] != "ESP32_PLATFORM" or manifest["build_profile"] != "full":
            passthrough.append(record)
            continue
        target = manifest["target"]
        base = target.rstrip("_")
        kind = "plain"
        if base.lower().endswith("_observer_mqtt"):
            base = base[:-len("_observer_mqtt")]
            kind = "observer"
        elif base.lower().endswith("_bridge_espnow"):
            bridge_base = base[:-len("_bridge_espnow")]
            if bridge_base.lower() in combined_espnow or bridge_base.lower() in {
                "meshadventurer_sx1262_repeater", "meshadventurer_sx1268_repeater",
            }:
                base = bridge_base
            kind = "bridge"
        key = base.lower()
        if key == tlora_uart_target and kind == "observer":
            key = target.rstrip("_").lower()
        # G2's observer is the deployed Full identity. The other listed
        # observer/bridge recipes offer features that their plain images do
        # not combine, so keep the richer ordinary release choice.
        special = key in {
            "station_g2_repeater", "station_g2_room_server",
            "tbeam_sx1262_repeater", "tbeam_sx1276_repeater",
            "tbeam_sx1262_room_server", "tbeam_sx1276_room_server",
        }
        # Capacity exceptions may still need a dedicated bridge. Old plain
        # Meshadventurer images without the linked driver must not supersede it.
        if key in {"meshadventurer_sx1262_repeater", "meshadventurer_sx1268_repeater"}:
            special = key not in combined_espnow
        priority = {"observer": 20, "bridge": 10, "plain": 0 if special else 30}[kind]
        if key in combined_espnow and not proven_espnow(manifest):
            # A stale normal image must not displace the proven transport
            # while simultaneously suppressing its historical dedicated image.
            priority = -1
        if key in combined_rs232 and not proven_rs232(manifest):
            priority = -1
        old = chosen.get(key)
        if old is None or priority > old[0]:
            chosen[key] = (priority, record)
    selected = [item[1] for item in chosen.values()]
    for record in selected:
        manifest = record["manifest"]
        if requires_uart_full(manifest["target"]) and not proven_rs232(manifest):
            raise ValueError(manifest["target"] + ": Full release requires the verified "
                             "RS232 bridge from its normal repeater")
    for record in selected:
        if not qualified_full(record["manifest"]):
            raise ValueError(record["manifest"]["target"]
                             + ": ordinary ESP32 release requires a verified Full image")
    return passthrough + selected


def stage_release(work: Path, migration_work: Path, destination: Path, version: str,
                  source: str, pio_build_dir: Path | None = None) -> None:
    if destination.exists():
        raise FileExistsError(f"release already exists: {destination}")
    utility_build_dir = resolve_pio_build_dir(pio_build_dir, project_dir=ROOT)
    package_environment = os.environ.copy()
    package_environment["PLATFORMIO_BUILD_DIR"] = str(utility_build_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    short_source = source[:8]
    tooling_source = git("rev-parse", "HEAD")
    artifact_version = f"{version}-{short_source}"
    records = select_ordinary_full_records(collect_artifacts(work, artifact_version))
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
                            "sensor_profile": nrf52_sensor_profile(manifest),
                            "ota_update_methods": manifest.get("ota_update_methods", []),
                            "files": files})

        migrations = staging / "esp32-partition-migration"
        migrations.mkdir()
        with tempfile.TemporaryDirectory(prefix="migration-package-", dir=staging) as temp_packages:
            package_command = [sys.executable, "-B", "scripts/package_esp32_partition_migration.py",
                               "--build-dir", str(migration_work), "--output-dir", temp_packages,
                               "--pio-build-dir", str(utility_build_dir),
                               "--version", version, "--source", short_source]
            package_log = work / "migration-packaging.log"
            with package_log.open("w", encoding="utf-8") as output:
                result = subprocess.run(package_command, cwd=ROOT, stdout=output,
                                        stderr=subprocess.STDOUT, env=package_environment)
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
    environment = os.environ.copy()
    environment["OPTION3_PIO_JOBS"] = str(args.pio_jobs)
    try:
        utility_build_dir = resolve_pio_build_dir(environment=environment, project_dir=ROOT)
    except ValueError as error:
        parser.error(str(error))
    environment["PLATFORMIO_BUILD_DIR"] = str(utility_build_dir)
    work.mkdir(parents=True, exist_ok=True)
    environment["OUTPUT_DIR"] = str(work)
    if args.resume:
        archived = archive_capacity_rejected_attempts(work, args.firmware_version, source)
        if archived["archived"]:
            print(f'Archived {len(archived["archived"])} proven portable-capacity rejections: '
                  f'{archived["archive"]}', flush=True)
        print(f'Preserved {len(archived["preserved"])} other package manifests; '
              'ordinary resume qualification still applies.', flush=True)
        # All other packages retain the existing recipe/qualification checks
        # in build_legacy.sh; this helper never makes them resumable.
    run_logged([
        "bash", "build_legacy.sh", "build-firmwares-logging-matrix",
        "--firmware-version", args.firmware_version,
        "--radio-preset", "usa-cascade-fixed", "--profile", "cascade",
        "--require-ota", "--skip-kiss", "--resume",
    ], work / "release-build.log", environment)
    # Keep exact-identity migration inputs outside the ordinary firmware
    # matrix. collect_artifacts() walks recursively, so nesting these under
    # work would still publish a second Full image for the same G2 role.
    migration_work = ROOT / ".releases" / f".migration-build-{label}"
    migration_environment = environment.copy()
    migration_environment["OUTPUT_DIR"] = str(migration_work)
    build_migration_artifacts(migration_work, args.firmware_version, short_source,
                              args.pio_jobs, migration_environment, utility_build_dir)
    stage_release(work, migration_work, destination, args.firmware_version, source,
                  utility_build_dir)


if __name__ == "__main__":
    main()
