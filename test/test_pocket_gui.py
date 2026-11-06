#!/usr/bin/env python3
"""Exercise MeshPocket GUI state and settings with the production headers."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/pocket_gui"


class PocketGuiTest(unittest.TestCase):
    def test_gui_is_only_enabled_for_pocket_full(self):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("host G++ required")
        source = "#include <helpers/ui/PocketGuiConfig.h>\nUI_POCKET_GUI\n"
        profiles = [
            ([], "0"),
            (["HELTEC_MESH_POCKET=1"], "0"),
            (["COMPANION_RADIO_FULL=1"], "0"),
            (["NRF52_PLATFORM=1", "RAK_4631=1", "COMPANION_RADIO_FULL=1"], "0"),
            (["ESP32_PLATFORM=1", "LILYGO_T_DECK=1", "COMPANION_RADIO_FULL=1"], "0"),
            (["HELTEC_MESH_POCKET=1", "COMPANION_RADIO_FULL=1"], "1"),
            (["HELTEC_MESH_POCKET=1", "COMPANION_RADIO_FULL=1", "UI_POCKET_GUI=0"], "0"),
        ]
        for flags, expected in profiles:
            with self.subTest(flags=flags):
                command = [compiler, "-E", "-P", "-x", "c++", "-",
                           "-I" + str(ROOT / "src"), *("-D" + flag for flag in flags)]
                result = subprocess.run(command, input=source, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        for flags in ([], ["HELTEC_MESH_POCKET=1"], ["COMPANION_RADIO_FULL=1"]):
            command = [compiler, "-E", "-P", "-x", "c++", "-", "-DUI_POCKET_GUI=1",
                       "-I" + str(ROOT / "src"), *("-D" + flag for flag in flags)]
            result = subprocess.run(command, input=source, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, "unsupported board accepted Pocket GUI")
            self.assertIn("requires MeshPocket Full Companion", result.stderr)

    def compile_and_run(self, source, extra_flags=()):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("host G++ required")
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-gui-") as directory:
            binary = Path(directory) / "gui-test"
            flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-g"]
            if os.name != "nt":
                flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                          "-fno-pie", "-no-pie"]
            command = [compiler, *flags, *extra_flags,
                       "-I" + str(FIXTURE), "-I" + str(ROOT / "src"), "-I" + str(ROOT),
                       str(source), "-o", str(binary)]
            for args in (command, [str(binary)]):
                result = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0,
                                 " ".join(args) + "\n" + result.stdout + result.stderr)

    def test_sent_messages_preserve_history_without_becoming_unread(self):
        self.compile_and_run(FIXTURE / "test_sent_history.cpp")

    def test_settings_integrity_and_message_board_state(self):
        self.compile_and_run(FIXTURE / "test_pocket_state.cpp")

    def test_native_board_layout_and_fractional_timezones(self):
        self.compile_and_run(FIXTURE / "test_pocket_render.cpp",
                             ["-DHELTEC_MESH_POCKET=1", "-DCOMPANION_RADIO_FULL=1"])

    def test_radio_page_hint_keeps_its_guard_when_reader_is_disabled(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        method = source.index("  bool isRadioPage() const {")
        start = source.rfind("#if ", 0, method)
        end = source.index("#endif", method) + len("#endif")
        # Retain the production preprocessor guard, unlike the navigation
        # fixture which extracts individual method bodies for its reader build.
        guarded_method = source[start:end]
        hint = next(line for line in source.splitlines()
                    if "else if (isRadioPage()) frame.hint" in line)
        fixture = """
#include <cassert>
#include <cstdint>
#include <cstring>
struct Screen {
@PAGES@;
  uint8_t _page = FIRST;
@GUARDED_METHOD@
#if UI_POCKET_GUI
  const char* hint() const {
    struct { const char* hint; } frame;
    if (false) frame.hint = "unused";
@HINT@
    else frame.hint = "other";
    return frame.hint;
  }
#endif
};
int main() {
#if UI_POCKET_GUI
  Screen screen;
  for (auto page : {Screen::RADIO, Screen::RADIO2}) {
    screen._page = page;
    assert(!strcmp(screen.hint(), "click next  2x back"));
  }
  screen._page = Screen::FIRST; assert(!strcmp(screen.hint(), "other"));
#endif
}
""".replace("@PAGES@", extract_braced(source, "enum HomePage {"))
        fixture = fixture.replace("@GUARDED_METHOD@", guarded_method).replace("@HINT@", hint)
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-reader-guard-") as directory:
            generated = Path(directory) / "hint.cpp"
            generated.write_text("#include <initializer_list>\n" + fixture)
            for pocket, reader in ((1, 0), (1, 1), (0, 1), (0, 0)):
                with self.subTest(pocket=pocket, reader=reader):
                    self.compile_and_run(generated, [f"-DUI_POCKET_GUI={pocket}",
                                                   f"-DCOMPANION_FEATURE_READER={reader}"])

    def test_pocket_controls_keep_network_reader_access(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        home = source[source.index("class HomeScreen :"):]
        navigation = home[home.index("  bool handleInput(char c) override {"):]
        navigation = navigation.split("#ifdef COMPANION_EXCLUSIVE_WIFI_BLE", 1)[0]
        implementation = "\n".join(extract_braced(home, signature) for signature in (
            "void resetRadioProfileDisplayPage() {", "bool isPageVisible(uint8_t page) const {",
            "void movePage(int direction) {", "void showFirstPage() {",
            "bool isRadioPage() const {"))
        implementation += "\n" + navigation + "return false; }\n"
        fixture = (FIXTURE / "test_pocket_controls.cpp").read_text()
        fixture = fixture.replace("// @HOME_IMPLEMENTATION@", implementation)
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-controls-") as directory:
            generated = Path(directory) / "controls.cpp"
            generated.write_text(fixture)
            for signedness in ("-fsigned-char", "-funsigned-char"):
                with self.subTest(signedness=signedness):
                    self.compile_and_run(generated, [signedness, "-DUI_POCKET_GUI=1",
                                         "-DCOMPANION_FEATURE_READER=1", "-DUI_NO_DISCOVER_SCREEN=1",
                                         "-Wno-unused-parameter"])

    def test_map_page_shows_world_without_locations_and_preserves_zoom(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        implementation = "\n".join(extract_braced(source, signature) for signature in (
            "static bool pocketLocation(",
            "__attribute__((noinline)) void renderPocketMap() {"))
        fixture = (FIXTURE / "test_pocket_map_binding.cpp").read_text()
        fixture = fixture.replace("// @MAP_IMPLEMENTATION@", implementation)
        gui = (ROOT / "examples/companion_radio/ui-new/PocketGui.h").read_text()
        fixture = fixture.replace("// @MAP_CHROME_IMPLEMENTATION@",
                                  extract_braced(gui, "void renderMapChrome("))
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-map-binding-") as directory:
            generated = Path(directory) / "map.cpp"
            generated.write_text(fixture)
            self.compile_and_run(generated)

    def test_received_and_sent_message_bindings_preserve_screen_policy(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void UITask::newMsg(", "void UITask::channelMessageSent(",
            "char UITask::handleMultiClick("))
        fixture = (FIXTURE / "test_pocket_bindings.cpp").read_text()
        fixture = fixture.replace("// @ADD_SENT@", extract_braced(source, "void addSent("))
        fixture = fixture.replace("// @TASK_METHODS@", methods)
        with tempfile.TemporaryDirectory(prefix="mesh-pocket-bindings-") as directory:
            generated = Path(directory) / "bindings.cpp"
            generated.write_text(fixture)
            self.compile_and_run(generated, ["-DUI_POCKET_GUI=1",
                                 "-DCOMPANION_FEATURE_READER=1", "-Wno-unused-parameter"])


if __name__ == "__main__":
    unittest.main()
