"""Production crypto vs RFC vector + independent Python decoder, no AES mocks."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("management", ROOT / "tools/management/report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = [Path(os.environ.get("MESHCORE_CRYPTO_DIR", "/nonexistent"))]
        candidates += sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        cls.crypto = next((p for p in candidates if (p / "AES128.cpp").is_file()), None)
        if cls.crypto is None:
            raise RuntimeError("Install rweather/Crypto 0.4.0 or set MESHCORE_CRYPTO_DIR (no mock fallback)")
        cls.work = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.work.name) / "management-test"
        sources = ["AES128.cpp", "AESCommon.cpp", "BlockCipher.cpp", "Crypto.cpp", "SHA256.cpp", "Hash.cpp"]
        host_define = [] if os.name == "nt" else ["-DHOST_BUILD"]
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        cmd = [shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra"] + host_define + [
               *sanitizers, "-I", str(cls.crypto), "-I", str(ROOT / "src"),
               str(ROOT / "src/helpers/ManagementReport.cpp"),
               str(ROOT / "test/fixtures/management/protocol_test.cpp")]
        cmd += [str(cls.crypto / p) for p in sources] + ["-o", str(cls.exe)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        result = subprocess.run([str(cls.exe)], check=True, capture_output=True, text=True)
        cls.pages = [bytes.fromhex(line) for line in result.stdout.splitlines()]
        # Compile the unchanged production runtime and transaction writer with
        # a memory filesystem/radio in place of the hardware-facing headers.
        fixture = ROOT / "test/fixtures/management"
        for name in ("ManagementReporter.cpp", "ManagementReporter.h", "FileRead.h", "ContactFileTransaction.h", "PersistentStoreFormat.h"):
            shutil.copyfile(ROOT / "src/helpers" / name, Path(cls.work.name) / name)
        for name in ("CommonCLI.h", "Mesh.h"):
            shutil.copyfile(fixture / name, Path(cls.work.name) / name)
        shutil.copyfile(ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h", Path(cls.work.name) / "IdentityStore.h")
        runtime = Path(cls.work.name) / "runtime-test"
        runtime_cmd = [shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g"] + host_define + ["-DRP2040_PLATFORM",
                       *sanitizers, "-I", cls.work.name,
                       "-I", str(cls.crypto), "-I", str(ROOT / "src/helpers"), "-I", str(ROOT / "src"),
                       str(Path(cls.work.name) / "ManagementReporter.cpp"), str(ROOT / "src/helpers/ManagementReport.cpp"),
                       str(fixture / "runtime_test.cpp")]
        runtime_cmd += [str(cls.crypto / p) for p in sources] + ["-o", str(runtime)]
        subprocess.run(runtime_cmd, check=True, capture_output=True, text=True)
        cls.runtime = runtime

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_interoperable_full_report(self):
        decoded = report.decode_report(list(reversed(self.pages)), "management test password")
        self.assertEqual(decoded["sequence"], 42)
        self.assertEqual(len(decoded["acl"]), 36)
        self.assertEqual(decoded["firmware_version"], "1.17.1.5")
        self.assertNotIn("manifest_id", decoded)

    def test_runtime_storage_scheduling_rollover_and_failures(self):
        subprocess.run([str(self.runtime)], check=True)

    def test_tampering_and_wrong_password(self):
        for position in (4, 20, 28, 74, 76, 83, -1):
            changed = bytearray(self.pages[0]); changed[position] ^= 1
            with self.subTest(position=position), self.assertRaises(ValueError):
                report.decode_report([bytes(changed)] + self.pages[1:], "management test password")
        with self.assertRaises(ValueError):
            report.decode_report(self.pages, "incorrect management password")

    def test_page_omission_duplicate_mixed(self):
        for pages in ([], self.pages[:-1], self.pages[1:] + [self.pages[1]], self.pages + [self.pages[0]]):
            with self.assertRaises(ValueError):
                report.decode_report(pages, "management test password")

    def test_fingerprints_and_password_length(self):
        a = report.fingerprint("management test password", bytes(16), bytes(32))
        b = report.fingerprint("management test password", bytes([1]) + bytes(15), bytes(32))
        self.assertEqual(len(a), 12); self.assertNotEqual(a, b)
        for password in ("short", "x" * 97):
            with self.assertRaises(ValueError): report.password_key(password)

    def test_companions_excluded(self):
        for role in ("simple_repeater/MyMesh", "simple_room_server/MyMesh", "simple_sensor/SensorMesh"):
            self.assertIn("_cli.beginManagement(*this, _fs)", (ROOT / "examples" / (role + ".cpp")).read_text())
        for path in (ROOT / "examples/companion_radio").glob("*.*"):
            if path.suffix in (".cpp", ".h"): self.assertNotIn("beginManagement", path.read_text())

    def test_mqtt_paths_scopes_and_duplicate_uplinks(self):
        messages = []
        for route in range(4):
            for page in self.pages:
                wire = bytes([0x3c | route]) + (bytes(4) if route in (0, 3) else b"")
                wire += bytes([0x42]) + bytes.fromhex("1234abcd") + page
                message = dict(type="PACKET", direction="rx", raw=wire.hex())
                self.assertEqual(report.mqtt_payload(message), page)
                messages.extend([message, message])
        decoded = report.mqtt_reports(messages, "management test password")
        self.assertEqual(len(decoded), 1)
        self.assertEqual(len(decoded[0]["acl"]), 36)
        self.assertIsNone(report.mqtt_payload(dict(type="PACKET", direction="tx", raw=wire.hex())))
        self.assertIsNone(report.mqtt_payload(dict(type="PACKET", direction="rx", raw="3dc0")))
        self.assertEqual(report.mqtt_reports(messages[:2], "management test password"), [])
        for page in self.pages:
            padded = page + bytes(3 + ((len(page) - 3 + 15) // 16) * 16 - len(page))
            message = dict(type="PACKET", direction="rx", raw=(b"\x19\x00" + padded).hex())
            self.assertEqual(report.mqtt_payload(message), page)
            message["raw"] = (b"\x19\x00" + padded[:-1] + b"\x01").hex()
            self.assertIsNone(report.mqtt_payload(message))


if __name__ == "__main__": unittest.main()
