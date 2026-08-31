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

# These legacy recipes do not use the regular
# ``<board>_companion_radio_<transport>`` spelling, or they carry a capability
# name that Full now provides at runtime.  Keep the mapping beside the shared
# resolver so the main and logging catalog updaters cannot drift apart.
LEGACY_PLAIN_FULL_COMPANION_ALIASES = {
    "generic_espnow_comp_radio_usb":
        "Generic_ESPNOW_companion_radio_full",
    "heltec_e290_companion_ble":
        "Heltec_E290_companion_radio_full",
    "heltec_e290_companion_usb":
        "Heltec_E290_companion_radio_full",
    "heltec_e290_companion_usb_ble":
        "Heltec_E290_companion_radio_full",
    "heltec_t190_companion_radio_usb_ble_":
        "Heltec_T190_companion_radio_full_",
    "sensecapindicator-espnow_comp_radio_usb":
        "SenseCapIndicator-ESPNow_companion_radio_full",
    "sensecapindicator-lora_comp_radio_usb_wifi":
        "SenseCapIndicator-LoRa_companion_radio_full",
    "heltec_v3_companion_radio_wifi_mqtt":
        "Heltec_v3_companion_radio_full",
    "heltec_v4_companion_radio_wifi_mqtt":
        "heltec_v4_2_v4_3_companion_radio_full_femon",
    "heltec_v4_companion_radio_wifi_mqtt_femon":
        "heltec_v4_2_v4_3_companion_radio_full_femon",
}

LEGACY_COMPANION_ARTIFACT_SUFFIXES = (
    "-full-logging-ota",
    "-logging-ota",
    "-ota-logging",
    "-logging",
    "-ota",
)

REDUCED_PROFILE_MARKER = "_lora_ota_no_external_sensors"
REDUCED_PROFILE_DESCRIPTION = (
    "PROFILE - Compact LoRa OTA: can install LoRa OTA while retaining this "
    "exact target's board functions. To fit update staging, it omits selected "
    "optional environmental/ranging drivers; I2C remains available and is not "
    "globally disabled. USB debug/packet logging is OFF."
)
REDUCED_PROFILE_SELECTION = "selected optional sensor drivers omitted"
RAK_INA_RETENTION_NOTE = (
    "RAK SENSOR RETENTION - This reduced RAK build retains INA219, INA226, "
    "INA260, and INA3221 I2C current/voltage monitor drivers."
)
RAK3401_GPS_RETENTION_NOTE = (
    "GPS RETENTION - Compatible RAK3401 GPS options remain available: "
    "RAK12500 over I2C and RAK12501 over UART."
)
RAK4631_GPS_RETENTION_NOTE = (
    "GPS RETENTION - Compatible RAK4631 GPS options remain available: "
    "RAK12500 over I2C and RAK12501 over Serial1."
)
RAK4631_SERIAL1_GPS_OMISSION_NOTE = (
    "GPS / RS-232 - GPS is omitted from this RAK4631 build because the "
    "RS-232 bridge owns Serial1."
)
REDUCED_PROFILE_SPECIAL_NOTE_PREFIXES = (
    "RAK SENSOR RETENTION ",
    "GPS RETENTION ",
    "GPS / RS-232 ",
)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=script_dir / "keymind-cascade-v1.16.0-provider.json",
        help="existing curated provider catalog",
    )
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument(
        "--artifact-version",
        help="filename version token, for example v1.17.1.1-759a35fc",
    )
    parser.add_argument("--main-tag")
    parser.add_argument("--advanced-tag")
    parser.add_argument(
        "--full-tag",
        help="FULL-profile release tag (defaults to --advanced-tag for compatibility)",
    )
    parser.add_argument("--utility-tag")
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
    parser.add_argument(
        "--normalize-reduced-metadata-only",
        action="store_true",
        help=(
            "normalize Compact LoRa OTA descriptions in an existing catalog "
            "without changing versions, files, or release URLs"
        ),
    )
    args = parser.parse_args()
    if args.normalize_reduced_metadata_only:
        if args.companion_only:
            parser.error(
                "--companion-only cannot be combined with "
                "--normalize-reduced-metadata-only"
            )
    else:
        required = (
            "release_dir",
            "artifact_version",
            "main_tag",
            "advanced_tag",
            "utility_tag",
        )
        missing = [
            f"--{name.replace('_', '-')}"
            for name in required
            if not getattr(args, name)
        ]
        if missing:
            parser.error(
                "the following arguments are required unless "
                "--normalize-reduced-metadata-only is used: "
                + ", ".join(missing)
            )
    if not args.full_tag and args.advanced_tag:
        args.full_tag = args.advanced_tag
    return args


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


def is_reduced_lora_ota_profile(identities: list[str]) -> bool:
    return any(
        REDUCED_PROFILE_MARKER in identity.lower()
        for identity in identities
    )


def reduced_profile_special_notes(identities: list[str]) -> list[str]:
    lowered = [identity.lower() for identity in identities]
    is_rak3401 = any(identity.startswith("rak_3401_") for identity in lowered)
    is_rak4631 = any(identity.startswith("rak_4631_") for identity in lowered)
    if not (is_rak3401 or is_rak4631):
        return []

    notes = [RAK_INA_RETENTION_NOTE]
    if is_rak3401:
        notes.append(RAK3401_GPS_RETENTION_NOTE)
    elif any(
        "_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors"
        in identity
        for identity in lowered
    ):
        notes.append(RAK4631_SERIAL1_GPS_OMISSION_NOTE)
    else:
        notes.append(RAK4631_GPS_RETENTION_NOTE)
    return notes


def normalize_reduced_profile_metadata(
    firmware: dict,
    notes: str,
    identities: list[str],
) -> str:
    """Describe reduced LoRa OTA profiles without claiming I2C is absent."""
    if not is_reduced_lora_ota_profile(identities):
        return notes

    subtitle = firmware.get("subTitle")
    if isinstance(subtitle, str):
        firmware["subTitle"] = re.sub(
            r"no external sensors",
            REDUCED_PROFILE_SELECTION,
            subtitle,
            flags=re.IGNORECASE,
        )

    paragraphs: list[str] = []
    profile_found = False
    for paragraph in notes.split("\n\n"):
        if paragraph.startswith(REDUCED_PROFILE_SPECIAL_NOTE_PREFIXES):
            continue
        if paragraph.startswith("PROFILE ") and "Compact LoRa OTA" in paragraph:
            paragraphs.append(REDUCED_PROFILE_DESCRIPTION)
            paragraphs.extend(reduced_profile_special_notes(identities))
            profile_found = True
            continue
        if paragraph.startswith("SELECTION "):
            paragraph = re.sub(
                r"no external sensors",
                REDUCED_PROFILE_SELECTION,
                paragraph,
                flags=re.IGNORECASE,
            )
        paragraphs.append(paragraph)

    if not profile_found:
        raise ValueError(
            "reduced LoRa OTA catalog entry has no Compact LoRa OTA profile: "
            + ", ".join(identities)
        )
    return "\n\n".join(paragraphs)


def normalize_reduced_catalog_metadata(catalog: dict) -> int:
    """Normalize every reduced entry in place and return its entry count."""
    normalized = 0
    for device in catalog["device"]:
        for firmware in device["firmware"]:
            identities = ordered_catalog_identities(firmware)
            if not is_reduced_lora_ota_profile(identities):
                continue
            for version in firmware["version"].values():
                version["notes"] = normalize_reduced_profile_metadata(
                    firmware,
                    version["notes"],
                    identities,
                )
            normalized += 1
    if normalized == 0:
        raise ValueError("catalog has no reduced LoRa OTA entries to normalize")
    return normalized


def canonical_runtime_identity(identity: str) -> str:
    """Map legacy setting/profile names to the canonical release identity."""
    result = re.sub(
        r"^Station_G2_logging(?=_)", "Station_G2", identity,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"^Station_G3_ESP32_logging(?=_)", "Station_G3_ESP32", result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"_ps(?=_|-|$)", "", result, flags=re.IGNORECASE)

    # V4.3 _femoff recipes extend the corresponding auto-detect V4 recipe.
    # Normalize the physical prefix before turning the compile-time default
    # into the canonical runtime-configurable identity.
    v4_prefixes = (
        (r"^heltec_v4_3_expansionkit_tft(?=_companion_radio)",
         "heltec_v4_expansionkit_tft"),
        (r"^heltec_v4_3_tft(?=_companion_radio)", "heltec_v4_tft"),
        (r"^heltec_v4_3(?=_companion_radio)", "heltec_v4"),
    )
    for pattern, replacement in v4_prefixes:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(
        r"_femoff(?=-|$)", "_femon", result, flags=re.IGNORECASE
    )

    # The unsuffixed V4 USB/BLE targets are exact aliases of their _femon
    # recipes and are the shorter canonical release names.
    result = re.sub(
        r"^(heltec_v4_companion_radio_(?:usb|ble))_femon(?=-|$)",
        r"\1",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"^heltec_v4_companion_radio_full(?:_femon)?(?=-|$)",
        "heltec_v4_2_v4_3_companion_radio_full_femon",
        result,
        flags=re.IGNORECASE,
    )
    return result


def release_identity_candidates(old_identity: str) -> list[str]:
    candidates = [old_identity]
    fixed_alias = LEGACY_COMPANION_IDENTITY_ALIASES.get(old_identity)
    if fixed_alias is not None:
        candidates.append(fixed_alias)
    for identity in tuple(candidates):
        canonical = canonical_runtime_identity(identity)
        if canonical not in candidates:
            candidates.append(canonical)
    return candidates


def legacy_identity_priority(
    old_identities: list[str], resolved_identities: list[str]
) -> int:
    """Prefer canonical catalog rows when several rows resolve to one file."""
    score = 0
    resolved = set(resolved_identities)
    for identity in old_identities:
        lowered = identity.lower()
        if identity not in resolved:
            score += 4
        if re.search(r"_ps(?=_|-|$)", lowered):
            score += 8
        if "_femoff" in lowered:
            score += 8
        if lowered.startswith(("station_g2_logging_", "station_g3_esp32_logging_")):
            score += 8
        if re.match(
            r"^heltec_v4_companion_radio_(?:usb|ble)_femon(?=-|$)",
            lowered,
        ):
            score += 1
    return score


def deduplicate_resolved_firmware(
    catalog: dict, priorities: dict[int, int]
) -> int:
    """Collapse catalog rows that now point at the same canonical artifacts."""
    collapsed = 0
    for device in catalog["device"]:
        retained: list[dict] = []
        index_by_identity: dict[tuple[str, ...], int] = {}
        for firmware in device["firmware"]:
            identities = tuple(ordered_catalog_identities(firmware))
            existing_index = index_by_identity.get(identities)
            if existing_index is None:
                index_by_identity[identities] = len(retained)
                retained.append(firmware)
                continue
            existing = retained[existing_index]
            if priorities.get(id(firmware), 0) < priorities.get(id(existing), 0):
                retained[existing_index] = firmware
            collapsed += 1
        device["firmware"] = retained
    return collapsed


def normalize_runtime_companion_metadata(firmware: dict, notes: str) -> str:
    subtitle = firmware.get("subTitle")
    if isinstance(subtitle, str):
        kept = []
        for part in subtitle.split(","):
            token = part.strip()
            if token.lower() in {
                "power saving", "fem on", "fem off",
            }:
                continue
            kept.append(token)
        if kept:
            firmware["subTitle"] = ", ".join(kept)
        else:
            firmware.pop("subTitle", None)

    paragraphs: list[str] = []
    added_runtime_note = False
    for paragraph in notes.split("\n\n"):
        lowered = paragraph.lower()
        if paragraph.startswith("HARDWARE ") and "fem on/off must match" in lowered:
            if not added_runtime_note:
                paragraphs.append(
                    "CONFIGURATION - Controllable external FEM receive gain is "
                    "a saved setting, not a different hardware image. Change it "
                    "with WebConfig, the Companion protocol, or "
                    "radio.fem.rxgain on|off where the text CLI is available."
                )
                added_runtime_note = True
            continue
        if paragraph.startswith("SELECTION "):
            paragraph = re.sub(
                r",?\s*(?:Power saving|FEM (?:on|off))(?=,|\.|$)",
                "",
                paragraph,
                flags=re.IGNORECASE,
            )
            paragraph = re.sub(r",\s*,", ",", paragraph)
            paragraph = re.sub(r"\s+,", ",", paragraph)
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def normalize_nrf52_full_companion_metadata(
    firmware: dict, notes: str
) -> str:
    """Describe the canonical dual-CDC nRF52 Full Companion accurately."""
    firmware["title"] = "Full Companion"
    firmware["subTitle"] = (
        "USB Companion + optional USB logging + BLE + LoRa OTA source"
    )
    profile = (
        "PROFILE - nRF52 Full Companion: one USB cable always exposes interface 00 "
        "for Binary Companion, the text terminal, and serial mOTA source "
        "traffic. Fresh installs default USB logging off and do not enumerate "
        "interface 02. Enabling logging and rebooting adds interface 02 as a "
        "separate plaintext packet/debug port. BLE remains available. Use "
        "set usb.logging on reboot or set usb.logging off reboot to save and "
        "apply the interface count."
    )
    logging_use = (
        "LOGGING USE - Point Companion software, meshcli, and motatool at USB "
        "interface 00. After enabling logging and rebooting, point a "
        "USB-connected MQTT/logging reader at interface 02. Match the USB "
        "interface number rather than assuming a tty or COM port number. Input "
        "received on interface 02 is ignored."
    )
    ota_use = (
        "LORA OTA SOURCE - This Full Companion can serve a host-supplied "
        "update to another node. It has no target-side staging store and does "
        "not install that LoRa update onto itself."
    )

    paragraphs: list[str] = []
    profile_added = False
    for paragraph in notes.split("\n\n"):
        if paragraph.startswith("PROFILE "):
            if not profile_added:
                paragraphs.append(profile)
                profile_added = True
            continue
        if paragraph.startswith("LOGGING USE "):
            continue
        if paragraph.startswith("SELECTION "):
            paragraphs.append(
                "SELECTION - nRF52 Full Companion with an optional second USB "
                "logging port, BLE, and source-only LoRa OTA."
            )
            continue
        paragraphs.append(paragraph)
    if not profile_added:
        paragraphs.append(profile)
    paragraphs.append(logging_use)
    if ota_use not in paragraphs:
        paragraphs.append(ota_use)
    return "\n\n".join(paragraphs)


def normalize_esp32_full_companion_metadata(
    firmware: dict, notes: str
) -> str:
    """Describe the canonical single-TTY ESP32 Full Companion accurately."""
    firmware["title"] = "Full Companion"
    firmware["subTitle"] = (
        "USB Companion or logging + BLE + Wi-Fi + LoRa OTA source"
    )
    profile = (
        "PROFILE - ESP32 Full Companion: its single USB TTY starts with USB "
        "logging off and uses the ASCII/Binary Companion switcher. BLE, "
        "Wi-Fi Companion on TCP "
        "5000, WebConfig, TCP mOTA seeding on 5001, and the text terminal on "
        "5002 remain available. Enter the USB text terminal and use set "
        "usb.logging on to turn that TTY into an input-capable plaintext "
        "packet/debug stream. Use set usb.logging off to stop diagnostics; "
        "after its reply, the TTY remains in the normal ASCII terminal, matching "
        "fresh Full firmware. Then use the normal terminal stop token or let a "
        "Companion app send a valid framed probe to select Binary Companion. "
        "The saved logging choice is restored at boot."
    )
    logging_use = (
        "LOGGING USE - USB logging and Binary Companion deliberately do not "
        "share the single TTY at the same time. While logging is on, the TTY "
        "continues to accept text CLI commands, including set usb.logging "
        "off. Use BLE or Wi-Fi Companion while the USB TTY is logging."
    )
    selection = (
        "SELECTION - One Full image for this exact hardware layout replaces "
        "separate USB, BLE, ordinary Wi-Fi, and USB-logging images."
    )

    paragraphs: list[str] = []
    profile_added = False
    selection_added = False
    for paragraph in notes.split("\n\n"):
        if paragraph.startswith("PROFILE "):
            if not profile_added:
                paragraphs.append(profile)
                profile_added = True
            continue
        if paragraph.startswith("LOGGING USE "):
            continue
        if paragraph.startswith("SELECTION "):
            if not selection_added:
                paragraphs.append(selection)
                selection_added = True
            continue
        paragraphs.append(paragraph)
    if not profile_added:
        paragraphs.append(profile)
    if not selection_added:
        paragraphs.append(selection)
    paragraphs.append(logging_use)
    return "\n\n".join(paragraphs)


def canonical_full_identity_for_transport(identity: str) -> str | None:
    # OTA and logging were artifact variants of the old transport-specific
    # recipes.  The consolidated target is a plain Full identity, so carrying
    # either suffix through the transport substitution produces a name that
    # can never exist (for example ``..._full-ota``).
    legacy_identity = identity
    while True:
        lowered = legacy_identity.lower()
        suffix = next(
            (
                suffix
                for suffix in LEGACY_COMPANION_ARTIFACT_SUFFIXES
                if lowered.endswith(suffix)
            ),
            None,
        )
        if suffix is None:
            break
        legacy_identity = legacy_identity[: -len(suffix)]

    legacy_identity = canonical_runtime_identity(legacy_identity)
    fixed_alias = LEGACY_PLAIN_FULL_COMPANION_ALIASES.get(
        legacy_identity.lower()
    )
    if fixed_alias is not None:
        return fixed_alias

    # A catalog can already use the Full recipe while retaining an old
    # ``-logging`` or ``-ota`` artifact qualifier.  Once the qualifier is
    # removed there is no transport substitution left to perform.
    if "_companion_radio_full" in legacy_identity.lower():
        return canonical_runtime_identity(legacy_identity)

    match = re.search(
        r"_companion_radio_(?:usb|ble|wifi(?!_mqtt)|serial|ethernet)(?=-|_|$)",
        legacy_identity,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    full_identity = (
        legacy_identity[:match.start()] + "_companion_radio_full" +
        legacy_identity[match.end():]
    )
    return canonical_runtime_identity(full_identity)


def release_identity_is_full_companion(
    release_files: dict[str, list[Path]], identity: str
) -> bool:
    return identity in release_files and "_companion_radio_full" in identity.lower()


def resolve_release_identity(
    old_identity: str, release_files: dict[str, list[Path]]
) -> tuple[str, bool]:
    candidates = release_identity_candidates(old_identity)
    for candidate in candidates:
        if candidate in release_files:
            return candidate, False

    # Full Companion replaces the old USB-only logging artifact. ESP32 safely
    # switches its one TTY between framed Companion and an input-capable
    # plaintext logging terminal; nRF52 can expose a second interface.
    for candidate in candidates:
        if not candidate.endswith("-logging"):
            continue
        logging_base = candidate.removesuffix("-logging")
        full_identity = canonical_full_identity_for_transport(logging_base)
        if full_identity is not None and release_identity_is_full_companion(
            release_files, full_identity
        ):
            return full_identity, "_wifi_mqtt" in logging_base.lower()

    # Canonical Full Companion replaces separate USB, BLE, and ordinary Wi-Fi
    # artifacts. Full is source-only for LoRa OTA and needs no target-side
    # staging/self-install slot.
    for candidate in candidates:
        full_identity = canonical_full_identity_for_transport(candidate)
        if full_identity is not None and release_identity_is_full_companion(
            release_files, full_identity
        ):
            return full_identity, "_wifi_mqtt" in candidate.lower()

    # Accept catalogs produced while portable MQTT observers were still
    # emitted, and migrate them to the expanded-partition FULL artifact.
    for candidate in candidates:
        if "observer_mqtt" in candidate.lower():
            for full_suffix in ("-full-usb-wifi-ota", "-full-ota"):
                full_identity = f"{candidate}{full_suffix}"
                if full_identity in release_files:
                    return full_identity, True
        if "bridge_espnow" in candidate.lower():
            for full_suffix in ("-full-usb-wifi-ota", "-full-ota"):
                full_identity = f"{candidate}{full_suffix}"
                if full_identity in release_files:
                    return full_identity, False

    # Retain compatibility with the short-lived reverse migration used by
    # older release directories.
    for candidate in candidates:
        for full_suffix in ("-full-usb-wifi-ota", "-full-ota"):
            if candidate.endswith(full_suffix):
                portable_identity = candidate[: -len(full_suffix)]
                if portable_identity in release_files:
                    return portable_identity, True
                unified_identity = f"{portable_identity}-full-usb-wifi-ota"
                if unified_identity in release_files:
                    return unified_identity, True

    # Logging catalogs historically carried a separate USB-only identity for
    # every ESP32 target. Prefer the unified FULL observer when present; if the
    # role has no MQTT sibling, use its FULL logging fallback instead.
    for candidate in candidates:
        logging_base = None
        if candidate.endswith("-full-logging-ota"):
            logging_base = candidate.removesuffix("-full-logging-ota")
        elif candidate.endswith("-logging"):
            logging_base = candidate.removesuffix("-logging")
        if logging_base is None:
            continue
        logging_candidates = (
            f"{logging_base}_observer_mqtt-full-usb-wifi-ota",
            f"{logging_base}-full-usb-wifi-ota",
            f"{logging_base}-full-logging-ota",
        )
        for identity in logging_candidates:
            if identity in release_files:
                return identity, "observer_mqtt" in identity.lower()

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
        "-full-usb-wifi-ota" in lowered
        or "-full-logging-ota" in lowered
        or "-full-ota" in lowered
    ):
        return args.full_tag
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
        or "-full-usb-wifi-ota" in identity.lower()
        or "-full-ota" in identity.lower()
        or "-full-logging-ota" in identity.lower()
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
    unified_output = any(
        "-full-usb-wifi-ota" in identity.lower() for identity in identities
    )
    profile_paragraph = (
        "PROFILE - Unified FULL USB + Wi-Fi observer: uses expanded partitions, "
        "compiles USB packet logging and the on-device Wi-Fi MQTT bridge into one "
        "image, keeps verbose USB debug off, and retains the complete role CLI. "
        "Use logging.output off|usb|wifi|both to persist the active paths. LoRa "
        "self-update is enabled. With no saved SSID, the setup AP is available "
        "for 30 minutes per boot and then powers Wi-Fi off; configured Wi-Fi "
        "retains its normal indefinite reconnect behavior."
        if unified_output
        else
        "PROFILE - FULL MQTT observer: uses expanded partitions, forwards mesh "
        "observations to an on-device Wi-Fi MQTT bridge, keeps USB debug/packet "
        "logging off, uses bridge NTP instead of mesh clock consensus, and retains "
        "the complete role CLI. LoRa self-update is enabled."
    )
    paragraphs = [
        (
            f"Keymind Cascade MeshCore {display_version} with Halo/Keymind retry "
            "tuning, Cascade defaults, and the USA/Canada 910.525 MHz / SF7 / "
            "BW62.5 / CR5 preset."
        ),
        role_paragraph,
        profile_paragraph,
        (
            "INSTALL - Flash Full install (the merged bootloader + firmware image) "
            "over USB once to install the expanded partition table. Routine upgrades "
            "can then use Update (the app-only image) while that layout remains installed."
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
    paragraphs.append(
        "SELECTION - Unified FULL USB + Wi-Fi observer."
        if unified_output
        else "SELECTION - FULL MQTT observer."
    )
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
            "Companion builds, lean LoRa-OTA builds, and expanded-partition FULL "
            "USB + Wi-Fi observer and ESP-NOW bridge builds. Unified observers "
            "provide persistent off/USB/WiFi/both output selection. Full Companion "
            "replaces separate BLE, USB, ordinary Wi-Fi, and USB-logging choices "
            "with one image. nRF52 Full Companion can add a separate plaintext "
            "port; ESP32 Full Companion switches its one USB TTY into an "
            "input-capable logging terminal. Open Release notes for role, "
            "hardware, installation, and partition requirements."
        )

    updated_entries = 0
    updated_files = 0
    observer_entries = 0
    used_identities: set[str] = set()
    processed_firmware_ids: set[int] = set()
    firmware_priorities: dict[int, int] = {}

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
            old_identities = ordered_catalog_identities(firmware)

            for old_identity in old_identities:
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
            processed_firmware_ids.add(id(firmware))
            firmware_priorities[id(firmware)] = legacy_identity_priority(
                old_identities, resolved_identities
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
                firmware["subTitle"] = (
                    "Unified FULL USB + Wi-Fi observer"
                    if any(
                        "-full-usb-wifi-ota" in identity.lower()
                        for identity in resolved_identities
                    )
                    else "FULL MQTT observer"
                )
                observer_entries += 1
            else:
                version_key = replacement_version_key(old_keys[0], args.artifact_version)
                notes = replace_release_version(old_notes, display_version)
                if converted_legacy_companion_ota:
                    notes = replace_legacy_companion_ota_notes(notes)
                notes = append_partition_warning(notes, resolved_identities)

            notes = normalize_reduced_profile_metadata(
                firmware,
                notes,
                resolved_identities,
            )

            if firmware["role"].startswith("companion"):
                notes = append_companion_power_saving_note(notes, display_version)
                notes = normalize_runtime_companion_metadata(firmware, notes)
                if device_type == "nrf52" and any(
                    "companion_radio_full" in identity.lower()
                    for identity in resolved_identities
                ):
                    notes = normalize_nrf52_full_companion_metadata(
                        firmware, notes
                    )
                elif device_type == "esp32" and any(
                    "companion_radio_full" in identity.lower()
                    for identity in resolved_identities
                ):
                    notes = normalize_esp32_full_companion_metadata(
                        firmware, notes
                    )

            firmware["version"] = {version_key: {"notes": notes, "files": files}}
            updated_entries += 1
            updated_files += len(files)

    collapsed_entries = deduplicate_resolved_firmware(catalog, firmware_priorities)
    retained_processed = [
        firmware
        for device in catalog["device"]
        for firmware in device["firmware"]
        if id(firmware) in processed_firmware_ids
    ]
    updated_entries = len(retained_processed)
    updated_files = sum(
        len(version["files"])
        for firmware in retained_processed
        for version in firmware["version"].values()
    )
    observer_entries = sum(
        any("observer_mqtt" in identity.lower()
            for identity in ordered_catalog_identities(firmware))
        for firmware in retained_processed
    )
    used_identities = {
        identity
        for firmware in retained_processed
        for identity in ordered_catalog_identities(firmware)
    }
    if updated_entries == 0 or updated_files == 0:
        raise ValueError("catalog update produced no firmware entries")

    print(
        json.dumps(
            {
                "catalog_entries": updated_entries,
                "catalog_files": updated_files,
                "observer_entries": observer_entries,
                "release_identities_used": len(used_identities),
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
        with args.catalog.open(encoding="utf-8") as handle:
            catalog = json.load(handle)
        if args.normalize_reduced_metadata_only:
            normalized = normalize_reduced_catalog_metadata(catalog)
            print(
                json.dumps(
                    {
                        "normalized_reduced_entries": normalized,
                        "update_mode": "reduced-metadata-only",
                    },
                    sort_keys=True,
                )
            )
        else:
            if not args.artifact_version.startswith("v"):
                raise ValueError("--artifact-version must start with 'v'")
            release_files = index_release_files(
                args.release_dir,
                args.artifact_version,
            )
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
