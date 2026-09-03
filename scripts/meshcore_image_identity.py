import glob
import os
import re

Import("env")


def source_filter_text():
    value = env.GetProjectOption("build_src_filter", "")
    return " ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)


def default_firmware_version():
    pattern = re.compile(r'#\s*define\s+FIRMWARE_VERSION\s+"([^"]+)"')
    example_dirs = set(re.findall(r"examples[/\\]([A-Za-z0-9_]+)", source_filter_text()))
    versions = set()
    for example_dir in example_dirs:
        header_pattern = os.path.join(
            env["PROJECT_DIR"], "examples", example_dir, "*.h"
        )
        for path in glob.glob(header_pattern):
            with open(path, encoding="utf-8", errors="ignore") as header:
                match = pattern.search(header.read())
            if match:
                versions.add(match.group(1))
    return next(iter(versions)) if len(versions) == 1 else "unknown"

# build.sh supplies OTA_VARIANT for release overlays whose logical target can
# differ from PIOENV. Direct `pio run -e ...` builds still need a useful board
# identity, so expose the concrete PlatformIO environment as the fallback.
env.AppendUnique(
    CPPDEFINES=[
        ("MESHCORE_BUILD_ENV", env.StringifyMacro(env["PIOENV"])),
        (
            "MESHCORE_DEFAULT_FIRMWARE_VERSION",
            env.StringifyMacro(default_firmware_version()),
        ),
    ]
)
