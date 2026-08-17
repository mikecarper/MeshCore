#!/usr/bin/env python3
"""Update the curated Mesh America catalog from a completed release matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote


FIRMWARE_EXTENSIONS = {".bin", ".hex", ".uf2", ".zip"}
FILENAME_VERSION_RE = re.compile(
    r"-v\d+(?:\.\d+)+(?:-[A-Za-z0-9_.-]+)?$"
)
VERSION_LABEL_RE = re.compile(r"v\d+(?:\.\d+)+")
CATALOG_VERSION_TEXT_RE = re.compile(
    r"Keymind Cascade MeshCore v[0-9][A-Za-z0-9_.-]*"
)

LEGACY_PORTABLE_CEILING_EXCEPTIONS = {
    "heltec_ct62_repeater_lora_ota_no_external_sensors",
    "heltec_tracker_v1_1_repeater_observer_mqtt",
    "heltec_tracker_v1_1_room_server_observer_mqtt",
    "heltec_tracker_v2_repeater_observer_mqtt",
    "heltec_tracker_v2_room_server_observer_mqtt",
    "lilygo_tbeam_1w_repeater_observer_mqtt",
    "station_g2_repeater_observer_mqtt",
    "station_g3_esp32_repeater_observer_mqtt",
    "tbeam_sx1262_repeater_observer_mqtt",
    "tbeam_sx1276_repeater_observer_mqtt",
    "tenstar_c3_sx1262_repeater_lora_ota_no_external_sensors",
    "tenstar_c3_sx1268_repeater_lora_ota_no_external_sensors",
    "t_beam_s3_supreme_sx1262_repeater_observer_mqtt",
}

LEGACY_COMPANION_IDENTITY_ALIASES = {
    # The short-lived install-capable LoRa-OTA Wi-Fi Companion profile was
    # replaced by the ordinary Wi-Fi Companion plus the safer, serve-only Full
    # Companion profile. Keep the curated role available without claiming that
    # the replacement can LoRa-install firmware onto itself.
    "LilyGo_TBeam_1W_companion_radio_wifi-ota":
        "LilyGo_TBeam_1W_companion_radio_wifi",
}


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=script_dir / "keymind-cascade-v1.16.0-provider.json",
        help="existing curated provider catalog",
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument(
        "--artifact-version",
        required=True,
        help="filename version token, for example v1.17.1.1-759a35fc",
    )
    parser.add_argument("--main-tag", required=True)
    parser.add_argument("--advanced-tag", required=True)
    parser.add_argument("--utility-tag", required=True)
    parser.add_argument("--repo", default="mikecarper/MeshCore")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate and report without writing the output catalog",
    )
    parser.add_argument(
        "--companion-only",
        action="store_true",
        help=(
            "update only Companion roles, preserving the versions and files for "
            "all unaffected roles"
        ),
    )
    return parser.parse_args()


def artifact_identity(name: str) -> str:
    stem = Path(name).stem
    if stem.endswith("-merged"):
        stem = stem[: -len("-merged")]
    identity = FILENAME_VERSION_RE.sub("", stem)
    if identity == stem:
        raise ValueError(f"cannot find a firmware version suffix in {name!r}")
    return identity


def index_release_files(
    release_dir: Path, artifact_version: str
) -> dict[str, list[Path]]:
    if not release_dir.is_dir():
        raise ValueError(f"release directory not found: {release_dir}")

    result: dict[str, list[Path]] = {}
    for path in sorted(release_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in FIRMWARE_EXTENSIONS:
            continue
        if path.stat().st_size <= 0:
            raise ValueError(f"empty release artifact: {path}")
        versioned_stem = path.stem
        if versioned_stem.endswith("-merged"):
            versioned_stem = versioned_stem[: -len("-merged")]
        if not versioned_stem.endswith(f"-{artifact_version}"):
            raise ValueError(
                f"mixed or unexpected firmware version in release artifact: {path.name}"
            )
        result.setdefault(artifact_identity(path.name), []).append(path)
    if not result:
        raise ValueError(f"no firmware artifacts found in {release_dir}")
    return result


def ordered_catalog_identities(firmware: dict) -> list[str]:
    identities: list[str] = []
    for version in firmware["version"].values():
        for file_info in version["files"]:
            identity = artifact_identity(file_info["name"])
            if identity not in identities:
                identities.append(identity)
    return identities


def resolve_release_identity(
    old_identity: str, release_files: dict[str, list[Path]]
) -> tuple[str, bool]:
    if old_identity in release_files:
        return old_identity, False

    alias = LEGACY_COMPANION_IDENTITY_ALIASES.get(old_identity)
    if alias is not None and alias in release_files:
        return alias, False

    # The previous catalog used expanded-partition FULL MQTT images. The new
    # standard matrix emits a portable MQTT observer under the same target name
    # without the build-only "-full-ota" filename marker.
    full_suffix = "-full-ota"
    if old_identity.endswith(full_suffix):
        portable_identity = old_identity[: -len(full_suffix)]
        if portable_identity in release_files:
            return portable_identity, True

    raise ValueError(f"no new release artifact matches catalog target {old_identity!r}")


def replace_legacy_companion_ota_notes(notes: str) -> str:
    paragraphs: list[str] = []
    for paragraph in notes.split("\n\n"):
        if paragraph.startswith("PROFILE ") and "LoRa OTA companion:" in paragraph:
            paragraphs.append(
                "PROFILE - Standard Wi-Fi Companion: keeps the normal Wi-Fi and "
                "USB Companion interfaces with LoRa self-update off. Use Full "
                "Companion when the complete transports and host-backed LoRa-OTA "
                "seeding tools are needed."
            )
        elif paragraph.startswith("LORA OTA "):
            continue
        elif paragraph.startswith("SELECTION "):
            paragraphs.append(paragraph.replace(", OTA.", "."))
        else:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def release_tag_for_identity(identity: str, args: argparse.Namespace) -> str:
    lowered = identity.lower()
    if (
        "companion_radio_full" in lowered
        or "lora_ota" in lowered
        or "observer_mqtt" in lowered
    ):
        return args.advanced_tag
    if (
        "kiss_modem" in lowered
        or "terminal_chat" in lowered
        or "sensor" in lowered
    ):
        return args.utility_tag
    if (
        "companion" in lowered
        or "comp_radio" in lowered
        or "repeater" in lowered
        or "room_server" in lowered
    ):
        return args.main_tag
    return args.utility_tag


def file_type_and_title(path: Path, device_type: str) -> tuple[str, str]:
    lowered = path.name.lower()
    if lowered.endswith("-merged.bin"):
        return "flash-wipe", "Full install (bootloader + firmware)"
    if path.suffix.lower() == ".bin" and device_type == "esp32":
        return "flash-update", "Update (app only)"
    if path.suffix.lower() == ".zip" and device_type == "nrf52":
        return "flash", "Serial DFU package"
    if path.suffix.lower() == ".uf2":
        return "download", "UF2 download"
    if path.suffix.lower() == ".hex":
        return "download", "HEX download"
    return "download", "Download"


def file_sort_key(path: Path, device_type: str) -> tuple[int, str]:
    file_type, _ = file_type_and_title(path, device_type)
    rank = {
        "flash-wipe": 10,
        "flash-update": 20,
        "flash": 30,
    }.get(file_type, 40 if path.suffix.lower() == ".uf2" else 50)
    return rank, path.name.lower()


def download_url(repo: str, release_tag: str, name: str) -> str:
    return (
        f"https://github.com/{repo}/releases/download/{quote(release_tag, safe='')}/"
        f"{quote(name, safe='')}"
    )


def replacement_version_key(old_key: str, artifact_version: str) -> str:
    match = VERSION_LABEL_RE.search(old_key)
    if not match:
        raise ValueError(f"catalog version key has no version token: {old_key!r}")
    return old_key[: match.start()] + artifact_version


def replace_release_version(notes: str, display_version: str) -> str:
    return CATALOG_VERSION_TEXT_RE.sub(
        f"Keymind Cascade MeshCore {display_version}", notes
    )


def partition_warning(identities: list[str]) -> str | None:
    if not any(
        identity.lower() in LEGACY_PORTABLE_CEILING_EXCEPTIONS
        for identity in identities
    ):
        return None
    return (
        "PARTITION - This target fits its generated app partition but is larger "
        "than the legacy 1,310,720-byte portable app ceiling. If the device has "
        "an older or smaller partition table, flash the matching merged image "
        "once before using app-only updates."
    )


def append_partition_warning(notes: str, identities: list[str]) -> str:
    warning = partition_warning(identities)
    if warning is None or warning in notes:
        return notes
    return notes + "\n\n" + warning


def append_companion_power_saving_note(notes: str, display_version: str) -> str:
    paragraph = (
        f"POWER SAVING - {display_version} enables Companion device power saving "
        "by default. On first boot it migrates the saved Companion setting to on "
        "once, repairing devices that carried the regressed off value. A later "
        "explicit `powersaving off` choice remains persistent. This controls "
        "MCU/GPS idle saving and is separate from LoRa receive power saving "
        "(RXPS). On nRF52, an active USB data-host connection can intentionally "
        "keep the device awake; USB power from a charger alone does not."
    )
    if paragraph in notes:
        return notes
    return notes + "\n\n" + paragraph


def observer_notes(
    role: str, display_version: str, identities: list[str], old_notes: str
) -> str:
    role_paragraphs = {
        "repeater": (
            "ROLE - Repeater: always-on infrastructure that relays mesh traffic. "
            "Configure its name/location, radio settings, and admin password."
        ),
        "roomServer": (
            "ROLE - Room Server: hosts a MeshCore room and its message service. "
            "Configure the room identity/password and administrator credentials."
        ),
    }
    role_paragraph = role_paragraphs.get(
        role, f"ROLE - {role}: use the firmware only for its named MeshCore role."
    )
    paragraphs = [
        (
            f"Keymind Cascade MeshCore {display_version} with Halo/Keymind retry "
            "tuning, Cascade defaults, and the USA/Canada 910.525 MHz / SF7 / "
            "BW62.5 / CR5 preset."
        ),
        role_paragraph,
        (
            "PROFILE - Portable MQTT observer: forwards mesh observations to an "
            "on-device Wi-Fi MQTT bridge, keeps USB debug/packet logging off, and "
            "uses bridge NTP instead of mesh clock consensus. LoRa self-update is "
            "off in this compact profile."
        ),
        (
            "INSTALL - First-time setup uses Full install (the merged bootloader + "
            "firmware image). Routine upgrades use Update (the app-only image) only "
            "when the existing app partition is compatible."
        ),
    ]
    warning = partition_warning(identities)
    if warning is not None:
        paragraphs.append(warning)
    paragraphs.append(
        "NETWORK - Configure Wi-Fi and MQTT broker settings before relying on "
        "observer forwarding."
    )
    for paragraph in old_notes.split("\n\n"):
        if paragraph.startswith("HARDWARE ") or paragraph.startswith(
            "Board selection note:"
        ):
            paragraphs.append(paragraph)
    paragraphs.append("SELECTION - Portable MQTT observer.")
    return "\n\n".join(paragraphs)


def update_catalog(catalog: dict, release_files: dict[str, list[Path]], args: argparse.Namespace) -> dict:
    display_version = args.artifact_version.rsplit("-", 1)[0]
    if args.companion_only:
        catalog["description"] = (
            "Keymind Cascade MeshCore v1.17.1.1 infrastructure firmware plus "
            f"corrected {display_version} Companion firmware, with Halo/Keymind "
            "retry tuning, Cascade defaults, and the USA/Canada 910.525 MHz / "
            "SF7 / BW62.5 / CR5 preset. Companion device power saving is enabled "
            "by default and migrated on once in the corrected release. Unaffected "
            "roles remain on v1.17.1.1. Open Release notes for role, hardware, "
            "installation, and partition requirements."
        )
    else:
        catalog["description"] = (
            f"Keymind Cascade MeshCore {display_version} firmware with Halo/Keymind "
            "retry tuning, Cascade defaults, and the USA/Canada 910.525 MHz / SF7 / "
            "BW62.5 / CR5 preset. This catalog contains standard builds, Full "
            "Companion builds, compact LoRa-OTA builds, and portable MQTT observer "
            "builds with USB debug/packet logging off. On nRF52, Full Companion "
            "replaces separate BLE and USB choices; on ESP32, Full Companion is "
            "offered next to the BLE/USB variants. Open Release notes for role, "
            "hardware, installation, and partition requirements."
        )

    updated_entries = 0
    updated_files = 0
    observer_entries = 0
    used_identities: set[str] = set()

    for device in catalog["device"]:
        device_type = device["type"]
        for firmware in device["firmware"]:
            if args.companion_only and not firmware["role"].startswith("companion"):
                continue
            old_keys = list(firmware["version"])
            old_notes = next(iter(firmware["version"].values()))["notes"]
            resolved_identities: list[str] = []
            converted_observer = False
            converted_legacy_companion_ota = False

            for old_identity in ordered_catalog_identities(firmware):
                identity, converted = resolve_release_identity(old_identity, release_files)
                converted_legacy_companion_ota = (
                    converted_legacy_companion_ota
                    or old_identity in LEGACY_COMPANION_IDENTITY_ALIASES
                )
                if identity not in resolved_identities:
                    resolved_identities.append(identity)
                converted_observer = (
                    converted_observer
                    or converted
                    or "observer_mqtt" in identity.lower()
                )

            paths: list[Path] = []
            for identity in resolved_identities:
                used_identities.add(identity)
                paths.extend(release_files[identity])
            paths.sort(key=lambda path: file_sort_key(path, device_type))

            files = []
            for path in paths:
                identity = artifact_identity(path.name)
                release_tag = release_tag_for_identity(identity, args)
                file_type, title = file_type_and_title(path, device_type)
                files.append(
                    {
                        "type": file_type,
                        "name": path.name,
                        "url": download_url(args.repo, release_tag, path.name),
                        "title": title,
                    }
                )

            if converted_observer:
                version_key = f"mqtt-observer-{args.artifact_version}"
                notes = observer_notes(
                    firmware["role"],
                    display_version,
                    resolved_identities,
                    old_notes,
                )
                firmware["subTitle"] = "Portable MQTT observer"
                observer_entries += 1
            else:
                version_key = replacement_version_key(old_keys[0], args.artifact_version)
                notes = replace_release_version(old_notes, display_version)
                if converted_legacy_companion_ota:
                    notes = replace_legacy_companion_ota_notes(notes)
                notes = append_partition_warning(notes, resolved_identities)

            if firmware["role"].startswith("companion"):
                notes = append_companion_power_saving_note(notes, display_version)

            firmware["version"] = {version_key: {"notes": notes, "files": files}}
            updated_entries += 1
            updated_files += len(files)

    if args.companion_only and updated_entries != 216:
        raise ValueError(
            f"expected 216 curated Companion entries, found {updated_entries}"
        )
    if updated_entries == 0 or updated_files == 0:
        raise ValueError("catalog update produced no firmware entries")

    print(
        json.dumps(
            {
                "catalog_entries": updated_entries,
                "catalog_files": updated_files,
                "observer_entries": observer_entries,
                "release_identities_used": len(used_identities),
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
        with args.catalog.open(encoding="utf-8") as handle:
            catalog = json.load(handle)
        release_files = index_release_files(args.release_dir, args.artifact_version)
        catalog = update_catalog(catalog, release_files, args)
        if not args.check_only:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(catalog, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote {output}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
