"""Keep unsupported hardware out of RXPS math without changing CLI readback."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

RADIO_STUB = r'''
#pragma once
#include <cstdint>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_INVALID_FREQUENCY -2
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_UNSUPPORTED -2
#define RADIOLIB_NC 0xffffffff
using RadioLibIrqFlags_t = uint32_t;
struct ChannelScanConfig_t;
class PhysicalLayer {
public:
 int setOutputPower(int8_t){return 0;}
 int setFrequency(float){return 0;}
 int standby(){return 0;}
 int startReceive(){return 0;}
 int transmitDirect(){return 0;}
 int setPreambleLength(uint16_t){return 0;}
 uint8_t randomByte(){return 0;}
};
'''

HARNESS = r'''
long random(long,long);
#include <helpers/radiolib/RadioLibWrappers.h>
#include <helpers/CLICommandUtils.h>
#include <cstdio>
#include <cstring>
#define WRAPPER_CLASS RadioLibWrapper
static_assert(!RadioLibWrapper::SupportsRxPowerSaving, "actual unsupported wrapper capability");
struct NodePrefs {
 uint8_t rx_powersaving_enabled=0,rx_ps_level=5,rx_ps_preamble=16,sf=8;
 uint32_t rx_ps_rx_us=12345,rx_ps_sleep_us=67890;
 float bw=62.5f;
};
struct Callbacks {
 unsigned calls=0;
 bool supportsRxPowerSaving() const { return false; }
 bool setRxPowerSaving(bool on,uint32_t,uint32_t){++calls;return !on;}
};
struct CommonCLI {
 NodePrefs* _prefs; Callbacks* _callbacks; unsigned saves=0;
 CommonCLI(NodePrefs& p,Callbacks& c):_prefs(&p),_callbacks(&c){}
 static constexpr uint8_t RxPowerSaving = 2;
 uint8_t _rx_settings_pending = 0;
 uint32_t _rx_settings_retry_at = 0;
 mesh::RadioParamApplyResult applyRxSetting(uint8_t) {
   return _callbacks->setRxPowerSaving(_prefs->rx_powersaving_enabled,
       _prefs->rx_ps_rx_us, _prefs->rx_ps_sleep_us)
       ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
 }
 bool saveRxSetting(uint8_t, mesh::RadioParamApplyResult result, char*) {
   if (result == mesh::RadioParamApplyResult::FAILED) return false;
   ++saves; return true;
 }
 void restoreRxSetting(uint8_t, mesh::RadioParamApplyResult, uint8_t, uint32_t) {}
 static bool calculateRxPowerSavingLevel(uint32_t,uint8_t,float,uint32_t,uint32_t*,uint32_t*);
 static bool recalculateRxPowerSavingFromLevel(NodePrefs*);
 void savePrefs(){++saves;}
 void set(const char* config,char* reply){@SETTER@}
 void getConfig(const char* config,char* reply){@GETTER@}
};
@CALCULATOR@
@RECALCULATOR@
@ADVICE@
#define CHECK(value,message) do { if(!(value)){std::fprintf(stderr,"%s\n",message);return 1;} } while(0)
int main(){
 NodePrefs prefs; Callbacks callbacks; CommonCLI cli(prefs,callbacks); char reply[160]={};
 uint32_t rx=1,sleep=2;
 CHECK(!cli.calculateRxPowerSavingLevel(8,8,62.5f,16,&rx,&sleep),"unsupported model must not run");
 CHECK(rx==1&&sleep==2,"unsupported model must preserve caller data");
 CHECK(!cli.recalculateRxPowerSavingFromLevel(&prefs),"unsupported retune must not run");
 CHECK(prefs.rx_ps_rx_us==12345&&prefs.rx_ps_sleep_us==67890,"manual periods must survive unsupported retune");
 std::strcpy(reply,"OK");prefs.rx_powersaving_enabled=1;
 appendRxPowerSavingAdjustmentNote(reply,&prefs,5,500);
 CHECK(!std::strcmp(reply,"OK"),"unsupported hardware must not advise timing adjustments");
 prefs.rx_powersaving_enabled=0;
 cli.set("radio.rxps max",reply);
 CHECK(!std::strcmp(reply,"ERROR: RX powersaving unsupported"),"enable must report the actual unsupported error");
 CHECK(!callbacks.calls&&!cli.saves,"unsupported enable must not touch hardware or saved state");
 cli.set("radio.rxps off",reply);
 CHECK(callbacks.calls==1&&cli.saves==1,"off must remain accepted");
 CHECK(prefs.rx_ps_level==5&&prefs.rx_ps_preamble==16,"off must preserve configured level and preamble");
 cli.getConfig("radio.rxps.config",reply);
 CHECK(!std::strcmp(reply,"> off,level=5,preamble=16,rx=12345,sleep=67890"),"legacy config readback must remain exact");
 CHECK(rxPowerSavingPreambleForParams(7,500)==32,"wire preamble must stay unchanged");
 CHECK(rxPowerSavingPreambleForParams(6,500)==64,"fast wire preamble must stay unchanged");
 CHECK(rxPowerSavingPreambleForParams(5,500)==128,"fastest wire preamble must stay unchanged");
}
'''


class RxpsHardwareCapabilityTests(unittest.TestCase):
    def probe_repeater_constructor(self, standard, capability, remove_guard=False):
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        start = source.index('  _prefs.rx_powersaving_enabled = DEFAULT_RXPS_ENABLED ? 1 : 0;')
        end = source.index('\n  // bridge defaults', start)
        initialization = source[start:end]
        if remove_guard:
            initialization = initialization.replace('#ifdef WRAPPER_CLASS\n', '', 1)
            initialization = initialization.replace('#endif\n', '', 1)
        if capability is None:
            radio = '#include <helpers/esp32/ESPNOWRadio.h>\n'
            self.assertIn('extern ESPNOWRadio radio_driver;',
                          (ROOT / 'variants/generic_espnow/target.h').read_text())
            self.assertNotIn('WRAPPER_CLASS',
                             (ROOT / 'variants/generic_espnow/platformio.ini').read_text())
        else:
            radio = 'long random(long,long);\n#include <helpers/radiolib/RadioLibWrappers.h>\n'
            if capability:
                radio += ('struct SupportedWrapper : RadioLibWrapper {\n'
                          ' static constexpr bool SupportsRxPowerSaving = true;\n};\n'
                          '#define WRAPPER_CLASS SupportedWrapper\n')
            else:
                radio += '#define WRAPPER_CLASS RadioLibWrapper\n'
        code = radio + r'''
#include <helpers/radiolib/RXPowerSaving.h>
#include <cstdio>
struct Prefs {
 uint8_t rx_powersaving_enabled, rx_ps_level, rx_ps_preamble, sf;
 uint32_t rx_ps_rx_us, rx_ps_sleep_us;
 float bw;
};
struct Repeater {
 Prefs _prefs = {};
 Repeater() {
  _prefs.sf = 7;
  _prefs.bw = 62.5f;
@INITIALIZATION@
 }
};
#define CHECK(value) do { if (!(value)) return 1; } while (0)
int main() {
 Repeater repeater;
 const Prefs& prefs = repeater._prefs;
 CHECK(prefs.rx_powersaving_enabled == 1 && prefs.rx_ps_level == 5);
 CHECK(prefs.rx_ps_preamble == 16 && prefs.sf == 7 && prefs.bw == 62.5f);
@CHECK_TIMINGS@
}
'''
        if capability:
            timing_check = (' CHECK(prefs.rx_ps_rx_us != RX_POWERSAVING_DEFAULT_RX_US\n'
                            '       || prefs.rx_ps_sleep_us != RX_POWERSAVING_DEFAULT_SLEEP_US);\n'
                            ' CHECK(isValidRxPowerSavingPeriod(prefs.rx_ps_rx_us));\n'
                            ' CHECK(isValidRxPowerSavingPeriod(prefs.rx_ps_sleep_us));')
        else:
            timing_check = (' CHECK(prefs.rx_ps_rx_us == RX_POWERSAVING_DEFAULT_RX_US);\n'
                            ' CHECK(prefs.rx_ps_sleep_us == RX_POWERSAVING_DEFAULT_SLEEP_US);')
        code = code.replace('@INITIALIZATION@', initialization).replace('@CHECK_TIMINGS@', timing_check)
        with tempfile.TemporaryDirectory(prefix='repeater-rxps-constructor-') as folder:
            work = Path(folder)
            (work / 'RadioLib.h').write_text(RADIO_STUB)
            cpp, binary = work / 'test.cpp', work / 'test'
            cpp.write_text(code)
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=' + standard,
                '-pedantic-errors', '-Os', '-DNDEBUG', '-ffunction-sections', '-fdata-sections',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                '-DDEFAULT_RXPS_ENABLED=1', '-DDEFAULT_RXPS_LEVEL=5', '-DDEFAULT_RXPS_PREAMBLE=16',
                '-DLORA_SF=7', '-DLORA_BW=125', '-I' + str(work),
                '-isystem', str(ROOT / 'src'), '-isystem', str(ROOT / 'test/mocks'),
                str(cpp), '-Wl,--gc-sections', '-o', str(binary)],
                capture_output=True, text=True, timeout=30)
            if remove_guard:
                self.assertNotEqual(built.returncode, 0)
                self.assertIn('WRAPPER_CLASS', built.stderr)
                return
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repeater_constructor_keeps_wrapperless_and_lora_capabilities(self):
        for standard in ('c++11', 'c++17'):
            for capability in (None, False, True):
                with self.subTest(standard=standard, capability=capability):
                    self.probe_repeater_constructor(standard, capability)

    def test_missing_constructor_guard_reproduces_espnow_compile_failure(self):
        self.probe_repeater_constructor('c++11', None, remove_guard=True)

    def probe(self, standard, mutate=False):
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        setter = body(common, 'if (memcmp(config, "radio.rxps ", 11) == 0)')
        if mutate:
            guard = body(setter, 'if (!powersaving_supported && parsed.mode')
            setter = setter.replace(guard, '', 1)
        code = HARNESS.replace('@SETTER@', setter)
        code = code.replace('@GETTER@', body(common, 'if (strcmp(config, "radio.rxps.config") == 0)'))
        for marker, signature in (
                ('@CALCULATOR@', 'bool CommonCLI::calculateRxPowerSavingLevel('),
                ('@RECALCULATOR@', 'bool CommonCLI::recalculateRxPowerSavingFromLevel('),
                ('@ADVICE@', 'static void appendRxPowerSavingAdjustmentNote(')):
            code = code.replace(marker, body(common, signature))
        # The setter references the following definition, just as production does.
        code = code.replace('struct CommonCLI {',
            'static void appendRxPowerSavingAdjustmentNote(char*,const NodePrefs*,uint8_t,float);\nstruct CommonCLI {')
        with tempfile.TemporaryDirectory(prefix='rxps-hardware-capability-') as folder:
            work = Path(folder)
            (work / 'RadioLib.h').write_text(RADIO_STUB)
            cpp, binary = work / 'test.cpp', work / 'test'
            cpp.write_text(code)
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=' + standard,
                '-pedantic-errors', '-Os', '-ffunction-sections', '-fdata-sections',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
                '-DLORA_SF=7', '-DLORA_BW=125', '-I' + str(work),
                # Existing SDK headers contain inline variables accepted by
                # the firmware toolchain. Keep strict diagnostics on the
                # extracted production CLI bodies, outside those headers.
                '-isystem', str(ROOT / 'src'), '-isystem', str(ROOT / 'test/mocks'),
                str(cpp), '-Wl,--gc-sections', '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if mutate:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('enable must report the actual unsupported error', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                symbols = subprocess.run(['nm', '-C', str(binary)], capture_output=True, text=True, check=True).stdout
                self.assertNotIn(' calcRxPowerSavingLevelAtOrAbove(', symbols)
                self.assertNotIn(' recalcRxPowerSavingFromLevel(', symbols)

    def test_actual_base_capability_skips_model_and_preserves_commands(self):
        for standard in ('c++11', 'c++17'):
            with self.subTest(standard=standard):
                self.probe(standard)

    def test_missing_early_unsupported_error_is_detected(self):
        self.probe('c++11', mutate=True)


if __name__ == '__main__':
    unittest.main()
