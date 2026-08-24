#!/usr/bin/env python3
"""Update the curated Mesh America logging catalog from a complete matrix."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_PATH = SCRIPT_DIR / "update-provider-release.py"


def load_common():
    spec = importlib.util.spec_from_file_location("provider_release_common", COMMON_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {COMMON_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


common = load_common()
CONSTRAINED_LOGGING_TARGETS = {
    "Tiny_Relay_repeater",
    "RAK_3x72_repeater",
    "wio-e5_repeater",
    "wio-e5-repeater_bridge_rs232",
    "wio-e5-mini_companion_radio_usb",
    "wio-e5-mini_repeater",
    "wio-e5-mini_sensor",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=SCRIPT_DIR / "keymind-cascade-logging-v1.16.0-provider.json",
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--artifact-version", required=True)
    parser.add_argument("--logging-main-tag", required=True)
    parser.add_argument("--logging-utility-tag", required=True)
    parser.add_argument("--full-tag", required=True)
    parser.add_argument("--main-tag", required=True)
    parser.add_argument("--advanced-tag", required=True)
    parser.add_argument("--utility-tag", required=True)
    parser.add_argument("--repo", default="mikecarper/MeshCore")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--companion-only",
        action="store_true",
        help=(
            "update only Companion roles, preserving the versions and files for "
            "all unaffected roles"
        ),
    )
    return parser.parse_args()


def is_logging_utility(identity: str) -> bool:
    lowered = identity.lower()
    return any(marker in lowered for marker in ("kiss_modem", "terminal_chat", "sensor"))


def is_constrained_logging(identity: str) -> bool:
    return (
        identity.endswith("-logging")
        and identity.removesuffix("-logging") in CONSTRAINED_LOGGING_TARGETS
    )


def release_tag(identity: str, args: argparse.Namespace) -> str:
    lowered = identity.lower()
    if "full-logging-ota" in lowered or "full-usb-wifi-ota" in lowered:
        return args.full_tag
    if "-logging" in lowered:
        return args.logging_utility_tag if is_logging_utility(identity) else args.logging_main_tag
    if (
        "companion_radio_full" in lowered
        or "-full-ota" in lowered
        or "lora_ota" in lowered
        or "observer_mqtt" in lowered
    ):
        return args.advanced_tag
    if is_logging_utility(identity):
        return args.utility_tag
    if any(
        marker in lowered
        for marker in ("companion", "comp_radio", "repeater", "room_server")
    ):
        return args.main_tag
    return args.utility_tag


def identity_category(identity: str) -> str:
    lowered = identity.lower()
    if "full-usb-wifi-ota" in lowered:
        return "full-unified"
    if "full-logging-ota" in lowered:
        return "full-logging"
    if "-logging" in lowered:
        return "logging-utility" if is_logging_utility(identity) else "logging-main"
    if (
        "companion_radio_full" in lowered
        or "lora_ota" in lowered
        or "observer_mqtt" in lowered
    ):
        return "standard-advanced"
    if is_logging_utility(identity):
        return "standard-utility"
    return "standard-main"


def update_catalog(catalog: dict, release_files: dict[str, list[Path]], args: argparse.Namespace) -> dict:
    display_version = args.artifact_version.rsplit("-", 1)[0]
    if args.companion_only:
        catalog["description"] = (
            "Keymind Cascade MeshCore v1.17.1.1 infrastructure firmware plus "
            f"corrected {display_version} Companion firmware, with Halo/Keymind "
            "retry tuning, Cascade defaults, and the USA/Canada 910.525 MHz / "
            "SF7 / BW62.5 / CR5 preset. Companion device power saving is enabled "
            "by default and migrated on once in the corrected release. Logging "
            "profiles are intended for a USB-connected MQTT/logging host. "
            "Unaffected roles remain on v1.17.1.1."
        )
    else:
        catalog["description"] = (
            f"Keymind Cascade MeshCore {display_version} firmware with Halo/Keymind "
            "retry tuning, Cascade defaults, and the USA/Canada 910.525 MHz / SF7 / "
            "BW62.5 / CR5 preset. This catalog contains packet-logging builds with "
            "USB debug enabled except on seven flash-constrained STM32 targets and "
            "selected non-logging utilities. Unified expanded-partition FULL builds "
            "provide USB packet logging and direct WiFi MQTT in one image with a "
            "persistent off/USB/WiFi/both selector. Host software can consume the "
            "USB serial log and publish it separately. nRF52 Full Companion uses "
            "separate USB interfaces for Companion traffic and plaintext logging, "
            "replacing its older separate USB-logging image. Open Release "
            "notes for role, hardware, installation, and partition requirements."
        )

    entry_count = 0
    file_count = 0
    used_identities: set[str] = set()
    category_counts = {
        "logging-main": 0,
        "logging-utility": 0,
        "full-logging": 0,
        "full-unified": 0,
        "standard-main": 0,
        "standard-advanced": 0,
        "standard-utility": 0,
    }
    processed_firmware_ids: set[int] = set()
    firmware_priorities: dict[int, int] = {}

    for device in catalog["device"]:
        device_type = device["type"]
        for firmware in device["firmware"]:
            if args.companion_only and not firmware["role"].startswith("companion"):
                continue
            old_keys = list(firmware["version"])
            old_version = next(iter(firmware["version"].values()))
            old_notes = old_version["notes"]
            old_identities = common.ordered_catalog_identities(firmware)
            identities: list[str] = []
            observer_entry = False
            for old_identity in old_identities:
                identity, converted_observer = common.resolve_release_identity(
                    old_identity, release_files
                )
                if identity not in identities:
                    identities.append(identity)
                observer_entry = (
                    observer_entry
                    or converted_observer
                    or "observer_mqtt" in identity.lower()
                )
            processed_firmware_ids.add(id(firmware))
            firmware_priorities[id(firmware)] = common.legacy_identity_priority(
                old_identities, identities
            )
            paths: list[Path] = []
            for identity in identities:
                if identity not in release_files:
                    raise ValueError(f"no release artifact matches {identity!r}")
                used_identities.add(identity)
                paths.extend(release_files[identity])

                category_counts[identity_category(identity)] += 1

            paths.sort(key=lambda path: common.file_sort_key(path, device_type))
            files = []
            for path in paths:
                identity = common.artifact_identity(path.name)
                file_type, title = common.file_type_and_title(path, device_type)
                files.append(
                    {
                        "type": file_type,
                        "name": path.name,
                        "url": common.download_url(
                            args.repo,
                            release_tag(identity, args),
                            path.name,
                        ),
                        "title": title,
                    }
                )

            version_key = common.replacement_version_key(old_keys[0], args.artifact_version)
            if observer_entry:
                notes = common.observer_notes(
                    firmware["role"], display_version, identities, old_notes
                )
                firmware["subTitle"] = (
                    "Unified FULL USB + Wi-Fi observer"
                    if any(
                        "-full-usb-wifi-ota" in identity.lower()
                        for identity in identities
                    )
                    else "FULL MQTT observer"
                )
            else:
                notes = common.replace_release_version(old_notes, display_version)
            if "USA/Canada 910.525 MHz" not in notes:
                notes = notes.replace(
                    "with Halo/Keymind retry tuning and Cascade defaults.",
                    "with Halo/Keymind retry tuning, Cascade defaults, and the "
                    "USA/Canada 910.525 MHz / SF7 / BW62.5 / CR5 preset.",
                    1,
                )
            if any(is_constrained_logging(identity) for identity in identities):
                old_profile = "turns USB debug and packet logging ON."
                standard_profile = (
                    "normal features for the selected role and hardware, with USB "
                    "debug/packet logging and LoRa self-update OFF."
                )
                replacement = (
                    "turns packet logging ON while keeping USB debug OFF to fit this "
                    "flash-constrained STM32 target."
                )
                if replacement in notes:
                    pass
                elif old_profile in notes:
                    notes = notes.replace(old_profile, replacement, 1)
                elif standard_profile in notes:
                    notes = notes.replace(
                        standard_profile,
                        standard_profile
                        + " The -logging variant turns packet logging ON while keeping "
                        "USB debug OFF to fit this flash-constrained STM32 target.",
                        1,
                    )
                else:
                    raise ValueError(
                        "cannot update constrained logging guidance for "
                        f"{identities!r}"
                    )
            if firmware["role"].startswith("companion"):
                notes = common.append_companion_power_saving_note(
                    notes, display_version
                )
                notes = common.normalize_runtime_companion_metadata(
                    firmware, notes
                )
                if device_type == "nrf52" and any(
                    "companion_radio_full" in identity.lower()
                    for identity in identities
                ):
                    notes = common.normalize_nrf52_full_companion_metadata(
                        firmware, notes
                    )
            if any("-logging" in identity.lower() for identity in identities):
                logging_note = (
                    "LOGGING USE - This USB packet-logging build is for a "
                    "USB-connected MQTT/logging host that reads the serial stream "
                    "and publishes it. It is not the direct on-device Wi-Fi MQTT "
                    "bridge."
                )
                if logging_note not in notes:
                    notes += "\n\n" + logging_note
            firmware["version"] = {version_key: {"notes": notes, "files": files}}
            entry_count += 1
            file_count += len(files)

    collapsed_entries = common.deduplicate_resolved_firmware(
        catalog, firmware_priorities
    )
    retained_processed = [
        firmware
        for device in catalog["device"]
        for firmware in device["firmware"]
        if id(firmware) in processed_firmware_ids
    ]
    entry_count = len(retained_processed)
    file_count = sum(
        len(version["files"])
        for firmware in retained_processed
        for version in firmware["version"].values()
    )
    used_identities = {
        identity
        for firmware in retained_processed
        for identity in common.ordered_catalog_identities(firmware)
    }
    category_counts = {key: 0 for key in category_counts}
    for identity in used_identities:
        category_counts[identity_category(identity)] += 1
    if file_count == 0:
        raise ValueError("catalog update produced no files")
    print(
        json.dumps(
            {
                "catalog_entries": entry_count,
                "catalog_files": file_count,
                "release_identities_used": len(used_identities),
                "identity_categories": category_counts,
                "collapsed_alias_entries": collapsed_entries,
                "update_mode": "companion-only" if args.companion_only else "all",
            },
            sort_keys=True,
        )
    )
    return catalog


def main() -> int:
    args = parse_args()
    output = args.output or args.catalog
    try:
        if not args.artifact_version.startswith("v"):
            raise ValueError("--artifact-version must start with 'v'")
        with args.catalog.open(encoding="utf-8") as stream:
            catalog = json.load(stream)
        release_files = common.index_release_files(args.release_dir, args.artifact_version)
        updated = update_catalog(catalog, release_files, args)
        if not args.check_only:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(updated, ensure_ascii=True, indent=2) + "\n",
                encoding="ascii",
            )
            print(f"Wrote {output}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
