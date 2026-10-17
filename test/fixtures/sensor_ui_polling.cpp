// Production header, begin and loop; only hardware/render boundaries are fake.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static uint32_t now_ms = 0, read_delay = 0, render_delay = 0, end_delay = 0;
static unsigned reads = 0, renders = 0;
static constexpr int HIGH = 1, LOW = 0, USER_BTN_PRESSED = LOW;
static constexpr int BUTTON_EVENT_NONE = 0;
static constexpr uint32_t BOOT_SCREEN_MILLIS = 4000;
static int button_level = HIGH;
static uint32_t millis() { return now_ms; }
[[maybe_unused]] static int digitalRead(int pin) {
  if (pin != 7) std::abort();
  ++reads;
  now_ms += read_delay;
  return button_level;
}
struct Radio { bool timing = false; } radio;
namespace mesh {
static bool morseTimingActive(const Radio* r) { return r && r->timing; }
namespace ui { enum class DisplayWake { Button }; }
}
struct Board {
  bool external = false, usb = false;
  bool isExternalPowered() const { return external; }
  bool isUsbHostConnected() const { return usb; }
} board;
struct SensorMesh {
  Radio* getProfileRadio() { return &radio; }
  bool isDualRadioActive() const { return false; }
} sensor_mesh;
static SensorMesh& activeSensorMesh() { return sensor_mesh; }
struct DisplayDriver {
  bool on = true, changed = false;
  unsigned frames = 0, wakes = 0;
  bool servicePower(bool) { bool result = changed; changed = false; return result; }
  bool isOn() const { return on; }
  void wake(mesh::ui::DisplayWake) { on = true; ++wakes; }
  void startFrame() { ++frames; }
  void endFrame() { now_ms += end_delay; }
};
struct MomentaryButton {
  int event = BUTTON_EVENT_NONE;
  bool held = false;
  void begin() { event = BUTTON_EVENT_NONE; }
  int check() { int result = event; event = BUTTON_EVENT_NONE; return result; }
  bool isWakeHoldActive() const { return held; }
} user_btn;
struct NodePrefs {};
#include "UITask.h"

@BEGIN@
@RESET@
@LOOP@
void UITask::renderCurrScreen() { ++renders; now_ms += render_delay; }

static const char* scenario;
static unsigned checks = 0;
#define CHECK(expr) do { ++checks; if (!(expr)) { \
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, #expr, __LINE__); \
  return 1; } } while (0)

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  DisplayDriver display;
  NodePrefs prefs;
  UITask ui(display);
  now_ms = 10000;
  if (!std::strcmp(scenario, "zero")) now_ms = 0;
  else if (!std::strcmp(scenario, "upper-half")) now_ms = 0x80001000u;
  else if (!std::strcmp(scenario, "max")) now_ms = UINT32_MAX;
  else if (!std::strcmp(scenario, "wrapped-future")) now_ms = UINT32_MAX - 99u;
  else if (!std::strcmp(scenario, "zero-boundary")) now_ms = UINT32_MAX - 199u;
  else if (!std::strcmp(scenario, "overdue-wrap") || !std::strcmp(scenario, "morse-wrap"))
    now_ms = UINT32_MAX - 1999u;
  else if (!std::strcmp(scenario, "forced-refresh")) now_ms = 0x80001000u;
  else if (!std::strcmp(scenario, "forced-zero")
      || !std::strcmp(scenario, "momentary-zero-wake")
      || !std::strcmp(scenario, "momentary-zero-morse")) now_ms = UINT32_MAX - 99u;
  ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
  if (!std::strcmp(scenario, "completion-anchor")) {
    read_delay = 10;
    render_delay = 20;
    end_delay = 30;
  }
  ui.loop();
  CHECK(display.frames == 1 && renders == 1);
  CHECK(reads == unsigned(TEST_RAW_BUTTON));

  if (!std::strcmp(scenario, "zero") || !std::strcmp(scenario, "upper-half")
      || !std::strcmp(scenario, "max") || !std::strcmp(scenario, "wrapped-future")) {
    ++now_ms;
    ui.loop();
    CHECK(display.frames == 1 && reads == unsigned(TEST_RAW_BUTTON));
  } else if (!std::strcmp(scenario, "cadence")) {
    now_ms += 199;
    ui.loop();
    CHECK(reads == unsigned(TEST_RAW_BUTTON) && display.frames == 1);
    ++now_ms;
    ui.loop();
    CHECK(reads == 2u * TEST_RAW_BUTTON && display.frames == 1);
    now_ms += 799;
    ui.loop();
    CHECK(display.frames == 1);
    ++now_ms;
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "zero-boundary")) {
    now_ms = UINT32_MAX;
    ui.loop();
    CHECK(reads == unsigned(TEST_RAW_BUTTON) && display.frames == 1);
    now_ms = 0;
    ui.loop();
    CHECK(reads == 2u * TEST_RAW_BUTTON && display.frames == 1);
    now_ms = 799;
    ui.loop();
    CHECK(display.frames == 1);
    ++now_ms;
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "overdue-wrap")) {
    now_ms = 100;
    ui.loop();
    CHECK(display.frames == 2 && reads == 2u * TEST_RAW_BUTTON);
    ui.loop();
    CHECK(display.frames == 2 && reads == 2u * TEST_RAW_BUTTON);
  } else if (!std::strcmp(scenario, "morse-wrap")) {
    radio.timing = true;
    now_ms += 1500;
    ui.loop();
    CHECK(display.frames == 1 && reads == 2u * TEST_RAW_BUTTON);
    now_ms += 1000;
    ui.loop();
    CHECK(display.frames == 1 && reads == 3u * TEST_RAW_BUTTON);
    radio.timing = false;
    ui.loop();
    CHECK(display.frames == 2 && reads == 3u * TEST_RAW_BUTTON);
  } else if (!std::strcmp(scenario, "forced-refresh")) {
    ++now_ms;
    display.changed = true;
    ui.loop();
    CHECK(display.frames == 2);
    ui.loop();
    CHECK(display.frames == 2);
    now_ms = UINT32_MAX;
    display.changed = true;
    ui.loop();
    CHECK(display.frames == 3);
    now_ms = 0;
    ui.loop();
    CHECK(display.frames == 3);
  } else if (!std::strcmp(scenario, "forced-zero")) {
    now_ms = 0;
    display.changed = true;
    ui.loop();
    CHECK(display.frames == 2);
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "forced-morse")) {
    ++now_ms;
    display.changed = true;
    radio.timing = true;
    ui.loop();
    CHECK(display.frames == 1);
    ++now_ms;
    radio.timing = false;
    ui.loop();
    CHECK(display.frames == 2);
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "momentary-zero-wake")
      || !std::strcmp(scenario, "momentary-zero-morse")) {
    const bool during_morse = !std::strcmp(scenario, "momentary-zero-morse");
    display.on = false;
    radio.timing = during_morse;
    now_ms = 0;
    user_btn.event = 1;
    ui.loop();
#if TEST_MOMENTARY_BUTTON
    CHECK(display.on && display.wakes == 2);
    CHECK(display.frames == (during_morse ? 1u : 2u));
    now_ms = 1;
    radio.timing = false;
    ui.loop();
    CHECK(display.frames == 2);
    ui.loop();
    CHECK(display.frames == 2);
#else
    CHECK(!display.on && display.frames == 1 && display.wakes == 0);
#endif
  } else if (!std::strcmp(scenario, "long-display-off")) {
    display.on = false;
    for (unsigned day = 1; day <= 26; ++day) {
      now_ms = 10000u + day * 86400000u;
      ui.loop();
      CHECK(display.frames == 1);
    }
    display.on = true; // No force event: elapsed work must remain due.
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "completion-anchor")) {
    const uint32_t poll_finished = 10000u + (TEST_RAW_BUTTON ? 10u : 0u);
    const uint32_t frame_finished = poll_finished + 50u;
    read_delay = render_delay = end_delay = 0;
    now_ms = poll_finished + 199u;
    ui.loop();
    CHECK(reads == unsigned(TEST_RAW_BUTTON) && display.frames == 1);
    ++now_ms;
    ui.loop();
    CHECK(reads == 2u * TEST_RAW_BUTTON && display.frames == 1);
    now_ms = frame_finished + 999u;
    ui.loop();
    CHECK(display.frames == 1);
    ++now_ms;
    ui.loop();
    CHECK(display.frames == 2);
  } else if (!std::strcmp(scenario, "rebegin")) {
    ++now_ms;
    ui.begin(&prefs, "16 Oct 2026", "v1.2.4-test");
    ui.loop();
    CHECK(display.frames == 2 && reads == 2u * TEST_RAW_BUTTON);
  } else if (!std::strcmp(scenario, "button-wake")) {
    display.on = false;
    now_ms += 200;
    button_level = LOW;
    user_btn.event = 1;
    ui.loop();
#if TEST_RAW_BUTTON
    CHECK(display.on && display.wakes == 2 && reads == 2);
    // Existing raw-button policy keeps the one-second render throttle.
    CHECK(display.frames == 1);
#elif TEST_MOMENTARY_BUTTON
    CHECK(display.on && display.wakes == 2 && display.frames == 2);
    user_btn.held = true;
    ui.loop();
    CHECK(display.wakes == 3 && display.frames == 2);
#else
    CHECK(!display.on && display.wakes == 0 && display.frames == 1);
#endif
  } else {
    std::fprintf(stderr, "unknown scenario: %s\n", scenario);
    return 2;
  }
  std::printf("PASS %s: %u checks\n", scenario, checks);
}
