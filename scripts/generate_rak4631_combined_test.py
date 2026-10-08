#!/usr/bin/env python3
"""Generate matched local RAK4631 baseline/combined test configs, without building.

The production environment and release artifacts are left untouched. The four
configs retain the unified target's OTA identity, 8 KiB loop stack, 63 flood rules
and exact board recipe. Full/Reduced sensor flags come from build.sh's normal
helpers. Run the printed PlatformIO commands individually; this checkout permits
only one PlatformIO process at a time. Preserve each build's outputs before the
next command reuses its environment directory.
"""

from __future__ import annotations

import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TARGET = "RAK_4631_repeater_unified_lora_ota"
ETHERNET_LIBRARY = "https://github.com/RAKWireless/RAK13800-W5100S/archive/1.0.2.zip"

PROFILE_HELPERS = r'''
set -euo pipefail
source build.sh
target=RAK_4631_repeater_unified_lora_ota
PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[$target]=1
PIO_ENV_OTA_BY_NAME[$target]=1
PIO_ENV_SD_OTA_BY_NAME[$target]=0
NRF52_OTA_SENSOR_PROFILE=$MESH_TRIAL_SENSOR_PROFILE
SKIP_DECLARED_REDUCTIONS=$([ "$NRF52_OTA_SENSOR_PROFILE" = full ] && printf 1 || printf 0)
DISABLE_DEBUG=1
PLATFORMIO_BUILD_FLAGS=''
PLATFORMIO_BUILD_UNFLAGS=''
disable_debug_flags "$target"
apply_nrf52_size_profile "$target"
apply_lora_ota_no_external_sensors_profile "$target"
apply_radio_overrides
apply_firmware_profile_overrides
export PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS
python3 -c 'import json,os;print(json.dumps({name:os.environ.get(name, "") for name in ("PLATFORMIO_BUILD_FLAGS","PLATFORMIO_BUILD_UNFLAGS")}))'
'''


def profile_flags(sensor: str) -> dict[str, str]:
    # No project-context initialization, build worker or PlatformIO invocation.
    environment = {**os.environ, "MESH_TRIAL_SENSOR_PROFILE": sensor,
                   "FIRMWARE_PROFILE_OVERRIDE": "cascade",
                   "RADIO_FREQ_OVERRIDE": "910.525", "RADIO_BW_OVERRIDE": "62.5",
                   "RADIO_SF_OVERRIDE": "7", "RADIO_CR_OVERRIDE": "5",
                   "RADIO_SETTING_TITLE": "USA Cascadia"}
    result = subprocess.run(["bash", "-c", PROFILE_HELPERS], cwd=ROOT,
                            env=environment, capture_output=True, text=True, check=True)
    values = json.loads(result.stdout.splitlines()[-1])
    if set(values) != {"PLATFORMIO_BUILD_FLAGS", "PLATFORMIO_BUILD_UNFLAGS"}:
        raise ValueError("unexpected build-profile helper output")
    return values


def generate(output: Path, firmware_version: str) -> dict:
    output = output.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("put experimental configs outside the repository and release output")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", firmware_version):
        raise ValueError("firmware version must contain only letters, digits, dots, underscores, plus or minus")
    source = (ROOT / "platformio.ini").read_text()
    extra_configs = "  variants/*/platformio.ini\n  platformio.local.ini"
    if source.count(extra_configs) != 1:
        raise ValueError("main extra_configs changed; refusing a disconnected trial config")
    variant = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    variant.read(ROOT / "variants/rak4631/platformio.ini")
    raw_flags = variant["env:" + TARGET]["build_flags"]
    mota_id = "0x%08x" % int.from_bytes(hashlib.sha256(TARGET.encode()).digest()[:4], "little")
    output.mkdir(parents=True, exist_ok=True)
    profiles, configs = {}, []
    for sensor in ("full", "reduced"):
        flags = profile_flags(sensor)
        profiles[sensor] = flags
        for combined in (False, True):
            name = ("combined" if combined else "baseline") + ("" if sensor == "full" else "-reduced")
            common = (f"-DFIRMWARE_VERSION='\"{firmware_version}\"' "
                      f"-DOTA_VARIANT='\"{TARGET}\"' -DMOTA_TARGET_ID={mota_id}")
            build_flags = raw_flags + "\n" + flags["PLATFORMIO_BUILD_FLAGS"] + " " + common
            if combined:
                build_flags += " -DETHERNET_ENABLED=1 -DRAK4631_COMBINED_ETHERNET=1"
            overlay = "[env:" + TARGET + "]\nbuild_flags =\n"
            overlay += "\n".join("  " + line.strip() for line in build_flags.splitlines() if line.strip()) + "\n"
            overlay += "build_unflags = " + flags["PLATFORMIO_BUILD_UNFLAGS"].strip() + "\n"
            if combined:
                overlay += "lib_deps = ${rak4631.lib_deps}\n  " + ETHERNET_LIBRARY + "\n"
            overlay_path = output / ("overlay-" + name + ".ini")
            overlay_path.write_text(overlay)
            config = source.replace(extra_configs,
                                    "  " + str(ROOT / "variants/*/platformio.ini") + "\n"
                                    "  " + str(ROOT / "platformio.local.ini") + "\n"
                                    "  " + str(overlay_path))
            config_path = output / ("platformio-" + name + ".ini")
            config_path.write_text(config)
            configs.append({"profile": name, "config": str(config_path),
                            "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
                            "overlay_sha256": hashlib.sha256(overlay.encode()).hexdigest()})
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt = {"schema_version": 1, "target": TARGET, "mota_target_id": mota_id,
               "firmware_version": firmware_version, "full_rule_capacity": 63,
               "loop_stack_bytes": 8192, "source_commit": source_commit,
               "profiles": profiles, "configs": configs,
               "scope": "Local config generation only; builds and hardware acceptance remain required."}
    (output / "profile-flags.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path.home() / ".cache" / ("meshcore-rak-combined-" + datetime.now(timezone.utc).strftime("%Y%m%d"))
    parser.add_argument("--output", type=Path, default=default)
    parser.add_argument("--firmware-version", default="v1.17.1.9-rak-combined-trial")
    args = parser.parse_args()
    receipt = generate(args.output, args.firmware_version)
    for config in receipt["configs"]:
        print(" ".join(shlex.quote(value) for value in (
            "pio", "run", "-d", str(ROOT), "-c", config["config"], "-e", TARGET)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
