#include <helpers/MorseLoopPolicy.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/PowerManagementUtils.h>
#include <cstdarg>
static unsigned usb_services=0,command_services=0,transport_services=0,delays=0;
static bool usb_watchdog_armed=false,wireless_pending=false;
bool profileIdlePreparationAllowed();
bool profileIdleQueryAllowed();
bool profileIdleSleepAllowed();
struct Stream {
  int available() const { return 0; }
  char read() const { return 0; }
  void print(char) {}
  void print(const char*) {}
  void println(const char*) {}
  void printf(const char*,...) {}
} console;
char command[180]={};
uint32_t millis() { return 100000; }
void delay(unsigned) { ++delays; }
bool usbLoggingRecoverySafe() { return true; }
namespace mesh {
void serviceUsbLoggingPort() { ++usb_services; }
void serviceUsbTerminalPort() { ++usb_services; }
bool isUsbLoggingEnabled() { return false; }
bool isUsbLoggingWatchdogArmed() { return usb_watchdog_armed; }
bool serviceUsbLoggingWatchdog(bool(*)()) { ++usb_services; return false; }
Stream& usbConsolePort() { return console; }
void noteUsbLoggingStatsCommand(const char*) { ++usb_services; }
namespace wireless {
struct Control { void service(uint32_t) { ++transport_services; } bool pending() { return wireless_pending; } } controller;
Control& control() { return controller; }
}
}
struct Board {
  unsigned calls=0,watchdogs=0,sleeps=0,fans=0,reboots=0,preparations=0;
  unsigned millisecond_sleeps=0,last_sleep_ms=0,profile_preparations=0;
  bool profile_prepared=false;
  void prepareSleepCycle() { ++preparations; profile_prepared=false; }
  void prepareProfileIdleCycle() {
    assert(profileIdlePreparationAllowed());
    ++profile_preparations; profile_prepared=true;
  }
  void loop() { ++calls; }
  void feedWatchdog(bool=true) { ++watchdogs; }
  void sleep(uint32_t) { assert(profileIdleSleepAllowed()); ++sleeps; }
  void sleepMilliseconds(uint32_t ms) {
    assert(profileIdleSleepAllowed()); ++sleeps; ++millisecond_sleeps; last_sleep_ms=ms;
  }
  bool isUsbDataConnected() { return false; }
  bool isUsbHostConnected() { return false; }
  void reboot() { ++reboots; }
  void updateFanControl() { ++fans; }
} board;
struct Task {
  unsigned calls=0,deadline=200;
  void loop() { ++calls; deadline+=100; }
  void tick() { loop(); }
  void feed() { loop(); }
} sensors,ui_task,rtc_clock,interface_manager,external_watchdog;
bool display_ready=true;
struct Mesh {
  struct Prefs { bool powersaving_enabled=true,system_watchdog_enabled=true; uint32_t reboot_interval=0; } prefs;
  unsigned calls=0;
  bool noise_floor_wait=false,work_pending=false;
  bool dual_radio=false,profile_idle_allowed=true;
  mutable unsigned profile_idle_queries=0;
  uint32_t noise_floor_delay_ms=50,sleep_budget_ms=1000;
  void loop() { ++calls; }
  Prefs* getNodePrefs() { return &prefs; }
  uint32_t getPowerSaveSleepMilliseconds(unsigned) { return work_pending ? 0 : sleep_budget_ms; }
  bool getNextNoiseFloorWakeDelay(uint32_t& ms) const {
    ms=noise_floor_delay_ms; return noise_floor_wait;
  }
  bool millisHasNowPassed(uint32_t) { return true; }
  bool isTrackerModeEnabled() { return false; }
  bool isDualRadioActive() const { return dual_radio; }
  bool canIdleDualRadio() const {
    if (!dual_radio) return true;
    assert(profileIdleQueryAllowed());
    ++profile_idle_queries;
    return profile_idle_allowed;
  }
  bool hasPendingWork() { return false; }
  mesh::powerdiag::Mask pendingWorkReason() const {
    if (!canIdleDualRadio()) return mesh::powerdiag::mask(mesh::powerdiag::Reason::DualRadio);
    return work_pending ? mesh::powerdiag::mask(mesh::powerdiag::Reason::Queue) : 0;
  }
  void handleCommand(uint32_t,char*,char* reply) { reply[0]=0; ++command_services; }
  void handleUsbCommand(char*,char* reply) { reply[0]=0; ++command_services; }
} the_mesh;
TestRadio radio_driver;
bool profileIdlePreparationAllowed() {
  return (the_mesh.prefs.powersaving_enabled || the_mesh.isTrackerModeEnabled())
      && the_mesh.isDualRadioActive() && !mesh::morseTimingActive(&radio_driver);
}
bool profileIdleQueryAllowed() {
  return profileIdlePreparationAllowed() && board.profile_prepared;
}
bool profileIdleSleepAllowed() {
  return !mesh::morseTimingActive(&radio_driver)
      && (!the_mesh.isDualRadioActive() || (profileIdleQueryAllowed() && the_mesh.profile_idle_allowed));
}
void serviceCommandInterfaces() { ++command_services; }
void serviceUsbLoggingOnlySession() { ++usb_services; }
void serviceUsbTerminalHostSessionReset() { ++usb_services; }
void serviceUsbAsciiSessionDefault() { ++usb_services; }
void serviceUsbLoggingReplyBarrier() { ++usb_services; }
void serviceUsbLoggingOwnership(bool) { ++usb_services; }
void expireUsbBinaryStartupProbeBeforeDispatch() { ++usb_services; }
void serviceUsbTerminal() { ++usb_services; }
void syncUsbTerminalInputEditing() { ++usb_services; }
void serviceDeferredCompanionBluetooth() { ++transport_services; }
void serviceCompanionBluetoothIdentity() { ++transport_services; }
void serviceCompanionBluetoothControl() { ++transport_services; }
void serviceCompanionBluetoothName() { ++transport_services; }
@LOOP@
int main() {
  assert(!mesh::morseTimingActive(nullptr));
  assert(!mesh::morseTimingActive(&radio_driver));
  loop();
  assert(sensors.calls==1 && ui_task.calls==1 && board.sleeps==1);
  radio_driver.cw=true; // Ordinary carrier-wave diagnostics keep old scheduling.
  loop();
  assert(sensors.calls==2 && ui_task.calls==2 && board.sleeps==2);
  for(unsigned mode=0;mode<3;++mode) {
    radio_driver.active=mode==0;
    radio_driver.rx_active=mode!=0;
    radio_driver.rx_recovering=mode==2;
    assert(mesh::morseTimingActive(&radio_driver));
    const auto sensor_deadline=sensors.deadline;
    const auto usb_before=usb_services;
    loop();
    // UI button/touch polling stays live; actual expensive redraw work is
    // deferred inside the UI implementation, not by skipping input service.
    assert(sensors.calls==2 && ui_task.calls==mode+3 && board.sleeps==2 && !delays);
    assert(sensors.deadline==sensor_deadline);
    assert(usb_services>usb_before && the_mesh.calls==mode+3 && board.watchdogs==mode+3);
    assert(rtc_clock.calls==mode+3 && external_watchdog.calls>=mode+3);
#if !ROLE_SENSOR
    assert(board.fans==mode+3);
#endif
  }
  radio_driver.active=radio_driver.rx_active=radio_driver.rx_recovering=false;
  loop();
  assert(sensors.calls==3 && ui_task.calls==6 && board.sleeps==3 && !delays);
  assert(the_mesh.calls==6 && rtc_clock.calls==6 && board.watchdogs==6);
  assert(board.preparations==6); // Prepare even when Morse vetoes actual sleep.
#if !ROLE_SENSOR
  assert(board.fans==6);
#endif
#if ROLE_COMPANION
  assert(interface_manager.calls==6 && transport_services==30 && board.calls==6);
#elif ROLE_REPEATER
  assert(command_services==6 && transport_services==6);
#elif ROLE_ROOM
  assert(transport_services==6 && board.calls==6);
#else
  assert(board.calls==6);
#endif
  const auto sleeps_before_hold=board.sleeps;
  usb_watchdog_armed=true;
  loop();
  assert(board.sleeps==sleeps_before_hold);
  usb_watchdog_armed=false;
#if !ROLE_SENSOR
  wireless_pending=true;
  loop();
  assert(board.sleeps==sleeps_before_hold);
  wireless_pending=false;
#endif
  // Calibration remains RF work, but its future sample deadline permits
  // bounded MCU idle in every actual role loop. Due work and other vetoes win.
  the_mesh.noise_floor_wait=true;
  auto before_noise_sleep=board.sleeps;
  const auto before_noise_ms_sleep=board.millisecond_sleeps;
  loop();
  assert(board.sleeps==before_noise_sleep+1 && board.millisecond_sleeps==before_noise_ms_sleep+1);
#if ROLE_COMPANION
  assert(board.last_sleep_ms==10); // Preserve the shorter Companion cadence.
#else
  assert(board.last_sleep_ms==50);
#endif
  the_mesh.noise_floor_delay_ms=1;
  loop();
  assert(board.sleeps==before_noise_sleep+2 && board.last_sleep_ms==1);
  the_mesh.noise_floor_delay_ms=0;
  loop();
  assert(board.sleeps==before_noise_sleep+2); // Sample/transition now due.
  the_mesh.noise_floor_delay_ms=50; the_mesh.work_pending=true;
  loop();
  assert(board.sleeps==before_noise_sleep+2); // Never widen another owner's veto.
  the_mesh.work_pending=false; usb_watchdog_armed=true;
  loop();
  assert(board.sleeps==before_noise_sleep+2);
  usb_watchdog_armed=false;
#if !ROLE_COMPANION
  // The role's earlier queue/bridge/timer deadline wins over calibration.
  the_mesh.sleep_budget_ms=17;
  loop();
  assert(board.last_sleep_ms==17);
  the_mesh.noise_floor_wait=false;
  for (uint32_t remaining : {1U,50U,999U,1001U,29999U}) {
    const auto before=board.sleeps;
    the_mesh.sleep_budget_ms=remaining;
    loop();
    assert(board.sleeps==before+1);
#if !defined(NRF52_PLATFORM)
    assert(board.last_sleep_ms==remaining); // No whole-second busy tail.
#endif
  }
#endif
  assert(board.profile_preparations==0); // Ordinary single-radio loops do not prepare a scan idle.
  // A scanner can prepare its board wake contract without admitting sleep.
  // Once settled it may idle, but it must not override Morse or other owners.
  the_mesh.noise_floor_wait=false;
  the_mesh.work_pending=false;
  the_mesh.sleep_budget_ms=1000;
  the_mesh.dual_radio=true;
  the_mesh.profile_idle_allowed=false;
  auto profile_preparations=board.profile_preparations;
  auto profile_sleeps=board.sleeps;
  auto idle_queries=the_mesh.profile_idle_queries;
  loop();
  assert(board.profile_preparations==profile_preparations+1 && board.sleeps==profile_sleeps);
  assert(the_mesh.profile_idle_queries>idle_queries);
  the_mesh.profile_idle_allowed=true;
  idle_queries=the_mesh.profile_idle_queries;
  loop();
  assert(board.profile_preparations==profile_preparations+2 && board.sleeps==profile_sleeps+1);
  assert(the_mesh.profile_idle_queries>idle_queries);
  profile_preparations=board.profile_preparations;
  profile_sleeps=board.sleeps;
  idle_queries=the_mesh.profile_idle_queries;
  for (unsigned mode=0;mode<3;++mode) {
    radio_driver.active=mode==0;
    radio_driver.rx_active=mode!=0;
    radio_driver.rx_recovering=mode==2;
    const auto sensor_before=sensors.calls;
    const auto ui_before=ui_task.calls;
    const auto watchdog_before=board.watchdogs;
    const auto usb_before=usb_services;
    loop();
    assert(board.profile_preparations==profile_preparations && board.sleeps==profile_sleeps);
    assert(the_mesh.profile_idle_queries==idle_queries);
    assert(sensors.calls==sensor_before && ui_task.calls==ui_before+1);
    assert(board.watchdogs==watchdog_before+1 && usb_services>usb_before);
  }
  radio_driver.active=radio_driver.rx_active=radio_driver.rx_recovering=false;
  the_mesh.prefs.powersaving_enabled=false;
  loop();
  assert(board.profile_preparations==profile_preparations && board.sleeps==profile_sleeps);
  assert(the_mesh.profile_idle_queries==idle_queries);
  the_mesh.prefs.powersaving_enabled=true;
  usb_watchdog_armed=true;
  loop();
  assert(board.profile_preparations==profile_preparations+1 && board.sleeps==profile_sleeps);
  usb_watchdog_armed=false;
  the_mesh.dual_radio=false;
  loop();
  assert(board.profile_preparations==profile_preparations+1 && board.sleeps==profile_sleeps+1);
}
