"""Consolidation evidence for new applications; historical OTA names stay exact."""
import json
import re
import sys

from optional_display_profiles import resolved_defines, spec_for_defines

SPECS = (
    dict(key="autoLoRaRadio", capability="radio.autodetect.sx126x_sx127x",
         marker="meshcore.capability.radio.autodetect.sx126x_sx127x.v1",
         platform="ESP32_PLATFORM", board="ttgo-t-beam",
         target=r"Tbeam_(?:repeater(?:_bridge_espnow)?|room_server|kiss_modem|companion_radio_(?:ble|usb|wifi|full)(?:_ps)?)(?:_observer_mqtt)?",
         defines={"MESH_RADIO_AUTO_FAMILY": "1", "RADIO_CLASS": "UniversalLoRaRadio",
                  "WRAPPER_CLASS": "UniversalLoRaRadio"}),
    dict(key="autoLoRaRadio", capability="radio.autodetect.sx126x_sx127x",
         marker="meshcore.capability.radio.autodetect.sx126x_sx127x.v1",
         platform="ESP32_PLATFORM", board="t3_s3_v1_x",
         target=r"LilyGo_T3S3_(?:repeater(?:_bridge_espnow)?|room_server|kiss_modem|terminal_chat|companion_radio_(?:ble|usb|wifi|full)(?:_ps)?)(?:_observer_mqtt)?",
         defines={"MESH_RADIO_AUTO_FAMILY": "1", "RADIO_CLASS": "UniversalLoRaRadio",
                  "WRAPPER_CLASS": "UniversalLoRaRadio"}),
    dict(key="unifiedV4Display", capability="display.heltec_v4.unified",
         marker="meshcore.capability.display.heltec_v4.unified.v1",
         platform="ESP32_PLATFORM", board="heltec_v4",
         target=(r"heltec_v4(?:_2_v4_3|(?:_3)?(?:_expansionkit)?(?:_tft)?)_"
                 r"(?:(?:repeater(?:_bridge_espnow)?|room_server)(?:_observer_mqtt)?|sensor|"
                 r"companion_radio_(?:usb|ble|wifi|full)(?:_mqtt)?(?:_ps)?(?:_fem(?:on|off))?)"),
         defines={"HELTEC_V4_DISPLAY_UNIFIED": "1", "DISPLAY_CLASS": "HeltecV4Display"}),
    dict(key="combinedCompanion", capability="companion.picow.combined",
         marker="meshcore.capability.companion.picow.combined.v1",
         platform="RP2040_PLATFORM", board="rpipicow", target=r"PicoW_companion_radio",
         defines={"PICOW_COMBINED_COMPANION": "1", "ENABLE_USB_INTERFACE": "1",
                  "ENABLE_WIFI_INTERFACE": "1", "PIO_FRAMEWORK_ARDUINO_ENABLE_BLUETOOTH": "1"}),
    dict(key="oledExpansionCompatible", capability="hardware.heltec_v4.oled_kit",
         marker="meshcore.capability.hardware.heltec_v4.oled_kit.v1",
         platform="ESP32_PLATFORM", board="heltec_v4",
         target=r"heltec_v4_(?:repeater|room_server)(?:_observer_mqtt)?",
         defines={"HELTEC_V4_OLED_KIT_COMPAT": "1", "HELTEC_LORA_V4_OLED": "1",
                  "DISPLAY_CLASS": "SSD1306Display", "ENV_PIN_SDA": "4", "ENV_PIN_SCL": "3"}),
    dict(key="displayRotation", capability="display.rotation.runtime",
         marker="meshcore.capability.display.rotation.heltec_r8.v1",
         platform="ESP32_PLATFORM", board="heltec_v4_r8",
         target=r"heltec_v4_r8_tft_(?:(?:repeater|room_server)(?:_observer_mqtt)?|sensor|companion_radio_full)",
         defines={"HELTEC_TFT_RUNTIME_ROTATION": "1", "HELTEC_V4_R8_TFT": "1",
                  "DISPLAY_CLASS": "ST7789LCDDisplay"}),
    dict(key="tftExpansionCompatible", capability="hardware.heltec_v4.tft_kit",
         marker="meshcore.capability.hardware.heltec_v4.tft_kit.v1",
         platform="ESP32_PLATFORM", board="heltec_v4", target=r"heltec_v4_tft_companion_radio_full_femon",
         defines={"HELTEC_V4_TFT_KIT_COMPAT": "1", "HELTEC_LORA_V4_TFT": "1",
                  "DISPLAY_CLASS": "ST7789LCDDisplay"}),
)


def specs_for_config(target, env):
    defines = resolved_defines(env.get("build_flags", []))
    return [spec for spec in SPECS if env.get("board") == spec["board"]
            and re.fullmatch(spec["target"], target, re.I)
            and all(defines.get(key) == value for key, value in spec["defines"].items())]


def proven(manifest, spec):
    return (manifest.get("verified") is True and manifest.get("platform") == spec["platform"]
            and re.fullmatch(spec["target"], manifest.get("target", ""), re.I)
            and spec["capability"] in manifest.get("capabilities", [])
            and any(row.get("capability") == spec["capability"] and row.get("present") is True
                    and row.get("source") == "packaged application" and row.get("evidence") == spec["marker"]
                    for row in manifest.get("verification", [])))


def runtime_metadata(manifest, env, release_source):
    result = {}
    for spec in SPECS:
        if spec["capability"] not in manifest.get("capabilities", []):
            continue
        if spec not in specs_for_config(manifest["target"], env) or not proven(manifest, spec):
            raise ValueError(manifest["target"] + ": consolidation requires exact configuration and packaged application proof")
        source = manifest.get("source_commit", release_source)
        if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source) or source != release_source:
            raise ValueError("Consolidation metadata requires the exact release source")
        result[spec["key"]] = True
        result[spec["key"] + "Source"] = source
    return result


def unified_v4_replacements(target):
    """Ordinary V4 display successors; R8 and deployed mOTA IDs stay separate."""
    match = re.fullmatch(
        r"heltec_v4(?:_3)?(?:_expansionkit)?(?:_tft)?_"
        r"(repeater(?:_bridge_espnow)?|room_server|sensor)(_observer_mqtt)?", target, re.I)
    if match:
        role, observer = match.groups()
        base = "heltec_v4_" + role.lower()
        choices = [base + (observer or ""), base]
        if role.lower() == "repeater_bridge_espnow":
            choices.append("heltec_v4_repeater")
        return [(name, "unifiedV4Display") for name in dict.fromkeys(choices)
                if name.lower() != target.lower()]
    if re.fullmatch(
        r"heltec_v4(?:_3)?(?:_expansionkit)?(?:_tft)?_companion_radio_"
        r"(?:usb|ble|wifi|full)(?:_ps)?(?:_fem(?:on|off))?", target, re.I):
        canonical = "heltec_v4_2_v4_3_companion_radio_full_femon"
        return [(canonical, "unifiedV4Display")]
    return []


def replacements(target):
    """Known functionality successors, never aliases for deployed update IDs."""
    unified = unified_v4_replacements(target)
    if re.fullmatch(r"PicoW_companion_radio_(?:usb|ble|wifi)", target, re.I):
        return [("PicoW_companion_radio", "combinedCompanion")]
    if re.fullmatch(r"heltec_v4(?:_3)?_expansionkit_tft_companion_radio_(?:usb|ble|wifi|full)(?:_ps)?(?:_fem(?:on|off))?", target, re.I):
        return unified + [("heltec_v4_tft_companion_radio_full_femon", "tftExpansionCompatible")]
    match = re.fullmatch(r"muzi_base_(uno|duo)_superio_companion_radio_(?:usb|ble|full)", target, re.I)
    if match:
        return [("muzi_base_" + match[1].lower() + "_companion_radio_full", "muziAccessories")]
    if re.fullmatch(r"LilyGo_T-Echo-Lite_non_shell_companion_radio_(?:usb|ble|full)", target, re.I):
        return [("LilyGo_T-Echo-Lite_companion_radio_full", "techoLiteDisplay")]
    match = re.fullmatch(r"heltec_v4_expansionkit_(repeater|room_server)(_observer_mqtt)?", target, re.I)
    if match:
        base = "heltec_v4_" + match[1].lower()
        return unified + [(base + (match[2] or ""), "oledExpansionCompatible"), (base, "oledExpansionCompatible")]
    match = re.fullmatch(r"heltec_v4_r8_tft_portrait_(repeater|room_server)_observer_mqtt", target, re.I)
    if match:
        base = "heltec_v4_r8_tft_" + match[1].lower()
        return [(base + "_observer_mqtt", "displayRotation"), (base, "displayRotation")]
    match = re.fullmatch(r"heltec_rc32_without_display_(repeater|room_server|sensor|companion_radio_(?:usb|ble|wifi|full))(?:_observer_mqtt)?", target, re.I)
    if match:
        return [(target.lower().replace("_without_display_", "_", 1), "displayAuto")]
    return unified


def configuration_replacement(target, envs):
    def environment(name):
        # Full nRF52 targets can be generated by build.sh from their exact
        # USB or BLE base. Never substitute another physical radio variant.
        for choice in (name, name.replace("_companion_radio_full", "_companion_radio_usb"),
                       name.replace("_companion_radio_full", "_companion_radio_ble")):
            if "env:" + choice in envs:
                return envs["env:" + choice]
        return None
    for candidate, key in replacements(target):
        env = environment(candidate)
        source_env = environment(target)
        if not env or not source_env or env.get("board") != source_env.get("board"):
            continue
        if key in ("displayAuto", "muziAccessories", "techoLiteDisplay"):
            spec = spec_for_defines(resolved_defines(env.get("build_flags", [])), env.get("board"))
            expected = {"displayAuto": "rc32", "muziAccessories": "muzi_superio", "techoLiteDisplay": "techo_lite"}[key]
            qualified = spec and spec["marker"] == expected and re.fullmatch(spec["targets"], candidate.lower())
        else:
            qualified = any(spec["key"] == key for spec in specs_for_config(candidate, env))
        if qualified:
            return candidate
    return None


def omit_redundant_records(records):
    """Remove resumed alternatives only with an actual qualified successor."""
    def same_policy(left, right):
        return (left.get("build_profile") == right.get("build_profile")
                and sorted(x for x in left.get("capabilities", []) if x.startswith("sensor.profile."))
                == sorted(x for x in right.get("capabilities", []) if x.startswith("sensor.profile.")))

    def qualified(manifest, key):
        if key in ("displayAuto", "muziAccessories", "techoLiteDisplay"):
            from optional_display_profiles import marker, SPECS as displays
            expected = {"displayAuto": "rc32", "muziAccessories": "muzi_superio", "techoLiteDisplay": "techo_lite"}[key]
            spec = next((s for s in displays if s["marker"] == expected
                         and re.fullmatch(s["targets"], manifest.get("target", "").lower())), None)
            if spec is None:
                return False
            transport = key == "displayAuto" or (
                {"companion.usb", "companion.bluetooth"} <= set(manifest.get("capabilities", []))
                and {"companion.usb", "companion.bluetooth"} <= {
                    row.get("capability") for row in manifest.get("verification", [])
                    if row.get("present") is True and row.get("source") == "linked image" and row.get("evidence")})
            return (transport and manifest.get("verified") is True
                    and manifest.get("platform") == spec.get("platform", "ESP32_PLATFORM")
                    and "display.autodetect" in manifest.get("capabilities", [])
                    and any(row.get("capability") == "display.autodetect" and row.get("present") is True
                            and row.get("source") == "packaged application" and row.get("evidence") == marker(spec)
                            for row in manifest.get("verification", [])))
        return any(s["key"] == key and proven(manifest, s) for s in SPECS)

    result = []
    for record in records:
        manifest = record["manifest"]
        alternatives = replacements(manifest["target"])
        if not any(candidate["manifest"]["target"].lower() == target.lower()
                   and (same_policy(manifest, candidate["manifest"])
                        or key in ("muziAccessories", "techoLiteDisplay")
                        and candidate["manifest"].get("build_profile") == "full"
                        and manifest["target"].lower().endswith(("_usb", "_ble")))
                   and qualified(candidate["manifest"], key)
                   for target, key in alternatives for candidate in records):
            result.append(record)
    return result


if __name__ == "__main__":
    envs = {name: dict(options) for name, options in json.load(sys.stdin)}
    mode, target = sys.argv[1:]
    if mode == "replacement":
        result = configuration_replacement(target, envs)
        if result is None:
            sys.exit(1)
        print(result)
    elif mode == "expectations":
        for spec in specs_for_config(target, envs.get("env:" + target, {})):
            print(spec["capability"] + "=" + spec["marker"])
    else:
        raise SystemExit("Unknown consolidation operation")
