"""Exercise RTC guards and real terminal GPIO retention on ESP32 families."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "src/helpers/ESP32GpioHold.h"
SDK = r'''
#pragma once
#include <cassert>
#include <cstdint>
using gpio_num_t=int;
using esp_err_t=int;
constexpr int ESP_OK=0;
constexpr int ESP_ERR_INVALID_ARG=258;
constexpr int GPIO_MODE_OUTPUT=2, GPIO_PULLUP_DISABLE=0;
constexpr int GPIO_PULLDOWN_DISABLE=0, GPIO_INTR_DISABLE=0;
struct gpio_config_t { uint64_t pin_bit_mask; int mode,pull_up_en,pull_down_en,intr_type; };
inline unsigned held=0,released=0,deinitialized=0,digital_held=0;
inline int api_result=0;
struct Pad { int latch=0; bool output=false, hold=false; int retained=-1; };
inline Pad pads[64];
inline bool deep_hold=false;
inline bool require_configured_release=false;
inline unsigned deep_hold_en_calls=0, deep_hold_dis_calls=0;
inline bool valid_output(int pin) {
#if CHIP_S3
  return pin>=0 && pin<=48 && pin!=46;
#else
  return pin>=0 && pin<=33;
#endif
}
#define GPIO_IS_VALID_OUTPUT_GPIO(pin) valid_output(pin)
inline int physical(int pin) { return pads[pin].hold ? pads[pin].retained : (pads[pin].output ? pads[pin].latch : -1); }
inline int gpio_set_level(int pin,unsigned level) { assert(valid_output(pin)); pads[pin].latch=level; return api_result; }
inline int gpio_config(const gpio_config_t* config) {
  assert(config->mode==GPIO_MODE_OUTPUT && config->pull_up_en==0 && config->pull_down_en==0 && config->intr_type==0);
  if (api_result) return api_result;
  for(int pin=0;pin<64;++pin) if(config->pin_bit_mask&(1ULL<<pin)) {
    assert(valid_output(pin)); pads[pin].output=true;
  }
  return ESP_OK;
}
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
inline bool rtc_gpio_is_valid_gpio(int pin) {
#if CHIP_S3
  return pin>=0 && pin<=21;
#else
  return pin==0 || pin==2 || pin==4 || (pin>=12 && pin<=15)
    || (pin>=25 && pin<=27) || (pin>=32 && pin<=39);
#endif
}
inline int rtc_gpio_hold_en(int pin) { assert(pin>=0 && rtc_gpio_is_valid_gpio(pin)); ++held; if(!api_result) { pads[pin].retained=physical(pin); pads[pin].hold=true; } return api_result; }
inline int rtc_gpio_hold_dis(int pin) { assert(pin>=0 && rtc_gpio_is_valid_gpio(pin)); assert(!require_configured_release || !pads[pin].hold || pads[pin].output); ++released; if(!api_result) pads[pin].hold=false; return api_result; }
inline int rtc_gpio_deinit(int pin) { assert(pin>=0 && rtc_gpio_is_valid_gpio(pin)); ++deinitialized; return api_result; }
#endif
inline int gpio_hold_en(int pin) {
  assert(valid_output(pin));
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  if(rtc_gpio_is_valid_gpio(pin)) return rtc_gpio_hold_en(pin);
#endif
  ++digital_held;
  if(!api_result) { pads[pin].retained=physical(pin); pads[pin].hold=true; }
  return api_result;
}
inline int gpio_hold_dis(int pin) {
  assert(valid_output(pin));
  assert(pads[pin].output); // Mode MUST be restored before release.
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  if(rtc_gpio_is_valid_gpio(pin)) return rtc_gpio_hold_dis(pin);
#endif
  if(!api_result) pads[pin].hold=false;
  return api_result;
}
inline void gpio_deep_sleep_hold_en() { deep_hold=true; ++deep_hold_en_calls; }
inline void gpio_deep_sleep_hold_dis() { deep_hold=false; ++deep_hold_dis_calls; }
inline void simulate_deep_sleep() {
  for(int pin=0;pin<64;++pin) {
    bool rtc=false;
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
    rtc=rtc_gpio_is_valid_gpio(pin);
#endif
    if(!rtc && !deep_hold) { pads[pin].output=false; pads[pin].retained=-1; }
  }
}
'''
HARNESS = r'''
#include <helpers/ESP32GpioHold.h>
#include <initializer_list>
int main() {
  for(int pin : {-1,0,2,4,7,8,15,18,33,42,43,47,48,64}) {
    bool valid=false;
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
    valid=pin>=0 && rtc_gpio_is_valid_gpio(pin);
#endif
    const unsigned before=held, release_before=released, deinit_before=deinitialized;
    assert(mesh::esp32::supportsRtcGpio(pin)==valid);
    assert(mesh::esp32::holdRtcGpioIfSupported(pin)==ESP_OK);
    assert(mesh::esp32::releaseRtcGpioIfSupported(pin)==ESP_OK);
    assert(mesh::esp32::deinitRtcGpioIfSupported(pin)==ESP_OK);
    assert(held==before+valid && released==release_before+valid && deinitialized==deinit_before+valid);
  }
  assert(digital_held==0); // No broad digital retention behavior was invented.
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
  api_result=99;
  assert(mesh::esp32::holdRtcGpioIfSupported(4)==99);
  assert(mesh::esp32::releaseRtcGpioIfSupported(4)==99);
  assert(mesh::esp32::deinitRtcGpioIfSupported(4)==99);
#endif
}
'''


class GpioHoldTests(unittest.TestCase):
    def compile_run(self, source, *, caps=1, s3=0, defines=(), broken=False, legacy_guard=False, helper_mutation=None, extra_source=None, files=None):
        compiler=shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="esp32-rtc-guard-") as directory:
            work=Path(directory)
            (work/"driver").mkdir()
            (work/"driver/rtc_io.h").write_text(SDK)
            (work/"driver/gpio.h").write_text('#include "rtc_io.h"\n')
            for name,contents in (files or {}).items():
                path=work/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(contents)
            if broken or legacy_guard or helper_mutation:
                (work/"helpers").mkdir()
                helper = HEADER.read_text()
                if broken:
                    helper = helper.replace("if (supportsRtcGpio(pin))", "if (true)")
                if legacy_guard:
                    guard = "#if defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) && SOC_RTCIO_INPUT_OUTPUT_SUPPORTED"
                    self.assertIn(guard, helper)
                    helper = helper.replace(guard, "#if !defined(SOC_RTCIO_INPUT_OUTPUT_SUPPORTED) || SOC_RTCIO_INPUT_OUTPUT_SUPPORTED")
                if helper_mutation:
                    helper = helper_mutation(helper)
                (work/"helpers/ESP32GpioHold.h").write_text(helper)
            cpp,binary=work/"test.cpp",work/"test"
            cpp.write_text(source)
            extra=[]
            if extra_source:
                other=work/"other.cpp"
                other.write_text(extra_source)
                extra=[str(other)]
            result=subprocess.run([compiler,"-std=c++17","-Wall","-Wextra","-Werror", "-I",str(work),"-I",str(ROOT/"src"),
                *([] if caps is None else [f"-DSOC_RTCIO_INPUT_OUTPUT_SUPPORTED={caps}"]),
                f"-DCHIP_S3={s3}",*("-D"+flag for flag in defines),str(cpp),*extra,"-o",str(binary)],capture_output=True,text=True)
            if legacy_guard:
                self.assertNotEqual(result.returncode, 0, "old undefined-capability guard compiled without RTC APIs")
                self.assertIn("not declared", result.stderr)
                return
            self.assertEqual(result.returncode,0,result.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True)
            if broken or helper_mutation:
                self.assertNotEqual(ran.returncode,0,"negative control concealed unsupported RTC call")
            else:
                self.assertEqual(ran.returncode,0,ran.stderr)

    def test_chip_specific_and_absent_rtc_apis_no_global_hold(self):
        for caps,s3 in ((1,0),(1,1),(0,0),(None,0)):
            with self.subTest(caps=caps,s3=s3):
                self.compile_run(HARNESS,caps=caps,s3=s3)
        self.compile_run(HARNESS,broken=True)
        # Installed ESP-IDF4.4C3 leaves the macro UNDEFINED and omits RTC API
        # declarations entirely, rather than defining capability0.
        self.compile_run(HARNESS,caps=None,legacy_guard=True)

    def test_real_shutdown_latches_known_output_without_arming_global_hold(self):
        shutdown=body((ROOT/"src/helpers/ESP32Board.cpp").read_text(),"void ESP32Board::shutdownPeripherals()")
        code=r'''
#include <helpers/ESP32GpioHold.h>
#include <cstddef>
#define HIGH 1
static bool powered_off=false,gps_off=false,flushed=false;
void delay(unsigned ms) { assert(ms==100); }
struct Radio { void powerOff(){powered_off=true;} } radio_driver;
struct GPS { void stop(){gps_off=true;} } gps;
struct Sensors { GPS* getLocationProvider(){return &gps;} void shutdownGpsTransportForDeepSleep(){assert(gps_off);} } sensors;
struct SerialPort { void flush(){flushed=true;} } Serial;
class ESP32Board { public: void shutdownPeripherals(); };
@SHUTDOWN@
int main(){ ESP32Board board; board.shutdownPeripherals();
 assert(powered_off && gps_off && flushed);
 const bool rtc=mesh::esp32::supportsRtcGpio(P_LORA_NSS);
 assert(held==rtc && digital_held==!rtc);
 assert(pads[P_LORA_NSS].output && physical(P_LORA_NSS)==HIGH);
 assert(!deep_hold && deep_hold_en_calls==0);
}
'''.replace("@SHUTDOWN@",shutdown)
        for caps,s3,definitions in ((1,0,("P_LORA_NSS=18",)),(1,1,("P_LORA_NSS=8",)),
                (1,1,("P_LORA_NSS=43",)),(0,0,("P_LORA_NSS=18","CONFIG_IDF_TARGET_ESP32C3=1")),
                (None,0,("P_LORA_NSS=18","CONFIG_IDF_TARGET_ESP32C3=1")),
                (0,0,("P_LORA_NSS=18","CONFIG_IDF_TARGET_ESP32C6=1"))):
            with self.subTest(definitions=definitions):
                self.compile_run(code,caps=caps,s3=s3,defines=definitions)

    def test_terminal_hold_retains_output_and_boot_release_preloads_before_unhold(self):
        code=r'''
#include <helpers/ESP32GpioHold.h>
int main() {
  using namespace mesh::esp32;
  resetTerminalDeepSleepHolds();
  assert(!deep_hold && !terminalDigitalHoldPending());
  assert(holdOutputForDeepSleep(-1,1)==ESP_ERR_INVALID_ARG);
  assert(holdOutputForDeepSleep(64,1)==ESP_ERR_INVALID_ARG);
  assert(holdOutputForDeepSleep(34,1)==@PIN34_RESULT@);
  resetTerminalDeepSleepHolds();
  assert(holdOutputForDeepSleep(P_LORA_NSS,1)==ESP_OK);
  assert(physical(P_LORA_NSS)==1 && pads[P_LORA_NSS].output);
  assert(!deep_hold); // No global hold in runtime/light sleep.
  commitTerminalDeepSleepHolds();
  assert(deep_hold==!supportsRtcGpio(P_LORA_NSS));
  simulate_deep_sleep();
  assert(physical(P_LORA_NSS)==1); // Negative control detects lost non-RTC retention.
  pads[P_LORA_NSS].latch=0; pads[P_LORA_NSS].output=false; // CPU reset.
  resetTerminalDeepSleepHolds();
  assert(!deep_hold);
  assert(releaseOutputAfterDeepSleep(P_LORA_NSS,1)==ESP_OK);
  assert(!pads[P_LORA_NSS].hold && physical(P_LORA_NSS)==1);
  // Neither an invalid pin nor failed setup is allowed to request auto-hold.
  resetTerminalDeepSleepHolds(); api_result=77;
  assert(holdOutputForDeepSleep(P_LORA_NSS,0)==77);
  commitTerminalDeepSleepHolds(); assert(!deep_hold);
}
'''
        for caps,s3,pin in ((1,0,18),(1,1,43),(1,1,8),(None,0,18),(0,0,18)):
            with self.subTest(caps=caps,s3=s3,pin=pin):
                self.compile_run(code.replace('@PIN34_RESULT@','ESP_OK' if s3 else 'ESP_ERR_INVALID_ARG'),
                                 caps=caps,s3=s3,defines=(f'P_LORA_NSS={pin}',))
        # gpio_hold_en alone is NOT a physical non-RTC deep-sleep fix.
        self.compile_run(code.replace('@PIN34_RESULT@','ESP_ERR_INVALID_ARG'),defines=('P_LORA_NSS=18',),
            helper_mutation=lambda text: text.replace('if (terminalDigitalHoldPending()) gpio_deep_sleep_hold_en();',''))

    def test_pending_terminal_hold_is_shared_between_board_and_fem_translation_units(self):
        self.compile_run(r'''
#include <helpers/ESP32GpioHold.h>
void femShutdown();
int main(){ mesh::esp32::resetTerminalDeepSleepHolds(); femShutdown();
 assert(!deep_hold); mesh::esp32::commitTerminalDeepSleepHolds(); assert(deep_hold);
}
''',s3=1,extra_source=r'''
#include <helpers/ESP32GpioHold.h>
void femShutdown(){ assert(mesh::esp32::holdOutputForDeepSleep(42,1)==ESP_OK); }
''')

    def test_only_terminal_sleep_commits_after_virtual_shutdown_and_wake_setup(self):
        production=(ROOT/'src/helpers/ESP32Board.cpp').read_text()
        sleep=body(production,'void ESP32Board::enterDeepSleep(uint32_t secs)')
        self.compile_run(r'''
#include <helpers/ESP32GpioHold.h>
#include <cstdint>
constexpr int ESP_SLEEP_WAKEUP_ALL=99;
static bool shut=false,wake_cleared=false,timer_set=false,slept=false;
void esp_sleep_disable_wakeup_source(int value){assert(shut && !deep_hold && value==99); wake_cleared=true;}
void esp_sleep_enable_timer_wakeup(uint64_t value){assert(wake_cleared && !deep_hold && value==5000000); timer_set=true;}
void esp_deep_sleep_start(){assert(shut && timer_set && deep_hold); slept=true;}
class ESP32Board { public: virtual void shutdownPeripherals(){}; void enterDeepSleep(uint32_t secs); };
class FEMBoard:public ESP32Board { void shutdownPeripherals() override {
 assert(!deep_hold); mesh::esp32::holdOutputForDeepSleep(43,1);
 mesh::esp32::holdOutputForDeepSleep(42,1); shut=true;
} };
@SLEEP@
int main(){FEMBoard board; board.enterDeepSleep(5); assert(slept);}
'''.replace('@SLEEP@',sleep),s3=1)
        header=(ROOT/'src/helpers/ESP32Board.h').read_text()
        light='\n'.join(body(header, signature) for signature in (
            'void sleep(uint32_t secs) override',
            'void sleepMilliseconds(uint32_t millisecs) override',
            'void sleepMicroseconds(uint64_t wake_us, bool require_timer = false)'))
        self.assertNotIn('commitTerminalDeepSleepHolds',light)
        for folder in (ROOT/'src',ROOT/'variants'):
            for path in folder.rglob('*'):
                if path.suffix not in ('.cpp','.h') or path in (HEADER,ROOT/'src/helpers/ESP32Board.cpp'):
                    continue
                self.assertNotIn('commitTerminalDeepSleepHolds(',path.read_text(),str(path))

    def test_actual_boot_restores_radio_output_before_runtime_initialization(self):
        header=(ROOT/'src/helpers/ESP32Board.h').read_text()
        boot=body(header,'void begin()')
        prefix=boot[:boot.index('// Arduino\'s early init hook')]
        prefix=prefix[prefix.index('{')+1:]
        self.compile_run(r'''
#include <helpers/ESP32GpioHold.h>
#define HIGH 1
class Board{public:void begin(){ @BOOT@ }};
int main(){using namespace mesh::esp32;
 holdOutputForDeepSleep(P_LORA_NSS,1); commitTerminalDeepSleepHolds();
 pads[P_LORA_NSS].latch=0; pads[P_LORA_NSS].output=false;
 Board board; board.begin(); assert(!deep_hold && !pads[P_LORA_NSS].hold && physical(P_LORA_NSS)==1);
}
'''.replace('@BOOT@',prefix),s3=1,defines=('P_LORA_NSS=43',))

    def test_g3_fem_real_startup_and_terminal_lna_disable_both_slots(self):
        variant=ROOT/'variants/station_g3_esp32'
        shutdown=body((variant/'StationG3Board.cpp').read_text(),'void StationG3Board::shutdownPeripherals()')
        code=r'''
#include <helpers/ESP32GpioHold.h>
#include "LoRaFEMControl.h"
#include "StationG3RadioSlot.h"
static bool radio_off=false;
class ESP32Board{public:void shutdownPeripherals(){radio_off=true;mesh::esp32::holdOutputForDeepSleep(P_LORA_NSS,HIGH);}};
class StationG3Board:public ESP32Board{public:LoRaFEMControl loRaFEMControl;void shutdownPeripherals();};
@SHUTDOWN@
int main(){ using namespace mesh::esp32;
 station_g3::slotState().candidate=@SLOT@;
 const int active_lna=station_g3::activePins().lna;
 const int inactive_lna=station_g3::pinsForSlot(@SLOT@==1 ? 2:1).lna;
 StationG3Board board; resetTerminalDeepSleepHolds();
 releaseOutputAfterDeepSleep(P_LORA_NSS,HIGH); board.loRaFEMControl.init();
 assert(physical(P_PA1_EN)==LOW && physical(active_lna)==HIGH && physical(inactive_lna)==HIGH);
 // The selector enables RX only after a physical radio has been found.
 board.loRaFEMControl.setRxModeEnable();
 assert(physical(active_lna)==LOW && physical(inactive_lna)==HIGH);
 board.loRaFEMControl.setPAGainEnable(true); board.loRaFEMControl.setTxModeEnable();
 assert(physical(P_PA1_EN)==HIGH && physical(active_lna)==HIGH && physical(inactive_lna)==HIGH);
 board.loRaFEMControl.setRxModeEnable(); assert(physical(active_lna)==LOW && physical(inactive_lna)==HIGH);
 board.shutdownPeripherals(); assert(radio_off && !deep_hold);
 assert(pads[active_lna].output && pads[active_lna].hold);
 assert(pads[inactive_lna].output && pads[inactive_lna].hold);
 assert(physical(P_PA1_EN)==LOW && physical(active_lna)==HIGH && physical(inactive_lna)==HIGH);
 commitTerminalDeepSleepHolds(); simulate_deep_sleep();
 assert(physical(active_lna)==HIGH && physical(inactive_lna)==HIGH && physical(P_LORA_NSS)==HIGH);
 // Reset has changed backing latches/modes; startup must configure before release.
 pads[P_PA1_EN].output=false; pads[active_lna].output=false; pads[inactive_lna].output=false;
 resetTerminalDeepSleepHolds(); releaseOutputAfterDeepSleep(P_LORA_NSS,HIGH);
 board.loRaFEMControl.init();
 assert(!pads[P_PA1_EN].hold && !pads[active_lna].hold && !pads[inactive_lna].hold);
 assert(physical(P_PA1_EN)==HIGH && physical(active_lna)==HIGH && physical(inactive_lna)==HIGH && !deep_hold);
 board.loRaFEMControl.setRxModeEnable();
 assert(physical(active_lna)==LOW && physical(inactive_lna)==HIGH);
}
'''.replace('@SHUTDOWN@',shutdown)
        files={'LoRaFEMControl.h':(variant/'LoRaFEMControl.h').read_text(),
            'StationG3RadioSlot.h':(variant/'StationG3RadioSlot.h').read_text(),
            'Arduino.h':'#pragma once\n#include <driver/rtc_io.h>\ninline void digitalWrite(int pin,int level){ gpio_set_level(pin,level); }\n'}
        # Both historical build-macro configurations must drive either runtime
        # slot, not merely the one which happened to be compiled in.
        for nss,lna in ((11,10),(43,42)):
            for slot in (1,2):
                self.compile_run(code.replace('@SLOT@',str(slot)),s3=1,
                    defines=(f'P_LORA_NSS={nss}',f'P_PRIMARY_LNA_EN={lna}',
                    'P_PA1_EN=9','P_PA1_EN_ACTIVE=1','P_PRIMARY_LNA_EN_ACTIVE=0','HIGH=1','LOW=0'),
                    extra_source=(variant/'LoRaFEMControl.cpp').read_text(),files=files)

    def test_terminal_gps_uart_release_respects_owner_and_leaves_i2c_alone(self):
        source=(ROOT/'src/helpers/sensors/EnvironmentSensorManager.cpp').read_text()
        hook=body(source,
            'void EnvironmentSensorManager::shutdownGpsTransportForDeepSleep()')
        stop=body(source,'void EnvironmentSensorManager::stopGpsSerialTransport()')
        code=r'''
#include <helpers/ESP32GpioHold.h>
#define INPUT 0
static unsigned released_pads=0;
struct Port { bool installed=false;unsigned ends=0;explicit operator bool()const{return installed;}
 void end(){assert(installed);installed=false;++ends;} } Serial1;
void pinMode(int pin,int mode){assert(!Serial1.installed && mode==INPUT);++released_pads;pads[pin].output=false;}
class EnvironmentSensorManager {public:bool gps_serial_transport=false,gps_serial_transport_blocked=false;
 bool gps_serial_open=false;
 void shutdownGpsTransportForDeepSleep();void stopGpsSerialTransport();};
#if ENV_INCLUDE_GPS
@STOP@
#endif
@HOOK@
int main(){ EnvironmentSensorManager sensors;
 // I2C/no-owned GPS: an unrelated serial user is untouched.
 Serial1.installed=true;sensors.shutdownGpsTransportForDeepSleep();assert(Serial1.installed && !Serial1.ends && !released_pads);
 sensors.gps_serial_transport=true;sensors.gps_serial_transport_blocked=true;
 sensors.shutdownGpsTransportForDeepSleep();assert(Serial1.installed && !Serial1.ends && !released_pads);
 sensors.gps_serial_transport_blocked=false;sensors.gps_serial_open=true;
 sensors.shutdownGpsTransportForDeepSleep();
#if EXPECT_RELEASE
 assert(!Serial1.installed && Serial1.ends==1 && released_pads==EXPECT_PADS);
 sensors.shutdownGpsTransportForDeepSleep();assert(Serial1.ends==1 && released_pads==EXPECT_PADS);
#else
 assert(Serial1.installed && !Serial1.ends && !released_pads);
#endif
}
'''.replace('@HOOK@',hook).replace('@STOP@',stop)
        for definitions in (
            ('ESP32_PLATFORM=1','ENV_INCLUDE_GPS=1','PIN_GPS_TX=18','PIN_GPS_RX=17','EXPECT_RELEASE=1','EXPECT_PADS=2'),
            ('ESP32_PLATFORM=1','ENV_INCLUDE_GPS=0','PIN_GPS_TX=18','PIN_GPS_RX=17','EXPECT_RELEASE=0'),
            ('NRF52_PLATFORM=1','ENV_INCLUDE_GPS=1','PIN_GPS_TX=18','PIN_GPS_RX=17','EXPECT_RELEASE=1','EXPECT_PADS=2'),
            ('ESP32_PLATFORM=1','ENV_INCLUDE_GPS=1','PIN_GPS_TX=-1','PIN_GPS_RX=17','EXPECT_RELEASE=1','EXPECT_PADS=1'),
            ('ESP32_PLATFORM=1','ENV_INCLUDE_GPS=1','EXPECT_RELEASE=1','EXPECT_PADS=0'),
            ('NRF52_PLATFORM=1','ENV_INCLUDE_GPS=0','PIN_GPS_TX=-1','PIN_GPS_RX=-1','EXPECT_RELEASE=0'),
        ):
            self.compile_run(code,defines=definitions)

    def test_w12_fem_rails_restore_known_output_before_releasing_terminal_hold(self):
        source=(ROOT/'variants/meshnology_w12/MeshnologyW12Board.cpp').read_text()
        begin=body(source,'void MeshnologyW12Board::begin()')
        prefix=begin[:begin.index('    prepareRadioFrequency(')]+'}\n'
        code=r'''
#include <helpers/ESP32GpioHold.h>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define P_LORA_LF_PA_POWER 4
#define P_LORA_HF_PA_POWER 3
#define PIN_ADC_CTRL 2
void pinMode(int pin,int mode){assert(mode==OUTPUT);pads[pin].output=true;}
void digitalWrite(int pin,int level){gpio_set_level(pin,level);}
class ESP32Board {public:void begin(){mesh::esp32::resetTerminalDeepSleepHolds();}};
class MeshnologyW12Board:public ESP32Board {public:void begin();};
@BEGIN@
int main(){ using namespace mesh::esp32;
 holdOutputForDeepSleep(4,LOW);holdOutputForDeepSleep(3,LOW);
 pads[4].output=false;pads[3].output=false;require_configured_release=true;
 MeshnologyW12Board board;board.begin();
 assert(!pads[3].hold && !pads[4].hold && physical(3)==LOW && physical(4)==LOW);
 assert(!deep_hold && !terminalDigitalHoldPending());
}
'''
        self.compile_run(code.replace('@BEGIN@',prefix),s3=1)
        # The prior release-before-pinMode sequence must be caught.
        broken=prefix.replace('releaseOutputAfterDeepSleep((gpio_num_t)P_LORA_LF_PA_POWER, LOW)',
            'releaseRtcGpioIfSupported((gpio_num_t)P_LORA_LF_PA_POWER)')
        self.compile_run(code.replace('@BEGIN@',broken),s3=1,helper_mutation=lambda text:text)

    def test_all_production_hold_release_deinit_sites_use_capability_wrapper(self):
        # No board or FEM may keep using unsupported RTC calls behind the fix.
        for folder in (ROOT/"src",ROOT/"variants"):
            for path in folder.rglob("*"):
                if path.suffix not in (".h",".cpp") or path==HEADER:
                    continue
                self.assertNotRegex(path.read_text(),r"\brtc_gpio_(?:hold_en|hold_dis|deinit)\s*\(",str(path))
        pins=(ROOT/"variants/station_g3_esp32/platformio.ini").read_text()
        self.assertRegex(pins,r"P_LORA_NSS=43\b")
        self.assertRegex(pins,r"P_PRIMARY_LNA_EN=42\b")
        self.assertRegex((ROOT/"variants/lilygo_tbeam_SX1262/platformio.ini").read_text(),r"P_LORA_NSS=18\b")


if __name__ == "__main__":
    unittest.main()
