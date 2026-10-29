#include <algorithm>
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionFrameLimits.h>
#include <helpers/CompanionHardwareCommandCompat.h>
#include "../usb_mode_console.h"

struct Board {
#if defined(NRF52_PLATFORM)
  bool known = true;
  std::string version = "0.11.0-OTAFIX2.4.9-preview+build";
  unsigned reads = 0;
  size_t reader_capacity = 0;
  bool getBootloaderVersion(char* output, size_t capacity) {
    ++reads;
    reader_capacity = capacity;
    assert(capacity == 128);
    if (!known) {
      // Unknown never exposes even a faulty reader's partially filled data.
      memset(output, 'F', capacity);
      return false;
    }
    snprintf(output, capacity, "%s", version.c_str());
    return true;
  }
#endif
};
struct BaseSerialInterface { unsigned id; };
struct SerialTransport {
  BaseSerialInterface usb{1}, ble{2};
  BaseSerialInterface* current = &usb;
  BaseSerialInterface* captured = nullptr;
  BaseSerialInterface* written = nullptr;
  std::vector<uint8_t> frame;
  unsigned capture_calls = 0, write_calls = 0;
  BaseSerialInterface* captureReplyRoute() {
    ++capture_calls;
    captured = current;
    return captured;
  }
  size_t writeFrameToRoute(BaseSerialInterface* route, const uint8_t* data, size_t size) {
    ++write_calls;
    written = route;
    frame.assign(data,data+size);
    assert(size <= MAX_FRAME_SIZE);
    return size;
  }
};
struct Output {
  std::string text;
  void printf(const char* format, ...) {
    char bytes[256]; va_list args; va_start(args,format);
    int count = vsnprintf(bytes,sizeof(bytes),format,args); va_end(args);
    assert(count >= 0 && size_t(count) < sizeof(bytes));
    text += bytes;
  }
};
struct MyMesh {
  Board board;
  SerialTransport serial;
  SerialTransport* _serial = &serial;
  Output output;
  char reply_buf[160] = {};
  uint8_t out_frame[MAX_FRAME_SIZE] = {};
  unsigned errors = 0, barrier_begin = 0, barrier_end = 0;
  bool queued = false;
  bool handleLocalControlCommand(const char*,char*,size_t);
  bool handleCommand(const char*,uint32_t,char*);
  void beginUsbLoggingReplyBarrier(BaseSerialInterface* route) {
    assert(route == serial.captured);
    ++barrier_begin;
    // A different transport becoming current cannot steal the reply.
    serial.current = route == &serial.usb ? &serial.ble : &serial.usb;
  }
  void endUsbLoggingReplyBarrier(bool accepted) { ++barrier_end; queued = accepted; }
  void writeErrFrame(uint8_t) { ++errors; }
  Output& terminalOutput() { return output; }
  void frame(uint8_t* cmd_frame, size_t len) {
    constexpr uint8_t ERR_CODE_ILLEGAL_ARG = 1;
    constexpr uint8_t RESP_CODE_CLI_REPLY = 29;
#include "production_companion_frame.inc"
  }
  void terminal(const char* command) {
    char local_reply[160];
    const bool usb_mota_owner_transition = false;
#include "production_companion_terminal.inc"
  }
};
#include "production_companion_getter.inc"

static std::string wanted(const MyMesh& mesh) {
#if defined(NRF52_PLATFORM)
  return mesh.board.known ? "> " + mesh.board.version.substr(0,127) : "> unknown";
#else
  (void)mesh;
  return "Error: unsupported";
#endif
}
static void bounds(MyMesh& mesh) {
  const std::string expected = wanted(mesh);
  for (size_t capacity=1; capacity <= 176; ++capacity) {
    std::vector<unsigned char> guarded(capacity+16,0xa5);
    char* reply=reinterpret_cast<char*>(guarded.data()+8);
    assert(mesh.handleLocalControlCommand("get bootloader.ver",reply,capacity));
    assert(std::string(reply) == expected.substr(0,capacity-1));
    for (size_t i=0;i<8;++i) assert(guarded[i] == 0xa5);
    for (size_t i=8+capacity;i<guarded.size();++i) assert(guarded[i] == 0xa5);
  }
  char reply[160];
  assert(mesh.handleLocalControlCommand("   get bootloader.ver",reply,sizeof(reply)));
  assert(std::string(reply) == expected);
  for (const char* command : {"", "get bootloader", "get bootloader.ver extra",
       "get bootloader.ver ", "get bootloader.verx", "set bootloader.ver 1",
       "GET bootloader.ver", "get\tbootloader.ver", "\tget bootloader.ver"}) {
    memset(reply,0xa5,sizeof(reply));
    assert(!mesh.handleLocalControlCommand(command,reply,sizeof(reply)));
    for (unsigned char byte : reply) assert(byte == 0xa5);
  }
  assert(!mesh.handleLocalControlCommand(nullptr,reply,sizeof(reply)));
  assert(!mesh.handleLocalControlCommand("get bootloader.ver",nullptr,sizeof(reply)));
  assert(!mesh.handleLocalControlCommand("get bootloader.ver",reply,0));
}
static void dispatch(MyMesh& mesh, BaseSerialInterface* requester, bool nul) {
  const std::string command = "AB|get bootloader.ver";
  std::vector<uint8_t> frame{0x42};
  frame.insert(frame.end(),command.begin(),command.end());
  if (nul) frame.push_back(0);
  const size_t len = frame.size(); frame.push_back(0xa5);
  mesh.serial.current=requester;
  const unsigned captures=mesh.serial.capture_calls,writes=mesh.serial.write_calls;
  mesh.frame(frame.data(),len);
  assert(mesh.serial.capture_calls == captures+1 && mesh.serial.write_calls == writes+1);
  assert(mesh.serial.written == requester && mesh.queued);
  assert(mesh.serial.frame[0] == 29);
  assert(std::string(mesh.serial.frame.begin()+1,mesh.serial.frame.end()) == "AB|"+wanted(mesh));
  assert(frame.back() == (nul ? 0xa5 : 0)); // Real CMD42 terminates no-NUL input.
}
int main() {
  MyMesh mesh;
  char usb_reply[160] = {};
  assert(mesh.handleCommand("AB|get usb.mode", 0, usb_reply));
  assert(std::string(usb_reply) == std::string("AB|usb.mode ") + mesh::usbModeName<decltype(Serial)>());
  bounds(mesh);
#if defined(NRF52_PLATFORM)
  assert(mesh.board.reader_capacity == 128);
  mesh.board.version = "0.11.0-OTAFIX2.4.9-";
  mesh.board.version.append(127-mesh.board.version.size(),'x');
  assert(mesh.board.version.size() == 127);
  bounds(mesh);
#endif
  dispatch(mesh,&mesh.serial.usb,false);
  dispatch(mesh,&mesh.serial.ble,true);
  mesh.output.text.clear();mesh.terminal("get bootloader.ver");
  assert(mesh.output.text == "  -> " + wanted(mesh) + "\r\n");
#if defined(NRF52_PLATFORM)
  mesh.board.known = false;
  bounds(mesh);
  dispatch(mesh,&mesh.serial.ble,false);
  assert(mesh.serial.frame.size() == 1+3+strlen("> unknown"));
#endif
  // Embedded NULs must fail before reading the bootloader or publishing a CLI reply.
  uint8_t malformed[] = {0x42,'g','e','t',0,'b',0};
  const unsigned writes=mesh.serial.write_calls,errors=mesh.errors;
  mesh.frame(malformed,sizeof(malformed)-1);
  assert(mesh.errors == errors+1 && mesh.serial.write_calls == writes);
  puts("Companion bootloader getter/dispatch cases passed");
}
