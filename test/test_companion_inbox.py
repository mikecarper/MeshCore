"""Exercise the real message UI and queue together, including app downloads."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_message_navigation import PREAMBLE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

UI_MEMBERS = r'''
  int _msgcount = 0;
  bool connected = false, pairing = false, _deferred_msg_preview = false;
  int getMsgCount() const { return _msgcount; }
  bool hasConnection() const { return connected; }
  bool isPairingScreenActive() const { return pairing; }
  bool shouldWakeDisplayForMessage() const { return !connected; }
  void setCurrScreen(UIScreen* screen) { curr = screen; }
  void newMsg(uint8_t, const char*, const char*, int, int, const char*, int);
  void msgRead(int);
  void syncMessageQueue(int, int = -1);
  int getPreviewCount() const;
  const char* inboxTitle() const;
  const char* client = "USB";
  const char* connectedClientLabel() const { return connected ? client : nullptr; }
  bool hasBluetoothConnection() const { return connected && std::string(client) == "BLUETOOTH"; }
  bool isBluetoothEnabled() const { return true; }
  bool isPairingPromptActive() const { return pairing; }
'''

QUEUE = r'''
#define DISPLAY_CLASS TestDisplay
#define MAX_FRAME_SIZE 32
class MyMesh {
  struct Frame {
    int len;
    uint8_t buf[MAX_FRAME_SIZE];
    bool isChannelMsg() const { return buf[0] == 17; }
  } frames[4];
public:
  UITask* _ui;
  int offline_queue_len = 0, offline_queue_head = 0;
  explicit MyMesh(UITask* ui) : _ui(ui) {}
  int getOfflineQueueCapacity() const { return 4; }
  Frame& offlineQueueFrameAt(int index) { return frames[(offline_queue_head + index) % 4]; }
  bool addToOfflineQueue(const uint8_t*, int);
  int getFromOfflineQueue(uint8_t*);
  bool receive(const char* text, uint8_t kind = 17) {
    uint8_t bytes[2] = {kind, uint8_t(text[0])};
    bool queued = addToOfflineQueue(bytes, sizeof(bytes));
    _ui->newMsg(1, "Alice", text, offline_queue_len, 0, "Public",
                queued ? offline_queue_len - 1 : -1);
    return queued;
  }
  void download() { uint8_t frame[MAX_FRAME_SIZE]; getFromOfflineQueue(frame); }
};
'''

SCENARIOS = r'''
using namespace mesh::ui;
int main() {
  resetArduinoMock();
  displayPowerPrefs().battery.mode = DisplayMode::On;
  Display display;
  display.servicePower(false);
  Screen home;
  UITask task(display);
  MsgPreviewScreen messages(&task);
  task.home = task.curr = &home;
  task.msg_preview = &messages;
  MyMesh mesh(&task);

  // Reproduce issue #5: twelve messages arrive and the connected app fetches
  // every one. History retains twelve, while Pending returns to zero.
  task.connected = true;
  for (int i = 0; i < 12; ++i) {
    mesh.receive("received by phone");
    assert(task.curr == &home);
    assert(task.getMsgCount() == 1);
    mesh.download();
    assert(task.curr == &home && task.getMsgCount() == 0);
    assert(task.getPreviewCount() == i + 1);
  }
  assert(std::string(task.inboxTitle()) == "HISTORY");
  displayPowerPrefs().inbox = DisplayInboxMode::Pending;
  assert(task.getPreviewCount() == 0 && !messages.hasMessages());
  assert(std::string(task.inboxTitle()) == "INBOX");
  displayPowerPrefs().inbox = DisplayInboxMode::Unread;
  assert(task.getPreviewCount() == 12); // App delivery is not a local read.
  assert(std::string(task.inboxTitle()) == "UNREAD");
  task.curr = &messages;
  display.turnOff();
  messages.render(display);
  assert(task.getPreviewCount() == 12); // A dark display cannot mark it read.
  display.turnOn();
  messages.render(display);
  assert(task.getPreviewCount() == 11);
  messages.render(display);
  assert(task.getPreviewCount() == 11); // Redraws do not consume another item.

  // A connected client's new message must not replace the text being browsed.
  displayPowerPrefs().inbox = DisplayInboxMode::History;
  mesh.receive("new while browsing");
  display.clear(); messages.render(display);
  assert(display.contains("received by phone"));
  mesh.download();
  assert(task.curr == &messages && task.getPreviewCount() == 13);

  // Without a client, select the new arrival. Reading it does not drain the
  // delivery queue; it remains available to the next phone connection.
  task.connected = false;
  task.curr = &home;
  mesh.receive("offline arrival");
  assert(task.curr == &messages && task.getMsgCount() == 1);
  display.clear(); messages.render(display);
  assert(display.contains("offline arrival"));
  assert(mesh.offline_queue_len == 1);
  mesh.download();

  // Non-text frames occupy queue positions too. Full queues can evict a
  // channel message in the middle; Pending must hide only that exact entry.
  displayPowerPrefs().inbox = DisplayInboxMode::Pending;
  const uint8_t other[1] = {99};
  assert(mesh.addToOfflineQueue(other, 1));
  assert(task.getMsgCount() == 1 && !messages.hasMessages());
  mesh.receive("evicted channel");
  mesh.receive("retained channel");
  assert(mesh.addToOfflineQueue(other, 1));
  mesh.receive("replacement channel");
  assert(task.getMsgCount() == 4);
  assert(messages.messageCount() == 4); // Includes the two non-text frames.
  display.clear(); messages.render(display);
  assert(display.contains("replacement channel"));
  messages.handleInput(KEY_NEXT);
  display.clear(); messages.render(display);
  assert(display.contains("retained channel"));
  messages.handleInput(KEY_NEXT);
  assert(task.curr == &home); // Evicted entry was excluded.
  mesh.download(); // Non-text head, with no matching history entry.
  assert(task.getMsgCount() == 3);
  mesh.download(); // Retained channel.
  mesh.download(); // Other non-text frame.
  assert(task.getMsgCount() == 1 && messages.hasMessages());
  mesh.download();
  assert(task.getMsgCount() == 0 && !messages.hasMessages());

  // Rejecting a fifth direct message must not assign it another frame's slot.
  for (int i = 0; i < 4; ++i) assert(mesh.addToOfflineQueue(other, 1));
  assert(!mesh.receive("not queued", 7));
  assert(!messages.hasMessages());
  while (mesh.offline_queue_len) mesh.download();
  assert(!messages.hasMessages());

  displayPowerPrefs().inbox = DisplayInboxMode::History;
  for (int i = 0; i < 40; ++i) { mesh.receive("ring wrap"); mesh.download(); }
  assert(task.getPreviewCount() == 32 && task.getMsgCount() == 0);
  displayPowerPrefs().inbox = DisplayInboxMode::Pending;
  assert(!messages.hasMessages());
}
'''


class CompanionInboxTest(unittest.TestCase):
    def test_labels_use_enabled_connected_interfaces(self):
        abstract = (ROOT / "examples/companion_radio/AbstractUITask.h").read_text()
        label = extract_braced(abstract, "const char* connectedClientLabel() const")
        source = r'''
#include <helpers/MultiSerialInterface.h>
#include <cassert>
#include <string>
struct Link : BaseSerialInterface {
  bool enabled = false, connected = false;
  void enable() override { enabled = true; }
  void disable() override { enabled = false; }
  bool isEnabled() const override { return enabled; }
  bool isConnected() const override { return connected; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t*, size_t len) override { return len; }
  size_t checkRecvFrame(uint8_t*) override { return 0; }
};
struct UI {
  MultiSerialInterface* _interfaceManager;
  bool hasBluetoothConnection() const { return _interfaceManager->isBluetoothConnected(); }
'''+ label + r'''
};
int main() {
  MultiSerialInterface interfaces;
  Link usb, ble, wifi;
  interfaces.addInterface(InterfaceType::USB, &usb);
  interfaces.addInterface(InterfaceType::Bluetooth, &ble);
  interfaces.addInterface(InterfaceType::WiFi, &wifi);
  UI ui{&interfaces};
  interfaces.enable();
  assert(ui.connectedClientLabel() == nullptr);
  usb.connected = true;
  assert(std::string(ui.connectedClientLabel()) == "USB");
  ble.connected = true;
  assert(std::string(ui.connectedClientLabel()) == "BLE + USB");
  usb.disable();
  assert(std::string(ui.connectedClientLabel()) == "BLUETOOTH");
  ble.connected = false;
  wifi.connected = true;
  assert(std::string(ui.connectedClientLabel()) == "TCP");
  interfaces.disable();
  assert(ui.connectedClientLabel() == nullptr);
}
'''
        self.compile_and_run(source)

    def test_home_history_pending_and_connections_fit(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        first_page = extract_braced(source, "if (_page == HomePage::FIRST)")
        first_page = first_page.rsplit("#if UI_MESSAGES_HOME_PAGE == 1", 1)[0] + "}"
        first_page = first_page.replace("if (_page == HomePage::FIRST)", "")
        preamble = PREAMBLE.replace("int getMsgCount() const { return 0; }", UI_MEMBERS)
        preamble = preamble.replace("bool getChannel(int channel", "uint32_t getBLEPin() const { return 123456; }\n  bool getChannel(int channel")
        implementation = r'''
#include <helpers/ui/CompanionHomeLayout.h>
#include <helpers/ui/BluetoothPairingUiPolicy.h>
#define PRESS_LABEL "Press"
int UITask::getPreviewCount() const { return 12; }
const char* UITask::inboxTitle() const { return "HISTORY"; }
void renderHome(DisplayDriver& display, UITask* _task) {
  char tmp[80];
  const int body_top = 14;
'''+ first_page + r'''
}
struct MeasuredDisplay : Display {
  int size = 1;
  MeasuredDisplay(int w, int h) { setDimensions(w, h); }
  void setTextSize(int value) override { size = value; }
  uint16_t getTextWidth(const char* text) override { return strlen(text) * 6 * size; }
  void print(const char* text) override {
    assert(y >= 0 && y + 8 * size <= height());
    for (const auto& line : lines) {
      // Home text owns distinct horizontal bands; every text run is centered.
      assert(y >= line.y + line.color || y + 8 * size <= line.y);
    }
    Display::print(text);
    lines.back().color = 8 * size; // Save glyph height for overlap detection.
  }
};
int main() {
  for (const auto dims : {std::pair<int,int>{240,135}, {135,240}, {128,64}, {160,80}}) {
    for (const char* client : {"USB", "BLUETOOTH", "BLE + USB", "TCP"}) {
      MeasuredDisplay display(dims.first, dims.second);
      UITask task(display);
      task._msgcount = 0; task.connected = true; task.client = client;
      renderHome(display, &task);
      assert(display.contains("HISTORY: 12"));
      assert(display.contains("Pending: 0"));
      assert(display.contains("CONNECTED"));
      if (dims.second >= 135) assert(display.contains(client));
    }
  }
}
'''
        self.compile_and_run(preamble + implementation)

    def compile_and_run(self, source):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="mesh-inbox-test-") as directory:
            executable = Path(directory) / "inbox.exe"
            result = subprocess.run([
                compiler, "-std=c++17", "-DCOMPANION_FEATURE_JOHN=0",
                "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                "-x", "c++", "-", str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                str(ROOT / "src/helpers/ui/DisplayDriver.cpp"), "-o", str(executable),
            ], input=source, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_ui_and_delivery_queue(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        preamble = PREAMBLE.replace("int getMsgCount() const { return 0; }", UI_MEMBERS)
        implementation = extract_braced(ui, "class MsgPreviewScreen :") + ";\n"
        for signature in ("void UITask::newMsg(", "void UITask::msgRead(",
                          "void UITask::syncMessageQueue(", "int UITask::getPreviewCount(",
                          "const char* UITask::inboxTitle("):
            implementation += extract_braced(ui, signature) + "\n"
        implementation += QUEUE
        for signature in ("bool MyMesh::addToOfflineQueue(", "int MyMesh::getFromOfflineQueue("):
            implementation += extract_braced(mesh, signature) + "\n"
        self.compile_and_run(preamble + implementation + SCENARIOS)


if __name__ == "__main__":
    unittest.main()
