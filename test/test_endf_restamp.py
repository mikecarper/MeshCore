"""Exercise the production raw-BIN post hook and ordered identity overrides."""
import os
from pathlib import Path
import tempfile
import unittest

from test_nrf52_endf_hex import BuildEnvironment, load_post_script, ml, stamp


class EndfRestampTests(unittest.TestCase):
    def environment(self):
        env = BuildEnvironment(ml.NRF52_APP_BASE_S140_V7)
        env["PIOENV"] = "esp32_restamp_test"
        env["CPPDEFINES"].remove("NRF52_PLATFORM")
        return env

    def test_bin_restamps_identity_without_length_growth_or_same_size_write_omission(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment()
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.bin"]
            path = Path(directory) / "firmware.bin"
            body = b"\xe9" + bytes(range(255))
            path.write_bytes(body)
            stamp(action, path, env)
            for index in range(3):
                env["CPPDEFINES"].extend([
                    ("FIRMWARE_VERSION", f"v1.17.1.{10 + index}"),
                    ("MOTA_TARGET_ID", hex(0x87654321 + index)),
                    ("MOTA_HW_ID", f"CHANGED_{index}"),
                ])
                stamp(action, path, env)
                stamped = path.read_bytes()
                self.assertEqual(len(stamped), len(body) + ml.ENDF_LEN)
                self.assertEqual(ml.parse_endf(stamped)[0], body)
                self.assertEqual(stamped.count(ml.ENDF_MAGIC), 1)
                self.assertEqual(ml.parse_endf_ident(stamped), ml.FwIdent(
                    ml.pack_version(f"1.17.1.{10 + index}"),
                    0x87654321 + index, f"CHANGED_{index}"))
                os.utime(path, ns=(1_000_000_000, 1_000_000_000))
                stamp(action, path, env)
                self.assertEqual(path.read_bytes(), stamped)
                self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)

    def test_cppdef_last_matching_tuple_list_or_bare_define_wins(self):
        env = self.environment()
        module = load_post_script(env)
        cases = (([("VALUE", "old"), ("VALUE", "new")], "new"),
                 ([("VALUE", "old"), ["VALUE", 123]], "123"),
                 ([("VALUE", "old"), "VALUE"], ""),
                 (["VALUE", ("VALUE", "new")], "new"),
                 ([("VALUE", "old"), ("VALUE",)], ""),
                 ([("OTHER", 123)], None))
        for defines, expected in cases:
            with self.subTest(defines=defines):
                env["CPPDEFINES"] = defines
                self.assertEqual(module._cppdef("VALUE"), expected)

    def test_general_ensure_endf_retains_its_existing_preserve_identity_contract(self):
        image, _ = ml.ensure_endf(b"body", ml.FwIdent(1, 2, "ORIGINAL"))
        self.assertEqual(ml.ensure_endf(image, ml.FwIdent(3, 4, "OTHER"))[0], image)

    def test_a_freshly_relinked_body_receives_its_own_current_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment()
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.bin"]
            path = Path(directory) / "firmware.bin"
            for body in (b"\xe9old app", b"\xe9new app, relinked without an old trailer"):
                path.write_bytes(body)
                stamp(action, path, env)
                parsed_body, hash8 = ml.parse_endf(path.read_bytes())
                self.assertEqual(parsed_body, body)
                self.assertEqual(hash8, ml.mh8(body))


if __name__ == "__main__":
    unittest.main()
