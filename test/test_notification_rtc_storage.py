"""Exercise notification controllers with local and RTC SLOW settings storage."""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Reuse the existing fault-injected filesystem, including production file I/O.
_spec = importlib.util.spec_from_file_location(
    "notification_file_fixture", Path(__file__).with_name("test_notification_settings.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)

PROGRAM = r'''
#include <cassert>
#include <cstdint>
#include <string>
#include <type_traits>
#include "NotificationSettingsFile.h"
using namespace mesh::notify;
#if MESH_NOTIFY_RTC_SETTINGS
#define SETTINGS_ARG(settings) ,settings
static_assert(!std::is_constructible<Controller,Sink&,Store&>::value,
              "RTC settings ownership must be explicit");
static_assert(std::is_constructible<Controller,Sink&,Store&,Settings&>::value,
              "Controllers must accept independent settings banks");
#else
#define SETTINGS_ARG(settings)
static_assert(std::is_constructible<Controller,Sink&,Store&>::value,
              "Keep the ordinary Controller constructor unchanged");
#endif

static void defaults(const Settings& settings) {
  assert(settings.enabled==0 && settings.outputs==31 && MAX_RULES==12);
  for(const auto& rule:settings.rules) {
    assert(rule.selector.kind==All && rule.selector.when==Any);
    for(auto key:rule.selector.key)assert(key==0);
    for(const auto* pulse:{&rule.vibration,&rule.led,&rule.gpio}) {
      assert(pulse->count==0);
      for(auto ms:pulse->ms)assert(ms==0);
    }
    for(auto ch:rule.sound)assert(ch==0);
    assert(rule.fields==0 && rule.repeat==1 && rule.gap==500);
    assert(rule.pin==-1 && rule.screen==-1 && rule.stop==Button);
    assert(rule.used==0 && rule.remote==0);
  }
}
struct OutputSink:Sink {
  unsigned pulses=0;
  bool vibration=false,led=false,gpio=false;
  int8_t screen_mode=-1;
  std::string tune;
  uint8_t capabilities()const override{return 31;}
  bool gpioAvailable(uint8_t pin)const override{return pin==22;}
  void pulse(Output output,bool on,int8_t)override{
    ++pulses;
    if(output==Vibration)vibration=on;
    if(output==Led)led=on;
    if(output==Gpio)gpio=on;
  }
  void melody(const char* text)override{tune=text?text:"";}
  void screen(int8_t mode)override{screen_mode=mode;}
};
struct FileStore:Store {
  FakeFS fs;
  unsigned writes=0;
  bool save(const Settings& settings)override{
    ++writes;return writeSettings(&fs,settings);
  }
};

// These constructors precede the storage definition below. Its initializer
// must be constant, so a later global initializer cannot erase this mutation.
OutputSink early_sink;
FileStore early_store;
Controller early_controller(early_sink,early_store SETTINGS_ARG(rtcSettings()));
struct EarlyMutation {
  EarlyMutation(){defaults(early_controller.settings());early_controller.settings().outputs=19;}
} early_mutation;

#include "helpers/CompanionNotificationPolicy.cpp"

struct GuardedController {
  uint64_t before[4]={17,29,43,61};
  Controller controller;
  uint64_t after[4]={71,89,103,127};
  GuardedController(Sink& sink,Store& store SETTINGS_ARG(Settings& settings))
    :controller(sink,store SETTINGS_ARG(settings)){}
  void intact()const{
    const uint64_t first[]={17,29,43,61},last[]={71,89,103,127};
    assert(!memcmp(before,first,sizeof(first)) && !memcmp(after,last,sizeof(last)));
  }
};
static std::string command(Controller& controller,const std::string& text) {
  char reply[256]={};
  assert(controller.handle(text.c_str(),reply,sizeof(reply),100,false));
  return reply;
}
static std::string target(unsigned index) {
  char result[80];snprintf(result,sizeof(result),"contact:%064x",index+1);
  return result;
}
int main() {
  assert(early_controller.settings().outputs==19);
  resetSettings(early_controller.settings());
  OutputSink a_sink,b_sink;
  FileStore a_store,b_store;
  Settings b_settings;defaults(b_settings);
  GuardedController a(a_sink,a_store SETTINGS_ARG(rtcSettings()));
  GuardedController b(b_sink,b_store SETTINGS_ARG(b_settings));
  defaults(a.controller.settings());defaults(b.controller.settings());
  auto* address=&a.controller.settings();
#if MESH_NOTIFY_RTC_SETTINGS
  assert(address!=&b.controller.settings() && address==&early_controller.settings());
  assert(&b.controller.settings()==&b_settings);
  assert(sizeof(Controller)<sizeof(Settings));
  uintptr_t start=reinterpret_cast<uintptr_t>(&a.controller);
  uintptr_t end=start+sizeof(a.controller);
  assert(reinterpret_cast<uintptr_t>(address)<start || reinterpret_cast<uintptr_t>(address)>=end);
#else
  assert(address!=&b.controller.settings() && address!=&early_controller.settings());
  assert(sizeof(Controller)>sizeof(Settings));
#endif

  // Fill every rule and every output without reducing the published capacity.
  for(unsigned index=0;index<MAX_RULES;++index) {
    const std::string selector=target(index);
    for(const auto& setting:{"vibration ","led ","sound ","gpio ","screen ","repeat ","gap "}) {
      const std::string field=setting;
      const char* value=field=="vibration "||field=="led "?"50,50":
        field=="sound "?"n:d=4,o=5,b=120:c":field=="gpio "?"22:50,50":
        field=="screen "?"on":field=="repeat "?"2":"250";
      assert(command(a.controller,"set notify."+field+selector+" "+value)=="OK");
    }
  }
  assert(a.controller.validSettings(a.controller.settings()));
  assert(a_store.writes==MAX_RULES*7 && b_store.writes==0);
  assert(command(a.controller,"set notify.vibration "+target(MAX_RULES)+" 50")==
         "Error: notification rule limit reached");
  assert(command(a.controller,"get notify.rules")=="> slots: 0 1 2 3 4 5 6 7 8 9 10 11");
  defaults(b.controller.settings());
  b.controller.settings()=a.controller.settings();
  a.intact();b.intact();

  // Settings sharing must not share playback or the Sink/Store references.
  uint8_t contact[32]={};contact[31]=1;
  assert(a.controller.message(contact,nullptr,false,100));
  assert(a_sink.vibration && a_sink.led && a_sink.gpio && !a_sink.tune.empty());
  assert(!b.controller.active() && b_sink.pulses==0);
  assert(b.controller.message(contact,nullptr,false,100));
  a.controller.stop();
  assert(!a.controller.active() && b.controller.active() && b_sink.vibration);
  b.controller.stop();
  assert(command(b.controller,"set notify.sound off")=="OK");
  assert(b_store.writes==1 && a_store.writes==MAX_RULES*7);
  assert((a.controller.settings().outputs&Sound) && !(b.controller.settings().outputs&Sound));

  // Failed commands keep both storage modes and the persisted record intact.
  const uint32_t before=checksum(a.controller.settings());
  const auto record=a_store.fs.files.at("/notify_prefs");
  a_store.fs.fail_write=true;
  assert(command(a.controller,"set notify.enabled off")=="Error: save failed");
  assert(command(a.controller,"set notify.gap "+target(0)+" 800")=="Error: save failed");
  assert(checksum(a.controller.settings())==before);
  assert(a_store.fs.files.at("/notify_prefs")==record);
  a_store.fs.fail_write=false;
  assert(command(a.controller,"set notify.enabled on")=="OK");
  assert(verifySettingsFile(&a_store.fs,"/notify_prefs",&a.controller.settings()));
  const uint32_t persisted=checksum(a.controller.settings());
  a.controller.stop();b.controller.stop();
  const uint32_t independent=checksum(b.controller.settings());

#if MESH_NOTIFY_RTC_SETTINGS
  // Emulate a reboot: ordinary BSS clears while RTC bytes may be arbitrary.
  memset(static_cast<void*>(&rtc_settings),0xa5,sizeof(rtc_settings));
  rtc_settings_initialized=false;
#endif
  OutputSink restarted_sink;
  GuardedController restarted(restarted_sink,a_store SETTINGS_ARG(rtcSettings()));
  defaults(restarted.controller.settings());
  assert(readSettings(&a_store.fs,"/notify_prefs",restarted.controller.settings()));
  assert(restarted.controller.validSettings(restarted.controller.settings()));
  assert(checksum(restarted.controller.settings())==persisted);
#if MESH_NOTIFY_RTC_SETTINGS
  assert(address==&restarted.controller.settings());
  assert(checksum(rtcSettings())==persisted);
  Settings later_settings;
  GuardedController later(b_sink,b_store,later_settings);
  defaults(later.controller.settings());
  later.intact();
#else
  assert(checksum(a.controller.settings())==persisted);
#endif
  assert(checksum(b.controller.settings())==independent);
  a.intact();b.intact();restarted.intact();
  printf("settings=%zu controller=%zu\n",sizeof(Settings),sizeof(Controller));
}
'''


class NotificationRtcStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temporary.name)
        (cls.folder / "helpers").mkdir()
        (cls.folder / "helpers/IdentityStore.h").write_text(_fixture.STUB)
        (cls.folder / "sdkconfig.h").write_text(
            "#ifndef CONFIG_IDF_TARGET_ESP32\n#define CONFIG_IDF_TARGET_ESP32 1\n#endif\n")
        (cls.folder / "esp_attr.h").write_text(
            '#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow")))\n')
        (cls.folder / "test.cpp").write_text(PROGRAM)
        cls.compiler = shutil.which("g++")
        if not cls.compiler:
            raise unittest.SkipTest("g++ unavailable")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def flags(self, definitions):
        return [self.compiler, "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wno-misleading-indentation", *["-D" + item for item in definitions],
                "-I" + str(self.folder), "-I" + str(ROOT / "src"),
                "-I" + str(ROOT / "examples/companion_radio")]

    def test_actual_controller_storage_playback_and_file_roundtrip(self):
        for enabled in (0, 1):
            with self.subTest(rtc=enabled):
                definitions = ["ESP32_PLATFORM", "TBEAM_SX1262", "MESH_RADIO_AUTO_FAMILY=1",
                               "MESH_NOTIFY_RTC_SETTINGS=" + str(enabled)]
                binary = self.folder / ("storage" + str(enabled) + (".exe" if os.name == "nt" else ""))
                sanitizer = [] if os.name == "nt" else [
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                subprocess.run([*self.flags(definitions), *sanitizer,
                                str(self.folder / "test.cpp"), "-o", str(binary)], check=True)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("settings=", result.stdout)
                objdump = shutil.which("objdump")
                if not objdump:
                    candidate = Path(self.compiler).with_name("objdump" + (".exe" if os.name == "nt" else ""))
                    if candidate.exists():
                        objdump = str(candidate)
                if objdump:
                    symbols = subprocess.run([objdump, "-t", str(binary)], check=True,
                                             capture_output=True, text=True).stdout
                    lines = [line for line in symbols.splitlines() if "rtc_settingsE" in line]
                    self.assertEqual(len(lines), enabled, lines)
                    for line in lines:
                        if os.name == "nt":
                            sections = subprocess.run([objdump, "-h", str(binary)], check=True,
                                                      capture_output=True, text=True).stdout
                            names = {int(index) + 1: name for index, name in
                                     re.findall(r"^\s*(\d+)\s+(\S+)\s+[0-9a-f]+\s", sections, re.M)}
                            section = re.search(r"\(sec\s+(\d+)\)", line)
                            self.assertIsNotNone(section, line)
                            self.assertEqual(names[int(section.group(1))], ".rtc.force_slow")
                        else:
                            self.assertIn(".rtc.force_slow", line)

    def test_opt_in_is_rejected_on_unsupported_hardware(self):
        source = self.folder / "guard.cpp"
        source.write_text('#include "helpers/CompanionNotificationPolicy.h"\n')
        base = ["MESH_NOTIFY_RTC_SETTINGS=1", "ESP32_PLATFORM", "TBEAM_SX1262",
                "MESH_RADIO_AUTO_FAMILY=1"]
        invalid = [["MESH_NOTIFY_RTC_SETTINGS=2"],
                   [item for item in base if item != "ESP32_PLATFORM"],
                   [item for item in base if item != "TBEAM_SX1262"],
                   [item for item in base if item != "MESH_RADIO_AUTO_FAMILY=1"],
                   base + ["CONFIG_IDF_TARGET_ESP32=0"]]
        for definitions in invalid:
            with self.subTest(definitions=definitions):
                result = subprocess.run([*self.flags(definitions), "-fsyntax-only", str(source)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("error:", result.stderr)
        # Ordinary targets continue to compile without any ESP32 SDK headers.
        subprocess.run([*self.flags([]), "-fsyntax-only", str(source)], check=True)


if __name__ == "__main__":
    unittest.main()
