"""Run saved external-output settings and the actual shared buzzer adapter."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import test_gps_voice_buttons as gps_tests
from test_regulator_settings import FILESYSTEM
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

BOARD = r'''
#pragma once
#include <cstdint>
namespace mesh {
struct MainBoard {
  bool available[64] = {};
  uint16_t identity[64] = {};
  MainBoard() {
    for(unsigned i=0;i<64;++i) { available[i]=i>=6; identity[i]=i; }
    identity[41]=9; // An Arduino alias for the same physical pad.
  }
  bool isPeripheralOutputAvailable(uint8_t pin) const {
    return pin<64 && available[pin];
  }
  uint16_t peripheralPinIdentity(uint8_t pin) const {
    return pin<64?identity[pin]:0xffff;
  }
};
}
'''

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
constexpr unsigned LOW=0,HIGH=1,INPUT=0,OUTPUT=1;
inline unsigned levels[64]={},modes[64]={},writes[64]={},mode_writes[64]={};
inline uint32_t tick=0;
inline uint32_t millis() { return tick; }
inline void digitalWrite(unsigned pin,unsigned value) {
  assert(pin<64);levels[pin]=value;++writes[pin];
}
inline void pinMode(unsigned pin,unsigned mode) {
  assert(pin<64);modes[pin]=mode;++mode_writes[pin];
}
'''

RTTTL = r'''
#pragma once
#include <cassert>
#include <string>
namespace rtttl {
inline bool active=false;
inline int pin=-1;
inline unsigned begins=0,stops=0,ticks=0;
inline std::string melody;
inline void begin(int output,const char* text) {
  assert(output>=0 && output<64);pin=output;melody=text;active=true;++begins;
}
inline void stop() { active=false;++stops; }
inline bool isPlaying() { return active; }
inline bool done() { return !active; }
inline void play() { ++ticks; }
}
'''

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <helpers/IdentityStore.h>
#include <Arduino.h>
#include <MeshCore.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#undef ATOMIC_FILE_WRITER_IMPLEMENTATION
#endif
#include <helpers/PeripheralOutputs.cpp>
#include <helpers/ui/buzzer.cpp>

using namespace mesh::peripherals;
using mesh::pins::Owner;
static const char* target="/outputs_v1";
static const char* rollback="/outputs_v1.bak";

std::string command(const char* text) {
  char reply[160]={};
  assert(handleCommand(text,reply,sizeof(reply)));
  return reply;
}
void resetOwners() {
  mesh::pins::release(Owner::UserGpio,~UINT64_C(0));
  mesh::pins::release(Owner::Rotary,~UINT64_C(0));
  mesh::pins::release(Owner::Notifications,~UINT64_C(0));
}
void defaultsAndPersistence() {
  mesh::MainBoard board;MockFS fs;
  begin(board,&fs);resetOwners();
  assert(command("get peripherals")=="> buzzer=off led.notify=off led.gps=off led.ble=off");
  assert(!configuredBuzzer() && !configuredNotificationLed() && fs.writes==0);
  assert(command("set peripheral.buzzer 12")=="OK");
  assert(command("set peripheral.led.notify 13")=="OK");
  assert(command("set peripheral.led.gps 14")=="OK");
  assert(command("set peripheral.led.ble 15")=="OK");
  assert(configuredBuzzerPin()==12 && configuredNotificationLed());
  assert(claims(12) && claims(13) && claims(14) && claims(15));
  assert(!claims(11) && !claims(64));
  assert(fs.files[target].size()==12);
  assert(!fs.exists("/outputs_v1.tmp") && !fs.exists(rollback));
  begin(board,&fs);
  assert(command("get peripherals")=="> buzzer=12 led.notify=13 led.gps=14 led.ble=15");
  assert(command("set peripheral.buzzer off")=="OK");
  assert(!configuredBuzzer() && !claims(12) && modes[12]==INPUT && levels[12]==LOW);
  assert(command("set peripheral.led.notify off")=="OK");
  assert(!claims(13) && modes[13]==INPUT && levels[13]==LOW);
  begin(board,&fs);
  assert(command("get peripheral.buzzer")=="> off");
  assert(command("get peripheral.led.gps")=="> 14");
}
void parsingAndConflicts() {
  mesh::MainBoard board;MockFS fs;
  begin(board,&fs);resetOwners();
  const char* unrelated[]={"", "get", "get peripheral", "get peripheralX.buzzer", "get peripherals extra", "set peripherals"};
  for(const char* text:unrelated) {
    char reply[30]="untouched";
    assert(!handleCommand(text,reply,sizeof(reply)) && !strcmp(reply,"untouched"));
  }
  const char* invalid[]={"set peripheral.buzzer", "set peripheral.buzzer -1", "set peripheral.buzzer 64", "set peripheral.buzzer 99999999999999999999999999999999999", "set peripheral.buzzer 12 ", "set peripheral.buzzer on", "get peripheral.buzzer extra", "set peripheral.unknown 12"};
  for(const char* text:invalid)assert(command(text).find("Error:")==0);
  assert(fs.writes==0);
  assert(command("set peripheral.buzzer 1").find("reserved")!=std::string::npos);
  assert(command("set peripheral.buzzer 9")=="OK");
  assert(command("get peripheral.pins.available").find(" 9 ")!=std::string::npos);
  assert(command("set peripheral.led.notify 41").find("distinct physical")!=std::string::npos);
  assert(claims(9) && claims(41));
  board.identity[40]=64;
  assert(command("set peripheral.led.notify 40").find("distinct physical")!=std::string::npos);
  const Owner owners[]={Owner::UserGpio,Owner::Rotary,Owner::Notifications};
  for(Owner owner:owners) {
    assert(mesh::pins::tryClaim(owner,mesh::pins::pinMask(17)));
    assert(command("set peripheral.led.gps 17").find("already in use")!=std::string::npos);
    const std::string available=command("get peripheral.pins.available")+" ";
    assert(available.find(" 17 ")==std::string::npos && available.find(" 6 ")!=std::string::npos);
    assert(!claims(17));mesh::pins::release(owner,mesh::pins::pinMask(17));
  }
  assert(command("set peripheral.led.gps\t 17")=="OK");
  assert(!mesh::pins::tryClaim(Owner::Rotary,mesh::pins::pinMask(17)));
  char bounded[3]={'x','y','z'};
  assert(handleCommand("get peripherals",bounded,1));
  assert(bounded[0]==0 && bounded[1]=='y' && bounded[2]=='z');
  assert(!handleCommand(nullptr,bounded,3));
  assert(!handleCommand("get peripherals",nullptr,3));
  assert(!handleCommand("get peripherals",bounded,0));
  assert(handleCommand("get peripheral.pins.available",bounded,1));
  assert(bounded[0]==0 && bounded[1]=='y' && bounded[2]=='z');
}
void outputsAndBuzzerOwnership() {
  mesh::MainBoard board;MockFS fs;
  begin(board,&fs);resetOwners();mesh::leds::setEnabled(true);
  genericBuzzer builtin(TEST_UI_BUZZER_PIN);
  builtin.begin();builtin.quiet(false);builtin.play("Builtin:d=4,o=5,b=100:c");
  assert(rtttl::active && rtttl::pin==TEST_UI_BUZZER_PIN);
  assert(command("set peripheral.buzzer 12")=="OK");
  assert(!rtttl::active);
  setBuzzerQuiet(false);
  playNotification("Custom:d=4,o=5,b=100:e");
  assert(rtttl::active && rtttl::pin==12);
  const auto begins=rtttl::begins,stops=rtttl::stops,io=writes[TEST_UI_BUZZER_PIN],mode=mode_writes[TEST_UI_BUZZER_PIN];
  builtin.begin();builtin.play("Ignored:d=4,o=5,b=100:c");builtin.playNotification("Ignored:d=4,o=5,b=100:c");
  builtin.stop();builtin.stopNotification();builtin.startup();builtin.shutdown();builtin.loop();builtin.quiet(true);
  assert(!builtin.isPlaying());
  assert(!builtin.speakButton(ButtonVoicePrompt::GpsOn));
  assert(rtttl::begins==begins && rtttl::stops==stops && rtttl::active && rtttl::pin==12);
  assert(writes[TEST_UI_BUZZER_PIN]==io && mode_writes[TEST_UI_BUZZER_PIN]==mode);
  const auto ticks=rtttl::ticks;loop(false,false,false);assert(rtttl::ticks==ticks+1);
  setBuzzerQuiet(true);assert(!rtttl::active);
  playNotification("Muted:d=4,o=5,b=100:c");assert(!rtttl::active);
  setBuzzerQuiet(false);playNotification("Custom:d=4,o=5,b=100:e");stopNotification();assert(!rtttl::active);
  assert(command("set peripheral.buzzer off")=="OK");
  builtin.quiet(false);builtin.play("Builtin:d=4,o=5,b=100:c");assert(rtttl::active && rtttl::pin==TEST_UI_BUZZER_PIN);
  // Updating the custom mute preference while no custom pin is assigned must
  // not stop the built-in UI's global tone.
  setBuzzerQuiet(true);assert(rtttl::active);
  builtin.stop();
  assert(command("set peripheral.led.notify 12")=="OK");
  assert(command("set peripheral.led.gps 13")=="OK");
  assert(command("set peripheral.led.ble 14")=="OK");
  notificationLed(true);loop(true,true,false);
  assert(levels[12]==HIGH && levels[13]==HIGH && levels[14]==HIGH);
  mesh::leds::setEnabled(false);loop(true,true,true);
  assert(levels[12]==LOW && levels[13]==LOW && levels[14]==LOW);
  mesh::leds::setEnabled(true);loop(false,false,false);
  assert(levels[12]==HIGH && levels[13]==LOW && levels[14]==LOW);
  notificationLed(false);assert(levels[12]==LOW);
  tick=100;notifyMessage(false,true,true);
  assert(levels[12]==HIGH && !rtttl::active); // The custom buzzer is disabled.
  notificationLed(false);assert(levels[12]==HIGH); // A legacy pulse still owns it.
  tick=1599;loop(false,false,false);assert(levels[12]==HIGH);
  tick=1600;loop(false,false,false);assert(levels[12]==LOW);
  tick=UINT32_MAX-500;notifyMessage(true,false,true);notificationLed(true);
  tick=999;loop(false,false,false);assert(levels[12]==HIGH); // Engine ownership survives expiry.
  notificationLed(false);assert(levels[12]==LOW);
  notifyMessage(true,false,false);assert(levels[12]==LOW);
  assert(command("set peripheral.buzzer 18")=="OK");
  setBuzzerQuiet(false);notifyMessage(false,true,false);
  assert(rtttl::active && rtttl::pin==18 && rtttl::melody.find("MsgRcv3:")==0);
  notifyMessage(true,true,false);assert(rtttl::melody.find("kerplop:")==0);
  setBuzzerQuiet(true);notifyMessage(false,true,true);assert(!rtttl::active && levels[12]==HIGH);
  stopNotification();assert(levels[12]==HIGH); // Sound stop does not cancel the LED pulse.
  loop(true,true,false);
  assert(command("set peripheral.led.gps 16")=="OK");
  assert(levels[13]==LOW && modes[13]==INPUT && levels[16]==HIGH && modes[16]==OUTPUT);
}
void saveFailuresAndRecovery() {
  for(unsigned failure=0;failure<5;++failure) {
    mesh::MainBoard board;MockFS fs;
    begin(board,&fs);resetOwners();
    assert(command("set peripheral.led.notify 12")=="OK");notificationLed(true);
    const auto old=fs.files[target];
    if(failure==0)fs.fail_open=true;
    if(failure==1)fs.fail_write=true;
    if(failure==2)fs.corrupt_write=true;
    if(failure==3)fs.fail_rename_from="/outputs_v1.tmp";
    if(failure==4)fs.fail_read_path=target;
    const auto next_modes=mode_writes[15],old_modes=mode_writes[12];
    assert(command("set peripheral.led.notify 15").find("save unconfirmed")!=std::string::npos);
    assert(command("get peripheral.led.notify")=="> 12");
    assert(claims(12) && !claims(15) && levels[12]==HIGH);
    assert(mode_writes[15]==next_modes && mode_writes[12]==old_modes);
    if(failure!=4)assert(fs.files[target]==old);
    fs.fail_open=fs.fail_write=fs.corrupt_write=false;
    fs.fail_rename_from.clear();fs.fail_read_path.clear();
    bool uncertain=failure==4;
#if defined(NRF52_PLATFORM)
    // AtomicFileWriter cannot distinguish an unperformed rename from an
    // ambiguous publication when the requested public bytes are absent.
    uncertain=uncertain||failure==3;
#endif
    if(uncertain) {
      const auto io=fs.io;
      assert(command("set peripheral.buzzer 18").find("writes locked")!=std::string::npos);
      assert(command("set peripheral.led.notify off").find("reboot may differ")!=std::string::npos);
      assert(command("get peripheral.led.notify")=="> 12");
      assert(fs.io==io && claims(12) && !claims(15) && !claims(18) && levels[12]==HIGH);
    } else {
      // Confirmed failures remain retryable without a reboot/reload.
      assert(command("set peripheral.led.notify 15")=="OK");
      assert(claims(15) && !claims(12));
    }
    begin(board,&fs);
    assert(command("get peripheral.led.notify")== (uncertain && failure!=4?"> 12":"> 15"));
    assert(command("set peripheral.led.notify 16")=="OK");
    assert(claims(16)); // Reload establishes disk authority and unlocks writes.
  }
  mesh::MainBoard board;MockFS fs;
  begin(board,&fs);resetOwners();
  assert(command("set peripheral.led.gps 12")=="OK");
  const auto saved=fs.files[target];
  fs.files[rollback]=saved;fs.files.erase(target);
  begin(board,&fs);assert(claims(12) && fs.files[target]==saved && !fs.exists(rollback));
  fs.files[rollback]=saved;fs.files[target]={0};
  begin(board,&fs);assert(claims(12) && fs.files[target]==saved);
  fs.files[rollback]=saved;fs.files.erase(target);fs.fail_rename_from=rollback;
  begin(board,&fs);assert(claims(12));
  assert(command("set peripheral.led.gps 13").find("save unconfirmed")!=std::string::npos);
  assert(claims(12) && !claims(13));
  fs.fail_rename_from.clear();begin(board,&fs);assert(claims(12));
  for(unsigned i=0;i<saved.size();++i) {
    fs.files[target]=saved;fs.files[target][i]^=1;
    begin(board,&fs);assert(!claims(12));
    assert(command("get peripherals").find("saved pins rejected")!=std::string::npos);
  }
  fs.files[target]=saved;board.available[12]=false;
  const auto count=mode_writes[12];begin(board,&fs);
  assert(!claims(12) && mode_writes[12]==count);
  board.available[12]=true;
  assert(mesh::pins::tryClaim(Owner::Rotary,mesh::pins::pinMask(12)));
  begin(board,&fs);assert(!claims(12));
  mesh::pins::release(Owner::Rotary,mesh::pins::pinMask(12));
}
unsigned gpioIo() {
  unsigned count=0;
  for(unsigned pin=0;pin<64;++pin)count+=writes[pin]+mode_writes[pin];
  return count;
}
void terminalShutdown() {
  mesh::MainBoard board;MockFS fs;
  begin(board,&fs);resetOwners();
  genericBuzzer builtin(TEST_UI_BUZZER_PIN);
  builtin.begin();builtin.quiet(false);builtin.play("Builtin:d=4,o=5,b=100:c");
  auto io=fs.io,gpio=gpioIo(),stops=rtttl::stops;
  shutdown();shutdown();
  assert(fs.io==io&&gpioIo()==gpio&&rtttl::stops==stops&&rtttl::active);
  builtin.stop();
  begin(board,&fs);
  assert(command("set peripheral.buzzer 12")=="OK");
  assert(command("set peripheral.led.notify 13")=="OK");
  assert(command("set peripheral.led.gps 14")=="OK");
  assert(command("set peripheral.led.ble 15")=="OK");
  const auto saved=fs.files[target];
  setBuzzerQuiet(false);playNotification("Custom:d=4,o=5,b=100:c");
  notificationLed(true);notifyMessage(false,false,true);loop(true,true,true);
  assert(rtttl::active&&needsPolling()&&levels[13]==HIGH&&levels[14]==HIGH&&levels[15]==HIGH);
  io=fs.io;shutdown();
  assert(fs.io==io&&fs.files[target]==saved&&!rtttl::active&&!needsPolling());
  assert(!configuredBuzzer()&&!configuredNotificationLed());
  for(unsigned pin=12;pin<=15;++pin)assert(levels[pin]==LOW&&modes[pin]==INPUT&&!claims(pin));
  assert(mesh::pins::held(Owner::Outputs)==0);
  assert(command("get peripherals")=="> buzzer=off led.notify=off led.gps=off led.ble=off");
  gpio=gpioIo();stops=rtttl::stops;
  shutdown();loop(true,true,true);notifyMessage(false,true,true);
  assert(gpioIo()==gpio&&rtttl::stops==stops&&fs.io==io&&!needsPolling());
  assert(command("set peripheral.led.notify 16").find("Error:")==0);
  assert(fs.io==io&&!claims(16));
  // Saved wiring is restored only by a fresh begin, never by shutdown.
  begin(board,&fs);assert(configuredBuzzerPin()==12&&configuredNotificationLed()&&claims(15));
  shutdown();
}
int main() {
  defaultsAndPersistence();parsingAndConflicts();outputsAndBuzzerOwnership();saveFailuresAndRecovery();
  terminalShutdown();
}
'''

VOICE_TAKEOVER = r'''
#include <cassert>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_GPS_VOICE 1
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main() {
  genericBuzzer builtin;
  genericBuzzer custom(-1);
  builtin.begin();builtin.quiet(false);
  assert(builtin.speakGps(true) && rtttl::isPlaying());
  genericBuzzer::setOverridePin(12);
  custom.setPin(12);custom.quiet(false);
  custom.playNotification("Custom:d=4,o=5,b=100:e");
  auto starts=rtttl::begins;
  builtin.loop();builtin.stop();builtin.quiet(true);builtin.shutdown();
  assert(rtttl::isPlaying() && rtttl::begins==starts && !builtin.isPlaying());
  assert(!HwPWM3.owned && !registers.ENABLE && pin_states[PIN_BUZZER_EN]==LOW);
  custom.setPin(-1);genericBuzzer::setOverridePin(-1);
  now_ms+=1000;builtin.loop();assert(!builtin.isPlaying() && !registers.ENABLE);
  // Ordinary builtin play powers its driver again after custom takeover.
  builtin.quiet(false);builtin.play("Builtin:d=4,o=5,b=100:c");
  assert(rtttl::isPlaying() && pin_states[PIN_BUZZER_EN]==HIGH);
  builtin.stop();assert(builtin.speakButton(ButtonVoicePrompt::Ready));
  builtin.loop();now_ms+=100;builtin.loop();
  assert(registers.ENABLE && HwPWM3.owned);
  genericBuzzer::setOverridePin(12);
  assert(!registers.ENABLE && !HwPWM3.owned);
  custom.setPin(12);custom.playNotification("Custom:d=4,o=5,b=100:e");
  builtin.loop();assert(rtttl::isPlaying());
  custom.setPin(-1);genericBuzzer::setOverridePin(-1);
  now_ms+=1000;builtin.loop();assert(!builtin.isPlaying() && !registers.ENABLE);
}
'''


class PeripheralOutputsTests(unittest.TestCase):
    def test_custom_takeover_cancels_builtin_tone_and_speech_without_resuming_them(self):
        gps_tests.Tests.build(self, VOICE_TAKEOVER, rtttl=True)

    def test_settings_outputs_and_single_buzzer_owner(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="meshcore-peripheral-outputs-") as temp:
            folder = Path(temp)
            (folder / "helpers").mkdir()
            filesystem = FILESYSTEM.replace(
                "  void flush() {}", "  void flush() {}\n  bool setBufferSize(size_t) { return true; }")
            filesystem = filesystem.replace("unsigned writes=0,renames=0;", "unsigned writes=0,renames=0;mutable unsigned io=0;")
            filesystem = filesystem.replace("return files.count(name);", "++io;return files.count(name);")
            filesystem = filesystem.replace("return files.erase(name)!=0;", "++io;return files.erase(name)!=0;")
            filesystem = filesystem.replace("++renames;", "++io;++renames;")
            filesystem = filesystem.replace("if(fail_open)return", "++io;if(fail_open)return")
            filesystem = filesystem.replace("inline size_t File::size() const {", "inline size_t File::size() const { ++fs->io;")
            filesystem = filesystem.replace("inline size_t File::read(uint8_t* bytes,size_t length) {", "inline size_t File::read(uint8_t* bytes,size_t length) { ++fs->io;")
            filesystem = filesystem.replace("inline size_t File::write(const uint8_t* bytes,size_t length) {", "inline size_t File::write(const uint8_t* bytes,size_t length) { ++fs->io;")
            (folder / "helpers/IdentityStore.h").write_text(filesystem)
            (folder / "helpers/ContactFileTransaction.h").write_text(
                (ROOT / "src/helpers/ContactFileTransaction.h").read_text())
            (folder / "MeshCore.h").write_text(BOARD)
            (folder / "Arduino.h").write_text(ARDUINO)
            (folder / "NonBlockingRtttl.h").write_text(RTTTL)
            (folder / "harness.cpp").write_text(HARNESS)
            for platform, flags in {
                    "esp32": ["-DESP32_PLATFORM=1", "-DPIN_BUZZER=4"],
                    "nrf52": ["-DNRF52_PLATFORM=1", "-DPIN_BUZZER=4"],
                    "rp2040_headless": ["-DRP2040_PLATFORM=1"],
            }.items():
                with self.subTest(platform=platform):
                    executable = folder / platform
                    compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-g", *SANITIZER_FLAGS,
                                    "-DMESH_CUSTOM_PERIPHERALS=1", "-DTEST_UI_BUZZER_PIN=4", "-DMESH_GPS_VOICE=0",
                                    *flags, "-I", str(folder), "-I", str(ROOT / "src"),
                                    "-I", str(ROOT / "src/helpers"),
                                    str(folder / "harness.cpp"), "-o", str(executable)],
                                   capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
