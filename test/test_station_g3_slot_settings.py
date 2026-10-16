"""Run the real G3 NVS adapter and board CLI/startup against a strict SDK fake."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/station_g3_esp32"

NVS = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
using esp_err_t=int;
using nvs_handle_t=unsigned;
constexpr int ESP_OK=0, ESP_ERR_NVS_NOT_FOUND=1, ESP_ERR_NVS_TYPE_MISMATCH=2;
constexpr int NVS_READONLY=0, NVS_READWRITE=1;
namespace fake {
struct Entry { int value; bool u8=true; };
inline bool exists=false,eagerWrites=false;
inline std::map<std::string,Entry> disk,pending;
inline unsigned opens=0,closes=0,gets=0,sets=0,commits=0;
inline int openError=0,setError=0,commitError=0,modeReadError=0,lastReadError=0;
inline int openedMode=-1;
inline std::vector<std::string> written;
inline void reset(){
 exists=eagerWrites=false;disk.clear();pending.clear();written.clear();
 opens=closes=gets=sets=commits=0;
 openError=setError=commitError=modeReadError=lastReadError=0;openedMode=-1;
}
inline void put(const char* key,int value,bool u8=true){exists=true;disk[key]={value,u8};}
}
inline int nvs_open(const char* ns,int mode,nvs_handle_t* handle){
 assert(std::string(ns)=="g3-radio"); ++fake::opens;
 if(fake::openError) return fake::openError;
 if(mode==NVS_READONLY && !fake::exists) return ESP_ERR_NVS_NOT_FOUND;
 assert(mode==NVS_READONLY || mode==NVS_READWRITE);
 fake::openedMode=mode;fake::pending.clear();*handle=7;return ESP_OK;
}
inline int nvs_get_u8(nvs_handle_t handle,const char* key,uint8_t* value){
 assert(handle==7); ++fake::gets;
 const std::string name(key);assert(name=="mode" || name=="last");
 const int injected=name=="mode" ? fake::modeReadError:fake::lastReadError;
 if(injected) return injected;
 const auto found=fake::disk.find(name);
 if(found==fake::disk.end()) return ESP_ERR_NVS_NOT_FOUND;
 if(!found->second.u8) return ESP_ERR_NVS_TYPE_MISMATCH;
 *value=found->second.value;return ESP_OK;
}
inline int nvs_set_u8(nvs_handle_t handle,const char* key,uint8_t value){
 assert(handle==7 && fake::openedMode==NVS_READWRITE);++fake::sets;
 const std::string name(key);assert(name=="mode" || name=="last");
 fake::written.push_back(name);
 if(fake::setError) return fake::setError;
 fake::pending[name]={value,true};
 // The NVS contract permits storage updates before commit. A commit error
 // therefore proves no live binding change, not physical flash rollback.
 if(fake::eagerWrites){fake::disk[name]={value,true};fake::exists=true;}
 return ESP_OK;
}
inline int nvs_commit(nvs_handle_t handle){
 assert(handle==7 && fake::openedMode==NVS_READWRITE);++fake::commits;
 if(fake::commitError) return fake::commitError;
 for(const auto& item:fake::pending) fake::disk[item.first]=item.second;
 fake::pending.clear();fake::exists=true;return ESP_OK;
}
inline void nvs_close(nvs_handle_t handle){
 assert(handle==7);++fake::closes;fake::pending.clear();fake::openedMode=-1;
}
'''

BOARD = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <utility>
#include <vector>
#include "StationG3RadioSlot.h"
using gpio_num_t=int;
using esp_reset_reason_t=int;
constexpr int HIGH=1,ESP_RST_DEEPSLEEP=5;
constexpr int BD_STARTUP_NORMAL=0,BD_STARTUP_RX_PACKET=1;
namespace fake {
inline int resetReason=0;
inline uint64_t wakeMask=0;
inline unsigned femInit=0,femMutations=0,baseBegins=0;
inline std::vector<std::pair<int,int>> releases;
inline std::vector<int> deinitialized;
inline void resetBoard(){resetReason=0;wakeMask=0;femInit=femMutations=baseBegins=0;releases.clear();deinitialized.clear();}
}
inline int esp_reset_reason(){return fake::resetReason;}
inline uint64_t esp_sleep_get_ext1_wakeup_status(){return fake::wakeMask;}
namespace mesh { namespace esp32 {
inline void releaseOutputAfterDeepSleep(int pin,int level){fake::releases.emplace_back(pin,level);}
inline void deinitRtcGpioIfSupported(int pin){fake::deinitialized.push_back(pin);}
inline void holdOutputForDeepSleep(int,int){}
}}
class KeyValueStore {public:
 virtual bool setByKey(const char*,const char*){assert(false);return false;}
 virtual bool getByKey(const char*,char*,size_t){assert(false);return false;}
};
class LoRaFEMControl {bool lna=false,pa=false;public:
 void init(){
  // Both previously held sockets are safe before any FEM startup occurs.
  for(int pin:{11,43,10,42}){
   bool found=false;for(const auto& item:fake::releases) if(item.first==pin && item.second==HIGH) found=true;
   assert(found);
  }
  ++fake::femInit;
 }
 void setSleepModeEnable(){}
 bool canControlLNA()const{return true;} bool canControlPAGain()const{return true;}
 void setLNAEnable(bool value){lna=value;++fake::femMutations;}
 void setPAGainEnable(bool value){pa=value;++fake::femMutations;}
 bool isLNAEnabled()const{return lna;} bool isPAGainEnabled()const{return pa;}
};
class ESP32Board {public:
 uint8_t startup_reason=BD_STARTUP_NORMAL;
 void begin(){startup_reason=BD_STARTUP_NORMAL;++fake::baseBegins;}
 void shutdownPeripherals(){}
 virtual uint32_t getIRQGpio(){return uint32_t(-1);}
};
class StationG3Board:public ESP32Board {
 KeyValueStore* _prefs=nullptr;
public:
 LoRaFEMControl loRaFEMControl;
 @BOOT@
 @IRQ@
 void attachDynamicPrefs(KeyValueStore*);
 bool handleCommand(const char*,uint32_t,char*);
 bool setLoRaFemLnaEnabled(bool);bool canControlLoRaFemLna()const;bool isLoRaFemLnaEnabled()const;
 bool setLoRaFemPaGainEnabled(bool);bool canControlLoRaFemPaGain()const;bool isLoRaFemPaGainEnabled()const;
 void shutdownPeripherals();
};
'''

HARNESS = r'''
#include "nvs.h"
#include "StationG3Board.h"
#include <algorithm>
#include <string>
using namespace station_g3;
void reset(){fake::reset();fake::resetBoard();slotState()=SlotState{};}
void unchangedForeign(){
 // The strict NVS fake rejects access to ANY namespace except g3-radio and
 // ANY key except mode/last. The separate KeyValueStore also rejects calls.
 assert(fake::femMutations==0);
 for(const auto& key:fake::written) assert(key=="mode" || key=="last");
}
void fresh(){
 reset();loadSlotSettings();const auto& s=slotState();
 assert(s.loaded && s.settingsHealthy && s.bootMode==0 && s.savedMode==0 && s.last==0);
 assert(s.active==0 && s.detected==0 && s.candidate==1);
 assert(fake::opens==1 && fake::closes==0 && fake::sets==0 && fake::commits==0);
 loadSlotSettings();assert(fake::opens==1);unchangedForeign();
 reset();fake::exists=true;loadSlotSettings();assert(slotState().settingsHealthy && fake::closes==1);
}
void valid(){
 for(uint8_t mode=0;mode<=2;++mode) for(uint8_t last=0;last<=2;++last){
  reset();fake::put("mode",mode);fake::put("last",last);loadSlotSettings();
  const auto& s=slotState();assert(s.bootMode==mode && s.savedMode==mode && s.last==last);
  assert(s.loaded && s.settingsHealthy && fake::opens==1 && fake::closes==1);
  assert(fake::gets==2 && fake::sets==0 && fake::commits==0);unchangedForeign();
 }
}
void readErrors(){
 reset();fake::openError=77;loadSlotSettings();
 assert(slotState().loaded && !slotState().settingsHealthy && slotState().bootMode==0);
 assert(fake::closes==0);loadSlotSettings();assert(fake::opens==1);
 for(int error:{ESP_ERR_NVS_TYPE_MISMATCH,78}){
  reset();fake::put("mode",2);fake::put("last",2);fake::modeReadError=error;
  loadSlotSettings();assert(!slotState().settingsHealthy && slotState().bootMode==0 && slotState().savedMode==0 && slotState().last==2);
  reset();fake::put("mode",1);fake::put("last",2);fake::lastReadError=error;
  loadSlotSettings();assert(!slotState().settingsHealthy && slotState().bootMode==1 && slotState().last==0);
 }
 reset();fake::put("mode",255);fake::put("last",2);loadSlotSettings();
 assert(!slotState().settingsHealthy && slotState().bootMode==0 && slotState().last==2);
 reset();fake::put("mode",1);fake::put("last",3);loadSlotSettings();
 assert(!slotState().settingsHealthy && slotState().bootMode==1 && slotState().last==0);
 reset();fake::put("mode",2,false);loadSlotSettings();assert(!slotState().settingsHealthy && slotState().bootMode==0);
 unchangedForeign();
}
void failedSaves(){
 for(int stage=0;stage<4;++stage){
  reset();fake::put("mode",1);fake::put("last",1);loadSlotSettings();
  auto& s=slotState();s.active=s.candidate=1;s.detected=3;
  if(stage==0) fake::openError=77;
  if(stage==1) fake::setError=78;
  if(stage>=2) fake::commitError=79;
  if(stage==3) fake::eagerWrites=true;
  const unsigned closes=fake::closes,sets=fake::sets,commits=fake::commits;
  StationG3Board board;char reply[160]={};
  assert(board.handleCommand("set radio.slot 2",0,reply));
  assert(std::string(reply).find("could not be saved")!=std::string::npos);
  assert(s.bootMode==1 && s.savedMode==1 && s.last==1 && s.active==1 && s.candidate==1 && s.detected==3);
  assert(fake::disk.at("mode").value==(stage==3 ? 2:1) && fake::disk.at("last").value==1);
  assert(fake::closes==closes+(stage!=0));assert(fake::sets==sets+(stage!=0));
  assert(fake::commits==commits+(stage>=2));unchangedForeign();
  if(stage==3){
   slotState()=SlotState{};loadSlotSettings();
   assert(slotState().bootMode==2); // A future boot must trust actual NVS.
  }
 }
 reset();assert(!saveSlotMode(3) && fake::opens==0);
}
void pendingCLI(){
 reset();fake::put("mode",1);fake::put("last",1);loadSlotSettings();
 auto& s=slotState();s.active=s.candidate=1;s.detected=3;
 StationG3Board board;char reply[160]={};
 assert(board.handleCommand("set radio.slot 2",123,reply));
 assert(std::string(reply)=="OK - radio.slot 2 saved; reboot to apply (active=1)");
 assert(s.savedMode==2 && s.bootMode==1 && s.active==1 && s.candidate==1 && s.last==1);
 assert(s.choose(3)==1); // Saved setting cannot move the running radio.
 assert(board.handleCommand("get radio.slot",0,reply));
 assert(std::string(reply)=="> 2; active=1; detected=1,2; reboot required");
 unchangedForeign();s=SlotState{};loadSlotSettings();assert(s.bootMode==2 && s.savedMode==2);
 assert(s.choose(3)==2);
 for(const char* value:{"auto","1","2"}){
  const std::string command=std::string("set radio.slot ")+value;
  assert(board.handleCommand(command.c_str(),0,reply));assert(std::string(reply).find("OK -")==0);
 }
 unchangedForeign();
}
void invalidCLI(){
 reset();loadSlotSettings();StationG3Board board;char reply[160]={};
 for(const char* value:{"","0","3","-1","255","AUTO","Auto","01","2 "," 2","1,2","1\n","2foo"}){
  const std::string command=std::string("set radio.slot ")+value;
  assert(board.handleCommand(command.c_str(),0,reply));
  assert(std::string(reply)=="Error: radio.slot must be auto, 1, or 2");
 }
 for(const char* command:{"set radio.slot","set radio.slotx 2","get radio.slotx","get radio2","get radio.slot "})
  assert(!board.handleCommand(command,0,reply));
 assert(!board.handleCommand(nullptr,0,reply) && !board.handleCommand("get radio.slot",0,nullptr));
 assert(fake::sets==0 && fake::commits==0 && slotState().savedMode==0);
 slotState().settingsHealthy=false;assert(board.handleCommand("get radio.slot",0,reply));
 assert(std::string(reply).find("WARN settings unavailable/invalid")!=std::string::npos);
 unchangedForeign();
}
void invalidLastWarning(){
 for(bool wrongType:{false,true}){
  reset();fake::put("mode",1);fake::put("last",wrongType ? 2:255,!wrongType);
  loadSlotSettings();assert(!slotState().settingsHealthy);
  StationG3Board board;char reply[160]={};
  assert(board.handleCommand("set radio.slot 2",0,reply));
  assert(std::string(reply).find("OK -")==0);
  assert(slotState().savedMode==2 && slotState().bootMode==1 && !slotState().settingsHealthy);
  assert(board.handleCommand("get radio.slot",0,reply));
  assert(std::string(reply).find("WARN settings unavailable/invalid")!=std::string::npos);
  assert(fake::disk.at("mode").value==2);
  assert(fake::disk.at("last").value==(wrongType ? 2:255));
  assert(fake::disk.at("last").u8==!wrongType);
  // Successful independent repair still does not erase the historical boot
  // warning. Only a subsequent load can establish both keys are now valid.
  rememberActiveSlot(1);assert(!slotState().settingsHealthy);
  slotState()=SlotState{};loadSlotSettings();
  assert(slotState().settingsHealthy && slotState().bootMode==2 && slotState().last==1);
  unchangedForeign();
 }
}
void remember(){
 reset();fake::put("last",1);loadSlotSettings();
 const unsigned opens=fake::opens;
 rememberActiveSlot(0);rememberActiveSlot(3);rememberActiveSlot(1);
 assert(fake::opens==opens && fake::sets==0 && fake::commits==0);
 rememberActiveSlot(2);assert(slotState().last==2 && fake::disk.at("last").value==2);
 assert(fake::sets==1 && fake::commits==1);
 rememberActiveSlot(2);assert(fake::sets==1 && fake::commits==1);
 fake::commitError=79;rememberActiveSlot(1);
 assert(slotState().last==2 && fake::disk.at("last").value==2 && !slotState().settingsHealthy);
 assert(fake::written==std::vector<std::string>({"last","last"}));unchangedForeign();
}
void choose(){
 reset();auto& s=slotState();
 for(uint8_t last=0;last<=2;++last){s.last=last;
  assert(s.choose(0)==0 && s.choose(1)==1 && s.choose(2)==2);
  assert(s.choose(3)==(last==2 ? 2:1));
 }
 for(uint8_t forced=1;forced<=2;++forced){s.bootMode=forced;
  assert(s.choose(0)==0 && s.choose(3)==forced);
  assert(s.choose(1U<<(forced-1))==forced);
  assert(s.choose(1U<<(2-forced))==0);
 }
 s.bootMode=3;assert(s.choose(3)==0);unchangedForeign();
}
void boot(){
 // Wake sources belong to the PREVIOUS boot's selected socket. A newly saved
 // next-boot slot must not hide a genuine wake from the other physical IRQ.
 for(uint8_t mode=0;mode<=2;++mode) for(uint8_t last=0;last<=2;++last)
  for(int wake:{-1,2,48}){
   reset();fake::put("mode",mode);fake::put("last",last);
   fake::resetReason=ESP_RST_DEEPSLEEP;
   fake::wakeMask=wake<0 ? 0:1ULL<<wake;
   StationG3Board board;board.begin();
   const uint8_t expected=mode==2 || (mode==0 && last==2) ? 2:1;
   assert(slotState().candidate==expected && slotState().active==0 && slotState().detected==0);
   assert(board.getIRQGpio()==pinsForSlot(expected).irq);
   assert(board.startup_reason==(wake<0 ? BD_STARTUP_NORMAL:BD_STARTUP_RX_PACKET));
   assert(fake::femInit==1 && fake::baseBegins==1 && fake::sets==0);
   unchangedForeign();
  }
}
int main(int argc,char** argv){
 assert(argc==2);const std::string which=argv[1];
 if(which=="fresh") fresh();else if(which=="valid") valid();
 else if(which=="read-errors") readErrors();else if(which=="failed-saves") failedSaves();
 else if(which=="pending-cli") pendingCLI();else if(which=="invalid-cli") invalidCLI();
 else if(which=="invalid-last-warning") invalidLastWarning();
 else if(which=="remember") remember();else if(which=="choose") choose();
 else if(which=="boot") boot();else assert(false);
}
'''


class StationG3SlotSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="g3-slot-settings-")
        work = Path(cls.temporary.name)
        header = (VARIANT / "StationG3Board.h").read_text()
        board = BOARD.replace("@BOOT@", body(header, "void begin()"))
        board = board.replace("@IRQ@", body(header, "uint32_t getIRQGpio()"))
        (work / "StationG3Board.h").write_text(board)
        (work / "StationG3RadioSlot.h").write_text((VARIANT / "StationG3RadioSlot.h").read_text())
        (work / "nvs.h").write_text(NVS)
        (work / "helpers").mkdir()
        (work / "helpers/ESP32GpioHold.h").write_text("#pragma once\n")
        (work / "settings.cpp").write_text((VARIANT / "StationG3RadioSlot.cpp").read_text())
        (work / "board.cpp").write_text((VARIANT / "StationG3Board.cpp").read_text())
        (work / "test.cpp").write_text(HARNESS)
        cls.binary = work / "test.exe"
        built = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-I", str(work), str(work / "settings.cpp"), str(work / "board.cpp"),
            str(work / "test.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if built.returncode:
            cls.temporary.cleanup()
            raise AssertionError(built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_and_empty_namespace_default_auto_without_writes(self):
        self.run_case("fresh")

    def test_checked_values_load_once_without_touching_radio_or_identity(self):
        self.run_case("valid")

    def test_read_errors_invalid_modes_and_wrong_types_remain_safe_and_visible(self):
        self.run_case("read-errors")

    def test_save_failures_preserve_live_state_without_claiming_flash_rollback(self):
        self.run_case("failed-saves")

    def test_cli_saved_pending_mode_stays_boot_frozen_until_reload(self):
        self.run_case("pending-cli")

    def test_invalid_cli_values_never_write_and_status_reports_invalid_storage(self):
        self.run_case("invalid-cli")

    def test_mode_save_preserves_invalid_last_warning_until_reboot_revalidation(self):
        self.run_case("invalid-last-warning")

    def test_last_successful_slot_only_persists_when_changed(self):
        self.run_case("remember")

    def test_choice_respects_forced_slot_and_last_successful_auto_preference(self):
        self.run_case("choose")

    def test_both_physical_wake_irqs_are_recognized_before_current_boot_probing(self):
        self.run_case("boot")


if __name__ == "__main__":
    unittest.main()
