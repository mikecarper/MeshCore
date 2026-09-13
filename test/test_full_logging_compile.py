#!/usr/bin/env python3
"""Exercise real PlatformIO flag ordering through a host compiler (no board).

Requires PlatformIO and its tool-scons package, installed by the native tests
in CI. This deliberately uses ProcessFlags, not a reimplementation of it.
"""

import contextlib
import io
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import SCons.Script
except ImportError:
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    for path in sorted((core / "packages" / "tool-scons").glob("scons-local-*"), reverse=True):
        sys.path.insert(0, str(path))
    import SCons.Script

from platformio.builder.tools.piobuild import ParseFlagsExtended, ProcessFlags

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deduplicate_full_build_flags.py"
SOURCE = """
#if !defined(MESH_PACKET_LOGGING) || MESH_PACKET_LOGGING != 1
#error Full image lost USB packet logging
#endif
#if defined(MESH_DEBUG)
#error Packet logging must not reenable disabled debug logging
#endif
int logging_contract(void) { return MESH_PACKET_LOGGING; }
"""


class FullLoggingCompilerTest(unittest.TestCase):
    def compile(self, flags, *, required):
        self.assertIsNotNone(shutil.which("gcc"), "Install gcc to run the compiler regression")
        env = SCons.Script.Environment(tools=["gcc"], BUILD_FLAGS=flags)
        env.AddMethod(ParseFlagsExtended)
        env.AddMethod(ProcessFlags)
        with mock.patch.dict(os.environ, {
            "MESHCORE_ESP32_FULL_BUILD": "1",
            "MESHCORE_COMPANION_RADIO_FULL": "0",
            "MESHCORE_REQUIRE_PACKET_LOGGING": "1" if required else "0",
            "PLATFORMIO_BUILD_FLAGS": "-DMESH_PACKET_LOGGING=1",
        }), contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(SCRIPT), init_globals={"Import": lambda _: None, "env": env})
        env.ProcessFlags(env["BUILD_FLAGS"])
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "probe.c", Path(directory) / "probe.o"
            source.write_text(SOURCE)
            command = env.subst_list("$CCCOM", source=env.File(str(source)), target=env.File(str(target)))[0]
            result = subprocess.run([str(item) for item in command], text=True, capture_output=True)
            return result, [str(item) for item in command]

    def test_reproduces_the_published_station_g2_failure(self):
        result, command = self.compile(
            ["-UMESH_DEBUG -UMESH_PACKET_LOGGING", "-DMESH_PACKET_LOGGING=1"], required=False)
        self.assertGreater(command.index("-UMESH_PACKET_LOGGING"), command.index("-DMESH_PACKET_LOGGING=1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Full image lost USB packet logging", result.stderr)

    def test_full_contract_survives_real_platformio_preprocessing(self):
        for disable in ("-UMESH_PACKET_LOGGING", "-U MESH_PACKET_LOGGING",
                        "-DMESH_PACKET_LOGGING=0", "-D MESH_PACKET_LOGGING=0"):
            for before in (True, False):
                with self.subTest(disable=disable, before=before):
                    flags = ["-DMESH_PACKET_LOGGING=1", "-UMESH_DEBUG", disable]
                    if before:
                        flags.reverse()
                    result, _ = self.compile(flags, required=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
