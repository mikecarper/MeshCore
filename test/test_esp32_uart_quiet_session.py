"""Exercise real UART session parking, inbox delivery and serial sleep vetoes."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_esp32_usb_sleep import board_method


ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/UsbAsciiBinarySwitch.h>
#include <helpers/esp32/PowerTestControls.h>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <deque>
#include <limits>
#include <vector>
#define COMPANION_FEATURE_USB_MOTA_SOURCE 1
#define COMPANION_FEATURE_NETWORK_TERMINAL 1
#define COMPANION_FEATURE_READER 1
#define MESH_USB_LOGGING_AVAILABLE 0
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define OFFLINE_QUEUE_SIZE 64
@CONSTANTS@
@BUFFER@
struct Uart : BufferStream {
  bool driver=true;
  unsigned begins=0,ends=0,flushes=0,fail_begins=0;
  explicit operator bool() const { return driver; }
  int availableForWrite() override { return driver ? BufferStream::availableForWrite() : 0; }
  void begin(unsigned baud) {
    assert(baud==115200);++begins;
    driver=fail_begins==0;if(fail_begins)--fail_begins;
  }
  void end() { assert(driver);++ends;driver=false;input.clear(); }
  void flush() override { assert(driver);++flushes; }
} Serial;
static uint32_t micros() { return millis()*1000; }
static bool terminal_output_pending=false;
namespace mesh {
bool usbDataLocked() { return false; }
bool hasPendingUsbTerminalOutput() { return terminal_output_pending; }
bool isUsbLoggingEnabled() { return false; }
bool hasDedicatedUsbLoggingPort() { return false; }
void discardUsbTerminalOutput() { terminal_output_pending=false; }
Stream& usbTerminalPort() { return Serial; }
static struct { const Stream* output=nullptr; } print_job;
static const Stream*& reader_owner=print_job.output;
@READER_CANCEL@
struct GroupChannel {};
struct Packet {
  uint8_t path_len=0;
  float getSNR() const { return 4; }
  bool isRouteFlood() const { return false; }
};
namespace ota {
struct Source { bool pending=false;bool hasPendingResponse() const { return pending; } };
struct OtaContext { static Source& serialFolderSource() { static Source source;return source; } };
}
}
ArduinoSerialInterface usb_serial_interface,other_interface;
MultiSerialInterface interface_manager;
BufferStream other_stream;
struct MyMesh {
  @FRAME@
  Frame offline_queue[OFFLINE_QUEUE_SIZE]{};
  int offline_queue_len=0,offline_queue_head=0;
  uint8_t out_frame[MAX_FRAME_SIZE]{};
  BaseSerialInterface* _serial=&interface_manager;
  bool terminal=false,network=false,stale_input=true;
  unsigned ascii_starts=0;
  bool last_banner=true;
  BaseSerialInterface* stream_route=nullptr;
  BaseSerialInterface* pending_route=nullptr;
  unsigned cancelled_usb=0,cancelled_other=0;
  bool isTerminalMode() const { return terminal; }
  void enterTerminalMode(bool banner) { assert(!network);terminal=true;++ascii_starts;last_banner=banner; }
  void exitTerminalMode() { terminal=false; }
  void resetUsbHostSessionInput() { stale_input=false; }
  void cancelSerialResponseStream(BaseSerialInterface* route) {
    if(stream_route==route)stream_route=nullptr;
  }
  void cancelSerialOperationsForRoute(BaseSerialInterface* route) {
    if(route==&usb_serial_interface)++cancelled_usb;else++cancelled_other;
    if(pending_route==route)pending_route=nullptr;
  }
  int findChannelIdxForPacket(const mesh::GroupChannel&,mesh::Packet*) const { return 0; }
  uint8_t rxViaTag(const mesh::Packet*,uint8_t none) const { return none; }
  int getOfflineQueueCapacity() const;
  Frame& offlineQueueFrameAt(int);
  bool addToOfflineQueue(const uint8_t*,int);
  int peekOfflineQueue(uint8_t*);
  void popOfflineQueue();
  void syncNextOfflineMessage();
  void onChannelDataRecv(const mesh::GroupChannel&,mesh::Packet*,uint16_t,const uint8_t*,size_t);
  mesh::powerdiag::Mask serialVeto() const {
    using mesh::powerdiag::Reason;using mesh::powerdiag::mask;
    @SERIAL_GUARD@
    return 0;
  }
} the_mesh;
@INBOX@
static bool usb_protocol_initialized=true,usb_mota_mode=false;
static bool usb_logging_terminal_mode=false,usb_terminal_discard_line=false;
static bool usb_terminal_host_reset_completion_pending=false;
static char usb_terminal_line[64]{};
static unsigned usb_terminal_line_len=0;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
static bool usb_uart_quiet_parked=false,usb_uart_quiet_restore_enabled=false;
static bool isNetworkTerminalActive() { return the_mesh.network; }
static void queueUsbTerminalControlReply(const char*) { assert(false); }
static void drainUsbTerminalOutputBeforeProtocolSwitch() { assert(false); }
static void clearUsbTerminalLine() { memset(usb_terminal_line,0,sizeof(usb_terminal_line));usb_terminal_line_len=0; }
@SESSION@
struct Board {
  bool inhibit_sleep=false,ota=false,radio_test=false;
  uint32_t uart_quiet_start_at=0,uart_quiet_restore_at=0;
  uint16_t uart_quiet_seconds=0;
  bool isOTAUpdateRunning() const { return ota; }
  bool isRadioTestActive() const { return radio_test; }
  @BOARD@
};
using Bytes=std::vector<uint8_t>;
static void select(ArduinoSerialInterface& route,BufferStream& stream) {
  const uint8_t input[]={'<',1,0,10};uint8_t command[MAX_FRAME_SIZE]{};
  stream.push(input,sizeof(input));assert(interface_manager.checkRecvFrame(command)==1);
  assert(interface_manager.captureReplyRoute()==&route);
}
static void receive(unsigned sequence) {
  mesh::GroupChannel channel;mesh::Packet packet;
  const uint8_t data[]={uint8_t(sequence),0xa5,0x5a,0xff};
  the_mesh.onChannelDataRecv(channel,&packet,0xff00,data,sizeof(data));
}
static void setup() {
  resetArduinoMock();g_mock_millis=1000;
  usb_serial_interface.begin(Serial,"START");usb_serial_interface.enableFlowControl(true);
  other_interface.begin(other_stream);other_interface.enableFlowControl(true);
  assert(interface_manager.addInterface(InterfaceType::USB,&usb_serial_interface));
  assert(interface_manager.addInterface(InterfaceType::WiFi,&other_interface));
  interface_manager.enable();other_interface.disable();
  mesh::esp32::registerUartQuietSessionControl(nullptr,transitionCompanionUartQuietSession);
}
'''

SCENARIOS = r'''
int main(int argc,char**) {
  setup();Board board;char reply[160]{};
  using mesh::esp32::UartQuietAction;
  using mesh::esp32::UartQuietSessionAction;
  select(usb_serial_interface,Serial);
  const uint32_t original_count=usb_serial_interface.getCompletedFrameCount();
  the_mesh.stream_route=the_mesh.pending_route=&usb_serial_interface;
  interface_manager.lockReplyRoute();
  mesh::reader_owner=&Serial;
  // Refused readiness cannot partly retire the live host or its producers.
  usb_protocol_initialized=false;
  assert(!transitionCompanionUartQuietSession(nullptr,UartQuietSessionAction::Park));
  usb_protocol_initialized=true;usb_mota_mode=true;
  assert(!transitionCompanionUartQuietSession(nullptr,UartQuietSessionAction::Park));
  usb_mota_mode=false;mesh::ota::OtaContext::serialFolderSource().pending=true;
  assert(!transitionCompanionUartQuietSession(nullptr,UartQuietSessionAction::Park));
  mesh::ota::OtaContext::serialFolderSource().pending=false;
  terminal_output_pending=true;
  assert(!transitionCompanionUartQuietSession(nullptr,UartQuietSessionAction::Park));
  terminal_output_pending=false;
  assert(the_mesh.cancelled_usb==0&&mesh::reader_owner==&Serial&&usb_serial_interface.isEnabled());
  // Cancelling an armed request is not a host-session disconnect.
  assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
  assert(board.handleUartQuiet(UartQuietAction::Cancel,0,reply,sizeof(reply)));
  assert(the_mesh.cancelled_usb==0&&usb_serial_interface.isEnabled()&&Serial.ends==0);
  // A queued ACK must drain before either the session or UART is parked.
  Serial.write_capacity=0;const uint8_t ack=0;
  assert(interface_manager.writeFrameToRoute(&usb_serial_interface,&ack,1)==1);
  assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
  g_mock_millis+=100;board.servicePowerTestControls(!interface_manager.hasPendingIO());
  assert(Serial.ends==0&&the_mesh.cancelled_usb==0&&interface_manager.hasPendingIO());
  Serial.write_capacity=4096;interface_manager.loop();
  assert(!interface_manager.hasPendingIO()&&Serial.output==Bytes({'>',1,0,0}));
  board.servicePowerTestControls(!interface_manager.hasPendingIO());
  assert(!Serial&&Serial.ends==1&&Serial.flushes==1);
  if(argc>1) {
    // Negative control: the old board ended the driver without parking its
    // still-assumed-connected interface. A real RX queues MSG_WAITING forever.
    assert(usb_serial_interface.isEnabled());receive(0);
    assert(the_mesh.offline_queue_len==1&&interface_manager.hasPendingIO());
    assert(the_mesh.serialVeto()==mesh::powerdiag::mask(mesh::powerdiag::Reason::SerialIO));
    puts("old UART quiet retained MSG_WAITING serial veto");return 42;
  }
  assert(!usb_serial_interface.isEnabled()&&!interface_manager.isConnected());
  assert(the_mesh.cancelled_usb==1&&the_mesh.cancelled_other==0);
  assert(mesh::reader_owner==nullptr);
  assert(!the_mesh.stream_route&&!the_mesh.pending_route&&!interface_manager.captureReplyRoute());
  assert(!the_mesh.stale_input&&!usb_binary_startup_probe.isActive());
  assert(!interface_manager.isReplyRouteAvailable(&usb_serial_interface));
  // The application's actual RX/inbox method retains every packet while no
  // transport is listening, with no unserviceable MSG_WAITING frame queued.
  for(unsigned i=0;i<40;++i) {
    receive(i);interface_manager.loop();
    assert(the_mesh.offline_queue_len==int(i+1));
    assert(!interface_manager.hasPendingIO()&&the_mesh.serialVeto()==0);
  }
  const Bytes quiet_output=Serial.output;
  the_mesh.syncNextOfflineMessage();assert(the_mesh.offline_queue_len==40);
  // Another transport's real queued work keeps the original serial veto.
  // Nothing about quiet mode grants it an exception or clears its session.
  other_interface.enable();other_stream.write_capacity=0;
  the_mesh.pending_route=the_mesh.stream_route=&other_interface;
  assert(interface_manager.writeFrameToRoute(&other_interface,&ack,1)==1);
  assert(interface_manager.hasPendingIO());
  assert(the_mesh.serialVeto()==mesh::powerdiag::mask(mesh::powerdiag::Reason::SerialIO));
  assert(!transitionCompanionUartQuietSession(nullptr,UartQuietSessionAction::Resume));
  assert(!usb_serial_interface.isEnabled()&&the_mesh.pending_route==&other_interface);
  other_stream.write_capacity=4096;interface_manager.loop();
  assert(!interface_manager.hasPendingIO()&&the_mesh.serialVeto()==0);
  other_interface.disable();
  // A failed driver restart retains the parked session and all inbox data;
  // the timer retries even when manual CPU sleep has been turned off.
  mesh::esp32::powerTestControls().sleep_enabled=false;Serial.fail_begins=1;
  g_mock_millis+=5000;board.servicePowerTestControls();
  assert(!Serial&&!usb_serial_interface.isEnabled()&&board.uart_quiet_restore_at);
  assert(the_mesh.offline_queue_len==40&&the_mesh.ascii_starts==0);
  g_mock_millis+=999;board.servicePowerTestControls();assert(!Serial);
  ++g_mock_millis;board.servicePowerTestControls();
  assert(Serial&&usb_serial_interface.isEnabled()&&!board.uart_quiet_restore_at);
  assert(the_mesh.ascii_starts==1&&!the_mesh.last_banner&&the_mesh.terminal);
  assert(usb_serial_interface.getCompletedFrameCount()==original_count);
  assert(Serial.output==quiet_output&&the_mesh.offline_queue_len==40);
  assert(the_mesh.pending_route==&other_interface&&the_mesh.stream_route==&other_interface);
  assert(!other_interface.isEnabled()&&the_mesh.cancelled_other==0);
  // Fresh Binary ownership can download the original inbox once, in order.
  leaveUsbTerminalMode(false);select(usb_serial_interface,Serial);
  for(unsigned i=0;i<40;++i)the_mesh.syncNextOfflineMessage();
  assert(the_mesh.offline_queue_len==0);
  size_t at=quiet_output.size();
  for(unsigned i=0;i<40;++i) {
    assert(Serial.output.at(at)=='>');size_t length=Serial.output.at(at+1)|(size_t(Serial.output.at(at+2))<<8);
    at+=3;assert(length==13&&Serial.output.at(at)==27&&Serial.output.at(at+9)==i);at+=length;
  }
  assert(at==Serial.output.size());
  // A pre-disabled interface must stay disabled after the timed recovery.
  usb_serial_interface.disable();const unsigned starts=the_mesh.ascii_starts;
  mesh::reader_owner=&other_stream;
  assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
  g_mock_millis+=100;board.servicePowerTestControls();assert(!Serial);
  g_mock_millis+=5000;board.servicePowerTestControls();
  assert(Serial&&!usb_serial_interface.isEnabled()&&the_mesh.ascii_starts==starts);
  assert(mesh::reader_owner==&other_stream);
  // A live network terminal/Reader retains its owner when USB comes back.
  usb_serial_interface.enable();the_mesh.network=true;
  assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
  g_mock_millis+=100;board.servicePowerTestControls();assert(!Serial);
  g_mock_millis+=5000;board.servicePowerTestControls();
  assert(Serial&&usb_serial_interface.isEnabled()&&the_mesh.ascii_starts==starts);
  assert(mesh::reader_owner==&other_stream&&the_mesh.network);
  // Active USB ASCII ownership is retired too. A subsequent global manager
  // shutdown must not be reversed merely because the UART recovery is due.
  the_mesh.network=false;serviceUsbAsciiSessionDefault();
  assert(the_mesh.terminal);mesh::reader_owner=&Serial;
  assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
  g_mock_millis+=100;board.servicePowerTestControls();
  assert(!Serial&&!the_mesh.terminal&&!usb_serial_interface.isEnabled()&&mesh::reader_owner==nullptr);
  interface_manager.disable();g_mock_millis+=5000;board.servicePowerTestControls();
  assert(Serial&&!interface_manager.isEnabled()&&!usb_serial_interface.isEnabled());
  puts("parked UART retained forty real RX inbox frames without bypassing serial guards");
}
'''


def harness(legacy=False):
    source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
    main = (ROOT / "examples/companion_radio/main.cpp").read_text()
    header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
    native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
    reader = (ROOT / "src/helpers/CompanionReader.cpp").read_text()
    constants = "\n".join(re.findall(
        r"^#define (?:RESP_CODE_(?:CHANNEL_MSG_RECV|CHANNEL_MSG_RECV_V3|CHANNEL_DATA_RECV|NO_MORE_MESSAGES)|PUSH_CODE_MSG_WAITING|MAX_CHANNEL_DATA_LENGTH)\s+.+$",
        source, re.MULTILINE))
    inbox = "\n".join(extract_braced(source, signature) for signature in (
        "bool MyMesh::Frame::isChannelMsg() const", "int MyMesh::getOfflineQueueCapacity() const",
        "MyMesh::Frame& MyMesh::offlineQueueFrameAt(", "bool MyMesh::addToOfflineQueue(",
        "int MyMesh::peekOfflineQueue(", "void MyMesh::popOfflineQueue(",
        "void MyMesh::syncNextOfflineMessage(", "void MyMesh::onChannelDataRecv("))
    session = "\n".join(extract_braced(main, signature) for signature in (
        "static void cancelUsbSerialOperations()", "static void enterUsbTerminalMode(",
        "static void leaveUsbTerminalMode(", "static void serviceUsbAsciiSessionDefault()",
        "static void beginUsbDefaultSession()", "static bool transitionCompanionUartQuietSession("))
    board = "\n".join(board_method(signature) for signature in (
        "bool handleUartQuiet(", "void servicePowerTestControls("))
    if legacy:
        park = "    if (!mesh::esp32::transitionUartQuietSession(\n            mesh::esp32::UartQuietSessionAction::Park)) return;\n"
        assert board.count(park) == 1
        board = board.replace(park, "", 1)
    guard = re.search(r"  if \(_serial != NULL && _serial->hasPendingIO\(\)\) return mask\(Reason::SerialIO\);", source)
    assert guard is not None
    result = PRELUDE
    for marker, value in {"@CONSTANTS@": constants, "@BUFFER@": extract_braced(native, "class BufferStream") + ";",
                          "@FRAME@": extract_braced(header, "struct Frame {") + ";", "@INBOX@": inbox,
                          "@SERIAL_GUARD@": guard.group(), "@SESSION@": session, "@BOARD@": board,
                          "@READER_CANCEL@": extract_braced(reader, "void cancelReaderCommand(")}.items():
        result = result.replace(marker, value)
    return result + SCENARIOS


class UartQuietSessionTests(unittest.TestCase):
    def test_actual_rx_queue_and_transport_lifecycle_with_old_negative_control(self):
        with tempfile.TemporaryDirectory(prefix="mesh-uart-quiet-session-") as directory:
            folder = Path(directory)
            for legacy in (True, False):
                with self.subTest(legacy=legacy):
                    cpp, binary = folder / "test.cpp", folder / "test"
                    cpp.write_text(harness(legacy), encoding="ascii")
                    built = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-sign-compare",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                        "-DESP32_PLATFORM=1", "-DARDUINO_USB_CDC_ON_BOOT=0",
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"), str(cpp),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"), "-o", str(binary)],
                        text=True, capture_output=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    ran = subprocess.run([str(binary)] + (["legacy"] if legacy else []),
                                         text=True, capture_output=True, timeout=20)
                    self.assertEqual(ran.returncode, 42 if legacy else 0, ran.stdout + ran.stderr)
                    self.assertIn("MSG_WAITING serial veto" if legacy else "forty real RX inbox frames", ran.stdout)

    def test_hook_is_registered_only_for_default_esp32_uart(self):
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        pattern = r"#if defined\(ESP32_PLATFORM\) \\\n    && \(!defined\(ARDUINO_USB_CDC_ON_BOOT\) \|\| !ARDUINO_USB_CDC_ON_BOOT\) \\\n    && !defined\(SERIAL_RX\) && !defined\(SERIAL_TX\)\n"
        self.assertEqual(len(re.findall(pattern, source)), 2)
        start = source.index("  mesh::esp32::registerUartQuietSessionControl(")
        prefix = source[:start]
        block = prefix[prefix.rfind("#if defined(ESP32_PLATFORM)"):] + source[start:source.index("#endif", start) + 6]
        for defines, expected in ((["ESP32_PLATFORM=1"], True), (["NRF52_PLATFORM=1"], False),
                (["ESP32_PLATFORM=1", "ARDUINO_USB_CDC_ON_BOOT=1"], False),
                (["ESP32_PLATFORM=1", "SERIAL_RX=1"], False), (["ESP32_PLATFORM=1", "SERIAL_TX=1"], False)):
            with self.subTest(defines=defines):
                result = subprocess.run([os.environ.get("CXX", "c++"), "-E", "-P", "-x", "c++",
                    *["-D" + value for value in defines], "-"], input=block, text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("registerUartQuietSessionControl" in result.stdout, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
