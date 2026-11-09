"""Check resident source copies, opt-in selection and pinned-source rejection."""
from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMAND = load("esp32_radio_command_code")
SPI = load("esp32_radio_spi_code")


class Node:
    def __init__(self, path):
        self.path = path

    def srcnode(self):
        return self

    def get_abspath(self):
        return str(self.path)


class Env(dict):
    def __init__(self, flag, build):
        super().__init__(CPPDEFINES=["ESP32_PLATFORM", (flag, 1)], CPPPATH=[])
        self.build = build

    def subst(self, value):
        assert value == "$BUILD_DIR"
        return str(self.build)

    def AppendUnique(self, **kwargs):
        self["CPPPATH"].extend(kwargs["CPPPATH"])

    def File(self, path):
        return Node(Path(path))


class RadioCommandCodeTests(unittest.TestCase):
    def test_only_attributes_are_changed(self):
        source = "// Preserve this comment.\nint16_t Module::command() { return checked(); }\n"
        for module in (COMMAND, SPI):
            with self.subTest(module=module.__name__), mock_patch.dict(module.SOURCES, {
                    "example.cpp": (hashlib.sha256(source.encode()).hexdigest(),
                                    [("int16_t Module::command(", 1)])}):
                output = module.transform(source, "example.cpp")
                self.assertEqual(output.removeprefix('#include <esp_attr.h>\n').replace("IRAM_ATTR ", ""), source)
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    module.transform(source + "// changed\n", "example.cpp")
                with mock_patch.dict(module.SOURCES, {
                        "example.cpp": (hashlib.sha256(source.encode()).hexdigest(),
                                        [("int16_t Missing::command(", 1)])}):
                    with self.assertRaisesRegex(RuntimeError, "signature changed"):
                        module.transform(source, "example.cpp")

    def test_esp32_opt_in_only(self):
        for module, flag in ((COMMAND, "MC_ESP32_RADIO_COMMAND_CODE"), (SPI, "MC_ESP32_RADIO_SPI_CODE")):
            with self.subTest(flag=flag):
                self.assertFalse(module.enabled({}))
                self.assertFalse(module.enabled({"CPPDEFINES": [(flag, 1), "NRF52_PLATFORM"]}))
                self.assertFalse(module.enabled({"CPPDEFINES": [(flag, 0), "ESP32_PLATFORM"]}))
                self.assertTrue(module.enabled({"CPPDEFINES": [(flag, 1), "ESP32_PLATFORM"]}))
                self.assertTrue(module.enabled({"CPPDEFINES": [flag, ("ESP32", 1)]}))

    def test_middleware_copies_without_mutating_dependency(self):
        for module, flag, path in ((COMMAND, "MC_ESP32_RADIO_COMMAND_CODE", "RadioLib/src/Module.cpp"),
                                  (SPI, "MC_ESP32_RADIO_SPI_CODE", "libraries/SPI/src/SPI.cpp")):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / path
                source.parent.mkdir(parents=True)
                body = "int16_t Example::command() { return checked(); }\n"
                source.write_text(body)
                key = "Module.cpp" if module is COMMAND else path
                with mock_patch.dict(module.SOURCES, {key: (hashlib.sha256(body.encode()).hexdigest(),
                                                           [("int16_t Example::command(", 1)])}):
                    env = Env(flag, root / "build")
                    original = Node(source)
                    copied = module.replace_source(env, original)
                    self.assertNotEqual(copied.path, source)
                    self.assertTrue(copied.path.is_relative_to(root / "build"))
                    self.assertIn("IRAM_ATTR", copied.path.read_text())
                    self.assertEqual(source.read_text(), body)
                    self.assertIn(str(source.parent), env["CPPPATH"])
                    env["CPPDEFINES"] = []
                    self.assertIs(module.replace_source(env, original), original)
                    env["CPPDEFINES"] = ["ESP32_PLATFORM", (flag, 1)]
                    unrelated = Node(root / "unrelated.cpp")
                    self.assertIs(module.replace_source(env, unrelated), unrelated)
                    source.write_text(body + "// upstream change\n")
                    with self.assertRaisesRegex(RuntimeError, "source changed"):
                        module.replace_source(env, original)


if __name__ == "__main__":
    unittest.main()
