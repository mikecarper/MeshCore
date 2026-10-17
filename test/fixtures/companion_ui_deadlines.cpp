// Compile the complete production UITask::loop() with a true 32-bit clock.
// Screen rendering, radio timing, display and unrelated services are adapters;
// deadline, popup, polling and scheduling decisions remain production source.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

static uint32_t now_ms = 0;
static uint32_t millis() { return now_ms; }
static void delay(uint32_t duration) { now_ms += duration; }
static constexpr uint32_t BOOT_SCREEN_MILLIS = 3000;
#define MESH_DEBUG_PRINTLN(...) do {} while (0)
struct Radio { bool timing = false; } radio_driver;
struct TestMesh { unsigned rescues = 0; void enterCLIRescue() { ++rescues; } } the_mesh;
namespace mesh {
static bool morseTimingActive(const Radio* radio) { return radio && radio->timing; }
namespace ui { enum class DisplayWake { Button }; }
}
namespace UIColor { constexpr int popup_bkg = 0, popup_txt = 1, warning_txt = 2; }
struct Board { bool isExternalPowered() const { return false; } } board;
struct Display {
  bool on = true;
  unsigned frames = 0, popups = 0;
  bool isOn() const { return on; }
  bool isEink() const { return false; }
  int height() const { return 64; }
  int width() const { return 128; }
  void wake(mesh::ui::DisplayWake) { on = true; }
  void startFrame() { ++frames; }
  void endFrame() {}
  void setTextSize(int) {}
  void setColor(int) {}
  void fillRect(int, int, int, int) {}
  void drawRect(int, int, int, int) {}
  void drawTextCentered(int, int, const char* text) { if (!std::strcmp(text, "ALERT")) ++popups; }
};
struct UIScreen {
  unsigned renders = 0, polls = 0;
  int delay_ms = 1000;
  int render(Display&) { ++renders; return delay_ms; }
  void poll() { ++polls; }
  void handleInput(char) {}
};
struct HomeScreen : UIScreen { void showFirstPage() {} };
struct StatusBar {
  void update(Display&, const char*, uint16_t, bool, bool, bool) {}
  bool needsRedraw() const { return false; }
  void render(Display&) {}
};
struct UITask {
  Display display;
  Display* _display = &display;
  HomeScreen screen;
  UIScreen* curr = &screen;
  UIScreen* home = &screen;
  UIScreen preview;
  UIScreen* msg_preview = &preview;
  StatusBar _statusBar;
  struct Prefs { const char* node_name = "test"; } prefs;
  Prefs* _node_prefs = &prefs;
  // Arduino unsigned long is 32 bits; the host unsigned long is NOT used.
  uint32_t _next_refresh = 0, _alert_expiry = 0, ui_started_at = 0, next_batt_chck = 0;
  uint32_t _pairing_screen_until = 0;
  bool _pairing_from_button = false;
  bool _startup_complete = false;
  uint16_t _cached_batt_mv = 4000, voltage = 4000;
  bool gesture_pending = false;
  bool _need_refresh = true;
  unsigned battery_reads = 0, shutdowns = 0, profile_services = 0;
  char _alert[80] = {}, _origin[80] = {}, _msg[80] = {};
  void servicePairingState() {}
  void serviceWiFiToggleButton() {}
  bool isPairingScreenActive() const { return false; }
  bool isButtonGesturePending() const { return gesture_pending; }
  bool isBuzzerQuiet() const { return true; }
  bool getGPSState() const { return false; }
  bool isBluetoothEnabled() const { return false; }
  void userLedHandler() {}
  void renderPairingBanner() {}
  void serviceRadioProfileDisplayPage() { ++profile_services; }
  void renderCurrScreen() { ++screen.renders; _need_refresh = false; }
  uint16_t getBattMilliVolts() { ++battery_reads; return voltage; }
  void shutdown() { ++shutdowns; }
  void showAlert(const char* text, int duration_millis);
  void setCurrScreen(UIScreen* screen);
  void loop();
  void handleButtonLongPress();
  char handleLongPress(char);
};
@LOOP@
@ALERT@
@SCREEN@

static void forceCurrentScreen(UITask& ui) {
#if UI_ORIG
  // Orig has no screen selector. Dirty is a real incoming-event condition;
  // retain its already-due refresh so the test never invents a comparator.
  ui._need_refresh = true;
#else
  ui.setCurrScreen(ui.home); // Actual source's force-refresh operation.
#endif
}

int main(int argc, char** argv) {
  setbuf(stdout, nullptr);
  assert(argc == 2);
  const char* scenario = argv[1];
  UITask ui;
  if (!std::strcmp(scenario, "startup-first-draw")) {
#if UI_ORIG
    ui.ui_started_at = now_ms = 1000;
    ui._need_refresh = false;
    ui.display.on = false;
    ui.loop();
    now_ms = 9000; ui.loop();
    for (uint64_t day = 1; day <= 49; ++day) {
      now_ms = uint32_t(1000u + day * 86400000ULL);
      ui.loop();
    }
    now_ms = 2000; ui.loop(); // Full-wrap alias, startup already observed closed.
    ui.display.on = true; ui.loop();
    assert(ui.display.frames == 1 && "deferred first paint must remain post-startup after full wrap");
#endif
    std::puts("PASS: startup-first-draw");
    return 0;
  }
  if (!std::strcmp(scenario, "startup-one-shot")) {
    for (uint32_t started : {0u, 1000u, UINT32_MAX - 4000u}) {
      for (unsigned gate = 0; gate < 4; ++gate) {
        UITask window;
        window.ui_started_at = started;
        window._next_refresh = now_ms = started;
        window.display.on = (gate & 1) == 0;
        radio_driver.timing = (gate & 2) != 0;
        window.loop();
        now_ms = started + 7999u; window.loop();
        const auto before = the_mesh.rescues;
#if UI_ORIG
        window.handleButtonLongPress();
#else
        assert(window.handleLongPress('x') == 0);
#endif
        assert(the_mesh.rescues == before + 1 && !window._startup_complete);
        now_ms = started + 8000u; window.loop();
#if UI_ORIG
        window.handleButtonLongPress();
        assert(window.shutdowns == 1);
#else
        assert(window.handleLongPress('x') == 'x');
#endif
        assert(the_mesh.rescues == before + 1); // Exact 8-second cutoff.
        for (uint64_t day = 1; day <= 49; ++day) {
          now_ms = uint32_t(started + day * 86400000ULL);
          window.loop();
        }
        // Live observation of expiry precedes the complete-counter alias.
        now_ms = started + 1000u; window.loop();
        window.display.on = true; radio_driver.timing = false;
        const auto profiles = window.profile_services;
        window.loop();
#if UI_ORIG
        window.handleButtonLongPress();
#else
        (void)profiles;
        const char returned = window.handleLongPress('x');
#endif
        assert(the_mesh.rescues == before + 1 && window._startup_complete
            && "expired startup rescue must never reopen after full wrap");
#if UI_ORIG
        assert(window.shutdowns == 2 && window.profile_services == profiles + 1);
#else
        assert(returned == 'x');
#endif
      }
    }
    std::puts("PASS: startup-one-shot");
    return 0;
  }
  if (!std::strcmp(scenario, "orig-long-clean-idle")) {
#if UI_ORIG
    now_ms = 0x90001000u;
    const uint32_t origin = now_ms;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms;
    // A persistent message preview disables automatic radio-page dirtiness.
    std::strcpy(ui._origin, "sender");
    std::strcpy(ui._msg, "retained preview");
    ui.loop();
    assert(ui.display.frames == 1 && !ui._need_refresh);
    for (uint32_t day = 1; day <= 26; ++day) {
      now_ms = origin + day * 86400000u;
      ui.loop(); // Every individual elapsed step is far below half a wrap.
      assert(ui.display.frames == 1 && !ui._need_refresh);
    }
    ui._need_refresh = true; // A fresh message finally makes the view dirty.
    ui.loop();
    std::printf("orig after 26 clean days: frames=%u next=%u now=%u\n",
                ui.display.frames, ui._next_refresh, now_ms);
    assert(ui.display.frames == 2 && "a clean interval cannot age a due deadline back into the future");
#else
    assert(false && "orig-only scenario");
#endif
  } else if (!std::strcmp(scenario, "display-off-long-idle")) {
#if !UI_ORIG
    now_ms = 0x90001000u;
    const uint32_t origin = now_ms;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms;
    ui.next_batt_chck = now_ms;
    ui.display.on = false;
    ui.loop();
    for (uint32_t day = 1; day <= 26; ++day) {
      now_ms = origin + day * 86400000u;
      ui.loop();
      assert(ui.display.frames == 0);
    }
    ui.display.on = true; // Do not inject a force-refresh assignment on wake.
    ui.loop();
    std::printf("wake after 26 display-off days: frames=%u next=%u now=%u\n",
                ui.display.frames, ui._next_refresh, now_ms);
    assert(ui.display.frames == 1 && "live loops must preserve due refresh status while display is off");
#else
    assert(false && "screened layouts only");
#endif
  } else if (!std::strcmp(scenario, "fresh-dirty-throttle")) {
#if UI_ORIG
    now_ms = 0x90001000u;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms;
    std::strcpy(ui._origin, "sender");
    std::strcpy(ui._msg, "preview");
    ui.loop();
    assert(ui.display.frames == 1 && !ui._need_refresh);
    ++now_ms;
    ui._need_refresh = true; // Fresh incoming dirtiness, not a force operation.
    ui.loop();
    assert(ui.display.frames == 1 && "normal fresh dirtiness still respects the one-second throttle");
    now_ms += 998u;
    ui.loop();
    assert(ui.display.frames == 1);
    ++now_ms;
    ui.loop();
    assert(ui.display.frames == 2);
#else
    assert(false && "orig-only dirty-flag scenario");
#endif
  } else if (!std::strcmp(scenario, "cache-battery-long-idle")) {
#if UI_TINY_CACHE
    now_ms = 0x90001000u;
    const uint32_t origin = now_ms;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms;
    ui.next_batt_chck = now_ms;
    ui.display.on = false;
    ui.loop();
    for (uint32_t day = 1; day <= 26; ++day) {
      now_ms = origin + day * 86400000u;
      ui.loop();
      assert(ui.battery_reads == 0 && ui.display.frames == 0);
    }
    ui.display.on = true;
    radio_driver.timing = true;
    ui.loop();
    assert(ui.battery_reads == 0 && ui.display.frames == 0);
    radio_driver.timing = false;
    ++now_ms;
    ui.loop();
    std::printf("cache ADC after 26 off days and Morse release: reads=%u next=%u now=%u\n",
                ui.battery_reads, ui.next_batt_chck, now_ms);
    assert(ui.battery_reads == 1 && "cached battery poll stays due while off but samples only on safe wake");
    assert(ui.display.frames == 1);
#else
    assert(false && "tiny no-auto-shutdown cache-only scenario");
#endif
  } else if (!std::strcmp(scenario, "cache-battery-wrapped-future")) {
#if UI_TINY_CACHE
    now_ms = UINT32_MAX - 7999u;
    ui.next_batt_chck = now_ms;
    ui.loop();
    assert(ui.battery_reads == 1 && ui.next_batt_chck == 0);
    ++now_ms;
    ui.loop();
    assert(ui.battery_reads == 1 && "future wrapped zero cannot be clamped into an immediate cache poll");
    ui.display.on = false;
    now_ms = 1u;
    ui.loop();
    assert(ui.battery_reads == 1);
    ui.display.on = true;
    radio_driver.timing = true;
    ui.loop();
    assert(ui.battery_reads == 1);
    radio_driver.timing = false;
    ui.loop();
    assert(ui.battery_reads == 2 && "due cached ADC waits for both display and Morse gates");
#else
    assert(false && "tiny cache-only scenario");
#endif
  } else if (!std::strcmp(scenario, "healthy") || !std::strcmp(scenario, "upper-half")) {
    now_ms = !std::strcmp(scenario, "healthy") ? 10000u : 0x80001000u;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms + 1000u;
    ui.loop();
    assert(ui.display.frames == 0);
    now_ms += 999u;
    ui.loop();
    assert(ui.display.frames == 0);
    ++now_ms;
    ui.loop();
    assert(ui.display.frames == 1 && ui.screen.renders == 1);
  } else if (!std::strcmp(scenario, "force-upper-half")) {
#if UI_ORIG
    assert(false && "orig has no setCurrScreen operation");
#else
    now_ms = 0x80001000u;
    ui._next_refresh = now_ms + 1000u;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.frames == 1 && "screen change must redraw immediately in upper half");
#endif
  } else if (!std::strcmp(scenario, "deferred-across-wrap")) {
    now_ms = UINT32_MAX - 1500u;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = UINT32_MAX - 1000u;
    ui.loop();
    assert(ui.display.frames == 0);
    radio_driver.timing = true;
    now_ms = UINT32_MAX - 500u;
    ui.loop();
    now_ms = 100u;
    ui.loop();
    assert(ui.display.frames == 0);
    radio_driver.timing = false;
    ui.loop();
    std::printf("after wrapped Morse deferral: frames=%u next=%u now=%u\n",
                ui.display.frames, ui._next_refresh, now_ms);
    assert(ui.display.frames == 1 && "overdue refresh must run after Morse releases across wrap");
  } else if (!std::strcmp(scenario, "wrapped-future-deadline")) {
    now_ms = UINT32_MAX - 999u;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui._next_refresh = now_ms;
    ui.loop();
    assert(ui.display.frames == 1 && ui._next_refresh == 0);
    ++now_ms;
    // Orig correctly clears dirty after render; model a fresh incoming event.
    // It still must respect the real loop's one-second redraw throttle.
    ui._need_refresh = true;
    ui.loop();
    std::printf("future deadline wrapped to zero: frames=%u now=%u\n", ui.display.frames, now_ms);
    assert(ui.display.frames == 1 && "a future wrapped deadline is not an immediate force sentinel");
    now_ms = 0;
    ui.loop();
    assert(ui.display.frames == 2);
  }
#if !UI_ORIG
  else if (!std::strcmp(scenario, "battery-healthy") || !std::strcmp(scenario, "battery-upper-half")) {
    ui.display.on = false;
    now_ms = !std::strcmp(scenario, "battery-healthy") ? 10000u : 0x80001000u;
    const uint32_t due = now_ms + 1000u;
    ui.next_batt_chck = due;
    ui.loop();
    assert(ui.battery_reads == 0 && ui.shutdowns == 0);
    now_ms = due + 1u;
    ui.loop();
    assert(ui.battery_reads == 1 && ui.shutdowns == 0);
    now_ms += 7999u;
    ui.loop();
    assert(ui.battery_reads == 1);
    now_ms += 2u;
    ui.loop();
    assert(ui.battery_reads == 2 && ui.shutdowns == 0);
  } else if (!std::strcmp(scenario, "alert-upper-half")) {
    now_ms = 0x80001000u;
    ui.showAlert("ALERT", 1000);
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1);
    now_ms += 999u;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 2);
    now_ms += 2u;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 2 && "healthy upper-half alert must expire normally");
  } else if (!std::strcmp(scenario, "battery-deferred-wrap")) {
    ui.display.on = false;
    radio_driver.timing = true;
    ui.voltage = 3350;
    now_ms = UINT32_MAX - 1500u;
    ui.next_batt_chck = UINT32_MAX - 1000u;
    ui.loop();
    assert(ui.battery_reads == 0 && ui.shutdowns == 0);
    now_ms = 100u; // The main loop crossed wrap before the protective poll ran.
    ui.loop();
    std::printf("low-battery poll across wrap: reads=%u shutdowns=%u next=%u\n",
                ui.battery_reads, ui.shutdowns, ui.next_batt_chck);
    assert(ui.battery_reads == 1 && ui.shutdowns == 1
           && "overdue protective battery sample must not be postponed another full wrap");
  } else if (!std::strcmp(scenario, "battery-wrapped-future")) {
    ui.display.on = false;
    now_ms = UINT32_MAX - 7999u;
    ui.next_batt_chck = now_ms - 1u;
    ui.loop();
    assert(ui.battery_reads == 1 && ui.next_batt_chck == 0);
    ++now_ms;
    ui.loop();
    std::printf("future battery poll wrapped to zero: reads=%u next=%u\n",
                ui.battery_reads, ui.next_batt_chck);
    assert(ui.battery_reads == 1 && "eight-second battery poll must not run again one ms later");
    now_ms = 1u;
    ui.loop();
    assert(ui.battery_reads == 2 && ui.shutdowns == 0);
  } else if (!std::strcmp(scenario, "expire-with-display-off")
             || !std::strcmp(scenario, "expire-during-morse")
             || !std::strcmp(scenario, "expire-during-gesture")) {
    now_ms = 10000u;
    ui.showAlert("ALERT", 1000);
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1);
    const unsigned before_frames = ui.display.frames;
    now_ms = 11001u;
    if (!std::strcmp(scenario, "expire-with-display-off")) ui.display.on = false;
    if (!std::strcmp(scenario, "expire-during-morse")) radio_driver.timing = true;
    if (!std::strcmp(scenario, "expire-during-gesture")) ui.gesture_pending = true;
    ui.loop();
    assert(ui.display.frames == before_frames);
    std::printf("popup expiry while redraw gated: text_active=%u frames=%u\n",
                unsigned(ui._alert[0] != 0), ui.display.frames);
    assert(!ui._alert[0] && "expired popup text must be consumed even when redraw is suppressed");
    ui.display.on = true;
    radio_driver.timing = false;
    ui.gesture_pending = false;
    now_ms = 0x80002af9u;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1 && "expired popup cannot revive after half a wrap");
  }
  else if (!std::strcmp(scenario, "alert-across-wrap") || !std::strcmp(scenario, "alert-zero-deadline")) {
    const bool zero_deadline = !std::strcmp(scenario, "alert-zero-deadline");
    now_ms = zero_deadline ? uint32_t(0u - 1000u) : UINT32_MAX - 500u;
    ui.ui_started_at = now_ms - BOOT_SCREEN_MILLIS;
    ui.showAlert("ALERT", 1000);
    forceCurrentScreen(ui);
    ui.loop();
    std::printf("new wrapped alert: popups=%u expiry=%u now=%u\n",
                ui.display.popups, ui._alert_expiry, now_ms);
    assert(ui.display.popups == 1 && "new popup must display before a wrapped expiry, including zero");
    now_ms = ui._alert_expiry;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1 && "popup expires at its exact deadline");
  } else if (!std::strcmp(scenario, "expired-after-wrap") || !std::strcmp(scenario, "expired-after-halfwrap")) {
    now_ms = 10000u;
    ui.showAlert("ALERT", 1000);
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1);
    now_ms = 11001u;
    forceCurrentScreen(ui);
    ui.loop();
    assert(ui.display.popups == 1);
    // It was observed expired. A stale expiry must never reactivate it later.
    now_ms = !std::strcmp(scenario, "expired-after-wrap") ? 100u : 0x80002af9u;
    forceCurrentScreen(ui);
    ui.loop();
    std::printf("previously expired alert: popups=%u expiry=%u now=%u\n",
                ui.display.popups, ui._alert_expiry, now_ms);
    assert(ui.display.popups == 1 && "observed expired popup must never reactivate");
  }
#endif
  else assert(false && "unknown scenario");
  std::printf("PASS: %s\n", scenario);
}
