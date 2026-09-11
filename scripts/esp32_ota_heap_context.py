#!/usr/bin/env python3
"""Keep the idle mOTA workspace off classic ESP32's static DRAM.

Classic ESP32 links into a dram0_0_seg of ~124 KiB: memory.ld reserves the BT
controller's 0xdb5c bytes off the 0x2c200 window before the application gets
any, and no runtime call gives those static bytes back. OtaContext is several
kilobytes of that budget, held for the life of the device even though a
repeater or room server is outside an OTA window essentially always.

OTA_HEAP_CONTEXT switches OtaContext to the on-demand storage path, so it is
allocated when an OTA operation needs it and freed once idle. Applied here
rather than per-env so every classic ESP32 build gets it and none can drift.

Scoped to build.mcu == "esp32" deliberately. S2/S3/C-series and nRF52 have no
equivalent static-DRAM ceiling, so there the .bss singleton is the better
trade: it guarantees the workspace is present, and OTA is a recovery path.

This must be a build flag, not a header default. OTA_HEAP_CONTEXT has to hold
the same value in every translation unit that sees OtaContext.h - including
OtaContext.cpp, which defines the storage - and a header test on
CONFIG_IDF_TARGET_ESP32 would depend on whether that unit had already reached
sdkconfig.h.
"""

import re

Import("env")  # noqa: F821 -- PlatformIO/SCons supplies Import

# Both name the owner of the context storage, and OtaContext.h rejects the
# pair. OTA_SHARED_COMPANION_QUEUE arrives via PLATFORMIO_BUILD_FLAGS from
# build.sh's Full Companion recipes, which are nRF52/S3 today - but a future
# classic ESP32 Full Companion must lose this default, not fail to compile.
CONFLICTING = ("OTA_HEAP_CONTEXT", "OTA_SHARED_COMPANION_QUEUE")


def _already_defined(env):
    for define in env.get("CPPDEFINES", []):
        name = define[0] if isinstance(define, (list, tuple)) else define
        if str(name) in CONFLICTING:
            return True
    # PLATFORMIO_BUILD_FLAGS reaches BUILD_FLAGS as raw text, which may not be
    # parsed into CPPDEFINES yet when this pre-script runs.
    flags = env.get("BUILD_FLAGS", [])
    text = flags if isinstance(flags, str) else " ".join(map(str, flags))
    return bool(re.search(r"(?:^|\s)-D\s*(?:" + "|".join(CONFLICTING)
                          + r")(?=[=\s]|$)", text))


def _apply(env):
    if str(env.BoardConfig().get("build.mcu", "")).lower() != "esp32":
        return
    if _already_defined(env):
        return
    env.Append(CPPDEFINES=[("OTA_HEAP_CONTEXT", 1)])


_apply(env)  # noqa: F821
