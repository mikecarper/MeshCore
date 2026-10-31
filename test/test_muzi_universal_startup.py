"""Run the Muzi target's actual identification retry and native LR1121 setup."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
TARGET = (ROOT / "variants/muzi_base/target.cpp").read_text()

HARNESS = r'''
#define MESH_RADIO_AUTO_FAMILY 1
#define MESH_RADIO_AUTO_LR1121 1
#include <helpers/LoRaRadioFamily.h>
#include <cassert>
#include <cstdint>
#include <vector>
#include <cmath>
#define P_LORA_NSS 44
#define P_LORA_RESET 42
#define P_LORA_BUSY 43
#define P_LORA_SCLK 45
#define P_LORA_MISO 47
#define P_LORA_MOSI 46
#define LORA_FREQ 869.618f
#define LORA_BW 62.5f
#define LORA_SF 8
#define LORA_CR 5
#define LORA_TX_POWER 22
#define LR11X0_DIO3_TCXO_VOLTAGE 3.0f
#define RX_BOOSTED_GAIN true
#define RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_ERR_NONE 0
#define LOW 0
#define HIGH 1
#define RADIOLIB_NC 0xffffffffU
#define RADIOLIB_LR11X0_DIO5 5
#define RADIOLIB_LR11X0_DIO6 6
#define END_OF_MODE_TABLE {0xff,{0,0,0}}
struct Module {
 static constexpr unsigned RFSWITCH_MAX_PINS=3;
 struct RfSwitchMode_t {unsigned mode; unsigned pins[3];};
};
struct LR11x0 {
 enum {MODE_STBY, MODE_RX, MODE_TX, MODE_TX_HP, MODE_TX_HF, MODE_GNSS, MODE_WIFI};
};
struct SPIClass {
 unsigned starts=0,pin_sets=0;
 void setPins(int m,int c,int o){assert(m==47&&c==45&&o==46);++pin_sets;}
 void begin(){++starts;}
} SPI;
struct CustomLR1121 {
 unsigned stage=0,fail=0;
 int result(){return ++stage==fail ? -1 : 0;}
 int begin(float f,float bw,uint8_t sf,uint8_t cr,uint8_t sync,int8_t p,
           uint16_t preamble,float tcxo){
  assert(std::fabs(f-869.618f)<.001&&bw==62.5&&sf==8&&cr==5);
  assert(sync==0x12&&p==22&&preamble==16&&tcxo==3.0);return result();
 }
 int setCRC(int crc){assert(stage==1&&crc==2);return result();}
 int explicitHeader(){assert(stage==2);return result();}
 void setRfSwitchTable(const uint32_t (&pins)[3],const Module::RfSwitchMode_t* modes){
  assert(stage==3&&pins[0]==5&&pins[1]==6&&pins[2]==RADIOLIB_NC);
  assert(modes[0].mode==LR11x0::MODE_STBY&&modes[0].pins[0]==LOW&&modes[0].pins[1]==LOW);
  assert(modes[1].mode==LR11x0::MODE_RX&&modes[1].pins[0]==HIGH&&modes[1].pins[1]==LOW);
  assert(modes[2].mode==LR11x0::MODE_TX&&modes[2].pins[0]==LOW&&modes[2].pins[1]==HIGH);
  assert(modes[3].mode==LR11x0::MODE_TX_HP&&modes[3].pins[0]==LOW&&modes[3].pins[1]==HIGH);
  assert(modes[4].mode==LR11x0::MODE_TX_HF&&modes[4].pins[0]==LOW&&modes[4].pins[1]==LOW);
  assert(modes[7].mode==0xff);
 }
 int setRxBoostedGainMode(bool on){assert(stage==3&&on);return result();}
};
namespace mesh {
struct PortableLoRaChipProbePins{unsigned nss,reset,busy;};
static std::vector<LoRaRadioFamily> outcomes;
static unsigned probes;
LoRaRadioFamily probePortableLoRaChip(SPIClass&,const PortableLoRaChipProbePins& pins){
 assert(pins.nss==44&&pins.reset==42&&pins.busy==43);
 assert(probes<outcomes.size());return outcomes[probes++];
}
}
static CustomLR1121 chip;
struct Driver {
 mesh::LoRaRadioFamily selected=mesh::LoRaRadioFamily::None;
 bool init_ok=true,register_ok=true;
 unsigned registrations=0,initializations=0;
 bool (*initialize)(CustomLR1121&,SPIClass*)=nullptr;
 bool (*deep)()=nullptr;
 mesh::LoRaRadioFamily family()const{return selected;}
 bool setLR1121Initializer(bool (*start)(CustomLR1121&,SPIClass*),bool (*recover)()){
  ++registrations;initialize=start;deep=recover;return register_ok;
 }
 bool selectFamily(mesh::LoRaRadioFamily found){
  assert(selected==mesh::LoRaRadioFamily::None);
  if(found==mesh::LoRaRadioFamily::None)return false;
  selected=found;return true;
 }
 bool stdInit(SPIClass* spi){
  ++initializations;assert(selected!=mesh::LoRaRadioFamily::None);
  if(!init_ok)return false;
  chip=CustomLR1121{};
  return selected!=mesh::LoRaRadioFamily::LR1121||initialize(chip,spi);
 }
} radio_driver;
bool radio_init();
@FUNCTIONS@
int main(){
 using F=mesh::LoRaRadioFamily;
 for(unsigned failure=0;failure<=4;++failure){
  chip=CustomLR1121{};chip.fail=failure;
  assert(muziInitLR1121(chip,&SPI)==(failure==0));
  assert(chip.stage==(failure?failure:4));
 }
 for(auto found:{F::SX126x,F::LR1121}){
  radio_driver=Driver{};SPI=SPIClass{};mesh::probes=0;
  mesh::outcomes={F::None,found};
  assert(!prepareUniversalLoRaRadio()&&mesh::probes==1);
  assert(radio_init()&&mesh::probes==2&&radio_driver.family()==found);
  assert(radio_driver.deep==radio_init&&radio_driver.initializations==1);
  unsigned pins=SPI.pin_sets,starts=SPI.starts;
  radio_driver.init_ok=false;
  assert(!radio_init()&&mesh::probes==2&&radio_driver.registrations==2);
  assert(SPI.pin_sets==pins&&SPI.starts==starts);
  radio_driver.init_ok=true;
  assert(radio_init()&&mesh::probes==2&&radio_driver.initializations==3);
 }
 radio_driver=Driver{};SPI=SPIClass{};mesh::probes=0;
 mesh::outcomes={F::None,F::None,F::None};
 for(unsigned attempt=0;attempt<3;++attempt){
  assert(!radio_init()&&mesh::probes==attempt+1);
  assert(radio_driver.initializations==0&&radio_driver.family()==F::None);
 }
 radio_driver=Driver{};radio_driver.register_ok=false;SPI=SPIClass{};mesh::probes=0;
 assert(!radio_init()&&mesh::probes==0&&SPI.starts==0);
}
'''


class MuziUniversalStartupTests(unittest.TestCase):
    def program(self, remove_retry=False):
        start = TARGET.index("  static const uint32_t rfswitch_dios")
        end = TARGET.index("  #endif", start)
        functions = TARGET[start:end] + "\n" + "\n".join(body(TARGET, signature) for signature in
            ("static bool muziInitLR1121(", "bool prepareUniversalLoRaRadio()", "bool radio_init()"))
        if remove_retry:
            functions = functions.replace("prepareUniversalLoRaRadio() && radio_driver.stdInit(&SPI)",
                                          "radio_driver.stdInit(&SPI)")
        return HARNESS.replace("@FUNCTIONS@", functions)

    def run_program(self, code):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="muzi-startup-") as folder:
            work = Path(folder)
            source = work / "check.cpp"
            binary = work / ("check.exe" if os.name == "nt" else "check")
            source.write_text(code)
            sanitize = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                        if sys.platform.startswith("linux") else [])
            result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitize, "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)

    def test_native_lr_setup_failures_and_family_probe_retries(self):
        result = self.run_program(self.program())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_retry_does_not_falsely_recover(self):
        result = self.run_program(self.program(remove_retry=True))
        self.assertNotEqual(result.returncode, 0)

    def test_detection_follows_board_power_and_voltage_protection(self):
        source = (ROOT / "variants/muzi_base/MuziBaseBoard.cpp").read_text()
        setup = body(source, "void MuziBaseBoard::begin()")
        self.assertLess(setup.index("NRF52BoardDCDC::begin();"), setup.index("prepareUniversalLoRaRadio();"))
        self.assertLess(setup.index("checkBootVoltage(&power_config);"), setup.index("prepareUniversalLoRaRadio();"))
        self.assertIn("P_LORA_NSS, LORA_SX1262_DIO_1", TARGET)
        self.assertIn("P_LORA_NSS, LORA_LR1121_DIO_1", TARGET)
        self.assertIn("radio_driver(sx1262_module, sx1262_module, board, &lr1121_module)", TARGET)

    def test_shutdown_releases_both_possible_gpio_wake_sources(self):
        source = (ROOT / "variants/muzi_base/MuziBaseBoard.cpp").read_text()
        method = body(source, "void MuziBaseBoard::shutdownPeripherals()")
        code = r'''
#include <cassert>
#include <cstdint>
#include <vector>
#define LORA_SX1262_DIO_1 38
#define LORA_LR1121_DIO_1 40
#define INPUT 0
static bool attached[48], sensing[48];
static std::vector<unsigned> operations;
void detachInterrupt(unsigned pin) { assert(pin==38 || pin==40); attached[pin]=false; operations.push_back(pin); }
void pinMode(unsigned pin, unsigned mode) { assert(mode==INPUT); sensing[pin]=false; operations.push_back(pin+100); }
struct NRF52BoardDCDC {
 unsigned shutdowns=0;
 virtual void shutdownPeripherals() {
  ++shutdowns; operations.push_back(1);
  // The common board implementation clears only compile-time IRQ38.
  attached[38]=false; sensing[38]=false;
 }
};
struct MuziBaseBoard : NRF52BoardDCDC { void shutdownPeripherals() override; };
@METHOD@
int main() {
 for(unsigned active : {38U,40U}) {
  operations.clear();
  for(unsigned pin : {38U,40U}) attached[pin]=sensing[pin]=false;
  attached[active]=sensing[active]=true;
  MuziBaseBoard board;
  // Button power-off and deep sleep call the common virtual shutdown hook.
  NRF52BoardDCDC& base=board;
  base.shutdownPeripherals();
  assert(board.shutdowns==1 && operations.front()==1);
  assert(!attached[38] && !sensing[38] && !attached[40] && !sensing[40]);
 }
}
'''.replace("@METHOD@", method)
        result = self.run_program(code)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Removing the runtime Duo IRQ cleanup must reproduce the wake leak.
        broken = code.replace("detachInterrupt(LORA_LR1121_DIO_1);", "")
        broken = broken.replace("pinMode(LORA_LR1121_DIO_1, INPUT);", "")
        result = self.run_program(broken)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
