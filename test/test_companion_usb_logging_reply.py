"""Exercise logging handoffs with real Companion USB parsing and routing."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "helpers/ArduinoSerialInterface.h"
#include "helpers/MultiSerialInterface.h"
#include "helpers/UsbAsciiBinarySwitch.h"
#include "helpers/CLICommandUtils.h"
#include "helpers/CompanionHardwareCommandCompat.h"
#define MESH_USB_LOGGING_AVAILABLE 1
#define COMPANION_FEATURE_USB_MOTA_SOURCE 0
#define COMPANION_FEATURE_NETWORK_TERMINAL 1
static bool connected=true, dedicated=false, logging=false, network=false;
static bool usb_logging_network_parked=false, usb_logging_terminal_mode=false;
static bool usb_protocol_initialized=true, usb_terminal_discard_line=false;
static bool usb_logging_reply_hold=false, usb_logging_reply_staged=false;
static bool usb_logging_reply_pending=false, usb_logging_reply_state=false;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
struct UsbStream : Stream {
  std::vector<uint8_t> rx, tx;
  size_t cursor=0;
  int capacity=4096, max_write=4096;
  int available() override { return static_cast<int>(rx.size()-cursor); }
  int read() override { return cursor<rx.size()?rx[cursor++]:-1; }
  int peek() override { return cursor<rx.size()?rx[cursor]:-1; }
  int availableForWrite() override { return capacity; }
  size_t write(const uint8_t* p,size_t n) override {
    if(n>static_cast<size_t>(capacity)) n=capacity;
    if(n>static_cast<size_t>(max_write)) n=max_write;
    tx.insert(tx.end(),p,p+n);
    return n;
  }
  void receive(const char* command) {
    size_t n=strlen(command)+1;
    rx={'<',static_cast<uint8_t>(n),0,mesh::companion::kRunCliCommand};
    rx.insert(rx.end(),command,command+n-1);
    cursor=0;
  }
} stream;
struct Remote : BaseSerialInterface {
  bool enabled=false;
  std::vector<uint8_t> rx, tx;
  void enable() override { enabled=true; }
  void disable() override { enabled=false; }
  bool isEnabled() const override { return enabled; }
  bool isConnected() const override { return true; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t* p,size_t n) override {
    tx.insert(tx.end(),p,p+n); return n;
  }
  size_t checkRecvFrame(uint8_t* out) override {
    const size_t n=rx.size();
    if(n) memcpy(out,rx.data(),n);
    rx.clear(); return n;
  }
} ble;
ArduinoSerialInterface usb_serial_interface;
MultiSerialInterface interface_manager;
static bool clientConnected() { return connected; }
static Stream& usbTerminalOutput() { return stream; }
static bool isNetworkTerminalActive() { return network; }
static void clearUsbTerminalLine() {}
static unsigned banners=0, cancellations=0;
namespace mesh {
bool hasDedicatedUsbLoggingPort() { return dedicated; }
bool isUsbLoggingEnabled() { return logging; }
void setUsbLoggingEnabled(bool value) { logging=value; }
void discardUsbTerminalOutput() {}
Stream& usbCompanionPort() { return stream; }
bool saveUsbLoggingBootPreference(bool) { return true; }
bool usbLoggingInterfaceRestartRequired() { return false; }
bool isUsbLoggingPacketStream() { return false; }
}
namespace CompanionMqttSetupPortal {
static bool saveEnabled(bool) { return true; }
}
static uint32_t futureMillis(uint32_t value) { return millis()+value; }
struct MyMesh {
  struct { unsigned usb_logging_enabled=0; } _prefs;
  BaseSerialInterface* _serial=&interface_manager;
  bool terminal=false, _mqtt_enabled=false;
  unsigned _scheduled_reboot_at=0;
  uint8_t cmd_frame[MAX_FRAME_SIZE+1]{}, out_frame[MAX_FRAME_SIZE+4]{};
  char reply_buf[166]{};
  bool isTerminalMode() const { return terminal; }
  void cancelSerialResponseStream(BaseSerialInterface* route) {
    if (interface_manager.isReplyRouteFor(route)) ++cancellations;
  }
  void cancelSerialOperationsForRoute(BaseSerialInterface*) {}
  void enterTerminalMode(bool show_banner) {
    terminal=true;
    if(show_banner) { ++banners; usbTerminalOutput().print("BANNER"); }
  }
  bool savePrefs() { return true; }
  void stopMQTT() {}
  void applyUsbLoggingState(bool);
  void beginUsbLoggingReplyBarrier(BaseSerialInterface*);
  void endUsbLoggingReplyBarrier(bool);
  void writeErrFrame(uint8_t) { assert(false); }
  bool handleCommand(const char* command,uint32_t,char* reply) {
    size_t reply_size=160;
    @SETTERS@
    strcpy(reply,"query"); return true;
  }
  void handleCmdFrame(size_t len) {
    static constexpr uint8_t RESP_CODE_CLI_REPLY=29, ERR_CODE_ILLEGAL_ARG=1;
    @CLI_FRAME@
  }
} the_mesh;
static void leaveUsbTerminalMode(bool) { the_mesh.terminal=false; }
@FUNCTIONS@
static void reset() {
  connected=true; dedicated=false; logging=false; network=false;
  usb_logging_network_parked=usb_logging_terminal_mode=false;
  usb_logging_reply_hold=usb_logging_reply_staged=false;
  usb_logging_reply_pending=usb_logging_reply_state=false;
  stream=UsbStream(); ble=Remote(); the_mesh=MyMesh();
  usb_serial_interface=ArduinoSerialInterface();
  interface_manager=MultiSerialInterface();
  usb_serial_interface.begin(stream);
  usb_serial_interface.setConnectedCheck(clientConnected);
  usb_serial_interface.enableFlowControl(true);
  assert(interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface));
  assert(interface_manager.addInterface(InterfaceType::Bluetooth,&ble));
  interface_manager.enable(); banners=cancellations=0;
}
static void command(const char* text) {
  stream.receive(text);
  size_t len=interface_manager.checkRecvFrame(the_mesh.cmd_frame);
  assert(len>0 && interface_manager.captureReplyRoute()==&usb_serial_interface);
  the_mesh.handleCmdFrame(len);
}
static void checkReply() {
  assert(stream.tx.size()>=4 && stream.tx[0]=='>');
  const size_t size=stream.tx[1]+256*stream.tx[2];
  assert(stream.tx.size()>=size+3 && stream.tx[3]==29);
  assert(ble.tx.empty());
  const std::string reply(stream.tx.begin()+4,stream.tx.begin()+size+3);
  assert(reply.find("saved")!=std::string::npos);
}
int main() {
  // Real CLI setters, not a mock apply call: both variants retain the request.
  for(const char* setting : {"set usb.logging on","set logging.output both"}) {
    reset(); command(setting);
    assert(!logging && !the_mesh.terminal && usb_logging_reply_pending);
    checkReply();
    const size_t frame_bytes=stream.tx.size();
    serviceUsbLoggingReplyBarrier();
    assert(logging && the_mesh.terminal && !usb_logging_reply_pending);
    assert(stream.tx.size()==frame_bytes+6);
    assert(std::string(stream.tx.begin()+frame_bytes,stream.tx.end())=="BANNER");
    assert(ble.tx.empty());
  }
  // Stall and partial writes never block the mesh or allow a banner to splice
  // into a framed response; the complete reply precedes the logging handoff.
  reset(); stream.capacity=0; command("set usb.logging on");
  assert(stream.tx.empty() && usb_logging_reply_pending && !logging);
  for(unsigned i=0;i<10000;++i) serviceUsbLoggingReplyBarrier();
  assert(!logging && !the_mesh.terminal && stream.tx.empty());
  stream.capacity=7; stream.max_write=3;
  for(unsigned i=0;i<1000 && usb_logging_reply_pending;++i) {
    serviceUsbLoggingReplyBarrier();
    if(usb_logging_reply_pending) assert(!logging && !the_mesh.terminal);
  }
  assert(logging); checkReply();
  // An incomplete *next* input does not hold the already admitted reply.
  reset(); stream.capacity=0; command("set usb.logging on");
  stream.rx={'<',5,0,1}; stream.cursor=0;
  uint8_t input[176]; assert(usb_serial_interface.checkRecvFrame(input)==0);
  assert(usb_serial_interface.isReadBusy());
  stream.capacity=4096; serviceUsbLoggingReplyBarrier();
  assert(logging && the_mesh.terminal); checkReply();
  // A new read-only USB request while stalled must not erase the old intent.
  reset(); stream.capacity=0; command("set usb.logging on");
  command("get version"); assert(usb_logging_reply_pending && !logging);
  stream.capacity=4096; serviceUsbLoggingReplyBarrier(); assert(logging);
  // Disconnect/reset/ASCII takeover cancels this session's queued intent.
  reset(); stream.capacity=0; command("set usb.logging on");
  connected=false; serviceUsbLoggingReplyBarrier();
  connected=true; stream.capacity=4096; serviceUsbLoggingReplyBarrier();
  assert(!logging && !usb_logging_reply_pending && ble.tx.empty());
  reset(); stream.capacity=0; command("set usb.logging on");
  cancelUsbSerialOperations(); usb_serial_interface.resetSessionState();
  stream.capacity=4096; serviceUsbLoggingReplyBarrier(); assert(!logging);
  // A full reply queue leaves the logging command unread until its
  // acknowledgement can fit; draining then permits the normal handoff.
  reset(); stream.capacity=0;
  const uint8_t required[]={29,1};
  for(unsigned i=0;i<4;++i) assert(usb_serial_interface.writeFrame(required,2)==2);
  stream.receive("set usb.logging on");
  assert(interface_manager.checkRecvFrame(the_mesh.cmd_frame)==0);
  assert(stream.cursor==0 && stream.available()>0);
  assert(!usb_logging_reply_pending && !logging && ble.tx.empty());
  stream.capacity=4096;
  size_t resumed=interface_manager.checkRecvFrame(the_mesh.cmd_frame);
  assert(resumed>0 && interface_manager.captureReplyRoute()==&usb_serial_interface);
  the_mesh.handleCmdFrame(resumed);
  assert(usb_logging_reply_pending && !logging && !the_mesh.terminal);
  serviceUsbLoggingReplyBarrier();
  assert(logging && the_mesh.terminal && !usb_logging_reply_pending);
  // A later explicit setting supersedes an earlier pending on request.
  reset(); stream.capacity=0; command("set usb.logging on");
  the_mesh.applyUsbLoggingState(false);
  stream.capacity=4096; serviceUsbLoggingReplyBarrier(); assert(!logging);
  // Dedicated CDC and commands from another interface don't defer its reply.
  reset(); dedicated=true; command("set usb.logging on");
  assert(logging && !usb_logging_reply_pending && !the_mesh.terminal); checkReply();
  reset(); the_mesh.beginUsbLoggingReplyBarrier(&ble);
  the_mesh.applyUsbLoggingState(true); the_mesh.endUsbLoggingReplyBarrier(true);
  assert(logging && !usb_logging_reply_pending && the_mesh.terminal);
  // A real BLE framed command retains its route even when it parks USB.
  // Network terminal ownership likewise survives this logging update.
  for(bool network_owner : {false,true}) {
    reset(); network=network_owner;
    const char* setting="set usb.logging on";
    ble.rx={mesh::companion::kRunCliCommand};
    ble.rx.insert(ble.rx.end(),setting,setting+strlen(setting));
    size_t len=interface_manager.checkRecvFrame(the_mesh.cmd_frame);
    assert(len>0 && interface_manager.captureReplyRoute()==&ble);
    the_mesh.handleCmdFrame(len);
    assert(logging && !usb_logging_reply_pending && ble.tx[0]==29);
    assert(interface_manager.captureReplyRoute()==&ble && cancellations==0);
    assert(the_mesh.terminal!=network_owner && network==network_owner);
    assert(usb_serial_interface.isPassthroughMode());
  }
}
'''


class CompanionLoggingReplyTest(unittest.TestCase):
    def test_logging_acknowledgement_precedes_single_tty_handoff(self):
        main = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        mesh = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        signatures = (
            'static void cancelUsbSerialOperations() {',
            'static void enterUsbTerminalMode(bool show_banner = true) {',
            'static void enterUsbLoggingTerminalMode() {',
            'static void serviceUsbLoggingOwnership(bool logging_enabled) {',
            'void MyMesh::applyUsbLoggingState(bool enabled) {',
            'void MyMesh::beginUsbLoggingReplyBarrier(BaseSerialInterface* route) {',
            'void MyMesh::endUsbLoggingReplyBarrier(bool reply_queued) {',
            'static void serviceUsbLoggingReplyBarrier() {',
        )
        source = HARNESS.replace('@FUNCTIONS@', '\n'.join(
            extract_braced(main, signature) for signature in signatures))
        setters = '\n'.join(extract_braced(mesh, signature) for signature in (
            'if (strncmp(command, "set usb.logging", 15) == 0',
            'if (strncmp(command, "set logging.output ", 19) == 0)',
        ))
        source = source.replace('@SETTERS@', setters).replace('@CLI_FRAME@',
            extract_braced(mesh, 'if (mesh::companion::isRunCliFrame(cmd_frame[0], len))'))
        with tempfile.TemporaryDirectory() as directory:
            cpp, exe = Path(directory) / 'reply.cpp', Path(directory) / 'reply.exe'
            cpp.write_text(source)
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-Wno-sign-compare',
                            '-I' + str(ROOT / 'test/mocks'), '-I' + str(ROOT / 'src'),
                            str(cpp), str(ROOT / 'src/helpers/ArduinoSerialInterface.cpp'),
                            '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
