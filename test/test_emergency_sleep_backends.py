"""Execute resumable emergency CPU sleeps with native RTOS/SDK boundaries."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>
static std::vector<std::string> events;
static bool radio_test = false, interrupt_context = false, wireless_ready = true;
static bool usb_held = false;
static unsigned usb_mount_checks = 0;
[[maybe_unused]] static bool tud_mounted() { ++usb_mount_checks; return usb_held; }
static bool attach_on_delay = false;
static unsigned primask = 0, basepri = 0, faultmask = 0;
static unsigned __get_IPSR() { return interrupt_context; }
static unsigned __get_PRIMASK() { return primask; }
static unsigned __get_BASEPRI() { return basepri; }
static unsigned __get_FAULTMASK() { return faultmask; }
static bool xPortInIsrContext() { return interrupt_context; }
static constexpr int taskSCHEDULER_RUNNING = 2;
static int scheduler = taskSCHEDULER_RUNNING;
static int xTaskGetSchedulerState() { return scheduler; }
static struct { unsigned __cr_flag = 0; } nrf_nvic_state;
using TickType_t = uint32_t;
static uint32_t tick_ms = 1, delayed_ticks = 0;
#define pdMS_TO_TICKS(ms) ((ms) / tick_ms)
static void vTaskDelay(TickType_t ticks) {
  assert(ticks != 0);
  delayed_ticks = ticks;
  events.push_back("delay");
  if (attach_on_delay) usb_held = true; // Model the USB SOF hook observing a new host.
}
static unsigned nrf_feeds = 0;
static constexpr uint32_t WDT_RR_RR_Reload = 0x6e524635;
struct ReloadRegister {
  uint32_t value = 0;
  void operator=(uint32_t reload) {
    assert(reload == WDT_RR_RR_Reload);
    value = reload; ++nrf_feeds; events.push_back("nrf_feed");
  }
};
static struct {
  uint32_t RUNSTATUS = 0, CRV = 60 * 32768UL, RREN = 1;
  ReloadRegister RR[8]{};
} nrf_watchdog;
#define NRF_WDT (&nrf_watchdog)
@NRF_RELOAD@

using esp_err_t = int;
static constexpr int ESP_OK = 0, ESP_ERR_NOT_FOUND = 1, ESP_ERR_INVALID_STATE = 2;
static constexpr int ESP_SLEEP_WAKEUP_TIMER = 3;
static esp_err_t task_watchdog_status = ESP_ERR_INVALID_STATE;
static unsigned task_watchdog_feeds = 0, fail_watchdog_feed = 0;
static esp_err_t esp_task_wdt_status(void*) { return task_watchdog_status; }
static esp_err_t esp_task_wdt_reset() {
  events.push_back("task_feed");
  return ++task_watchdog_feeds == fail_watchdog_feed ? 9 : ESP_OK;
}
static uint64_t timer_us = 0, last_armed_us = 0;
static unsigned timer_calls = 0, sleep_calls = 0, clear_calls = 0;
static uint32_t other_wake_sources = 0x71;
static int timer_error = 0, sleep_error = 0, clear_error = 0;
static esp_err_t esp_sleep_enable_timer_wakeup(uint64_t value) {
  events.push_back("timer"); ++timer_calls;
  if (timer_error) return timer_error;
  timer_us = last_armed_us = value;
  return ESP_OK;
}
static esp_err_t esp_light_sleep_start() {
  events.push_back("sleep"); ++sleep_calls;
  return sleep_error;
}
static esp_err_t esp_sleep_disable_wakeup_source(int source) {
  assert(source == ESP_SLEEP_WAKEUP_TIMER); // Never disable GPIO/ALL sources.
  events.push_back("clear_timer"); ++clear_calls;
  if (clear_error) return clear_error;
  timer_us = 0;
  return ESP_OK;
}
namespace mesh { namespace esp32 {
static bool manualLightSleepReady() { return wireless_ready; }
} }
struct Base {
  @DEFAULT_SUPPORT@
  @DEFAULT_SLEEP@
};
struct NRF52Board : Base {
  bool watchdog_disable_reset_pending = false;
  bool isRadioTestActive() const { return radio_test; }
  bool isUsbHostConnected();
  @NRF_SUPPORT@
  bool emergencySleepMilliseconds(uint32_t millisecs) override;
};
struct ESP32Board : Base {
  bool isRadioTestActive() const { return radio_test; }
  bool isUsbSleepHeld() const { return usb_held; }
  @ESP_SUPPORT@
  bool emergencySleepMilliseconds(uint32_t millisecs) override;
};
@NRF_USB@
@NRF_SLEEP@
@ESP_SLEEP@
static void reset() {
  events.clear(); radio_test = interrupt_context = false; wireless_ready = true;
  usb_held = false;
  usb_mount_checks = 0;
  attach_on_delay = false;
  primask = basepri = faultmask = 0; nrf_nvic_state.__cr_flag = 0;
  scheduler = taskSCHEDULER_RUNNING;
  tick_ms = 1; delayed_ticks = nrf_feeds = 0;
  nrf_watchdog = {0, 60 * 32768UL};
  task_watchdog_status = ESP_ERR_INVALID_STATE;
  task_watchdog_feeds = fail_watchdog_feed = 0;
  timer_us = last_armed_us = 0; timer_calls = sleep_calls = clear_calls = 0;
  timer_error = sleep_error = clear_error = 0; other_wake_sources = 0x71;
}
int main() {
  Base unsupported;
  assert(!unsupported.supportsEmergencySleep());
  assert(!unsupported.emergencySleepMilliseconds(1000));
  NRF52Board nrf; ESP32Board esp;
  Base* supported[] = {&nrf, &esp};
  for (Base* board : supported) assert(board->supportsEmergencySleep());
  for (Base* board : supported) {
    reset(); assert(!board->emergencySleepMilliseconds(0)); assert(events.empty());
    radio_test = true;
    assert(!board->emergencySleepMilliseconds(100)); assert(events.empty());
    reset(); interrupt_context = true;
    assert(!board->emergencySleepMilliseconds(100)); assert(events.empty());
    reset(); scheduler = 0;
    assert(!board->emergencySleepMilliseconds(100)); assert(events.empty());
  }
  for (unsigned* masked : {&primask, &basepri, &faultmask, &nrf_nvic_state.__cr_flag}) {
    reset(); *masked = 1;
    assert(!nrf.emergencySleepMilliseconds(100)); assert(events.empty());
  }
  reset(); nrf.watchdog_disable_reset_pending = true;
  assert(!nrf.emergencySleepMilliseconds(100)); assert(events.empty());
  nrf.watchdog_disable_reset_pending = false;
  reset(); tick_ms = 100;
  assert(!nrf.emergencySleepMilliseconds(1)); assert(events.empty());
  reset(); assert(nrf.emergencySleepMilliseconds(123));
  assert(delayed_ticks == 123 && !nrf_watchdog.RUNSTATUS && !nrf_feeds);
  assert(events == std::vector<std::string>{"delay"});
  reset(); assert(nrf.emergencySleepMilliseconds(UINT32_MAX));
  assert(delayed_ticks == 10000); // Bound the maintenance absence.
  reset(); nrf_watchdog.RUNSTATUS = 1;
  assert(nrf.emergencySleepMilliseconds(10000));
  assert(delayed_ticks == 10000 && nrf_feeds == 2);
  assert((events == std::vector<std::string>{"nrf_feed", "delay", "nrf_feed"}));
  reset(); nrf_watchdog.RUNSTATUS = 1; nrf_watchdog.RREN = 0x82;
  assert(nrf.emergencySleepMilliseconds(100)); assert(nrf_feeds == 4);
  assert(nrf_watchdog.RR[1].value == WDT_RR_RR_Reload);
  assert(nrf_watchdog.RR[7].value == WDT_RR_RR_Reload);
  assert(nrf_watchdog.RR[0].value == 0 && nrf_watchdog.RR[6].value == 0);
  reset(); nrf_watchdog.RUNSTATUS = 1; nrf_watchdog.CRV = 2 * 32768;
  assert(nrf.emergencySleepMilliseconds(10000));
  assert(delayed_ticks == 1000 && nrf_feeds == 2); // Short inherited watchdog.
  reset(); nrf_watchdog.RUNSTATUS = 1; nrf_watchdog.CRV = 32767;
  assert(!nrf.emergencySleepMilliseconds(10000)); assert(events.empty());

  reset(); wireless_ready = false;
  assert(!esp.emergencySleepMilliseconds(100)); assert(events.empty());
  reset(); usb_held = true; wireless_ready = false; timer_us = 555;
  assert(esp.emergencySleepMilliseconds(UINT32_MAX));
  assert(delayed_ticks == 100 && events == std::vector<std::string>{"delay"});
  assert(!timer_calls && !sleep_calls && !clear_calls && timer_us == 555);
  assert(other_wake_sources == 0x71); // USB, RF and existing wake ownership are untouched.
  usb_held = false; events.clear();
  assert(!esp.emergencySleepMilliseconds(100) && events.empty()); // Teardown is still live.
  wireless_ready = true;
  assert(esp.emergencySleepMilliseconds(100)); // Recheck the host on every call; detach permits light sleep.
  assert(last_armed_us == 100000 && sleep_calls == 1 && !timer_us);
  reset(); usb_held = true; task_watchdog_status = ESP_OK;
  assert(esp.emergencySleepMilliseconds(250));
  assert(delayed_ticks == 100 && task_watchdog_feeds == 2);
  assert((events == std::vector<std::string>{"task_feed", "delay", "task_feed"}));
  assert(!timer_calls && !sleep_calls && !clear_calls);
  reset(); usb_held = true; tick_ms = 1000;
  assert(!esp.emergencySleepMilliseconds(100) && events.empty());
  for (unsigned failure : {1U, 2U}) {
    reset(); usb_held = true; task_watchdog_status = ESP_OK; fail_watchdog_feed = failure;
    assert(!esp.emergencySleepMilliseconds(100));
    assert(delayed_ticks == (failure == 1 ? 0U : 100U));
    assert(!timer_calls && !sleep_calls && !clear_calls);
  }
  reset(); task_watchdog_status = 99;
  assert(!esp.emergencySleepMilliseconds(100)); assert(events.empty());
  reset(); assert(esp.emergencySleepMilliseconds(UINT32_MAX));
  assert(sleep_calls == 1 && timer_calls == 1 && clear_calls == 1);
  assert(last_armed_us == 10000000ULL);
  assert(!timer_us && !task_watchdog_feeds && other_wake_sources == 0x71);
  assert((events == std::vector<std::string>{"timer", "sleep", "clear_timer"}));
  reset(); task_watchdog_status = ESP_ERR_NOT_FOUND;
  assert(esp.emergencySleepMilliseconds(250)); assert(!task_watchdog_feeds);
  reset(); task_watchdog_status = ESP_OK;
  assert(esp.emergencySleepMilliseconds(10000));
  assert(last_armed_us == 2000000ULL);
  assert(task_watchdog_feeds == 2 && other_wake_sources == 0x71);
  assert((events == std::vector<std::string>{"task_feed", "timer", "sleep", "clear_timer", "task_feed"}));
  for (int* failed : {&sleep_error, &clear_error}) {
    reset(); *failed = 9;
    assert(!esp.emergencySleepMilliseconds(100));
    assert(sleep_calls == 1 && clear_calls == 1 && other_wake_sources == 0x71);
  }
  reset(); timer_us = 555; timer_error = 9;
  assert(!esp.emergencySleepMilliseconds(100));
  assert(timer_us == 555 && sleep_calls == 0 && clear_calls == 0);
  reset(); task_watchdog_status = ESP_OK; fail_watchdog_feed = 1;
  assert(!esp.emergencySleepMilliseconds(100));
  assert(timer_calls == 0 && sleep_calls == 0 && clear_calls == 0);
  reset(); task_watchdog_status = ESP_OK; fail_watchdog_feed = 2;
  assert(!esp.emergencySleepMilliseconds(100)); assert(clear_calls == 1 && !timer_us);
}
'''


class EmergencySleepBackendTests(unittest.TestCase):
    def platform_code(self):
        sources = {name: (ROOT / path).read_text() for name, path in (
            ("core", "src/MeshCore.h"),
            ("nrf", "src/helpers/NRF52Board.cpp"),
            ("nrf_header", "src/helpers/NRF52Board.h"),
            ("esp", "src/helpers/ESP32Board.cpp"),
            ("esp_header", "src/helpers/ESP32Board.h"))}
        code = HARNESS
        for marker, source, signature in (
            ("DEFAULT_SUPPORT", "core", "virtual bool supportsEmergencySleep() const"),
            ("DEFAULT_SLEEP", "core", "virtual bool emergencySleepMilliseconds("),
            ("NRF_RELOAD", "nrf", "static void reloadWatchdogChannels("),
            ("NRF_SUPPORT", "nrf_header", "bool supportsEmergencySleep() const override"),
            ("NRF_USB", "nrf", "bool NRF52Board::isUsbHostConnected("),
            ("ESP_SUPPORT", "esp_header", "bool supportsEmergencySleep() const override"),
            ("NRF_SLEEP", "nrf", "bool NRF52Board::emergencySleepMilliseconds("),
            ("ESP_SLEEP", "esp", "bool ESP32Board::emergencySleepMilliseconds(")):
            code = code.replace("@" + marker + "@", extract_braced(sources[source], signature))
        return code

    def run_code(self, code, flags=()):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="emergency-sleep-backends-") as directory:
            work = Path(directory)
            cpp, binary = work / "test.cpp", work / "test"
            cpp.write_text(code)
            subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-DCONFIG_ESP_TASK_WDT_TIMEOUT_S=4", *flags, *SANITIZERS,
                            str(cpp), "-o", str(binary)], check=True,
                           capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary)], check=True, capture_output=True,
                           text=True, timeout=20)

    def test_actual_platform_waits_and_cleanup(self):
        self.run_code(self.platform_code())

    def test_nrf_native_usb_keeps_cooperative_maintenance_prompt_without_waking_rf(self):
        runtime = extract_braced(
            (ROOT / "src/helpers/EmergencyPowerRuntime.h").read_text(),
            "class EmergencyPowerRuntime")
        code = self.platform_code().split("int main() {", 1)[0] + r'''
static void delay(uint32_t budget) { events.push_back("backoff:" + std::to_string(budget)); }
namespace mesh { namespace wireless {
struct Control {
  unsigned suspends = 0, resumes = 0;
  bool suspendForEmergency() { ++suspends; return true; }
  void resumeAfterEmergency() { ++resumes; }
};
static Control transport;
static Control& control() { return transport; }
} namespace power {
@RUNTIME@;
} }
struct RuntimeBoard : NRF52Board {
  bool fem_sleeping = false;
  bool isOTAUpdateRunning() const { return false; }
  void setEmergencyRadioSleeping(bool sleeping) { fem_sleeping = sleeping; }
};
struct RuntimeMesh {
  bool available = true;
  bool hasOutbound() const { return false; }
  bool isRadioAvailable() const { return available; }
  void setRadioAvailable(bool value) { available = value; }
};
struct RuntimeRadio {
  unsigned sleeps = 0, wakes = 0;
  bool isMorseTxActive() const { return false; }
  bool isMorseRxActive() const { return false; }
  bool setTrackerSleep(bool sleeping) { sleeping ? ++sleeps : ++wakes; return true; }
  bool recoverRadio(bool) { assert(false); return false; }
};
struct RuntimeCLI {
  uint32_t budget = 2700000;
  bool sleeping = false;
  uint32_t emergencySleepMilliseconds() const { return budget; }
  void setEmergencyRadioSleeping(bool value) { sleeping = value; }
};
int main() {
  reset(); usb_held = true;
  RuntimeBoard board; RuntimeMesh mesh; RuntimeRadio radio; RuntimeCLI cli;
  mesh::power::EmergencyPowerRuntime runtime;
  // Session reset, input, reply construction and queued-output service can
  // require separate sketch passes. Bound the combined CPU absence across
  // those passes with the actual backend and actual RF ownership policy.
  uint32_t elapsed = 0;
  for (unsigned pass = 0; pass < 4; ++pass) {
    assert(runtime.service(mesh, radio, board, cli, false));
    elapsed += delayed_ticks * tick_ms;
    assert(delayed_ticks == 100 && elapsed <= (pass + 1) * 100);
    assert(!mesh.available && cli.sleeping && board.fem_sleeping);
    assert(radio.sleeps == 1 && radio.wakes == 0);
    assert(cli.budget == 2700000); // No awake lease or policy-window change.
    assert(mesh::wireless::transport.resumes == 0);
  }
  assert(usb_mount_checks == 4 && elapsed == 400);
  usb_held = false;
  assert(runtime.service(mesh, radio, board, cli, false));
  assert(delayed_ticks == 10000); // Recheck detach every call.
  assert(!mesh.available && radio.sleeps == 1 && radio.wakes == 0);
  usb_held = true;
  assert(runtime.service(mesh, radio, board, cli, false));
  assert(delayed_ticks == 100 && radio.wakes == 0);
  assert(mesh::wireless::transport.suspends == 1);

  reset(); usb_held = true;
  assert(board.emergencySleepMilliseconds(37) && delayed_ticks == 37);
  reset(); usb_held = true; nrf_watchdog.RUNSTATUS = 1; nrf_watchdog.RREN = 0x82;
  assert(board.emergencySleepMilliseconds(UINT32_MAX));
  assert(delayed_ticks == 100 && nrf_feeds == 4);
  reset(); usb_held = true; nrf_watchdog.RUNSTATUS = 1; nrf_watchdog.CRV = 32767;
  assert(!board.emergencySleepMilliseconds(10000) && events.empty());
  reset(); usb_held = true; tick_ms = 1000;
  assert(!board.emergencySleepMilliseconds(10000) && events.empty());
}
'''.replace("@RUNTIME@", runtime)
        self.run_code(code, ("-DUSE_TINYUSB=1",))

    def test_nrf_without_native_usb_retains_long_sleep_slice(self):
        code = self.platform_code().split("int main() {", 1)[0] + r'''
int main() {
  reset(); usb_held = true;
  NRF52Board board;
  assert(board.emergencySleepMilliseconds(10000));
  assert(delayed_ticks == 10000 && usb_mount_checks == 0);
}
'''
        self.run_code(code)

    def test_native_usb_timer_wake_reserves_enumeration_service_without_extending_budget(self):
        code = self.platform_code().split("int main() {", 1)[0] + r'''
int main() {
  ESP32Board esp;
  reset(); assert(esp.emergencySleepMilliseconds(10000));
  assert(last_armed_us == 9900000ULL && delayed_ticks == 100);
  assert((events == std::vector<std::string>{"timer", "sleep", "clear_timer", "delay"}));
  assert(timer_us == 0 && other_wake_sources == 0x71);
  reset(); task_watchdog_status = ESP_OK;
  assert(esp.emergencySleepMilliseconds(10000));
  assert(last_armed_us == 1900000ULL && delayed_ticks == 100 && task_watchdog_feeds == 2);
  assert((events == std::vector<std::string>{"task_feed", "timer", "sleep", "clear_timer", "delay", "task_feed"}));
  reset(); assert(esp.emergencySleepMilliseconds(125));
  assert(last_armed_us == 25000 && delayed_ticks == 100);
  reset(); assert(esp.emergencySleepMilliseconds(30));
  assert(delayed_ticks == 30 && events == std::vector<std::string>{"delay"});
  assert(!timer_calls && !sleep_calls && !clear_calls);
  reset(); tick_ms = 10;
  assert(esp.emergencySleepMilliseconds(129));
  assert(last_armed_us == 29000 && delayed_ticks == 10); // Timer + floored RTOS wait <=129ms.
  reset(); tick_ms = 1000;
  assert(!esp.emergencySleepMilliseconds(10000) && events.empty());
  for (int* failed : {&sleep_error, &clear_error}) {
    reset(); *failed = 9;
    assert(!esp.emergencySleepMilliseconds(10000));
    assert(!delayed_ticks && clear_calls == 1);
    assert((events == std::vector<std::string>{"timer", "sleep", "clear_timer"}));
  }
  reset(); timer_error = 9;
  assert(!esp.emergencySleepMilliseconds(10000));
  assert(!delayed_ticks && !sleep_calls && !clear_calls);
  reset(); usb_held = true; wireless_ready = false;
  assert(esp.emergencySleepMilliseconds(10000));
  assert(delayed_ticks == 100 && !sleep_calls); // Existing host still bypasses physical USB sleep.
  reset(); attach_on_delay = true;
  assert(esp.emergencySleepMilliseconds(10000) && usb_held);
  assert(sleep_calls == 1 && clear_calls == 1);
  events.clear();
  assert(esp.emergencySleepMilliseconds(10000));
  assert(events == std::vector<std::string>{"delay"} && sleep_calls == 1);
}
'''
        self.run_code(code, ("-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=1",))

    def test_disabled_feature_has_no_new_base_virtuals(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        core = (ROOT / "src/MeshCore.h").read_text()
        start = core.index("#if MESH_ENABLE_EMERGENCY_POWER\n", core.index("class MainBoard"))
        block = core[start:core.index("#endif", start) + len("#endif")]
        code = r'''
#include <cassert>
#include <cstdint>
#include <type_traits>
#include <utility>
struct Board {
@API@
};
template<typename T, typename = void> struct HasApi : std::false_type {};
template<typename T> struct HasApi<T, std::void_t<decltype(
    std::declval<T>().supportsEmergencySleep())>> : std::true_type {};
static_assert(HasApi<Board>::value == bool(MESH_ENABLE_EMERGENCY_POWER));
int main() {
#if MESH_ENABLE_EMERGENCY_POWER
  Board b; assert(!b.supportsEmergencySleep() && !b.emergencySleepMilliseconds(1));
#endif
}
'''.replace("@API@", block)
        with tempfile.TemporaryDirectory(prefix="emergency-sleep-feature-") as directory:
            work = Path(directory)
            cpp, binary = work / "test.cpp", work / "test"
            cpp.write_text(code)
            for enabled in (0, 1):
                subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                f"-DMESH_ENABLE_EMERGENCY_POWER={enabled}", str(cpp),
                                "-o", str(binary)], check=True,
                               capture_output=True, text=True, timeout=60)
                subprocess.run([str(binary)], check=True, capture_output=True,
                               text=True, timeout=20)


if __name__ == "__main__":
    unittest.main()
