"""Reproduce framework flash-event text corrupting the Companion USB stream."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nrf52_core_fix", ROOT / "scripts/nrf52_loop_stack_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)

# The pinned Adafruit core's printf retarget, including its explicit non-USB
# logger branches. The runtime test executes the production patch against it.
CORE_LOGGER = """// nanolib printf() retarget
// Logger 0: Serial (CDC), 1 Serial1 (UART), 2 Segger RTT
int _write (int fd, const void *buf, size_t count)
{
  (void) fd;

  size_t ret = 0;

#if CFG_LOGGER == 2 || CFG_SYSVIEW
  SEGGER_RTT_Write(0, buf, count);
  ret = count;

#elif CFG_LOGGER == 1
  if ( Serial1 )
  {
    ret = Serial1.write((const uint8_t *) buf, count);
  }

#else
  if ( Serial )
  {
    ret = Serial.write((const uint8_t *) buf, count);
  }
#endif

  return (int) ret;
}
"""

ARDUINO = """#pragma once
#include <cstddef>
#include <cstdint>
class Stream {
 public:
  virtual ~Stream() = default;
  virtual int available() { return 0; }
  virtual int read() { return -1; }
  virtual int peek() { return -1; }
  virtual void flush() {}
  virtual int availableForWrite() { return 0; }
  virtual size_t write(uint8_t byte) { return write(&byte, 1); }
  virtual size_t write(const uint8_t*, size_t) = 0;
};
"""

RUNTIME = r'''
#define MESH_ENABLE_HOST_KISS 0
#include <atomic>
#include <cassert>
#include <cstring>
#include <helpers/NonBlockingWriteStream.h>
struct FakeSerial : Stream {
  bool connected = true;
  int capacity = 4096;
  unsigned bytes = 0, calls = 0;
  explicit operator bool() const { return connected; }
  int availableForWrite() override { return connected ? capacity : 0; }
  size_t write(const uint8_t*, size_t size) override {
    ++calls; bytes += size; return size;
  }
};
static FakeSerial Serial, Serial1, dedicated;
static unsigned rtt_bytes = 0;
static void SEGGER_RTT_Write(unsigned, const void*, size_t size) { rtt_bytes += size; }
static bool in_isr = false;
static bool isInISR() { return in_isr; }
namespace mesh {
static bool usbDataLocked() { return false; }
static bool logging = true, debug = true, packet_stream = false;
static bool dedicated_usb_logging_port_started = true;
static std::atomic<bool> dedicated_usb_logging_port_connected{true};
static bool isUsbLoggingEnabled() { return logging; }
static bool isUsbDebugEnabled() { return debug; }
static bool isUsbLoggingPacketStream() { return packet_stream; }
@DEBUG_GATE@
class NullStream : public Stream {
 public:
  size_t write(const uint8_t*, size_t size) override { return size; }
};
static NullStream null_usb_logging_stream;
static WholeRecordNonBlockingStream<640> nonblocking_primary_usb_logging_port(Serial);
static WholeRecordNonBlockingStream<640> nonblocking_dedicated_usb_logging_port(dedicated);
@LOGGING_PORT@
@DEBUG_PORT@
}
@HOOK@
extern "C" {
@CORE@
}
static void clear_output() {
  Serial.bytes = Serial.calls = Serial1.bytes = Serial1.calls = 0;
  dedicated.bytes = dedicated.calls = rtt_bytes = 0;
}
static const char flash_event[] = "[SOC   ] NRF_EVT_FLASH_OPERATION_SUCCESS\r\n";
static void emit_contact_save_and_delete_events() {
  static_assert(sizeof(flash_event) - 1 == 42, "Real captured event length");
  for (unsigned i = 0; i < 48; ++i) {
    assert(_write(1, flash_event, sizeof(flash_event) - 1) == 42);
  }
}
int main() {
  emit_contact_save_and_delete_events();
#if CFG_LOGGER == 2 || CFG_SYSVIEW
  assert(rtt_bytes == 2016 && Serial.bytes == 0 && dedicated.bytes == 0);
#elif CFG_LOGGER == 1
  assert(Serial1.bytes == 2016 && Serial.bytes == 0 && dedicated.bytes == 0);
#elif !PATCHED || !WITH_HOOK
  // Reproduce the complete 2016-byte corruption seen on the RAK4631.
  assert(Serial.bytes == 2016 && dedicated.bytes == 0);
#else
  #if defined(MESH_DUAL_CDC_LOGGING)
    assert(Serial.bytes == 0 && dedicated.bytes == 2016);
  #else
    // A logging-only/ASCII single-CDC role retains its selected primary route.
    assert(Serial.bytes == 2016 && dedicated.bytes == 0);
  #endif
  clear_output();
  mesh::debug = false;
  emit_contact_save_and_delete_events();
  assert(Serial.bytes == 0 && dedicated.bytes == 0);
  mesh::debug = true;
  mesh::logging = false;
  emit_contact_save_and_delete_events();
  assert(Serial.bytes == 0 && dedicated.bytes == 0);
  mesh::logging = true;
  in_isr = true;
  emit_contact_save_and_delete_events();
  assert(Serial.bytes == 0 && dedicated.bytes == 0);
  in_isr = false;
  Serial.capacity = dedicated.capacity = 0;
  emit_contact_save_and_delete_events();
  assert(Serial.calls == 0 && dedicated.calls == 0); // No retry/wait on a full FIFO.
  Serial.capacity = dedicated.capacity = 4096;
  #if defined(MESH_DUAL_CDC_LOGGING)
    mesh::dedicated_usb_logging_port_connected.store(false);
    emit_contact_save_and_delete_events();
    assert(Serial.bytes == 0 && dedicated.bytes == 0); // Never fall back to CDC0.
    mesh::dedicated_usb_logging_port_connected.store(true);
    mesh::packet_stream = true;
    emit_contact_save_and_delete_events();
    assert(Serial.bytes == 2016 && dedicated.bytes == 0); // Explicit primary logging.
    clear_output();
    mesh::packet_stream = false;
  #endif
  // Dropped records report consumed; later enabling debug remains usable.
  emit_contact_save_and_delete_events();
  #if defined(MESH_DUAL_CDC_LOGGING)
    assert(Serial.bytes == 0 && dedicated.bytes == 2016);
  #else
    assert(Serial.bytes == 2016 && dedicated.bytes == 0);
  #endif
  assert(_write(1, nullptr, 42) == 0);
  assert(_write(1, flash_event, 0) == 0);
#endif
}
'''


class Nrf52FrameworkLoggingTest(unittest.TestCase):
    def test_retarget_is_idempotent_and_rejects_changed_or_partial_core(self):
        patched = FIX.patched_logging_source(CORE_LOGGER.replace("\n", "\r\n"))
        self.assertEqual(FIX.patched_logging_source(patched), patched)
        for source in (
            CORE_LOGGER.replace("ret = Serial.write", "ret = another_writer"),
            CORE_LOGGER.replace(FIX.OLD_LOGGER, FIX.LOGGER),
            FIX.LOGGER_DECLARATION + CORE_LOGGER,
            CORE_LOGGER + CORE_LOGGER,
        ):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                FIX.patched_logging_source(source)

    def test_existing_private_core_copy_keeps_both_policies(self):
        source = (FIX.LOOP_STACK + "\n" + FIX.OLD_LOOP_CREATE + "\n" + CORE_LOGGER)
        patched = FIX.patched_logging_source(FIX.patched_source(source))
        self.assertIn(FIX.LOOP_CREATE, patched)
        self.assertIn(FIX.LOGGER, patched)
        self.assertEqual(FIX.patched_source(patched), patched)
        self.assertEqual(FIX.patched_logging_source(patched), patched)
        script = (ROOT / "scripts/nrf52_loop_stack_fix.py").read_text()
        self.assertIn("patched_logging_source(patched_source(", script)

    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_flash_event_corruption_then_routing_gates_and_bounded_output(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        header = (ROOT / "src/helpers/UsbLogging.h").read_text()
        parts = {
            "DEBUG_GATE": extract_braced(header, "inline bool isUsbBinaryStreamOwned(") + "\n"
                + extract_braced(header, "inline bool isUsbDebugLoggingEnabled("),
            "LOGGING_PORT": "\n".join(extract_braced(source, signature) for signature in (
                "static Stream& rawUsbLoggingPort(", "Stream& usbLoggingPort(")),
            "DEBUG_PORT": extract_braced(source, "Stream& usbDebugPort("),
        }
        hook = extract_braced(source, 'extern "C" size_t meshNrf52FrameworkDebugWrite(')
        # Keep the hook architecture-scoped; unrelated ESP output is unchanged.
        self.assertIn('#if defined(NRF52_PLATFORM)\n// The framework', source)
        with tempfile.TemporaryDirectory(prefix="meshcore-framework-log-") as temp:
            work = Path(temp)
            (work / "Arduino.h").write_text(ARDUINO)
            for patched, with_hook, dual, logger, sysview in (
                (False, True, True, 0, 0),
                (True, True, True, 0, 0),
                (True, True, False, 0, 0),
                (True, False, True, 0, 0),
                (True, True, True, 1, 0),
                (True, True, True, 2, 0),
                (True, True, True, 0, 1),
            ):
                with self.subTest(patched=patched, hook=with_hook, dual=dual,
                                  logger=logger, sysview=sysview):
                    generated = RUNTIME
                    for key, value in parts.items():
                        generated = generated.replace("@" + key + "@", value)
                    generated = generated.replace("@HOOK@", hook if with_hook else "")
                    generated = generated.replace("@CORE@", FIX.patched_logging_source(CORE_LOGGER)
                                                  if patched else CORE_LOGGER)
                    (work / "main.cpp").write_text(generated)
                    flags = [f"-DPATCHED={int(patched)}", f"-DWITH_HOOK={int(with_hook)}",
                             f"-DCFG_LOGGER={logger}", f"-DCFG_SYSVIEW={sysview}",
                             "-DNRF52_PLATFORM=1"]
                    if dual:
                        flags.append("-DMESH_DUAL_CDC_LOGGING=1")
                    built = subprocess.run([
                        "c++", "-std=c++17", "-O2", "-flto", "-flto-partition=one",
                        "-fno-semantic-interposition", "-I" + str(work),
                        "-I" + str(ROOT / "src"), *flags, str(work / "main.cpp"),
                        "-o", str(work / "test"),
                    ], text=True, capture_output=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    run = subprocess.run([str(work / "test")], text=True, capture_output=True, timeout=5)
                    self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
