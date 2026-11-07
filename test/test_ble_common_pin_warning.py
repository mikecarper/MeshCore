"""Exercise the real warning and inbox sync against two distinct transports."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CommonPinWarningTest(unittest.TestCase):
    def test_bluetooth_only_notice(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        # Check the effective PIN after saved/default and generated-session
        # resolution, not the stored zero sentinel or a board's default.
        self.assertIn("_using_common_ble_pin = mesh::companion::usingCommonBlePin(_active_ble_pin)", source)
        sync = source[source.index("void MyMesh::syncNextOfflineMessage()"):source.index("float MyMesh::getAirtimeBudgetFactor()")]
        start = source.index("void MyMesh::scheduleCommonBlePinWarning(")
        warning = source[start:source.index("\n#endif", start)]
        # Check the APP_START guard as well as the actual delivery methods.
        start = source.index("BaseSerialInterface* app_route = _serial->captureReplyRoute();", source.index("CMD_APP_START"))
        app_start = source[start:source.index("} else if", start)]
        self.assertIn("scheduleCommonBlePinWarning(app_route)", app_start)
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/BleCommonPinWarning.h>
#define BLE_PIN_CODE 123456
#define MAX_GROUP_CHANNELS 3
#define MAX_FRAME_SIZE 176
#define RESP_CODE_CHANNEL_MSG_RECV 8
#define RESP_CODE_CHANNEL_MSG_RECV_V3 17
#define RESP_CODE_NO_MORE_MESSAGES 10
#define TXT_TYPE_PLAIN 0
#define PUSH_CODE_MSG_WAITING 0x83
struct BaseSerialInterface {};
BaseSerialInterface ble, usb;
bool companionIsBluetoothRoute(BaseSerialInterface* p) {return p == &ble;}
struct ChannelDetails { char name[20]{}; };
struct Frame { uint8_t buf[176]{}; int len=0; };
struct Serial {
  BaseSerialInterface* route=&ble;
  bool available=true, accept=true, busy=false;
  std::vector<uint8_t> output;
  BaseSerialInterface* captureReplyRoute() {return route;}
  bool isReplyRouteAvailable(BaseSerialInterface*) {return available;}
  bool isReplyRouteWriteBusy(BaseSerialInterface*) {return busy;}
  size_t writeFrameToRoute(BaseSerialInterface*, const uint8_t* b, size_t n) {
    if (!accept) return 0;
    output.assign(b,b+n); return n;
  }
};
struct Clock { uint32_t getCurrentTime() {return 123;} };
struct Millis { uint32_t now=0; uint32_t getMillis() {return now;} };
struct MyMesh {
  bool _common_ble_pin_warning_pending=true, _common_ble_pin_warning_sent=false;
  bool _common_ble_pin_warning_in_flight=false;
  uint8_t _common_ble_pin_warning_pushes=0;
  uint32_t _common_ble_pin_warning_last_push=0;
  BaseSerialInterface* _common_ble_pin_warning_route=nullptr;
  bool _using_common_ble_pin=true, bluetooth_enabled=true, channel_enabled=true;
  int offline_queue_len=0, app_target_ver=3, radio_sends=0;
  uint8_t out_frame[MAX_FRAME_SIZE]{};
  Frame queue;
  Serial serial; Serial* _serial=&serial; Clock clock; Millis millis; Millis* _ms=&millis;
  bool isBluetoothEnabledPreference() const {return bluetooth_enabled;}
  bool getChannel(int i, ChannelDetails& c) {
    if (i!=1 || !channel_enabled) return false;
    std::strcpy(c.name,"Local test channel"); return true;
  }
  Clock* getRTCClock() {return &clock;}
  Frame& offlineQueueFrameAt(int) {return queue;}
  void popOfflineQueue() {--offline_queue_len;}
  bool sendCommonBlePinWarning(BaseSerialInterface* route);
  void scheduleCommonBlePinWarning(BaseSerialInterface* route);
  void serviceCommonBlePinWarning();
  void syncNextOfflineMessage();
};
''' + warning + '\n' + sync + r'''
int main() {
  using mesh::companion::usingCommonBlePin;
  assert(usingCommonBlePin(123456));
  assert(usingCommonBlePin(111111));
  assert(usingCommonBlePin(0));
  assert(!usingCommonBlePin(654321));
  assert(!usingCommonBlePin(123457));
  assert(!usingCommonBlePin(111112));
  assert(!usingCommonBlePin(1));
  MyMesh m;
  m._common_ble_pin_warning_pending=false;
  m.scheduleCommonBlePinWarning(&usb); assert(!m._common_ble_pin_warning_pending);
  m.scheduleCommonBlePinWarning(&ble); assert(m._common_ble_pin_warning_pending);
  m.serial.busy=true; m.serviceCommonBlePinWarning(); assert(m.serial.output.empty());
  m.serial.busy=false; m.serial.accept=false; m.serviceCommonBlePinWarning();
  assert(m._common_ble_pin_warning_route == &ble && m._common_ble_pin_warning_pushes==0);
  m.serial.accept=true; m.serviceCommonBlePinWarning();
  assert(m.serial.output[0]==PUSH_CODE_MSG_WAITING && m._common_ble_pin_warning_pushes==1);
  m.serial.output.clear(); m.serviceCommonBlePinWarning(); assert(m.serial.output.empty());
  m.millis.now=3000; m.serviceCommonBlePinWarning(); assert(m._common_ble_pin_warning_pushes==2);
  m.millis.now=6000; m.serviceCommonBlePinWarning(); assert(m._common_ble_pin_warning_pushes==3);
  m.serial.output.clear(); m.millis.now=9000; m.serviceCommonBlePinWarning();
  assert(m.serial.output.empty() && m._common_ble_pin_warning_pushes==3);
  m.serial.available=false; m.serviceCommonBlePinWarning();
  assert(!m._common_ble_pin_warning_route && m._common_ble_pin_warning_pending);
  m.serial.available=true; m.scheduleCommonBlePinWarning(&ble);
  m.serviceCommonBlePinWarning(); assert(m.serial.output[0]==PUSH_CODE_MSG_WAITING);
  m.serial.route=&usb; m.syncNextOfflineMessage();
  assert(m.serial.output[0]==RESP_CODE_NO_MORE_MESSAGES && m._common_ble_pin_warning_pending);
  m.serial.route=&ble; m.offline_queue_len=1; m.queue.buf[0]=42; m.queue.len=1;
  m.syncNextOfflineMessage(); assert(m.serial.output[0]==42 && m.offline_queue_len==0);
  m.serial.output.clear(); m.millis.now+=10000; m.serviceCommonBlePinWarning();
  assert(m.serial.output.empty()); // no redundant pushes during active sync
  m.serial.accept=false; m.syncNextOfflineMessage();
  assert(m._common_ble_pin_warning_pending && !m._common_ble_pin_warning_sent);
  assert(m._common_ble_pin_warning_route==&ble && m._common_ble_pin_warning_pushes==0);
  m.serial.accept=true; m.serviceCommonBlePinWarning();
  assert(m.serial.output[0]==PUSH_CODE_MSG_WAITING);
  m.syncNextOfflineMessage();
  assert(m.serial.output[0]==RESP_CODE_CHANNEL_MSG_RECV_V3);
  assert(m.serial.output[4]==1 && m.serial.output[5]==0xff);
  std::string notice(m.serial.output.begin()+11,m.serial.output.end());
  assert(notice.find("DEVICE WARNING:")==0);
  assert(notice.find("Common Bluetooth PIN")!=std::string::npos);
  assert(notice.find("Settings > Bluetooth Settings > Custom")!=std::string::npos);
  assert(notice.find("6 digits, save and reboot")!=std::string::npos);
  assert(notice.find("Local notice")!=std::string::npos);
  assert(notice.find("\n- Settings > Bluetooth Settings > Custom\n- Enter 6 digits")!=std::string::npos);
  assert(notice.size()<133 && m.serial.output.size()==notice.size()+11);
  assert(notice.find("123456")==std::string::npos && m.radio_sends==0);
  assert(!m._common_ble_pin_warning_sent && m._common_ble_pin_warning_pending &&
         m._common_ble_pin_warning_in_flight);
  // A queued warning can be dropped on disconnect; re-arm on next APP_START.
  m.serial.available=false; m.serviceCommonBlePinWarning();
  assert(!m._common_ble_pin_warning_in_flight && m._common_ble_pin_warning_pending);
  m.serial.available=true; m.scheduleCommonBlePinWarning(&ble);
  m.syncNextOfflineMessage(); assert(m.serial.output[0]==RESP_CODE_CHANNEL_MSG_RECV_V3);
  m.syncNextOfflineMessage();
  assert(m.serial.output[0]==RESP_CODE_NO_MORE_MESSAGES && m._common_ble_pin_warning_sent &&
         !m._common_ble_pin_warning_pending);
  // Even if pipelined requests looked like confirmation before the queued
  // frame reached the app, a later APP_START re-arms the warning.
  m.scheduleCommonBlePinWarning(&ble);
  assert(m._common_ble_pin_warning_pending && !m._common_ble_pin_warning_sent);
  m.syncNextOfflineMessage(); assert(m.serial.output[0]==RESP_CODE_CHANNEL_MSG_RECV_V3);
  m.syncNextOfflineMessage(); assert(m._common_ble_pin_warning_sent);
  MyMesh off; off.bluetooth_enabled=false; off.syncNextOfflineMessage();
  assert(off.serial.output[0]==RESP_CODE_NO_MORE_MESSAGES);
  MyMesh custom; custom._using_common_ble_pin=false; custom.syncNextOfflineMessage();
  assert(custom.serial.output[0]==RESP_CODE_NO_MORE_MESSAGES);
  MyMesh no_channel; no_channel.channel_enabled=false; no_channel.syncNextOfflineMessage();
  assert(no_channel.serial.output[0]==RESP_CODE_NO_MORE_MESSAGES);
  MyMesh old; old.app_target_ver=2; old.syncNextOfflineMessage();
  assert(old.serial.output[0]==RESP_CODE_CHANNEL_MSG_RECV);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "warning.cpp"
            exe = Path(directory) / ("warning.exe" if os.name == "nt" else "warning")
            cpp.write_text(harness, encoding="utf-8")
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-I", str(ROOT / "src"),
                            str(cpp), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
