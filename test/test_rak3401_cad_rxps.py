"""Exercise the RAK3401 CAD handoff against independently arriving radio IRQs."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/RXPowerSaving.h>

constexpr uint16_t RADIOLIB_SX126X_IRQ_RX_DONE = 0x02;
constexpr uint16_t RADIOLIB_SX126X_IRQ_HEADER_VALID = 0x10;
constexpr uint16_t RADIOLIB_SX126X_IRQ_HEADER_ERR = 0x20;
constexpr uint16_t RADIOLIB_SX126X_IRQ_CRC_ERR = 0x40;
constexpr uint16_t RADIOLIB_SX126X_IRQ_CAD_DETECTED = 0x100;
constexpr uint16_t RADIOLIB_SX126X_IRQ_TIMEOUT = 0x200;
constexpr int16_t RADIOLIB_ERR_NONE = 0;
constexpr int16_t RADIOLIB_ERR_SPI_CMD_FAILED = -707;
constexpr uint32_t RADIOLIB_SX126X_RX_TIMEOUT_NONE = 0;
constexpr uint8_t RADIOLIB_SX126X_CMD_GET_IRQ_STATUS = 0x12;
constexpr uint16_t Hit = RADIOLIB_SX126X_IRQ_CAD_DETECTED;
constexpr uint16_t Header = RADIOLIB_SX126X_IRQ_HEADER_VALID;
constexpr uint16_t RxDone = RADIOLIB_SX126X_IRQ_RX_DONE;

static uint32_t now;
static uint16_t flags;
static bool spi_owned;
static bool interrupt_masked;
static uint32_t millis() { return now; }
namespace rak3401::cadRxps {
  bool interruptAsserted() { return flags && !interrupt_masked; }
  bool ownsBus() { return spi_owned; }
  void stop() { spi_owned = false; }
  bool start() { spi_owned = true; return true; }
  bool exhausted() { return false; }
  bool overdue() { return false; }
}

struct CustomSX1262 {
  uint16_t cleared = 0;
  unsigned reads = 0;
  bool complete_on_clear = false;
  unsigned rx_starts = 0;
  CustomSX1262* getMod() { return this; }
  int16_t SPIreadStream(uint8_t command, uint8_t* out, unsigned size) {
    assert(command == RADIOLIB_SX126X_CMD_GET_IRQ_STATUS && size == 2);
    ++reads;
    out[0] = flags >> 8;
    out[1] = flags & 0xff;
    return RADIOLIB_ERR_NONE;
  }
  int16_t setRx(uint32_t timeout) {
    assert(timeout == RADIOLIB_SX126X_RX_TIMEOUT_NONE);
    ++rx_starts;
    return RADIOLIB_ERR_NONE;
  }
  int16_t clearIrqFlags(uint16_t observed) {
    if (observed & Hit) assert(rx_starts == 1);
    cleared = observed;
    if (complete_on_clear) flags |= RxDone;
    flags &= ~observed;
    return RADIOLIB_ERR_NONE;
  }
};

struct RadioLibWrapper {
  bool _loop_event_pending = false;
  unsigned restarts = 0;
  int recvRaw(uint8_t*, int) {
    assert(_loop_event_pending);
    _loop_event_pending = false;
    ++restarts;
    return flags & RxDone ? 42 : 0;
  }
  void claimRadioInterrupt() { _loop_event_pending = false; }
  void requestRestartRecv() { _loop_event_pending = true; }
  void stopReceiveDutyCycle() { rak3401::cadRxps::stop(); }
  RxPowerSavingArmRetryState _rx_ps_arm_retry;
  uint32_t _rx_ps_arm_failures = 0;
  int16_t _rx_ps_last_error = RADIOLIB_ERR_NONE;
};

struct Wrapper : RadioLibWrapper {
  CustomSX1262 radio;
  CustomSX1262* _radio = &radio;
  @STATE@
  @RECEIVE@
  int poll() { return recvCadRxPowerSaving(nullptr, 0); }
};

int main() {
  {
    Wrapper radio;
    const uint32_t started = UINT32_MAX - 54;
    now = started; flags = Hit; spi_owned = true;
    assert(radio.poll() == 0 && !spi_owned && radio.radio.cleared == Hit);
    assert(radio.radio.rx_starts == 1);
    now = started + Wrapper::CadHeaderTimeoutMs - 1;
    assert(radio.poll() == 0 && radio.restarts == 0 && radio.radio.reads == 1);
    ++now;
    assert(radio.poll() == 0 && radio.restarts == 1 && radio.radio.reads == 2);
  }
  {
    Wrapper radio;
    now = 100; flags = Hit;
    assert(radio.poll() == 0);
    // Header latches at the same time as the short receive deadline.
    now = 100 + Wrapper::CadHeaderTimeoutMs; flags = Header; interrupt_masked = true;
    assert(radio.poll() == 0 && radio.restarts == 0 && radio.radio.cleared == Header);
    interrupt_masked = false;
    now = 1300;
    assert(radio.poll() == 0 && radio.restarts == 0);
    flags = RxDone;
    assert(radio.poll() == 42 && radio.restarts == 1);
  }
  {
    Wrapper radio;
    now = 200; flags = Hit;
    assert(radio.poll() == 0);
    now = 250; flags = Header;
    radio.radio.complete_on_clear = true;
    assert(radio.poll() == 0 && flags == RxDone);
    assert(radio.poll() == 42 && radio.restarts == 1);
  }
}
'''


class Rak3401CadRxpsTest(unittest.TestCase):
    def test_false_detection_deadline_and_valid_packet_handoff(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        state = source[source.index("  static constexpr uint16_t CadHit"):
                       source.index("  int16_t armCadRxPowerSaving()")]
        receive = body(source, "int recvCadRxPowerSaving(")
        cpp = HARNESS.replace("@STATE@", state).replace("@RECEIVE@", receive)
        with tempfile.TemporaryDirectory(prefix="cad-rxps-") as tmp:
            main = Path(tmp) / "main.cpp"
            binary = Path(tmp) / "test"
            main.write_text(cpp)
            build = subprocess.run(
                [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src"), str(main), "-o", str(binary)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)
