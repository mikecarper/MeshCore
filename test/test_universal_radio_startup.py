"""Run each universal target's real startup/retry functions against a transient probe."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("generic_e22_universal", "meshadventurer_universal", "tenstar_c3_universal",
            "lilygo_tbeam_universal", "lilygo_t3s3_universal")

FIXTURE = r'''
struct SPIClass {
  unsigned starts=0;
  void begin(int,int,int){++starts;}
};
namespace mesh {
using LoRaRadioFamily=::mesh::LoRaRadioFamily;
struct LoRaChipProbePins {unsigned nss,reset,busy;};
static std::vector<LoRaRadioFamily> outcomes;
static unsigned probes=0;
LoRaRadioFamily probeUniversalLoRaChip(SPIClass&,const LoRaChipProbePins&){
  assert(probes<outcomes.size());return outcomes[probes++];
}
}
struct Driver {
  mesh::LoRaRadioFamily selected=mesh::LoRaRadioFamily::None;
  unsigned initializations=0;
  bool init_ok=true;
  mesh::LoRaRadioFamily family()const{return selected;}
  bool selectFamily(mesh::LoRaRadioFamily family){
    if(family==mesh::LoRaRadioFamily::None)return false;
    assert(selected==mesh::LoRaRadioFamily::None);selected=family;return true;
  }
  bool stdInit(SPIClass*){
    ++initializations;assert(selected!=mesh::LoRaRadioFamily::None);return init_ok;
  }
};
struct Clock {void begin(){}void begin(int){}};
static Driver radio_driver;
static SPIClass spi;
static Clock fallback_clock,rtc_clock;
static int Wire=0;
@FUNCTIONS@
static void check(){
  using F=mesh::LoRaRadioFamily;
  for(auto found:{F::SX126x,F::SX127x,F::SX1268}){
    radio_driver=Driver{};spi=SPIClass{};mesh::probes=0;
    // Board setup initially sees no radio; the next startup attempt finds it.
    mesh::outcomes={F::None,found};
    assert(!prepareUniversalLoRaRadio()&&mesh::probes==1);
    assert(radio_init()&&mesh::probes==2&&radio_driver.family()==found);
    assert(radio_driver.initializations==1&&spi.starts==2);
    // A selected driver's later hardware failure retries initialization only.
    // Reprobing here would reset active IRQ state or replace a cached backend.
    radio_driver.init_ok=false;
    assert(!radio_init()&&mesh::probes==2&&radio_driver.family()==found);
    assert(radio_driver.initializations==2&&spi.starts==2);
    radio_driver.init_ok=true;
    assert(radio_init()&&mesh::probes==2&&radio_driver.initializations==3);
  }
  radio_driver=Driver{};spi=SPIClass{};mesh::probes=0;
  mesh::outcomes={F::None,F::None,F::None};
  for(unsigned attempt=0;attempt<3;++attempt){
    assert(!radio_init()&&mesh::probes==attempt+1);
    assert(radio_driver.family()==F::None&&radio_driver.initializations==0);
  }
}
'''


class UniversalRadioStartupTest(unittest.TestCase):
    def program(self, remove_retry=False):
        parts=[]
        for index,variant in enumerate(VARIANTS):
            source=(ROOT/"variants"/variant/"target.cpp").read_text()
            functions=body(source,"bool prepareUniversalLoRaRadio()")+"\n"+body(source,"bool radio_init()")
            if remove_retry and index==0:
                self.assertIn("prepareUniversalLoRaRadio() && radio_driver.stdInit(&spi)",functions)
                functions=functions.replace("prepareUniversalLoRaRadio() && radio_driver.stdInit(&spi)",
                                            "radio_driver.stdInit(&spi)")
            parts.append("namespace board%d {\n%s\n}\n"%(index,FIXTURE.replace("@FUNCTIONS@",functions)))
        return r'''
#include <helpers/LoRaRadioFamily.h>
#include <cassert>
#include <vector>
#define P_LORA_NSS 10
#define P_LORA_RESET 11
#define P_LORA_BUSY 12
#define P_LORA_SCLK 13
#define P_LORA_MISO 14
#define P_LORA_MOSI 15
'''+"\n".join(parts)+"\nint main(){"+"".join("board%d::check();"%i for i in range(len(VARIANTS)))+"}\n"

    def run_program(self, source):
        compiler=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:self.skipTest("host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="universal-radio-startup-") as folder:
            work=Path(folder);cpp=work/"test.cpp";cpp.write_text(source)
            executable=work/("test.exe" if os.name=="nt" else "test")
            sanitize=(["-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie"]
                      if sys.platform.startswith("linux") else [])
            compiled=subprocess.run([compiler,"-std=c++11","-Wall","-Wextra","-Werror",*sanitize,
                "-I"+str(ROOT/"src"),str(cpp),"-o",str(executable)],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            return subprocess.run([str(executable)],capture_output=True,text=True,timeout=20)

    def test_transient_probe_failure_recovers_without_rebinding_a_selected_driver(self):
        tested=self.run_program(self.program())
        self.assertEqual(tested.returncode,0,tested.stdout+tested.stderr)

    def test_removing_startup_reprobe_prevents_recovery(self):
        tested=self.run_program(self.program(remove_retry=True))
        self.assertNotEqual(tested.returncode,0,"A missing probe retry falsely recovered")


if __name__=="__main__":
    unittest.main()
