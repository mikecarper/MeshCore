Import("env")

import os


def has_define(name):
    for define in env.get("CPPDEFINES", []):
        if define == name:
            return True
        if isinstance(define, str) and define.startswith(name + "="):
            return True
        if isinstance(define, (tuple, list)) and define and define[0] == name:
            return True
    return False


build_flags = (
    str(env.get("BUILD_FLAGS", ""))
    + " "
    + os.environ.get("PLATFORMIO_BUILD_FLAGS", "")
)

if (
    has_define("PORTABLE_ESP32_ROM_NANO_FORMAT")
    or "PORTABLE_ESP32_ROM_NANO_FORMAT" in build_flags
):
    # ESP-IDF supplies linker tables for the compact printf implementation in
    # chip ROM. This retains the framework's normal C library and ABI; it does
    # not substitute libc_nano. The classic ESP32 table must not be used with
    # that chip's PSRAM cache workaround. The ESP32-S3 has its own ROM table and
    # no equivalent restriction in the framework linker script.
    mcu = str(env.BoardConfig().get("build.mcu", "")).lower()
    classic_esp32_psram = mcu == "esp32" and (
        has_define("BOARD_HAS_PSRAM") or "BOARD_HAS_PSRAM" in build_flags
    )
    if mcu == "esp32" and not classic_esp32_psram:
        env.Append(LINKFLAGS=["-T", "%s.rom.newlib-nano.ld" % mcu])
    elif mcu == "esp32s3":
        env.Append(LINKFLAGS=["-T", "%s.rom.newlib-nano.ld" % mcu])
