"""Execute the real regulator settings and retained-platform implementations."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

FILESYSTEM = r'''
#pragma once
#include <map>
#include <string>
#include <vector>
#include <cstdint>
#include <cstring>
#include <algorithm>
struct MockFS;
struct File {
  MockFS* fs=nullptr;std::string name;size_t cursor=0;bool valid=false;
  File()=default;explicit File(MockFS& fs):fs(&fs) {}
  File(MockFS* fs,const std::string& name,bool valid):fs(fs),name(name),valid(valid) {}
  bool open(const char*,int);
  explicit operator bool() const { return valid; }
  size_t size() const;
  size_t read(uint8_t*,size_t);
  size_t write(const uint8_t*,size_t);
  void close() {}
  void flush() {}
  bool isDirectory() const { return false; }
};
struct MockFS {
  std::map<std::string,std::vector<uint8_t>> files;
  bool fail_open=false,fail_write=false,fail_restore=false,corrupt_write=false;
  std::string fail_rename_from,fail_read_path;
  unsigned writes=0,renames=0;
  bool exists(const char* name) const { return files.count(name); }
  bool remove(const char* name) { return files.erase(name)!=0; }
  bool rename(const char* from,const char* to) {
    ++renames;
    if(fail_rename_from==from || (fail_restore && std::string(from)=="/regulator_v1.bak"))return false;
    if(!files.count(from))return false;
#if !defined(NRF52_PLATFORM)
    if(files.count(to))return false;
#endif
    // Adafruit LittleFS atomically replaces an existing destination.
    files[to]=files[from];files.erase(from);return true;
  }
  File open(const char* name,const char* mode,bool=false) {
    if(fail_open)return File();
    if(*mode=='w'){ files[name].clear();return File(this,name,true); }
    return File(this,name,exists(name));
  }
};
inline bool File::open(const char* key,int flags) {
  File f=fs->open(key,flags?"w":"r");*this=f;return valid;
}
inline size_t File::size() const { return valid ? fs->files[name].size() : 0; }
inline size_t File::read(uint8_t* bytes,size_t length) {
  if(!valid || fs->fail_read_path==name)return 0;
  const auto& data=fs->files[name];length=std::min(length,data.size()-cursor);
  memcpy(bytes,data.data()+cursor,length);cursor+=length;return length;
}
inline size_t File::write(const uint8_t* bytes,size_t length) {
  if(!valid)return 0;
  ++fs->writes;if(fs->fail_write)length/=2;
  auto& data=fs->files[name];data.insert(data.end(),bytes,bytes+length);
  if(fs->corrupt_write && !data.empty())data.back()^=1;
  return length;
}
#define FILESYSTEM MockFS
#define FILE_O_READ 0
#define FILE_O_WRITE 1
'''

DISPATCHER = r'''
#pragma once
#include <cstdint>
#include <vector>
#include <helpers/RegulatorMode.h>
namespace mesh {
struct MainBoard {
  uint8_t modes=3;power::RegulatorMode default_mode=power::RegulatorMode::Dcdc;
  power::RegulatorMode active=power::RegulatorMode::Default;
  unsigned applies=0;bool dcdc_applied=false;uint16_t mv=3600;
  std::vector<uint16_t> samples;unsigned sample_index=0;
  uint8_t mcuRegulatorModes() const { return modes; }
  power::RegulatorMode defaultMcuRegulatorMode() const { return default_mode; }
  power::RegulatorMode activeMcuRegulatorMode() const { return active; }
  bool applyMcuRegulator(power::RegulatorMode mode) {
    ++applies;if(!power::regulatorModeSupported(modes,mode))return false;
    active=mode==power::RegulatorMode::Default ? default_mode : mode;
    dcdc_applied|=active==power::RegulatorMode::Dcdc;return true;
  }
  uint16_t getBattMilliVolts() {
    return samples.empty()?mv:samples[sample_index++%samples.size()];
  }
  void resetHardware() { active=power::RegulatorMode::Default;sample_index=0; }
};
struct Radio {
  uint8_t modes=3;power::RegulatorMode default_mode=power::RegulatorMode::Dcdc;
  power::RegulatorMode active=power::RegulatorMode::Default,boot=power::RegulatorMode::Default;
  unsigned configures=0,inits=0;bool locked=false,dcdc_applied=false;
  uint8_t regulatorModes() const { return modes; }
  power::RegulatorMode defaultRegulatorMode() const { return default_mode; }
  power::RegulatorMode activeRegulatorMode() const { return active; }
  bool configureBootRegulator(power::RegulatorMode mode) {
    ++configures;if(locked||!power::regulatorModeSupported(modes,mode))return false;
    boot=mode;return true;
  }
  void initialize() {
    ++inits;locked=true;active=boot==power::RegulatorMode::Default?default_mode:boot;
    dcdc_applied|=active==power::RegulatorMode::Dcdc;
  }
  void resetHardware() { active=boot=power::RegulatorMode::Default;locked=false; }
};
}
'''

ESP_SYSTEM = r'''
#pragma once
extern int mock_reset_reason;
enum { ESP_RST_UNKNOWN=0,ESP_RST_POWERON,ESP_RST_EXT,ESP_RST_SW,ESP_RST_PANIC,
  ESP_RST_INT_WDT,ESP_RST_TASK_WDT,ESP_RST_WDT,ESP_RST_DEEPSLEEP,ESP_RST_BROWNOUT };
inline int esp_reset_reason() { return mock_reset_reason; }
'''


class RegulatorSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('native C++ compiler unavailable')
        cls.temp = tempfile.TemporaryDirectory(prefix='meshcore-regulator-settings-')
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        headers = {
            'helpers/IdentityStore.h': FILESYSTEM,
            'Dispatcher.h': DISPATCHER,
            'Arduino.h': '#pragma once\n#include <cstdint>\nextern uint32_t mock_now;\ninline uint32_t millis(){return mock_now;}\ninline void delay(unsigned ms){mock_now+=ms;}\n',
            'esp_system.h': ESP_SYSTEM,
            'esp_attr.h': '#pragma once\n#define RTC_NOINIT_ATTR\n',
            'soc/soc_caps.h': '#pragma once\n#ifndef SOC_RTC_SLOW_MEM_SUPPORTED\n#define SOC_RTC_SLOW_MEM_SUPPORTED 1\n#endif\n#define SOC_RTC_FAST_MEM_SUPPORTED 0\n',
            'hardware/watchdog.h': '#pragma once\nextern bool mock_watchdog_enabled,mock_watchdog_any;\ninline bool watchdog_enable_caused_reboot(){return mock_watchdog_enabled;}\ninline bool watchdog_caused_reboot(){return mock_watchdog_any;}\n',
        }
        for name, content in headers.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cls.binaries = {}
        for platform, flags in {'esp32': ['-DESP32=1'],
                'esp32_fixed_radio_name': ['-DESP32=1','-DRADIO_CLASS=CustomSTM32WLx'],
                'stm32_future_external_radio': ['-DSTM32_PLATFORM=1','-DRADIO_CLASS=CustomSX1262'],
                'rp2040': ['-DRP2040_PLATFORM=1'],
                'nrf52': ['-DNRF52_PLATFORM=1','-DNRF52_POWER_MANAGEMENT=1'],
                'no_retention': ['-DESP32=1','-DSOC_RTC_SLOW_MEM_SUPPORTED=0']}.items():
            binary = folder / platform
            result = subprocess.run([compiler, '-std=c++17','-Wall','-Wextra','-Werror',
                *SANITIZER_FLAGS,*flags,'-I',str(folder),'-I',str(ROOT/'src'),
                str(ROOT/'test/fixtures/regulator_settings_native.cpp'),'-o',str(binary)],
                text=True,capture_output=True,timeout=45)
            if result.returncode:
                raise AssertionError(result.stdout+result.stderr)
            cls.binaries[platform] = binary
        binary = folder / 'stm32_fixed'
        result = subprocess.run([compiler, '-std=c++17','-Wall','-Wextra','-Werror',
            *SANITIZER_FLAGS,'-DSTM32_PLATFORM=1','-DRADIO_CLASS=CustomSTM32WLx',
            '-I',str(folder),'-I',str(ROOT/'src'),
            str(ROOT/'test/fixtures/regulator_settings_stm32_fixed.cpp'),'-o',str(binary)],
            text=True,capture_output=True,timeout=45)
        if result.returncode:
            raise AssertionError(result.stdout+result.stderr)
        cls.stm32_fixed = binary

    def test_fixed_stm32wl_driver_defers_to_ordinary_cli_without_hardware_or_storage_work(self):
        result = subprocess.run([str(self.stm32_fixed)], text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_fixed_stm32wl_guard_must_be_revisited_if_current_classes_gain_selection(self):
        # These are the classes excluded by the exact platform/driver guard.
        # A future implementation must remove or narrow that guard as it adds
        # coordinated shared-supply support, rather than silently hiding it.
        paths = [ROOT/'src/helpers/stm32/STM32Board.h',
            ROOT/'src/helpers/radiolib/CustomSTM32WLx.h',
            ROOT/'src/helpers/radiolib/CustomSTM32WLxWrapper.h']
        for variant in (ROOT/'variants').glob('*/platformio.ini'):
            if 'extends = stm32_base' in variant.read_text():
                paths.extend(variant.parent.glob('*.h'))
                paths.extend(variant.parent.glob('*.cpp'))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(path.read_text(), r'\b(?:mcuRegulatorModes|regulatorModes)\s*\(')

    def scenario(self, scenario):
        for platform, binary in self.binaries.items():
            if platform == 'no_retention':
                continue
            with self.subTest(platform=platform, scenario=scenario):
                result = subprocess.run([str(binary),scenario],text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_selections_save_both_domains_without_altering_active_hardware(self):
        self.scenario('select')

    def test_three_crashes_at_fifty_percent_latch_both_domains_across_cold_boot(self):
        self.scenario('fallback')

    def test_domain_choices_and_fallbacks_are_independent(self):
        self.scenario('domains')

    def test_below_fifty_missing_stale_and_outlier_battery_readings_do_not_count(self):
        self.scenario('battery')

    def test_intentional_reset_breaks_streak_and_preserves_unsaved_fallback(self):
        self.scenario('clean')

    def test_short_writes_open_and_rename_failures_recover_last_committed_settings(self):
        self.scenario('atomic')

    def test_crc_length_validation_and_backup_recovery(self):
        self.scenario('corruption')

    def test_unsupported_dcdc_is_rejected_without_touching_either_regulator(self):
        self.scenario('unsupported')

    def test_command_prefix_boundaries_syntax_and_bounded_reply(self):
        self.scenario('bounds')

    def test_actual_esp_and_pico_reset_classification(self):
        self.scenario('platform')

    def test_five_healthy_minutes_break_old_crash_streak(self):
        self.scenario('healthy')

    def test_failed_fallback_save_retries_and_then_survives_power_loss(self):
        self.scenario('retry')

    def test_missing_primary_store_rejects_persistence_without_live_mode_changes(self):
        self.scenario('no_store')

    def test_failed_radio_initialization_is_not_counted_as_running_dcdc(self):
        self.scenario('failed_radio')

    def test_missing_rtc_retention_disables_automatic_recovery(self):
        result = subprocess.run([str(self.binaries['no_retention']),'platform_unsupported'],
            text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_four_roles_initialize_once_before_first_radio_and_service_loop(self):
        for role in ('companion_radio','simple_repeater','simple_room_server','simple_sensor'):
            with self.subTest(role=role):
                source = (ROOT/f'examples/{role}/main.cpp').read_text()
                setup = method(source,'\nvoid setup(')
                loop = method(source,'\nvoid loop(')
                hook = 'mesh::power::beginRegulatorSettings(board, radio_driver, '
                self.assertEqual(setup.count(hook),1)
                self.assertLess(setup.index('board.begin()'),setup.index(hook))
                self.assertLess(setup.index(hook),setup.index('radio_init()'))
                self.assertIn('beginRegulatorSettings(board, radio_driver, regulator_prefs_fs)',setup)
                if role == 'companion_radio':
                    self.assertIn('store.isVolatilePrimaryFS()',setup)
                else:
                    self.assertIn('if (volatile_primary_fs) regulator_prefs_fs = nullptr',setup)
                if role == 'simple_repeater':
                    self.assertEqual(loop.count('serviceCommandInterfaces()'),1)
                    loop = method(source,'serviceCommandInterfaces() {')
                self.assertEqual(loop.count('mesh::power::serviceRegulatorSettings()'),1)
                self.assertLess(loop.index('mesh::power::serviceRegulatorSettings()'),loop.index('board.loop()'))
        cli = (ROOT/'src/helpers/CommonCLI.cpp').read_text()
        self.assertIn('handleRegulatorCommand(command, reply, 157)',cli)
        other = (ROOT/'examples/kiss_modem/main.cpp').read_text()
        self.assertNotIn('beginRegulatorSettings(',other)


class RegulatorAPIDispatchTests(unittest.TestCase):
    def compile_api(self, flags, fixed, require_virtual=False):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('native C++ compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='mesh-regulator-api-') as directory:
            folder = Path(directory)
            (folder/'Stream.h').write_text('#pragma once\nclass Stream {};\n')
            binary = folder/'test'
            command = [compiler,'-std=c++17','-O2','-Wall','-Wextra','-Werror',
                '-Wno-unused-parameter','-Wno-reorder',*SANITIZER_FLAGS,*flags,
                f'-DEXPECT_FIXED_REGULATOR_API={int(fixed)}',
                '-I',str(folder),'-I',str(ROOT/'src'),
                str(ROOT/'test/fixtures/regulator_api_dispatch.cpp'),'-o',str(binary)]
            if require_virtual:
                command.append('-DREQUIRE_VIRTUAL_REGULATOR_API=1')
            result = subprocess.run(command,text=True,capture_output=True,timeout=45)
            if require_virtual and fixed:
                self.assertNotEqual(result.returncode,0)
                for method in ('mcuRegulatorModes','defaultMcuRegulatorMode',
                        'applyMcuRegulator','activeMcuRegulatorMode','regulatorModes',
                        'defaultRegulatorMode','configureBootRegulator','activeRegulatorMode'):
                    self.assertIn(method,result.stderr)
            else:
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                result = subprocess.run([str(binary)],text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_fixed_stm32wl_base_defaults_are_not_overridden(self):
        self.compile_api(['-DSTM32_PLATFORM=1','-DRADIO_CLASS=CustomSTM32WLx'],True)

    def test_all_eight_fixed_stm32wl_regulator_methods_reject_override_annotations(self):
        self.compile_api(['-DSTM32_PLATFORM=1','-DRADIO_CLASS=CustomSTM32WLx'],True,True)

    def test_other_platforms_and_future_stm32_radios_keep_all_eight_virtual_methods(self):
        for flags in ([],['-DESP32=1','-DRADIO_CLASS=CustomSTM32WLx'],
                ['-DNRF52_PLATFORM=1','-DRADIO_CLASS=CustomSX1262'],
                ['-DRP2040_PLATFORM=1','-DRADIO_CLASS=CustomSX1262'],
                ['-DSTM32_PLATFORM=1','-DRADIO_CLASS=CustomSX1262'],
                ['-DSTM32_PLATFORM=1']):
            with self.subTest(flags=flags):
                self.compile_api(flags,False,True)


if __name__ == '__main__':
    unittest.main()
