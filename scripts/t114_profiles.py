"""Release evidence for the canonical T114 image; legacy OTA names stay intact."""
import re

CAPABILITY = "display.autodetect"
MARKER = "meshcore.capability.display.autodetect.t114.v1"
_ALIASES = re.compile(r"^Heltec_t114_without_display_((?:repeater(?:_bridge_rs232)?|"
                      r"room_server)(?:_lora_ota_no_external_sensors)?"
                      r"(?:-(?:full|reduced)(?:-ota)?)?|companion_radio_(?:ble|usb|full))$", re.I)


def canonical_target(target):
    match = _ALIASES.fullmatch(target) if isinstance(target, str) else None
    return "Heltec_t114_" + match[1].lower() if match else None


def qualified_auto_display(manifest):
    return (manifest.get("platform") == "NRF52_PLATFORM"
            and manifest.get("verified") is True
            and str(manifest.get("target", "")).lower().startswith("heltec_t114_")
            and CAPABILITY in (manifest.get("capabilities") or [])
            and any(check.get("capability") == CAPABILITY
                    and check.get("source") == "packaged application"
                    and check.get("evidence") == MARKER
                    and check.get("present") is True
                    for check in manifest.get("verification") or []))


def runtime_metadata(manifest, defines, release_source=None):
    if CAPABILITY not in (manifest.get("capabilities") or []):
        return {}
    if (not qualified_auto_display(manifest)
            or defines.get("HELTEC_T114", "0") in ("0", "false")
            or defines.get("T114_DISPLAY_AUTODETECT") != "1"
            or defines.get("DISPLAY_CLASS") != "ST7789Display"):
        raise ValueError("T114 display metadata requires qualified automatic ST7789 application proof")
    source = manifest.get("source_commit", release_source)
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("T114 display metadata requires an exact source commit")
    return {"displayAuto": True, "displayController": "st7789", "displaySource": source}


def omit_legacy_display_records(records):
    """Future releases need the exact proven successor, never a renamed old file."""
    def key(manifest, target):
        sensors = tuple(sorted(value for value in manifest.get("capabilities", [])
                               if value.startswith("sensor.profile.")))
        return target.lower(), manifest.get("build_profile"), sensors

    successors = {key(record["manifest"], record["manifest"]["target"])
                  for record in records if qualified_auto_display(record["manifest"])}

    def combined_successor(manifest, target):
        for record in records:
            candidate = record["manifest"]
            if not qualified_auto_display(candidate):
                continue
            linked = {check.get("capability") for check in candidate.get("verification", [])
                      if check.get("present") is True and check.get("source") == "linked image"
                      and check.get("evidence")}
            capabilities = set(candidate.get("capabilities", []))
            if target.endswith(("companion_radio_usb", "companion_radio_ble")):
                required = {"companion.usb", "companion.bluetooth"}
                if (candidate["target"].lower() == "heltec_t114_companion_radio_full"
                        and required <= capabilities and required <= linked):
                    return True
            if target.endswith("repeater_bridge_rs232"):
                merged = any(check.get("capability") == "bridge.rs232.merged"
                             and check.get("present") is True
                             and check.get("source") == "packaged application" and check.get("evidence")
                             for check in candidate.get("verification", []))
                if (key(manifest, "Heltec_t114_repeater") == key(candidate, candidate["target"])
                        and {"bridge.rs232", "bridge.rs232.merged"} <= capabilities
                        and "bridge.rs232" in linked and merged):
                    return True
        return False

    result = []
    for record in records:
        manifest = record["manifest"]
        target = canonical_target(manifest["target"])
        if target:
            if key(manifest, target) not in successors and not combined_successor(manifest, target):
                raise ValueError(manifest["target"] + ": ordinary T114 release requires the qualified "
                                 "automatic-display successor " + target)
        else:
            result.append(record)
    return result
