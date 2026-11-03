"""Execute LR2021 trim persistence, native commands and packet-offset capture."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body, _uncommented
from test_display_power import FS_STUB


ROOT = Path(__file__).resolve().parents[1]
RADIO = ROOT / "src/helpers/radiolib"


def method(source, signature):
    return body(source, signature)


POLICY = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <helpers/radiolib/LR2021CalibrationPolicy.h>
using namespace mesh::lr2021;
int main() {
  XoscTrimSelection selection;
  for (unsigned a=0;a<=47;++a) for (unsigned b=0;b<=47;++b) {
    XoscTrimSelection expected{true,uint8_t(a),uint8_t(b)};
    uint8_t bytes[XoscTrimRecordSize];encodeXoscTrim(expected,bytes);
    assert(decodeXoscTrim(bytes,sizeof(bytes),selection));
    assert(sameXoscTrim(expected,selection));
    for (unsigned i=0;i<sizeof(bytes);++i) for(unsigned bit=0;bit<8;++bit) {
      bytes[i]^=1U<<bit;selection={true,17,19};
      assert(!decodeXoscTrim(bytes,sizeof(bytes),selection));
      assert(selection.enabled && selection.a==17 && selection.b==19);
      bytes[i]^=1U<<bit;
    }
  }
  for(const char* value:{"0","47","03","0,47","47,0","default"}) {
    assert(parseXoscTrim(value,selection));
    if(!strcmp(value,"default")) assert(!selection.enabled);
    else assert(selection.enabled);
  }
  for(const char* invalid:{"","48","256","-1","+3","3,48","3,",",3",
      "3,4,5","3.5","3,4 ","3, 4","default ","Default","99999999999999999"}) {
    selection={true,17,19};
    assert(!parseXoscTrim(invalid,selection));
    assert(selection.enabled && selection.a==17 && selection.b==19);
  }
  assert(!parseXoscTrim(nullptr,selection));
  XoscTrimSelection native;uint8_t bytes[XoscTrimRecordSize];encodeXoscTrim(native,bytes);
  assert(decodeXoscTrim(bytes,sizeof(bytes),selection) && !selection.enabled);
  bytes[4]=1;bytes[7]^=1;
  assert(!decodeXoscTrim(bytes,sizeof(bytes),selection)); // no hidden default pair
  const uint8_t samples[][3]={{0,0,0},{0,0,1},{0xff,0xff,0xff},
      {0x7f,0xff,0xff},{0x80,0,0},{0xff,0xfc,0x18},{0,3,0xe8}};
  const int32_t expected[]={0,1,-1,8388607,-8388608,-1000,1000};
  for(unsigned i=0;i<7;++i) assert(decodeFrequencyErrorHz(samples[i])==expected[i]);
}
'''

DRIVER = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include <helpers/radiolib/LR2021CalibrationPolicy.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_UNSUPPORTED -25
#define RADIOLIB_ERR_PACKET_TOO_SHORT -4
#define RADIOLIB_LR2021_STANDBY_RC 0
#define RADIOLIB_LR2021_CMD_SET_XOSC_CP_TRIM 0x0131
#define RADIOLIB_LR2021_CMD_GET_LORA_PACKET_STATUS 0x022a
#define RADIOLIB_LR2021_PACKET_TYPE_NONE 0
#define RADIOLIB_LR2021_PACKET_TYPE_LORA 1
struct Hal {uint32_t now=1000;uint32_t millis(){return now;}};
struct Module {Hal clock;Hal* hal=&clock;};
class LR2021 {
public:
  Module storage;Module* mod=&storage;
  float freqMHz=910.525f;
  int standby_result=0,command_result=0,read_result=0,type_result=0,fifo_result=0;
  uint8_t modem=1,trim_a=23,trim_b=25;
  uint16_t fifo=4;
  int trim_commands=0,status_commands=0,reads=0,standbys=0,clears=0;
  uint8_t raw_offset[3]={0xff,0xfc,0x18};
  std::vector<int> sequence;
  int16_t standby(uint8_t mode) {assert(mode==0);++standbys;sequence.push_back(1);return standby_result;}
  int16_t SPIcommand(uint16_t command,bool write,uint8_t* data,size_t length) {
    if(command==0x0131) {
      assert(write && length==2);++trim_commands;sequence.push_back(2);
      // A failed command can still change the chip. Wrapper rollback is required.
      trim_a=data[0];trim_b=data[1];
    } else {
      assert(command==0x022a && !write && length==9);++status_commands;sequence.push_back(3);
      memcpy(data+6,raw_offset,3);
    }
    return command_result;
  }
  int16_t getPacketType(uint8_t* type) {*type=modem;return type_result;}
  int16_t getRxFifoLevel(uint16_t* size) {*size=fifo;return fifo_result;}
  size_t getPacketLength() {return 4;}
  int16_t clearRxFifo() {++clears;fifo=0;return 0;}
  int16_t readData(uint8_t*,size_t) {++reads;sequence.push_back(4);return read_result;}
};
class CustomLR2021:public LR2021 {
  mesh::lr2021::XoscTrimSelection _xosc_trim;
  int32_t _last_frequency_error_hz=0;
  float _last_frequency_error_mhz=0;
  uint32_t _last_frequency_error_at=0;
  bool _last_frequency_error_valid=false;
public:
@METHODS@
};
int main() {
  CustomLR2021 radio;
  uint8_t a=1,b=2;
  assert(!radio.getXoscTrim(a,b) && a==1 && b==2);
#if defined(LR2021_TCXO_VOLTAGE) && LR2021_TCXO_VOLTAGE == 0
  assert(radio.supportsXoscTrim());
  assert(radio.restoreXoscTrim()==0 && radio.trim_commands==0);
  assert(!radio.configureBootXoscTrim(true,48,0));
  assert(radio.configureBootXoscTrim(true,3,4) && radio.trim_commands==0);
  assert(radio.getXoscTrim(a,b) && a==3 && b==4);
  assert(radio.restoreXoscTrim()==0 && radio.trim_a==3 && radio.trim_b==4);
  assert(radio.sequence[0]==1 && radio.sequence[1]==2);
  radio.standby_result=-71;
  assert(radio.applyXoscTrim(8,9)==-71 && radio.trim_commands==1);
  assert(radio.getXoscTrim(a,b) && a==3 && b==4);
  radio.standby_result=0;radio.command_result=-72;
  assert(radio.applyXoscTrim(8,9)==-72 && radio.trim_commands==2);
  assert(radio.getXoscTrim(a,b) && a==3 && b==4);
  radio.command_result=0;
  assert(radio.restoreXoscTrim()==0 && radio.trim_a==3 && radio.trim_b==4);
  radio.clearXoscTrim();const int before=radio.trim_commands;
  assert(!radio.getXoscTrim(a,b) && radio.restoreXoscTrim()==0 && radio.trim_commands==before);
#else
  assert(!radio.supportsXoscTrim());
  assert(!radio.configureBootXoscTrim(true,3,4));
  assert(radio.applyXoscTrim(3,4)==RADIOLIB_ERR_UNSUPPORTED && radio.trim_commands==0);
#endif
  char result[160];radio.formatFrequencyError(result,sizeof(result));
  assert(strstr(result,"unavailable"));
  uint8_t packet[4];radio.sequence.clear();
  assert(radio.readData(packet,4)==0);
  assert(radio.sequence.size()==2 && radio.sequence[0]==3 && radio.sequence[1]==4);
  radio.mod->hal->now+=2000;radio.freqMHz=915;
  radio.formatFrequencyError(result,sizeof(result));
  assert(strstr(result,"-1000 Hz") && strstr(result,"freq=910.525") && strstr(result,"age=2s"));
  radio.raw_offset[0]=0;radio.raw_offset[1]=0;radio.raw_offset[2]=0;
  radio.read_result=-7;
  assert(radio.readData(packet,4)==-7);
  radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"-1000 Hz"));
  radio.read_result=0;radio.command_result=-72;
  assert(radio.readData(packet,4)==0);
  radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"-1000 Hz"));
  radio.command_result=0;radio.modem=2;
  const int status_before=radio.status_commands;
  assert(radio.readData(packet,4)==0 && radio.status_commands==status_before);
  radio.modem=1;radio.fifo=3;
  assert(radio.readData(packet,4)==RADIOLIB_ERR_PACKET_TOO_SHORT && radio.clears==1);
  radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"-1000 Hz"));
  radio.fifo=4;
  assert(radio.readData(packet,4)==0);
  radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"+0 Hz") && strstr(result,"freq=915.000000"));
}
'''

WRAPPER = r'''
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <helpers/radiolib/LR2021CalibrationPolicy.h>
namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
inline uint32_t test_now=100;
inline uint32_t millis(){return test_now;}
class CustomLR2021Wrapper {
public:
  bool supported=true,selected=false;
  uint8_t a=0,b=0;
  unsigned calls=0,boot_calls=0,cancels=0;
  mesh::RadioParamApplyResult result=mesh::RadioParamApplyResult::APPLIED;
  bool supportsXoscTrim() const {return supported;}
  void cancelXoscTrimApply(){++cancels;}
  bool getXoscTrim(uint8_t& first,uint8_t& second) const {
    if(!selected)return false;
    first=a;second=b;return true;
  }
  bool configureBootXoscTrim(bool enabled,uint8_t first,uint8_t second) {
    ++boot_calls;selected=enabled;a=first;b=second;return true;
  }
  mesh::RadioParamApplyResult trySetXoscTrim(bool enabled,uint8_t first,uint8_t second) {
    ++calls;
    if(result==mesh::RadioParamApplyResult::APPLIED){selected=enabled;a=first;b=second;}
    return result;
  }
  void formatFrequencyError(char* out,size_t size) const {snprintf(out,size,"> +125 Hz; age=1s");}
};
'''

SETTINGS = r'''
#include <cassert>
#include <cstring>
#include <helpers/radiolib/LR2021CalibrationSettings.h>
#include <helpers/radiolib/CustomLR2021Wrapper.h>
using namespace mesh::lr2021;
char reply[160];
static bool command(CustomLR2021Wrapper& radio,const char* text){return handleLR2021CalibrationCommand(text,reply,sizeof(reply),radio);}
int main() {
  FILESYSTEM fs;CustomLR2021Wrapper radio;
  assert(!loadLR2021CalibrationSettings(&fs,radio) && !radio.selected && !radio.calls);
  assert(!command(radio,"get freq") && !command(radio,"get radio.xtrimmed"));
  assert(command(radio,"get radio.xtrim") && !strcmp(reply,"> default"));
  assert(command(radio,"get radio.freqerror") && strstr(reply,"+125 Hz"));
  for(const char* bad:{"48","-1","1,48","1.5","1,2 extra",""}) {
    const std::string input=std::string("set radio.xtrim ")+bad;
    assert(command(radio,input.c_str()) && strstr(reply,"Error:"));
    assert(!radio.calls && fs.files.empty());
  }
  assert(command(radio,"set radio.xtrim 3,4") && strstr(reply,"applying in 2s"));
  assert(!radio.calls && !radio.selected && fs.files.count("/radio_xtrim"));
  test_now+=1999;loopLR2021CalibrationSettings(radio);assert(!radio.calls && !radio.selected);
  test_now+=1;loopLR2021CalibrationSettings(radio);
  assert(radio.selected && radio.a==3 && radio.b==4 && radio.calls==1);
  const auto original=fs.files["/radio_xtrim"];
  const unsigned calls=radio.calls;
  assert(command(radio,"set radio.xtrim 3,4") && strstr(reply,"unchanged") && radio.calls==calls);
  for(int failure=0;failure<5;++failure) {
    fs.fail_write=failure==0;fs.corrupt_write=failure==1;fs.fail_temp_read=failure==2;
    fs.fail_publish=failure==3;fs.fail_open_write=failure==4;
    assert(command(radio,"set radio.xtrim 8") && strstr(reply,"save failed"));
    assert(radio.calls==calls && radio.a==3 && fs.files["/radio_xtrim"]==original);
  }
  fs.fail_write=fs.corrupt_write=fs.fail_temp_read=fs.fail_publish=fs.fail_open_write=false;
  radio.result=mesh::RadioParamApplyResult::BUSY;
  assert(command(radio,"set radio.xtrim 8") && strstr(reply,"applying in 2s"));
  assert(radio.a==3 && radio.b==4);
  assert(command(radio,"get radio.xtrim") && !strcmp(reply,"> 8,8 (pending)"));
  const unsigned busy_calls=radio.calls;
  test_now+=2000;
  loopLR2021CalibrationSettings(radio);assert(radio.calls==busy_calls+1);
  radio.result=mesh::RadioParamApplyResult::APPLIED;
  loopLR2021CalibrationSettings(radio);
  assert(radio.a==8 && radio.b==8);
  assert(command(radio,"get radio.xtrim") && !strcmp(reply,"> 8,8"));
  CustomLR2021Wrapper reboot;
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.selected && reboot.a==8 && reboot.b==8 && !reboot.calls);
  radio.result=mesh::RadioParamApplyResult::FAILED;
  assert(command(radio,"set radio.xtrim 1,2") && strstr(reply,"applying in 2s"));
  test_now+=2000;loopLR2021CalibrationSettings(radio);
  assert(command(radio,"get radio.xtrim") && strstr(reply,"> 8,8 (failed"));
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.a==8 && reboot.b==8);
  radio.result=mesh::RadioParamApplyResult::BUSY;
  assert(command(radio,"set radio.xtrim 4") && strstr(reply,"applying in 2s"));
  test_now+=2000;loopLR2021CalibrationSettings(radio);
  test_now+=30000;const unsigned timeout_calls=radio.calls,timeout_cancels=radio.cancels;
  loopLR2021CalibrationSettings(radio);assert(radio.calls==timeout_calls+1);
  assert(radio.cancels==timeout_cancels+1);
  assert(command(radio,"get radio.xtrim") && strstr(reply,"> 8,8 (failed"));
  assert(command(radio,"set radio.xtrim 4") && strstr(reply,"applying in 2s"));
  test_now+=2000;loopLR2021CalibrationSettings(radio);
  test_now+=29999;
  loopLR2021CalibrationSettings(radio);
  assert(command(radio,"get radio.xtrim") && !strcmp(reply,"> 4,4 (pending)"));
  assert(command(radio,"set radio.xtrim 5") && strstr(reply,"applying in 2s"));
  test_now+=2000;loopLR2021CalibrationSettings(radio);
  test_now+=30000;const unsigned admitted_calls=radio.calls;
  radio.result=mesh::RadioParamApplyResult::APPLIED;
  loopLR2021CalibrationSettings(radio);assert(radio.calls==admitted_calls+1);
  assert(radio.a==5 && radio.b==5);
  assert(command(radio,"get radio.xtrim") && !strcmp(reply,"> 5,5"));
  assert(command(radio,"set radio.xtrim default") && strstr(reply,"applying in 2s") && radio.selected);
  test_now+=2000;loopLR2021CalibrationSettings(radio);assert(!radio.selected);
  assert(loadLR2021CalibrationSettings(&fs,reboot) && !reboot.selected);
  fs.files["/radio_xtrim.bak"]=original;fs.files["/radio_xtrim"]={0,1,2};
  assert(loadLR2021CalibrationSettings(&fs,reboot) && reboot.a==3 && reboot.b==4);
  fs.files["/radio_xtrim"][0]^=1;
  assert(!loadLR2021CalibrationSettings(&fs,reboot) && !reboot.selected);
  CustomLR2021Wrapper tcxo;tcxo.supported=false;
  const auto before=fs.files;
  assert(!loadLR2021CalibrationSettings(&fs,tcxo));
  assert(command(tcxo,"set radio.xtrim 3") && strstr(reply,"TCXO") && fs.files==before && !tcxo.calls);
}
'''


class LR2021CalibrationTests(unittest.TestCase):
    def compile_run(self, source, flags=(), stubs=None, production=None):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for path, content in (stubs or {}).items():
                target = work / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            test = work / "test.cpp"
            test.write_text(source)
            binary = work / "test"
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(work),
                       "-I", str(ROOT / "src"), *flags, str(test)]
            if production:
                command.append(str(production))
            built = subprocess.run([*command, "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_policy(self):
        self.compile_run(POLICY)

    def test_driver_commands_and_measurements(self):
        source = (RADIO / "CustomLR2021.h").read_text()
        signatures = ["bool supportsXoscTrim() const", "bool configureBootXoscTrim(bool enabled, uint8_t a, uint8_t b)",
                      "bool getXoscTrim(uint8_t& a, uint8_t& b) const", "void clearXoscTrim()",
                      "int16_t applyXoscTrim(uint8_t a, uint8_t b)", "int16_t restoreXoscTrim()",
                      "int16_t readPacketFrequencyErrorHz(int32_t& value)",
                      "void formatFrequencyError(char* reply, size_t capacity) const",
                      "int16_t readData(uint8_t* data, size_t len) override"]
        methods = "\n".join(method(source, signature).replace(" override", "") for signature in signatures)
        for flags in [("-DLR2021_TCXO_VOLTAGE=0",), ("-DLR2021_TCXO_VOLTAGE=1",), ()]:
            with self.subTest(flags=flags):
                self.compile_run(DRIVER.replace("@METHODS@", methods), flags)

    def test_checked_settings_and_pending_apply(self):
        stubs = {"helpers/IdentityStore.h": FS_STUB.replace("/display_prefs", "/radio_xtrim"),
                 "helpers/radiolib/CustomLR2021Wrapper.h": WRAPPER}
        # The source uses a quoted include; isolate it beside its mocked boundary.
        source = (RADIO / "LR2021CalibrationSettings.cpp").read_text()
        source = source.replace('#include "CustomLR2021Wrapper.h"',
                                '#include <helpers/radiolib/CustomLR2021Wrapper.h>')
        source = source.replace('#include "LR2021CalibrationSettings.h"',
                                '#include <helpers/radiolib/LR2021CalibrationSettings.h>')
        source = source.replace('#include "LR2021CalibrationPolicy.h"',
                                '#include <helpers/radiolib/LR2021CalibrationPolicy.h>')
        self.compile_run(SETTINGS + "\n" + source, ("-DUSE_LR2021=1",), stubs)

    def test_reset_and_morse_reapply_only_selected_trim(self):
        source = (RADIO / "CustomLR2021.h").read_text()
        self.assertIn("restoreXoscTrim()", body(source, "bool std_init("))
        self.assertIn("restoreXoscTrim()", body(source, "int16_t beginMorseReceive("))

    def test_all_cli_roles_load_before_init_and_service_pending(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor", "companion_radio"):
            source = (ROOT / "examples" / role / "main.cpp").read_text()
            setup = _uncommented(body(source, "void setup()"))
            with self.subTest(role=role):
                init = re.search(r"\bwhile\s*\([^\n;]*\bradio_init\s*\(\s*\)", setup)
                self.assertIsNotNone(init, "startup must initialize the radio")
                self.assertLess(setup.index("loadLR2021CalibrationSettings("), init.start())
                self.assertIn("loopLR2021CalibrationSettings(radio_driver)", source)
                self.assertIn("#if defined(USE_LR2021)", source)


if __name__ == "__main__":
    unittest.main()
