// Actual guarded loop block and timer field are injected without rewriting.
// Only Arduino clock/GPIO and display wake are host boundary adapters.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static uint32_t clock_ms = 0;
uint32_t millis() { return clock_ms; }
static unsigned reads = 0, wakes = 0;
static uint32_t read_duration = 0, wake_duration = 0;
static bool pin_pressed = false;
static const char* active_case = "startup";
static unsigned checks = 0;
#define CHECK(value) do { \
  ++checks; \
  if (!(value)) { \
    std::fprintf(stderr, "FAIL %s: %s (line %d)\n", active_case, #value, __LINE__); \
    std::exit(1); \
  } \
} while (0)

#if defined(BACKLIGHT_BTN)
constexpr unsigned LOW = 0, HIGH = 1;
int digitalRead(unsigned pin) {
  CHECK(pin == PIN_BUTTON2);
  ++reads;
  clock_ms += read_duration;
  return pin_pressed ? LOW : HIGH;
}
#endif

namespace mesh { namespace ui { enum class DisplayWake { Button }; } }
struct DisplayDriver {
  void wake(mesh::ui::DisplayWake reason) {
    CHECK(reason == mesh::ui::DisplayWake::Button);
    ++wakes;
    clock_ms += wake_duration;
  }
} display;

struct UITask {
  @FIELD@
  DisplayDriver* _display = &display;
  void poll() {
@BLOCK@
  }
};

static void at(UITask& ui, uint32_t now, bool pressed,
               unsigned expected_reads, unsigned expected_wakes) {
  clock_ms = now;
  pin_pressed = pressed;
  ui.poll();
  CHECK(reads == expected_reads);
  CHECK(wakes == expected_wakes);
}

#if defined(BACKLIGHT_BTN)
static void cadence(UITask& ui, uint32_t start, bool initially_pressed) {
  const unsigned first_wake = initially_pressed ? 1 : 0;
  at(ui, start, initially_pressed, 1, first_wake);
  at(ui, start, initially_pressed, 1, first_wake);
  at(ui, start + 1, !initially_pressed, 1, first_wake);
  at(ui, start + 300, !initially_pressed, 1, first_wake);
  at(ui, start + 301, !initially_pressed, 2, 1);
  at(ui, start + 301, !initially_pressed, 2, 1);
}
#endif

// Every case is a fresh process: real static sampled-button state is retained.
int main(int argc, char** argv) {
  CHECK(argc == 2);
  active_case = argv[1];
  UITask ui;
  const char* name = argv[1];
#if defined(BACKLIGHT_BTN)
  if (!std::strcmp(name, "zero")) {
    at(ui, 0, true, 0, 0);
    cadence(ui, 1, true);
  } else if (!std::strcmp(name, "upper")) {
    cadence(ui, 0x80000000u, false);
  } else if (!std::strcmp(name, "upper-pressed")) {
    cadence(ui, 0x80000000u, true);
  } else if (!std::strcmp(name, "max")) {
    cadence(ui, UINT32_MAX, false);
  } else if (!std::strcmp(name, "normal")) {
    cadence(ui, 100, false);
  } else if (!std::strcmp(name, "signed-boundary")) {
    cadence(ui, 0x7fffffffu - 150, false);
  } else if (!std::strcmp(name, "wrap-visible")) {
    cadence(ui, UINT32_MAX - 15, false);
  } else if (!std::strcmp(name, "wrap-wake")) {
    at(ui, UINT32_MAX - 310, false, 1, 0);
    at(ui, 20, true, 2, 1);
    at(ui, 20, true, 2, 1);
  } else if (!std::strcmp(name, "deadline-zero")) {
    cadence(ui, uint32_t(0u - 300u), false);
  } else if (!std::strcmp(name, "held")) {
    at(ui, 1, true, 1, 1);
    at(ui, 302, true, 2, 1);
    at(ui, 603, true, 3, 1);
    at(ui, 904, false, 4, 1);
    at(ui, 1205, true, 5, 2);
  } else if (!std::strcmp(name, "unobserved-release")) {
    at(ui, 1, true, 1, 1);
    at(ui, 100, false, 1, 1);
    at(ui, 101, true, 1, 1);
    at(ui, 302, true, 2, 1);
  } else if (!std::strcmp(name, "null-pressed")) {
    ui._display = nullptr;
    at(ui, 1, true, 1, 0);
    ui._display = &display;
    at(ui, 302, true, 2, 0);
    at(ui, 603, false, 3, 0);
    at(ui, 904, true, 4, 1);
  } else if (!std::strcmp(name, "null-released")) {
    ui._display = nullptr;
    at(ui, 1, false, 1, 0);
    ui._display = &display;
    at(ui, 302, true, 2, 1);
  } else if (!std::strcmp(name, "completion")) {
    read_duration = 7;
    wake_duration = 37;
    at(ui, 100, true, 1, 1);
    CHECK(clock_ms == 144);
    read_duration = wake_duration = 0;
    at(ui, 444, false, 1, 1);
    at(ui, 445, false, 2, 1);
    at(ui, 745, true, 2, 1);
    at(ui, 746, true, 3, 2);
  } else if (!std::strcmp(name, "completion-zero-wake")) {
    wake_duration = 11;
    at(ui, UINT32_MAX - 10, true, 1, 1);
    CHECK(clock_ms == 0);
    wake_duration = 0;
    at(ui, 0, true, 1, 1);
    at(ui, 1, true, 1, 1);
    at(ui, 300, false, 1, 1);
    at(ui, 301, false, 2, 1);
  } else if (!std::strcmp(name, "completion-zero-read")) {
    read_duration = 11;
    at(ui, UINT32_MAX - 10, false, 1, 0);
    CHECK(clock_ms == 0);
    read_duration = 0;
    at(ui, 0, true, 1, 0);
    at(ui, 300, true, 1, 0);
    at(ui, 301, true, 2, 1);
  } else if (!std::strcmp(name, "long-released")) {
    at(ui, 1, false, 1, 0);
    at(ui, 1u + 26u * 24u * 60u * 60u * 1000u, true, 2, 1);
  } else if (!std::strcmp(name, "long-pressed")) {
    at(ui, 1, true, 1, 1);
    const uint32_t later = 1u + 26u * 24u * 60u * 60u * 1000u;
    at(ui, later, true, 2, 1);
    at(ui, later + 301, false, 3, 1);
    at(ui, later + 602, true, 4, 2);
  } else if (!std::strcmp(name, "near-full-cycle")) {
    at(ui, 100, false, 1, 0);
    at(ui, uint32_t(100u + UINT32_MAX - 1000u), true, 2, 1);
  } else if (!std::strcmp(name, "late-one-poll")) {
    at(ui, 1, false, 1, 0);
    at(ui, 100000, true, 2, 1);
    at(ui, 100000, false, 2, 1);
    at(ui, 100300, false, 2, 1);
    at(ui, 100301, false, 3, 1);
  } else {
    std::fprintf(stderr, "unknown case: %s\n", name);
    return 2;
  }
#else
  CHECK(std::strcmp(name, "no-feature") == 0);
  const uint32_t stamps[] = {0u, 1u, 0x7fffffffu, 0x80000000u, UINT32_MAX};
  for (uint32_t stamp : stamps) {
    at(ui, stamp, true, 0, 0);
    at(ui, stamp, false, 0, 0);
  }
  CHECK(read_duration == 0 && wake_duration == 0);
#endif
  std::printf("PASS %s: %u checks\n", name, checks);
}
