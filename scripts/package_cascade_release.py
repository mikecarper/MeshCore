#!/usr/bin/env python3
"""Stage a completed Option 3 matrix as audited, directly downloadable releases."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import quote, urljoin
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from firmware_memory_manifest import validate_package
from check_nrf52_uf2 import check_uf2
from package_nrf52_font_license import validate_artifact_notice
from package_meshtext_license import validate_artifact_notice as validate_meshtext_notice
from package_pocket_gui_license import validate_artifact_notice as validate_pocket_gui_notice
from package_cardputer_license import validate_artifact_notice as validate_cardputer_notice


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mota"))
from motalib import parse_endf_ident, parse_nrf52_layout, target_id_for_env

FIRMWARE_SUFFIXES = {".bin", ".uf2", ".hex", ".zip"}


def is_companion(target):
    return "companion" in target.lower() or "comp_radio" in target.lower()


def embedded_bootloader_variant(manifest):
    """Keep ordinary and exact, qualified bootloader-carrying images distinct."""
    metadata = manifest.get("embedded_bootloader")
    target = manifest["target"]
    artifact = manifest.get("artifact_target", target)
    capability = "bootloader.embedded"
    if metadata is None:
        if "-with-bootloader-" in artifact or capability in manifest.get("capabilities", []):
            raise ValueError(f"{artifact}: embedded bootloader lacks qualified metadata")
        return ""
    from nrf52_embedded_bootloader import (BOOT_SIZE, LEGACY_SHA256, MANIFEST_SHA256,
                                          VERSION, load_manifest, select_profile)
    if not isinstance(metadata, dict):
        raise ValueError(f"{artifact}: invalid embedded bootloader metadata")
    try:
        profile, _ = select_profile(load_manifest(), target)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"{artifact}: embedded bootloader requires an exact supported profile") from exc
    verified = any(check.get("capability") == capability
                   and check.get("present") is True
                   and check.get("source") == "packaged application"
                   for check in manifest.get("verification", []))
    offset = metadata.get("app_offset")
    if (manifest.get("platform") != "NRF52_PLATFORM"
            or metadata.get("release") != VERSION
            or metadata.get("profile") != profile["id"]
            or metadata.get("manifest_sha256") != MANIFEST_SHA256
            or metadata.get("asset_sha256") != profile["files"]["zip"]["sha256"]
            or not isinstance(metadata.get("candidate_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["candidate_sha256"])
            or metadata.get("legacy_sha256") not in (None, LEGACY_SHA256)
            or metadata.get("image_bytes") != BOOT_SIZE
            or type(offset) is not int or offset < 0 or offset % 4
            or metadata.get("activation") != "ota bootloader update"
            or metadata.get("automatic_delay_seconds") != 70
            or capability not in manifest.get("capabilities", []) or not verified
            or not artifact.startswith(target + "-with-bootloader-" + VERSION + "-")):
        raise ValueError(f"{artifact}: embedded bootloader qualification disagrees with its pinned release")
    return VERSION


def nrf52_sensor_profile(manifest):
    """Read the qualified sensor policy, never infer it from an artifact name."""
    choices = {value.removeprefix("sensor.profile.")
               for value in manifest.get("capabilities", []) if value.startswith("sensor.profile.")}
    if not choices:
        return ""
    target = manifest["target"]
    if len(choices) != 1 or not choices <= {"full", "reduced"}:
        raise ValueError(f"{target}: ambiguous or unknown sensor profile")
    verified = {item["capability"] for item in manifest.get("verification", [])
                if item.get("present") is True and item.get("source") in
                ("linked image", "packaged application")}
    if (manifest.get("platform") != "NRF52_PLATFORM" or is_companion(target)
            or not re.search(r"_(?:repeater|room_server|room_svr|sensor)(?:[_-]|$)", target, re.I)
            or manifest.get("verified") is not True
            or manifest.get("ota_update_verified") is not True
            or "lora" not in manifest.get("ota_update_methods", [])
            or "ota.update.lora" not in manifest.get("capabilities", [])
            or "ota.update.lora" not in verified):
        raise ValueError(f"{target}: sensor profile requires qualified nRF52 infrastructure LoRa OTA")
    return next(iter(choices))


def omit_retired_onboard_qspi_records(records):
    """Omit retired onboard-QSPI alternatives from ordinary releases.

    Call after package qualification. Keep the original files and OTA
    identities intact, and require each retired board's matching qualified
    external-QSPI Full/Reduced pair before excluding its recovery packages.
    """
    room_bases = (
        "solarxiao_30s_room_server", "solarxiao_33s_room_server",
        "xiao_nrf52_room_server",
        "ikoka_nano_nrf_22dbm_room_server", "ikoka_nano_nrf_30dbm_room_server",
        "ikoka_nano_nrf_33dbm_room_server", "ikoka_stick_nrf_22dbm_room_server",
        "ikoka_stick_nrf_30dbm_room_server", "ikoka_stick_nrf_33dbm_room_server",
        "ikoka_handheld_nrf_e22_30dbm_room_server",
        "lilygo_t-echo_room_server", "sensecap_solar_room_server",
        "thinknode_m1_room_server", "thinknode_m6_room_server", "wiotrackerl1_room_server",
        "lilygo_t-echo-lite_room_server",
    )
    legacy_targets = {}
    for base in room_bases + ("lilygo_t-echo-lite_repeater",):
        for legacy in (base, base + "_lora_ota_no_external_sensors"):
            legacy_targets[legacy] = base + "_lora_ota"
    for primary in ("lilygo_t-echo_repeater", "sensecap_solar_repeater", "thinknode_m1_repeater",
                    "thinknode_m6_repeater", "wiotrackerl1_repeater"):
        legacy_targets[primary + "_lora_ota_no_external_sensors"] = primary
    bridge_legacy = {"xiao_nrf52_repeater_bridge_rs232",
                     "xiao_nrf52_repeater_bridge_rs232_lora_ota_no_external_sensors"}
    legacy_targets.update({legacy: "xiao_nrf52_repeater" for legacy in bridge_legacy})
    targets = {record["manifest"]["target"].lower() for record in records}
    for legacy, primary in legacy_targets.items():
        if legacy not in targets:
            continue
        # collect_artifacts() has already checked the packaged OTA identity
        # and layout-matched sensor pair. Do not let retired leftovers
        # conceal a missing normal release pair for this exact hardware.
        variants = {embedded_bootloader_variant(record["manifest"]) for record in records
                    if record["manifest"]["target"].lower() == legacy}
        for variant in variants:
            replacements = [record["manifest"] for record in records
                            if record["manifest"]["target"].lower() == primary
                            and embedded_bootloader_variant(record["manifest"]) == variant]
            _require_onboard_qspi_replacement(legacy, primary, replacements, bridge_legacy)
    return [record for record in records
            if record["manifest"]["target"].lower() not in legacy_targets]


def _require_onboard_qspi_replacement(legacy, primary, replacements, bridge_legacy):
    # A bootloader-carrying pair cannot retire an ordinary pair, or vice versa.
    error = (legacy + ": ordinary onboard-QSPI release requires the qualified "
             "external-QSPI Full/Reduced pair for " + primary
             + "; retired artifacts cannot substitute for its distinct OTA identity")
    try:
        profiles = [nrf52_sensor_profile(manifest) for manifest in replacements]
    except ValueError as cause:
        raise ValueError(error) from cause
    if (sorted(profiles) != ["full", "reduced"]
            or any("hardware.qualification.pending" in manifest.get("capabilities", [])
                   or (manifest.get("ota_update_requirements") or {})
                   .get("lora", {}).get("storage") != "external_qspi"
                   for manifest in replacements)
            or replacements[0].get("source_commit") != replacements[1].get("source_commit")
            or replacements[0].get("ota_update_requirements") !=
               replacements[1].get("ota_update_requirements")):
        raise ValueError(error)
    if legacy in bridge_legacy:
        # The ordinary QSPI repeater may retire the separate UART role
        # only when both sensor profiles retain the linked driver and
        # prove its runtime controls in the packaged application.
        required = {"bridge.rs232": "linked image",
                    "bridge.rs232.merged": "packaged application"}
        for manifest in replacements:
            proven = {check.get("capability") for check in manifest.get("verification", [])
                      if check.get("present") is True
                      and required.get(check.get("capability")) == check.get("source")
                      and isinstance(check.get("evidence"), str) and check["evidence"].strip()}
            if (not required.keys() <= set(manifest.get("capabilities", []))
                    or not required.keys() <= proven):
                raise ValueError(error + "; replacement requires a verified runtime RS232 bridge")


def omit_internal_solar_room_records(records):
    """Compatibility entry point for the shared onboard-QSPI retirement policy."""
    return omit_retired_onboard_qspi_records(records)


def release_profile_label(manifest):
    sensor = nrf52_sensor_profile(manifest)
    if sensor:
        label = ("Full supported sensors" if sensor == "full" else "Reduced sensors") + " + LoRa OTA"
    else:
        label = manifest["build_profile"]
    bootloader = embedded_bootloader_variant(manifest)
    return label + " + OTAFIX " + bootloader + " bootloader" if bootloader else label


def validate_nrf52_sensor_pairs(records):
    """Publish the new sensor policies only as an identity/layout-matched pair.

    The DFU application is the authority for identity and flash geometry. Old
    releases without the new capability or publication suffix are unchanged.
    Partial/resumed matrices must not publish just one half of a new pair.
    """
    groups = {}
    for record in records:
        manifest = record["manifest"]
        bootloader = embedded_bootloader_variant(manifest)
        sensor = nrf52_sensor_profile(manifest)
        artifact_target = manifest.get("artifact_target", manifest["target"])
        hint = (manifest.get("platform") == "NRF52_PLATFORM"
                and (re.search(r"-(?:full|reduced)-ota$", artifact_target)
                     or any(re.search(r"-(?:full|reduced)-ota-v\d+\.", path.name)
                            for path in record["files"])))
        if not sensor:
            if hint:
                raise ValueError(f"{artifact_target}: sensor pair publication suffix lacks qualified metadata")
            continue
        if not artifact_target.endswith("-" + sensor + "-ota"):
            raise ValueError(f"{artifact_target}: sensor pair publication suffix disagrees with metadata")
        if bootloader and artifact_target != manifest["target"] + "-with-bootloader-" + bootloader + "-" + sensor + "-ota":
            raise ValueError(f"{artifact_target}: embedded bootloader sensor pair publication suffix disagrees")
        if any(not path.name.startswith(artifact_target + "-v") for path in record["files"]
               if path.suffix in FIRMWARE_SUFFIXES):
            raise ValueError(f"{artifact_target}: sensor pair artifact filename disagrees with metadata")
        packages = [path for path in record["files"] if path.suffix == ".zip"]
        if len(packages) != 1:
            raise ValueError(f"{artifact_target}: sensor pair requires exactly one DFU application package")
        try:
            with zipfile.ZipFile(packages[0]) as package:
                app = json.loads(package.read("manifest.json"))["manifest"]["application"]
                application = package.read(app["bin_file"])
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise ValueError(f"{artifact_target}: invalid sensor pair DFU application: {exc}") from exc
        identity = parse_endf_ident(application)
        layout = parse_nrf52_layout(application)
        if identity is None or layout is None or not identity.target_id or not identity.hw_id or not identity.fw_version:
            raise ValueError(f"{artifact_target}: sensor pair needs valid packaged EndF identity and flash layout")
        if identity.target_id != target_id_for_env(manifest["target"]):
            raise ValueError(f"{artifact_target}: sensor pair packaged target ID disagrees with logical target")
        if len(application) > layout.linked_app_end - layout.app_base:
            raise ValueError(f"{artifact_target}: sensor pair application exceeds its linked flash region")
        embedded = manifest.get("embedded_bootloader")
        if embedded:
            offset, size = embedded["app_offset"], embedded["image_bytes"]
            candidate = application[offset:offset + size]
            if (len(candidate) != size
                    or hashlib.sha256(candidate).hexdigest() != embedded["candidate_sha256"]
                    or b"BL embedded 2.4.12" not in application):
                raise ValueError(f"{artifact_target}: packaged embedded bootloader differs from qualification")
        proof = (identity.target_id, identity.hw_id, identity.fw_version,
                 layout.app_base, layout.linked_app_end, layout.stage_ceiling, layout.flags,
                 manifest.get("ota_update_requirements", {}), manifest.get("source_commit"),
                 tuple((key, embedded.get(key)) for key in ("profile", "candidate_sha256", "legacy_sha256"))
                 if embedded else ())
        groups.setdefault((manifest["target"], bootloader), []).append((sensor, proof))
    for (target, bootloader), pair in groups.items():
        target += "-with-bootloader-" + bootloader if bootloader else ""
        if sorted(item[0] for item in pair) != ["full", "reduced"]:
            raise ValueError(f"{target}: sensor pair requires exactly one full and one reduced profile")
        if pair[0][1] != pair[1][1]:
            raise ValueError(f"{target}: sensor pair disagrees on packaged target, firmware or storage layout")


def validate_manifest(manifest):
    target = manifest["target"]
    if "hardware.qualification.pending" in manifest.get("capabilities", []):
        raise ValueError(f"{target}: hardware qualification pending; experimental firmware cannot be published")
    if manifest.get("schema_version", 0) < 2 or not manifest.get("verified"):
        raise ValueError(f"{target}: firmware capability qualification failed")
    if not is_companion(target) and not manifest.get("ota_update_verified"):
        raise ValueError(f"{target}: infrastructure has no verified wireless updater")
    embedded_bootloader_variant(manifest)
    nrf52_sensor_profile(manifest)
    if "companion_radio_full" in target.lower():
        required = {"companion.usb_mota_source", "companion.mota_sender",
                    "companion.temp_radio", "companion.ota_cli"}
        required.add("companion.wifi_ota_seeder" if manifest["platform"] == "ESP32_PLATFORM"
                     else "companion.ble_mota_source")
        verified = {item["capability"] for item in manifest["verification"] if item["present"]}
        if not required <= verified:
            raise ValueError(f"{target}: Full Companion lacks verified MOTA sending")


def collect_artifacts(directory, version):
    records = []
    accounted = set()
    for path in sorted(directory.glob("*.capabilities.json")):
        stem = path.name.removesuffix(".capabilities.json")
        files = [candidate for candidate in directory.glob(stem + ".*")
                 if candidate.suffix in FIRMWARE_SUFFIXES]
        merged = directory / (stem + "-merged.bin")
        if merged.is_file():
            files.append(merged)
        if not files:
            continue  # A measured rejected attempt may leave only a manifest.
        if not stem.endswith("-" + version):
            raise ValueError(f"stale or mixed-version artifact: {stem}")
        manifest = json.loads(path.read_text())
        validate_manifest(manifest)
        extensions = {item.suffix for item in files}
        platform = manifest["platform"]
        if platform == "ESP32_PLATFORM" and (not merged.is_file() or not (directory / (stem + ".bin")).is_file()):
            raise ValueError(f"{stem}: ESP32 application/merged pair incomplete")
        if platform == "NRF52_PLATFORM":
            if ".uf2" not in extensions or (manifest.get("ota_update_verified") and ".zip" not in extensions):
                raise ValueError(f"{stem}: nRF52 UF2/DFU artifacts incomplete")
            check_uf2(directory / (stem + ".uf2"))
        if any(item.stat().st_size == 0 for item in files):
            raise ValueError(f"{stem}: empty firmware artifact")
        memory = validate_package(directory / stem)
        font_notice = validate_artifact_notice(directory / stem)
        if font_notice is not None:
            files.append(font_notice)
        meshtext_notice = validate_meshtext_notice(directory / stem)
        if meshtext_notice is not None:
            files.append(meshtext_notice)
        pocket_gui_notice = validate_pocket_gui_notice(directory / stem)
        if pocket_gui_notice is not None:
            files.append(pocket_gui_notice)
        cardputer_notice = validate_cardputer_notice(directory / stem)
        if cardputer_notice is not None:
            files.append(cardputer_notice)
        manifest["runtime_ram"] = {key: memory[key] for key in (
            "passed", "available_internal_bytes", "required_heap_bytes", "elf_sha256")}
        accounted.update(files)
        records.append({"manifest": manifest, "files": sorted(files) + [path, directory / (stem + ".memory.json")]})
    unaccounted = {path for path in directory.iterdir() if path.suffix in FIRMWARE_SUFFIXES} - accounted
    if unaccounted:
        raise ValueError("firmware without qualification: " + ", ".join(sorted(p.name for p in unaccounted)))
    if not records:
        raise ValueError("no qualified firmware artifacts")
    validate_nrf52_sensor_pairs(records)
    return records


def category(record):
    manifest = record["manifest"]
    if is_companion(manifest["target"]):
        return "companion"
    if nrf52_sensor_profile(manifest):
        return "lora-ota"
    if manifest["build_profile"] == "full":
        return "full-profiles"
    if "lora_ota" in manifest["target"].lower():
        return "lora-ota"
    if "logging" in manifest["artifact_target"].lower():
        return "logging"
    if any(role in manifest["target"].lower() for role in ("sensor", "terminal")):
        return "utility"
    return "repeater-room"


def joined_labels(values, conjunction="and"):
    if len(values) < 2:
        return "".join(values)
    return ", ".join(values[:-1]) + " " + conjunction + " " + values[-1]


def release_group_description(name, records, extras=()):
    """Describe actual page contents without assuming every matrix is current."""
    manifests = [record["manifest"] for record in records]
    platforms = {manifest["platform"] for manifest in manifests}
    platform = ({"NRF52_PLATFORM": "nRF52", "ESP32_PLATFORM": "ESP32"}
                .get(next(iter(platforms)), "") if len(platforms) == 1 else "")
    role_names = []
    for pattern, label in ((r"_repeater(?:[_-]|$)", "Repeater"),
                           (r"_room_(?:server|svr)(?:[_-]|$)", "Room Server"),
                           (r"_sensor(?:[_-]|$)", "Sensor"),
                           (r"_terminal(?:[_-]|$)", "Terminal")):
        if any(re.search(pattern, manifest["target"], re.I) for manifest in manifests):
            role_names.append(label)
    roles = joined_labels(role_names) or "Radio"
    operating_roles = joined_labels(["a " + role.lower() for role in role_names], "or")
    radio = ("an " if platform == "ESP32" else "a ") + platform + " radio" if platform else "a radio"
    if platform == "nRF52":
        radio = "an nRF52 radio"
    if name == "companion":
        return "Companion firmware", "Connect a radio to a phone or computer using the Companion mode supported by its hardware."
    if name == "lora-ota":
        lora = bool(manifests) and all("lora" in manifest.get("ota_update_methods", []) for manifest in manifests)
        title = platform + " " + roles + " firmware" if platform else (
            "LoRa OTA firmware" if lora else "Wireless update firmware")
        purpose = ("Run " + radio + " as " + operating_roles if operating_roles
                   else "Firmware for your exact radio")
        return title, purpose + (" with LoRa OTA reception." if lora else " with wireless updates.")
    if name == "full-profiles":
        title = (platform + " " if platform else "Full ") + roles + " firmware"
        return title, ("Full firmware for " + radio + " running as " + operating_roles + "." if operating_roles
                       else "Choose the Full firmware for your exact radio and role.")
    if name == "logging":
        return "USB packet logging firmware", "Use these images when the chosen radio role needs USB packet logging."
    if name == "utility":
        names = [path.name.lower() for path in extras]
        migration = any(filename.endswith("-migration.zip") or filename.startswith("esp32-partition-migration-")
                        for filename in names)
        bootloader = any("bootloader" in filename for filename in names)
        tools = joined_labels((["migration"] if migration else []) + (["bootloader"] if bootloader else []))
        if records:
            title = roles + " firmware" + (" and upgrade tools" if extras else "")
            return title, "Applications for " + (operating_roles or "your radio") + (
                ", plus exact-board " + (tools or "upgrade") + " tools." if extras else ".")
        return (tools.capitalize() if tools else "Upgrade") + " tools", (
            "Use the exact board/role migration package or board/storage bootloader file for an update or recovery."
            if migration and bootloader else
            "Use the exact board and role package to migrate an ESP32 partition layout." if migration else
            "Use the exact board/storage bootloader file for an update or recovery." if bootloader else
            "Use the exact hardware package and follow its included instructions.")
    return roles + " firmware", ("Run " + radio + " as " + operating_roles + "." if operating_roles
                                  else "Firmware for the exact radio hardware and role selected in the picker.")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def completed_matrix_failures(status, allow_partial=False):
    if status.get("state") == "completed" and status.get("exit_code") == "0":
        return []
    if not allow_partial:
        raise ValueError("the Option 3 matrix has not completed successfully")
    if status.get("state") != "failed" or status.get("exit_code") != "1":
        raise ValueError("partial publication requires a finished matrix, not a running or interrupted build")
    log = Path(status.get("log", ""))
    if not log.is_file():
        raise ValueError("partial publication requires the matrix failure summary")
    text = log.read_text(errors="replace")
    summaries = list(re.finditer(r"^Logging matrix completed with (\d+) failed build\(s\):\s*$", text, re.M))
    if not summaries:
        raise ValueError("partial publication requires the matrix failure summary")
    summary = summaries[-1]
    count = int(summary[1])
    lines = text[summary.end():].lstrip("\r\n").splitlines()
    failures = []
    for line in lines[:count]:
        match = re.fullmatch(r"  ([\w.+-]+) \(([^()]+)\) -> (.+)", line)
        if not match:
            raise ValueError("matrix failure summary is incomplete")
        failures.append({"target": match[1], "profile": match[2],
                         "log_file": Path(match[3]).name})
    if not count or len(failures) != count:
        raise ValueError("matrix failure summary is incomplete")
    return failures


def portable_profile_exclusions(status):
    if not status.get("log"):
        return []  # Older successful status files did not require a log path.
    text = Path(status["log"]).read_text(errors="replace")
    summaries = list(re.finditer(
        r"^(\d+) standard ESP32 target\(s\) exceeded the portable OTA slot and were deferred to the expanded FULL pass:\s*$",
        text, re.M))
    if not summaries:
        if re.search(r"^DEFERRED: ", text, re.M):
            raise ValueError("portable-profile exclusion summary is missing")
        return []
    summary = summaries[-1]
    count = int(summary[1])
    names = []
    for line in text[summary.end():].lstrip("\r\n").splitlines()[:count]:
        match = re.fullmatch(r"  ([\w.+-]+)", line)
        if not match:
            raise ValueError("portable-profile exclusion summary is incomplete")
        names.append(match[1])
    if not count or len(names) != count or len(set(names)) != count:
        raise ValueError("portable-profile exclusion summary is incomplete")
    return [{"target": name, "profile": "standard", "reason": "portable_slot_overflow"}
            for name in names]


def collect_local_release(directory, version, source):
    """Recheck a completed local release without adding migration-only images."""
    from build_esp32_partition_migration import verify_archive
    from package_esp32_partition_migration import BOARDS, published_full_image

    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("format") != "meshcore-local-release-v1"
            or manifest.get("source_commit") != source
            or manifest.get("firmware_version") != version
            or manifest.get("profile") != "cascade"
            or manifest.get("publication") != "local-only"):
        raise ValueError("local release belongs to another source, version or profile")
    expected_radio = {"frequency_mhz": 910.525, "bandwidth_khz": 62.5,
                      "spreading_factor": 7, "coding_rate": 5}
    if manifest.get("radio") != expected_radio:
        raise ValueError("local release did not use USA Cascade radio defaults")
    checked = set()
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError("invalid local release checksum line")
        name = match[2]
        path = directory / name
        if (name in checked or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(directory.resolve())
                or sha256(path) != match[1]):
            raise ValueError(f"local release checksum failed: {name}")
        checked.add(name)
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*")
              if p.is_file() and p != directory / "SHA256SUMS.txt"}
    if checked != actual:
        raise ValueError("local release checksum inventory is incomplete")
    records = []
    for group in sorted((directory / "firmware").iterdir()):
        if not group.is_dir():
            raise ValueError("unexpected file outside firmware groups")
        group_records = collect_artifacts(group, f"{version}-{source[:8]}")
        if any(category(record) != group.name for record in group_records):
            raise ValueError("local release firmware group disagrees with qualification")
        records.extend(group_records)
    # Standard and expanded Full images may retain the same OTA identity.
    # Match each independently qualified package by its exact file inventory;
    # the logical target alone cannot distinguish those publication profiles.
    indexed = {frozenset(str(p.relative_to(directory)) for p in record["files"]): record
               for record in records}
    entries = manifest["firmware"]
    if (len(indexed) != len(records) or len(entries) != len(records)
            or len(records) != manifest["firmware_target_count"]
            or any(len(entry["files"]) != len(set(entry["files"])) for entry in entries)
            or len({frozenset(entry["files"]) for entry in entries}) != len(entries)):
        raise ValueError("local release target inventory disagrees with qualification")
    for entry in entries:
        record = indexed.get(frozenset(entry["files"]))
        if record is None:
            raise ValueError("local release contains an unqualified target")
        qualified = record["manifest"]
        if (qualified["target"] != entry["target"]
                or qualified["artifact_target"] != entry["artifact_target"]
                or qualified.get("source_commit", source) != source
                or qualified["platform"] != entry["platform"]
                or qualified["build_profile"] != entry["profile"]
                or qualified.get("embedded_bootloader") != entry.get("embedded_bootloader")
                or set(entry["files"]) != {
                    str(p.relative_to(directory)) for p in record["files"]}):
            raise ValueError("local release manifest disagrees with qualified files")
    extras = []
    for board in BOARDS:
        path = directory / "esp32-partition-migration" / f"{board}-{version}-{source[:8]}-migration.zip"
        verify_archive(path, board, version, source[:8],
                       full_image=published_full_image(records, BOARDS[board].get("target")))
        extras.append(path)
    if manifest["migration_board_role_count"] != len(extras):
        raise ValueError("local release migration inventory is incomplete")
    bundles = list(directory.glob(f"esp32-partition-migration-{version}-{source[:8]}-release.zip"))
    if len(bundles) != 1:
        raise ValueError("local release needs exactly one migration bundle")
    with zipfile.ZipFile(bundles[0]) as archive:
        if set(archive.namelist()) != {p.name for p in extras} | {"release-manifest.json"}:
            raise ValueError("migration bundle inventory is incomplete")
        for path in extras:
            if hashlib.sha256(archive.read(path.name)).hexdigest() != sha256(path):
                raise ValueError("migration bundle differs from individual packages")
    extras.extend(bundles)
    bootloader_directory = directory / "nrf52-bootloaders"
    if (manifest.get("nrf52_bootloader_variants") is not None
            or manifest.get("nrf52_bootloaders") is not None
            or bootloader_directory.exists()
            or any(record["manifest"].get("embedded_bootloader") is not None for record in records)):
        if (manifest.get("nrf52_bootloader_variants") is None
                or manifest.get("nrf52_bootloaders") is None):
            raise ValueError("local release bootloader inventories are incomplete")
        from build_local_release import validate_embedded_bootloader_records
        from package_nrf52_bootloaders import validate_bootloader_catalog
        ordinary = [record for record in records
                    if not embedded_bootloader_variant(record["manifest"])]
        embedded = [record for record in records
                    if embedded_bootloader_variant(record["manifest"])]
        if manifest["nrf52_bootloader_variants"] != validate_embedded_bootloader_records(ordinary, embedded):
            raise ValueError("local release embedded bootloader inventory disagrees with qualified files")
        bootloaders = bootloader_directory
        if manifest.get("nrf52_bootloaders") != validate_bootloader_catalog(bootloaders):
            raise ValueError("local release standalone bootloader inventory disagrees with pinned files")
        extras.extend(sorted(path for path in bootloaders.iterdir() if path.is_file()))
    return records, extras, {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    provenance = parser.add_mutually_exclusive_group(required=True)
    provenance.add_argument("--build-status", type=Path)
    provenance.add_argument("--local-release", action="store_true",
                            help="Package the verified output of build_local_release.py")
    parser.add_argument("--version", default="1.17.1.5")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default="mikecarper/MeshCore")
    parser.add_argument("--stable", action="store_true", help="Stage a stable release family")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Publish qualified outputs from a finished matrix with failures, listing every failed attempt")
    args = parser.parse_args()
    firmware_label = f"v{args.version}-halo-keymind-cascade-dev"
    version = f"{firmware_label}-{args.commit[:8]}"
    extras = []
    if args.local_release:
        if args.allow_partial:
            parser.error("a completed local release cannot be partially published")
        records, extras, radio = collect_local_release(args.input, firmware_label, args.commit)
        status = {"state": "completed", "exit_code": "0"}
        failures, portable_exclusions = [], []
    else:
        status = dict(line.split("=", 1) for line in args.build_status.read_text().splitlines() if "=" in line)
        failures = completed_matrix_failures(status, args.allow_partial)
        portable_exclusions = portable_profile_exclusions(status)
        output_directory = Path(status["working_directory"]) / status["output_directory"]
        if output_directory.resolve() != args.input.resolve():
            raise ValueError("build status belongs to another output directory")
        if status.get("source_commit") != args.commit or status.get("firmware_version") != firmware_label:
            raise ValueError("build status belongs to another source revision or version")
        if status.get("firmware_profile") != "cascade":
            raise ValueError("matrix did not use Cascade runtime defaults")
        radio = {key: status["radio_" + key] for key in ("frequency", "bandwidth", "sf", "cr")}
        records = collect_artifacts(args.input, version)
    records = omit_retired_onboard_qspi_records(records)
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("staging directory must be empty; existing releases are never overwritten")
    base_tag = version
    groups = []
    descriptions = {}
    for name in ("companion", "repeater-room", "utility", "logging", "lora-ota", "full-profiles"):
        current = []
        count = 0
        chunks = []
        for record in (r for r in records if category(r) == name):
            if count + len(record["files"]) > 900:
                chunks.append(current)
                current, count = [], 0
            current.append(record)
            count += len(record["files"])
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks):
            key = name + (f"-{index + 1}" if index else "")
            tag = base_tag if key == "companion" else f"{key}-{base_tag}"
            label, purpose = release_group_description(name, chunk, extras if key == "utility" else [])
            if len(chunks) > 1:
                label += f" (part {index + 1} of {len(chunks)})"
            title = f"MeshCore {args.version}{'' if args.stable else ' Dev'} - {label}"
            descriptions[key] = {"label": label, "purpose": purpose, "parts": len(chunks)}
            groups.append({"key": key, "tag": tag, "title": title, "prerelease": not args.stable, "records": chunk})
    if extras and not any(g['key'] == 'utility' for g in groups):
        label, purpose = release_group_description("utility", [], extras)
        groups.append({'key': 'utility', 'tag': 'utility-' + base_tag,
                       'title': f'MeshCore {args.version}{"" if args.stable else " Dev"} - {label}',
                       'prerelease': not args.stable, 'records': []})
        descriptions["utility"] = {"label": label, "purpose": purpose, "parts": 1}
    links = "\n".join(f"- [{descriptions[g['key']]['label']}](https://github.com/{args.repo}/releases/tag/{g['tag']})"
                      for g in groups)
    source_url = f"https://github.com/{args.repo}/blob/{args.commit}"
    owner, repository = args.repo.split("/", 1)
    docs_url = f"https://{owner}.github.io/" + ("" if repository.lower() == owner.lower() + ".github.io"
                                               else quote(repository, safe="") + "/")
    release_guide = docs_url + f"releases/{args.version}/"
    rows = []
    for group in groups:
        destination = args.output / group["key"]
        destination.mkdir(parents=True)
        summaries = []
        for record in group["records"]:
            manifest = record["manifest"]
            if manifest.get("source_commit", args.commit) != args.commit:
                raise ValueError("qualified firmware belongs to another source")
            file_links = []
            for path in record["files"]:
                shutil.copy2(path, destination / path.name)
                if path.suffix in FIRMWARE_SUFFIXES:
                    url = f"https://github.com/{args.repo}/releases/download/{group['tag']}/{quote(path.name)}"
                    label = "merged.bin (USB)" if path.name.endswith("-merged.bin") else path.suffix[1:]
                    file_links.append(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')
            methods = ", ".join(manifest.get("ota_update_methods", [])) or "USB"
            # Older capability manifests inherit source provenance from the
            # validated completed release inventory. Preserve their raw bytes.
            summaries.append({**manifest, "source_commit": args.commit,
                              "sensor_profile": nrf52_sensor_profile(manifest),
                              "files": [path.name for path in record["files"]]})
            rows.append(f"<tr><td>{html.escape(manifest['artifact_target'])}</td><td>{html.escape(release_profile_label(manifest))}</td><td>{html.escape(methods)}</td><td>{' | '.join(file_links)}</td></tr>")
        if group['key'] == 'utility':
            for path in extras:
                if (destination / path.name).exists():
                    raise ValueError(f'duplicate utility asset: {path.name}')
                shutil.copy2(path, destination / path.name)
        (destination / "TARGET-MANIFEST.json").write_text(json.dumps(summaries, indent=2) + "\n")
        columns = ("artifact_target", "target", "platform", "build_profile", "sensor_profile",
                   "ota_update_methods", "files")
        (destination / "TARGET-MANIFEST.tsv").write_text(
            "\t".join(columns) + "\n" + "".join(
                "\t".join(",".join(row.get(key, [])) if isinstance(row.get(key), list)
                          else str(row.get(key, "")) for key in columns) + "\n"
                for row in summaries))
        guide = (ROOT / "docs/full_companion_features.md").read_text()
        guide = re.sub(r"\]\(([^)]+)\)",
                       lambda match: "](" + urljoin(source_url + "/docs/", match[1]) + ")", guide)
        (destination / "FULL-COMPANION-FEATURES.md").write_text(guide)
        exclusions = args.input / "ota-excluded-targets.txt"
        if exclusions.is_file():
            shutil.copy2(exclusions, destination / exclusions.name)
        partial_note = ""
        portable_note = ""
        if portable_exclusions:
            report = {"source": args.commit, "version": args.version,
                      "portable_slot_bytes": 1310720, "exclusions": portable_exclusions}
            (destination / "PORTABLE-PROFILE-EXCLUSIONS.json").write_text(json.dumps(report, indent=2) + "\n")
            (destination / "PORTABLE-PROFILE-EXCLUSIONS.md").write_text(
                "# Portable image limits\n\n"
                "These standard profiles exceeded the 1.25 MiB (1,310,720-byte) application slot. "
                "The matrix attempted their expanded Full alternatives instead. Check TARGET-MANIFEST.json "
                "for qualified Full images and use their matching partition layout. "
                "Smaller images that passed qualification remain available.\n\n"
                + "".join(f"- `{item['target']}`\n" for item in portable_exclusions))
            report_url = f"https://github.com/{args.repo}/releases/download/{group['tag']}/PORTABLE-PROFILE-EXCLUSIONS.md"
            portable_note = (f"**Portable image limits:** {len(portable_exclusions)} standard profile(s) exceeded "
                             f"the 1.25 MiB slot and were redirected to the Full pass. [Affected profiles]({report_url}).\n\n")
        if failures:
            report = {"source": args.commit, "version": args.version, "matrix_state": status["state"],
                      "matrix_exit_code": int(status["exit_code"]), "failures": failures}
            (destination / "BUILD-FAILURES.json").write_text(json.dumps(report, indent=2) + "\n")
            (destination / "BUILD-FAILURES.md").write_text(
                "# Build attempts awaiting repair\n\n"
                f"Source: `{args.commit}`. The matrix finished with exit code {status['exit_code']}.\n\n"
                "Published files passed their individual capability, size and memory checks. "
                "These failed attempts are omitted pending repair; another profile for the same board may be available.\n\n"
                "| Target | Profile | Build log |\n| --- | --- | --- |\n"
                + "".join(f"| `{f['target']}` | `{f['profile']}` | `{f['log_file']}` |\n" for f in failures))
            report_url = f"https://github.com/{args.repo}/releases/download/{group['tag']}/BUILD-FAILURES.md"
            partial_note = (f"**Partial matrix:** {len(failures)} build attempt(s) failed and await repair. "
                            f"Only individually qualified firmware is published. [Missing builds]({report_url}).\n\n")
        channel = 'Stable release' if args.stable else 'Development prerelease'
        description = descriptions[group["key"]]
        split_note = ("These pages are split for download limits and contain the same firmware version; "
                      "the picker finds the correct page for your board.\n\n" if description["parts"] > 1 else "")
        body = (f"[Open the firmware picker]({docs_url}firmware_picker/) | "
                f"[What's new in {args.version}]({release_guide})\n\n"
                f"# {group['title']}\n\n"
                f"> **{channel}.** USA Cascade.\n\n"
                + description["purpose"] + "\n\n"
                "Select the exact board, radio, storage and role in the picker.\n\n"
                + split_note + partial_note + portable_note
                + f"[Installation and upgrade guide]({release_guide}) | "
                f"[Feature controls]({docs_url}role_feature_switches/) | "
                f"[Full Companion guide]({docs_url}full_companion_features/)\n\n"
                "**Testing:** Published images passed build, capability and memory checks. "
                "Physical coverage varies by board and update route; "
                f"see the [release guide]({release_guide}).\n\n"
                "Other firmware and tools:\n\n" + links + "\n\n"
                f"USA Cascade defaults: **{radio['frequency']} MHz, BW{radio['bandwidth']}, "
                f"SF{radio['sf']}, CR{radio['cr']}**. Saved radio settings take precedence.\n\n"
                f"Firmware identifier: `{base_tag}`. Source: `{args.commit}`.\n")
        (args.output / (group["key"] + "-notes.md")).write_text(body)
        (destination / "BUILD-NOTES.txt").write_text(body)
    picker = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
              f"<title>MeshCore {html.escape(args.version)} USA Cascade</title>"
              "<style>body{font:16px system-ui;margin:2rem}input{font:inherit;padding:.5rem;width:90%;max-width:40rem}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.7rem;border-bottom:1px solid #ccc}td:first-child{overflow-wrap:anywhere}a{color:#165acb}</style>"
              f"<h1>MeshCore {html.escape(args.version)} USA Cascade</h1>"
              "<p>Match your exact hardware. Full Companions can send MOTA; USB is their normal update method.</p>"
              f"<p><a href='{source_url}/docs/full_companion_features.md'>Feature switches and update instructions</a></p>"
              "<input id=search placeholder='Filter by board, profile, or update method' aria-label='Filter firmware'>"
              "<table><thead><tr><th>Target</th><th>Profile</th><th>Self-update</th><th>Downloads</th></tr></thead><tbody>"
              + "\n".join(rows) + "</tbody></table>"
              "<script>document.getElementById('search').oninput=function(){const q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))}</script>")
    local_picker = picker
    for group in groups:
        local_picker = local_picker.replace(
            f"https://github.com/{args.repo}/releases/download/{group['tag']}/",
            group["key"] + "/")
    local_picker = local_picker.replace(
        f"{source_url}/docs/full_companion_features.md",
        groups[0]["key"] + "/FULL-COMPANION-FEATURES.md")
    (args.output / f"FIRMWARE-PICKER-{args.version}.html").write_text(local_picker)
    for group in groups:
        destination = args.output / group["key"]
        (destination / f"FIRMWARE-PICKER-{args.version}.html").write_text(picker)
        files = sorted(destination.iterdir())
        (destination / "SHA256SUMS.txt").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
        group["asset_count"] = len(files) + 1
        group["target_count"] = len(group.pop("records"))
    (args.output / "release-plan.json").write_text(json.dumps({"source": args.commit, "version": args.version, "radio": radio, "groups": groups,
        "provenance": "local-release" if args.local_release else "matrix-status",
        "matrix": {"state": status["state"], "exit_code": int(status["exit_code"]), "failures": failures,
                   "portable_profile_exclusions": portable_exclusions}}, indent=2) + "\n")
    print(json.dumps({"targets": len(records), "groups": groups}, indent=2))


if __name__ == "__main__":
    main()
