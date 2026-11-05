"""Exercise the production portal's bounded foreground activity tracking and WiFi coordinator leases."""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class WebConfigNetworkPowerTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void WebConfigServer::noteNetworkActivity(",
            "void WebConfigServer::requestNetworkAccess(",
            "bool WebConfigServer::isNetworkForegroundActive("))
        return r'''
#include <cassert>
#include <cstdint>
#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_MODE_NULL 0
#define WIFI_MODE_STA 1
#define ESP_OK 0
#include <helpers/WebConfigBatch.h>
#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/RoomWebMailbox.h>
uint32_t now = 1000;
uint32_t millis() { return now; }
struct WCLock { explicit WCLock(void*) {} };
struct WebConfigServer {
  enum { MODE_OFF, MODE_SETUP, MODE_CONNECTING, MODE_LAN };
  enum { BATCH_IDLE, BATCH_PENDING, BATCH_DONE };
  int _mode = MODE_LAN, _batch_state = BATCH_IDLE;
  bool _stopping = false, _setup_wifi_handoff_pending = false;
  uint32_t _last_activity = 1000, _network_activity_until = 0;
  unsigned _handler_refs = 0;
  void* _mux = nullptr;
  struct Terminal { bool running = false; struct { bool pending = false; } queue; };
  Terminal* _terminal = nullptr;
  mesh::RoomWebMailbox* _room_mailbox = nullptr;
  unsigned handlerRefCount() const { return _handler_refs; }
  void noteNetworkActivity();
  void requestNetworkAccess();
  bool isNetworkForegroundActive(uint32_t) const;
};
@METHODS@
int main() {
  WebConfigServer portal;
  auto& coord = mesh::wifi::coordinator();
  coord.reset();
  // Boot/connection timestamps and a listener alone do not mark foreground activity.
  portal._last_activity = now;
  assert(!portal.isNetworkForegroundActive(now));
  portal.requestNetworkAccess();
  now += 119999;
  assert(portal.isNetworkForegroundActive(now));
  ++now;
  assert(!portal.isNetworkForegroundActive(now));
  // The bounded lease works across millis() rollover.
  now = UINT32_MAX - 100;
  portal.noteNetworkActivity();
  now += 1000;
  assert(portal.isNetworkForegroundActive(now));
  now += 119000;
  assert(!portal.isNetworkForegroundActive(now));
  // Setup, teardown and in-flight handlers/batches survive lease expiry.
  portal._mode = WebConfigServer::MODE_SETUP;
  assert(portal.isNetworkForegroundActive(now));
  portal._mode = WebConfigServer::MODE_LAN;
  portal._handler_refs = 1;
  assert(portal.isNetworkForegroundActive(now));
  portal._handler_refs = 0; portal._batch_state = WebConfigServer::BATCH_PENDING;
  assert(portal.isNetworkForegroundActive(now));
  portal._batch_state = WebConfigServer::BATCH_DONE;
  assert(!portal.isNetworkForegroundActive(now));
  portal._setup_wifi_handoff_pending = true;
  assert(portal.isNetworkForegroundActive(now));
  portal._setup_wifi_handoff_pending = false;
  WebConfigServer::Terminal terminal;
  portal._terminal = &terminal; terminal.running = true;
  assert(portal.isNetworkForegroundActive(now));
  terminal.running = false; terminal.queue.pending = true;
  assert(portal.isNetworkForegroundActive(now));
  terminal.queue.pending = false;
  assert(!portal.isNetworkForegroundActive(now));
  mesh::RoomWebMailbox mailbox; uint8_t token[32] = {1}, digest[32] = {2};
  portal._room_mailbox = &mailbox;
  assert(mailbox.submit(token, 1, digest, "{}", 2, now)
      == mesh::RoomWebMailbox::Result::Accepted);
  assert(portal.isNetworkForegroundActive(now));
  assert(mailbox.begin() && portal.isNetworkForegroundActive(now));
  mailbox.finish(now);
  assert(!portal.isNetworkForegroundActive(now));
  portal._mode = WebConfigServer::MODE_OFF; portal._stopping = true;
  assert(portal.isNetworkForegroundActive(now));
  portal._stopping = false;
  assert(!portal.isNetworkForegroundActive(now));
  coord.reset();
}
'''.replace("@METHODS@", methods)

    def test_actual_activity_setup_and_inflight_work_hold_with_bounded_idle(self):
        self.compile_and_run(self.fixture())


if __name__ == "__main__":
    unittest.main()
