#!/usr/bin/env python3
"""Execute production repeater scheduling, watchdog and advert service methods."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(text, signature):
    start = text.index(signature)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <helpers/RepeaterRadioTiming.h>
#include <helpers/TempRadioLeaseDeadline.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MESH_ENABLE_TELEMETRY_HISTORY 0
#define MAX_SCHEDULED_RADIO_SETTINGS 8
#define MAX_SCHEDULED_RADIO_SETTINGS_PER_TYPE 4
#define SCHEDULED_RADIO_CLOCK_CHECKPOINT_SECS 60
#define RADIO_APPLY_RETRY_INTERVAL_MILLIS 100
static uint32_t now_ms = 1;
uint32_t millis() { return now_ms; }
namespace mesh {
struct Packet {};
int clampLoRaTxPower(int power, float) { return power; }
}
struct CommonCLI {
  template <typename T> static void recalculateRxPowerSavingFromLevel(T*) {}
};
uint32_t nextRadioApplyRetryDelay(uint8_t& failures) { ++failures; return 100; }
struct RadioDriver {
  void setRxBoostedGainMode(bool) {}
  void setTxPower(int) {}
} radio_driver;
struct RTC {
  uint32_t now = 100000;
  uint32_t getCurrentTime() const { return now; }
};
struct Board { int reboots = 0; void reboot() { ++reboots; } };
struct CLI { Board board; Board* getBoard() { return &board; } };
struct Radio { uint32_t last_rx = 0; uint32_t getLastRecvMillis() { return last_rx; } };
struct Barrier { bool held = false; bool waiting() const { return held; } void clear() { held = false; } };
struct ScheduledRadioSetting {
  bool active = false, temporary = false, started = false;
  float freq = 0, bw = 0;
  uint8_t sf = 0, cr = 0;
  uint32_t start_time = 0, end_time = 0;
  uint64_t hard_end_uptime_millis = 0;
};
class MyMesh {
public:
  struct Prefs {
    bool rx_watchdog_enabled = true, rx_boosted_gain = true;
    uint8_t advert_interval = 60, flood_advert_interval = 24, path_hash_mode = 0;
    float freq = 909.5f, bw = 62.5f;
    uint8_t sf = 7, cr = 5;
    int tx_power_dbm = 20;
  } _prefs;
  CLI _cli;
  Radio radio; Radio* _radio = &radio;
  uint32_t last_meshcore_rx = 0;
  uint32_t getLastMeshCoreRecvMillis() const { return last_meshcore_rx; }
  RTC rtc;
  Barrier temp_radio_reply_barrier;
  mesh::RxInactivityWatchdog rx_inactivity_watchdog;
  mesh::RepeaterRadioTiming radio_timing;
  ScheduledRadioSetting scheduled_radio_settings[MAX_SCHEDULED_RADIO_SETTINGS];
  uint64_t uptime_millis = 1;
  uint32_t last_millis = 1;
  uint32_t next_local_advert = 0, next_flood_advert = 0;
  bool saved_radio_apply_pending = false, temp_radio_handoff_pending = false;
  bool temp_radio_applied = false, scheduled_temp_radio_started = false;
  uint32_t next_scheduled_radio_time = 0, next_scheduled_radio_check_at = 0;
  uint32_t scheduled_temp_radio_end_time = 0, scheduled_temp_radio_end_check_at = 0;
  bool scheduled_temp_radio_end_check_final = false;
  uint32_t scheduled_radio_retry_at = 0;
  uint8_t scheduled_radio_retry_failures = 0;
  bool pending_self_advert = false, pending_self_advert_flood = false;
  uint32_t pending_self_advert_delay = 0;
  bool outbound = false, apply_success = true, restore_success = true;
  int saves = 0, applies = 0, restores = 0, local_adverts = 0, flood_adverts = 0;
  int default_scope = 0;
  mesh::Packet packet;
  MyMesh() { now_ms = 1; updateAdvertTimer(); updateFloodAdvertTimer(); }
  RTC* getRTCClock() const { return const_cast<RTC*>(&rtc); }
  uint32_t futureMillis(uint32_t delay) const { return now_ms + delay; }
  bool millisHasNowPassed(uint32_t deadline) const { return (int32_t)(now_ms - deadline) >= 0; }
  bool hasOutbound() const { return outbound; }
  bool hasStartedScheduledTempRadio() const { return scheduled_temp_radio_started; }
  bool applyRadioParams(float, float, uint8_t, uint8_t) { ++applies; return apply_success; }
  bool applySavedRadioParams() { ++restores; return restore_success; }
  bool isValidScheduledRadioParams(float, float, uint8_t, uint8_t) { return true; }
  void savePrefs() { ++saves; }
  int getScheduledRadioSettingIndex(bool, int slot) const { return slot + 1; }
  void formatScheduledRadioDuration(char* dest, size_t size, uint32_t when) const {
    snprintf(dest, size, "%lus", (unsigned long)(when - rtc.now));
  }
  void appendTempRadioTimingNote(char* reply, size_t size, uint32_t seconds) const {
    mesh::RepeaterRadioTiming::appendTempWatchdogNote(reply, size, seconds);
  }
  void advance(uint32_t seconds) {
    now_ms += seconds * 1000UL;
    uptime_millis += (uint64_t)seconds * 1000UL;
    last_millis = now_ms;
    rtc.now += seconds;
  }
  mesh::Packet* createSelfAdvert() { return &packet; }
  void sendFloodScoped(int, mesh::Packet*, uint32_t, int) { ++flood_adverts; }
  void sendZeroHop(mesh::Packet*) { ++local_adverts; }
  void sendSelfAdvertisementNow(uint32_t, bool) {}
  void checkBatteryAlert() {}
  void expireRecentRepeatersIfDue() {}
  void checkRxInactivityWatchdog();
  void setTempRadioTiming(uint32_t);
  void updateAdvertTimer();
  void updateFloodAdvertTimer();
  void queueSavedRadioApply();
  void refreshScheduledRadioState();
  void processScheduledRadioSettings();
  void applyTempRadioParams(float, float, uint8_t, uint8_t, int);
  bool scheduleNormalRadio();
  void clearScheduledRadioSetting(int, bool);
  int findFreeScheduledRadioSlot() const;
  int countScheduledRadioSettings(bool) const;
  bool scheduledRadioConflicts(bool, uint32_t, uint32_t) const;
  void addScheduledRadioParams(bool, float, float, uint8_t, uint8_t, uint32_t, uint32_t, char*);
  void servicePostMeshLoop();
};
@METHODS@
static constexpr uint32_t HOUR = 3600;
static void startTemp(MyMesh& m, int minutes) {
  m.applyTempRadioParams(910, 250, 5, 5, minutes);
  m.advance(2);
  m.servicePostMeshLoop();
  assert(m.temp_radio_applied);
}
int main() {
  { // Pending schedules do not change current timings, even on an apply failure.
    MyMesh m;
    const uint32_t normal_local = m.next_local_advert;
    char reply[160];
    m.addScheduledRadioParams(true, 910, 250, 5, 5, m.rtc.now + 60,
                              m.rtc.now + 60 + 24 * HOUR, reply);
    assert(strstr(reply, "1440 mins (1d0h0m); rx.watchdog=12hours"));
    assert(!m.radio_timing.isTemporary());
    assert(m.next_local_advert == normal_local);
    m.apply_success = false;
    m.advance(60); m.servicePostMeshLoop();
    assert(!m.radio_timing.isTemporary());
    m.apply_success = true;
    m.advance(1); m.servicePostMeshLoop();
    assert(m.radio_timing.tempDuration() == 24 * HOUR);
    assert(m.next_local_advert == now_ms + HOUR * 1000);
    assert(m.next_flood_advert == now_ms + 3 * HOUR * 1000);
    assert(m.saves == 0);
    assert(m._prefs.advert_interval == 60 && m._prefs.flood_advert_interval == 24);
  }
  { // The exact-reply barrier must still delay activation and its overrides.
    MyMesh m;
    m.applyTempRadioParams(910, 250, 5, 5, 1440);
    m.temp_radio_reply_barrier.held = true;
    m.advance(3); m.servicePostMeshLoop();
    assert(!m.radio_timing.isTemporary());
    m.temp_radio_reply_barrier.clear();
    m.advance(1); m.servicePostMeshLoop();
    assert(m.radio_timing.tempDuration() == 24 * HOUR);
  }
  { // Both adverts are sent at hour three; hourly local adverts cannot vanish.
    MyMesh m; startTemp(m, 1440);
    for (int hour = 1; hour <= 6; ++hour) { m.advance(HOUR); m.servicePostMeshLoop(); }
    assert(m.local_adverts == 6 && m.flood_adverts == 2);
    assert(m._cli.board.reboots == 0 && m.saves == 0);
  }
  { // A short session keeps the normal flood setting, including disabled.
    MyMesh m;
    m._prefs.flood_advert_interval = 0;
    m._prefs.advert_interval = 0;
    startTemp(m, 180);
    assert(m.next_flood_advert == 0);
    assert(m.radio_timing.watchdogMillis(true) == 0);
    m.advance(HOUR); m.servicePostMeshLoop();
    assert(m.local_adverts == 1 && m.flood_adverts == 0);
    m.advance(2 * HOUR); m.servicePostMeshLoop();
    assert(!m.radio_timing.isTemporary());
    assert(m.next_local_advert == 0 && m.next_flood_advert == 0);
    assert(m.radio_timing.watchdogMillis(true) == 24 * HOUR * 1000);
  }
  { // At exactly 12 hours, expiry wins over the temporary watchdog deadline.
    MyMesh m; startTemp(m, 720);
    m.advance(12 * HOUR); m.servicePostMeshLoop();
    assert(!m.radio_timing.isTemporary());
    assert(m._cli.board.reboots == 0);
    m.advance(24 * HOUR); m.servicePostMeshLoop();
    assert(m._cli.board.reboots == 1);
  }
  { // A longer session really does reboot after a 12-hour no-RX window.
    MyMesh m; startTemp(m, 1440);
    m.advance(12 * HOUR); m.servicePostMeshLoop();
    assert(m._cli.board.reboots == 1);
  }
  { // Replacing an active session retains old policy until the new tuple applies.
    MyMesh m; startTemp(m, 1440);
    m.applyTempRadioParams(911, 250, 5, 5, 60);
    assert(m.radio_timing.tempDuration() == 24 * HOUR);
    m.advance(2); m.servicePostMeshLoop();
    assert(m.radio_timing.tempDuration() == HOUR);
    assert(m.radio_timing.watchdogMillis(true) == 0);
  }
  { // Failed restoration must not leave a short session's watchdog disabled.
    MyMesh m; startTemp(m, 60);
    m.restore_success = false;
    m.advance(HOUR); m.servicePostMeshLoop();
    assert(m.temp_radio_applied); // hardware restoration is still pending
    assert(!m.radio_timing.isTemporary());
    assert(m.radio_timing.watchdogMillis(true) == 24 * HOUR * 1000);
    m.restore_success = true;
    m.advance(1); m.servicePostMeshLoop();
    assert(!m.temp_radio_applied && m.saves == 0);
  }
  { // Manual cancellation restores saved timings without saving temp overrides.
    MyMesh m; startTemp(m, 1440);
    assert(m.scheduleNormalRadio());
    assert(!m.radio_timing.isTemporary());
    m.servicePostMeshLoop();
    assert(!m.temp_radio_applied && m.saves == 0);
    assert(m.next_local_advert == now_ms + 2 * HOUR * 1000);
    assert(m.next_flood_advert == now_ms + 24 * HOUR * 1000);
  }
  { // Deleting an active scheduled entry has the same restoration behavior.
    MyMesh m; startTemp(m, 1440);
    m.clearScheduledRadioSetting(0, true);
    assert(!m.radio_timing.isTemporary());
    m.servicePostMeshLoop();
    assert(!m.temp_radio_applied && m.saves == 0);
  }
  { // Adjacent scheduled windows adopt the new duration at the boundary.
    MyMesh m;
    char reply[160];
    uint32_t start = m.rtc.now + 2;
    m.addScheduledRadioParams(true, 910, 250, 5, 5, start, start + HOUR, reply);
    m.addScheduledRadioParams(true, 911, 250, 5, 5, start + HOUR, start + 25 * HOUR, reply);
    m.advance(2); m.servicePostMeshLoop();
    assert(m.radio_timing.tempDuration() == HOUR);
    m.advance(HOUR); m.servicePostMeshLoop();
    assert(m.radio_timing.tempDuration() == 24 * HOUR);
    assert(m._cli.board.reboots == 0 && m.saves == 0);
  }
  { // Raw driver receptions must not keep the whole-board watchdog alive.
    MyMesh m; m.servicePostMeshLoop();
    m.advance(23 * HOUR);
    m.radio.last_rx = now_ms;
    m.servicePostMeshLoop();
    m.advance(HOUR);
    m.radio.last_rx = now_ms;
    m.servicePostMeshLoop();
    assert(m._cli.board.reboots == 1);
  }
  { // A parsed MeshCore reception moves the actual repeater reboot deadline.
    MyMesh m; m.servicePostMeshLoop();
    m.advance(23 * HOUR);
    m.last_meshcore_rx = now_ms;
    m.servicePostMeshLoop();
    m.advance(HOUR); m.servicePostMeshLoop();
    assert(m._cli.board.reboots == 0);
    m.advance(23 * HOUR); m.servicePostMeshLoop();
    assert(m._cli.board.reboots == 1);
  }
  puts("13 production scheduler/watchdog/advert scenarios passed");
}
'''

CLI_HARNESS = r'''
#include <cassert>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/RepeaterRadioTiming.h>
#include <helpers/CLICommandUtils.h>
namespace mesh {
class Utils { public: static int parseTextParts(char*, const char*[], int, char separator=','); };
@PARTS@
}
@PARSERS@
struct RTC { uint32_t getCurrentTime() const { return 1789160000UL; } } rtc;
RTC* getRTCClock() { return &rtc; }
struct Callbacks {
  int calls = 0;
  uint32_t duration = 0;
  void applyTempRadioParams(float, float, uint8_t, uint8_t, int minutes) {
    ++calls; duration = (uint32_t)minutes * 60;
  }
  void appendTempRadioTimingNote(char* reply, size_t size, uint32_t seconds) {
    mesh::RepeaterRadioTiming::appendTempWatchdogNote(reply, size, seconds);
  }
} callbacks;
void appendRxPowerSavingAdjustmentNote(char*, const void*, uint8_t, float) {}
void handle(const char* command, char* reply) {
  char tmp[160];
  Callbacks* _callbacks = &callbacks;
  const void* _prefs = nullptr;
  if (false) {
  @BRANCH@
  }
}
int main() {
  struct Reply { char text[160]; char guard = '!'; } reply;
  handle("tempradio 910,250,5,5,1440", reply.text);
  assert(!strcmp(reply.text, "OK - temp params for 1440 mins (1d0h0m); rx.watchdog=12hours"));
  assert(!strncmp(reply.text, "OK - temp params for ", 21)); // exact reply-barrier marker
  handle("tempradio 910,250,5,5,90", reply.text);
  assert(!strcmp(reply.text, "OK - temp params for 90 mins (0d1h30m); rx.watchdog=off (temp<12hours)"));
  assert(callbacks.calls == 2 && callbacks.duration == 5400);
  for (const char* minutes : {"0", "-1", "12oops", "1.5", "4294967295", "4294967296"}) {
    char command[100];
    snprintf(command, sizeof(command), "tempradio 910,250,5,5,%s", minutes);
    handle(command, reply.text);
    assert(!strcmp(reply.text, "Error, invalid params"));
    assert(callbacks.calls == 2);
  }
  assert(reply.guard == '!');
  puts("production TempRadio replies and duration validation passed");
}
'''


class RepeaterRadioTimingIntegrationTest(unittest.TestCase):
    def compile_and_run(self, source):
        with tempfile.TemporaryDirectory(prefix="meshcore-temp-timing-") as temp:
            cpp = Path(temp) / "test.cpp"
            exe = Path(temp) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(source, encoding="utf-8")
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-I", str(ROOT / "src"),
                            str(cpp), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_production_scheduler_and_services(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="utf-8")
        signatures = [
            "void MyMesh::checkRxInactivityWatchdog()",
            "void MyMesh::setTempRadioTiming(",
            "void MyMesh::updateAdvertTimer()",
            "void MyMesh::updateFloodAdvertTimer()",
            "void MyMesh::queueSavedRadioApply()",
            "void MyMesh::refreshScheduledRadioState()",
            "void MyMesh::processScheduledRadioSettings()",
            "void MyMesh::applyTempRadioParams(",
            "bool MyMesh::scheduleNormalRadio()",
            "void MyMesh::clearScheduledRadioSetting(",
            "int MyMesh::findFreeScheduledRadioSlot() const",
            "int MyMesh::countScheduledRadioSettings(",
            "bool MyMesh::scheduledRadioConflicts(",
            "void MyMesh::addScheduledRadioParams(",
        ]
        methods = "\n".join(method(source, signature) for signature in signatures)
        post = method(source, "void __attribute__((noinline)) MyMesh::servicePostMeshLoop()")
        # The remaining service tail handles unrelated MQTT/OTA/peripheral work.
        post = post[:post.index("#if defined(WITH_MQTT_BRIDGE) && defined(OTA_MANIFEST_BASE)")] + "}\n"
        methods += "\n" + post
        self.compile_and_run(HARNESS.replace("@METHODS@", methods))

    def test_production_cli_replies_and_duration_validation(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        parsers = "\n".join(method(source, signature) for signature in [
            "static bool looksUnsignedInteger(", "static const char* skipSpacesConst(",
            "static bool parseUint32Strict(", "static bool bwMatches(",
            "static bool isValidLoRaBandwidth(",
        ])
        start = source.index('    } else if (memcmp(command, "tempradio ", 10) == 0)')
        end = source.index('    } else if (memcmp(command, "password ", 9) == 0)', start)
        utils = (ROOT / "src/Utils.cpp").read_text(encoding="utf-8")
        parts = method(utils, "int Utils::parseTextParts(")
        self.compile_and_run(CLI_HARNESS.replace("@PARSERS@", parsers)
                             .replace("@BRANCH@", source[start:end])
                             .replace("@PARTS@", parts))


if __name__ == "__main__":
    unittest.main()
