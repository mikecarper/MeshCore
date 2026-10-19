#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <map>
#include <algorithm>
#include <iostream>
#include <helpers/RegulatorMode.h>
#include <helpers/RegulatorRecovery.h>
#include <helpers/IdentityStore.h>
#include <Dispatcher.h>
uint32_t mock_now=0;
#if defined(ESP32)
#include <esp_system.h>
int mock_reset_reason=ESP_RST_POWERON;
#elif defined(RP2040_PLATFORM)
bool mock_watchdog_enabled=false,mock_watchdog_any=false;
#else
namespace mesh { namespace power {
volatile RecoveryRecord test_retained;
ResetKind test_reset=ResetKind::Cold;
volatile RecoveryRecord& platformRecoveryRecord() { return test_retained; }
ResetKind platformResetKind() { return test_reset; }
bool platformRecoverySupported() { return true; }
unsigned configured_percent_calls=0;
uint8_t configuredBatteryPercent(uint16_t mv) {
  ++configured_percent_calls;
  return mv<=3000 ? 0 : mv>=4200 ? 100 : uint8_t((mv-3000)*100/1200);
}
}}
#endif
#if !defined(NRF52_PLATFORM)
#include <helpers/RegulatorPlatform.cpp>
#endif
#if defined(NRF52_PLATFORM)
// Compile the production writer implementation, including independent CRC32C
// validation and the public-file verification after an attempted rename.
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#undef ATOMIC_FILE_WRITER_IMPLEMENTATION
#endif
#include <helpers/RegulatorSettings.cpp>
using namespace mesh::power;
static void retainedClear() {
  volatile uint8_t* p=reinterpret_cast<volatile uint8_t*>(&platformRecoveryRecord());
  for(size_t i=0;i<sizeof(RecoveryRecord);++i)p[i]=0;
}
static void resetKind(ResetKind kind) {
#if defined(ESP32)
  mock_reset_reason=kind==ResetKind::Crash ? ESP_RST_PANIC : kind==ResetKind::Intentional
      ? ESP_RST_SW : kind==ResetKind::Wake ? ESP_RST_DEEPSLEEP : kind==ResetKind::Cold
      ? ESP_RST_POWERON : ESP_RST_BROWNOUT;
#elif defined(RP2040_PLATFORM)
  mock_watchdog_enabled=kind==ResetKind::Crash;
  mock_watchdog_any=kind==ResetKind::Crash || kind==ResetKind::Intentional;
#else
  test_reset=kind;
#endif
}
static void resetGlobals(bool cold=false) {
  mesh::power::main_board=nullptr;mesh::power::main_radio=nullptr;
  mesh::power::settings_fs=nullptr;
  mesh::power::requested[0]=mesh::power::requested[1]=RegulatorMode::Default;
  mesh::power::saved_fallback=0;mesh::power::initialized=false;
  mesh::power::fallback_saved=true;
  mesh::power::last_service=mesh::power::last_battery=mesh::power::last_save=0;
  mock_now=0;
  if(cold)retainedClear();
}
static void fresh(mesh::MainBoard& board,mesh::Radio& radio,MockFS& fs,
                  ResetKind kind=ResetKind::Cold,bool cold=false) {
  resetGlobals(cold);resetKind(kind);board.resetHardware();radio.resetHardware();
  assert(beginRegulatorSettings(board,radio,&fs));
  assert(radio.active==RegulatorMode::Default); // selecting touches no active radio
  radio.initialize();
}
static std::string command(const char* text) {
  char reply[160];memset(reply,0xa5,sizeof(reply));
  assert(handleRegulatorCommand(text,reply,sizeof(reply)));
  assert(memchr(reply,0,sizeof(reply)));
  return reply;
}
static void advance(uint32_t ms) { mock_now+=ms;serviceRegulatorSettings(); }
static void counts(unsigned cpu,unsigned rf) {
  assert(recovery().crashCount(RECOVERY_MCU)==cpu);
  assert(recovery().crashCount(RECOVERY_RADIO)==rf);
}
int main(int argc,char** argv) {
  assert(argc==2);const std::string test=argv[1];
  mesh::MainBoard board;mesh::Radio radio;MockFS fs;
  if(test=="select") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    assert(board.active==RegulatorMode::Dcdc && radio.active==RegulatorMode::Dcdc);
    const unsigned applies=board.applies,configs=radio.configures,inits=radio.inits;
    assert(command("set mcu.regulator ldo").find("reboot required")!=std::string::npos);
    assert(command("set radio.regulator ldo").find("reboot required")!=std::string::npos);
    assert(requestedRegulatorMode(true)==RegulatorMode::Ldo);
    assert(requestedRegulatorMode(false)==RegulatorMode::Ldo);
    assert(board.active==RegulatorMode::Dcdc && radio.active==RegulatorMode::Dcdc);
    assert(board.applies==applies && radio.configures==configs && radio.inits==inits);
    advance(1000);
    assert(board.active==RegulatorMode::Dcdc && radio.active==RegulatorMode::Dcdc);
    fresh(board,radio,fs,ResetKind::Cold,true);
    assert(board.active==RegulatorMode::Ldo && radio.active==RegulatorMode::Ldo);
  } else if(test=="fallback") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(unsigned n=1;n<=3;++n) {
      advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(n,n);
      assert(regulatorFallbackActive(true)==(n==3));
      assert(regulatorFallbackActive(false)==(n==3));
    }
    assert(board.active==RegulatorMode::Ldo && radio.active==RegulatorMode::Ldo);
    assert(fs.files.count("/regulator_v1"));
    fresh(board,radio,fs,ResetKind::Cold,true);counts(0,0);
    assert(board.active==RegulatorMode::Ldo && radio.active==RegulatorMode::Ldo);
    assert(command("get radio.regulator").find("fallback ldo")!=std::string::npos);
  } else if(test=="domains") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    command("set mcu.regulator ldo");fresh(board,radio,fs,ResetKind::Intentional);
    for(unsigned n=1;n<=3;++n) { advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(0,n); }
    assert(!regulatorFallbackActive(true) && regulatorFallbackActive(false));
    const auto saved=fs.files["/regulator_v1"];
    command("set mcu.regulator dcdc");
    assert(regulatorFallbackActive(false) && !regulatorFallbackActive(true));
    assert(requestedRegulatorMode(false)==RegulatorMode::Default);
    fresh(board,radio,fs,ResetKind::Intentional);
    assert(board.active==RegulatorMode::Dcdc && radio.active==RegulatorMode::Ldo);
    command("set radio.regulator auto");
    assert(!regulatorFallbackActive(false));
    fresh(board,radio,fs,ResetKind::Intentional);
    assert(radio.active==RegulatorMode::Dcdc);
  } else if(test=="battery") {
    for(unsigned mv:{3588u,0u,1999u,5501u}) {
      fs.files.clear();board.mv=mv;
      fresh(board,radio,fs,ResetKind::Cold,true);
      for(unsigned n=0;n<4;++n) {advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(0,0);}
      assert(!regulatorFallbackActive(true) && !regulatorFallbackActive(false));
    }
    board.mv=3600;fs.files.clear();fresh(board,radio,fs,ResetKind::Cold,true);
    // Heartbeat alone cannot make a 60-second-old voltage sample fresh.
    recovery().heartbeat(mock_now+REGULATOR_BATTERY_FRESH_MS+1);
    fresh(board,radio,fs,ResetKind::Crash);counts(0,0);
    // A median rejects one high outlier rather than guessing from the maximum.
    board.samples={3588,5000,3588};fresh(board,radio,fs,ResetKind::Cold,true);
    advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(0,0);
  } else if(test=="clean") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(unsigned n=1;n<=2;++n) {advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(n,n);}
    meshRegulatorCleanReset();fresh(board,radio,fs,ResetKind::Crash);counts(0,0);
    // A clean-reset marker cannot discard a learned fallback that has not yet
    // reached flash because a write failed.
    for(unsigned n=1;n<=2;++n) {advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(n,n);}
    fs.fail_write=true;advance(1000);fresh(board,radio,fs,ResetKind::Crash);
    assert(regulatorFallbackActive(true) && regulatorFallbackActive(false));
    meshRegulatorCleanReset();
    resetGlobals();meshRegulatorCleanReset(); // early reboot before filesystem setup
    fs.fail_write=false;fresh(board,radio,fs,ResetKind::Crash);
    assert(regulatorFallbackActive(true) && regulatorFallbackActive(false));
  } else if(test=="atomic") {
    for(unsigned failure=0;failure<5;++failure) {
      fs=MockFS();fresh(board,radio,fs,ResetKind::Cold,true);
      command("set mcu.regulator ldo");const auto old=fs.files["/regulator_v1"];
      if(failure==0)fs.fail_write=true;
      if(failure==1)fs.fail_open=true;
#if defined(NRF52_PLATFORM)
      if(failure==2)fs.fail_rename_from="/regulator_v1.tmp";
      if(failure==3)fs.fail_read_path="/regulator_v1.tmp";
      if(failure==4)fs.corrupt_write=true;
#else
      if(failure==2)fs.fail_rename_from="/regulator_v1";
      if(failure==3)fs.fail_rename_from="/regulator_v1.tmp";
      if(failure==4) {fs.fail_rename_from="/regulator_v1.tmp";fs.fail_restore=true;}
#endif
      assert(command("set radio.regulator ldo").find("save failed")!=std::string::npos);
      assert(requestedRegulatorMode(false)==RegulatorMode::Default);
      fs.fail_write=fs.fail_open=fs.fail_restore=fs.corrupt_write=false;
      fs.fail_rename_from.clear();fs.fail_read_path.clear();
      fresh(board,radio,fs,ResetKind::Cold,true);
      assert(requestedRegulatorMode(true)==RegulatorMode::Ldo);
      assert(requestedRegulatorMode(false)==RegulatorMode::Default);
      assert(fs.files["/regulator_v1"]==old);
    }
  } else if(test=="corruption") {
    fresh(board,radio,fs,ResetKind::Cold,true);command("set mcu.regulator ldo");
    const auto good=fs.files["/regulator_v1"];
    for(size_t byte=0;byte<good.size();++byte) {
      fs.files["/regulator_v1"]=good;fs.files["/regulator_v1"][byte]^=1;
      fresh(board,radio,fs,ResetKind::Cold,true);
      assert(requestedRegulatorMode(true)==RegulatorMode::Default);
      assert(board.active==RegulatorMode::Dcdc);
    }
    fs.files["/regulator_v1"]=good;fs.files["/regulator_v1"].push_back(0);
    fresh(board,radio,fs,ResetKind::Cold,true);assert(requestedRegulatorMode(true)==RegulatorMode::Default);
    fs.files["/regulator_v1"]=good;fs.files["/regulator_v1"].pop_back();
    fs.files["/regulator_v1.bak"]=good;
    fresh(board,radio,fs,ResetKind::Cold,true);assert(board.active==RegulatorMode::Ldo);
  } else if(test=="unsupported") {
    board.modes=radio.modes=REGULATOR_LDO;board.default_mode=radio.default_mode=RegulatorMode::Ldo;
    fresh(board,radio,fs,ResetKind::Cold,true);
    assert(command("set mcu.regulator dcdc").find("unsupported")!=std::string::npos);
    assert(command("set radio.regulator dcdc").find("unsupported")!=std::string::npos);
    assert(board.active==RegulatorMode::Ldo && radio.active==RegulatorMode::Ldo);
    assert(!board.dcdc_applied && !radio.dcdc_applied);
    // A previously saved choice from a DC/DC-capable recipe cannot activate
    // absent circuitry if that file is encountered by an LDO-only recipe.
    board.modes=radio.modes=3;
    command("set mcu.regulator dcdc");command("set radio.regulator dcdc");
    board.modes=radio.modes=REGULATOR_LDO;
    board.dcdc_applied=radio.dcdc_applied=false;
    resetGlobals(true);resetKind(ResetKind::Cold);board.resetHardware();radio.resetHardware();
    (void)beginRegulatorSettings(board,radio,&fs);
    assert(!board.dcdc_applied && !radio.dcdc_applied);
    assert(board.active!=RegulatorMode::Dcdc && radio.active!=RegulatorMode::Dcdc);
    board.modes=radio.modes=0;
    assert(command("get mcu.regulator").find("unsupported")!=std::string::npos);
    assert(command("get radio.regulator").find("unsupported")!=std::string::npos);
  } else if(test=="bounds") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(const char* text:{"get mcu.regulatorx","set radio.regulatorx ldo","get radio.regulator.extra","get mcu.regulato","set radio.regulator\tldo","other"}) {
      char reply[4]={'x','x','x','x'};assert(!handleRegulatorCommand(text,reply,sizeof(reply)));
      assert(reply[0]=='x' && reply[3]=='x');
    }
    for(const char* text:{"get mcu.regulator arg","set radio.regulator", "set radio.regulator dcdc extra","set mcu.regulator LDO"})
      assert(command(text).find("Error:")==0);
    for(size_t size:{0u,1u,2u,8u,32u}) {
      char guarded[40];memset(guarded,0x5a,sizeof(guarded));
      assert(handleRegulatorCommand("get radio.regulator",guarded+3,size));
      for(size_t i=0;i<3;++i)assert(guarded[i]==0x5a);
      for(size_t i=size+3;i<sizeof(guarded);++i)assert(guarded[i]==0x5a);
      if(size)assert(memchr(guarded+3,0,size));
    }
  } else if(test=="no_store") {
    resetGlobals(true);resetKind(ResetKind::Cold);
    assert(beginRegulatorSettings(board,radio,nullptr));radio.initialize();
    assert(command("set radio.regulator ldo").find("save failed")!=std::string::npos);
    assert(requestedRegulatorMode(false)==RegulatorMode::Default);
    assert(radio.active==RegulatorMode::Dcdc && fs.files.empty());
  } else if(test=="failed_radio") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(unsigned n=1;n<=3;++n) {
      radio.active=RegulatorMode::Default; // radio initialization never succeeded
      advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(n,0);
    }
    assert(regulatorFallbackActive(true) && !regulatorFallbackActive(false));
  } else if(test=="platform_unsupported") {
    assert(!platformRecoverySupported());
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(unsigned n=0;n<4;++n){advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(0,0);}
    assert(!regulatorRecoveryAvailable());
    assert(!regulatorFallbackActive(true) && !regulatorFallbackActive(false));
  } else if(test=="platform") {
#if defined(ESP32)
    for(int reason:{ESP_RST_PANIC,ESP_RST_INT_WDT,ESP_RST_TASK_WDT,ESP_RST_WDT}) {
      mock_reset_reason=reason;assert(platformResetKind()==ResetKind::Crash);
    }
    mock_reset_reason=ESP_RST_SW;assert(platformResetKind()==ResetKind::Intentional);
    mock_reset_reason=ESP_RST_DEEPSLEEP;assert(platformResetKind()==ResetKind::Wake);
    for(int reason:{ESP_RST_POWERON,ESP_RST_EXT}) {mock_reset_reason=reason;assert(platformResetKind()==ResetKind::Cold);}
    mock_reset_reason=ESP_RST_BROWNOUT;assert(platformResetKind()==ResetKind::Unknown);
#elif defined(RP2040_PLATFORM)
    mock_watchdog_any=mock_watchdog_enabled=false;assert(platformResetKind()==ResetKind::Cold);
    mock_watchdog_any=true;assert(platformResetKind()==ResetKind::Intentional);
    mock_watchdog_enabled=true;assert(platformResetKind()==ResetKind::Crash);
#endif
    assert(&platformRecoveryRecord()==&platformRecoveryRecord());
  } else if(test=="healthy") {
    fresh(board,radio,fs,ResetKind::Cold,true);advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(1,1);
    for(unsigned n=0;n<301;++n)advance(1000);
    fresh(board,radio,fs,ResetKind::Crash);counts(1,1);
  } else if(test=="retry") {
    fresh(board,radio,fs,ResetKind::Cold,true);
    for(unsigned n=1;n<=2;++n){advance(1000);fresh(board,radio,fs,ResetKind::Crash);counts(n,n);}
    fs.fail_write=true;advance(1000);fresh(board,radio,fs,ResetKind::Crash);
    assert(command("get radio.regulator").find("fallback not saved")!=std::string::npos);
    fs.fail_write=false;advance(30000);
    assert(command("get radio.regulator").find("fallback not saved")==std::string::npos);
    fresh(board,radio,fs,ResetKind::Cold,true);
    assert(board.active==RegulatorMode::Ldo && radio.active==RegulatorMode::Ldo);
  } else assert(false);
}
