"""Consolidation evidence for new applications; historical OTA names stay exact."""
import json
import re
import sys

from optional_display_profiles import resolved_defines, spec_for_defines

SPECS = (
    dict(key="unifiedWioL1Display", capability="display.wio_l1.unified",
         marker="meshcore.capability.display.wio_l1_unified.v1",
         platform="NRF52_PLATFORM", board="seeed-wio-tracker-l1",
         target=r"WioTrackerL1_companion_radio_(?:ble|full)",
         defines={"WIO_TRACKER_L1": "1", "WIO_L1_DISPLAY_UNIFIED": "1",
                  "DISPLAY_CLASS": "WioL1Display", "MAX_CONTACTS": "350",
                  "MAX_GROUP_CHANNELS": "40", "OFFLINE_QUEUE_SIZE": "256"}),
    *[dict(key=key, capability="modem.headless." + family,
           marker="meshcore.capability.modem.headless." + family + ".v1",
           platform=platform, board=board, target=target,
           defines={guard: "1", "MESHCORE_HEADLESS_KISS_COMPAT": "1"})
      for key, family, platform, board, target, guard in (
          ("headlessR8Kiss", "heltec_r8", "ESP32_PLATFORM", "heltec_v4_r8",
           r"heltec_v4_r8_kiss_modem", "HELTEC_V4_R8"),
          ("headlessWioL1Kiss", "wio_l1", "NRF52_PLATFORM", "seeed-wio-tracker-l1",
           r"WioTrackerL1_kiss_modem", "WIO_TRACKER_L1"),
      )],
    dict(key="autoLoRaRadio", capability="radio.autodetect.sx126x_sx127x",
         marker="meshcore.capability.radio.autodetect.sx126x_sx127x.v1",
         platform="ESP32_PLATFORM", board="ttgo-t-beam",
         target=r"Tbeam_(?:repeater(?:_bridge_espnow)?|room_server|kiss_modem|companion_radio_(?:ble|usb|wifi|full)(?:_ps)?)(?:_observer_mqtt)?",
         defines={"MESH_RADIO_AUTO_FAMILY": "1", "RADIO_CLASS": "UniversalLoRaRadio",
                  "MESH_RADIO_AUTO_SX127X": "1", "USE_SX1262": "1", "USE_SX1276": "1",
                  "WRAPPER_CLASS": "UniversalLoRaRadio"}),
    dict(key="autoLoRaRadio", capability="radio.autodetect.sx126x_sx127x",
         marker="meshcore.capability.radio.autodetect.sx126x_sx127x.v1",
         platform="ESP32_PLATFORM", board="t3_s3_v1_x",
         target=r"LilyGo_T3S3_(?:repeater(?:_bridge_espnow)?|room_server|kiss_modem|terminal_chat|companion_radio_(?:ble|usb|wifi|full)(?:_ps)?)(?:_observer_mqtt)?",
         defines={"MESH_RADIO_AUTO_FAMILY": "1", "RADIO_CLASS": "UniversalLoRaRadio",
                  "MESH_RADIO_AUTO_SX127X": "1", "USE_SX1262": "1", "USE_SX1276": "1",
                  "WRAPPER_CLASS": "UniversalLoRaRadio"}),
    *[dict(key="autoLoRaRadio", capability="radio.autodetect.sx1262_sx1268",
           marker="meshcore.capability.radio.autodetect.sx1262_sx1268.v1",
           platform="ESP32_PLATFORM", board=board, target=target,
           defines={"MESH_RADIO_AUTO_FAMILY": "1", "MESH_RADIO_AUTO_SX1268": "1",
                    "MESH_RADIO_AUTO_SX127X": "0", "USE_SX1262": "1", "USE_SX1268": "1",
                    "RADIO_CLASS": "UniversalLoRaRadio", "WRAPPER_CLASS": "UniversalLoRaRadio"})
      for board, target in (
          ("esp32doit-devkit-v1", r"Generic_E22_(?:m33s_)?(?:repeater(?:_bridge_espnow)?|kiss_modem)(?:_observer_mqtt)?"),
          ("esp32doit-devkit-v1", r"Meshadventurer_(?:m33s_)?(?:repeater(?:_bridge_espnow)?|room_server|kiss_modem|terminal_chat|companion_radio_(?:usb|ble|full))(?:_observer_mqtt)?"),
          ("esp32-c3-devkitm-1", r"Tenstar_C3_(?:repeater(?:_bridge_espnow)?|kiss_modem)(?:_observer_mqtt)?"),
      )],
    dict(key="autoLoRaRadio", capability="radio.autodetect.sx1262_lr1121",
         marker="meshcore.capability.radio.autodetect.sx1262_lr1121.v1",
         platform="NRF52_PLATFORM", board="muzi_base",
         target=r"muzi_base_(?:repeater|room_server|companion_radio_(?:usb|ble|full))",
         defines={"MESH_RADIO_AUTO_FAMILY": "1", "MESH_RADIO_AUTO_LR1121": "1",
                  "MESH_MUZI_QSPI_CAPACITY_GUARD": "1",
                  "MESH_RADIO_AUTO_SX127X": "0", "USE_SX1262": "1", "USE_LR1121": "1",
                  "RADIO_CLASS": "UniversalLoRaRadio", "WRAPPER_CLASS": "UniversalLoRaRadio"}),
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
    dict(key="wm1110CombinedCompanion", capability="companion.wm1110.combined",
         marker="meshcore.capability.companion.wm1110.combined.v1",
         platform="NRF52_PLATFORM", board="seeed-xiao-afruitnrf52-nrf52840",
         target=r"wio_wm1110_companion_radio_full",
         defines={"WIO_WM1110": "1", "COMPANION_RADIO_FULL": "1",
                  "ENABLE_USB_INTERFACE": "1", "MESH_PRIMARY_UART_PORT": "Serial1",
                  "BLE_PIN_CODE": "123456"}),
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


RADIO_FAMILIES = (
    ("Tbeam", r"Tbeam_(SX1262|SX1276)",
     ("repeater", "repeater_bridge_espnow", "room_server", "kiss_modem",
      "repeater_observer_mqtt", "room_server_observer_mqtt", "companion_radio_ble",
      "companion_radio_ble_ps", "companion_radio_full")),
    ("LilyGo_T3S3", r"LilyGo_T3S3_(sx1262|sx1276)",
     ("repeater", "repeater_bridge_espnow", "room_server", "kiss_modem", "terminal_chat",
      "repeater_observer_mqtt", "room_server_observer_mqtt", "companion_radio_usb",
      "companion_radio_ble", "companion_radio_ble_ps", "companion_radio_full")),
    ("Generic_E22", r"Generic_E22_(sx1262|sx1268)", ("repeater", "repeater_bridge_espnow")),
    ("Meshadventurer", r"Meshadventurer_(sx1262|sx1268)",
     ("repeater", "repeater_bridge_espnow", "room_server", "kiss_modem", "terminal_chat",
      "companion_radio_usb", "companion_radio_ble", "companion_radio_full")),
    ("Tenstar_C3", r"Tenstar_C3_(sx1262|sx1268)", ("repeater", "repeater_bridge_espnow", "kiss_modem")),
    ("muzi_base", r"muzi_base_(uno|duo)",
     ("repeater", "room_server", "companion_radio_usb", "companion_radio_ble", "companion_radio_full")),
)
RADIO_CAPACITY_MINIMUMS = {
    "tbeam": dict(MAX_CONTACTS=160, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=128),
    "lilygo_t3s3": dict(MAX_CONTACTS=350, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=512),
    "meshadventurer": dict(MAX_CONTACTS=160, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=128),
    "meshadventurer_m33s": dict(MAX_CONTACTS=160, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=128),
    "muzi_base": dict(MAX_CONTACTS=350, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=256),
}
WM1110_CAPACITY_MINIMUMS = dict(MAX_CONTACTS=350, MAX_GROUP_CHANNELS=40,
                              OFFLINE_QUEUE_SIZE=256)


def integer_define(value):
    return int(value.rstrip("uUlL")) if isinstance(value, str) and re.fullmatch(r"\d+[uUlL]*", value) else None


def radio_capacity_matches(target, defines):
    if "_companion_radio_" not in target.lower():
        return True
    family = target.lower().split("_companion_radio_", 1)[0]
    minimums = RADIO_CAPACITY_MINIMUMS.get(family)
    return minimums is not None and all(
        integer_define(defines.get(key)) is not None
        and integer_define(defines[key]) >= minimum for key, minimum in minimums.items())


def specs_for_config(target, env):
    defines = resolved_defines(env.get("build_flags", []))
    return [spec for spec in SPECS if env.get("board") == spec["board"]
            and re.fullmatch(spec["target"], target, re.I)
            and all(defines.get(key) == value for key, value in spec["defines"].items())
            and (spec["key"] != "autoLoRaRadio" or radio_capacity_matches(target, defines))
            and (spec["key"] != "wm1110CombinedCompanion" or all(
                integer_define(defines.get(key)) is not None
                and integer_define(defines[key]) >= value
                for key, value in WM1110_CAPACITY_MINIMUMS.items()))
            and (spec["key"] not in ("headlessR8Kiss", "headlessWioL1Kiss")
                 or "DISPLAY_CLASS" not in defines)]


def proven(manifest, spec):
    return (manifest.get("verified") is True and manifest.get("platform") == spec["platform"]
            and "hardware.qualification.pending" not in manifest.get("capabilities", [])
            and re.fullmatch(spec["target"], manifest.get("target", ""), re.I)
            and spec["capability"] in manifest.get("capabilities", [])
            and any(row.get("capability") == spec["capability"] and row.get("present") is True
                    and row.get("source") == "packaged application" and row.get("evidence") == spec["marker"]
                    for row in manifest.get("verification", [])))


def runtime_metadata(manifest, env, release_source):
    result = {}
    configured = specs_for_config(manifest["target"], env)
    for capability in dict.fromkeys(spec["capability"] for spec in SPECS):
        if capability not in manifest.get("capabilities", []):
            continue
        matches = [spec for spec in configured if spec["capability"] == capability]
        if len(matches) != 1 or not proven(manifest, matches[0]):
            raise ValueError(manifest["target"] + ": consolidation requires exact configuration and packaged application proof")
        spec = matches[0]
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


def radio_legacy(target):
    accessory = re.fullmatch(r"muzi_base_(uno|duo)_superio_(companion_radio_(?:usb|ble|full))", target, re.I)
    if accessory:
        return "muzi_base", accessory[1].lower(), accessory[2].lower()
    for canonical, prefix, roles in RADIO_FAMILIES:
        found = re.fullmatch(prefix + r"_(" + "|".join(roles) + r")", target, re.I)
        if found:
            return canonical, found[1].lower(), found[2].lower()
    return None


def radio_replacements(target):
    legacy = radio_legacy(target)
    if legacy is None:
        return []
    prefix, _, role = legacy
    if "_superio_" in target.lower():
        return [("muzi_base_companion_radio_full", "muziRadioAccessories"),
                ("muzi_base_" + legacy[1] + "_companion_radio_full", "muziAccessories")]
    if role.startswith("companion_radio_") or role == "terminal_chat":
        return [(prefix + "_companion_radio_full", "autoLoRaRadio")]
    choices = [prefix + "_" + role]
    if role.endswith("_observer_mqtt"):
        choices.append(prefix + "_" + role.removesuffix("_observer_mqtt"))
    return [(name, "autoLoRaRadio") for name in choices]


def replacements(target):
    """Known functionality successors, never aliases for deployed update IDs."""
    if target.lower() == "wio_wm1110_companion_radio_ble":
        return [("wio_wm1110_companion_radio_full", "wm1110CombinedCompanion")]
    if re.fullmatch(r"WioTrackerL1Eink_companion_radio_(?:ble|full)", target, re.I):
        return [("WioTrackerL1_companion_radio_full", "unifiedWioL1Display")]
    if target.lower() == "heltec_v4_r8_tft_kiss_modem":
        return [("heltec_v4_r8_kiss_modem", "headlessR8Kiss")]
    if target.lower() == "wiotrackerl1eink_kiss_modem":
        return [("WioTrackerL1_kiss_modem", "headlessWioL1Kiss")]
    radio = radio_replacements(target)
    if radio:
        return radio
    unified = unified_v4_replacements(target)
    if re.fullmatch(r"PicoW_companion_radio_(?:usb|ble|wifi)", target, re.I):
        return [("PicoW_companion_radio", "combinedCompanion")]
    if re.fullmatch(r"heltec_v4(?:_3)?_expansionkit_tft_companion_radio_(?:usb|ble|wifi|full)(?:_ps)?(?:_fem(?:on|off))?", target, re.I):
        return unified + [("heltec_v4_tft_companion_radio_full_femon", "tftExpansionCompatible")]
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
    def environment(name, radio=False):
        # Full nRF52 targets can be generated by build.sh from their exact
        # USB or BLE base. Never substitute another physical radio variant.
        transports = ("ble", "usb") if radio else ("usb", "ble")
        for choice in (name, *(name.replace("_companion_radio_full", "_companion_radio_" + transport)
                               for transport in transports)):
            if "env:" + choice in envs:
                return envs["env:" + choice]
        return None
    for candidate, key in replacements(target):
        radio = key in ("autoLoRaRadio", "muziRadioAccessories")
        env = environment(candidate, radio or key == "unifiedWioL1Display")
        source_env = environment(target, radio)
        if not env or not source_env or env.get("board") != source_env.get("board"):
            continue
        if key in ("displayAuto", "muziAccessories", "techoLiteDisplay"):
            spec = spec_for_defines(resolved_defines(env.get("build_flags", [])), env.get("board"))
            expected = {"displayAuto": "rc32", "muziAccessories": "muzi_superio", "techoLiteDisplay": "techo_lite"}[key]
            qualified = spec and spec["marker"] == expected and re.fullmatch(spec["targets"], candidate.lower())
        else:
            qualified = any(spec["key"] == ("autoLoRaRadio" if radio else key)
                            for spec in specs_for_config(candidate, env))
            if key == "muziRadioAccessories":
                accessory = spec_for_defines(resolved_defines(env.get("build_flags", [])), env.get("board"))
                qualified = (qualified and accessory and accessory["marker"] == "muzi_superio"
                             and re.fullmatch(accessory["targets"], candidate.lower()))
        if radio:
            legacy = radio_legacy(target)
            old = resolved_defines(source_env.get("build_flags", []))
            new = resolved_defines(env.get("build_flags", []))
            expected = {"uno": "CustomSX1262", "duo": "CustomLR1121"}.get(legacy[1],
                       "CustomSX" + legacy[1][2:])
            qualified = (qualified and old.get("RADIO_CLASS") == expected
                         and old.get("WRAPPER_CLASS") == expected + "Wrapper"
                         and old.get("MESH_RADIO_AUTO_FAMILY", "0") == "0")
            for field in ("MAX_CONTACTS", "MAX_GROUP_CHANNELS", "OFFLINE_QUEUE_SIZE", "MAX_NEIGHBOURS",
                          "FLOOD_CHANNEL_SCOPE_SLOTS"):
                if field in old:
                    before, after = integer_define(old[field]), integer_define(new.get(field))
                    qualified = qualified and before is not None and after is not None and after >= before
        if key == "wm1110CombinedCompanion":
            old = resolved_defines(source_env.get("build_flags", []))
            new = resolved_defines(env.get("build_flags", []))
            qualified = qualified and old.get("WIO_WM1110") == "1" and "BLE_PIN_CODE" in old
            for field in WM1110_CAPACITY_MINIMUMS:
                before, after = integer_define(old.get(field)), integer_define(new.get(field))
                qualified = qualified and before is not None and after is not None and after >= before
        if qualified:
            return candidate
    return None


def omit_redundant_records(records):
    """Remove resumed alternatives only with an actual qualified successor."""
    def same_sensors(left, right):
        return (sorted(x for x in left.get("capabilities", []) if x.startswith("sensor.profile."))
                == sorted(x for x in right.get("capabilities", []) if x.startswith("sensor.profile.")))

    def same_policy(left, right):
        return (left.get("build_profile") == right.get("build_profile")
                and same_sensors(left, right))

    def qualified(manifest, key):
        if key == "muziRadioAccessories":
            return qualified(manifest, "autoLoRaRadio") and qualified(manifest, "muziAccessories")
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

    def radio_successor(left, right, key):
        source = left.get("source_commit")
        wio_full = (key == "unifiedWioL1Display" and right.get("build_profile") == "full"
                    and left.get("target", "").lower().endswith(("_usb", "_ble"))
                    and same_sensors(left, right))
        if (not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source)
                or right.get("source_commit") != source
                or not (same_policy(left, right) or wio_full)):
            return False
        # Full already requires its full capacity recipe before advertising
        # this packaged marker. An old transport/terminal image may retire
        # only when both actual replacement transports survived linking.
        if "_companion_radio_" in right.get("target", "").lower():
            required = {"companion.usb", "companion.bluetooth"}
            linked = {row.get("capability") for row in right.get("verification", [])
                      if row.get("present") is True and row.get("source") == "linked image"
                      and row.get("evidence")}
            return (right.get("build_profile") == "full"
                    and required <= set(right.get("capabilities", [])) and required <= linked)
        return True

    def wm1110_successor(left, right):
        source = left.get("source_commit")
        required = {"companion.usb", "companion.bluetooth"}
        linked = {row.get("capability") for row in right.get("verification", [])
                  if row.get("present") is True and row.get("source") == "linked image"
                  and row.get("evidence")}
        return (left.get("target", "").lower() == "wio_wm1110_companion_radio_ble"
                and right.get("target", "").lower() == "wio_wm1110_companion_radio_full"
                and isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source)
                and right.get("source_commit") == source
                and right.get("build_profile") == "full" and same_sensors(left, right)
                and required <= set(right.get("capabilities", [])) and required <= linked)

    result = []
    for record in records:
        manifest = record["manifest"]
        alternatives = replacements(manifest["target"])
        if not any(candidate["manifest"]["target"].lower() == target.lower()
                   and (key not in ("autoLoRaRadio", "muziRadioAccessories", "unifiedWioL1Display",
                                    "headlessR8Kiss", "headlessWioL1Kiss")
                        or radio_successor(manifest, candidate["manifest"], key))
                   and (key != "wm1110CombinedCompanion"
                        or wm1110_successor(manifest, candidate["manifest"]))
                   and (same_policy(manifest, candidate["manifest"])
                        or key in ("muziAccessories", "techoLiteDisplay", "unifiedWioL1Display",
                                   "wm1110CombinedCompanion")
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
