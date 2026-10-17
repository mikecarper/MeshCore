// Actual Sensor UI methods and header; infrastructure init/prefix/metadata only.
// Hardware, optional status-page and radio dependencies are explicit adapters.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static uint32_t now_ms = 0;
static uint32_t millis() { return now_ms; }
static constexpr int HIGH = 1;
enum class UIColor { corp_blue, primary_txt, secondary_txt, warning_txt };
namespace mesh {
struct Params { float freq = 915, bw = 125; uint8_t sf = 7, cr = 5; };
struct RadioProfiles {
  bool enabled() const { return false; }
  bool secondary_temporary = false, primary_temporary = false;
  const Params& params(int) const { static Params value; return value; }
};
struct MainBoard {
  bool isExternalPowered() const { return true; }
  bool isUsbHostConnected() const { return true; }
};
namespace ui {
enum class DisplayWake { Button };
struct RadioProfileSystemStatus {};
[[maybe_unused]] static bool showSecondaryRadioProfilePage(bool, uint8_t, uint32_t) {
  return false;
}
[[maybe_unused]] static bool showRadioProfileSystemStatusPage(bool, uint8_t, uint32_t,
                                                           uint8_t*) { return false; }
[[maybe_unused]] static const char* radioProfileDisplayTag(int, bool) { return ""; }
}
}
struct DisplayDriver {
  bool on = true, changed = false;
  unsigned frames = 0, logos = 0, homes = 0;
  bool servicePower(bool) { bool result = changed; changed = false; return result; }
  bool isOn() const { return on; }
  void wake(mesh::ui::DisplayWake) { on = true; }
  void startFrame() { ++frames; }
  void endFrame() {}
  void setColor(UIColor) {}
  void setTextSize(float) {}
  void setCursor(int, int) {}
  int width() const { return 128; }
  unsigned getTextWidth(const char* text) const { return std::strlen(text) * 6; }
  void drawXbm(int, int, const uint8_t*, int, int) { ++logos; }
  void print(const char* text) { if (!std::strcmp(text, "normal-node")) ++homes; }
};
namespace mesh { namespace ui {
[[maybe_unused]] static void drawRadioProfileSystemStatusPage(
    DisplayDriver&, const RadioProfileSystemStatus&, uint8_t) {}
}}
static mesh::MainBoard board;
struct Radio { bool timing = false; } radio;
namespace mesh {
[[maybe_unused]] static bool morseTimingActive(const Radio* value) { return value->timing; }
}
struct SensorMesh {
  Radio* getProfileRadio() { return &radio; }
  bool isDualRadioActive() const { return false; }
} sensor_mesh;
[[maybe_unused]] static SensorMesh& activeSensorMesh() { return sensor_mesh; }
static SensorMesh& the_mesh = sensor_mesh;
struct NodePrefs {
  char node_name[32] = "normal-node";
  float freq = 915, bw = 125;
  uint8_t sf = 7, cr = 5;
};
[[maybe_unused]] static const mesh::RadioProfiles* configuredRadioProfiles() { return nullptr; }
[[maybe_unused]] static mesh::ui::RadioProfileSystemStatus radioProfileSystemStatus(
    const NodePrefs&) { return {}; }
[[maybe_unused]] static uint8_t radioProfileStatusPageCount(DisplayDriver&, bool) { return 0; }
[[maybe_unused]] static const uint8_t meshcore_logo[] = {0};
#define BOOT_SCREEN_MILLIS 4000
#include "UITask.h"

@METHODS@

static const char* scenario;
static unsigned checks = 0;
#define CHECK(expression) do { ++checks; if (!(expression)) { \
  std::fprintf(stderr, "FAIL %s: %s (line %d)\n", scenario, #expression, __LINE__); \
  return 1; } } while (0)

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  scenario = argv[1];
  DisplayDriver display;
  NodePrefs prefs;
#if TEST_REPEATER
  UITask ui(board, display);
#else
  UITask ui(display);
#endif
#if TEST_SENSOR
  const bool late = !std::strcmp(scenario, "late-begin");
  const bool upper = !std::strcmp(scenario, "upper-begin");
  const bool maximum = !std::strcmp(scenario, "max-begin");
  const bool off_first = !std::strcmp(scenario, "off-first");
  const bool morse_first = !std::strcmp(scenario, "morse-first");
  now_ms = late ? 4000u : upper ? 0x80001000u : maximum ? UINT32_MAX : 0u;
  display.on = !off_first;
  radio.timing = morse_first;
  ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
  ui.loop();
  CHECK(display.logos == unsigned(!late && !upper && !maximum && !off_first && !morse_first));
  CHECK(display.homes == unsigned(late || upper || maximum));
  if (!std::strcmp(scenario, "boundary")) {
    now_ms = 3999;
    display.changed = true;
    ui.loop();
    CHECK(display.logos == 2 && display.homes == 0);
    now_ms = 4000;
    display.changed = true;
    ui.loop();
    CHECK(display.logos == 2 && display.homes == 1);
  } else if (late || upper || maximum) {
    // Re-entering the low boot clock range must not undo begin's late cutoff.
    now_ms = 0;
    display.changed = true;
    ui.loop();
    CHECK(display.logos == 0 && display.homes == 2);
  } else if (!std::strcmp(scenario, "live-wrap")) {
    now_ms = 5000;
    ui.loop();
    CHECK(display.logos == 1 && display.homes == 1);
    for (uint64_t day = 1; day <= 49; ++day) {
      now_ms = static_cast<uint32_t>(day * 86400000ULL);
      ui.loop();
      CHECK(display.logos == 1);
    }
    const unsigned homes = display.homes;
    now_ms = 0; // absolute 2^32, reached after the live checkpoints above
    ui.loop();
    CHECK(display.logos == 1 && display.homes == homes + 1);
  } else if (!std::strcmp(scenario, "off-retirement")
      || !std::strcmp(scenario, "morse-retirement") || off_first || morse_first) {
    const bool off = off_first || !std::strcmp(scenario, "off-retirement");
    display.on = !off;
    radio.timing = !off;
    const unsigned frames = display.frames, logos = display.logos;
    now_ms = 4000;
    ui.loop();
    CHECK(display.frames == frames);
    now_ms = 0; // full-wrap alias, after observed expiry with no rendered frame
    display.on = true;
    display.changed = true;
    radio.timing = false;
    ui.loop();
    CHECK(display.logos == logos && display.homes == 1);
  } else if (!std::strcmp(scenario, "wake")) {
    now_ms = 4000;
    ui.loop();
    CHECK(display.logos == 1 && display.homes == 1);
    display.on = false;
    now_ms = 12000;
    ui.loop();
    now_ms = 0;
    display.wake(mesh::ui::DisplayWake::Button);
    display.changed = true;
    ui.loop();
    CHECK(display.logos == 1 && display.homes == 2);
  } else if (!std::strcmp(scenario, "rebegin")) {
    now_ms = 4000;
    ui.loop();
    CHECK(display.logos == 1 && display.homes == 1);
    now_ms = 100;
    ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
    ui.loop();
    CHECK(display.logos == 2 && display.homes == 1);
    now_ms = 4100;
    ui.loop();
    CHECK(display.logos == 2 && display.homes == 2);
    now_ms = 5000;
    ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
    ui.loop();
    CHECK(display.logos == 2 && display.homes == 3);
  } else return 3;
#else
  const bool wrapping = !std::strcmp(scenario, "infra-wrap-start");
  const uint32_t start = wrapping ? UINT32_MAX - 1000u : 1000u;
  now_ms = start;
  ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
  CHECK(ui.testRenderBoot() && ui.testSignatureBoot());
  now_ms = start + 3999u;
  ui.loop();
  CHECK(ui.testRenderBoot() && ui.testSignatureBoot());
  now_ms = start + 4000u;
  ui.loop();
  CHECK(!ui.testRenderBoot() && !ui.testSignatureBoot());
  now_ms = start; // full-wrap alias, same retained boot timestamp
  ui.loop();
  CHECK(!ui.testRenderBoot() && !ui.testSignatureBoot());
  if (!std::strcmp(scenario, "infra-rebegin")) {
    ui.begin(&prefs, "16 Oct 2026", "v1.2.3-test");
    CHECK(ui.testRenderBoot() && ui.testSignatureBoot());
  } else if (std::strcmp(scenario, "infra-boundary") && !wrapping) return 3;
#endif
  std::printf("PASS %s (%u checks)\n", scenario, checks);
}
