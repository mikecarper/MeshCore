#include <helpers/MorseLoopPolicy.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <cstdarg>
static unsigned usb_services=0,command_services=0,transport_services=0,delays=0;
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
bool isUsbLoggingWatchdogArmed() { return false; }
bool serviceUsbLoggingWatchdog(bool(*)()) { ++usb_services; return false; }
Stream& usbConsolePort() { return console; }
void noteUsbLoggingStatsCommand(const char*) { ++usb_services; }
namespace wireless {
struct Control { void service(uint32_t) { ++transport_services; } bool pending() { return false; } } controller;
Control& control() { return controller; }
}
}
struct Board {
  unsigned calls=0,watchdogs=0,sleeps=0,fans=0,reboots=0;
  void loop() { ++calls; }
  void feedWatchdog(bool=true) { ++watchdogs; }
  void sleep(uint32_t) { ++sleeps; }
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
  void loop() { ++calls; }
  Prefs* getNodePrefs() { return &prefs; }
  uint32_t getPowerSaveSleepSeconds(unsigned) { return 1; }
  bool millisHasNowPassed(uint32_t) { return true; }
  bool isTrackerModeEnabled() { return false; }
  bool hasPendingWork() { return false; }
  mesh::powerdiag::Mask pendingWorkReason() const { return 0; }
  void handleCommand(uint32_t,char*,char* reply) { reply[0]=0; ++command_services; }
  void handleUsbCommand(char*,char* reply) { reply[0]=0; ++command_services; }
} the_mesh;
TestRadio radio_driver;
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
}
