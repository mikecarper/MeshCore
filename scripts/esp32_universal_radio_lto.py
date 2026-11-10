"""Link qualified universal radio images with the compiler's LTO plugin."""
import re

LINK_FLAGS = ("-flto", "-flto-partition=one", "-ffunction-sections", "-fdata-sections",
              "-mtext-section-literals", "-mlongcalls")


def enabled(platform, mcu, board, target):
    return (platform == "espressif32" and (
        (mcu == "esp32s3" and board == "t3_s3_v1_x"
         and re.fullmatch(r"LilyGo_T3S3_(?:repeater(?:_bridge_espnow|_observer_mqtt)?|"
                          r"room_server(?:_observer_mqtt)?|kiss_modem|terminal_chat|"
                          r"companion_radio_(?:usb|ble|full)(?:_ps)?)", target) is not None)
        or (mcu == "esp32s3" and board == "ebyte_eora-s3"
            and target == "Ebyte_EoRa-S3_repeater_observer_mqtt")
        or (mcu == "esp32" and board == "ttgo-t-beam"
            and target in ("Tbeam_repeater_observer_mqtt", "Tbeam_room_server_observer_mqtt"))))


def install(build_env):
    if not enabled(build_env.PioPlatform().name,
                   str(build_env.BoardConfig().get("build.mcu", "")).lower(),
                   str(build_env.get("BOARD", "")), str(build_env.get("PIOENV", ""))):
        return False
    # PlatformIO already supplies plugin-aware gcc-ar/gcc-ranlib. BUILD_FLAGS
    # reaches compilation, but LTO's final code generation needs these too.
    # Keep each function's literals nearby so a large LTO partition cannot
    # exceed L32R's range; longcalls retains the SDK's cross-file call policy.
    flags = list(LINK_FLAGS)
    if str(build_env.BoardConfig().get("build.mcu", "")).lower() == "esp32":
        # The T-Beam's PSRAM cache workaround must also affect LTO codegen.
        flags.extend(("-mfix-esp32-psram-cache-issue", "-fno-tree-loop-im", "-fno-partial-inlining",
                      "-mno-target-align"))
    build_env.AppendUnique(LINKFLAGS=flags)
    return True


if "Import" in globals():
    Import("env")
    install(env)
