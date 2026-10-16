"""Link real Companion hook declarations against their production providers."""
from itertools import product
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_ota_kiss_direct import dispatcher_harness
from test_radio_receive_contract import method
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]


def preprocessor_block(source, guard):
    start = source.index(guard)
    depth = 0
    for line in source[start:].splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("#if ", "#ifdef ", "#ifndef ")):
            depth += 1
        elif stripped == "#endif":
            depth -= 1
        start += len(line)
        if depth == 0:
            return source[source.index(guard):start]
    raise AssertionError("unterminated production preprocessor block")


def guarded_method(source, signature):
    # Retain the source translation unit's surrounding eligibility conditions,
    # including Ethernet's !CHAT fallback. Method bodies are never rewritten.
    stack = []
    for line in source[:source.index(signature)].splitlines():
        text = line.strip()
        if text.startswith("#if "):
            stack.append(text[4:])
        elif text.startswith("#ifdef "):
            stack.append("defined(" + text[7:] + ")")
        elif text.startswith("#ifndef "):
            stack.append("!defined(" + text[8:] + ")")
        elif text == "#else":
            stack[-1] = "!(" + stack[-1] + ")"
        elif text == "#endif":
            stack.pop()
        elif text.startswith("#elif "):
            raise AssertionError("extend guard extraction before testing an elif provider")
    return "\n".join([*("#if " + item for item in stack), method(source, signature),
                      *("#endif" for _ in stack)])


def fixture(declarations=None):
    header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
    declarations = declarations or preprocessor_block(header, "#if MESH_ENABLE_FOREIGN_RADIO\n")
    mesh_h = (ROOT / "src/Mesh.h").read_text()
    mesh_cpp = (ROOT / "src/Mesh.cpp").read_text()
    direct = preprocessor_block(mesh_h, "#if MESH_ENABLE_OTA_KISS_DIRECT\n  bool allowDirectOtaTransmit")
    direct_methods = "\n".join(guarded_method(mesh_cpp, signature) for signature in (
        "uint32_t Mesh::directOtaEpoch(", "bool Mesh::allowDirectOtaTransmit(",
        "bool Mesh::onDirectOtaRadioFrame("))
    mt = (ROOT / "examples/companion_radio/MeshtextCompanion.cpp").read_text()
    eth = (ROOT / "examples/companion_radio/EthernetCompanion.cpp").read_text()
    callbacks = "\n".join(guarded_method(source, signature) for source, signature in (
        (mt, "bool MyMesh::onForeignRadioFrame("), (mt, "void MyMesh::onForeignRadioSendComplete("),
        (eth, "bool MyMesh::onEthernetFrame("), (eth, "bool MyMesh::onForeignRadioFrame("),
        (eth, "void MyMesh::onForeignRadioSendComplete(")))
    radio = dispatcher_harness().split("class TestDispatcher", 1)[0]
    radio = radio.replace("  bool supportsMeshtext() const override { return supported; }",
                          "  bool supportsMeshtext() const override { return supported; }\n"
                          "  bool supportsEthernetTunnel() const override { return supported; }")
    text = (ROOT / "test/fixtures/companion_foreign_hooks.cpp").read_text()
    for token, value in (("@RADIO@", radio), ("@COMPANION_DECLARATIONS@", declarations),
                         ("@DIRECT_DECLARATIONS@", direct), ("@DIRECT_METHODS@", direct_methods),
                         ("@CALLBACK_METHODS@", callbacks)):
        text = text.replace(token, value)
    return text


class CompanionForeignHookTests(unittest.TestCase):
    def compile_case(self, code, chat, tunnel, direct, expect_link=True):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-foreign-hooks-") as folder:
            binary = Path(folder) / "hooks"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", *SANITIZER_FLAGS,
                "-DENABLE_OTA=1", f"-DMESH_ENABLE_MESHTEXT_CHAT={chat}",
                f"-DMESH_ENABLE_ETHERNET_TUNNEL={tunnel}", f"-DMESH_ENABLE_OTA_KISS_DIRECT={direct}",
                f"-I{ROOT / 'test/mocks'}", f"-I{ROOT / 'src'}",
                *(str(ROOT / name) for name in ("src/Dispatcher.cpp", "src/Packet.cpp",
                                                "src/helpers/StaticPoolPacketManager.cpp")),
                "-x", "c++", "-", "-o", str(binary),
            ], input=code, text=True, capture_output=True, timeout=60)
            if not expect_link:
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr.lower(), r"undefined reference|undefined symbols")
                self.assertIn("MyMesh::onForeignRadioFrame", result.stderr)
                self.assertIn("MyMesh::onForeignRadioSendComplete", result.stderr)
                return
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: Companion foreign-hook feature matrix", result.stdout)

    def test_all_application_and_direct_feature_combinations_link_and_dispatch(self):
        code = fixture()
        for chat, tunnel, direct in product((0, 1), repeat=3):
            with self.subTest(chat=chat, tunnel=tunnel, direct=direct):
                self.compile_case(code, chat, tunnel, direct)

    def test_direct_only_broadened_callback_guard_fails_real_link(self):
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        declarations = preprocessor_block(header, "#if MESH_ENABLE_FOREIGN_RADIO\n")
        guard = "#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL\n"
        self.assertEqual(declarations.count(guard), 1)
        broken = declarations.replace(guard, "#if MESH_ENABLE_FOREIGN_RADIO\n", 1)
        self.compile_case(fixture(broken), 0, 0, 1, expect_link=False)


if __name__ == "__main__":
    unittest.main()
