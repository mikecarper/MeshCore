#!/usr/bin/env python3
"""Run real Companion inbox downloads against bounded UART/TCP transports."""

from pathlib import Path
import configparser
import re
import shlex
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/wifi/SerialWifiInterface.h>
#include <helpers/BorrowableFrameBuffer.h>
#include <algorithm>
#include <cassert>
#include <cstring>
#include <deque>
#include <limits>
#include <vector>
#define DISPLAY_CLASS TestDisplay
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#ifndef OFFLINE_QUEUE_SIZE
#define OFFLINE_QUEUE_SIZE 256
#endif
@CONSTANTS@
@STREAM@
std::deque<WiFiClient> WiFiServer::incoming;
static bool usb_connected = true;
static bool usbConnected() { return usb_connected; }
namespace mesh { namespace ota { struct OtaContext { unsigned marker=0x11223344; }; } }
struct UI {
  int pending=0, last_removed=-1;
  unsigned changes=0;
  void syncMessageQueue(int count,int removed=-1) {
    pending=count; last_removed=removed; ++changes;
  }
};
struct MyMesh {
@FRAME@
  UI ui, *_ui=&ui;
  BaseSerialInterface* _serial=nullptr;
  uint8_t out_frame[MAX_FRAME_SIZE]{};
  int offline_queue_len=0, offline_queue_head=0;
#if defined(OTA_SHARED_COMPANION_QUEUE)
  mesh::BorrowableFrameBuffer<Frame,OFFLINE_QUEUE_SIZE,128,mesh::ota::OtaContext> offline_queue;
#elif ONE_KEY_DM_SHARED_OFFLINE_QUEUE
  Frame offline_queue[OFFLINE_QUEUE_SIZE]{};
  unsigned held_dm_count=15;
#elif defined(BOARD_HAS_PSRAM)
  Frame frames[OFFLINE_QUEUE_SIZE]{};
  Frame* offline_queue=frames;
  int offline_queue_capacity=128;
#else
  Frame offline_queue[OFFLINE_QUEUE_SIZE]{};
#endif
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int);
  bool addToOfflineQueue(const uint8_t[],int);
  int peekOfflineQueue(uint8_t[]);
  void popOfflineQueue();
  int getFromOfflineQueue(uint8_t[]);
  void syncNextOfflineMessage();
#if defined(OTA_SHARED_COMPANION_QUEUE)
  static mesh::ota::OtaContext* acquireOfflineQueueForOta(void*);
  static uint16_t drainOfflineQueueForOta(void*);
  static void releaseOfflineQueueFromOta(void*);
#endif
  void sync() {
    uint8_t cmd_frame[]={CMD_SYNC_NEXT_MESSAGE};
    @SYNC@
  }
  void add(uint8_t code,uint8_t marker) {
    const uint8_t bytes[]={code,marker};
    assert(addToOfflineQueue(bytes,sizeof(bytes)));
  }
};
@METHODS@
using Bytes=std::vector<uint8_t>;
static std::vector<Bytes> frames(const Bytes& bytes) {
  std::vector<Bytes> result;
  size_t at=0;
  while(at<bytes.size()) {
    assert(bytes.size()-at>=3&&bytes[at]=='>');
    size_t len=bytes[at+1]|size_t(bytes[at+2])<<8;
    at+=3; assert(len<=bytes.size()-at);
    result.emplace_back(bytes.begin()+at,bytes.begin()+at+len); at+=len;
  }
  return result;
}
struct Fixture {
  BufferStream stream, other_stream;
  ArduinoSerialInterface usb, other;
  SerialWifiInterface tcp;
  MultiSerialInterface manager;
  MyMesh mesh;
  uint8_t command[MAX_FRAME_SIZE]{};
  Fixture() {
    g_mock_millis=100; usb_connected=true; WiFiServer::incoming.clear();
    usb.begin(stream); usb.enableFlowControl(true); usb.setConnectedCheck(usbConnected);
    other.begin(other_stream); other.enableFlowControl(true); tcp.begin(5000);
    assert(manager.addInterface(InterfaceType::USB,&usb));
    assert(manager.addInterface(InterfaceType::Bluetooth,&other));
    assert(manager.addInterface(InterfaceType::WiFi,&tcp));
    manager.enable();mesh._serial=&manager;select(stream,&usb);
  }
  ~Fixture() {tcp.end();}
  void select(BufferStream& stream,BaseSerialInterface* target) {
    const uint8_t bytes[]={'<',1,0,CMD_SYNC_NEXT_MESSAGE};
    stream.push(bytes,sizeof(bytes));
    assert(manager.checkRecvFrame(command)==1);
    assert(manager.captureReplyRoute()==target);
  }
  std::shared_ptr<MockSocket> connect() {
    auto socket=std::make_shared<MockSocket>(IPAddress(192,168,1,10));
    socket->received={'<',1,0,CMD_SYNC_NEXT_MESSAGE};
    WiFiServer::incoming.push_back(WiFiClient(socket));
    unsigned len=0;
    for(unsigned i=0;i<8&&len==0;++i) len=manager.checkRecvFrame(command);
    assert(len==1&&manager.captureReplyRoute()==&tcp);return socket;
  }
  void fill(BaseSerialInterface* route) {
    const uint8_t required=0;
    for(unsigned i=0;i<4;++i) assert(manager.writeFrameToRoute(route,&required,1)==1);
  }
  void drain() {
    stream.write_capacity=4096;other_stream.write_capacity=4096;
    for(unsigned i=0;i<8;++i) {g_mock_millis+=100;manager.loop();manager.checkRecvFrame(command);}
  }
};
struct ShortReply : BaseSerialInterface {
  bool enabled=true, connected=true;
  size_t accepted=1; unsigned writes=0;
  mutable unsigned availability_checks=0;
  Bytes last_reply;
  void enable() override {enabled=true;}
  void disable() override {enabled=false;}
  bool isEnabled() const override {return enabled;}
  bool isConnected() const override {return connected;}
  bool isReadBusy() const override {return false;}
  bool isWriteBusy() const override {return false;}
  bool isReplyRouteAvailable(BaseSerialInterface* route) const override {
    ++availability_checks;return BaseSerialInterface::isReplyRouteAvailable(route);
  }
  size_t writeFrame(const uint8_t* data,size_t len) override {
    ++writes;last_reply.assign(data,data+len);return std::min(accepted,len);
  }
  size_t checkRecvFrame(uint8_t*) override {return 0;}
};
int main() {
  // Every downloaded frame type must survive a full requester's real UART
  // queue, even though another live transport has room to accept it.
  for(uint8_t type:{7,8,16,17,27}) {
    Fixture f;f.mesh.add(type,42);f.mesh.add(7,43);
    f.stream.write_capacity=0;f.fill(&f.usb);
    const unsigned ui_changes=f.mesh.ui.changes;
    for(unsigned retry=0;retry<5;++retry) f.mesh.sync();
    assert(f.mesh.offline_queue_len==2&&f.mesh.ui.pending==2);
    assert(f.mesh.ui.changes==ui_changes&&f.other_stream.output.empty());
    uint8_t peek[MAX_FRAME_SIZE]{};
    assert(f.mesh.peekOfflineQueue(peek)==2&&peek[0]==type&&peek[1]==42);
    f.drain();f.mesh.sync();f.mesh.sync();f.mesh.sync();
    const auto received=frames(f.stream.output);
    assert(received.size()==7&&received[4]==Bytes({type,42}));
    assert(received[5]==Bytes({7,43})&&received[6]==Bytes({10}));
    assert(f.mesh.offline_queue_len==0&&f.mesh.ui.pending==0);
    assert(f.mesh.ui.changes==ui_changes+2&&f.other_stream.output.empty());
  }
  // The application's request can be received just before USB disappears.
  // Neither a disconnected route nor a disabled manager consumes the inbox.
  for(unsigned fault=0;fault<3;++fault) {
    Fixture f;f.mesh.add(7,51);const auto changes=f.mesh.ui.changes;
    if(fault==0) usb_connected=false;
    if(fault==1) f.usb.disable();
    if(fault==2) f.manager.disable();
    f.mesh.sync();assert(f.mesh.offline_queue_len==1&&f.mesh.ui.changes==changes);
    assert(f.stream.output.empty()&&f.other_stream.output.empty());
    usb_connected=true;f.manager.enable();f.select(f.other_stream,&f.other);
    f.mesh.sync();assert(f.mesh.offline_queue_len==0);
    assert(frames(f.other_stream.output)==std::vector<Bytes>({{7,51}}));
    assert(f.stream.output.empty());
  }
  // A positive short delivery is still a rejection; null/empty peeks and
  // empty commits leave queue and display state unchanged.
  {
    MyMesh node;ShortReply reply;node._serial=&reply;node.add(7,55);
    const auto changes=node.ui.changes;
    node.sync();assert(node.offline_queue_len==1&&node.ui.changes==changes);
    reply.accepted=MAX_FRAME_SIZE;node.sync();assert(node.offline_queue_len==0);
    assert(node.ui.changes==changes+1);
    node.popOfflineQueue();assert(node.ui.changes==changes+1);
    assert(node.peekOfflineQueue(nullptr)==0);
    reply.connected=false;node.add(7,56);node.sync();assert(reply.writes==2);
  }
  // An empty response still delegates to the captured transport without a
  // nonempty-message availability precheck. A zero-length retained head has
  // the same behavior and must never be committed as a delivered message.
  {
    MyMesh node;ShortReply reply;node._serial=&reply;reply.connected=false;
    node.sync();assert(reply.writes==1&&reply.availability_checks==0);
    assert(reply.last_reply==Bytes({10})&&node.offline_queue_len==0);
    node.add(7,57);node.offlineQueueFrameAt(0).len=0;
    const auto changes=node.ui.changes;
    node.sync();assert(reply.writes==2&&reply.availability_checks==0);
    assert(reply.last_reply==Bytes({10})&&node.offline_queue_len==1);
    assert(node.ui.changes==changes);
    node.offlineQueueFrameAt(0).len=2;reply.connected=true;reply.accepted=MAX_FRAME_SIZE;
    node.sync();assert(reply.last_reply==Bytes({7,57})&&node.offline_queue_len==0);
    assert(reply.availability_checks==1&&node.ui.changes==changes+1);
  }
  // Keep the non-flow-controlled UART's existing short-write contract. A
  // rejected frame remains pending and is retried in full on a new request.
  {
    Fixture f;f.usb.enableFlowControl(false);f.stream.max_write=2;f.mesh.add(7,61);
    f.mesh.sync();assert(f.mesh.offline_queue_len==1&&f.stream.output.size()==2);
    f.stream.output.clear();f.stream.max_write=999;f.mesh.sync();
    assert(f.mesh.offline_queue_len==0&&frames(f.stream.output)==std::vector<Bytes>({{7,61}}));
  }
  // Actual TCP backpressure must retain the unread head for that requester;
  // a different transport can later request it exactly once.
  {
    Fixture f;auto socket=f.connect();f.fill(&f.tcp);f.mesh.add(7,71);
    const auto changes=f.mesh.ui.changes;f.mesh.sync();
    assert(f.mesh.offline_queue_len==1&&f.mesh.ui.changes==changes);
    assert(f.stream.output.empty()&&socket->sent.empty());
    f.select(f.stream,&f.usb);f.mesh.sync();
    assert(f.mesh.offline_queue_len==0&&frames(f.stream.output)==std::vector<Bytes>({{7,71}}));
    socket->write_limit=4096;f.drain();assert(frames(socket->sent).size()==4);
  }
  // Circular-head wrap and the held-DM tail must keep FIFO ordering. The
  // configured PSRAM capacity follows the actual allocation size.
  {
    Fixture f;const unsigned capacity=f.mesh.getOfflineQueueCapacity()
#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
        -f.mesh.held_dm_count
#endif
        ;
#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
    memset(f.mesh.offline_queue+capacity,0xA5,sizeof(MyMesh::Frame)*f.mesh.held_dm_count);
#endif
    for(unsigned round=0;round<3;++round) {
      f.drain();
      for(unsigned i=0;i<capacity;++i) f.mesh.add(7,uint8_t(i));
      const unsigned consumed=capacity/2;
      for(unsigned i=0;i<consumed;++i) f.mesh.sync();
      for(unsigned i=0;i<consumed;++i) f.mesh.add(7,uint8_t(i+capacity));
      for(unsigned i=consumed;i<capacity+consumed;++i) {
        uint8_t head[MAX_FRAME_SIZE];assert(f.mesh.peekOfflineQueue(head)==2&&head[1]==uint8_t(i));
        f.mesh.sync();assert(f.mesh.ui.pending==int(capacity+consumed-i-1));
      }
      assert(f.mesh.offline_queue_len==0&&f.mesh.offline_queue_head==0);
    }
#if ONE_KEY_DM_SHARED_OFFLINE_QUEUE
    const auto* tail=reinterpret_cast<const uint8_t*>(f.mesh.offline_queue+capacity);
    for(unsigned i=0;i<sizeof(MyMesh::Frame)*f.mesh.held_dm_count;++i) assert(tail[i]==0xA5);
#endif
  }
#if defined(OTA_SHARED_COMPANION_QUEUE)
  // The explicitly destructive mOTA drain still keeps the newest 128,
  // including when borrowing/releasing relocates a wrapped queue in place.
  {
    Fixture f;for(unsigned i=0;i<200;++i) f.mesh.add(7,uint8_t(i));
    for(unsigned i=0;i<100;++i) f.mesh.sync();
    for(unsigned i=200;i<356;++i) f.mesh.add(7,uint8_t(i));
    assert(MyMesh::acquireOfflineQueueForOta(&f.mesh)==nullptr);
    assert(MyMesh::drainOfflineQueueForOta(&f.mesh)==128&&f.mesh.offline_queue_len==128);
    auto* workspace=MyMesh::acquireOfflineQueueForOta(&f.mesh);
    assert(workspace&&workspace->marker==0x11223344&&f.mesh.getOfflineQueueCapacity()==128);
    f.stream.write_capacity=0;f.fill(&f.usb);f.mesh.sync();
    assert(f.mesh.offline_queue_len==128&&f.mesh.ui.pending==128);
    MyMesh::releaseOfflineQueueFromOta(&f.mesh);
    assert(f.mesh.getOfflineQueueCapacity()==256);f.drain();
    for(unsigned i=228;i<356;++i) {
      uint8_t head[MAX_FRAME_SIZE];assert(f.mesh.peekOfflineQueue(head)==2&&head[1]==uint8_t(i));
      f.mesh.sync();
    }
    assert(f.mesh.offline_queue_len==0&&f.mesh.ui.pending==0);
  }
#endif
}
'''


class CompanionOfflineDeliveryTest(unittest.TestCase):
    def test_tag_ble_capacity_preserves_contacts_channels_and_full_queue(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "variants/rak_wismesh_tag/platformio.ini")
        flags = shlex.split(config["env:RAK_WisMesh_Tag_companion_radio_ble"]["build_flags"])
        self.assertEqual(flags.count("OFFLINE_QUEUE_SIZE=240"), 1)
        self.assertIn("MAX_CONTACTS=350", flags)
        self.assertIn("MAX_GROUP_CHANNELS=40", flags)
        # Run the production Full overlay without resolving or building any
        # PlatformIO environment. It must explicitly replace a BLE override
        # if a future Full recipe inherits the BLE base instead of USB.
        result = subprocess.run(["bash", "-c", r'''
set -euo pipefail
source build.sh
pio() { echo "unexpected PlatformIO invocation" >&2; return 127; }
PIO_ENV_PLATFORM_BY_NAME[RAK_WisMesh_Tag_companion_radio_full]=NRF52_PLATFORM
PLATFORMIO_BUILD_FLAGS='-DMAX_CONTACTS=350 -DMAX_GROUP_CHANNELS=40'
pio_env_option_contains() { return 1; }
apply_companion_radio_full_profile RAK_WisMesh_Tag_companion_radio_full RAK_WisMesh_Tag_companion_radio_usb
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS" "$PLATFORMIO_BUILD_UNFLAGS"
'''], cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-DMAX_CONTACTS=350", result.stdout)
        self.assertIn("-DMAX_GROUP_CHANNELS=40", result.stdout)
        self.assertIn("-DOFFLINE_QUEUE_SIZE=256", result.stdout)
        self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", result.stdout)
        self.assertIn("-D OFFLINE_QUEUE_SIZE=240", result.stdout)

    def test_real_download_paths_in_every_queue_layout(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool MyMesh::Frame::isChannelMsg() const",
            "int MyMesh::getOfflineQueueCapacity() const",
            "MyMesh::Frame& MyMesh::offlineQueueFrameAt(",
            "bool MyMesh::addToOfflineQueue(", "int MyMesh::peekOfflineQueue(",
            "void MyMesh::popOfflineQueue(", "int MyMesh::getFromOfflineQueue(",
            "void MyMesh::syncNextOfflineMessage(",
        ))
        methods += "\n#if defined(OTA_SHARED_COMPANION_QUEUE)\n" + "\n".join(
            extract_braced(source, signature) for signature in (
                "mesh::ota::OtaContext* MyMesh::acquireOfflineQueueForOta(",
                "uint16_t MyMesh::drainOfflineQueueForOta(",
                "void MyMesh::releaseOfflineQueueFromOta(",
            )) + "\n#endif\n"
        constants = "\n".join(re.findall(
            r"^#define (?:RESP_CODE_(?:CONTACT_MSG_RECV|CHANNEL_MSG_RECV|CHANNEL_DATA_RECV|NO_MORE_MESSAGES)(?:_V3)?|CMD_SYNC_NEXT_MESSAGE)\s+.+$",
            source, re.MULTILINE))
        native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
        harness = HARNESS.replace("@METHODS@", methods).replace("@CONSTANTS@", constants)
        harness = harness.replace("@STREAM@", extract_braced(native, "class BufferStream") + ";")
        harness = harness.replace("@FRAME@", extract_braced(header, "struct Frame {") + ";")
        harness = harness.replace("@SYNC@", extract_braced(source, "if (cmd_frame[0] == CMD_SYNC_NEXT_MESSAGE)"))
        configurations = {
            "circular": [],
            "held-dm": ["-DONE_KEY_DM_SHARED_OFFLINE_QUEUE=1"],
            "tag-ble": ["-DONE_KEY_DM_SHARED_OFFLINE_QUEUE=1", "-DOFFLINE_QUEUE_SIZE=240"],
            "mota": ["-DOTA_SHARED_COMPANION_QUEUE=1"],
            "psram": ["-DESP32_PLATFORM=1", "-DBOARD_HAS_PSRAM=1"],
        }
        with tempfile.TemporaryDirectory(prefix="mesh-offline-delivery-") as temp:
            work = Path(temp)
            path = work / "test.cpp"
            path.write_text(harness)
            for name, flags in configurations.items():
                with self.subTest(layout=name):
                    binary = work / name
                    compiled = subprocess.run([
                        "c++", "-std=c++17", "-g", "-Werror", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", *flags,
                        "-I", str(ROOT / "test/mocks"),
                        "-I", str(ROOT / "test/fixtures/serial_wifi_sessions/mocks"),
                        "-I", str(ROOT / "src"), str(path),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"),
                        str(ROOT / "src/helpers/wifi/SerialWifiInterface.cpp"),
                        "-o", str(binary),
                    ], text=True, capture_output=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
