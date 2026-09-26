#!/usr/bin/env python3
"""Execute the Companion's real USB mOTA exit functions with both entry modes."""

from pathlib import Path
import subprocess
import tempfile
import unittest


MAIN = (Path(__file__).resolve().parents[1] / "examples/companion_radio/main.cpp").read_text()


def function(signature):
    start = MAIN.index(signature)
    end = MAIN.index("{", start) + 1
    depth = 1
    while depth:
        depth += (MAIN[end] == "{") - (MAIN[end] == "}")
        end += 1
    return MAIN[start:end]


HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
namespace mesh {
enum class UsbMotaEntryOrigin { BINARY, ASCII };
static void discardUsbTerminalOutput() {}
}
static bool usb_mota_mode = true, usb_mota_disconnect_armed = true;
static mesh::UsbMotaEntryOrigin usb_mota_entry_origin = mesh::UsbMotaEntryOrigin::BINARY;
static char usb_mota_line[32] = "old";
static size_t usb_mota_line_len = 3;
static bool ascii = false;
static int drained = 0, folder_off = 0;
static char control_reply[224];
struct SerialInterface {
  bool passthrough = true;
  void setPassthroughMode(bool enabled) { passthrough = enabled; }
} usb_serial_interface;
struct Mesh {
  bool handleLocalControlCommand(const char* command, char* reply, size_t size) {
    assert(strcmp(command, "ota folder off") == 0);
    ++folder_off;
    snprintf(reply, size, "OK folder off");
    return true;
  }
} the_mesh;
static void queueUsbTerminalControlReply(const char* reply) {
  snprintf(control_reply, sizeof(control_reply), "%s", reply);
}
static void drainUsbTerminalOutputBeforeProtocolSwitch() { ++drained; }
static void enterUsbTerminalMode(bool show_banner) {
  assert(!show_banner);
  ascii = true;
  usb_serial_interface.setPassthroughMode(true);
}
@FUNCTIONS@
static void prepare(mesh::UsbMotaEntryOrigin origin) {
  usb_mota_mode = usb_mota_disconnect_armed = true;
  usb_mota_entry_origin = origin;
  usb_mota_line_len = 3;
  strcpy(usb_mota_line, "old");
  usb_serial_interface.passthrough = true;
  ascii = false;
  drained = folder_off = 0;
  control_reply[0] = 0;
}
int main() {
  prepare(mesh::UsbMotaEntryOrigin::ASCII);
  leaveUsbMotaMode(true);
  assert(ascii && usb_serial_interface.passthrough);
  assert(!usb_mota_mode && !usb_mota_disconnect_armed && usb_mota_line_len == 0);
  assert(folder_off == 1 && drained == 1);
  assert(strstr(control_reply, "OK - Terminal mode") != nullptr);

  prepare(mesh::UsbMotaEntryOrigin::BINARY);
  leaveUsbMotaMode(true);
  assert(!ascii && !usb_serial_interface.passthrough);
  assert(folder_off == 1 && drained == 1);
  assert(strstr(control_reply, "OK - Binary mode") != nullptr);

  prepare(mesh::UsbMotaEntryOrigin::ASCII);
  leaveUsbMotaMode(false);  // Host reset owns the next protocol decision.
  assert(!ascii && !usb_serial_interface.passthrough);
  assert(folder_off == 1 && drained == 0 && control_reply[0] == 0);
}
'''


class UsbMotaExitTests(unittest.TestCase):
    def test_clean_text_sender_restores_text_terminal(self):
        source = HARNESS.replace(
            "@FUNCTIONS@",
            function("static void resetUsbMotaMode()") + "\n"
            + function("static void leaveUsbMotaMode(bool acknowledge)"),
        )
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / "test"
            cpp.write_text(source)
            subprocess.run(["c++", "-std=c++11", str(cpp), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
