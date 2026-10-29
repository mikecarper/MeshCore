"""Execute the production board clock API and actual role policy handoffs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_esp32_usb_sleep import board_method
from cpp_source import body

ROOT=Path(__file__).resolve().parents[1]

PRELUDE=r'''
#include <cassert>
#include <cstdint>
#include <helpers/esp32/CpuClockPolicy.h>
#include <helpers/esp32/PowerTestControls.h>
static uint16_t actual_mhz=240;
static unsigned clock_writes=0;
static bool fail_clock=false,wrong_readback=false;
static uint32_t now_ms=100;
uint32_t millis(){return now_ms;}
uint32_t getCpuFrequencyMhz(){return actual_mhz;}
bool setCpuFrequencyMhz(uint32_t value){
 ++clock_writes;if(fail_clock)return false;actual_mhz=wrong_readback?80:value;return true;
}
#define EFUSE_BLK0_RDATA3_REG 0
#define EFUSE_RD_CHIP_CPU_FREQ_RATED 1
#define EFUSE_RD_CHIP_CPU_FREQ_LOW 2
static unsigned fuse_bits=0;
#define REG_GET_BIT(reg,mask) (fuse_bits&(mask))
using esp_err_t=int;constexpr int ESP_OK=0;
struct esp_pm_config_t {int max_freq_mhz,min_freq_mhz;bool light_sleep_enable;};
using esp_pm_config_esp32_t=esp_pm_config_t;
using esp_pm_config_esp32s3_t=esp_pm_config_t;
using esp_pm_config_esp32c3_t=esp_pm_config_t;
static esp_pm_config_t applied_pm{};
int esp_pm_configure(esp_pm_config_t* config){
 ++clock_writes;if(fail_clock)return 9;applied_pm=*config;
 actual_mhz=wrong_readback?80:config->min_freq_mhz;return ESP_OK;
}
struct MainBoard {
 virtual uint16_t getCpuFrequencyMHz()const=0;
 virtual bool supportsCpuFrequencyMHz(uint16_t)const=0;
 virtual void setCpuFrequencyRequest(uint16_t,bool){}
 virtual bool applyCpuFrequencyMHz(uint16_t,bool)=0;
 virtual bool prepareRadioProfileCpuClock(bool,bool& changed){changed=false;return true;}
};
struct ESP32Board:MainBoard {
 bool companion_cpu_clock_policy=false;
 bool radio_profile_cpu_clock_policy=false,applied_profile_cpu_clock_policy=false,applied_cpu_clock_valid=false;
 uint16_t requested_cpu_clock_selection=0,sampled_profile_cpu_mhz=0;
 bool requested_cpu_clock_powersaving=false;
 uint32_t profile_cpu_clock_retry_at=0;
 unsigned quiet_services=0;
 void servicePowerTestControls(){++quiet_services;}
 @METHODS@
} board;
namespace mesh {
struct Console {void printf(const char*,...){}};
Console& usbLoggingPort(){static Console value;return value;}
}
struct Prefs {uint16_t cpu_freq_mhz=0;bool powersaving_enabled=true;} prefs;
struct Mesh {bool dual=false;Prefs* getNodePrefs(){return &prefs;}bool isDualRadioActive(){return dual;}} the_mesh;
'''


class CpuClockBoardTest(unittest.TestCase):
    def fixture(self):
        methods="\n".join(board_method(signature) for signature in (
            "void setCompanionCpuClockPolicy(","void setRadioProfileCpuClockPolicy(",
            "uint16_t getCpuFrequencyMHz() const override", "bool supportsCpuFrequencyMHz(",
            "uint16_t resolveCpuFrequencyMHz(", "void setCpuFrequencyRequest(", "bool applyCpuFrequencyMHz(",
            "bool prepareRadioProfileCpuClock("))
        return PRELUDE.replace("@METHODS@",methods)

    def run_fixture(self,main,defines=()):
        with tempfile.TemporaryDirectory(prefix="mesh-esp32-clock-board-") as directory:
            folder=Path(directory);path=folder/"test.cpp";path.write_text(self.fixture()+main)
            result=subprocess.run([os.environ.get("CXX","c++"),"-std=c++17","-Wall","-Wextra",
                "-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie",
                "-DESP32_CPU_FREQ=240","-I",str(ROOT/"src"),*defines,str(path),"-o",str(folder/"test")],
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(folder/"test")],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_supported_silicon_fixed_clocks_pm_and_auto_role_policy(self):
        source=r'''
int main(){
 const unsigned nominal=board.supportsCpuFrequencyMHz(240)?240:160;
 assert(board.supportsCpuFrequencyMHz(80)&&board.supportsCpuFrequencyMHz(160));
#if CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C6
 assert(!board.supportsCpuFrequencyMHz(240));
#else
 assert(board.supportsCpuFrequencyMHz(240));
#endif
 assert(!board.supportsCpuFrequencyMHz(120)&&!board.supportsCpuFrequencyMHz(0));
 unsigned before=clock_writes;assert(!board.applyCpuFrequencyMHz(120,true));assert(clock_writes==before);
 for(uint16_t selected:{uint16_t(80),uint16_t(160)}){
   for(bool ps:{false,true}){
     assert(board.applyCpuFrequencyMHz(selected,ps));assert(board.getCpuFrequencyMHz()==selected);
#if CONFIG_PM_ENABLE
     assert(applied_pm.min_freq_mhz==selected&&applied_pm.max_freq_mhz==selected);
#endif
   }
 }
 board.setCompanionCpuClockPolicy(true);
 assert(board.applyCpuFrequencyMHz(0,true));
#if CONFIG_PM_ENABLE
 assert(applied_pm.max_freq_mhz==80&&applied_pm.min_freq_mhz==40);
#if ENABLE_USB_INTERFACE || ARDUINO_USB_CDC_ON_BOOT
 assert(!applied_pm.light_sleep_enable);
#else
 assert(applied_pm.light_sleep_enable);
#endif
#else
 assert(actual_mhz==80);
#endif
 assert(board.applyCpuFrequencyMHz(0,false));assert(actual_mhz==nominal);
 board.setCompanionCpuClockPolicy(false);
 assert(board.applyCpuFrequencyMHz(0,true));assert(actual_mhz==nominal);
#if CONFIG_PM_ENABLE
 assert(unsigned(applied_pm.max_freq_mhz)==nominal&&unsigned(applied_pm.min_freq_mhz)==nominal&&!applied_pm.light_sleep_enable);
#endif
 fail_clock=true;assert(!board.applyCpuFrequencyMHz(160,true));fail_clock=false;
 wrong_readback=true;assert(!board.applyCpuFrequencyMHz(160,true));wrong_readback=false;
#if CONFIG_IDF_TARGET_ESP32
 // Rated+low is the classic160MHz-only eFuse combination. Reject explicit
 //240 without letting Arduino silently replace it with a different clock.
 for(unsigned bits:{0U,1U,2U,3U}){
   fuse_bits=bits;assert(board.supportsCpuFrequencyMHz(240)==(bits!=3));
 }
 before=clock_writes;assert(!board.applyCpuFrequencyMHz(240,false));assert(clock_writes==before);
 assert(board.applyCpuFrequencyMHz(0,false)&&actual_mhz==160);
#endif
}
'''
        source="#include <initializer_list>\n"+source
        for target in ("ESP32","ESP32S3","ESP32C3","ESP32C6"):
            for pm in (0,1):
                with self.subTest(target=target,pm=pm):
                    self.run_fixture(source,[f"-DCONFIG_IDF_TARGET_{target}=1",f"-DCONFIG_PM_ENABLE={pm}","-DENABLE_USB_INTERFACE=1"])
        self.run_fixture(source,["-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=1"])

    def test_actual_companion_service_tracks_clock_choice_independently_of_ps(self):
        main=(ROOT/"examples/companion_radio/main.cpp").read_text()
        methods="\n".join(body(main,signature) for signature in (
            "static bool applyCompanionPowerSaving(","static void serviceCompanionPowerSaving("))
        prefix=r'''
static int8_t applied_power_saving=-1,attempted_power_saving=-1;
static uint16_t applied_cpu_frequency=UINT16_MAX,attempted_cpu_frequency=UINT16_MAX;
static unsigned long power_saving_retry_at=0;
'''
        tests=r'''
int main(){
 board.setCompanionCpuClockPolicy(true);serviceCompanionPowerSaving(true);
 assert(actual_mhz==80);unsigned writes=clock_writes;
 serviceCompanionPowerSaving();assert(clock_writes==writes);
 prefs.cpu_freq_mhz=160;serviceCompanionPowerSaving();assert(actual_mhz==160);
 prefs.powersaving_enabled=false;serviceCompanionPowerSaving();assert(actual_mhz==160);
 prefs.powersaving_enabled=true;serviceCompanionPowerSaving();assert(actual_mhz==160);
 prefs.cpu_freq_mhz=0;serviceCompanionPowerSaving();assert(actual_mhz==80);
 the_mesh.dual=true;serviceCompanionPowerSaving();assert(actual_mhz==240);
 writes=clock_writes;serviceCompanionPowerSaving();assert(clock_writes==writes);
 // Repeated Auto commands use the same board policy, rather than dropping
 // back to80 behind the role's unchanged request cache.
 assert(board.applyCpuFrequencyMHz(0,true)&&actual_mhz==240);
 prefs.cpu_freq_mhz=80;serviceCompanionPowerSaving();assert(actual_mhz==80);
 prefs.cpu_freq_mhz=0;serviceCompanionPowerSaving();assert(actual_mhz==240);
 the_mesh.dual=false;serviceCompanionPowerSaving();assert(actual_mhz==80);
 prefs.powersaving_enabled=false;serviceCompanionPowerSaving();assert(actual_mhz==240);
 // Failures retain the request and retry after the original bounded delay.
 prefs.cpu_freq_mhz=160;fail_clock=true;serviceCompanionPowerSaving();writes=clock_writes;
 serviceCompanionPowerSaving();assert(clock_writes==writes);
 now_ms+=5000;fail_clock=false;serviceCompanionPowerSaving();assert(actual_mhz==160);
}
'''
        self.run_fixture(prefix+methods+tests,["-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0",
            "-DCOMPANION_IDF_PM_AVAILABLE=0","-DCOMPANION_BT_MODEM_SLEEP_AVAILABLE=0"])

    def test_actual_infrastructure_loop_services_saved_choice_and_quiet_deadline(self):
        for role in ("simple_repeater","simple_room_server","simple_sensor"):
            source=(ROOT/"examples"/role/"main.cpp").read_text()
            loop_start=source.index("void loop()")
            clock_service=source.index("mesh::esp32::serviceCpuFrequencyPolicy(",loop_start)
            # Emergency maintenance also services UART restoration before its
            # early return. Keep this fixture anchored to the clock policy.
            quiet_service=source.rindex("  board.servicePowerTestControls();",loop_start,clock_service)
            # Execute the two real calls without importing unrelated enclosing
            # feature guards (early emergency placement is tested separately).
            service=source[quiet_service:source.index(";",quiet_service)+1]+"\n"
            service+=source[clock_service:source.index(";",clock_service)+1]+"\n"
            tests="void role_service(){\n"+service+"}\n"+r'''
int main(){
 prefs.cpu_freq_mhz=160;role_service();assert(actual_mhz==160&&board.quiet_services==1);
 unsigned writes=clock_writes;role_service();assert(clock_writes==writes&&board.quiet_services==2);
 prefs.powersaving_enabled=false;role_service();assert(actual_mhz==160);
 prefs.cpu_freq_mhz=80;role_service();assert(actual_mhz==80);
 prefs.cpu_freq_mhz=0;role_service();assert(actual_mhz==mesh::esp32::cpuclock::nominalMHz());
 // Active profiles prefer the chip maximum over a low nominal or Auto+PS.
 prefs.powersaving_enabled=true;the_mesh.dual=true;role_service();assert(actual_mhz==240);
 prefs.cpu_freq_mhz=80;role_service();assert(actual_mhz==80);
 prefs.cpu_freq_mhz=0;role_service();assert(actual_mhz==240);
 the_mesh.dual=false;role_service();assert(actual_mhz==mesh::esp32::cpuclock::nominalMHz());
}
'''
            with self.subTest(role=role):
                self.run_fixture(tests,["-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0"])
                self.run_fixture(tests,["-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0",
                    "-DESP32_CPU_FREQ=80"])

    def test_profile_auto_clock_limits_dials_timing_changes_and_failed_boost_retry(self):
        tests=r'''
int main(){
 board.setCompanionCpuClockPolicy(true);
 assert(board.applyCpuFrequencyMHz(0,true));
 bool changed=true;
 assert(board.prepareRadioProfileCpuClock(true,changed));
 const unsigned fast=board.supportsCpuFrequencyMHz(240)?240:160;
 assert(actual_mhz==fast);
#if CONFIG_PM_ENABLE
 assert(unsigned(applied_pm.min_freq_mhz)==fast&&unsigned(applied_pm.max_freq_mhz)==fast);
 // Fixing the clock does not disable the existing CPU sleep permission.
 assert(applied_pm.light_sleep_enable);
#endif
 unsigned writes=clock_writes;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&!changed&&clock_writes==writes);
 for(uint16_t dial : {uint16_t(80),uint16_t(160)}){
   assert(board.applyCpuFrequencyMHz(dial,true)&&actual_mhz==dial);
   assert(board.prepareRadioProfileCpuClock(true,changed));
   assert(changed);
   assert(actual_mhz==dial);
   assert(board.prepareRadioProfileCpuClock(true,changed)&&!changed);
 }
 assert(board.applyCpuFrequencyMHz(0,true)&&actual_mhz==fast);
 assert(board.prepareRadioProfileCpuClock(true,changed));
 assert(changed==(fast!=160));
 assert(board.prepareRadioProfileCpuClock(false,changed)&&!changed);
#if CONFIG_PM_ENABLE
 assert(actual_mhz==40&&applied_pm.max_freq_mhz==80);
#else
 assert(actual_mhz==80);
#endif
 fail_clock=true;writes=clock_writes;
 assert(!board.prepareRadioProfileCpuClock(true,changed)&&!changed);
 assert(clock_writes==writes+1);
 writes=clock_writes;
 assert(!board.prepareRadioProfileCpuClock(true,changed)&&clock_writes==writes);
 now_ms+=5000;fail_clock=false;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&actual_mhz==fast);
 assert(board.prepareRadioProfileCpuClock(true,changed)&&!changed);
}
'''
        for target in ("ESP32S3","ESP32C3","ESP32C6"):
            for pm in (0,1):
                with self.subTest(target=target,pm=pm):
                    self.run_fixture("#include <initializer_list>\n"+tests,[
                        f"-DCONFIG_IDF_TARGET_{target}=1",f"-DCONFIG_PM_ENABLE={pm}"])

    def test_indicator_keeps_existing_auto_clock_policy_with_active_profiles(self):
        tests=r'''
int main(){
 board.setCompanionCpuClockPolicy(true);
 board.setRadioProfileCpuClockPolicy(true);
 assert(board.applyCpuFrequencyMHz(0,true));
 assert(!board.radio_profile_cpu_clock_policy);
#if CONFIG_PM_ENABLE
 assert(applied_pm.min_freq_mhz==40&&applied_pm.max_freq_mhz==80);
#else
 assert(actual_mhz==80);
#endif
 bool changed=true;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&!changed);
 assert(board.applyCpuFrequencyMHz(160,true)&&actual_mhz==160);
}
'''
        for pm in (0,1):
            self.run_fixture(tests,["-DSENSECAP_INDICATOR_LORA=1",
                "-DCONFIG_IDF_TARGET_ESP32S3=1",f"-DCONFIG_PM_ENABLE={pm}"])

    def test_retune_entry_boosts_before_preamble_and_resets_only_pair_timing(self):
        source=(ROOT/"src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        entry=body(source,"mesh::RadioParamApplyResult RadioLibWrapper::tuneProfile(")
        # Execute the production admission prefix through tuple validation.
        # Stop before the measured hardware transaction; its SPI lifecycle has
        # separate coverage. Clock application must precede even preamble math.
        entry=entry[:entry.index("  const bool dual_profile =")]
        entry=entry.replace("RadioLibWrapper::tuneProfile", "tuneProfile")
        code=r'''
#include <RadioProfiles.h>
namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
struct RadioLibWrapper {
 ESP32Board* _board=&board;
 mesh::RadioProfiles _profiles;
 bool _cw_active=false,_profile_refresh_required=true,_profile_repair_required=false;
 uint8_t _active_profile=0;
 uint32_t _profile_generation=0;
 uint16_t _physical_preamble=32;
 unsigned preamble_reads=0,retunes=0;
 uint16_t expected=240;
 uint16_t profilePreamble(uint8_t){++preamble_reads;assert(actual_mhz==expected);return 32;}
 bool validateProfile(const mesh::RadioProfileParams&)const{return true;}
 @ENTRY@
   ++retunes;return mesh::RadioParamApplyResult::APPLIED;
 }
};
int main(){
 board.setCompanionCpuClockPolicy(true);
 assert(board.applyCpuFrequencyMHz(0,true)&&actual_mhz==80);
 RadioLibWrapper radio;
 mesh::RadioProfileConfig config;config.mode=mesh::RadioProfileMode::RxTx;
 radio._profiles.setSecondary(config,false);
 radio._profiles.switch_test_max_us[0]=99999;
 radio._profiles.switch_test_samples[0]=4;
 radio._profiles.switches=19;radio._profiles.switch_failures=3;
 assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::APPLIED&&actual_mhz==240);
 assert(!radio._profiles.switch_test_max_us[0]&&!radio._profiles.switch_test_samples[0]);
 assert(radio._profiles.switches==19&&radio._profiles.switch_failures==3);
 radio._profiles.switch_test_max_us[0]=1200;radio._profiles.switch_test_samples[0]=1;
 unsigned writes=clock_writes;
 assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::APPLIED&&clock_writes==writes);
 assert(radio._profiles.switch_test_max_us[0]==1200&&radio._profiles.switch_test_samples[0]==1);
 assert(board.applyCpuFrequencyMHz(160,true));radio.expected=160;
 assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::APPLIED);
 assert(!radio._profiles.switch_test_max_us[0]&&!radio._profiles.switch_test_samples[0]);
 radio._profiles.switch_test_max_us[0]=1700;
 assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::APPLIED);
 assert(radio._profiles.switch_test_max_us[0]==1700);
 // A failed boost defers before preamble math or hardware retuning.
 bool changed=false;assert(board.prepareRadioProfileCpuClock(false,changed));
 assert(board.applyCpuFrequencyMHz(0,true));fail_clock=true;
 const unsigned previous=radio.retunes,reads=radio.preamble_reads;
 assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::BUSY);
 assert(radio.retunes==previous&&radio.preamble_reads==reads);
}
'''.replace("@ENTRY@",entry)
        self.run_fixture(code,["-DESP32_PLATFORM=1","-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0"])

    def test_loaded_clock_choice_is_seeded_before_saved_radio_and_survives_apply_failure(self):
        common=(ROOT/"src/helpers/CommonCLI.cpp").read_text()
        load=body(common,"void CommonCLI::loadPrefs(")
        seed=load[load.index("#if defined(ESP32_PLATFORM)",load.index('mesh::hilStartupTrace("prefs_apply_begin")')):]
        seed=seed[:seed.index("#endif")]
        seed=seed[seed.index("\n")+1:]
        seed=seed.replace("_board->","board.").replace("_prefs->","prefs.")
        companion=(ROOT/"examples/companion_radio/MyMesh.cpp").read_text()
        begin=body(companion,"void MyMesh::begin(bool has_display,")
        companion_seed=begin[begin.index("  if (!mesh::esp32::cpuclock::validSelection("):]
        companion_seed=companion_seed[:companion_seed.index("#endif")]
        companion_seed=companion_seed.replace("_prefs.","prefs.")
        self.assertLess(begin.index("_store->loadPrefs("),begin.index("board.setCpuFrequencyRequest("))
        self.assertLess(begin.index("board.setCpuFrequencyRequest("),begin.index("configureRadioFromPrefs();"))
        self.assertLess(load.index("loadPrefsInt("),load.index("_board->setCpuFrequencyRequest("))
        self.assertLess(load.index("_board->setCpuFrequencyRequest("),load.index("_radio_profiles.stagePrimary("))
        for path,signature in (("simple_repeater/MyMesh.cpp","void MyMesh::begin(FILESYSTEM"),
                               ("simple_room_server/MyMesh.cpp","void MyMesh::begin(FILESYSTEM"),
                               ("simple_sensor/SensorMesh.cpp","void SensorMesh::begin(FILESYSTEM")):
            role=body((ROOT/"examples"/path).read_text(),signature)
            self.assertLess(role.index("_cli.loadPrefs("),role.index("applySavedRadioParams()"))
        tests=r'''
int main(){
 board.setCompanionCpuClockPolicy(true);
 prefs.cpu_freq_mhz=80;prefs.powersaving_enabled=true;
 seed_request();
 assert(clock_writes==0); // Seeding must not use unconfigured peripherals/SDK.
 bool changed=false;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&actual_mhz==80);
 assert(board.requested_cpu_clock_selection==80&&board.requested_cpu_clock_powersaving);
 prefs.cpu_freq_mhz=160;prefs.powersaving_enabled=false;
 seed_request();
 fail_clock=true;const unsigned writes=clock_writes;
 assert(!board.prepareRadioProfileCpuClock(true,changed));
 assert(clock_writes==writes+1);
 assert(board.requested_cpu_clock_selection==160&&!board.requested_cpu_clock_powersaving);
 assert(!board.prepareRadioProfileCpuClock(true,changed)&&clock_writes==writes+1);
 now_ms+=5000;fail_clock=false;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&actual_mhz==160);
 // A direct CLI/role apply which fails also retains its requested clock;
 // the retune gate must not retry the last successful160MHz selection.
 fail_clock=true;assert(!board.applyCpuFrequencyMHz(80,true));fail_clock=false;
 assert(board.prepareRadioProfileCpuClock(true,changed)&&actual_mhz==80);
 assert(board.requested_cpu_clock_selection==80&&board.requested_cpu_clock_powersaving);
}
'''
        for role,seed_code in (("infrastructure",seed),("companion",companion_seed)):
            for pm in (0,1):
                with self.subTest(role=role,pm=pm):
                    self.run_fixture("void seed_request(){\n"+seed_code+"\n}\n"+tests,[
                        "-DESP32_PLATFORM=1","-DCONFIG_IDF_TARGET_ESP32S3=1",f"-DCONFIG_PM_ENABLE={pm}"])

    def test_rc32_post_boot_override_is_repaired_before_retune_with_cached_role_policy(self):
        source=(ROOT/"src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        entry=body(source,"mesh::RadioParamApplyResult RadioLibWrapper::tuneProfile(")
        entry=entry[:entry.index("  const bool dual_profile =")]
        entry=entry.replace("RadioLibWrapper::tuneProfile", "tuneProfile")
        for role in ("simple_repeater","simple_room_server","simple_sensor"):
            main=(ROOT/"examples"/role/"main.cpp").read_text()
            setup=body(main,"void setup()")
            # The role must not cache the loaded request before board-specific
            # onBootComplete can select its post-startup clock (RC32:160MHz).
            self.assertNotIn("serviceCpuFrequencyPolicy(",setup[:setup.index("board.onBootComplete();")])
            start=main.index("  board.servicePowerTestControls();",main.index("void loop()"))
            service=main[start:main.index("#endif",start)]
            code=r'''
#include <RadioProfiles.h>
namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
void role_service(){@SERVICE@}
struct RadioLibWrapper {
 ESP32Board* _board=&board;
 mesh::RadioProfiles _profiles;
 bool _cw_active=false,_profile_refresh_required=true,_profile_repair_required=false;
 uint8_t _active_profile=0;uint32_t _profile_generation=0;
 uint16_t _physical_preamble=32,expected=240;
 uint16_t profilePreamble(uint8_t){assert(actual_mhz==expected);return 32;}
 bool validateProfile(const mesh::RadioProfileParams&)const{return true;}
 @ENTRY@
   return mesh::RadioParamApplyResult::APPLIED;
 }
};
int main(){
 RadioLibWrapper radio;mesh::RadioProfileConfig config;config.mode=mesh::RadioProfileMode::RxTx;
 radio._profiles.setSecondary(config,false);the_mesh.dual=true;
 // Loaded profiles can apply before onBootComplete. A fixed requested clock
 // must be respected both there and after the board's post-boot override.
 for(uint16_t selected : {uint16_t(0),uint16_t(80),uint16_t(160),uint16_t(240)}){
   prefs.cpu_freq_mhz=selected;prefs.powersaving_enabled=true;
   board.setCpuFrequencyRequest(selected,true);
   radio.expected=selected?selected:240;
   assert(radio.tuneProfile(0)==mesh::RadioParamApplyResult::APPLIED);
   role_service();assert(actual_mhz==radio.expected);
   actual_mhz=160; // RC32::onBootComplete hardware change, outside role cache.
   const unsigned writes=clock_writes;
   role_service();assert(clock_writes==writes); // Same cached role request.
   assert(radio.tuneProfile(1)==mesh::RadioParamApplyResult::APPLIED);
   assert(actual_mhz==radio.expected);
   assert(clock_writes==writes+(radio.expected!=160));
 }
}
'''.replace("@SERVICE@",service).replace("@ENTRY@",entry)
            self.run_fixture("#include <initializer_list>\n"+code,["-DESP32_PLATFORM=1",
                "-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0","-DESP32_POST_BOOT_CPU_FREQ=160"])

    def test_rc32_post_boot_nominal_survives_shared_policy_and_explicit_selection(self):
        main=(ROOT/"examples/companion_radio/main.cpp").read_text()
        apply=body(main,"static bool applyCompanionPowerSaving(")
        tests=r'''
int main(){
 // RC32 starts peripherals at 240 MHz, then uses 160 MHz as its Auto nominal.
 // This must take precedence over the generic ESP32_CPU_FREQ=240 fixture.
 board.setCompanionCpuClockPolicy(true);
 assert(applyCompanionPowerSaving(false)&&actual_mhz==160);
 assert(applyCompanionPowerSaving(true)&&actual_mhz==80);
 assert(applyCompanionPowerSaving(false)&&actual_mhz==160);
 // A saved explicit clock remains an operator choice even with PS enabled.
 prefs.cpu_freq_mhz=240;
 assert(applyCompanionPowerSaving(true)&&actual_mhz==240);
 prefs.cpu_freq_mhz=0;
 assert(applyCompanionPowerSaving(false)&&actual_mhz==160);
 // Infrastructure Auto also uses the post-boot nominal in either PS state.
 board.setCompanionCpuClockPolicy(false);
 for(bool ps:{false,true})assert(board.applyCpuFrequencyMHz(0,ps)&&actual_mhz==160);
}
'''
        self.run_fixture("#include <initializer_list>\n"+apply+tests,[
            "-DCONFIG_IDF_TARGET_ESP32S3=1","-DCONFIG_PM_ENABLE=0",
            "-DESP32_POST_BOOT_CPU_FREQ=160","-DCOMPANION_IDF_PM_AVAILABLE=0",
            "-DCOMPANION_BT_MODEM_SLEEP_AVAILABLE=0"])


if __name__=="__main__": unittest.main(verbosity=2)
