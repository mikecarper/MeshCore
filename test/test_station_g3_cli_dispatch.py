"""Execute CommonCLI get/set routing through the real G3 board and NVS adapter.

Only unrelated hardware setting branches are omitted. The production verb
normalizer, top-level get/set branches, shared FEM handlers and unknown tails
are extracted verbatim; board methods and checked NVS persistence run whole.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body
from test_replay_reset_integration import extract_braced
from test_station_g3_slot_settings import BOARD, NVS, VARIANT


ROOT = Path(__file__).resolve().parents[1]


def unknown_tail(method, *, legacy=False):
    final_else = method.rfind("\n  } else {")
    if final_else < 0:
        raise AssertionError("review the production unknown-setting branch")
    start = method.rfind("\n#if defined(STATION_G3_ESP32)", 0, final_else)
    if legacy or start < 0 or "_board->handleCommand(" not in method[start:final_else]:
        start = final_else
    return method[start:]


HARNESS = r'''
#include "nvs.h"
#include "StationG3Board.h"
#include <helpers/CLICommandUtils.h>
#include <cassert>
#include <cstdlib>
#include <string>
#undef assert
#define assert(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "fixture assertion line %u: %s\n", unsigned(__LINE__), #condition); \
  std::exit(91); } } while (false)
struct Board : StationG3Board {
  unsigned calls = 0;
  bool handleCommand(const char* command, uint32_t stamp, char* reply) {
    ++calls;
    return StationG3Board::handleCommand(command, stamp, reply);
  }
} board;
struct Prefs {
  uint8_t radio_fem_rxgain = 1, radio_fem_txgain = 0;
  bool dirty = false;
  bool isDirty() const { return dirty; }
} prefs;
struct Callbacks {
  bool tx = false, rx = false;
  unsigned calibrations = 0;
  const Callbacks* getProfileRadio() const { return this; }
  bool isMorseTxActive() const { return tx; }
  bool isMorseRxActive() const { return rx; }
  void recalibrateNoiseFloor() { ++calibrations; }
} callbacks;
@KEY_EQUALS@
struct CommonCLI {
  Board* _board = &board;
  Prefs* _prefs = &prefs;
  Callbacks* _callbacks = &callbacks;
  unsigned saves = 0;
  void savePrefs() { ++saves; prefs.dirty = false; }
  void handleSetCmd(uint32_t sender_timestamp, char* command, char* reply) {
    const char* config = &command[4];
    (void)sender_timestamp;
    @SET_BRANCHES_OPEN@
    @SET_TAIL@
  void handleGetCmd(uint32_t sender_timestamp, char* command, char* reply) {
    const char* config = &command[4];
    (void)sender_timestamp;
    @GET_BRANCHES_OPEN@
    @GET_TAIL@
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply) {
    mesh::cli::normalizeCommandVerb(command);
    @DISPATCH@
    else @TOP_BOARD@
    else { strcpy(reply, "Unknown command"); }
  }
};
static void fresh() {
  fake::reset(); fake::resetBoard(); board = Board(); prefs = Prefs(); callbacks = Callbacks();
  station_g3::slotState() = station_g3::SlotState();
  station_g3::loadSlotSettings();
  auto& state = station_g3::slotState();
  state.candidate = state.active = state.last = 2; state.detected = 3;
}
static std::string run(CommonCLI& cli, const char* input, uint32_t stamp = 0) {
  char command[160] = {}, reply[160] = {};
  snprintf(command, sizeof(command), "%s", input);
  cli.handleCommand(stamp, command, reply);
  return reply;
}
int main(int argc, char** argv) {
  assert(argc == 2); const std::string scenario = argv[1];
  if (scenario == "routing") {
    for (uint32_t stamp : {0U, 123456U}) {
      fresh(); CommonCLI cli;
      prefs.dirty = true;
      auto reply = run(cli, "GET radio.slot", stamp);
#if defined(STATION_G3_ESP32)
      assert(reply.find("> auto; active=2; detected=1,2") == 0 && "get must reach the real board");
      assert(cli.saves == 0 && prefs.dirty && fake::sets == 0);
      reply = run(cli, "SET radio.slot 1", stamp);
      assert(reply.find("OK - radio.slot 1 saved") == 0 && "set must reach the real board");
      auto& state = station_g3::slotState();
      assert(state.savedMode == 1 && state.bootMode == 0 && state.active == 2 && state.candidate == 2);
      assert(state.last == 2 && fake::disk.at("mode").value == 1 && cli.saves == 1);
      assert(fake::written.size() == 1 && fake::written.front() == "mode");
      reply = run(cli, "get radio.slot", stamp);
      assert(reply.find("reboot required") != std::string::npos && cli.saves == 1);
      assert(run(cli, "set radio.slot auto", stamp).find("OK") == 0);
      assert(run(cli, "set radio.slot 2", stamp).find("OK") == 0);
      state = station_g3::SlotState(); station_g3::loadSlotSettings();
      assert(state.bootMode == 2 && state.savedMode == 2 && state.active == 0);
#else
      assert(reply.find("unknown") != std::string::npos && board.calls == 0 && cli.saves == 0);
      assert(run(cli, "set radio.slot 1", stamp) == "unknown config");
      assert(fake::sets == 0 && board.calls == 0);
#endif
    }
  } else if (scenario == "failures") {
    fresh(); CommonCLI cli;
    for (const char* invalid : {"0", "3", "01", "2 extra", "AUTO", ""}) {
      const auto reply = run(cli, (std::string("set radio.slot ") + invalid).c_str());
      assert(reply.find("Error") == 0 && fake::sets == 0);
    }
    for (const char* command : {"set radio.slotting 1", "get radio.slotting", "set nothing 1", "get nothing"}) {
      assert(run(cli, command).find("unknown") != std::string::npos && fake::sets == 0);
    }
    for (unsigned fault = 0; fault < 3; ++fault) {
      fresh();
      fake::openError = fault == 0 ? 9 : 0;
      fake::setError = fault == 1 ? 9 : 0;
      fake::commitError = fault == 2 ? 9 : 0;
      const auto reply = run(cli, "set radio.slot 1", 123);
      assert(reply.find("Error: radio.slot could not be saved") == 0);
      const auto& state = station_g3::slotState();
      assert(state.savedMode == 0 && state.bootMode == 0 && state.active == 2 && state.candidate == 2);
    }
  } else if (scenario == "shared") {
    fresh(); CommonCLI cli;
    for (unsigned morse = 0; morse < 2; ++morse) {
      callbacks.tx = morse == 0; callbacks.rx = morse == 1;
      for (const char* command : {"set radio.fem.rxgain on", "set radio.fem.txgain on"}) {
        const auto reply = run(cli, command);
        assert(reply.find("Error: stop Morse") == 0 && "shared FEM guard must precede the board hook");
        assert(board.calls == 0 && fake::femMutations == 0 && cli.saves == 0);
      }
    }
    callbacks.tx = callbacks.rx = false;
    assert(run(cli, "set radio.fem.rxgain off").find("OK") == 0);
    assert(prefs.radio_fem_rxgain == 0 && cli.saves == 1 && board.calls == 0);
    assert(run(cli, "get radio.fem.rxgain") == "> off" && board.calls == 0);
    assert(run(cli, "set radio.fem.txgain on").find("OK") == 0);
    assert(prefs.radio_fem_txgain == 1 && cli.saves == 2 && board.calls == 0);
    assert(run(cli, "get radio.fem.txgain") == "> on" && board.calls == 0);
    assert(fake::sets == 0);
  } else assert(false);
}
'''


class StationG3CliDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        setter = body(common, "void CommonCLI::handleSetCmd(")
        getter = body(common, "void CommonCLI::handleGetCmd(")
        top = body(common, "void CommonCLI::handleCommand(")
        dispatch = "\nelse ".join(extract_braced(top, signature) for signature in (
            'if (memcmp(command, "get ", 4) == 0)',
            'if (memcmp(command, "set ", 4) == 0)'))
        values = {
            "@KEY_EQUALS@": body(common, "static bool configKeyEquals("),
            "@SET_BRANCHES_OPEN@": "\nelse ".join(extract_braced(setter, signature) for signature in (
                'if (memcmp(config, "radio.fem.rxgain ", 17) == 0)',
                'if (memcmp(config, "radio.fem.txgain ", 17) == 0)'))[:-1],
            "@GET_BRANCHES_OPEN@": "\nelse ".join(extract_braced(getter, signature) for signature in (
                'if (configKeyEquals(config, "radio.fem.rxgain"))',
                'if (configKeyEquals(config, "radio.fem.txgain"))'))[:-1],
            "@DISPATCH@": dispatch,
            "@TOP_BOARD@": extract_braced(top, "if (_board->handleCommand("),
        }
        cls.work = tempfile.TemporaryDirectory(prefix="g3-cli-dispatch-")
        work = Path(cls.work.name)
        header = (VARIANT / "StationG3Board.h").read_text()
        board_header = BOARD.replace("@BOOT@", body(header, "void begin()"))
        board_header = board_header.replace("@IRQ@", body(header, "uint32_t getIRQGpio()"))
        (work / "StationG3Board.h").write_text(board_header)
        (work / "StationG3RadioSlot.h").write_text((VARIANT / "StationG3RadioSlot.h").read_text())
        (work / "nvs.h").write_text(NVS)
        (work / "helpers").mkdir()
        (work / "helpers/ESP32GpioHold.h").write_text("#pragma once\n")
        (work / "settings.cpp").write_text((VARIANT / "StationG3RadioSlot.cpp").read_text())
        (work / "board.cpp").write_text((VARIANT / "StationG3Board.cpp").read_text())
        cls.binaries = {}
        for variant in ("production", "old-get", "old-set", "early-board", "other-board"):
            code = HARNESS
            for token, value in values.items():
                code = code.replace(token, value)
            code = code.replace("@SET_TAIL@", unknown_tail(setter, legacy=variant == "old-set"))
            code = code.replace("@GET_TAIL@", unknown_tail(getter, legacy=variant == "old-get"))
            if variant == "early-board":
                code = code.replace(dispatch, 'if (_board->handleCommand(command, sender_timestamp, reply)) {} else ' + dispatch)
            source, binary = work / (variant + ".cpp"), work / variant
            source.write_text(code)
            sanitize = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                        if sys.platform.startswith("linux") else [])
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitize,
                *([] if variant == "other-board" else ["-DSTATION_G3_ESP32=1"]),
                "-I" + str(work), "-I" + str(ROOT / "src"), str(work / "settings.cpp"),
                str(work / "board.cpp"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if built.returncode:
                cls.work.cleanup()
                raise AssertionError(built.stderr)
            cls.binaries[variant] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, case, variant="production", failure=None):
        result = subprocess.run([str(self.binaries[variant]), case], capture_output=True, text=True, timeout=15)
        if failure:
            self.assertEqual(result.returncode, 91, result.stdout + result.stderr)
            self.assertIn(failure, result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_local_and_remote_get_set_reach_checked_board_persistence(self):
        self.run_case("routing")

    def test_invalid_values_unknown_keys_and_nvs_failures_keep_live_slot(self):
        self.run_case("failures")

    def test_shared_fem_ownership_and_preference_saving_take_priority(self):
        self.run_case("shared")

    def test_non_g3_boards_keep_the_previous_unknown_setting_path(self):
        self.run_case("routing", "other-board")

    def test_old_get_tail_cannot_reach_the_board(self):
        self.run_case("routing", "old-get", "get must reach the real board")

    def test_old_set_tail_cannot_reach_the_board(self):
        self.run_case("routing", "old-set", "set must reach the real board")

    def test_early_board_hook_would_bypass_shared_morse_guard(self):
        self.run_case("shared", "early-board", "shared FEM guard must precede the board hook")


if __name__ == "__main__":
    unittest.main()
