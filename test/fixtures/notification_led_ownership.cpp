#include "notification_harness.h"
#include <cstdio>
#include <cstring>

namespace {
unsigned checks = 0, failures = 0, scenarios = 0;
const char* scenario = nullptr;

void check(bool condition, const char* expression, unsigned line) {
  ++checks;
  if (!condition) {
    ++failures;
    std::printf("FAIL: %s:%u: %s [R=%u G=%u B=%u]\n", scenario, line,
                expression, levels[3], levels[24], levels[28]);
  }
}
#define CHECK(expression) check(bool(expression), #expression, __LINE__)

template <typename Action>
void run(const char* name, Action action) {
  scenario = name;
  ++scenarios;
  resetIo();
  const unsigned before = failures;
  action();
  if (before == failures) std::printf("PASS: %s\n", name);
}

void command(MyMesh& node, const char* text, const char* expected = "OK", uint32_t now = 1000) {
  char reply[160] = {};
  CHECK(node._notifications.handle(text, reply, sizeof(reply), now, false));
  CHECK(!std::strcmp(reply, expected));
}

void expectPinFamily() {
  for (unsigned pin = 0; pin < 64; ++pin) {
#if EXPECT_RGB
    CHECK(digital_writes[pin] == 0 && mode_writes[pin] == 0);
    if (pin != 3 && pin != 24 && pin != 28) CHECK(analog_writes[pin] == 0);
#elif EXPECT_MONO
    CHECK(analog_writes[pin] == 0);
    if (pin != 24) CHECK(digital_writes[pin] == 0 && mode_writes[pin] == 0);
#else
    CHECK(analog_writes[pin] == 0 && digital_writes[pin] == 0 && mode_writes[pin] == 0);
#endif
  }
}

void expectLed(bool on) {
#if EXPECT_RGB
  CHECK(levels[3] == (on ? 255u : 0u));
  CHECK(levels[24] == (on ? 90u : 0u));
  CHECK(levels[28] == 0);
#elif EXPECT_MONO
  CHECK(levels[24] == (on ? EXPECT_ON_LEVEL : 255 - EXPECT_ON_LEVEL));
#else
  (void)on;
  CHECK(ioWrites() == 0);
#endif
  expectPinFamily();
}

void seedPreviousLed(MyMesh& node) {
#if EXPECT_RGB
  (void)node;
  seedUiRgb();
  CHECK(levels[3] == 200 && levels[24] == 120 && levels[28] == 90);
#elif EXPECT_MONO
  node.pulse(mesh::notify::Led, true, -1);
  expectLed(true);
#else
  (void)node;
  seedUiRgb();
  CHECK(ioWrites() == 0);
#endif
}

void checkGuard(MyMesh& node, bool admitted) {
  const unsigned before_admitted = ui_admitted, before_writes = ioWrites();
  node.ui.userLedHandler();
  CHECK(ui_admitted == before_admitted + unsigned(admitted));
  CHECK(ioWrites() == before_writes);
}

[[maybe_unused]] void startAlert(MyMesh& node) {
  seedPreviousLed(node);
  command(node, "set notify.led all 50,50");
  command(node, "notify.test all", "OK - alert test started");
  CHECK(node._notifications.active());
  CHECK(node._notifications.ownsLed());
}
}  // namespace

int main() {
  run("capabilities", [] {
    MyMesh node;
    CHECK(bool(node.capabilities() & mesh::notify::Led) == bool(EXPECT_RGB || EXPECT_MONO));
    CHECK(!(node.capabilities() & mesh::notify::Gpio));
    node.ui.extra_capabilities = mesh::notify::Sound;
    CHECK(node.capabilities() & mesh::notify::Sound);
    node._ui = nullptr;
    CHECK(bool(node.capabilities() & mesh::notify::Led) == bool(EXPECT_RGB || EXPECT_MONO));
    CHECK(!(node.capabilities() & mesh::notify::Sound));
    CHECK(ioWrites() == 0);
  });

  run("actual-ui-writer-and-ownership-guard", [] {
    MyMesh node;
#if EXPECT_RGB
    seedUiRgb();
    CHECK(levels[3] == 200 && levels[24] == 120 && levels[28] == 90);
    clearUiRgb();
    CHECK(levels[3] == 0 && levels[24] == 0 && levels[28] == 0);
#else
    seedUiRgb();
    clearUiRgb();
    CHECK(ioWrites() == 0);
#endif
    checkGuard(node, true);
    node.ui.setNotificationLedOwnership(true);
    checkGuard(node, false);
    node.ui.setNotificationLedOwnership(false);
    node.ui.setNotificationOutputs(31 & ~mesh::notify::Led);
    checkGuard(node, false);
    expectPinFamily();
  });

  run("master-led-off-clears-idle-ui", [] {
    MyMesh node;
    seedPreviousLed(node);
    command(node, "set notify.led off");
    CHECK(node._notifications.settings().enabled == 1);
    CHECK(node._notifications.settings().outputs == (31 & ~mesh::notify::Led));
    node.syncNotifications();
    CHECK(!node.ui._notification_led_owned);
    CHECK(!(node.ui._notification_outputs & mesh::notify::Led));
    expectLed(false);
    checkGuard(node, false);
  });

#if EXPECT_RGB || EXPECT_MONO
  run("healthy-led-on-colors", [] {
    MyMesh node;
    command(node, "set notify.led on");
    startAlert(node);
    expectLed(true);
    node.syncNotifications();
    checkGuard(node, false);
    node._notifications.loop(1049, false);
    CHECK(node._notifications.active() && node._notifications.ownsLed());
    expectLed(true);
  });

  run("inactive-pulse-phase-stays-owned", [] {
    MyMesh node;
    startAlert(node);
    node._notifications.loop(1050, false);
    CHECK(node._notifications.active() && node._notifications.ownsLed());
    expectLed(false);
    node.syncNotifications();
    checkGuard(node, false);
    node._notifications.loop(1099, false);
    CHECK(node._notifications.active() && node._notifications.ownsLed());
    expectLed(false);
  });

  run("command-stop-from-active-phase", [] {
    MyMesh node;
    startAlert(node);
    command(node, "notify.stop", "OK - alerts stopped", 1020);
    CHECK(!node._notifications.active() && !node._notifications.ownsLed());
    expectLed(false);  // Check before releasing the heartbeat guard.
    const unsigned before_writes = ioWrites();
    command(node, "notify.stop", "OK - alerts stopped", 1021);
    CHECK(ioWrites() == before_writes);
    node.syncNotifications();
    checkGuard(node, true);
  });

  run("command-stop-from-inactive-phase", [] {
    MyMesh node;
    startAlert(node);
    node._notifications.loop(1050, false);
    command(node, "notify.stop", "OK - alerts stopped", 1075);
    CHECK(!node._notifications.active() && !node._notifications.ownsLed());
    expectLed(false);
    node.syncNotifications();
    checkGuard(node, true);
  });

  run("natural-completion-clears-before-ui-release", [] {
    MyMesh node;
    startAlert(node);
    node._notifications.loop(1100, false);
    CHECK(!node._notifications.active() && !node._notifications.ownsLed());
    expectLed(false);
    node.syncNotifications();
    checkGuard(node, true);
  });

  run("master-led-off-stops-active-alert", [] {
    MyMesh node;
    startAlert(node);
    command(node, "set notify.led off", "OK", 1020);
    CHECK(!node._notifications.active() && !node._notifications.ownsLed());
    node.syncNotifications();
    expectLed(false);
    checkGuard(node, false);
  });

  run("disabled-feature-restores-ui-policy", [] {
    MyMesh node;
    startAlert(node);
    command(node, "set notify.enabled off", "OK", 1020);
    CHECK(!node._notifications.active() && !node._notifications.ownsLed());
    expectLed(false);
    node.syncNotifications();
    CHECK(node.ui._notification_outputs == 31);
    checkGuard(node, true);
  });
#else
  run("unsupported-led-commands-and-direct-sink-are-inert", [] {
    MyMesh node;
    unsigned char before[sizeof(mesh::notify::Settings)];
    std::memcpy(before, &node._notifications.settings(), sizeof(before));
    command(node, "set notify.led on", "Error: output unsupported on this build");
    command(node, "set notify.led all 50,50", "Error: output unsupported on this build");
    CHECK(node.saves == 0);
    CHECK(!std::memcmp(before, &node._notifications.settings(), sizeof(before)));
    CHECK(!node._notifications.message(nullptr, nullptr, false, 1000));
    node.pulse(mesh::notify::Led, true, -1);
    node.pulse(mesh::notify::Led, false, -1);
    expectLed(false);
    command(node, "set notify.led all off");
    CHECK(node.saves == 1);
    expectLed(false);
  });
#endif
  std::printf("%u scenarios, %u checks, %u failures\n", scenarios, checks, failures);
  return failures ? 1 : 0;
}
