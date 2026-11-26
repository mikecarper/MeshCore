#!/usr/bin/env python3
"""Execute cached USB text guards and Full Companion raw-KISS handoff code."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
ARDUINO = r'''
#pragma once
#include <cstddef>
#include <cstdint>
class Stream {
public:
  virtual ~Stream() = default;
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual void flush() = 0;
  virtual int availableForWrite() { return 0; }
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t*, size_t) = 0;
};
'''

TEXT_FIXTURE = r'''
#include <cassert>
#include <string>
#include <atomic>
#include <helpers/UsbBinaryTextGuard.h>
static std::atomic<bool> usb_binary_stream_owned{false};
static std::atomic<uint32_t> usb_text_writers{0};
static bool logging = true, debug = true;
struct Port : Stream {
  unsigned reads=0, peeks=0, available_calls=0, flushes=0, capacity_calls=0;
  std::string output;
  int available() override { ++available_calls; return 3; }
  int read() override { ++reads; return 65; }
  int peek() override { ++peeks; return 65; }
  void flush() override { ++flushes; }
  int availableForWrite() override { ++capacity_calls; return 99; }
  size_t write(uint8_t value) override { return write(&value,1); }
  size_t write(const uint8_t* data,size_t length) override {
#if MESH_ENABLE_HOST_KISS
    if (!usb_binary_stream_owned.load()) assert(usb_text_writers.load()==1);
#endif
    output.append(reinterpret_cast<const char*>(data),length); return length;
  }
} raw, null_port;
namespace mesh {
bool usbDataLocked() { return false; }
bool isUsbBinaryStreamOwned() { return usb_binary_stream_owned.load(); }
bool isUsbDebugLoggingEnabled() { return !isUsbBinaryStreamOwned() && logging && debug; }
@COUNTERS@
static Stream& null_usb_logging_stream = null_port;
static Stream& rawUsbLoggingPort() { return logging ? static_cast<Stream&>(raw) : null_port; }
static Stream& rawUsbTerminalPort(bool enabled) { return enabled ? static_cast<Stream&>(raw) : null_port; }
static Stream& rawUsbConsolePort() { return raw; }
@PORTS@
}
int main() {
  using namespace mesh;
  auto& cached_log=usbLoggingPort();
  auto& cached_debug=usbDebugPort();
  auto& cached_terminal=usbTerminalPort(true);
  auto& cached_console=usbConsolePort();
  const uint8_t text[]={'a','b','c'};
  assert(cached_log.write(text,3)==3 && raw.output=="abc");
  usb_binary_stream_owned.store(true);
#if MESH_ENABLE_HOST_KISS
  for (auto* cached: {&cached_log,&cached_debug,&cached_terminal,&cached_console}) {
    assert(cached->write(text,3)==3 && cached->write(uint8_t('!'))==1);
    assert(!cached->available() && cached->read()==-1 && cached->peek()==-1);
    assert(!cached->availableForWrite());cached->flush();
  }
  assert(raw.output=="abc" && !raw.reads && !raw.peeks && !raw.available_calls);
  assert(!raw.flushes && !raw.capacity_calls && !canAcceptUsbConsoleCommand());
  // New port lookups must be guarded too, without retargeting cached writers
  // to a binary facade. Binary output uses its separate raw transport.
  assert(usbLoggingPort().write(text,3)==3);
  assert(usbTerminalPort(true).write(text,3)==3);
  assert(usbConsolePort().write(text,3)==3 && raw.output=="abc");
  assert(raw.write(reinterpret_cast<const uint8_t*>("KISS"),4)==4 && raw.output=="abcKISS");
  usb_binary_stream_owned.store(false);
  assert(cached_terminal.write(text,3)==3 && raw.output=="abcKISSabc");
  assert(cached_log.available()==3 && cached_log.read()==65 && cached_log.peek()==65);
  cached_console.flush();assert(cached_console.availableForWrite()==99 && canAcceptUsbConsoleCommand());
  assert(raw.flushes==1 && raw.capacity_calls==1);
#else
  // Feature-disabled images retain the prior raw facade and layouts.
  assert(&cached_log==&raw && &cached_debug==&raw && &cached_terminal==&raw && &cached_console==&raw);
  assert(cached_console.write(text,3)==3 && raw.output=="abcabc");
#endif
}
'''

COMPANION_FIXTURE = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <deque>
static bool kiss_active=false, terminal_mode=false, usb_mota_mode=false, binary_owned=false;
static bool usb_logging_reply_pending=true;
static bool host_kiss_usb_parked=false,host_kiss_usb_restore_enabled=false;
static bool usb_terminal_discard_line=false,usb_logging_terminal_mode=false;
static unsigned serial_cancels=0,route_cancels=0,forgotten_routes=0;
static unsigned input_resets=0,text_discards=0,line_clears=0,reader_cancels=0,mota_resets=0;
static unsigned ascii_calls=0,usb_binary_calls=0,network_calls=0,kiss_resets=0;
static std::deque<uint8_t> input;
struct Port {} terminal_port;
struct Usb {
  bool enabled=true,passthrough=false,receiving=false;
  bool isReceivingFrame()const{return receiving;}
  unsigned resets=0,frames=19,enables=0,disables=0;
  bool isEnabled() const { return enabled; }
  void enable(){enabled=true;++enables;}
  void disable(){enabled=false;++disables;}
  void resetSessionState(){++resets;}
  unsigned getCompletedFrameCount()const{return frames;}
  void setPassthroughMode(bool value){passthrough=value;}
} usb_serial_interface;
static bool ble_stream_pending=true,ble_operations_pending=true;
struct Mesh {
  bool usb_stream_pending=true,usb_operations_pending=true;
  void cancelSerialResponseStream(Usb* p){assert(p==&usb_serial_interface);++serial_cancels;usb_stream_pending=false;}
  void cancelSerialOperationsForRoute(Usb* p){assert(p==&usb_serial_interface);++route_cancels;usb_operations_pending=false;}
  void resetUsbHostSessionInput(){++input_resets;}
  bool isTerminalMode()const{return terminal_mode;}
} the_mesh;
struct Manager {
  bool enabled=true;
  bool isEnabled()const{return enabled;}
  void forgetReplyRouteForDisconnected(Usb* p){assert(p==&usb_serial_interface);++forgotten_routes;}
  void loop(){++network_calls;if(usb_serial_interface.enabled && !input.empty())++usb_binary_calls;}
} interface_manager;
struct SessionProbe {
  unsigned cancels=0,requests=0,last_frame=0;
  void cancel(){++cancels;}
  void request(unsigned frame){++requests;last_frame=frame;}
} usb_binary_startup_probe,usb_ascii_session_default;
void clearUsbTerminalLine(){++line_clears;}
void leaveUsbTerminalMode(bool banner){assert(!banner);terminal_mode=false;}
void leaveUsbMotaMode(bool resume){assert(!resume);usb_mota_mode=false;}
namespace mesh {
Port& usbTerminalPort(){return terminal_port;}
void cancelReaderCommand(Port* p){assert(p==&terminal_port);++reader_cancels;}
void discardUsbTerminalOutput(){++text_discards;}
void setUsbBinaryStreamOwned(bool value){binary_owned=value;}
namespace ota {
struct Source {bool pending=false;bool hasPendingResponse()const{return pending;}void resetSessionState(){++mota_resets;}} source;
struct OtaContext {static Source& serialFolderSource(){return source;}};
}
namespace hostkiss {
bool usbActive(){return kiss_active;}
void resetUsbSession(){++kiss_resets;if(kiss_active){kiss_active=false;binary_owned=false;}}
bool serviceUsb(){
  if(!kiss_active && !input.empty() && input.front()==0xc0){input.pop_front();kiss_active=true;binary_owned=true;}
  bool claimed=kiss_active;
  while(kiss_active && !input.empty()){
    auto byte=input.front();input.pop_front();
    if(byte==0xff){kiss_active=false;binary_owned=false;break;}
  }
  return claimed;
}
}
}
@CANCEL@
@RESET@
@SERVICE@
static bool poll(){
  bool claimed=serviceCompanionHostKissUsb();
  if(!claimed && !input.empty()){++ascii_calls;input.clear();}
  interface_manager.loop();
  return claimed;
}
int main(){
  input={'v','e','r','\r'};assert(!poll() && ascii_calls==1 && !serial_cancels);
#if MESH_ENABLE_HOST_KISS
  // Existing binary ownership cannot be stolen by a FEND payload byte.
  usb_serial_interface.receiving=true; input={0xc0};
  assert(!serviceCompanionHostKissUsb() && !kiss_active && input.size()==1);
  usb_serial_interface.receiving=false;
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  usb_mota_mode=true;assert(!serviceCompanionHostKissUsb() && !kiss_active && input.size()==1);
  usb_mota_mode=false;mesh::ota::source.pending=true;
  assert(!serviceCompanionHostKissUsb() && !kiss_active && input.size()==1);
  mesh::ota::source.pending=false;
#endif
  terminal_mode=true;usb_terminal_discard_line=usb_logging_terminal_mode=true;
  input={0xc0,0x00,0x41};assert(poll());
  assert(kiss_active && host_kiss_usb_parked && host_kiss_usb_restore_enabled && !usb_serial_interface.enabled);
  assert(serial_cancels==1 && route_cancels==1 && forgotten_routes==1);
  assert(!the_mesh.usb_stream_pending && !the_mesh.usb_operations_pending);
  assert(ble_stream_pending && ble_operations_pending && network_calls==2);
  assert(!terminal_mode && !usb_terminal_discard_line && !usb_logging_terminal_mode);
  assert(input_resets==1 && line_clears==1 && text_discards==1);
  assert(!usb_logging_reply_pending && usb_binary_startup_probe.cancels==1 && usb_ascii_session_default.cancels==1);
#if COMPANION_FEATURE_READER
  assert(reader_cancels==1);
#endif
#if COMPANION_FEATURE_USB_MOTA_SOURCE
  assert(!usb_mota_mode && mota_resets==1);
#endif
  assert(poll() && network_calls==3 && !usb_binary_calls && ascii_calls==1);
  // RETURN ends binary ownership, but does not dispatch a trailing ASCII or
  // Companion frame during this same poll. Restore is the next poll's work.
  input={0xff,'v','e','r','\r'};assert(poll());
  assert(!kiss_active && host_kiss_usb_parked && !usb_serial_interface.enabled && binary_owned);
  assert(ascii_calls==1 && !usb_binary_calls && !input.empty());
  assert(!poll());
  assert(!host_kiss_usb_parked && !host_kiss_usb_restore_enabled && usb_serial_interface.enabled && !binary_owned);
  assert(ascii_calls==2 && !usb_binary_calls && usb_serial_interface.resets==1);
  assert(usb_ascii_session_default.requests==1 && usb_ascii_session_default.last_frame==19);
  // A transport already disabled by its manager must not be resurrected.
  usb_serial_interface.enabled=false;input={0xc0};assert(poll());
  assert(!host_kiss_usb_restore_enabled);input={0xff};assert(poll());assert(!poll());
  assert(!usb_serial_interface.enabled && !host_kiss_usb_parked);
  // Disabling the manager during the KISS lease also vetoes restoration.
  usb_serial_interface.enabled=true;input={0xc0};assert(poll());interface_manager.enabled=false;
  input={0xff};assert(poll());assert(!poll());assert(!usb_serial_interface.enabled);
  interface_manager.enabled=true;
  // A disconnect between RETURN and the next poll also releases the saved
  // one-poll quarantine even though the runtime KISS session already ended.
  usb_serial_interface.enabled=true;input={0xc0};assert(poll());
  input={0xff};assert(poll());assert(binary_owned && !kiss_active);
  resetUsbTerminalHostSession();
  assert(!binary_owned && !host_kiss_usb_parked && usb_serial_interface.enabled && kiss_resets==1);
  // An actual host-session reset cancels only USB work and retires KISS.
  usb_serial_interface.enabled=true;input={0xc0};assert(poll());
  resetUsbTerminalHostSession();
  assert(!kiss_active && kiss_resets==2 && !host_kiss_usb_parked && !host_kiss_usb_restore_enabled);
  assert(usb_serial_interface.enabled && !usb_serial_interface.passthrough);
  assert(ble_stream_pending && ble_operations_pending && !usb_terminal_discard_line && !usb_logging_terminal_mode);
#else
  assert(!serviceCompanionHostKissUsb() && usb_serial_interface.enabled);
  assert(!serial_cancels && !route_cancels && !forgotten_routes && !input_resets);
#endif
}
'''


class HostKissUsbTest(unittest.TestCase):
    def run_cpp(self, source, *, enabled, reader=0, mota=0, fail=False, extra_flags=()):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="host-kiss-usb-") as directory:
            directory = Path(directory)
            (directory / "Arduino.h").write_text(ARDUINO, encoding="ascii")
            (directory / "test.cpp").write_text(source, encoding="ascii")
            flags = ["-std=c++17", "-Wall", "-Wextra", "-DMESH_ENABLE_HOST_KISS=" + str(enabled),
                     "-DMESH_USB_LOGGING_AVAILABLE=1", "-DMESH_ESP32_TINYUSB_NONBLOCKING=0",
                     "-DMESH_ESP32_HWCDC_SESSION_GUARD=0", "-DMESH_NRF52_USB_CONSOLE_COOPERATIVE=0",
                     "-DCOMPANION_FEATURE_READER=" + str(reader),
                     "-DCOMPANION_FEATURE_USB_MOTA_SOURCE=" + str(mota),
                     "-I", str(directory), "-I", str(ROOT / "src")]
            if sys.platform.startswith("linux"):
                flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            result = subprocess.run([compiler, *flags, *extra_flags, str(directory / "test.cpp"), "-o", str(directory / "test")],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(directory / "test")], capture_output=True, text=True, timeout=10)
            if fail:
                self.assertNotEqual(result.returncode, 0, "Negative control failed to lose USB ownership")
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_cached_text_facades_suppress_binary_session_writes_and_reads(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        ports = "\n".join(body(source, signature) for signature in (
            "Stream& usbLoggingPort()", "Stream& usbDebugPort()", "Stream& usbTerminalPort(bool enabled)",
            "Stream& usbConsolePort()", "bool canAcceptUsbConsoleCommand()"))
        for enabled in (0, 1):
            with self.subTest(enabled=enabled):
                counters = "\n".join(body(source, signature) for signature in (
                    "static bool beginUsbTextWrite()", "static void endUsbTextWrite()"))
                self.run_cpp(TEXT_FIXTURE.replace("@PORTS@", ports).replace("@COUNTERS@", counters), enabled=enabled)

    def test_actual_companion_claim_return_reset_and_usb_only_cancellation(self):
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        cancel = body(source, "static void cancelUsbSerialOperations()")
        reset = body(source, "static void resetUsbTerminalHostSession()")
        service = body(source, "static bool serviceCompanionHostKissUsb()")
        for enabled, reader, mota, negative in ((0, 0, 0, False), (1, 0, 0, False),
                                               (1, 1, 1, False), (1, 1, 1, True)):
            with self.subTest(enabled=enabled, reader=reader, mota=mota, negative=negative):
                candidate = service
                if negative:
                    candidate = candidate.replace("host_kiss_usb_parked && !claimed && !mesh::hostkiss::usbActive()",
                                                  "host_kiss_usb_parked && !mesh::hostkiss::usbActive()")
                    self.assertNotEqual(candidate, service)
                fixture = COMPANION_FIXTURE.replace("@CANCEL@", cancel).replace("@RESET@", reset).replace("@SERVICE@", candidate)
                self.run_cpp(fixture, enabled=enabled, reader=reader, mota=mota, fail=negative)

    def test_actual_nrf52_text_queue_discards_backlog_without_draining_binary_stream(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        fixture = r'''
#include <cassert>
#include <atomic>
#include <cstdint>
static std::atomic<bool> usb_binary_stream_owned{false};
static std::atomic<uint32_t> usb_text_writers{0};
static std::atomic<bool> primary_usb_terminal_discard_pending{false};
static uint32_t primary_usb_terminal_seen_reset_generation=0, reset_generation=0;
static bool platform_debug=false, saved_logging=true, saved_debug=true;
bool usbDataLocked() { return false; }
uint32_t primaryUsbSessionGeneration(){return reset_generation;}
bool isUsbDebugLoggingEnabled(){return !usb_binary_stream_owned.load() && saved_logging && saved_debug;}
void setPlatformDebugOutputEnabled(bool enabled){platform_debug=enabled;}
struct Queue {
  unsigned pending=3,discards=0,services=0; bool busy=false;
  bool tryDiscardPending(){++discards;if(busy)return false;pending=0;return true;}
  void service(){++services;pending=0;}
} buffered_primary_usb_terminal_port;
void serviceUsbTerminalPort();
void discardUsbTerminalOutput();
bool hasPendingUsbTerminalOutput(){return buffered_primary_usb_terminal_port.pending || primary_usb_terminal_discard_pending.load();}
@BEGIN@
@END@
@OWNED@
@SET@
@READY@
@SERVICE@
@DISCARD@
int main(){
  // An admitted writer can outlive the ownership store. Even with no queued
  // bytes or busy queue lock, READY must wait for that producer to leave.
  assert(beginUsbTextWrite() && usb_text_writers.load()==1);
  setUsbBinaryStreamOwned(true);
  assert(!isUsbBinaryStreamReady() && !beginUsbTextWrite() && usb_text_writers.load()==1);
  endUsbTextWrite();
  assert(usb_text_writers.load()==0 && isUsbBinaryStreamReady());
  setUsbBinaryStreamOwned(false);
  buffered_primary_usb_terminal_port.pending=3;
  buffered_primary_usb_terminal_port.busy=true;
  setUsbBinaryStreamOwned(true);
  assert(!isUsbBinaryStreamReady());
  assert(isUsbBinaryStreamOwned() && !platform_debug && saved_logging && saved_debug);
  assert(primary_usb_terminal_discard_pending.load() && !buffered_primary_usb_terminal_port.services);
  assert(buffered_primary_usb_terminal_port.pending==3);
  serviceUsbTerminalPort();assert(!buffered_primary_usb_terminal_port.services);
  buffered_primary_usb_terminal_port.busy=false;
  serviceUsbTerminalPort();
  assert(!buffered_primary_usb_terminal_port.pending && !buffered_primary_usb_terminal_port.services);
  assert(!primary_usb_terminal_discard_pending.load() && isUsbBinaryStreamReady());
  // New host/reset generations discard rather than expose a queued old line.
  buffered_primary_usb_terminal_port.pending=4;++reset_generation;
  serviceUsbTerminalPort();
  assert(primary_usb_terminal_seen_reset_generation==reset_generation);
  assert(!buffered_primary_usb_terminal_port.pending && !buffered_primary_usb_terminal_port.services);
  setUsbBinaryStreamOwned(false);assert(!isUsbBinaryStreamOwned() && platform_debug);
  serviceUsbTerminalPort();assert(buffered_primary_usb_terminal_port.services==1);
}
'''
        for token, signature in (("@BEGIN@", "static bool beginUsbTextWrite()"),
                                 ("@END@", "static void endUsbTextWrite()"),
                                 ("@READY@", "bool isUsbBinaryStreamReady()"),
                                 ("@OWNED@", "bool isUsbBinaryStreamOwned()"),
                                 ("@SET@", "void setUsbBinaryStreamOwned(bool owned)"),
                                 ("@SERVICE@", "void serviceUsbTerminalPort()"),
                                 ("@DISCARD@", "void discardUsbTerminalOutput()")):
            fixture = fixture.replace(token, body(source, signature))
        self.run_cpp(fixture, enabled=1, extra_flags=("-DNRF52_PLATFORM=1", "-DUSE_TINYUSB=1"))
        negative = fixture.replace(" || usb_text_writers.load() != 0", "")
        self.assertNotEqual(negative, fixture)
        self.run_cpp(negative, enabled=1, fail=True,
                     extra_flags=("-DNRF52_PLATFORM=1", "-DUSE_TINYUSB=1"))

    def test_actual_loop_holds_ascii_and_binary_parser_gates_through_return_poll(self):
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        loop = body(source, "\nvoid loop()")
        self.assertLess(loop.index("serviceUsbTerminalHostSessionReset();"),
                        loop.index("serviceCompanionHostKissUsb();"))
        self.assertLess(loop.index("serviceCompanionHostKissUsb();"), loop.index("mesh::serviceUsbLoggingPort();"))
        for call in ("serviceUsbAsciiSessionDefault();", "serviceUsbLoggingReplyBarrier();",
                     "expireUsbBinaryStartupProbeBeforeDispatch();", "serviceUsbMotaResponseBoundary();",
                     "serviceUsbTerminal();"):
            prefix = loop[:loop.index(call)]
            self.assertIn("if (!host_kiss_usb_claimed)", prefix[prefix.rfind("#if defined(ENABLE_USB_INTERFACE)"):])
        self.assertIn("the_mesh.loop();", loop)
        self.assertIn("interface_manager.loop();", loop)
        self.assertIn("if (mesh::hostkiss::usbActive()) return false;",
                      body(source, "static bool usbLoggingRecoverySafe("))
        rescue = body((ROOT / "examples/companion_radio/MyMesh.cpp").read_text(), "void MyMesh::checkCLIRescueCmd()")
        self.assertLess(rescue.index("if (mesh::isUsbBinaryStreamOwned()) return;"),
                        rescue.index("mesh::usbCompanionPort()"))
        mymesh = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        admission = body(mymesh, "bool allowForeignRadioTransmit() const")
        self.assertIn("_radio_available", admission)
        self.assertIn("_scheduled_reboot_at == 0", admission)
        self.assertIn("mesh::hostkiss::radioActive()", admission)


if __name__ == "__main__":
    unittest.main()
