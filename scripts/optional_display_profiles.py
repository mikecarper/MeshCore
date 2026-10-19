"""Bind optional-screen directions to the exact qualified application."""
import json
import re
import sys

from t114_profiles import runtime_metadata as t114_metadata

CAPABILITY = "display.autodetect"
SPECS = (
    dict(flag="RC32_DISPLAY_AUTODETECT", board="heltec-rc32", guard="HELTEC_RC32",
         driver="NV3001BDisplay", controller="nv3001b", marker="rc32",
         targets=r"heltec_rc32_(?!without_display_).+"),
    dict(flag="WIRELESS_TRACKER_DISPLAY_AUTODETECT", board="esp32-s3-devkitc-1",
         guard="HELTEC_LORA_V3", driver="ST7735Display", controller="st7735", marker="tracker",
         targets=r"heltec_wireless_tracker_.+"),
    dict(flag="WIRELESS_TRACKER_DISPLAY_AUTODETECT", board="heltec_tracker_v1_1",
         guard="HELTEC_TRACKER_V1_1", driver="ST7735Display", controller="st7735", marker="tracker",
         targets=r"heltec_tracker_v1_1_.+"),
    dict(flag="HELTEC_TFT_DISPLAY_AUTODETECT", board="heltec_v4_r8",
         guard="HELTEC_V4_R8_TFT", driver="ST7789LCDDisplay", controller="st7789", marker="heltec_r8",
         targets=r"heltec_v4_r8_tft_.+"),
    dict(flag="MUZI_BASE_ACCESSORY_AUTODETECT", board="muzi_base", guard="MUZI_BASE",
         driver="SH1107Display", controller="sh1107", marker="muzi_superio", platform="NRF52_PLATFORM",
         defines={"RADIO_CLASS": "CustomLR1121", "USE_LR1121": "1"},
         targets=r"muzi_base_duo_(?:superio_)?companion_radio_(?:usb|ble|full)"),
    dict(flag="MUZI_BASE_ACCESSORY_AUTODETECT", board="muzi_base", guard="MUZI_BASE",
         driver="SH1107Display", controller="sh1107", marker="muzi_superio", platform="NRF52_PLATFORM",
         defines={"RADIO_CLASS": "CustomSX1262", "USE_SX1262": "1"},
         targets=r"muzi_base_uno_(?:superio_)?companion_radio_(?:usb|ble|full)"),
    dict(flag="TECHO_LITE_DISPLAY_AUTODETECT", board="t-echo", guard="LILYGO_TECHO_LITE",
         driver="GxEPDDisplay", controller="ssd1681", marker="techo_lite", platform="NRF52_PLATFORM",
         defines={"EINK_DISPLAY_MODEL": "GxEPD2_122_T61"},
         targets=r"lilygo_t-echo-lite_(?:non_shell_)?(?:companion_radio_(?:usb|ble|full)|(?:repeater|room_server)(?:_lora_ota(?:_no_external_sensors)?)?)"),
)


def marker(spec):
    return "meshcore.capability.display.autodetect." + spec["marker"] + ".v1"


def spec_for_defines(defines, board=None):
    for spec in SPECS:
        if (defines.get(spec["flag"]) == "1"
                and defines.get(spec["guard"], "0") not in ("0", "false")
                and defines.get("DISPLAY_CLASS") == spec["driver"]
                and all(defines.get(key) == value for key, value in spec.get("defines", {}).items())
                and (board is None or board == spec["board"])):
            return spec
    return None


def runtime_metadata(manifest, defines, release_source=None, board=None):
    if CAPABILITY not in (manifest.get("capabilities") or []):
        return {}
    if str(manifest.get("target", "")).lower().startswith("heltec_t114_"):
        return t114_metadata(manifest, defines, release_source)
    spec = spec_for_defines(defines, board)
    if (not spec or board is None or manifest.get("verified") is not True
            or manifest.get("platform") != spec.get("platform", "ESP32_PLATFORM")
            or not re.fullmatch(spec["targets"], str(manifest.get("target", "")).lower())
            or not any(row.get("capability") == CAPABILITY and row.get("present") is True
                       and row.get("source") == "packaged application"
                       and row.get("evidence") == marker(spec)
                       for row in manifest.get("verification") or [])):
        raise ValueError("Optional display metadata requires exact board, flags and packaged detection proof")
    source = manifest.get("source_commit", release_source)
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Optional display metadata requires an exact source commit")
    if release_source is not None and source != release_source:
        raise ValueError("Optional display metadata source must match its release")
    return dict(displayAuto=True, displayController=spec["controller"], displaySource=source)


def resolved_defines(flags):
    result = {}
    for flag in flags:
        match = re.fullmatch(r"-([DU])\s*(\w+)(?:=(.*))?", str(flag).strip())
        if match:
            kind, key, value = match.groups()
            if kind == "U":
                result.pop(key, None)
            else:
                result[key] = "1" if value is None else value
    return result


if __name__ == "__main__":
    # Configuration resolution is owned by build.sh; this command never runs PIO.
    envs = {name: dict(options) for name, options in json.load(sys.stdin)}
    env = envs.get("env:" + sys.argv[1], {})
    spec = spec_for_defines(resolved_defines(env.get("build_flags", [])), env.get("board"))
    if not spec:
        sys.exit(1)
    print(marker(spec))
