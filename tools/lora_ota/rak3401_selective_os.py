"""PlatformIO pre-hook for cumulative, per-translation-unit ``-Os`` bridges.

Stage 1 size-optimizes crypto/Curve25519 units first. The remaining C/C++
units are assigned deterministically to stages 2 through 13. A stage includes
all earlier stages, so every intermediate is a normal bootable build and stage
13 is equivalent to globally selecting ``-Os``. The last two primary buckets
are subdivided because their measured nRF52 deltas exceeded internal staging.
"""

import hashlib
import os
import re

Import("env")


try:
    STAGE = int(os.environ.get("MOTA_BRIDGE_OS_STAGE", "0"))
except ValueError as exc:
    raise RuntimeError("MOTA_BRIDGE_OS_STAGE must be an integer") from exc

if STAGE < 1 or STAGE > 13:
    raise RuntimeError("MOTA_BRIDGE_OS_STAGE must be in 1..13")

CRYPTO_MARKERS = (
    "ed25519",
    "curve25519",
    "nrf52crypto",
    "adafruit_nrfcrypto",
)


def _normalized_unit_path(path, construction_env):
    absolute = os.path.abspath(path)
    project = os.path.abspath(construction_env.subst("$PROJECT_DIR"))
    relative = os.path.relpath(absolute, project)
    if relative != ".." and not relative.startswith(".." + os.sep):
        return relative.replace("\\", "/").lower()
    normalized = absolute.replace("\\", "/").lower()
    marker = "/.platformio/"
    if marker in normalized:
        return ".platformio/" + normalized.split(marker, 1)[1]
    return "/".join(normalized.rsplit("/", 4)[-4:])


def _unit_stage(path, construction_env):
    normalized = _normalized_unit_path(path, construction_env)
    if any(marker in normalized for marker in CRYPTO_MARKERS):
        return 1
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    primary = digest[0] % 7
    if primary <= 4:
        return 2 + primary
    if primary == 5:
        secondary = digest[1] % 3
        if secondary == 0:
            return 7
        if secondary == 1:
            if digest[2] % 2 == 0:
                return 8 + digest[3] % 2
            return 10
        return 11
    return 12 + digest[1] % 2


def _size_optimize_selected(env, node):
    path = node.get_abspath()
    if not re.search(r"\.(?:c|cc|cpp|cxx)$", path, re.IGNORECASE):
        return node
    normalized_path = _normalized_unit_path(path, env)
    forced_opt = os.environ.get("MOTA_BRIDGE_MY_MESH_OPT", "")
    if normalized_path.endswith("/examples/simple_repeater/mymesh.cpp") and forced_opt:
        profiles = {
            "-O3": ["-O3"],
            "-O2": ["-O2"],
            "-O1": ["-O1"],
            "-Os": ["-Os"],
            "O2_NO_ALIGN": [
                "-O2",
                "-fno-align-functions",
                "-fno-align-jumps",
                "-fno-align-labels",
                "-fno-align-loops",
            ],
            "O2_SIZE_1": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen",
            ],
            "O2_SIZE_1_HOIST": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-fcode-hoisting",
            ],
            "O2_SIZE_1_INLINE": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-finline-functions",
            ],
            "O2_SIZE_1_INLINE_HOIST": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-finline-functions",
                "-fcode-hoisting",
            ],
            "O2_SIZE_1_NOSCHED": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-fno-schedule-insns",
            ],
            "O2_SIZE_2": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-freorder-blocks-algorithm=simple",
            ],
            "O2_SIZE_3": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-freorder-blocks-algorithm=simple",
                "-fno-schedule-insns",
            ],
            "O2_SIZE_4": [
                "-O2", "-fno-align-functions", "-fno-align-jumps",
                "-fno-align-labels", "-fno-align-loops",
                "-fno-optimize-strlen", "-freorder-blocks-algorithm=simple",
                "-fno-schedule-insns", "-fno-tree-loop-vectorize",
            ],
            "O2_SIZE_FLAGS": [
                "-O2",
                "-fno-align-functions",
                "-fno-align-jumps",
                "-fno-align-labels",
                "-fno-align-loops",
                "-fno-optimize-strlen",
                "-freorder-blocks-algorithm=simple",
                "-fno-schedule-insns",
                "-fno-tree-loop-vectorize",
                "-fno-tree-slp-vectorize",
            ],
        }
        if forced_opt not in profiles:
            raise RuntimeError("unsupported MOTA_BRIDGE_MY_MESH_OPT profile")
        flags = [
            str(flag)
            for flag in env.get("CCFLAGS", [])
            if not re.fullmatch(r"-O(?:0|1|2|3|g|s|fast)", str(flag))
        ]
        flags.extend(profiles[forced_opt])
        print(f"MOTA bridge MyMesh optimizer: {forced_opt}")
        return env.Object(node, CCFLAGS=flags)

    unit_stage = _unit_stage(path, env)
    if unit_stage > STAGE:
        return node
    if (
        STAGE == 5
        and unit_stage == 5
        and os.environ.get("MOTA_BRIDGE_SPLIT_STAGE5") == "first"
    ):
        normalized = _normalized_unit_path(path, env)
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        if digest[1] % 2:
            return node

    if os.environ.get("MOTA_BRIDGE_OS_VERBOSE") == "1" and unit_stage == STAGE:
        print(f"MOTA bridge stage {STAGE} unit: {normalized_path}")

    flags = [
        str(flag)
        for flag in env.get("CCFLAGS", [])
        if not re.fullmatch(r"-O(?:0|1|2|3|g|s|fast)", str(flag))
    ]
    flags.append("-Os")
    return env.Object(node, CCFLAGS=flags)


print(f"MOTA bridge selective optimizer stage {STAGE}/13")
if STAGE == 5 and os.environ.get("MOTA_BRIDGE_SPLIT_STAGE5") == "first":
    print("MOTA bridge stage 5 split: first half")
env.AddBuildMiddleware(_size_optimize_selected)
