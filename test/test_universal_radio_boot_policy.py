"""Run real late-radio regulator/tuning restore without repeating boot side effects."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body
from test_regulator_settings import FILESYSTEM, DISPATCHER, ESP_SYSTEM

ROOT=Path(__file__).resolve().parents[1]

REGULATOR_PROGRAM=r'''
#define main existing_regulator_main
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
#include "test/fixtures/regulator_settings_native.cpp"
#pragma GCC diagnostic pop
#undef main
static RecoveryRecord retained(){
  RecoveryRecord copy{};
  const volatile uint8_t* source=reinterpret_cast<const volatile uint8_t*>(&platformRecoveryRecord());
  auto* destination=reinterpret_cast<uint8_t*>(&copy);
  for(size_t i=0;i<sizeof(copy);++i)destination[i]=source[i];
  return copy;
}
static void sameWindow(const RecoveryRecord& before){
  auto after=retained();
  assert(after.startedAt==before.startedAt&&after.heartbeatAt==before.heartbeatAt);
  assert(after.batteryAt==before.batteryAt&&after.batteryPercent==before.batteryPercent);
  assert(after.mcuCrashes==before.mcuCrashes&&after.radioCrashes==before.radioCrashes);
  assert(after.flags==before.flags&&after.fallbackMask==before.fallbackMask);
}
int main(){
  mesh::MainBoard board;mesh::Radio radio,other;MockFS fs;
  assert(reapplyDeferredRadioRegulator(radio)); // No registered boot context.
  assert(!board.reads&&!board.applies&&!radio.configures);
  for(auto request:{RegulatorMode::Default,RegulatorMode::Dcdc,RegulatorMode::Ldo}){
    for(bool fallback:{false,true}){
      resetGlobals(true);fs=MockFS{};board=mesh::MainBoard{};radio=mesh::Radio{};
      board.modes=0;radio.modes=0;radio.detected=false;
      uint8_t data[12];encode(RegulatorMode::Default,request,fallback?RECOVERY_RADIO:0,data);
      fs.files["/regulator_v1"]={data,data+sizeof(data)};
      mock_now=100;assert(beginRegulatorSettings(board,radio,&fs));
      assert(initialized&&requestedRegulatorMode(false)==request&&board.reads==3);
      assert(!board.applies&&!radio.configures);
      const auto window=retained();const auto files=fs.files;
      const auto reads=board.reads,writes=fs.writes,renames=fs.renames,removes=fs.removes;
      assert(!reapplyDeferredRadioRegulator(radio)); // Still unidentified.
      assert(reapplyDeferredRadioRegulator(other)); // Different radio cannot steal it.
      radio.detected=true;radio.modes=3;radio.locked=true;mock_now+=2000;
      assert(!reapplyDeferredRadioRegulator(radio)); // Admission failure keeps it pending.
      assert(radio_boot_deferred&&radio.configures==1&&!radio.inits);
      sameWindow(window);
      radio.locked=false;
      assert(reapplyDeferredRadioRegulator(radio)&&!radio_boot_deferred);
      const auto selected=request==RegulatorMode::Ldo||fallback?RegulatorMode::Ldo:RegulatorMode::Dcdc;
      assert(radio.boot==selected&&radio.configures==2&&!radio.inits);
      assert(retained().activeDcdcMask==(selected==RegulatorMode::Dcdc?RECOVERY_RADIO:0));
      sameWindow(window);
      radio.initialize(); // Now and only now may native initialization enable the selected mode.
      assert(radio.active==selected);
      assert(reapplyDeferredRadioRegulator(radio)&&radio.configures==2);
      assert(board.reads==reads&&!board.applies&&fs.files==files);
      assert(fs.writes==writes&&fs.renames==renames&&fs.removes==removes);
      sameWindow(window);
    }
  }
  // A detected family without selectable regulators must remain a no-op.
  resetGlobals(true);fs=MockFS{};board=mesh::MainBoard{};radio=mesh::Radio{};
  board.modes=0;radio.modes=0;radio.detected=false;
  assert(beginRegulatorSettings(board,radio,&fs));
  auto window=retained();const auto reads=board.reads;
  radio.detected=true;
  assert(reapplyDeferredRadioRegulator(radio)&&!radio_boot_deferred);
  assert(!radio.configures&&board.reads==reads&&!board.applies);
  sameWindow(window);
}
'''

TUNING_PROGRAM=r'''
#define main existing_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
@TUNING_RADIO@
@CRC@
struct DeferredRadio:TuningRadio {
  bool detected=false,boot_ok=true;
  unsigned boot_attempts=0;
  mesh::RadioProfiles* profiles()override{return detected?&p:nullptr;}
  const mesh::RadioProfiles* profiles()const override{return detected?&p:nullptr;}
  mesh::RadioSwitchCapabilities switchTuningCapabilities()const override{
    return detected?caps:mesh::RadioSwitchCapabilities{};
  }
  bool configureBootSwitchTuning(const mesh::RadioSwitchTuning& value)override{
    ++boot_attempts;return boot_ok&&TuningRadio::configureBootSwitchTuning(value);
  }
};
static std::vector<uint8_t> image(){
  std::vector<uint8_t> data(24,0);data[0]='R';data[1]='2';data[2]=1;
  data[3]=mesh::RadioSwitchTuning::Buffer;data[4]=4;crc(data);return data;
}
static void untouched(const MemoryFS& fs,const std::map<std::string,std::vector<uint8_t>>& saved){
  assert(fs.files==saved&&!fs.writes&&!fs.renames&&!fs.removes);
}
int main(){
  DeferredRadio radio,other;MemoryFS fs;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(!radio.boot_attempts&&!fs.reads);
  fs.files["/radio_fast"]=image();auto saved=fs.files;
  mesh::RadioProfileCLI::configureBootTuning(&fs,radio);
  assert(!radio.boot_attempts&&!fs.reads);
  assert(!mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(other)&&!fs.reads);
  radio.detected=true;radio.boot_ok=false;
  assert(!mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(radio.boot_attempts==1&&!radio.boot_configurations&&fs.reads);
  untouched(fs,saved);
  radio.boot_ok=true;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(radio.boot_attempts==2&&radio.boot_configurations==1);
  assert(radio.tuning.flags==mesh::RadioSwitchTuning::Buffer&&radio.tuning.spi_mhz==4);
  auto reads=fs.reads;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(fs.reads==reads&&radio.boot_attempts==2&&!radio.attempts&&!radio.persists);
  untouched(fs,saved);

  // Only the verified backup is usable: apply it without renaming/removing either file.
  fs=MemoryFS{};radio=DeferredRadio{};
  fs.files["/radio_fast"]=image();fs.files["/radio_fast"][23]^=1;
  fs.files["/radio_fast.bak"]=image();saved=fs.files;
  mesh::RadioProfileCLI::configureBootTuning(&fs,radio);radio.detected=true;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio));
  assert(radio.tuning.spi_mhz==4&&radio.boot_attempts==1);untouched(fs,saved);

  // An unreadable saved image must be retried before the physical driver starts.
  fs=MemoryFS{};radio=DeferredRadio{};fs.files["/radio_fast"]=image();saved=fs.files;
  mesh::RadioProfileCLI::configureBootTuning(&fs,radio);radio.detected=true;fs.fail_read_open=true;
  assert(!mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio)&&!radio.boot_attempts);
  untouched(fs,saved);fs.fail_read_open=false;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio)&&radio.tuning.spi_mhz==4);
  untouched(fs,saved);

  // No saved request and no registered filesystem both leave the driver's defaults intact.
  fs=MemoryFS{};radio=DeferredRadio{};
  mesh::RadioProfileCLI::configureBootTuning(&fs,radio);radio.detected=true;
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio)&&!radio.boot_attempts);
  radio=DeferredRadio{};mesh::RadioProfileCLI::configureBootTuning(nullptr,radio);
  assert(mesh::RadioProfileCLI::reapplyDeferredBootTuning(radio)&&!radio.boot_attempts);
}
'''


class UniversalRadioBootPolicyTest(unittest.TestCase):
    def compile_run(self, folder, source, includes, extra=(), flags=(), strict=True):
        compiler=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:self.skipTest("host C++ compiler required")
        cpp=folder/"test.cpp";cpp.write_text(source)
        exe=folder/("test.exe" if os.name=="nt" else "test")
        sanitize=(["-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie"]
                  if sys.platform.startswith("linux") else [])
        built=subprocess.run([compiler,"-std=c++17","-O1","-Wall","-Wextra",*(["-Werror"] if strict else []),
            "-DMESH_RADIO_AUTO_FAMILY=1",*sanitize,*flags,
            *["-I"+str(path) for path in includes],str(cpp),*[str(path) for path in extra],"-o",str(exe)],
            capture_output=True,text=True,timeout=60)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        ran=subprocess.run([str(exe)],capture_output=True,text=True,timeout=20)
        self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

    def test_real_regulator_restore_keeps_saved_request_fallback_and_original_recovery_window(self):
        filesystem=FILESYSTEM.replace("unsigned writes=0,renames=0;","unsigned writes=0,renames=0,removes=0;")
        filesystem=filesystem.replace("bool remove(const char* name) { return files.erase(name)!=0; }",
                                      "bool remove(const char* name) { ++removes;return files.erase(name)!=0; }")
        dispatcher=DISPATCHER.replace("struct Radio {","struct Radio {\n  bool detected=true;\n  void* profiles(){return detected?this:nullptr;}")
        headers={"helpers/IdentityStore.h":filesystem,"Dispatcher.h":dispatcher,
            "Arduino.h":"#pragma once\n#include <cstdint>\nextern uint32_t mock_now;\ninline uint32_t millis(){return mock_now;}\ninline void delay(unsigned ms){mock_now+=ms;}\n",
            "esp_system.h":ESP_SYSTEM,"esp_attr.h":"#pragma once\n#define RTC_NOINIT_ATTR\n",
            "soc/soc_caps.h":"#pragma once\n#define SOC_RTC_SLOW_MEM_SUPPORTED 1\n#define SOC_RTC_FAST_MEM_SUPPORTED 0\n"}
        with tempfile.TemporaryDirectory(prefix="universal-regulator-boot-") as directory:
            folder=Path(directory)
            for name,text in headers.items():
                path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            self.compile_run(folder,REGULATOR_PROGRAM,[folder,ROOT,ROOT/"src"],flags=["-DESP32=1"])

    def test_real_tuning_restore_handles_deferred_admission_and_backup_without_writes(self):
        fixture=(ROOT/"test/fixtures/radio_profiles/switch_tuning_test.cpp").read_text()
        program=TUNING_PROGRAM.replace("@TUNING_RADIO@",body(fixture,"struct TuningRadio : Radio")+";")
        program=program.replace("@CRC@",body(fixture,"static void crc("))
        fs=(ROOT/"test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
        fs=fs.replace("class MemoryFS {\n public:","class MemoryFS {\n public:\n  unsigned reads=0,writes=0,removes=0,renames=0;")
        fs=fs.replace("bool remove(const char* path) {", "bool remove(const char* path) { ++removes;")
        fs=fs.replace("bool rename(const char* from, const char* to) {", "bool rename(const char* from, const char* to) { ++renames;")
        fs=fs.replace("if (!fs_) return -1;", "if (!fs_) return -1;\n  ++fs_->reads;")
        fs=fs.replace("if (!fs_ || fs_->fail_write) return 0;", "if (!fs_ || fs_->fail_write) return 0;\n  ++fs_->writes;")
        with tempfile.TemporaryDirectory(prefix="universal-tuning-boot-") as directory:
            folder=Path(directory);header=folder/"helpers/IdentityStore.h"
            header.parent.mkdir();header.write_text(fs)
            self.compile_run(folder,program,[folder,ROOT,ROOT/"test/fixtures/radio_profiles/mocks",
                ROOT/"test/mocks",ROOT/"src"],extra=[ROOT/"src/helpers/RadioProfileCLI.cpp"],strict=False)


if __name__=="__main__":
    unittest.main()
