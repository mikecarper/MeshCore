#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

static uint32_t now_ms;
static constexpr uint32_t BOOT_SCREEN_MILLIS = 4000;
static uint32_t millis() { return now_ms; }
static constexpr int HIGH = 1;
static bool deny_copy;
static unsigned copies, resets, flips, palettes;
[[maybe_unused]] static char* copyVersion(const char* text) {
  ++copies;
  return deny_copy ? nullptr : ::strdup(text);
}
#define strdup copyVersion
namespace mesh {
struct MainBoard {
  bool external = false, usb = false;
  bool isExternalPowered() const { return external; }
  bool isUsbHostConnected() const { return usb; }
};
}
static mesh::MainBoard board;
struct DisplayDriver {
  unsigned power_calls = 0;
  bool servicePower(bool powered) {
    if (powered != (board.external || board.usb)) std::abort();
    ++power_calls;
    return false;
  }
};
struct NodePrefs {};
struct Button { unsigned begins = 0; void begin() { ++begins; } } user_btn;
struct CHSC6XTouch { unsigned begins = 0; void begin() { ++begins; } };
struct RadioActivityWindow {};
struct ObserverDashboard {
  enum { ROW_COUNT = 4 };
  struct Context {};
  static void applyDarkPalette() { ++palettes; }
};
#include "UITask.h"
void UITask::resetRadioProfileDisplayPage() { ++resets; }
#if !TEST_SENSOR
void UITask::applyDisplayFlip() { ++flips; }
#endif
@BEGIN@

static const char* scenario;
static unsigned checks;
#define CHECK(expr) do { ++checks; if (!(expr)) { \
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, #expr, __LINE__); \
  return false; } } while (0)

static bool exercise(const std::string& version, const std::string& date,
                     bool denied, bool require_no_copy, uint32_t now, unsigned power) {
  const std::string saved_version = version, saved_date = date;
  copies = resets = flips = palettes = user_btn.begins = 0;
  deny_copy = denied;
  now_ms = now;
  board.external = (power & 1) != 0;
  board.usb = (power & 2) != 0;
  DisplayDriver display;
  NodePrefs prefs;
#if TEST_REPEATER || (!TEST_SENSOR && defined(DISPLAY_TOUCH_TOGGLE))
  UITask ui(board, display);
#else
  UITask ui(display);
#endif
#if !TEST_SENSOR && defined(DISPLAY_REDRAW_ON_CHANGE)
  ui._frame_valid = true;
#endif
  std::memset(ui._version_info, 0x55, sizeof(ui._version_info));
  ui.begin(&prefs, date.c_str(), version.c_str());
  const std::string full = version.substr(0, version.find('-')) + " (" + date + ")";
  const std::string expected = full.substr(0, sizeof(ui._version_info) - 1);
  CHECK(std::memchr(ui._version_info, 0, sizeof(ui._version_info)) != nullptr);
  CHECK(expected == ui._version_info);
  CHECK(version == saved_version && date == saved_date);
  CHECK(ui._node_prefs == &prefs && ui._display == &display && ui._prevBtnState == HIGH);
  CHECK(display.power_calls == 1 && resets == 1);
  CHECK(user_btn.begins == unsigned(TEST_FEATURES != 0));
  if (require_no_copy) CHECK(copies == 0);
#if TEST_SENSOR
  CHECK(ui._last_read == uint32_t(now - 200u));
  CHECK(ui._last_refresh == uint32_t(now - 1000u));
  CHECK(flips == 0 && palettes == 0);
#else
  CHECK(ui._started_at == now && flips == 1);
  CHECK(palettes == unsigned(TEST_FEATURES == 2));
#ifdef DISPLAY_REDRAW_ON_CHANGE
  CHECK(!ui._frame_valid);
#endif
#ifdef DISPLAY_TOUCH_TOGGLE
  CHECK(ui._touch.begins == 1);
#endif
#endif
  return true;
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  if (!std::strcmp(scenario, "denied")) {
    if (!exercise("v1.17.1.10-halo-keymind-cascade-dev-7acd76d0", "17-Oct-2026",
                  true, true, 1234, 2)) return 1;
  } else if (!std::strcmp(scenario, "format") || !std::strcmp(scenario, "no-copy")) {
    const bool no_copy = !std::strcmp(scenario, "no-copy");
    for (const std::string& version : {std::string(), std::string("v1.18.0"),
         std::string("v1.18.0-alpha-beta"), std::string("-hash"), std::string("v1-"),
         std::string(30, 'v'), std::string(31, 'v'), std::string(32, 'v'),
         std::string(33, 'v'), std::string(31, 'v') + "-hash", std::string(32768, 'v')}) {
      for (const std::string& date : {std::string("17-Oct-2026"), std::string(),
                                    std::string(4096, 'd')}) {
        for (uint32_t now : {0u, 1234u, UINT32_MAX}) for (unsigned power = 0; power < 4; ++power) {
          if (!exercise(version, date, false, no_copy, now, power)) return 1;
        }
      }
    }
  } else return 2;
  std::printf("PASS %s: %u checks\n", scenario, checks);
}
