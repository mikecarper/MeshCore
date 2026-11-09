"""Exercise production Companion notification adapters and transactional GPIO claims."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_notification_settings import STUB as FILESYSTEM
from test_replay_reset_integration import extract_braced
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <helpers/CompanionNotificationPolicy.h>
#include <helpers/LedPolicy.h>
#include <helpers/RuntimePinClaims.h>
#include <helpers/RadioMaintenanceCLI.h>
#include "NotificationSettingsFile.h"
constexpr unsigned INPUT=0,OUTPUT=1,LOW=0,HIGH=1;
static unsigned levels[64]={},modes[64]={},writes[64]={};
static uint32_t now=0;
uint32_t millis() { return now; }
void digitalWrite(unsigned pin,unsigned value) { assert(pin<64);levels[pin]=value;++writes[pin]; }
void pinMode(unsigned pin,unsigned value) { assert(pin<64);modes[pin]=value; }
namespace mesh {
struct MainBoard {
  bool available[64]={};
  uint16_t identity[64]={};
  MainBoard() { for(unsigned i=0;i<64;++i) { available[i]=i>=6;identity[i]=i; }identity[41]=9; }
  bool isPeripheralOutputAvailable(uint8_t pin) const { return pin<64&&available[pin]; }
  uint16_t peripheralPinIdentity(uint8_t pin) const { return pin<64?identity[pin]:0xffff; }
};
namespace peripherals {
bool has_buzzer=true,has_led=true,quiet=true,led=false,last_channel=false,last_sound=false,last_led=false;
unsigned melodies=0,legacy=0,cli_calls=0;
std::string tune;
bool configuredBuzzer() { return has_buzzer; }
bool configuredNotificationLed() { return has_led; }
void setBuzzerQuiet(bool value) { quiet=value; }
void playNotification(const char* text) { ++melodies;tune=text?text:""; }
void notificationLed(bool on) { led=on&&mesh::leds::enabled(); }
void notifyMessage(bool channel,bool sound,bool lamp) {
  ++legacy;last_channel=channel;last_sound=sound;last_led=lamp;
}
bool handleCommand(const char* command,char* reply,size_t size) {
  ++cli_calls;if(strcmp(command,"get peripherals"))return false;
  snprintf(reply,size,"> external outputs");return true;
}
}
namespace ui {
unsigned rotary_calls=0;
bool handleRotaryCommand(const char* command,char* reply,size_t size) {
  ++rotary_calls;if(strcmp(command,"get rotary"))return false;
  snprintf(reply,size,"> rotary");return true;
}
}
}
mesh::MainBoard board;
struct UI {
  uint8_t notificationCapabilities() const { return 0; }
  unsigned melodies=0,syncs=0;
  void notificationVibration(bool) {}
  void notificationMelody(const char*) { ++melodies; }
  void notificationScreen(int8_t) {}
  void syncAlertPreferences() { ++syncs; }
};
struct Radio { bool pending=false;bool isAGCMaintenancePending() const { return pending; } };
struct Serial { bool isConnected() const { return false; } };
struct Prefs { uint8_t buzzer_quiet=1,vibe_quiet=0; };
struct DataStore {
  FakeFS fs;
  FILESYSTEM* getPrimaryFS() { return &fs; }
};
class MyMesh : public mesh::notify::Sink,public mesh::notify::Store {
public:
  Prefs _prefs;
  UI ui;UI* _ui=nullptr;
  DataStore store;DataStore* _store=&store;
  Radio radio;Radio* _radio=&radio;
  bool save_prefs=true;
  bool savePrefs() { return save_prefs; }
  mesh::notify::Controller _notifications{*this,*this};
  uint8_t capabilities() const override;
  bool gpioAvailable(uint8_t) const override;
  void pulse(mesh::notify::Output,bool,int8_t) override;
  void melody(const char*) override;
  void screen(int8_t) override;
  bool save(const mesh::notify::Settings&) override;
  bool notificationGpioMask(const mesh::notify::Settings&,uint64_t&) const;
  void releaseNotificationGpio(uint64_t);
  bool claimNotificationGpio();
  void notifyCustomMessage(bool);
  bool handleAlertCommand(const char*,char*,size_t);
  bool handleLocalControlCommand(const char*,char*,size_t);
};
@ADAPTERS@
using mesh::pins::Owner;
void resetOwners() {
  for(auto owner:{Owner::Notifications,Owner::Outputs,Owner::Rotary,Owner::UserGpio})
    mesh::pins::release(owner,~UINT64_C(0));
  board=mesh::MainBoard{};
}
std::string command(MyMesh& mesh,const char* text) {
  char reply[160]={};assert(mesh._notifications.handle(text,reply,sizeof(reply),now,false));return reply;
}
void claimsAndFailedSaves() {
  resetOwners();MyMesh mesh;
  assert(command(mesh,"set notify.gpio all 9:100,100")=="OK");
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(9));
  assert(mesh.gpioAvailable(9)&&mesh.gpioAvailable(41));
  assert(!mesh::pins::tryClaim(Owner::Outputs,mesh::pins::pinMask(9)));
  assert(command(mesh,"notify.test all")=="OK - alert test started");
  assert(mesh._notifications.active()&&levels[9]==HIGH);
  const auto old=mesh.store.fs.files["/notify_prefs"];
  mesh.store.fs.fail_write=true;
  assert(command(mesh,"set notify.gpio all 10:50")=="Error: save failed");
  assert(mesh.store.fs.files["/notify_prefs"]==old);
  assert(mesh._notifications.settings().rules[0].pin==9&&mesh._notifications.active());
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(9)&&levels[9]==HIGH);
  mesh.store.fs.fail_write=false;
  mesh.store.fs.fail_read_path="/notify_prefs";
  assert(command(mesh,"set notify.gpio all 10:50")=="Error: save failed");
  assert(mesh._notifications.settings().rules[0].pin==9&&mesh._notifications.active());
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(9)&&levels[9]==HIGH);
  mesh.store.fs.fail_read_path.clear();
  assert(command(mesh,"set notify.gpio all 10:50")=="OK");
  assert(!mesh._notifications.active()&&levels[9]==LOW&&modes[9]==INPUT);
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(10));
  assert(mesh::pins::tryClaim(Owner::Outputs,mesh::pins::pinMask(9)));
  assert(!mesh.gpioAvailable(9)&&!mesh.gpioAvailable(41));
  assert(command(mesh,"set notify.gpio all 41:50").find("GPIO reserved")!=std::string::npos);
  assert(command(mesh,"set notify.gpio all inherit")=="OK");
  assert(!mesh::pins::held(Owner::Notifications));
  // Inherit retains the old pin in the wire image. It no longer reserves or
  // validates a pad, including after another peripheral has acquired it.
  assert(mesh._notifications.settings().rules[0].pin==10);
  assert(mesh::pins::tryClaim(Owner::Rotary,mesh::pins::pinMask(10)));
  assert(mesh._notifications.validSettings(mesh._notifications.settings()));
  assert(command(mesh,"set notify.gpio all inherit")=="OK");
  assert(command(mesh,"set notify.led all 50")=="OK");
  assert(!mesh::pins::held(Owner::Notifications));
  mesh::pins::release(Owner::Rotary,mesh::pins::pinMask(10));
  assert(command(mesh,"set notify.gpio all 10:off")=="OK");
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(10));
  assert(command(mesh,"set notify.enabled off")=="OK");
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(10));
  assert(command(mesh,"notify.delete all")=="OK");
  assert(!mesh::pins::held(Owner::Notifications));
}
void bootClaimsAndAliases() {
  resetOwners();MyMesh mesh;
  auto& prefs=mesh._notifications.settings();prefs.enabled=1;
  prefs.rules[0].used=1;prefs.rules[0].fields=mesh::notify::FGpio;
  prefs.rules[0].pin=9;assert(mesh::notify::parsePulse("100",prefs.rules[0].gpio));
  const auto before=writes[9];assert(mesh.claimNotificationGpio());
  assert(writes[9]==before&&mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(9));
  // Multiple selectors may intentionally drive one pad. They share one
  // claim, including Arduino aliases of that physical pad.
  prefs.rules[1]=prefs.rules[0];prefs.rules[1].selector.when=mesh::notify::Online;prefs.rules[1].pin=41;
  assert(mesh.claimNotificationGpio());
  assert(mesh::pins::held(Owner::Notifications)==mesh::pins::pinMask(9));
  mesh::pins::release(Owner::Notifications,~UINT64_C(0));
  assert(mesh::pins::tryClaim(Owner::UserGpio,mesh::pins::pinMask(9)));
  assert(!mesh.claimNotificationGpio());
  assert(!mesh::pins::held(Owner::Notifications));
  mesh::pins::release(Owner::UserGpio,~UINT64_C(0));
  board.available[9]=false;assert(!mesh.claimNotificationGpio());
}
void customAdaptersAndLocalCli() {
  resetOwners();MyMesh mesh;
  using namespace mesh::peripherals;
  has_buzzer=has_led=true;
  assert((mesh.capabilities()&(mesh::notify::Sound|mesh::notify::Led))==(mesh::notify::Sound|mesh::notify::Led));
  mesh._prefs.buzzer_quiet=0;mesh.melody("test:d=4,o=5,b=100:c");
  assert(!quiet&&tune=="test:d=4,o=5,b=100:c");
  mesh._ui=&mesh.ui;auto custom=melodies;
  mesh.melody(nullptr);assert(melodies==custom+1&&mesh.ui.melodies==0&&tune.empty());
  has_buzzer=false;mesh.melody("builtin");assert(mesh.ui.melodies==1);
  has_buzzer=true;
  mesh::leds::setEnabled(true);mesh.pulse(mesh::notify::Led,true,-1);assert(led);
  mesh::leds::setEnabled(false);mesh.pulse(mesh::notify::Led,true,-1);assert(!led);
  mesh::leds::setEnabled(true);
  mesh.notifyCustomMessage(false);assert(!last_channel&&last_sound&&last_led);
  mesh._notifications.settings().enabled=1;mesh._notifications.settings().outputs=mesh::notify::Led;
  mesh.notifyCustomMessage(true);assert(last_channel&&!last_sound&&last_led);
  mesh._prefs.buzzer_quiet=1;mesh.notifyCustomMessage(false);assert(quiet);
  custom=melodies;const auto builtin=mesh.ui.melodies;
  char reply[160];assert(mesh.handleAlertCommand("set buzzer on",reply,sizeof(reply)));
  assert(!strcmp(reply,"OK")&&!mesh._prefs.buzzer_quiet&&!quiet);
  // Explicit sound checks use the selected custom output even with message
  // sounds masked off. They do not also start the built-in UI buzzer.
  assert(melodies==custom+1&&mesh.ui.melodies==builtin&&tune.find("SoundOn:")==0);
  assert(mesh._notifications.settings().outputs==mesh::notify::Led);
  assert(mesh.handleAlertCommand("set buzzer on",reply,sizeof(reply)));
  assert(!strcmp(reply,"OK")&&melodies==custom+2&&mesh.ui.melodies==builtin);
  mesh.save_prefs=false;
  assert(mesh.handleAlertCommand("set buzzer off",reply,sizeof(reply)));
  assert(!strncmp(reply,"Error",5)&&!mesh._prefs.buzzer_quiet&&!quiet);
  assert(melodies==custom+2&&mesh.ui.melodies==builtin);
  mesh.save_prefs=true;
  assert(mesh.handleAlertCommand("set buzzer off",reply,sizeof(reply)));
  assert(!strcmp(reply,"OK")&&quiet&&melodies==custom+2);
  mesh.save_prefs=false;
  assert(mesh.handleAlertCommand("set buzzer on",reply,sizeof(reply)));
  assert(!strncmp(reply,"Error",5)&&quiet&&melodies==custom+2);
  mesh.save_prefs=true;mesh._ui=nullptr;
  assert(mesh.handleAlertCommand("set buzzer on",reply,sizeof(reply)));
  assert(!strcmp(reply,"OK")&&!quiet&&melodies==custom+3);
  has_buzzer=false;assert(mesh.handleAlertCommand("get buzzer",reply,sizeof(reply)));
  assert(strstr(reply,"unavailable"));
  mesh.radio.pending=true;auto outputs_before=cli_calls,rotary_before=mesh::ui::rotary_calls;
  assert(mesh.handleLocalControlCommand("set peripheral.buzzer 12",reply,sizeof(reply)));
  assert(strstr(reply,"maintenance pending")&&cli_calls==outputs_before&&mesh::ui::rotary_calls==rotary_before);
  assert(mesh.handleLocalControlCommand("get rotary",reply,sizeof(reply)));
  assert(strstr(reply,"maintenance pending")&&mesh::ui::rotary_calls==rotary_before);
  mesh.radio.pending=false;
  assert(mesh.handleLocalControlCommand("get peripherals",reply,sizeof(reply))&&!strcmp(reply,"> external outputs"));
  assert(mesh.handleLocalControlCommand("get rotary",reply,sizeof(reply))&&!strcmp(reply,"> rotary"));
}
int main() { claimsAndFailedSaves();bootClaimsAndAliases();customAdaptersAndLocalCli(); }
'''


class NotificationGpioClaimsTests(unittest.TestCase):
    def test_saved_gpio_claims_output_adapters_and_maintenance_guard(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        source = (ROOT / "examples/companion_radio/Notifications.cpp").read_text()
        adapters = "\n".join(extract_braced(source, signature) for signature in (
            "uint8_t MyMesh::capabilities(", "bool MyMesh::gpioAvailable(",
            "void MyMesh::pulse(", "void MyMesh::melody(", "void MyMesh::screen(",
            "bool MyMesh::save(", "bool MyMesh::notificationGpioMask(",
            "void MyMesh::releaseNotificationGpio(", "bool MyMesh::claimNotificationGpio(",
            "void MyMesh::notifyCustomMessage(", "bool MyMesh::handleAlertCommand("))
        mesh_source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        start = mesh_source.index("bool MyMesh::handleLocalControlCommand(")
        end = mesh_source.index("#if defined(USE_LR2021)", start)
        adapters += "\n" + mesh_source[start:end] + "return false;\n}\n"
        filesystem = FILESYSTEM.replace("int read_limit=-1", "std::string fail_read_path;\n  int read_limit=-1")
        filesystem = filesystem.replace("  fs->largest_read=", "  if(fs->fail_read_path==name)return 0;\n  fs->largest_read=")
        with tempfile.TemporaryDirectory(prefix="meshcore-notification-gpio-") as temp:
            folder = Path(temp)
            (folder / "helpers").mkdir()
            (folder / "helpers/IdentityStore.h").write_text(filesystem)
            program = folder / "test.cpp"
            program.write_text(HARNESS.replace("@ADAPTERS@", adapters))
            executable = folder / "test"
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-Wno-misleading-indentation", *SANITIZER_FLAGS,
                                    "-DMESH_CUSTOM_PERIPHERALS=1", "-DCOMPANION_FEATURE_NOTIFICATIONS=1", "-DRP2040_PLATFORM=1",
                                    "-I", str(folder), "-I", str(ROOT / "src"),
                                    "-I", str(ROOT / "examples/companion_radio"), str(program),
                                    str(ROOT / "src/helpers/CompanionNotificationPolicy.cpp"),
                                    "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
