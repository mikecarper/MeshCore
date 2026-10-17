// Inserted after the ESP32Board peripheral harness. Peripheral queues/time are
// simulated. @-marked methods below are copied verbatim from production.
#include <algorithm>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <deque>
#include <sstream>
#include <string>

struct Stream {
  std::deque<uint8_t> input;
  std::string output;
  int available() { return input.size(); }
  int read() { int c = input.front(); input.pop_front(); return c; }
  void print(char c) { output += c; }
  void print(const char* text) { output += text; }
  void println(const char* text) { output += text; output += "\r\n"; }
  void printf(const char* format, ...) {
    char buffer[256]; va_list args; va_start(args, format);
    int n = vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    assert(n >= 0 && size_t(n) < sizeof(buffer)); output.append(buffer, n);
  }
} console;
namespace mesh {
void serviceUsbLoggingPort() {}
void serviceUsbTerminalPort() {}
bool takeUsbTerminalSessionReset() { return false; }
bool tryCompleteUsbTerminalSessionReset() { return true; }
bool canAcceptUsbConsoleCommand() { return true; }
Stream& usbConsolePort() { return console; }
void noteUsbLoggingStatsCommand(const char*) {}
bool isUsbLoggingWatchdogArmed() { return false; }
namespace wireless {
struct Control { bool pending() { return false; } };
Control& control() { static Control value; return value; }
}
}
struct Prefs { uint8_t powersaving_enabled = 0; } prefs;
struct Sensors { void setPowerSavingEnabled(bool) {} } sensors;
struct MyMesh {
  Prefs* _prefs = &prefs;
  Sensors* _sensors = &sensors;
  unsigned saves = 0;
  bool dual_radio_active = false;
  void savePrefs() { ++saves; }
  void set(const char* config, char* reply) { @SETTER@ }
  void get(const char* config, char* reply) { @GETTER@ }
  void handleUsbCommand(const char* command, char* reply) {
    if (!strncmp(command, "set ", 4)) set(command + 4, reply);
    else if (!strncmp(command, "get ", 4)) get(command + 4, reply);
    else strcpy(reply, "Unknown");
  }
  void cancelPendingSerialOutput() {}
  bool hasPendingSerialOutput() { return false; }
  bool isDualRadioActive() { return dual_radio_active; }
  Prefs* getNodePrefs() { return _prefs; }
  unsigned getPowerSaveSleepMilliseconds(unsigned limit) { return limit; }
  bool millisHasNowPassed(unsigned deadline) { return millis() > deadline; }
} the_mesh;
static ESP32Board board;
static constexpr unsigned POWERSAVING_FIRSTSLEEP_SECS = 120;
static constexpr size_t LOCAL_SERIAL_COMMAND_MAX = 159;
static char command[LOCAL_SERIAL_COMMAND_MAX + 2] = {};
static bool command_overflow = false;
@COMMAND_PUMP@
static void power_loop() { @POWER_LOOP@ }

static void emit() {
  if (!console.output.empty()) {
    std::cout << "DATA ";
    for (uint8_t c : console.output) printf("%02x", c);
    std::cout << '\n'; console.output.clear();
  }
  std::cout << "STATE " << unsigned(prefs.powersaving_enabled) << ' '
            << sleep_calls << ' ' << millis() << '\n';
  std::cout << "DONE\n" << std::flush;
}
int main() {
  attachHost(true); // The Web Serial port is open, with no DTR/keepalive.
  Serial.terminal_open = false;
  mesh::logging_enabled = false;
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.rfind("WRITE ", 0) == 0) {
      // A sleeping console cannot deliver the first complete command during
      // the stock reply window; no UART wake source was configured.
      if (sleep_calls == 0) {
        for (size_t n = 6; n < line.size(); n += 2) {
          unsigned byte; assert(n + 1 < line.size());
          assert(sscanf(line.c_str() + n, "%2x", &byte) == 1);
          console.input.push_back(uint8_t(byte));
        }
        while (console.available()) serviceCommandInterfaces();
      }
    } else if (line.rfind("TIME ", 0) == 0) {
      mock_millis = std::stoul(line.substr(5)); power_loop();
    } else if (line == "CLOSE") {
      console.input.clear(); console.output.clear(); command[0] = 0;
    } else if (line == "HOST_OFF") {
      attachHost(false);
    } else if (line == "DRIVER_END") {
      Serial.end();
    } else { assert(false && "Unknown peripheral operation"); }
    emit();
  }
}
