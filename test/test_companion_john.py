#!/usr/bin/env python3
"""Compare the real firmware decoder and CLI output against every source verse."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib

from test_message_navigation import PREAMBLE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/companion_john"
spec = importlib.util.spec_from_file_location("pack_john", ROOT / "tools/bible/pack_john.py")
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class CompanionJohnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(packer.SOURCE.read_text(encoding="utf-8"))
        # Independent expected typography, not the packer's conversion helper.
        typography = str.maketrans({"\u00a0": " ", "\u2013": "-", "\u2014": "-",
                                   "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
        cls.expected_verses = {ref: text.translate(typography)
                               for ref, text in cls.document["verses"].items()}

    def test_generated_header_is_current(self):
        self.assertEqual(packer.HEADER.read_text(encoding="utf-8"), packer.render(self.document))

    def test_block_sizes_roundtrip_all_verses(self):
        for size in (1024, 2048, 4096):
            packed, blocks = packer.pack(self.document, size)
            actual = []
            for offset, length, plain_length, first in blocks:
                self.assertEqual(first, len(actual))
                plain = zlib.decompress(packed[offset:offset + length], wbits=-15)
                self.assertEqual(len(plain), plain_length)
                self.assertLessEqual(len(plain), size)
                self.assertEqual(plain[-1], 0)
                actual.extend(part.decode("ascii") for part in plain[:-1].split(b"\0"))
            self.assertEqual(actual, list(self.expected_verses.values()))

    def test_ascii_typography_preserves_words_case_and_pinned_source(self):
        original = copy.deepcopy(self.document)
        sample = '\u201cJohn\u2019s\u00a0Word\u201d\u2014\u2018Test\u2019\u2013Yes!'
        self.assertEqual(packer.ascii_verse(sample, 64), b'"John\'s Word"-\'Test\'-Yes!')
        self.assertEqual(packer.ascii_verse('Already ASCII: John 3:16.', 64),
                         b'Already ASCII: John 3:16.')
        # Bounds apply after conversion, not to the original multibyte form.
        self.assertEqual(packer.ascii_verse('\u201cHi\u201d', 4), b'"Hi"')
        with self.assertRaises(ValueError):
            packer.ascii_verse('\u201cHi\u201d', 3)
        packer.pack(self.document)
        self.assertEqual(self.document, original)

    def test_ascii_data_size(self):
        packed, blocks = packer.pack(self.document)
        self.assertEqual(sum(block[2] for block in blocks), 96568)
        self.assertEqual(len(blocks), 49)
        self.assertEqual(len(packed) + 12 * len(blocks), 43891)

    def test_invalid_sources_are_rejected(self):
        for bad_text in ("", "leading\ncommand", "bad\x1b[2J", "bad\0text",
                         "bad\u202eRTL", " too much space", "a" * 2048,
                         "caf\u00e9", "unmapped\u2026punctuation", "bad\U0001f600text"):
            document = copy.deepcopy(self.document)
            document["verses"]["1:1"] = bad_text
            with self.assertRaises(ValueError):
                packer.pack(document)
        for key in ("1:1", "21:25"):
            document = copy.deepcopy(self.document)
            del document["verses"][key]
            with self.assertRaises(ValueError):
                packer.pack(document)

    def test_firmware_decoder_and_terminal_on_both_platform_profiles(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        with tempfile.TemporaryDirectory(prefix="meshcore-john-") as directory:
            directory = Path(directory)
            for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
                flags = [f"-D{platform}=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
                obj, binary = directory / "tinf.o", directory / "john.exe"
                self.run_checked([cc, "-std=c11", "-Os", *flags, "-c",
                                  str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(obj)])
                self.run_checked([cxx, "-std=c++17", "-Os", "-Wall", "-Wextra", "-Werror",
                                  *flags, "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                  str(FIXTURE / "test_john.cpp"),
                                  str(ROOT / "src/helpers/CompanionJohn.cpp"), str(obj), "-o", str(binary)])
                result = self.run_checked([str(binary), "--dump"])
                actual = dict(line.split("\t", 1) for line in result.splitlines())
                self.assertEqual(actual, self.expected_verses)

    def test_other_profiles_exclude_corpus(self):
        cxx = shutil.which("g++")
        if not cxx:
            self.skipTest("host G++ required")
        for flags in ([], ["-DESP32_PLATFORM=1"], ["-DNRF52_PLATFORM=1"],
                      ["-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1"],
                      ["-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                       "-DENABLE_USB_INTERFACE=1", "-DCOMPANION_FEATURE_JOHN=0"]):
            result = self.run_checked([cxx, "-E", "-P", *flags,
                                      str(ROOT / "src/helpers/CompanionJohn.cpp")])
            self.assertNotIn("johnData", result)
            self.assertNotIn("handleJohnCommand", result)

    def test_terminal_dispatch_is_separate_from_bounded_api_replies(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        start = source.index("void MyMesh::handleTerminalCommand(")
        end = source.index("\nvoid MyMesh::enterCLIRescue()", start)
        terminal = source[start:end]
        self.assertEqual(source.count("mesh::handleJohnCommand("), 1)
        self.assertLess(terminal.index("mesh::handleJohnCommand("),
                        terminal.index("char local_reply[160]"))
        self.assertIn("get John <chapter>:<verse> (World English Bible, offline)", terminal)
        self.assertEqual(terminal.count("#if COMPANION_FEATURE_JOHN"), 2)

    def test_reader_navigation_and_bookmark_recovery(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        with tempfile.TemporaryDirectory(prefix="meshcore-john-reader-") as directory:
            directory = Path(directory)
            flags = ["-DNRF52_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
            obj, binary = directory / "tinf.o", directory / "reader.exe"
            self.run_checked([cc, "-Os", *flags, "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                              "-o", str(obj)])
            for small_font in (0, 1):
                for button_hint, touch_bar in ((0, 0), (1, 0), (1, 1)):
                    self.run_checked([cxx, "-std=c++11", "-Os", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                                      *flags, f"-DUI_SMALL_MESSAGE_FONT={small_font}",
                                      f"-DUI_BUTTON_READER_HINT={button_hint}",
                                      f"-DUI_READER_TOUCH_BAR={touch_bar}",
                                      "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                      str(FIXTURE / "test_reader.cpp"), str(ROOT / "src/helpers/CompanionJohn.cpp"),
                                      str(obj), "-o", str(binary)])
                    self.run_checked([str(binary)])

    def test_reader_button_routing_and_profile_gate(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(encoding="utf-8")
        long_press = ui[ui.index("char UITask::handleLongPress("):]
        self.assertLess(long_press.index("isJohnReaderActive()"), long_press.index("enterCLIRescue()"))
        self.assertLess(long_press.index("isRadioPage()"), long_press.index("enterCLIRescue()"))
        self.assertIn("#if COMPANION_FEATURE_JOHN\n#include \"JohnReaderScreen.h\"", ui)
        self.assertLess(ui.index("#define UI_BUTTON_READER_HINT 1"),
                        ui.index('#include "JohnReaderScreen.h"'))
        self.assertIn("else if (isJohnReaderActive())", ui)
        self.assertIn("c = handleLongPress(KEY_ENTER);", ui)
        self.assertIn("handleDoubleClick(KEY_PREV)", ui)

    def test_reader_bookmark_follows_display_power_transitions(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(encoding="utf-8")
        stubs = r'''
struct Board {
  bool external = false, host = false;
  bool isExternalPowered() const { return external; }
  bool isUsbHostConnected() const { return host; }
};
struct Interfaces { bool takePairingRequest() { return false; } };
struct JohnReaderScreen : Screen { int saves = 0; void flush() { ++saves; } };
'''
        members = r'''
  Board* _board;
  Interfaces* _interfaceManager;
  uint32_t _pairing_screen_until = 0;
  bool hasConnection() const { return false; }
  bool isPairingScreenActive() const { return false; }
  void showPairingPin() {}
  void finishPairingScreen(bool) {}
  void servicePairingState();
'''
        source = PREAMBLE.replace("class UITask {", stubs + "class UITask {")
        source = source.replace("int getMsgCount() const { return 0; }", members)
        source += extract_braced(ui, "void UITask::servicePairingState(") + "\n"
        source += extract_braced(ui, "char UITask::checkDisplayOn(") + r'''
int main() {
  using namespace mesh::ui;
  resetArduinoMock();
  displayPowerPrefs().battery = {DisplayMode::Button, 5};
  displayPowerPrefs().usb = {DisplayMode::On, 5};
  Display display;
  Screen home;
  JohnReaderScreen reader;
  Board board;
  Interfaces interfaces;
  UITask task(display);
  task._board = &board; task._interfaceManager = &interfaces;
  task.curr = task.home = &home; task.john_reader = &reader;
  task.servicePairingState();
  assert(!display.isOn() && reader.saves == 0);
  task.curr = &reader;
  assert(task.checkDisplayOn(KEY_NEXT) == 0); // Wake consumes navigation.
  assert(display.isOn());
  g_mock_millis = 4999; task.servicePairingState();
  assert(display.isOn() && reader.saves == 0);
  g_mock_millis = 5000; task.servicePairingState();
  assert(!display.isOn());
  const int expected_saves = COMPANION_FEATURE_JOHN ? 1 : 0;
  assert(reader.saves == expected_saves);
  task.servicePairingState();
  assert(reader.saves == expected_saves); // No repeated writes while dark.
  for (bool* plugged : {&board.external, &board.host}) {
    *plugged = true; task.servicePairingState();
    assert(display.isOn());
    g_mock_millis += 10000; task.servicePairingState();
    assert(display.isOn()); // USB's always-on policy applies to the reader.
    int before = reader.saves;
    *plugged = false; task.servicePairingState();
    assert(!display.isOn() && reader.saves == before + expected_saves);
  }
  task.curr = &home;
  task.checkDisplayOn(KEY_NEXT);
  int before = reader.saves;
  g_mock_millis += 5000; task.servicePairingState();
  assert(!display.isOn() && reader.saves == before);
  task._display = nullptr; task.servicePairingState();
}
'''
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "host G++ required")
        with tempfile.TemporaryDirectory(prefix="mesh-reader-power-") as directory:
            directory = Path(directory)
            (directory / "power.cpp").write_text(source, encoding="utf-8")
            for enabled in (0, 1):
                with self.subTest(reader_enabled=enabled):
                    binary = directory / "power.exe"
                    self.run_checked([compiler, "-std=c++17", f"-DCOMPANION_FEATURE_JOHN={enabled}",
                                      "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                                      str(directory / "power.cpp"),
                                      str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                                      str(ROOT / "src/helpers/ui/DisplayDriver.cpp"), "-o", str(binary)])
                    self.run_checked([str(binary)])

    def run_checked(self, command):
        result = subprocess.run(command, capture_output=True, encoding="utf-8",
                                errors="backslashreplace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
