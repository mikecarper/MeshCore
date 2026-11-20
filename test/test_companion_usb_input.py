#!/usr/bin/env python3
"""Run actual Companion ASCII/probe and mOTA input handlers on host streams."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "examples/companion_radio/main.cpp").read_text()
CONTEXT = (ROOT / "src/helpers/ota/OtaContext.h").read_text()


HARNESS = r'''
#define MESH_ENABLE_HOST_KISS 0
#include "helpers/HostKiss.h"
#include <cassert>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
#include <vector>
#include "helpers/ArduinoSerialInterface.h"
#include "helpers/ota/MotaSourceSerial.h"
#include "helpers/UsbAsciiBinarySwitch.h"
#include "helpers/CLICommandUtils.h"
#define MAX_TRANS_UNIT @MAX_TRANS_UNIT@
#define COMPANION_FEATURE_USB_MOTA_SOURCE 1
#define COMPANION_FEATURE_NETWORK_TERMINAL 0
#define MESH_USB_LOGGING_AVAILABLE 1
struct Input : Stream {
  std::deque<uint8_t> bytes;
  std::string output;
  std::deque<std::vector<uint8_t>> responses;
  unsigned source_requests=0;
  bool replenish=false;
  uint32_t advance_per_read=0;
  unsigned reads=0;
  unsigned rx_dropped=0;
  int available() override { return static_cast<int>(bytes.size()); }
  int read() override {
    if(bytes.empty()) return -1;
    uint8_t result=bytes.front(); bytes.pop_front(); ++reads;
    g_mock_millis+=advance_per_read;
    if(replenish) bytes.push_back('x');
    return result;
  }
  int peek() override { return bytes.empty()?-1:bytes.front(); }
  int availableForWrite() override { return 4096; }
  size_t write(uint8_t value) override { output+=char(value); return 1; }
  size_t write(const uint8_t* p,size_t n) override {
    if(n>=4 && p[0]=='M' && p[1]=='S') {
      ++source_requests;
      if(!responses.empty()) {
        for(uint8_t value:responses.front()) bytes.push_back(value);
        responses.pop_front();
      }
    }
    output.append(reinterpret_cast<const char*>(p),n); return n;
  }
  void push(const char* p) { while(*p) bytes.push_back(uint8_t(*p++)); }
  // The real HWCDC ISR reads a 64-byte FIFO packet, then abandons its
  // remaining bytes when xQueueSendFromISR finds the RX queue full.
  void hwcdcBurst(const std::string& wire,size_t capacity) {
    for(size_t start=0;start<wire.size();start+=64) {
      const size_t end=std::min(start+64,wire.size());
      for(size_t i=start;i<end;++i) {
        if(bytes.size()==capacity) { rx_dropped+=end-i; break; }
        bytes.push_back(uint8_t(wire[i]));
      }
    }
  }
  void frame(uint8_t tail=3) {
    const uint8_t frame[]={'<',2,0,0x16,tail};
    for(uint8_t c : frame) bytes.push_back(c);
  }
} input;
#define OTA_FOLDER_SERIAL_STREAM input
#define OTA_FOLDER_SERIAL_WRITE_POLICY mesh::ota::MotaStreamWritePolicy::NoFlush
namespace mesh { namespace ota {
struct OtaContext {
@SOURCE_ACCESSOR@
};
} }
ArduinoSerialInterface usb_serial_interface;
static Stream& usbTerminalOutput() { return input; }
static bool connected=true, logging=false, dedicated=false;
static std::vector<std::string> stats_commands, console_commands;
namespace mesh {
void noteUsbConsoleCommand(const char* command) { console_commands.emplace_back(command); }
bool isUsbLoggingEnabled() { return logging; }
bool hasDedicatedUsbLoggingPort() { return dedicated; }
void noteUsbLoggingStatsCommand(const char* command) {
  stats_commands.emplace_back(command);
}
Stream& usbCompanionPort() { return input; }
void discardUsbTerminalOutput() {}
}
struct Mesh {
  bool terminal=false, waiting=false, attach_succeeds=true;
  bool attach_with_source=false;
  unsigned banners=0, attaches=0, detaches=0;
  std::vector<std::string> commands;
  bool isTerminalMode() const { return terminal; }
  bool isTerminalWaitingForInput() const { return waiting; }
  void enterTerminalMode(bool banner) {
    terminal=true; waiting=!banner;
    if(banner) { ++banners; input.output+="BANNER"; }
  }
  void exitTerminalMode() { terminal=false; waiting=false; }
  void resetUsbHostSessionInput() {}
  void handleTerminalCommand(char* command) { commands.emplace_back(command); }
  bool handleLocalControlCommand(const char* command,char* reply,size_t size) {
    if(!strcmp(command,"ota folder on")) {
      ++attaches;
      if(attach_with_source) {
        auto& source=mesh::ota::OtaContext::serialFolderSource();
        mesh::ota::MotaDesc desc;
        attach_succeeds=source.count()!=0 && source.describe(0,desc);
      }
      snprintf(reply,size,"%s",attach_succeeds?"OK attached":"ERR attach failed");
    } else {
      assert(!strcmp(command,"ota folder off")); ++detaches;
      snprintf(reply,size,"OK detached");
    }
    return true;
  }
} the_mesh;
@TERMINAL_LINE_DECL@
static constexpr size_t hwcdc_rx_capacity=@HWCDC_RX_CAPACITY@;
static size_t usb_terminal_line_len=0;
static bool usb_terminal_discard_line=false;
static bool usb_logging_terminal_mode=false,usb_logging_network_parked=false;
static mesh::UsbBinaryStartupProbe usb_binary_startup_probe;
static mesh::UsbAsciiSessionDefault usb_ascii_session_default;
static bool usb_mota_mode=false,usb_mota_disconnect_armed=false;
static mesh::UsbMotaEntryOrigin usb_mota_entry_origin=mesh::UsbMotaEntryOrigin::BINARY;
static uint8_t usb_mota_stop_matched=0;
static bool usb_mota_discard_line=false;
static const char USB_TERMINAL_START_TOKEN[]="+++MESHCORE-TERM-START";
static const char USB_TERMINAL_STOP_TOKEN[]="+++MESHCORE-TERM-STOP";
static const char USB_MOTA_START_TOKEN[]="ota folder on";
static bool isUsbTerminalDataConnected() { return connected; }
static void redrawUsbTerminalInput() {}
static void serviceUsbLoggingOwnership(bool) {}
static void cancelUsbSerialOperations() {}
static void queueUsbTerminalControlReply(const char* reply) { input.output+=reply; }
static void drainUsbTerminalOutputBeforeProtocolSwitch() {}
@FUNCTIONS@
static void reset() {
  input=Input(); the_mesh=Mesh(); connected=true; logging=dedicated=false;
  stats_commands.clear(); console_commands.clear();
  mesh::ota::OtaContext::serialFolderSource().resetSessionState();
  usb_serial_interface=ArduinoSerialInterface();
  usb_serial_interface.begin(input,USB_TERMINAL_START_TOKEN,USB_MOTA_START_TOKEN);
  usb_serial_interface.enable();
  usb_serial_interface.setReceiveFrameCheck(acceptUsbBinaryStartupFrame);
  usb_logging_terminal_mode=usb_logging_network_parked=false;
  usb_mota_mode=usb_mota_disconnect_armed=usb_mota_discard_line=false;
  usb_mota_stop_matched=0;
  clearUsbTerminalLine(); usb_terminal_discard_line=false;
  g_mock_millis=100;
  enterUsbTerminalMode(false);
}
static void finishProbe() {
  uint8_t frame[MAX_FRAME_SIZE]={};
  assert(usb_serial_interface.checkRecvFrame(frame)==2);
  assert(frame[0]==0x16 && frame[1]==3);
  serviceUsbTerminal();
  assert(!the_mesh.terminal && !usb_serial_interface.isPassthroughMode());
  assert(!usb_binary_startup_probe.isActive());
}
static std::vector<uint8_t> response(uint8_t op,const std::vector<uint8_t>& payload,
                                     uint8_t status=0) {
  std::vector<uint8_t> result={'m','s',op,status};
  result.insert(result.end(),payload.begin(),payload.end());
  uint8_t checksum=0;
  for(uint8_t value:result) checksum^=value;
  result.push_back(checksum);
  return result;
}
int main() {
  for(const char* leading : {"","\r","\n","\r\n","\n\r\n\r"}) {
    reset(); input.push(leading); input.frame();
    serviceUsbTerminal();
    assert(!the_mesh.terminal && usb_binary_startup_probe.isActive());
    assert(input.available()==5 && input.peek()=='<');
    assert(input.output.empty() && the_mesh.banners==0);
    finishProbe();
    assert(console_commands.empty());
  }
  // Empty Enter reveals the silent terminal without fabricating a command.
  for(const char* leading : {"\r","\n","\r\n"}) {
    reset(); input.push(leading); serviceUsbTerminal();
    assert(the_mesh.terminal && !the_mesh.waiting && the_mesh.banners==1);
    assert(!usb_binary_startup_probe.isActive() && input.available()==0);
    assert(the_mesh.commands.empty() && console_commands.empty());
    serviceUsbTerminal(); assert(the_mesh.banners==1);
  }
  reset(); input.push("\r\nget name\r\n"); serviceUsbTerminal();
  assert(the_mesh.banners==1 && the_mesh.commands.size()==1);
  assert(the_mesh.commands[0]=="get name" && the_mesh.terminal);
  assert(console_commands==the_mesh.commands);
  // Reaching an empty prompt through an edit also leaves the frame untouched.
  reset(); input.push("x\b"); input.frame(); serviceUsbTerminal();
  assert(usb_binary_startup_probe.isActive() && input.available()==5);
  finishProbe();
  // A typed prefix and an overlong-line discard cannot become binary probes.
  reset(); input.push("x"); input.frame(); serviceUsbTerminal();
  assert(the_mesh.terminal && !usb_binary_startup_probe.isActive());
  reset(); the_mesh.waiting=false; usb_terminal_discard_line=true;
  input.frame(); serviceUsbTerminal();
  assert(the_mesh.terminal && !usb_binary_startup_probe.isActive());
  assert(usb_terminal_discard_line && usb_terminal_line_len==0);
  // Single-CDC logging forbids frames; a dedicated logger does not block CDC0.
  reset(); logging=true; usb_logging_terminal_mode=true;
  input.push("\r\n"); input.frame(); serviceUsbTerminal();
  assert(the_mesh.terminal && !usb_binary_startup_probe.isActive());
  reset(); logging=dedicated=true; input.push("\r\n"); input.frame();
  serviceUsbTerminal(); assert(usb_binary_startup_probe.isActive()); finishProbe();
  reset(); usb_logging_network_parked=true; input.frame(); serviceUsbTerminal();
  assert(input.available()==5 && !usb_binary_startup_probe.isActive());
  // NUL is legal inside a Binary frame, not a plaintext-line terminator.
  reset(); input.push("\r\n"); input.frame(0); serviceUsbTerminal();
  uint8_t nul_frame[MAX_FRAME_SIZE]={};
  assert(usb_serial_interface.checkRecvFrame(nul_frame)==2);
  assert(nul_frame[0]==0x16 && nul_frame[1]==0);
  serviceUsbTerminal(); assert(!the_mesh.terminal && !usb_binary_startup_probe.isActive());

  // The gate uses the actual completion timestamp, before returning a command
  // or granting client proof, including delayed mesh work and millis rollover.
  for(uint32_t start : {100u,UINT32_MAX-20}) {
    for(uint32_t elapsed : {999u,1000u,1001u}) {
      reset(); g_mock_millis=start; input.frame(); serviceUsbTerminal();
      assert(usb_binary_startup_probe.isActive());
      g_mock_millis=start+elapsed;
      uint8_t command[MAX_FRAME_SIZE]={};
      const bool accepted=elapsed<1000;
      assert(usb_serial_interface.checkRecvFrame(command)==(accepted?2u:0u));
      assert(usb_serial_interface.hasReceivedFrame()==accepted);
      assert(usb_serial_interface.getCompletedFrameCount()==(accepted?1u:0u));
      assert(usb_serial_interface.getLastFrameMillis()==(accepted?millis():0));
      assert(!usb_serial_interface.isReadBusy());
      serviceUsbTerminal();
      assert(the_mesh.terminal==!accepted && !usb_binary_startup_probe.isActive());
    }
  }
  reset(); input.frame(); serviceUsbTerminal();
  g_mock_millis=1099; expireUsbBinaryStartupProbeBeforeDispatch();
  assert(usb_binary_startup_probe.isActive());
  // Time spent inside mesh.loop after its earlier deadline precheck.
  g_mock_millis=1100;
  uint8_t delayed_command[MAX_FRAME_SIZE]={};
  assert(usb_serial_interface.checkRecvFrame(delayed_command)==0);
  assert(!usb_serial_interface.hasReceivedFrame());
  serviceUsbTerminal(); assert(the_mesh.terminal);
  reset(); input.frame(); serviceUsbTerminal();
  g_mock_millis=1098; input.advance_per_read=1;
  assert(usb_serial_interface.checkRecvFrame(delayed_command)==0);
  assert(millis()==1103 && !usb_serial_interface.hasReceivedFrame());
  input.advance_per_read=0; serviceUsbTerminal(); assert(the_mesh.terminal);
  reset(); input.frame(); serviceUsbTerminal(); g_mock_millis=1099;
  assert(usb_serial_interface.checkRecvFrame(delayed_command)==2);
  assert(!usb_binary_startup_probe.isActive());
  // The first timely frame establishes ownership before the service poll.
  g_mock_millis=1100; input.frame();
  assert(usb_serial_interface.checkRecvFrame(delayed_command)==2);
  assert(usb_serial_interface.getCompletedFrameCount()==2);
  serviceUsbTerminal(); assert(!the_mesh.terminal);
  // Once explicitly selected, newline-free STOP retains its legacy behavior.
  reset(); input.push(USB_TERMINAL_STOP_TOKEN); serviceUsbTerminal();
  assert(!the_mesh.terminal && !usb_binary_startup_probe.isActive());
  g_mock_millis+=1001; input.frame();
  assert(usb_serial_interface.checkRecvFrame(delayed_command)==2);

  // Text-first attach, clean exit, and the remaining CRLF cannot corrupt a
  // immediately following framed client command.
  reset(); input.push("ota folder on\r\n"); serviceUsbTerminal();
  assert(usb_mota_mode && usb_mota_entry_origin==mesh::UsbMotaEntryOrigin::ASCII);
  assert(!the_mesh.terminal && the_mesh.attaches==1);
  input.push("ota folder off\r\n"); input.frame(); serviceUsbTerminal();
  assert(!usb_mota_mode && the_mesh.terminal && the_mesh.waiting);
  assert(the_mesh.detaches==1);
  input.output.clear(); serviceUsbTerminal();
  assert(usb_binary_startup_probe.isActive() && input.available()==5);
  assert(input.output.empty()); finishProbe();

  // Failed ASCII entry stays ASCII; failed Binary entry stays Binary.
  reset(); the_mesh.attach_succeeds=false;
  input.push("ota folder on\r\n"); serviceUsbTerminal();
  assert(!usb_mota_mode && the_mesh.terminal && the_mesh.banners==2);
  assert(usb_serial_interface.isPassthroughMode() && !usb_mota_discard_line);
  reset(); leaveUsbTerminalMode(false); the_mesh.attach_succeeds=false;
  input.push("ota folder on\r\n"); uint8_t frame[MAX_FRAME_SIZE]={};
  assert(usb_serial_interface.checkRecvFrame(frame)==0);
  serviceUsbTerminal();
  assert(!usb_mota_mode && !the_mesh.terminal && !usb_serial_interface.isPassthroughMode());
  assert(!usb_mota_discard_line);

  // Every NUL position rejects the whole ASCII owner-transition/mutating
  // line, then the next real command is handled normally.
  for(const char* command : {"ota folder on","ota folder off","set usb.logging on","get stats"}) {
    for(size_t position=0;position<=strlen(command);++position) {
      reset();
      for(size_t i=0;i<position;++i) input.bytes.push_back(command[i]);
      input.bytes.push_back(0);
      input.push(command+position); input.push("\r\nget name\r\n");
      serviceUsbTerminal();
      assert(!usb_mota_mode && the_mesh.attaches==0 && the_mesh.detaches==0);
      assert(the_mesh.commands.size()==1 && the_mesh.commands[0]=="get name");
      assert(stats_commands==the_mesh.commands && console_commands==the_mesh.commands);
      const auto error=input.output.find("ERROR: invalid command");
      assert(error!=std::string::npos);
      assert(input.output.find("ERROR: invalid command",error+1)==std::string::npos);
    }
  }
  reset(); input.push("ota fol"); input.bytes.push_back(0); serviceUsbTerminal();
  assert(usb_terminal_discard_line && usb_terminal_line_len==0 && the_mesh.attaches==0);
  input.push("der on\r\nget name\r\n"); serviceUsbTerminal();
  assert(the_mesh.commands.size()==1 && the_mesh.commands[0]=="get name");
  assert(!usb_terminal_discard_line && !usb_mota_mode && the_mesh.attaches==0);

  // Reproduce the hardware failure with the SDK's old 256-byte queue: the
  // oversized setter loses its CR before reaching the actual line limit.
  // A later ordinary Enter would dispatch a truncated mutating command.
  const std::string oversized_setter="set name "+std::string(600,'X')+"\r";
  assert(oversized_setter.size()==610);
  reset(); input.hwcdcBurst(oversized_setter,256); serviceUsbTerminal();
  assert(input.rx_dropped==354 && input.output.size()==256+6); // Silent banner is revealed.
  assert(the_mesh.commands.empty() && usb_terminal_line_len==256 && console_commands.empty());
  assert(!usb_terminal_discard_line && input.output.find("ERROR")==std::string::npos);
  input.push("\r"); serviceUsbTerminal();
  assert(the_mesh.commands==std::vector<std::string>{oversized_setter.substr(0,256)});

  // The configured pre-begin RX queue preserves the complete unpaced burst.
  // Production parsing rejects it and its delimiter produces the real prompt.
  static_assert(hwcdc_rx_capacity>sizeof(usb_terminal_line));
  reset(); input.hwcdcBurst(oversized_setter,hwcdc_rx_capacity); serviceUsbTerminal();
  assert(!input.rx_dropped && input.available()==0 && the_mesh.commands.empty());
  assert(!usb_terminal_discard_line && !usb_terminal_line_len);
  assert(input.output.find("ERROR: command too long")!=std::string::npos);
  assert(input.output.substr(input.output.size()-2)=="> ");
  input.push("get name\r"); serviceUsbTerminal();
  assert(the_mesh.commands==std::vector<std::string>{"get name"});

  // Even a single line exceeding the RX queue reaches discard mode before
  // any CR is lost. Recovery Enter cannot execute its retained setter prefix.
  // Hosts must still use request/reply rather than overflow a multi-line batch.
  for(size_t size : {hwcdc_rx_capacity+64,hwcdc_rx_capacity*4}) {
    reset();
    input.hwcdcBurst("set name "+std::string(size,'X')+"\r",hwcdc_rx_capacity);
    serviceUsbTerminal();
    assert(input.rx_dropped && input.available()==0 && the_mesh.commands.empty());
    assert(usb_terminal_discard_line && !usb_terminal_line_len);
    assert(input.output.find("ERROR: command too long")!=std::string::npos);
    input.push("\rget name\r"); serviceUsbTerminal();
    assert(!usb_terminal_discard_line && the_mesh.commands==std::vector<std::string>{"get name"});
  }

  // Embedded NUL rejection remains whole-line with the real HWCDC burst path.
  reset();
  std::string nul_setter="set name MUST-NOT-CHANGE";
  nul_setter.push_back(0); nul_setter+="ignored\r";
  input.hwcdcBurst(nul_setter,hwcdc_rx_capacity); serviceUsbTerminal();
  assert(!input.rx_dropped && the_mesh.commands.empty() && !usb_terminal_discard_line);
  assert(input.output.find("ERROR: invalid command")!=std::string::npos);
  assert(input.output.substr(input.output.size()-2)=="> ");

  // Every accepted byte, UTF-8 erase, and invalid-line discard preserves the
  // terminated-length invariant; only complete valid lines prove a reader.
  reset();
  const uint8_t edited[]={'g','e','t',' ','s','t','a','t','s',0xc3,0xa9,0x7f,'\r','\n'};
  for(uint8_t byte : edited) {
    input.bytes.push_back(byte); serviceUsbTerminal();
    assert(strlen(usb_terminal_line)==usb_terminal_line_len);
  }
  assert(the_mesh.commands==std::vector<std::string>{"get stats"});
  assert(stats_commands==the_mesh.commands);
  reset(); dedicated=true; input.push("get stats\r\n"); serviceUsbTerminal();
  assert(the_mesh.commands==std::vector<std::string>{"get stats"});
  assert(stats_commands.empty());

  // Overlong lines never accept their suffix as an independent shutdown.
  for(unsigned length : {32u,40u,4096u}) {
    reset(); leaveUsbTerminalMode(false);
    assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::ASCII));
    for(unsigned i=0;i<length;++i) input.bytes.push_back('x');
    input.push("ota folder off\r\n"); serviceUsbMota();
    assert(usb_mota_mode && the_mesh.detaches==0);
    assert(!usb_mota_discard_line && usb_mota_stop_matched==0);
    input.push("ota folder off\r\n"); serviceUsbMota();
    assert(!usb_mota_mode && the_mesh.detaches==1 && the_mesh.terminal);
  }
  // Discard survives arbitrary read boundaries, but reset/entry clear it.
  reset(); leaveUsbTerminalMode(false);
  assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
  input.push("ota folder "); serviceUsbMota();
  assert(usb_mota_stop_matched==11 && !usb_mota_discard_line);
  input.push("xota folder off"); serviceUsbMota();
  assert(usb_mota_discard_line && usb_mota_mode && the_mesh.detaches==0);
  input.push("\n"); serviceUsbMota(); assert(!usb_mota_discard_line);
  input.push("ota folder off\r\n"); serviceUsbMota();
  assert(!usb_mota_mode && !the_mesh.terminal && !usb_serial_interface.isPassthroughMode());
  usb_mota_discard_line=true; resetUsbMotaMode(); assert(!usb_mota_discard_line);
  usb_mota_discard_line=true;
  assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
  assert(!usb_mota_discard_line);
  // mOTA NUL-containing shutdown strings must not truncate into an exact
  // owner command. Rejecting them is silent and preserves the source.
  for(const char* command : {"ota folder on","ota folder off"}) {
    for(size_t position=0;position<=strlen(command);++position) {
      reset(); leaveUsbTerminalMode(false);
      assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::ASCII));
      input.output.clear();
      for(size_t i=0;i<position;++i) input.bytes.push_back(command[i]);
      input.bytes.push_back(0);
      input.push(command+position); input.push("\r\n"); serviceUsbMota();
      assert(usb_mota_mode && the_mesh.detaches==0 && input.output.empty());
      input.push("ota folder off\r\n"); serviceUsbMota();
      assert(!usb_mota_mode && the_mesh.detaches==1 && the_mesh.terminal);
    }
  }
  reset(); leaveUsbTerminalMode(false);
  assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
  input.push("ota folder off"); input.bytes.push_back(0); serviceUsbMota();
  assert(usb_mota_discard_line && usb_mota_stop_matched==0 && the_mesh.detaches==0);
  input.push("suffix\n"); serviceUsbMota();
  assert(!usb_mota_discard_line && usb_mota_mode && the_mesh.detaches==0);
  input.push("ota folder off\r\n"); serviceUsbMota();
  assert(!usb_mota_mode && the_mesh.detaches==1 && !the_mesh.terminal);
  // Continually arriving data cannot make a single mOTA control poll unbounded.
  reset(); leaveUsbTerminalMode(false);
  assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
  input.bytes={'x','x','x','x'}; input.replenish=true;
  const unsigned before=input.reads; serviceUsbMota();
  assert(input.reads==before+4 && input.available()==4);
  // The ordinary ASCII handler yields too, including while discarding an
  // overlong command from a host that never stops writing.
  reset(); input.bytes={'x','x','x','x'}; input.replenish=true;
  for(unsigned pass=0;pass<sizeof(usb_terminal_line)/4+2;++pass) {
    const unsigned before=input.reads; serviceUsbTerminal();
    assert(input.reads==before+4 && input.available()==4);
  }
  assert(usb_terminal_discard_line && the_mesh.commands.empty());
  input.replenish=false; input.push("\nget name\r\n"); serviceUsbTerminal();
  assert(!usb_terminal_discard_line && the_mesh.commands.size()==1);
  assert(the_mesh.commands[0]=="get name");

  // A real timed-out READ reply can contain shutdown text as arbitrary image
  // bytes. No prefix, header, payload or checksum boundary may expose it.
  const char dangerous[]="x\nota folder off\n";
  const auto late_read=response(3,std::vector<uint8_t>(dangerous,dangerous+sizeof(dangerous)-1));
  for(size_t prefix=0;prefix<late_read.size();++prefix) {
    reset(); leaveUsbTerminalMode(false);
    assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::ASCII));
    auto& source=mesh::ota::OtaContext::serialFolderSource();
    input.responses.emplace_back(late_read.begin(),late_read.begin()+prefix);
    uint8_t data[sizeof(dangerous)-1]={};
    assert(!source.read(0,0,data,sizeof(data)) && source.hasPendingResponse());
    const unsigned requests=input.source_requests;
    for(unsigned retry=0;retry<3;++retry) {
      assert(!source.read(0,0,data,1));
      assert(input.source_requests==requests && source.hasPendingResponse());
    }
    // Drain fragments through both txn's stale pass and the control consumer.
    for(size_t i=prefix;i<late_read.size();++i) {
      input.bytes.push_back(late_read[i]);
      if(i+1<late_read.size() && i%2==0) {
        assert(!source.read(0,0,data,1));
        assert(input.source_requests==requests);
      } else serviceUsbMota();
      assert(usb_mota_mode && the_mesh.detaches==0);
    }
    assert(!source.hasPendingResponse());
    input.push("ota folder off\r\n"); serviceUsbMota();
    assert(!usb_mota_mode && the_mesh.detaches==1 && the_mesh.terminal);
  }
  // Successful response followed immediately by an ordinary shutdown retains
  // both its payload and the exact control bytes at the proven wire boundary.
  reset(); leaveUsbTerminalMode(false);
  assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::ASCII));
  auto good_read=response(3,{42});
  const char off[]="ota folder off\r\n";
  good_read.insert(good_read.end(),off,off+sizeof(off)-1);
  input.responses.push_back(good_read);
  uint8_t good_byte=0;
  assert(mesh::ota::OtaContext::serialFolderSource().read(0,0,&good_byte,1));
  assert(good_byte==42); serviceUsbMota();
  assert(!usb_mota_mode && the_mesh.detaches==1 && the_mesh.terminal);

  // Failed ASCII and Binary attach must quarantine a late COUNT/DESCRIBE
  // frame through every partial boundary, not execute its text/frame payload.
  std::vector<uint8_t> desc(38,'x');
  const char mutation[]="\nset usb.logging on\n";
  std::copy(mutation,mutation+sizeof(mutation)-1,desc.begin()+2);
  const uint8_t embedded_frame[]={'<',2,0,0x16,3};
  std::copy(embedded_frame,embedded_frame+sizeof(embedded_frame),desc.begin()+25);
  for(bool ascii : {false,true}) {
    for(bool describe : {false,true}) {
      const auto delayed=describe?response(2,desc):response(1,{1});
      for(size_t prefix=0;prefix<delayed.size();++prefix) {
        reset(); the_mesh.attach_with_source=true;
        if(describe) input.responses.push_back(response(1,{1}));
        input.responses.emplace_back(delayed.begin(),delayed.begin()+prefix);
        if(!ascii) {
          leaveUsbTerminalMode(false);
          assert(!enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
        } else {
          input.push("ota folder on\r\n"); serviceUsbTerminal();
        }
        auto& source=mesh::ota::OtaContext::serialFolderSource();
        assert(!usb_mota_mode && source.hasPendingResponse());
        assert(the_mesh.terminal==ascii && usb_serial_interface.isPassthroughMode());
        const unsigned requests=input.source_requests;
        assert(!enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
        assert(source.hasPendingResponse() && input.source_requests==requests);
        uint8_t command[MAX_FRAME_SIZE]={};
        for(size_t i=prefix;i<delayed.size();++i) {
          input.bytes.push_back(delayed[i]);
          assert(usb_serial_interface.checkRecvFrame(command)==0);
          serviceUsbTerminal();
          assert(the_mesh.commands.empty() && !usb_serial_interface.hasReceivedFrame());
        }
        assert(!source.hasPendingResponse());
        assert(usb_serial_interface.isPassthroughMode()==ascii);
        if(ascii) {
          input.push("get name\r\n"); serviceUsbTerminal();
          assert(the_mesh.commands.size()==1 && the_mesh.commands[0]=="get name");
        } else {
          input.frame(); assert(usb_serial_interface.checkRecvFrame(command)==2);
        }
      }
    }
  }
  // A partial payload is deliberately ambiguous: ordinary input cannot end
  // quarantine. A physical session reset does clear the retained transaction.
  reset(); the_mesh.attach_with_source=true;
  input.responses.push_back(response(1,{1}));
  const auto delayed_desc=response(2,desc);
  input.responses.emplace_back(delayed_desc.begin(),delayed_desc.begin()+5);
  input.push("ota folder on\r\n"); serviceUsbTerminal();
  auto& waiting_source=mesh::ota::OtaContext::serialFolderSource();
  assert(waiting_source.hasPendingResponse());
  input.push("get name\r\n"); serviceUsbTerminal();
  assert(the_mesh.commands.empty() && waiting_source.hasPendingResponse());
  input.bytes.clear(); // The transport purges the old session's bytes.
  resetUsbTerminalHostSession();
  assert(!waiting_source.hasPendingResponse() && !usb_serial_interface.hasReceivedFrame());
  // A real software handoff preserves the outstanding frame and restores the
  // chosen terminal/logging/network ownership only after its final checksum.
  for(bool terminal : {false,true}) {
    for(bool shared_logging : {false,true}) {
      for(bool parked : {false,true}) {
        reset(); leaveUsbTerminalMode(false);
        assert(enterUsbMotaMode(mesh::UsbMotaEntryOrigin::BINARY));
        auto& source=mesh::ota::OtaContext::serialFolderSource();
        input.responses.emplace_back(late_read.begin(),late_read.begin()+5);
        uint8_t data[sizeof(dangerous)-1]={};
        assert(!source.read(0,0,data,sizeof(data)));
        leaveUsbMotaMode(false);
        assert(source.hasPendingResponse() && usb_serial_interface.isPassthroughMode());
        if(terminal) enterUsbTerminalMode(false);
        logging=true; dedicated=!shared_logging;
        usb_logging_network_parked=parked;
        for(size_t i=5;i<late_read.size();++i) input.bytes.push_back(late_read[i]);
        assert(serviceUsbMotaResponseBoundary());
        assert(!source.hasPendingResponse() && the_mesh.commands.empty());
        assert(the_mesh.terminal==terminal);
        assert(usb_serial_interface.isPassthroughMode()==(terminal || shared_logging || parked));
      }
    }
  }
}
'''


def bind_production_input_constants(harness):
    terminal_line = re.search(r"static char usb_terminal_line\[[^\n]+;", MAIN).group()
    rx_capacity = re.search(r"#define MESH_ESP32_USB_RX_BUFFER_SIZE (\d+)",
                            (ROOT / "src/helpers/UsbLogging.h").read_text()).group(1)
    max_trans_unit = re.search(r"#define MAX_TRANS_UNIT\s+(\d+)",
                               (ROOT / "src/MeshCore.h").read_text()).group(1)
    return (harness.replace("@TERMINAL_LINE_DECL@", terminal_line)
                   .replace("@HWCDC_RX_CAPACITY@", rx_capacity)
                   .replace("@MAX_TRANS_UNIT@", max_trans_unit))


class CompanionUsbInputTests(unittest.TestCase):
    def test_actual_usb_input_state_machines(self):
        functions = "\n".join(extract_braced(MAIN, signature)
                              for signature in (
            "static void clearUsbTerminalLine()",
            "static bool acceptUsbBinaryStartupFrame(uint32_t completed_at)",
            "static void enterUsbTerminalMode(bool show_banner",
            "static void leaveUsbTerminalMode(bool acknowledge)",
            "static void resetUsbMotaMode()",
            "static void leaveUsbMotaMode(bool acknowledge)",
            "static bool enterUsbMotaMode(mesh::UsbMotaEntryOrigin origin)",
            "static void serviceUsbMota()",
            "static bool serviceUsbMotaResponseBoundary()",
            "static void resetUsbTerminalHostSession()",
            "static void serviceUsbTerminal()",
            "static void expireUsbBinaryStartupProbeBeforeDispatch()",
        ))
        # The default is in the forward declaration in production.
        functions = functions.replace("static void enterUsbTerminalMode(bool show_banner)",
                                      "static void enterUsbTerminalMode(bool show_banner = true)")
        accessor = extract_braced(CONTEXT, "static SerialMotaSource& serialFolderSource()")
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "usb-input.cpp"
            cpp.write_text(bind_production_input_constants(
                HARNESS.replace("@FUNCTIONS@", functions)
                       .replace("@SOURCE_ACCESSOR@", accessor)))
            for sanitizer in (False, True):
                with self.subTest(sanitizer=sanitizer):
                    binary = Path(directory) / ("usb-input-" + str(sanitizer))
                    flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                              "-fno-pie", "-no-pie"] if sanitizer else [])
                    build = subprocess.run([
                        "g++", "-std=c++17", *flags,
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        str(cpp), str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"),
                        str(ROOT / "src/helpers/ota/MotaSourceSerial.cpp"),
                        "-o", str(binary)], capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    run = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
