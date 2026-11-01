"""Exercise the production portal's bounded foreground ownership policy."""
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
            "bool WebConfigServer::isNetworkForegroundActive(",
            "void WebConfigServer::refreshNetworkPowerHold("))
        return r'''
#include <cassert>
#include <cstdint>
#include <helpers/WebConfigBatch.h>
#include <helpers/WiFiNetworkPowerPolicy.h>
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
  void refreshNetworkPowerHold(uint32_t);
};
@METHODS@
int main() {
  WebConfigServer portal;
  auto& power = mesh::wifi::networkPower();
  power.setManaged(true); power.setSuspended(true);
  // Boot/connection timestamps and a listener alone never hold failed WiFi.
  portal._last_activity = now;
  assert(!portal.isNetworkForegroundActive(now));
  portal.refreshNetworkPowerHold(now);
  assert(!power.foregroundActive());
  portal.requestNetworkAccess();
  assert(power.consumeProbeRequest() && power.foregroundActive());
  now += 119999;
  assert(portal.isNetworkForegroundActive(now));
  ++now; portal.refreshNetworkPowerHold(now);
  assert(!portal.isNetworkForegroundActive(now) && !power.foregroundActive());
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
  portal.refreshNetworkPowerHold(now);
  assert(!power.foregroundActive());
  power.setManaged(false);
}
'''.replace("@METHODS@", methods)

    def test_actual_activity_setup_and_inflight_work_hold_with_bounded_idle(self):
        self.compile_and_run(self.fixture())

    def test_status_reports_shared_cooldown_before_connecting_or_off(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        formatter = extract_braced(source, "bool WebConfigServer::formatWiFiStatus(")
        block = extract_braced(formatter,
            "  if (mesh::wifi::networkPower().backgroundSuspended()")
        self.assertLess(formatter.index(block),
                        formatter.index("active->_mode == MODE_CONNECTING"))
        fixture = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <helpers/WiFiNetworkPowerPolicy.h>
void appendOtaSeederStatus(char* reply, size_t capacity, const char*) {
  size_t used = strlen(reply);
  snprintf(reply + used, capacity - used, "; seeder status");
}
bool format(char* reply, size_t reply_len, bool configured) {
  const char* ssid = "working-network";
@BLOCK@
  snprintf(reply, reply_len, "> connecting");
  return true;
}
int main() {
  char reply[160]; auto& power = mesh::wifi::networkPower();
  power.setManaged(true); power.setSuspended(true);
  assert(format(reply, sizeof(reply), true));
  assert(strcmp(reply, "> WiFi: paused; retries every 30 minutes; saved network: 'working-network'; seeder status") == 0);
  assert(format(reply, sizeof(reply), false));
  assert(strstr(reply, "saved network: '(not configured)'; seeder status"));
  power.setHold(mesh::wifi::NetworkPowerHold::WebConfig, true);
  format(reply, sizeof(reply), true); assert(strcmp(reply, "> connecting") == 0);
  power.setHold(mesh::wifi::NetworkPowerHold::WebConfig, false);
  power.setSuspended(false);
  format(reply, sizeof(reply), true); assert(strcmp(reply, "> connecting") == 0);
  power.setManaged(false);
}
'''.replace("@BLOCK@", block)
        self.compile_and_run(fixture)


if __name__ == "__main__":
    unittest.main()
