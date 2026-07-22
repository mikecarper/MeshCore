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


build_flags = str(env.get("BUILD_FLAGS", "")) + " " + os.environ.get("PLATFORMIO_BUILD_FLAGS", "")
pio_env = env.subst("$PIOENV").lower()

if (has_define("PORTABLE_ESP32_NANO_LIBC") or
        "PORTABLE_ESP32_NANO_LIBC" in build_flags or
        os.environ.get("MESHCORE_PORTABLE_NANO_LIBC") == "1" or
        "observer_mqtt" in pio_env or "bridge_espnow" in pio_env):
    # newlib-nano provides the same C ABI with a substantially smaller printf
    # implementation. These profiles omit display, GPS, external sensors, and
    # the general CLI paths that require floating-point printf.
    env.Append(LINKFLAGS=["--specs=nano.specs"])
