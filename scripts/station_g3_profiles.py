"""Release selection for verified shared-slot G3 images; no OTA identities change."""
import re

CAPABILITY = "radio.station_g3.auto_slots"
MARKER = "meshcore.capability.station_g3.auto_slots.v1"
_LEGACY = re.compile(r"^Station_G3_ESP32_r2_(repeater(?:_observer_mqtt)?|"
                     r"room_server(?:_observer_mqtt)?|sensor)$")


def canonical_target(target):
    """Only the five known SX1262 slot aliases, with the same exact role/profile."""
    if not isinstance(target, str) or not _LEGACY.fullmatch(target):
        return None
    return target.replace("Station_G3_ESP32_r2_", "Station_G3_ESP32_", 1)


def qualified_auto_slots(manifest):
    return (manifest.get("platform") == "ESP32_PLATFORM"
            and manifest.get("verified") is True
            and CAPABILITY in (manifest.get("capabilities") or [])
            and any(check.get("capability") == CAPABILITY
                    and check.get("source") == "packaged application"
                    and check.get("evidence") == MARKER
                    and check.get("present") is True
                    for check in manifest.get("verification") or []))


def omit_redundant_slot_records(records):
    """Discard resumed R2 duplicates only after the exact shared successor is proven.

    Never mutate files/manifests or suppress an unmatched historical artifact.
    This is a future ordinary-release preference, not an OTA migration path.
    """
    successors = {(record["manifest"]["target"], record["manifest"].get("build_profile"))
                  for record in records if qualified_auto_slots(record["manifest"])}
    return [record for record in records
            if (canonical_target(record["manifest"]["target"]),
                record["manifest"].get("build_profile")) not in successors]


def runtime_metadata(manifest, defines, release_source=None):
    if CAPABILITY not in (manifest.get("capabilities") or []):
        return {}
    if (not qualified_auto_slots(manifest)
            or defines.get("RADIO_CLASS") != "CustomSX1262"
            or defines.get("USE_SX1262", "0") in ("0", "false")
            or defines.get("STATION_G3_ESP32", "0") in ("0", "false")
            or not str(manifest.get("target", "")).startswith("Station_G3_ESP32_")):
        raise ValueError("Station G3 shared-slot metadata needs qualified SX1262 application proof")
    source = manifest.get("source_commit", release_source)
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Station G3 slot metadata requires an exact source commit")
    return {"radioSlotAuto": True, "radioSlotFamily": "sx1262", "radioSlotSource": source}
