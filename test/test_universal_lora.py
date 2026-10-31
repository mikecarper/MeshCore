"""Execute universal LoRa identification and driver dispatch without RF hardware."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

PROBE_HARNESS = r'''
#include <helpers/LoRaChipProbe.h>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

using mesh::LoRaRadioFamily;
struct IO {
  uint32_t clock = 0, initial = 0;
  unsigned resets = 0, reads127 = 0, reads126 = 0, pauses = 0;
  bool busy = false, read_ok = true;
  unsigned busy_checks = 0, busy_until = 0;
  std::vector<uint8_t> versions;
  std::vector<std::string> signatures;
  uint8_t readSX127Version() {
    size_t i = reads127++;
    return i < versions.size() ? versions[i] : 0xff;
  }
  bool readSX126Version(char* out, size_t size) {
    assert(size == 6);
    size_t i = reads126++;
    if (!read_ok) return false;
    std::string s = i < signatures.size() ? signatures[i] : std::string(6, char(0xff));
    assert(s.size() == size);
    std::memcpy(out, s.data(), size);
    return true;
  }
  bool ready() { return !busy && busy_checks++ >= busy_until; }
  void reset() { ++resets; clock += 11; }
  uint32_t now() const { return clock; }
  void pause(uint32_t ms) { assert(ms == 1); ++pauses; clock += ms; }
};

int main(int argc, char** argv) {
  assert(argc == 2);
  const int test = std::stoi(argv[1]);
  if (test == 0) {
    for (uint8_t version : {0x12, 0x13, 0x11}) {
      IO io; io.versions = {version, version}; io.busy = true;
      assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::SX127x);
      assert(io.resets == 1 && io.reads127 == 2 && !io.reads126 && !io.pauses);
    }
  } else if (test == 1) {
    IO io; io.signatures = {"SX1261", "SX1261"};
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::SX126x);
    assert(io.resets == 2 && io.reads127 == 1 && io.reads126 == 2 && !io.pauses);
  } else if (test == 2) {
    // An SX1272 has a different register map and is not an SX1276 backend.
    IO io; io.versions = {0x22, 0x22};
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
    assert(io.resets == 2 && io.reads127 == 1 && io.pauses == 250);
  } else if (test == 3) {
    for (uint8_t floating : {0x00, 0xff}) {
      IO io; io.versions = {floating}; io.signatures.assign(500, std::string(6, char(floating)));
      assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
      assert(io.clock == 272 && io.reads127 == 1 && io.pauses == 250);
    }
  } else if (test == 4) {
    IO io; io.busy = true;
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
    assert(io.clock == 272 && io.pauses == 250 && !io.reads126);
  } else if (test == 5) {
    IO io; io.read_ok = false; io.signatures = {"SX1261", "SX1261"};
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
    assert(io.pauses == 250 && io.reads126 == 250);
  } else if (test == 6) {
    IO io; io.versions = {0x12, 0x13};
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
    assert(io.reads127 == 2 && io.resets == 2 && io.pauses == 250);
    IO io2; io2.signatures = {"SX1261", "SX1262"};
    assert(mesh::probeLoRaChip(io2) == LoRaRadioFamily::None);
    assert(io2.pauses == 250);
  } else if (test == 7) {
    IO io; io.signatures = {"badbad", "SX1261", "SX1261"};
    io.busy_until = 2;
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::SX126x);
    assert(io.resets == 2 && io.reads126 == 3 && io.pauses == 3);
  } else if (test == 8) {
    IO io; io.initial = io.clock = UINT32_MAX - 15;
    assert(mesh::probeLoRaChip(io) == LoRaRadioFamily::None);
    assert(uint32_t(io.clock - io.initial) == 272 && io.pauses == 250);
  } else if (test == 9) {
    IO io;
    assert(mesh::probeLoRaChip(io, 0) == LoRaRadioFamily::None);
    assert(io.clock == 22 && !io.pauses && !io.reads126);
  } else if (test == 10) {
    IO io; io.signatures = {"SX1268", "SX1268"};
#if MESH_RADIO_AUTO_SX1268
    assert(mesh::probeLoRaChip(io, 250, false) == LoRaRadioFamily::SX1268);
    assert(io.resets == 1 && io.reads127 == 0 && io.reads126 == 2 && !io.pauses);
#else
    assert(mesh::probeLoRaChip(io, 250, false) == LoRaRadioFamily::None);
    assert(io.reads127 == 0 && io.pauses == 250);
#endif
  } else if (test == 11) {
    IO io; io.signatures = {"SX1261", "SX1261"};
    assert(mesh::probeLoRaChip(io, 250, false) == LoRaRadioFamily::SX126x);
    assert(io.resets == 1 && io.reads127 == 0 && io.reads126 == 2 && io.clock == 11);
  } else if (test == 12) {
    IO io; io.signatures = {"SX1268", "SX1261"};
    assert(mesh::probeLoRaChip(io, 250, false) == LoRaRadioFamily::None);
    assert(io.reads127 == 0 && io.pauses == 250);
  } else if (test == 13) {
    for (const auto* unsupported : {"SX1267", "SX1262", "LLCC68", "LR1121"}) {
      IO io; io.signatures.assign(500, unsupported);
      assert(mesh::probeLoRaChip(io, 250, false) == LoRaRadioFamily::None);
    }
  } else if (test == 14) {
    for (float preferred : {410.0f, 433.0f, 810.0f})
      assert(mesh::autoLoRaInitialFrequency(LoRaRadioFamily::SX1268, preferred) == preferred);
    for (float preferred : {0.0f, 409.0f, 811.0f, 910.525f}) {
      assert(mesh::autoLoRaInitialFrequency(LoRaRadioFamily::SX1268, preferred) == 434.0f);
      assert(mesh::autoLoRaInitialFrequency(LoRaRadioFamily::SX126x, preferred) == preferred);
      assert(mesh::autoLoRaInitialFrequency(LoRaRadioFamily::SX127x, preferred) == preferred);
    }
  } else assert(false);
}
'''

ARDUINO_STUB = r'''
#pragma once
#include <cstdint>
#include <map>
#include <utility>
#include <vector>
#define LOW 0
#define HIGH 1
#define OUTPUT 2
#define INPUT_PULLDOWN 3
#define INPUT 4
extern uint32_t now_ms;
extern bool chip_busy;
extern std::map<uint32_t, int> levels;
extern std::vector<std::pair<uint32_t, int>> modes, writes;
extern std::vector<uint32_t> reads;
inline uint32_t millis() { return now_ms; }
inline void delay(uint32_t ms) { now_ms += ms; }
inline void delayMicroseconds(uint32_t) {}
inline void digitalWrite(uint32_t pin, int value) { writes.emplace_back(pin, value); levels[pin] = value; }
inline void pinMode(uint32_t pin, int mode) { modes.emplace_back(pin, mode); }
inline int digitalRead(uint32_t pin) { reads.push_back(pin); return chip_busy ? HIGH : LOW; }
'''

SPI_STUB = r'''
#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>
#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings {
  SPISettings(uint32_t hz, int order, int mode) { assert(hz == 2000000 && order == MSBFIRST && mode == SPI_MODE0); }
};
struct SPIClass {
  std::vector<std::vector<uint8_t>> frames, responses;
  bool active = false;
  void beginTransaction(SPISettings) { assert(!active); active = true; frames.emplace_back(); }
  uint8_t transfer(uint8_t value) {
    assert(active);
    size_t i = frames.size() - 1, j = frames.back().size();
    frames.back().push_back(value);
    return i < responses.size() && j < responses[i].size() ? responses[i][j] : 0xff;
  }
  void endTransaction() { assert(active); active = false; }
};
'''

SPI_HARNESS = r'''
#include <helpers/radiolib/ESP32LoRaChipProbe.h>
#include <cassert>
#include <string>
uint32_t now_ms = 0;
bool chip_busy = false;
std::map<uint32_t, int> levels;
std::vector<std::pair<uint32_t, int>> modes, writes;
std::vector<uint32_t> reads;
unsigned usb_services = 0;
namespace mesh {
void serviceUsbLoggingPort() { ++usb_services; }
void serviceUsbTerminalPort() { ++usb_services; }
}
std::vector<uint8_t> signature(const char* text) {
  std::vector<uint8_t> out(4, 0);
  for (unsigned i = 0; i < 6; ++i) out.push_back(text[i]);
  return out;
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const int test = std::stoi(argv[1]);
  mesh::LoRaChipProbePins pins = {10, 11, 12};
  SPIClass spi;
  mesh::LoRaRadioFamily wanted = mesh::LoRaRadioFamily::None;
  if (test == 0) {
    spi.responses = {{0, 0x12}, {0, 0x12}};
    chip_busy = true;  // On SX127x this pin is DIO2, not BUSY.
    wanted = mesh::LoRaRadioFamily::SX127x;
  } else if (test == 1) {
    spi.responses = {{0, 0xff}, signature("SX1261"), signature("SX1261")};
    wanted = mesh::LoRaRadioFamily::SX126x;
  } else if (test == 2) chip_busy = true;
  else if (test == 3) {
    spi.responses = {signature("SX1268"), signature("SX1268")};
    wanted = mesh::LoRaRadioFamily::SX1268;
  } else if (test == 4 || test == 6) {
    pins.reset = RADIOLIB_NC;
    if (test == 6) pins.busy = RADIOLIB_NC;
    spi.responses = {{0}, {0,0x24}, signature("SX1268"), signature("SX1268")};
    wanted = mesh::LoRaRadioFamily::SX1268;
  } else if (test == 5) {
    pins.reset = RADIOLIB_NC;
    chip_busy = true;
  } else if (test == 7) {
    for (const auto& invalid : {mesh::LoRaChipProbePins{RADIOLIB_NC,11,12},
        mesh::LoRaChipProbePins{63,11,12}, mesh::LoRaChipProbePins{34,11,12},
        mesh::LoRaChipProbePins{10,63,12}, mesh::LoRaChipProbePins{10,11,63}}) {
      assert(mesh::probeUniversalLoRaChip(spi, invalid) == mesh::LoRaRadioFamily::None);
      assert(spi.frames.empty() && modes.empty() && writes.empty() && reads.empty() && now_ms == 0);
    }
    return 0;
  } else if (test == 8) {
    pins.reset = RADIOLIB_NC;
    assert(mesh::probeUniversalLoRaChip(spi, pins) == mesh::LoRaRadioFamily::None);
    assert(spi.frames.empty() && modes.empty() && writes.empty() && reads.empty());
    return 0;
  }
  else assert(false);
  assert(mesh::probeUniversalLoRaChip(spi, pins) == wanted);
  assert(!spi.active && levels[pins.nss] == HIGH);
  assert(!modes.empty() && !writes.empty());
  for (const auto& mode : modes) {
    assert((mode.first == pins.busy && (mode.second == INPUT_PULLDOWN || mode.second == INPUT))
        || ((mode.first == pins.reset || mode.first == pins.nss) && mode.second == OUTPUT));
  }
  for (const auto& frame : spi.frames) {
    assert(!frame.empty());
    // No TX/CAD/power/FIFO/profile writes. Only reset-less SX126x recipes
    // may send the NOP/RC-standby wake sequence before identification.
    if (frame[0] == 0x42) assert(frame == std::vector<uint8_t>({0x42, 0}));
    else if (frame[0] == 0) {
      assert(pins.reset == RADIOLIB_NC && !MESH_RADIO_AUTO_SX127X);
      assert(frame == std::vector<uint8_t>({0}));
    } else if (frame[0] == 0x80) {
      assert(pins.reset == RADIOLIB_NC && !MESH_RADIO_AUTO_SX127X);
      assert(frame == std::vector<uint8_t>({0x80, 0}));
    }
    else {
      assert(frame.size() == 10 && frame[0] == 0x1d && frame[1] == 0x03 && frame[2] == 0x20 && frame[3] == 0);
      for (size_t i = 4; i < frame.size(); ++i) assert(frame[i] == 0);
    }
  }
  for (uint32_t pin : reads) assert(pin == pins.busy && pin < 40);
  for (const auto& entry : modes) assert(entry.first < 40);
  for (const auto& entry : writes) assert(entry.first < 34);
  if (test <= 2) assert(spi.frames[0] == std::vector<uint8_t>({0x42, 0}));
  if (test == 0) assert(spi.frames.size() == 2 && now_ms == 11);
  if (test == 1) assert(spi.frames.size() == 3 && now_ms == 22);
  if (test == 2) assert(spi.frames.size() == 1 && now_ms == 272);
  if (test == 3) assert(spi.frames.size() == 2 && now_ms == 11);
  if (test == 4 || test == 6) assert(spi.frames.size() == 4 && now_ms == 1);
  if (test == 5) assert(spi.frames.size() == 1 && now_ms == 500);
  if (test >= 3) for (const auto& frame : spi.frames) assert(frame[0] != 0x42);
  if (pins.reset == RADIOLIB_NC) {
    for (const auto& entry : writes) assert(entry.first != pins.reset);
    for (const auto& entry : modes) assert(entry.first != pins.reset);
  }
  if (pins.busy == RADIOLIB_NC) assert(reads.empty());
#if MESH_USB_CONSOLE_COOPERATIVE
  assert(usb_services > 0);
#else
  assert(usb_services == 0);
#endif
}
'''


def split_arguments(text):
    """Split C++ arguments while retaining callback parameter groups."""
    parts, depth, start = [], 0, 0
    for i, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    if text[start:].strip():
        parts.append(text[start:].strip())
    return parts


def close_paren(text, opening):
    depth = 1
    for i in range(opening + 1, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if not depth:
                return i
    raise AssertionError("Unclosed C++ parameter group")


def parameter_type(argument):
    argument = argument.split("=", 1)[0].strip()
    if "(*" in argument.replace(" ", ""):
        return re.sub(r"\(\s*\*\s*\w*\s*\)", "(*@)", argument)
    argument = re.sub(r"\b[A-Za-z_]\w*\s*\[\s*\]$", "*", argument)
    match = re.fullmatch(r"(.+[\s*&])([A-Za-z_]\w*)", argument)
    if match and match[2] not in ("long", "short", "int", "char", "float", "double", "bool"):
        argument = match[1].strip()
    return argument + " @"


def methods_from_interface(source):
    methods = []
    for match in re.finditer(r"\bvirtual\s+", source):
        opening = source.find("(", match.end())
        prefix = source[match.end():opening].strip()
        found = re.fullmatch(r"(.+?)\s+([A-Za-z_]\w*)", prefix)
        if not found:
            raise AssertionError("Unsupported radio declaration: " + prefix)
        closing = close_paren(source, opening)
        methods.append(dict(result=found[1], name=found[2],
                            parameters=[parameter_type(p) for p in split_arguments(source[opening + 1:closing])],
                            const=bool(re.match(r"\s*const\b", source[closing + 1:])), virtual=True))
    return methods


def method_key(method):
    return (method["name"], tuple(re.sub(r"\s+|mesh::", "", p) for p in method["parameters"]), method["const"])


def extra_adapter_methods(source):
    methods = []
    pattern = r"^\s+MESH_AUTO_LORA_(EXTRA_CONST|EXTRA)\("
    for match in re.finditer(pattern, source, re.M):
        opening = match.end() - 1
        arguments = split_arguments(source[opening + 1:close_paren(source, opening)])
        assert len(arguments) == 5, arguments
        methods.append(dict(result=arguments[0], name=arguments[1],
                            parameters=[parameter_type(p) for p in split_arguments(arguments[2][1:-1])],
                            const=match[1] == "EXTRA_CONST", virtual=False))
    for result, name, parameters, const in (
            ("uint32_t", "rxPowerSavingTransitionUs", (), True),
            ("void", "getRxPowerSavingTimings", ("uint32_t& @", "uint32_t& @"), True),
            ("void", "resetStats", (), False),
            ("void", "powerOff", (), False)):
        methods.append(dict(result=result, name=name, parameters=list(parameters), const=const, virtual=False))
    return methods


BASE_TYPES = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <type_traits>
#include <vector>
#include <RadioProfiles.h>
#include <helpers/RadioSwitchTuning.h>
#include <helpers/MorseTx.h>
#include <helpers/MorseRx.h>
#include <helpers/RegulatorMode.h>
#include <helpers/TelemetryHistoryConfig.h>
#include <helpers/radiolib/RXPowerSaving.h>
namespace mesh {
enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED };
class MainBoard {};
@RADIO_CLASS@
}
using mesh::RadioParamApplyResult;
using mesh::RadioProfiles;
using mesh::RadioProfileParams;
using mesh::RadioSwitchTuning;
using mesh::RadioSwitchCapabilities;
using mesh::MorseTxResult;
using mesh::MorseRxResult;
using mesh::MorseRxDiagnostics;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
using mesh::MorseRssiTraceEntry;
#endif
namespace power = mesh::power;
struct PacketMillis { uint32_t preambleMillis, payloadMillis; };
'''

BACKEND_SUPPORT = r'''
struct Module { int id; bool init_ok = true; explicit Module(int n):id(n){} };
class SPIClass {};
extern unsigned live_chips, live_backends, init_calls;
extern unsigned last_method, last_family, shutdowns;
extern bool last_rfo;
extern const void* last_module;
extern std::vector<uint64_t> last_arguments;
extern char returned_text[];
extern uint8_t data_bytes[];
extern uint32_t timing_values[];
extern int context_value;
extern bool regulator_boot_ok,tuning_boot_ok;
template <typename T>
typename std::enable_if<std::is_arithmetic<T>::value || std::is_enum<T>::value, uint64_t>::type fingerprint(T value) {
  return uint64_t(static_cast<int64_t>(static_cast<double>(value) * 16));
}
template <typename T> uint64_t fingerprint(T* value) { return reinterpret_cast<uintptr_t>(value); }
template <typename T>
typename std::enable_if<!std::is_arithmetic<T>::value && !std::is_enum<T>::value && !std::is_pointer<T>::value, uint64_t>::type fingerprint(const T& value) {
  return reinterpret_cast<uintptr_t>(&value);
}
template <typename... Args> void record(unsigned method, unsigned family, const Args&... args) {
  last_method = method; last_family = family;
  last_arguments = {fingerprint(args)...};
}
template <typename T> typename std::enable_if<std::is_integral<T>::value, T>::type sample(unsigned id) { return T(17 + id); }
template <typename T> typename std::enable_if<std::is_floating_point<T>::value, T>::type sample(unsigned id) { return T(id + 0.25); }
template <typename T> typename std::enable_if<std::is_enum<T>::value, T>::type sample(unsigned) { return static_cast<T>(1); }
template <typename T> typename std::enable_if<!std::is_arithmetic<T>::value && !std::is_enum<T>::value && !std::is_pointer<T>::value, T>::type sample(unsigned) { return T{}; }
template <> inline bool sample<bool>(unsigned) { return true; }
template <typename T> typename std::enable_if<std::is_pointer<T>::value, T>::type sample(unsigned) { return reinterpret_cast<T>(returned_text); }
class alignas(32) PhysicalLayer {
 public:
  Module* module;
  explicit PhysicalLayer(Module* m):module(m) { ++live_chips; assert(reinterpret_cast<uintptr_t>(this) % 32 == 0); }
  ~PhysicalLayer() { assert(live_backends == 0); --live_chips; }
};
class CustomSX1262 : public PhysicalLayer {
 public:
  explicit CustomSX1262(Module* m):PhysicalLayer(m){}
  bool std_init(SPIClass*) { ++init_calls; last_module = module; return module->init_ok; }
};
class CustomSX1268 : public PhysicalLayer {
 public:
  explicit CustomSX1268(Module* m):PhysicalLayer(m){}
  bool std_init(SPIClass*) { ++init_calls; last_module = module; return module->init_ok; }
};
class CustomLR1121 : public PhysicalLayer {
 public:
  explicit CustomLR1121(Module* m):PhysicalLayer(m){}
};
class SX1276 : public PhysicalLayer {
 public:
  explicit SX1276(Module* m):PhysicalLayer(m){}
  int checkOutputPower(int8_t power, void*, bool rfo) {
    last_rfo = rfo;
    return (rfo ? power >= -4 && power <= 15 : power >= 2 && power <= 20) ? 0 : -1;
  }
};
class CustomSX1276 : public SX1276 {
 public:
  explicit CustomSX1276(Module* m):SX1276(m){}
  bool std_init(SPIClass*) { ++init_calls; last_module = module; return module->init_ok; }
};
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNSUPPORTED -1
'''

BACKEND_CLASS = r'''
class alignas(64) RadioLibWrapper : public mesh::Radio {
 protected:
  PhysicalLayer* _radio;
  unsigned family_;
  mesh::RadioProfiles profile_;
  virtual bool preserveActiveReceiveOnLateIrq() const { return false; }
  virtual bool usesLR1121Policy() const { return false; }
  virtual float initialProfileFrequency(float preferred) const { return preferred; }
  virtual bool checkBeaconOutputPower(float, int8_t power) const { return power >= -9 && power <= 22; }
  ~RadioLibWrapper() { --live_backends; selected=nullptr; }
 public:
  static RadioLibWrapper* selected;
  bool lateIrqGuardForTest() const { return preserveActiveReceiveOnLateIrq(); }
  bool lr1121PolicyForTest() const { return usesLR1121Policy(); }
  virtual float rxPowerSavingCaptureCostSymbols() const { return rxPowerSavingDefaultCaptureCost(); }
  float profileInitialForTest(float preferred) const { return initialProfileFrequency(preferred); }
  RadioLibWrapper(PhysicalLayer& radio, mesh::MainBoard&):_radio(&radio), family_(radio.module->id) {
    selected=this;
    ++live_backends; assert(reinterpret_cast<uintptr_t>(this) % 64 == 0);
  }
@METHODS@
};
class CustomSX1262Wrapper : public RadioLibWrapper {
 public:
  CustomSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board):RadioLibWrapper(radio,board){}
  ~CustomSX1262Wrapper(){}
};
class CustomSX1276Wrapper : public RadioLibWrapper {
 public:
  CustomSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard& board):RadioLibWrapper(radio,board){}
  ~CustomSX1276Wrapper(){}
};
class CustomSX1268Wrapper : public RadioLibWrapper {
 public:
  CustomSX1268Wrapper(CustomSX1268& radio, mesh::MainBoard& board):RadioLibWrapper(radio,board){}
  ~CustomSX1268Wrapper(){}
};
class CustomLR1121Wrapper : public RadioLibWrapper {
  bool (*deep_)() = nullptr;
 public:
  CustomLR1121Wrapper(CustomLR1121& radio, mesh::MainBoard& board):RadioLibWrapper(radio,board){}
  ~CustomLR1121Wrapper(){}
  void setDeepInitCallback(bool (*callback)()){deep_=callback;}
  bool (*deepCallbackForTest() const)(){return deep_;}
};
RadioLibWrapper* RadioLibWrapper::selected=nullptr;
'''


def backend_method(method, index):
    parameters = ", ".join(p.replace("@", "a" + str(i)) for i, p in enumerate(method["parameters"]))
    args = ", ".join("a" + str(i) for i in range(len(method["parameters"])))
    cv = " const" if method["const"] else ""
    override = " override" if method["virtual"] else ""
    code = "record(%du, family_%s);" % (index, ", " + args if args else "")
    name = method["name"]
    if name == "profiles":
        result = "&profile_"
    elif name in ("supportsMeshtext", "supportsEthernetTunnel"):
        result = "bool(" + {"supportsMeshtext": "MESH_ENABLE_MESHTEXT_WIRE", "supportsEthernetTunnel": "MESH_ENABLE_ETHERNET_WIRE"}[name] + ")"
        if name == "supportsEthernetTunnel":
            result = "(family_!=1268 && family_!=1121 && " + result + ")"
    elif name == "supportsRxPowerSaving":
        result = "family_ == 126 || family_ == 1121"
    elif name == "supportsRxBoostedGainMode":
        result = "family_ == 126 || family_ == 1268 || family_ == 1121"
    elif name == "validateBeaconPower":
        result = "checkBeaconOutputPower(a0,a1)"
    elif method["result"] == "void":
        result = None
        if name == "getRxPowerSavingTimings":
            code += "a0=700; a1=900;"
        if name == "powerOff":
            code += "++shutdowns;"
    else:
        result = "sample<%s>(%du)" % (method["result"], index)
    if name == "getNoiseFloorReading":
        code += "a1=-107.5f; a2=12345;"
    if result is not None:
        code += "return " + result + ";"
    definition = "%s %s(%s)%s%s {%s}" % (method["result"], name, parameters, cv, override, code)
    if name in ("idle", "startRecv"):
        definition = "protected:\n" + definition + "\npublic:"
    return definition


def argument_declaration(parameter, index):
    name = "p" + str(index)
    if "(*@)" in parameter:
        return parameter.replace("@", name) + "=&persistCallback;", name
    raw = parameter.replace("@", "").strip()
    if "*" in raw:
        if "void" in raw:
            value = "&context_value"
        elif "uint32_t" in raw:
            value = "timing_values"
        elif "uint8_t" in raw:
            value = "data_bytes"
        elif "char" in raw:
            value = "returned_text"
        else:
            raise AssertionError("Unrecognized pointer argument " + parameter)
        return parameter.replace("@", name) + "=" + value + ";", name
    base = raw.replace("const", "").replace("&", "").strip()
    if "RadioProfileParams" in base:
        value = "makeProfile()"
    elif "RegulatorMode" in base:
        value = "mesh::power::RegulatorMode::Dcdc"
    elif re.fullmatch(r"(?:u?int\d+_t|unsigned long|int|float|bool|size_t)", base):
        value = "static_cast<%s>(%d)" % (base, 11 + index)
    else:
        value = base + "{}"
    return base + " " + name + "=" + value + ";", name


def dispatch_check(method, index):
    declarations, args = [], []
    for i, parameter in enumerate(method["parameters"]):
        declaration, name = argument_declaration(parameter, i)
        declarations.append(declaration)
        args.append(name)
    argument_text = ",".join(args)
    code = "{" + "".join(declarations)
    code += "std::vector<uint64_t> expected={" + ",".join("fingerprint(" + arg + ")" for arg in args) + "};"
    # Interface methods are invoked through mesh::Radio, so an omitted override
    # cannot accidentally pass by executing the backend stub directly.
    target = "api" if method["virtual"] else "radio"
    if method["const"]:
        target = "const_api" if method["virtual"] else "const_radio"
    if method["name"] == "validateProfile":
        # The production SX127 subclass deliberately specializes this method.
        code += "if(family==126 && !MESH_RADIO_AUTO_LR1121){"
    code += "last_method=0;"
    call = target + "." + method["name"] + "(" + argument_text + ")"
    if method["result"] == "void":
        code += call + ";"
    else:
        code += "const auto result=" + call + ";(void)result;"
        result = method["result"]
        if result not in ("RxPowerSavingStatus", "PacketMillis", "RadioSwitchTuning", "RadioSwitchCapabilities", "mesh::RadioSwitchTuning", "mesh::RadioSwitchCapabilities"):
            if method["name"] == "profiles":
                code += "assert(result!=nullptr);"
            elif method["name"] == "supportsRxPowerSaving":
                code += "assert(result==(family==126 || family==1121));"
            elif method["name"] == "supportsRxBoostedGainMode":
                code += "assert(result==(family==126 || family==1268 || family==1121));"
            elif method["name"] in ("supportsMeshtext", "supportsEthernetTunnel"):
                macro = {"supportsMeshtext": "MESH_ENABLE_MESHTEXT_WIRE", "supportsEthernetTunnel": "MESH_ENABLE_ETHERNET_WIRE"}[method["name"]]
                expected = "bool(" + macro + ")"
                if method["name"] == "supportsEthernetTunnel":
                    expected = "(family!=1268 && family!=1121 && " + expected + ")"
                code += "assert(result==" + expected + ");"
            elif method["name"] == "validateBeaconPower":
                code += "assert(result);"
            else:
                code += "assert(result==sample<%s>(%du));" % (result, index)
    code += "assert(last_method==%du && last_family==family && last_arguments==expected);" % index
    if method["name"] == "validateProfile":
        code += "}"
    return code + "}"


class UniversalLoRaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++11 compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-universal-lora-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.probe = cls.compile_cpp("probe", PROBE_HARNESS)
        cls.probe1268 = cls.compile_cpp("probe1268", PROBE_HARNESS, ["-DMESH_RADIO_AUTO_SX1268=1"])
        stub = cls.work / "stubs"
        (stub / "helpers").mkdir(parents=True)
        (stub / "Arduino.h").write_text(ARDUINO_STUB)
        (stub / "SPI.h").write_text(SPI_STUB)
        (stub / "RadioLib.h").write_text(
            "#pragma once\n#define RADIOLIB_SX126X_CMD_READ_REGISTER 0x1d\n"
            "#define RADIOLIB_SX126X_REG_VERSION_STRING 0x0320\n"
            "#define RADIOLIB_NC 0xFFFFFFFFu\n#define RADIOLIB_SX126X_CMD_NOP 0\n"
            "#define RADIOLIB_SX126X_CMD_SET_STANDBY 0x80\n")
        (stub / "driver").mkdir()
        (stub / "driver/gpio.h").write_text(
            "#pragma once\n#define GPIO_IS_VALID_GPIO(pin) ((pin)<40)\n"
            "#define GPIO_IS_VALID_OUTPUT_GPIO(pin) ((pin)<34)\n")
        (stub / "helpers/UsbLogging.h").write_text(
            "#pragma once\nnamespace mesh { void serviceUsbLoggingPort(); void serviceUsbTerminalPort(); }\n")
        cls.spi = {enabled: cls.compile_cpp("spi" + str(enabled), SPI_HARNESS,
                                          ["-DMESH_USB_CONSOLE_COOPERATIVE=" + str(enabled)], [stub])
                   for enabled in (0, 1)}
        cls.spi1268 = {enabled: cls.compile_cpp("spi1268" + str(enabled), SPI_HARNESS,
                    ["-DMESH_USB_CONSOLE_COOPERATIVE=" + str(enabled),
                     "-DMESH_RADIO_AUTO_SX1268=1", "-DMESH_RADIO_AUTO_SX127X=0"], [stub])
                       for enabled in (0, 1)}

    @classmethod
    def compile_cpp(cls, name, source, flags=(), includes=()):
        cpp = cls.work / (name + ".cpp")
        exe = cls.work / (name + (".exe" if os.name == "nt" else ""))
        cpp.write_text(source)
        command = [cls.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *flags]
        if os.name != "nt":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        for path in includes:
            command += ["-I", str(path)]
        command += ["-I", str(ROOT / "src"), str(cpp), "-o", str(exe)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return exe

    @classmethod
    def compile_dispatch(cls, feature_values, *, extra_virtual=False, sx1268=False, sx127x=True,
                         lr1121=False):
        """Build the unchanged adapter against instrumented chip backends.

        The virtual methods are derived from the production mesh::Radio class,
        independent of the adapter. Omitting a new optional/default API from the
        adapter will execute the base default and fail the backend call check.
        """
        raw_radio = body((ROOT / "src/Dispatcher.h").read_text(), "class Radio {") + ";"
        if extra_virtual:
            raw_radio = raw_radio[:-2] + "\nvirtual uint32_t futureRadioCapability() const { return 0; }\n};"
        prefix = BASE_TYPES.replace("@RADIO_CLASS@", raw_radio)
        flags = ["-DMESH_RADIO_AUTO_FAMILY=1", "-Wno-unused-parameter",
                 "-DMESH_RADIO_AUTO_SX1268=" + str(int(sx1268)),
                 "-DMESH_RADIO_AUTO_SX127X=" + str(int(sx127x)),
                 "-DMESH_RADIO_AUTO_LR1121=" + str(int(lr1121)),
                 *["-D%s=%d" % row for row in feature_values.items()]]
        number = "".join(str(value) for value in feature_values.values())
        number += "-68" + str(int(sx1268)) + "-127" + str(int(sx127x))
        number += "-lr" + str(int(lr1121))
        if extra_virtual:
            number += "-unforwarded"
        folder = cls.work / ("adapter-" + number)
        folder.mkdir()
        preprocess = folder / "interface.cpp"
        preprocess.write_text(prefix)
        expanded = subprocess.run([cls.compiler, "-std=c++11", *flags,
                                   "-I", str(ROOT / "src"), "-E", "-P", str(preprocess)],
                                  capture_output=True, text=True, timeout=60)
        if expanded.returncode:
            raise AssertionError(expanded.stdout + expanded.stderr)
        interface = methods_from_interface(body(expanded.stdout, "class Radio {"))
        adapter = (ROOT / "src/helpers/radiolib/UniversalLoRaRadio.h").read_text()
        methods = interface + extra_adapter_methods(adapter)
        keys = [method_key(method) for method in methods]
        if len(keys) != len(set(keys)):
            raise AssertionError("Duplicate API descriptors")
        definitions = "\n".join(backend_method(method, i) for i, method in enumerate(methods, 1))
        (folder / "RadioLibWrappers.h").write_text(prefix + BACKEND_SUPPORT +
                                                  BACKEND_CLASS.replace("@METHODS@", definitions))
        for filename in ("CustomSX1262Wrapper.h", "CustomSX1268Wrapper.h", "CustomSX1276Wrapper.h",
                         "CustomLR1121Wrapper.h"):
            (folder / filename).write_text('#pragma once\n#include "RadioLibWrappers.h"\n')
        (folder / "helpers").mkdir()
        (folder / "helpers/RegulatorSettings.h").write_text(
            "#pragma once\nnamespace mesh { namespace power {\n"
            "inline bool reapplyDeferredRadioRegulator(Radio&){return ::regulator_boot_ok;}\n}}\n")
        (folder / "helpers/RadioProfileCLI.h").write_text(
            "#pragma once\nnamespace mesh { struct RadioProfileCLI {\n"
            "static bool reapplyDeferredBootTuning(Radio&){return ::tuning_boot_ok;}\n};}\n")
        # Copies preserve the production includes and bodies. Only their
        # hardware/backend dependencies are replaced by host instrumentation.
        for filename in ("UniversalLoRaRadio.h", "UniversalLoRaRadio.cpp"):
            shutil.copyfile(ROOT / "src/helpers/radiolib" / filename, folder / filename)
        checks = "\n".join(dispatch_check(method, i) for i, method in enumerate(methods, 1))
        harness = r'''
#include "UniversalLoRaRadio.h"
#include "UniversalLoRaRadio.cpp"
unsigned live_chips=0,live_backends=0,init_calls=0,last_method=0,last_family=0,shutdowns=0;
bool last_rfo=false;
bool regulator_boot_ok=true,tuning_boot_ok=true;
const void* last_module=nullptr;
std::vector<uint64_t> last_arguments;
char returned_text[64]="test text";
uint8_t data_bytes[8]={1,2,3};
uint32_t timing_values[2]={17,23};
int context_value=42;
bool persistCallback(void*,const mesh::RadioSwitchTuning&) { return true; }
bool deepInitCallback() { return true; }
bool initializeLR(CustomLR1121& chip, SPIClass*) {
  ++init_calls; last_module=chip.module; return chip.module->init_ok;
}
mesh::RadioProfileParams makeProfile() {
  mesh::RadioProfileParams p;p.freq=915.125f;p.bw=125;p.sf=7;p.cr=5;return p;
}
int main(int argc,char** argv) {
  assert(argc==2);
  const unsigned family=std::stoi(argv[1]);
  Module sx126(family==1268?1268:126),sx127(127),lr1121(1121);
  mesh::MainBoard board;
  SPIClass spi;
  {
    UniversalLoRaRadio radio(sx126,sx127,board
#if MESH_RADIO_AUTO_LR1121
        , &lr1121
#endif
        );
    mesh::Radio& api=radio;
    const mesh::Radio& const_api=radio;
    const UniversalLoRaRadio& const_radio=radio;
    static_assert(UniversalLoRaRadio::SupportsRxPowerSaving,"compile SX126 RXPS policy");
    assert(radio.family()==mesh::LoRaRadioFamily::None && radio.physicalLayer()==nullptr);
    assert(std::strcmp(radio.familyName(),"none")==0);
    assert(!radio.selectFamily(mesh::LoRaRadioFamily::None));
    assert(!radio.selectFamily(static_cast<mesh::LoRaRadioFamily>(99)));
    assert(!radio.stdInit(&spi) && !init_calls && !live_chips && !live_backends);
    assert(api.profiles()==nullptr && !api.startSendRaw(data_bytes,3));
    assert(api.trySetParams(915,125,7,5)==mesh::RadioParamApplyResult::FAILED);
    assert(!api.supportsRxPowerSaving());
    radio.powerOff();
    assert(!shutdowns);
    const auto chosen=family==1121?mesh::LoRaRadioFamily::LR1121:
      family==1268?mesh::LoRaRadioFamily::SX1268:
      family==126?mesh::LoRaRadioFamily::SX126x:mesh::LoRaRadioFamily::SX127x;
#if !MESH_RADIO_AUTO_SX1268
    assert(!radio.selectFamily(mesh::LoRaRadioFamily::SX1268));
#endif
#if !MESH_RADIO_AUTO_SX127X
    assert(!radio.selectFamily(mesh::LoRaRadioFamily::SX127x));
#endif
#if MESH_RADIO_AUTO_LR1121
    assert(!radio.selectFamily(mesh::LoRaRadioFamily::LR1121));
    assert(!radio.setLR1121Initializer(nullptr,deepInitCallback));
    assert(!radio.setLR1121Initializer(initializeLR,nullptr));
    assert(radio.setLR1121Initializer(initializeLR,deepInitCallback));
#else
    assert(!radio.selectFamily(mesh::LoRaRadioFamily::LR1121));
#endif
    assert(radio.selectFamily(chosen));
    assert(live_chips==1 && live_backends==1 && !init_calls);
    auto* physical=radio.physicalLayer();
    auto* profiles=api.profiles();
    assert(physical && profiles && const_radio.physicalLayer()==physical);
    assert(radio.family()==chosen);
    assert(std::strcmp(radio.familyName(),family==1121?"LR1121":family==1268?"SX1268":family==126?"SX126x":"SX127x")==0);
    assert(RadioLibWrapper::selected->lateIrqGuardForTest()==(family!=127 && family!=1121));
    assert(RadioLibWrapper::selected->lr1121PolicyForTest()==(family==1121));
#if MESH_RADIO_AUTO_LR1121
    assert(!radio.setLR1121Initializer(initializeLR,deepInitCallback));
    if(family==1121)
      assert(static_cast<CustomLR1121Wrapper*>(RadioLibWrapper::selected)->deepCallbackForTest()==deepInitCallback);
    if(family==126)
      assert(radio.rxPowerSavingCaptureCostSymbols()==RX_POWERSAVING_CAPTURE_COST_SYMBOLS);
#endif
    assert(RadioLibWrapper::selected->profileInitialForTest(915)==(family==1268?434:915));
    assert(RadioLibWrapper::selected->profileInitialForTest(433)==433);
    assert(radio.defaultFrequency(915)==(family==1268?434:915));
    assert(radio.defaultFrequency(433)==433);
    assert(radio.selectFamily(chosen) && radio.physicalLayer()==physical && api.profiles()==profiles);
    assert(!radio.selectFamily(family==126?mesh::LoRaRadioFamily::SX127x:mesh::LoRaRadioFamily::SX126x));
    assert(radio.physicalLayer()==physical && live_chips==1 && live_backends==1);
    // Boot settings can reach the chosen driver before chip initialization.
    assert(api.configureBootRegulator(mesh::power::RegulatorMode::Dcdc));
    assert(last_family==family && !init_calls);
    regulator_boot_ok=false;
    assert(!radio.stdInit(&spi)&&init_calls==0);
    regulator_boot_ok=true;
#if MESH_RADIO_SWITCH_TUNING
    tuning_boot_ok=false;
    assert(!radio.stdInit(&spi)&&init_calls==0);
    tuning_boot_ok=true;
#endif
    auto& module=family==1121?lr1121:family==127?sx127:sx126;
    module.init_ok=false;
    assert(!radio.stdInit(&spi));
    module.init_ok=true;
    assert(radio.stdInit(&spi) && init_calls==2);
    assert(last_module==&module);
    @CHECKS@
    if(family==127) {
      auto p=makeProfile();
      assert(api.validateProfile(p));
      p.sf=5;assert(!api.validateProfile(p));p.sf=6;assert(!api.validateProfile(p));p.sf=7;
      p.freq=137;assert(api.validateProfile(p));p.freq=1020;assert(api.validateProfile(p));
      p.freq=136;assert(!api.validateProfile(p));p.freq=1021;assert(!api.validateProfile(p));
      p=makeProfile();p.bw=812.5f;assert(!api.validateProfile(p));
      p=makeProfile();p.protocol=mesh::RadioProfileProtocol::Meshtext;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_MESHTEXT_WIRE));
      p.protocol=mesh::RadioProfileProtocol::Ethernet;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_ETHERNET_WIRE));
      p.protocol=mesh::RadioProfileProtocol::Kiss;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_HOST_KISS));
#if MESH_ENABLE_TX_BEACONS
      assert(api.validateBeaconPower(915,-4) && last_rfo);
      assert(api.validateBeaconPower(915,1) && last_rfo);
      assert(api.validateBeaconPower(915,2) && !last_rfo);
      assert(api.validateBeaconPower(915,20) && !last_rfo);
      assert(!api.validateBeaconPower(915,21));
#endif
    }
    if(family==1268) {
      auto p=makeProfile();p.freq=433;assert(api.validateProfile(p));
      p.sf=5;assert(api.validateProfile(p));p.sf=12;assert(api.validateProfile(p));
      p.freq=410;assert(api.validateProfile(p));p.freq=810;assert(api.validateProfile(p));
      p.freq=409;assert(!api.validateProfile(p));p.freq=811;assert(!api.validateProfile(p));
      p.freq=910.525f;assert(!api.validateProfile(p));p.freq=433;
      p.bw=812.5f;assert(!api.validateProfile(p));p.bw=125;
      p.protocol=mesh::RadioProfileProtocol::Meshtext;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_MESHTEXT_WIRE));
      p.protocol=mesh::RadioProfileProtocol::Ethernet;assert(!api.validateProfile(p));
      p.protocol=mesh::RadioProfileProtocol::Kiss;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_HOST_KISS));
      assert(!api.supportsRxPowerSaving() && radio.supportsRxBoostedGainMode());
#if MESH_ENABLE_TX_BEACONS
      assert(api.validateBeaconPower(433,-9));assert(api.validateBeaconPower(433,22));
      assert(!api.validateBeaconPower(433,-10) && !api.validateBeaconPower(433,23));
#endif
    }
#if MESH_RADIO_AUTO_LR1121
    if(family==126) {
      auto p=makeProfile();
      for(float f : {150.0f,433.0f,960.0f}) {p.freq=f;assert(api.validateProfile(p));}
      for(float f : {149.0f,961.0f,1900.0f,2400.0f}) {p.freq=f;assert(!api.validateProfile(p));}
      p=makeProfile();p.bw=7.8f;assert(api.validateProfile(p));
      p.bw=203.125f;assert(!api.validateProfile(p));
    }
    if(family==1121) {
      auto p=makeProfile();
      for(float f : {150.0f,960.0f,1900.0f,2200.0f,2400.0f,2500.0f}) {
        p.freq=f;assert(api.validateProfile(p));
      }
      for(float f : {149.0f,961.0f,1800.0f,2300.0f,2501.0f}) {
        p.freq=f;assert(!api.validateProfile(p));
      }
      p=makeProfile();p.bw=7.8f;assert(!api.validateProfile(p));
      for(float bw : {203.125f,406.25f,812.5f}) {
        p.bw=bw;p.freq=915;assert(!api.validateProfile(p));
        p.freq=2400;assert(api.validateProfile(p));
      }
      p=makeProfile();p.protocol=mesh::RadioProfileProtocol::Ethernet;
      assert(!api.validateProfile(p));
      p.protocol=mesh::RadioProfileProtocol::Meshtext;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_MESHTEXT_WIRE));
      p.protocol=mesh::RadioProfileProtocol::Kiss;
      assert(api.validateProfile(p)==bool(MESH_ENABLE_HOST_KISS));
#if MESH_ENABLE_TX_BEACONS
      assert(api.validateBeaconPower(915,-17));assert(api.validateBeaconPower(915,22));
      assert(!api.validateBeaconPower(915,-18));assert(!api.validateBeaconPower(915,23));
      assert(api.validateBeaconPower(2400,-18));assert(api.validateBeaconPower(2400,13));
      assert(!api.validateBeaconPower(2400,14));assert(!api.validateBeaconPower(2400,-19));
#endif
    }
#endif
    assert(live_chips==1 && live_backends==1);
  }
  assert(!live_chips && !live_backends);
}
'''.replace("@CHECKS@", checks)
        return cls.compile_cpp("dispatch" + number, harness, flags, [folder]), len(interface)

    def execute(self, binary, scenario):
        env = dict(os.environ)
        env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
        result = subprocess.run([str(binary), str(scenario)], capture_output=True,
                                text=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sx1276_sx1278_and_rfm9x_signatures_skip_other_family(self):
        self.execute(self.probe, 0)

    def test_sx1262_signature_is_confirmed(self):
        self.execute(self.probe, 1)

    def test_sx1272_is_rejected(self):
        self.execute(self.probe, 2)

    def test_floating_spi_is_rejected(self):
        self.execute(self.probe, 3)

    def test_stuck_busy_has_a_bounded_deadline(self):
        self.execute(self.probe, 4)

    def test_failed_register_reads_are_not_a_detection(self):
        self.execute(self.probe, 5)

    def test_inconsistent_identifiers_are_rejected(self):
        self.execute(self.probe, 6)

    def test_busy_and_initial_unrecognized_reads_can_recover(self):
        self.execute(self.probe, 7)

    def test_deadline_survives_millis_wrap(self):
        self.execute(self.probe, 8)

    def test_zero_budget_does_not_spin(self):
        self.execute(self.probe, 9)

    def test_sx1268_requires_its_exact_confirmed_signature_and_build_support(self):
        for binary in (self.probe, self.probe1268):
            for scenario in (10, 12, 13):
                with self.subTest(binary=binary.name, scenario=scenario):
                    self.execute(binary, scenario)

    def test_sx126x_only_probe_skips_the_sx127x_command(self):
        self.execute(self.probe, 11)

    def test_sx1268_default_frequency_keeps_valid_settings_and_other_radios(self):
        self.execute(self.probe, 14)

    def test_actual_spi_transport_is_read_only_and_leaves_chip_deselected(self):
        for enabled, binary in self.spi.items():
            for scenario in (0, 1, 2, 7, 8):
                with self.subTest(cooperative_usb=enabled, scenario=scenario):
                    self.execute(binary, scenario)

    def test_sx126x_only_spi_transport_handles_no_reset_and_missing_pins_safely(self):
        for enabled, binary in self.spi1268.items():
            for scenario in (3, 4, 5, 6, 7):
                with self.subTest(cooperative_usb=enabled, scenario=scenario):
                    self.execute(binary, scenario)

    def test_actual_sx1268_initialization_uses_a_valid_boot_frequency(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1268.h").read_text()
        method = body(source, "bool std_init(SPIClass* spi = NULL)")
        method = "bool std_init(SPIClass* spi = nullptr) " + method[method.index("{"):]
        harness = r'''
#include <helpers/LoRaRadioFamily.h>
#include <cassert>
#include <limits>
#include <cstdint>
float preferred=910.525f;
#define LORA_FREQ preferred
#define LORA_BW 62.5f
#define LORA_SF 7
#define LORA_TX_POWER 22
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_ERR_NONE 0
struct SPIClass{};
namespace mesh {
struct Log { template<typename T>void print(T){} template<typename T>void println(T){} };
Log& usbLoggingPort(){static Log log;return log;}
}
struct Driver {
 float initialized=0;
 int begin(float frequency,float,uint8_t,uint8_t,uint8_t,int8_t,uint16_t,float){initialized=frequency;return 0;}
 void setCRC(int){}
 @METHOD@
};
int main(){
 Driver driver;
 for(float value:{410.0f,433.0f,810.0f,910.525f,409.0f,811.0f}){
   preferred=value;assert(driver.std_init());
   assert(driver.initialized==
#if MESH_RADIO_AUTO_SX1268
     (value>=410&&value<=810?value:434)
#else
     value
#endif
   );
 }
#if MESH_RADIO_AUTO_SX1268
 preferred=std::numeric_limits<float>::quiet_NaN();
 assert(driver.std_init()&&driver.initialized==434);
#endif
}
'''.replace("@METHOD@", method)
        harness = harness.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>")
        for enabled in (0, 1):
            binary = self.compile_cpp("sx1268-boot" + str(enabled), harness,
                ["-Wno-unused-parameter", "-DMESH_RADIO_AUTO_SX1268=" + str(enabled)])
            self.execute(binary, 0)

    def test_every_virtual_radio_api_and_concrete_helper_dispatches_to_the_selected_backend(self):
        features = ("MESH_RADIO_SWITCH_TUNING", "MESH_ENABLE_TELEMETRY_HISTORY", "MESH_MORSE_HIL",
                    "MESH_ENABLE_HOST_KISS", "MESH_ENABLE_TX_BEACONS", "MESH_ENABLE_MESHTEXT_WIRE",
                    "MESH_ENABLE_ETHERNET_WIRE")
        for values in ((1, 1, 1, 1, 1, 1, 1), (0, 0, 0, 0, 0, 0, 0), (0, 0, 0, 1, 1, 0, 1)):
            definitions = dict(zip(features, values))
            for sx1268, sx127x, lr1121 in ((False, True, False), (True, False, False),
                    (True, True, False), (False, False, False), (False, False, True), (True, True, True)):
                with self.subTest(features=definitions, sx1268=sx1268, sx127x=sx127x,lr1121=lr1121):
                    binary, virtual_count = self.compile_dispatch(definitions, sx1268=sx1268,
                                                                  sx127x=sx127x,lr1121=lr1121)
                    self.assertGreater(virtual_count, 75)
                    data = binary.read_bytes()
                    self.assertEqual(b"meshcore.capability.radio.autodetect.sx126x_sx127x.v1" in data, sx127x)
                    self.assertEqual(b"meshcore.capability.radio.autodetect.sx1262_sx1268.v1" in data, sx1268)
                    self.assertEqual(b"meshcore.capability.radio.autodetect.sx1262_lr1121.v1" in data, lr1121)
                    for family in (126, *([127] if sx127x else []), *([1268] if sx1268 else []),
                                   *([1121] if lr1121 else [])):
                        with self.subTest(family=family):
                            self.execute(binary, family)

    def test_new_unforwarded_default_virtual_is_detected(self):
        features = {name: 1 for name in (
            "MESH_RADIO_SWITCH_TUNING", "MESH_ENABLE_TELEMETRY_HISTORY", "MESH_MORSE_HIL",
            "MESH_ENABLE_HOST_KISS", "MESH_ENABLE_TX_BEACONS", "MESH_ENABLE_MESHTEXT_WIRE",
            "MESH_ENABLE_ETHERNET_WIRE")}
        binary, _ = self.compile_dispatch(features, extra_virtual=True)
        result = subprocess.run([str(binary), "126"], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0, "An unforwarded default virtual escaped the dispatch checks")


if __name__ == "__main__":
    unittest.main()
