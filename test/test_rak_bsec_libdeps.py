#!/usr/bin/env python3
"""Check the real RAK BSEC hooks with default and isolated libdeps roots."""

from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FakeEnvironment:
    def __init__(self, project, libdeps, target):
        self.values = {
            "$PROJECT_DIR": str(project),
            "$PROJECT_LIBDEPS_DIR": str(libdeps),
            "$PIOENV": target,
        }
        self.libpaths = [str(libdeps / target / "BSEC Software Library" / "src" / "cortex-m4")]

    def subst(self, variable):
        return self.values[variable]

    def Prepend(self, *, LIBPATH):
        self.libpaths[:0] = LIBPATH


class RakBsecLibdepsTests(unittest.TestCase):
    def test_hard_float_archive_is_first_for_each_dependency_root(self):
        with tempfile.TemporaryDirectory(prefix="rak bsec ") as temp:
            project = Path(temp) / "checkout"
            for board, target in (("rak3401", "RAK_3401_companion_radio_usb"),
                                  ("rak4631", "RAK_4631_companion_radio_full")):
                for libdeps in (project / ".pio" / "libdeps", Path(temp) / "isolated worker" / "libdeps"):
                    with self.subTest(board=board, libdeps=libdeps):
                        hard = libdeps / target / "BSEC Software Library" / "src" / "cortex-m4" / "fpv4-sp-d16-hard"
                        hard.mkdir(parents=True, exist_ok=True)
                        (hard / "libalgobsec.a").touch()
                        env = FakeEnvironment(project, libdeps, target)
                        runpy.run_path(str(ROOT / "variants" / board / "fix_bsec_lib.py"),
                                       init_globals={"env": env, "Import": lambda _: None})
                        self.assertEqual(Path(env.libpaths[0]), hard)
                        self.assertTrue((Path(env.libpaths[0]) / "libalgobsec.a").is_file())
                        self.assertNotEqual(env.libpaths[0], env.libpaths[1])


if __name__ == "__main__":
    unittest.main()
