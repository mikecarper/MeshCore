"""Exercise the patched driver functions with missing events and competing callers."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from framework_timeouts import is_esp32_2017, patched_copy

PACKAGES = Path(os.environ.get("PLATFORMIO_PACKAGES_DIR",
                Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio")) / "packages"))


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


class DriverTimeoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def patched(self, path):
        original = PACKAGES / path
        if path.startswith("framework-arduinoespressif32/"):
            suffix = Path(path).relative_to("framework-arduinoespressif32")
            for framework in sorted(PACKAGES.glob("framework-arduinoespressif32*")):
                if (framework / suffix).is_file() and is_esp32_2017(framework):
                    original = framework / suffix
                    break
            else:
                self.skipTest("requires the Arduino-ESP32 2.0.17 framework")
        if not original.exists():
            self.skipTest(f"requires PlatformIO package {original}")
        dest = self.directory / original.name
        patched_copy(original, ROOT / "scripts/framework_timeouts" / (original.name + ".patch"), dest)
        return dest.read_text()

    def run_cpp(self, program, *flags):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("requires host g++")
        source = self.directory / "test.cpp"
        source.write_text("#include <cassert>\n#include <cstdint>\n#include <cstddef>\n" + program)
        binary = self.directory / "test"
        subprocess.run([compiler, "-std=c++17", "-I", str(self.directory), "-I", str(ROOT / "src"),
                        *flags, str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], timeout=5, check=True)

    def test_button_press_release_and_stuck_across_tick_wrap(self):
        (self.directory / "Arduino.h").touch()
        (self.directory / "nrf_gpio.h").touch()
        self.run_cpp(r'''
enum {LOW, HIGH};
enum {NRF_GPIO_PIN_SENSE_LOW, NRF_GPIO_PIN_SENSE_HIGH};
using nrf_gpio_pin_pull_t = int;
unsigned g_ADigitalPinMap[] = {31};
uint32_t elapsed;
uint32_t millis() { return 0xffffff00u + elapsed; }
void delay(unsigned ms) { elapsed += ms; }
int held, releaseAt, sensed;
int digitalRead(unsigned pin) { assert(pin==0); return elapsed < unsigned(releaseAt) ? held : !held; }
void nrf_gpio_cfg_sense_input(unsigned pin, int pull, int sense) {
    assert(pin==31 && pull==2); sensed=sense;
}
#include <helpers/nrf52/ShutdownButton.h>
int main() {
    for (held=0; held<=1; ++held) {
        for (int release : {0, 50, 4000}) {
            elapsed=0; releaseAt=release;
            mesh::nrf52::armShutdownButton(0, held, 2);
            assert(elapsed == unsigned(release < 2000 ? release : 2000));
            assert(sensed == (release > 2000 ? !held : held));
        }
    }
}''', "-include", "initializer_list")

    def test_adc_aborts_each_missing_event_then_reuses_peripheral(self):
        src = self.patched("framework-arduinoadafruitnrf52/cores/nRF5/wiring_analog_nRF52.c")
        read = function(src, "static uint32_t analogRead_internal")
        calibration = function(src, "void analogCalibrateOffset")
        constants = "".join(f"#define {name} 0\n" for name in sorted(set(
            re.findall(r"\bSAADC_\w+", read))))
        self.run_cpp(constants + r'''
struct {
    unsigned ENABLE, RESOLUTION, EVENTS_STARTED, EVENTS_STOPPED, EVENTS_END, EVENTS_CALIBRATEDONE;
    unsigned TASKS_START, TASKS_SAMPLE, TASKS_STOP, TASKS_CALIBRATEOFFSET;
    struct { unsigned PSELN, PSELP, CONFIG; } CH[8];
    struct { unsigned PTR, MAXCNT; } RESULT;
} adc;
#define NRF_SAADC (&adc)
unsigned missing, polls;
void delayMicroseconds(unsigned) {
    ++polls;
    if(adc.TASKS_START && missing!=1) adc.EVENTS_STARTED=1;
    if(adc.TASKS_SAMPLE && missing!=2) adc.EVENTS_END=1;
    if(adc.TASKS_STOP && missing!=3) adc.EVENTS_STOPPED=1;
    if(adc.TASKS_CALIBRATEOFFSET && missing!=4) adc.EVENTS_CALIBRATEDONE=1;
}
int readResolution=12, saadcGain=0, saadcReference=0, saadcSampleTime=0, saadcBurst=0;
unsigned mapResolution(int, unsigned, unsigned) {return 123;}
''' + function(src, "static bool waitForAdcEvent") + function(src, "static void abortAdc")
        + read.replace("(uint32_t)&value", "(uint32_t)(uintptr_t)&value") + calibration + r'''
int main() {
    for(unsigned fault=1; fault<=4; ++fault) {
        adc={}; polls=0; missing=fault;
        if(fault==4) analogCalibrateOffset();
        else assert(analogRead_internal(1)==0);
        assert(!adc.ENABLE && !adc.RESULT.MAXCNT && !adc.RESULT.PTR);
        assert(polls >= 10000 && polls < 20010);
        missing=0;
        assert(analogRead_internal(1)==123);
    }
}''')

    def test_pico_fifo_uses_read_deadline_and_can_retry(self):
        src = self.patched("framework-arduinopico/pico-sdk/src/rp2_common/hardware_i2c/i2c.c")
        self.run_cpp(r'''
#define invalid_params_if(a,b) ((void)0)
enum { I2C_IC_DATA_CMD_RESTART_LSB=10, I2C_IC_DATA_CMD_STOP_LSB=9,
       I2C_IC_DATA_CMD_CMD_BITS=256, I2C_IC_RAW_INTR_STAT_TX_ABRT_BITS=64,
       I2C_IC_TX_ABRT_SOURCE_ABRT_7B_ADDR_NOACK_BITS=1, PICO_ERROR_GENERIC=-1, PICO_ERROR_TIMEOUT=-2 };
struct Hw {unsigned enable, tar, data_cmd, tx_abrt_source, raw_intr_stat, clr_tx_abrt;};
struct i2c_inst_t {Hw* hw; bool restart_on_next;};
struct timeout_state_t {int calls;};
using check_timeout_fn=bool (*)(timeout_state_t*, bool);
bool deadline(timeout_state_t* t, bool reset) {return !reset && ++t->calls==3;}
bool full=true;
bool i2c_get_write_available(i2c_inst_t*) {return !full;}
bool i2c_get_read_available(i2c_inst_t* i) {i->hw->data_cmd=42; return true;}
unsigned bool_to_bit(bool b) {return b;}
void tight_loop_contents() {}
''' + function(src, "static int i2c_read_blocking_internal") + r'''
int main() {
    uint8_t dst=11; Hw hw={}; i2c_inst_t bus{&hw,true}; timeout_state_t ts{};
    assert(i2c_read_blocking_internal(&bus,0x40,&dst,1,true,deadline,&ts)==PICO_ERROR_TIMEOUT);
    assert(dst==11 && !hw.data_cmd && !bus.restart_on_next);
    full=false; ts={};
    assert(i2c_read_blocking_internal(&bus,0x40,&dst,1,false,deadline,&ts)==1 && dst==42);
}''')

    def test_failed_sample_cannot_lower_a_battery_average(self):
        src = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
        self.run_cpp(r'''
struct NRF52Board {static uint32_t readBatteryAdcAverage(uint32_t,uint8_t,unsigned);};
unsigned samples[]={4000,4004,3996,4000}, index=0, waited=0;
uint32_t analogRead(uint32_t pin) {assert(pin==7); return samples[index++];}
void delayMicroseconds(unsigned us) {waited+=us;}
''' + function(src, "uint32_t NRF52Board::readBatteryAdcAverage") + r'''
int main() {
    assert(NRF52Board::readBatteryAdcAverage(7,4,100)==4000 && waited==400);
    index=0; samples[1]=0;
    assert(NRF52Board::readBatteryAdcAverage(7,4,0)==0 && index==2);
}''')

    def test_esp32_already_initialized_releases_hal_lock(self):
        src = self.patched("framework-arduinoespressif32/cores/esp32/esp32-hal-i2c.c")
        self.run_cpp(r'''
#define log_e(...) ((void)0)
#define log_i(...) ((void)0)
#define pdMS_TO_TICKS(t) (t)
using esp_err_t=int; using gpio_num_t=int; using i2c_port_t=int;
enum { SOC_I2C_NUM=1, pdTRUE=1, ESP_OK=0, ESP_FAIL=-1, ESP_ERR_NO_MEM=2,
       ESP_ERR_TIMEOUT=3, ESP_ERR_INVALID_ARG=4, I2C_MODE_MASTER=0, GPIO_PULLUP_ENABLE=1,
       I2C_SCLK_SRC_FLAG_FOR_NOMAL=0, I2C_LL_MAX_TIMEOUT=1 };
struct {int* lock; bool initialized; uint32_t frequency;} bus[1];
struct i2c_config_t {int mode,sda_io_num,scl_io_num,sda_pullup_en,scl_pullup_en,clk_flags;
    struct {uint32_t clk_speed;} master;};
int mutex=0;
int* xSemaphoreCreateMutex() {return &mutex;}
bool xSemaphoreTake(int* lock, unsigned ticks) {assert(ticks==50); if(*lock)return false; *lock=1; return true;}
void xSemaphoreGive(int* lock) {assert(*lock); *lock=0;}
int i2c_param_config(int,i2c_config_t*) {return ESP_OK;}
int i2c_driver_install(int,int,int,int,int) {return ESP_OK;}
void i2c_set_timeout(int,int) {}
''' + function(src, "esp_err_t i2cInit(") + r'''
int main() {
    assert(i2cInit(0,1,2,100000)==ESP_OK && !mutex);
    assert(i2cInit(0,1,2,100000)==ESP_FAIL && !mutex);
    mutex=1;
    assert(i2cInit(0,1,2,100000)==ESP_ERR_TIMEOUT && mutex);
}''')

    def test_esp32_contention_and_repeated_start_cleanup(self):
        src = self.patched("framework-arduinoespressif32/libraries/Wire/src/Wire.cpp")
        functions = "\n".join(function(src, f) for f in ["static bool ownsWireMutex",
            "void TwoWire::beginTransmission(uint16_t address)", "uint8_t TwoWire::endTransmission(bool",
            "size_t TwoWire::requestFrom(uint16_t address, size_t size", "size_t TwoWire::write(uint8_t data)"])
        self.run_cpp(r'''
#define log_e(...) ((void)0)
#define pdMS_TO_TICKS(t) (t)
using SemaphoreHandle_t = int*;
int task=1;
int xTaskGetCurrentTaskHandle() {return task;}
int xSemaphoreGetMutexHolder(int* lock) {return *lock;}
bool xSemaphoreTake(int* lock, unsigned ticks) {assert(ticks==50); if(*lock)return false; *lock=task; return true;}
void xSemaphoreGive(int* lock) {assert(*lock==task); *lock=0;}
using esp_err_t=int;
enum {ESP_OK, ESP_FAIL, ESP_ERR_TIMEOUT, pdTRUE=1};
unsigned writes, reads;
int i2cWrite(unsigned, unsigned addr, const uint8_t* buf, size_t n, unsigned) {
    assert(addr==0x40 && n==1 && buf[0]==42); ++writes; return ESP_OK;
}
int i2cRead(unsigned,unsigned,uint8_t*,size_t n,unsigned,size_t* received) {
    ++reads; *received=n; return ESP_OK;
}
int i2cWriteReadNonStop(unsigned,unsigned addr,const uint8_t* buf,size_t n,uint8_t*,
                        size_t count,unsigned,size_t* received) {
    assert(addr==0x40 && n==1 && buf[0]==42); ++reads; *received=count; return ESP_OK;
}
struct TwoWire {
    int owner=0; SemaphoreHandle_t lock=&owner;
    unsigned num=0, _timeOutMillis=50, bufferSize=8, txLength=0, txAddress=0;
    uint8_t buf[8]={}, *txBuffer=buf, *rxBuffer=buf;
    size_t rxIndex=0, rxLength=0;
    int nonStopTask=0; bool nonStop=false, is_slave=false;
    void beginTransmission(uint16_t); uint8_t endTransmission(bool);
    size_t requestFrom(uint16_t,size_t,bool); size_t write(uint8_t);
};
''' + functions + r'''
int main() {
    TwoWire w;
    w.beginTransmission(0x40); assert(w.write(42)==1);
    task=2; w.beginTransmission(0x55);
    assert(w.write(99)==0 && w.endTransmission(true)==5 && w.requestFrom(0x55,1,true)==0);
    assert(w.owner==1 && !reads && !writes);
    task=1; assert(w.endTransmission(true)==0 && w.owner==0 && writes==1);
    w.beginTransmission(0x40); w.write(42); assert(w.endTransmission(false)==0);
    assert(w.requestFrom(0x55,1,true)==0 && !w.owner && !w.nonStop);
    task=2; w.beginTransmission(0x40); w.write(42); assert(w.endTransmission(false)==0);
    assert(w.requestFrom(0x40,1,true)==1 && !w.owner && reads==1);
    assert(w.requestFrom(0x55,1,true)==1 && !w.owner && reads==2);
    w.is_slave=true; assert(w.write(27)==1);
}''')
