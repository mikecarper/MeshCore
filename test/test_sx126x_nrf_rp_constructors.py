"""Compile actual tunable nRF52 and buffered RP2040 radio constructors."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_sx126x_buffered_radio_hal import MOCK, RADIO, ROOT


PREFIX = r'''
#include <helpers/radiolib/SX126xBufferedRadioHal.h>
constexpr unsigned P_LORA_NSS=1,P_LORA_DIO_1=2,P_LORA_RESET=3,P_LORA_BUSY=4;
SPIClass* testDefaultBus=nullptr;
struct Module {
  ArduinoHal* hal; bool owned=false;
  Module(ArduinoHal* adapter,unsigned cs,unsigned irq,unsigned reset,unsigned busy)
      : hal(adapter) { assert(cs==1 && irq==2 && reset==3 && busy==4); }
  Module(unsigned cs,unsigned irq,unsigned reset,unsigned busy,SPIClass& bus)
      : hal(new ArduinoHal(bus,SPISettings(2000000,MSBFIRST,SPI_MODE0))),owned(true) {
    assert(cs==1 && irq==2 && reset==3 && busy==4);
  }
  Module(unsigned cs,unsigned irq,unsigned reset,unsigned busy)
      : Module(cs,irq,reset,busy,*testDefaultBus) { hal->initInterface=true; }
  ~Module() { if (owned) delete hal; }
};
struct Radio {
  Module* mod;
  Radio(Module* value) : mod(value) {}
  ~Radio() { delete mod; }
};
#define RADIO_CLASS Radio
'''


MUZI_PREFIX = r'''
#include <helpers/radiolib/TunableRadioHal.h>
#include "variants/muzi_base/variant.h"
#define P_LORA_NSS LORA_NSS
#define P_LORA_DIO_1 LORA_DIO_1
#define P_LORA_RESET LORA_RESET
#define P_LORA_BUSY LORA_BUSY
SPIClass SPI;
struct Module {
  ArduinoHal* hal;
  unsigned cs,irq,reset,busy;
  Module(ArduinoHal* adapter,unsigned select,unsigned interrupt,unsigned rst,unsigned ready)
      : hal(adapter),cs(select),irq(interrupt),reset(rst),busy(ready) {}
};
struct Board {} board;
struct Radio {
  Module* mod;
  Radio(Module* value) : mod(value) {}
  ~Radio() { delete mod; }
};
struct Wrapper {
  Radio& chip;
  Board& selectedBoard;
  Wrapper(Radio& value,Board& selected) : chip(value),selectedBoard(selected) {}
};
struct UniversalLoRaRadio {
  Module& sx127x;
  Module& sx126x;
  Module* lr1121;
  Board& selectedBoard;
  UniversalLoRaRadio(Module& first,Module& second,Board& selected,Module* lr)
      : sx127x(first),sx126x(second),lr1121(lr),selectedBoard(selected) {}
};
#define RADIO_CLASS Radio
#define WRAPPER_CLASS Wrapper
'''


def target_sources(architecture):
    sources=set()
    for config in sorted((ROOT/"variants").glob("*/platformio.ini")):
        text=config.read_text()
        if not re.search(r"^\s*-D\s+RADIO_CLASS=CustomSX1262\s*$",text,re.MULTILINE):
            continue
        base=re.search(r"^extends\s*=\s*(\S+)",text,re.MULTILINE)
        if not base or base.group(1) not in architecture:
            continue
        if config.parent.name=="rak3401":
            continue # Dedicated physical-board test pins its qualified SPI1 setup.
        source=config.with_name("target.cpp")
        if config.parent.name=="wio-tracker-l1-eink":
            source=ROOT/"variants/wio-tracker-l1/target.cpp"
        sources.add(source)
    return sorted(sources)


class SX126xTargetConstructors(unittest.TestCase):
    def compile_run(self,architecture,source,*,defines=()):
        compiler=shutil.which(os.environ.get("CXX","g++"))
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="sx126x-target-constructors-") as folder:
            base=Path(folder)
            (base/"hal/Arduino").mkdir(parents=True)
            (base/"hal/Arduino/ArduinoHal.h").write_text(MOCK,encoding="ascii")
            (base/"WVariant.h").write_text("#include <cstdint>\n",encoding="ascii")
            cpp,executable=base/"test.cpp",base/"test"
            cpp.write_text(source,encoding="ascii")
            built=subprocess.run([compiler,"-std=c++17","-Wall","-Wextra","-Werror",
                f"-D{architecture}",*[f"-D{item}" for item in defines],"-I",str(base),
                "-I",str(ROOT/"src"),"-I",str(ROOT),str(cpp),"-o",str(executable)],
                capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

    def test_actual_rp2040_constructions_keep_buffered_selected_bus(self):
        for arch,bases in (("RP2040_PLATFORM",{"rp2040_base"}),):
            with self.subTest(architecture=arch):
                cases=[]
                sources=target_sources(bases)
                self.assertTrue(sources)
                for source in sources:
                    text=source.read_text()
                    self.assertIn("#include <helpers/radiolib/SX126xBufferedRadioHal.h>",
                                  text,str(source))
                    constructions=re.findall(
                        r"^static SX126xBufferedRadioHal radioHal\(([^;]+)\);\n"
                        r"(RADIO_CLASS radio = new Module\(&radioHal,[^;]+;)",
                        text,re.MULTILINE)
                    self.assertTrue(constructions,str(source))
                    legacy=re.findall(r"new Module\(P_LORA_NSS[^;]+;",text)
                    self.assertEqual(len(legacy),0,str(source))
                    for ordinal,(arguments,module) in enumerate(constructions):
                        bus=arguments.split(",")[0]
                        expected_bus="SPI1" if source.parent.name in {
                            "rak11310","rpi_picow","waveshare_rp2040_lora"} else "SPI"
                        self.assertEqual(bus,expected_bus,str(source))
                        # These are the pre-existing Module() default-bus
                        # branches. Every other constructor passed its bus
                        # explicitly and must leave begin/end board-owned.
                        owns=source.parent.name=="xiao_rp2040" or (
                            source.parent.name in {"heltec_t096","heltec_t1","heltec_t114"}
                            and ordinal==1)
                        name=f"case_{len(cases)}"
                        cases.append((name,f'''
namespace {name} {{
SPIClass SPI,SPI1;
static SX126xBufferedRadioHal radioHal({arguments});
{module}
void check() {{
  assert(radio.mod->hal==&radioHal && radioHal.spi==&{bus});
  assert(radioHal.spiSettings.hz==8000000);
  assert(radioHal.initInterface=={str(owns).lower()});
  radio.mod->hal->init();radio.mod->hal->term();
  assert({bus}.begins=={int(owns)} && {bus}.ends=={int(owns)});
}}
}}
'''))
                fixture=PREFIX+"\n".join(code for _,code in cases)
                fixture+="\nint main() {"+"".join(f"{name}::check();" for name,_ in cases)+"}\n"
                self.compile_run(arch,fixture)

    def test_all_nrf52_spim3_defaults_use_tunable_8mhz_byte_path(self):
        cases=[]
        sources=target_sources({"nrf52_base","rak4631_hw"})
        self.assertEqual(len(sources),32)
        for source in sources:
            text=source.read_text()
            if source.parent.name == "xiao_nrf52":
                # Its separate constructor test executes the default-off
                # candidate gate and actual legacy/opt-in branches.
                self.assertIn("#if MC_XIAO_NRF52_HIL_FAST_SPI",text)
            elif source.parent.name in {"rak_wismesh_tag", "heltec_tower_v2"}:
                # Their SPIM1 candidate is also default-off, with the actual
                # global SDK binding exercised by its separate native test.
                self.assertIn("#if MC_NRF52_HIL_SPIM1_RADIO",text)
            else:
                self.assertNotIn("BufferedRadioHal",text,str(source))
            # Muzi's shared image now puts the universal/legacy selection
            # between this common HAL and its legacy Module constructor.
            # Extract both declarations independently, preserving every
            # fallback branch rather than requiring them to be adjacent.
            arguments=re.findall(r"^static TunableRadioHal radio_hal\(([^;]+)\);$",
                                 text,re.MULTILINE)
            modules=re.findall(r"^RADIO_CLASS radio = new Module\(&radio_hal,[^;]+;",
                               text,re.MULTILINE)
            fallback=source.parent.name in {"heltec_t096","heltec_t1","heltec_t114"}
            self.assertEqual(len(arguments),2 if fallback else 1,str(source))
            self.assertEqual(len(modules),len(arguments),str(source))
            for ordinal,(arguments,module) in enumerate(zip(arguments,modules)):
                owns=fallback and ordinal==1
                self.assertEqual(arguments, "SPI, 8000000, true" if owns else "SPI",str(source))
                construction=f"static TunableRadioHal radio_hal({arguments});\n{module}"
                name=f"case_{len(cases)}"
                cases.append((name,f'''
namespace {name} {{
SPIClass SPI;
struct BindDefaultBus {{ BindDefaultBus() {{testDefaultBus=&SPI;}} }} bindDefaultBus;
{construction}
void check() {{
  assert(!radio.mod->owned && radio.mod->hal==&radio_hal && radio.mod->hal->spi==&SPI);
  assert(radio.mod->hal->spiSettings.hz==8000000);
  assert(radio_hal.capabilities().flags==0 && radio_hal.capabilities().spi_clocks==(2|4|8));
  assert(radio.mod->hal->initInterface=={str(owns).lower()});
  radio.mod->hal->init();radio.mod->hal->term();
  assert(SPI.begins=={int(owns)} && SPI.ends=={int(owns)});
  uint8_t tx[]={{1,0x7f,0xff}},rx[3]={{}};
  radio.mod->hal->spiTransfer(tx,3,rx);
  assert(SPI.bytes==3 && SPI.bursts==0 && rx[0]==(1^0xa5) && rx[2]==(0xff^0xa5));
}}
}}
'''))
        self.assertEqual(len(cases),35)
        fixture=PREFIX+"\n".join(code for _,code in cases)
        fixture+="\nint main() {"+"".join(f"{name}::check();" for name,_ in cases)+"}\n"
        self.compile_run("NRF52_PLATFORM",fixture)

    def test_muzi_fixed_and_automatic_radios_share_tunable_byte_bus(self):
        text=(ROOT/"variants/muzi_base/target.cpp").read_text()
        start=text.index("static TunableRadioHal radio_hal(")
        end=text.index("VolatileRTCClock fallback_clock;",start)
        # Compile the actual complete conditional declaration block, so both
        # physical modules in the automatic image must use this same HAL.
        constructors=text[start:end]
        self.assertNotIn("BufferedRadioHal",text)
        fixture=MUZI_PREFIX+constructors+r'''
void check(Module& mod,unsigned irq) {
  assert(mod.hal==&radio_hal && mod.hal->spi==&SPI);
  assert(mod.cs==44 && mod.irq==irq && mod.reset==42 && mod.busy==43);
  assert(mod.hal->spiSettings.hz==8000000 && !mod.hal->initInterface);
  assert(TunableRadioHal::find(mod.hal)==&radio_hal);
  assert(radio_hal.capabilities().flags==0);
  assert(radio_hal.capabilities().spi_clocks==(2|4|8));
  mod.hal->init();mod.hal->term();
  assert(SPI.begins==0 && SPI.ends==0);
  uint8_t tx[]={1,0x7f,0xff},rx[3]={};
  const unsigned bytes=SPI.bytes;
  mod.hal->spiTransfer(tx,3,rx);
  assert(SPI.bytes==bytes+3 && SPI.bursts==0);
  assert(rx[0]==(1^0xa5) && rx[1]==(0x7f^0xa5) && rx[2]==(0xff^0xa5));
}
int main() {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  assert(&radio_driver.sx127x==&sx1262_module);
  assert(&radio_driver.sx126x==&sx1262_module);
  assert(radio_driver.lr1121==&lr1121_module && &radio_driver.selectedBoard==&board);
  check(sx1262_module,38);
  check(lr1121_module,40);
#else
  assert(&radio_driver.chip==&radio && &radio_driver.selectedBoard==&board);
  check(*radio.mod,
#if defined(USE_LR1121)
      40
#else
      38
#endif
  );
#endif
  using Tuning=mesh::RadioSwitchTuning;
  Tuning tuning;
  tuning.flags=Tuning::Buffer;
  assert(!radio_hal.setTuning(tuning));
  tuning.flags=0;
  for (uint8_t clock : {2,4,8}) {
    tuning.spi_mhz=clock;
    assert(radio_hal.setTuning(tuning));
    assert(radio_hal.effectiveHz()==unsigned(clock)*1000000);
    assert(radio_hal.spi==&SPI && !radio_hal.initInterface);
  }
  tuning.spi_mhz=9;
  assert(!radio_hal.setTuning(tuning) && radio_hal.effectiveHz()==8000000);
  assert(SPI.begins==0 && SPI.ends==0 && SPI.bursts==0);
}
'''
        fixture="#include <initializer_list>\n"+fixture
        for definitions in (("USE_SX1262",),("USE_LR1121",),
                            ("USE_SX1262","USE_LR1121","MESH_RADIO_AUTO_FAMILY=1",
                             "MESH_RADIO_AUTO_LR1121=1")):
            with self.subTest(defines=definitions):
                self.compile_run("NRF52_PLATFORM",fixture,defines=definitions)

    def test_lr1110_m4_keeps_manual_spi_start_and_prior_2mhz_byte_path(self):
        text=(ROOT/"variants/thinknode_m4/target.cpp").read_text()
        construction=re.search(r"^static TunableRadioHal radio_hal\([^;]+;\n"
                               r"RADIO_CLASS radio = new Module\(&radio_hal,[^;]+;",text,re.MULTILINE)
        self.assertIsNotNone(construction)
        self.assertNotIn("BufferedRadioHal",text)
        self.assertIn("SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);",text)
        self.assertIn("SPI.begin();",text)
        self.assertIn("int status = radio.begin(",text)
        fixture=PREFIX+"\nSPIClass SPI;\n"+construction.group(0)+r'''
int main() {
  assert(!radio.mod->owned && radio.mod->hal==&radio_hal && radio.mod->hal->spi==&SPI);
  assert(radio.mod->hal->spiSettings.hz==2000000 && !radio.mod->hal->initInterface);
  radio.mod->hal->init();radio.mod->hal->term();
  assert(SPI.begins==0 && SPI.ends==0);
  uint8_t tx[]={1,0x7f,0xff},rx[3]={};
  radio.mod->hal->spiTransfer(tx,3,rx);
  assert(SPI.bursts==0 && SPI.bytes==3 && rx[0]==(1^0xa5));
}
'''
        self.compile_run("NRF52_PLATFORM",fixture)

    def test_existing_lr1110_buffered_constructors_remain_unchanged(self):
        cases=[]
        for name in ("minewsemi_me25ls01","t1000-e","thinknode_m3","wio_wm1110"):
            text=(ROOT/"variants"/name/"target.cpp").read_text()
            construction=re.search(r"^Nrf52BufferedRadioHal radioHal\(SPI\);\n"
                                   r"RADIO_CLASS radio = new Module\([^;]+;",text,re.MULTILINE)
            self.assertIsNotNone(construction,name)
            namespace=f"case_{len(cases)}"
            cases.append((namespace,f'''
namespace {namespace} {{
SPIClass SPI;
{construction.group(0)}
void check() {{
  assert(radio.mod->hal==&radioHal && radioHal.spi==&SPI);
  assert(radioHal.spiSettings.hz==16000000 && !radioHal.initInterface);
  uint8_t tx[]={{0x86,0,1}},rx[3]={{}};
  radio.mod->hal->spiTransfer(tx,3,rx);
  assert(SPI.bursts==1 && SPI.bytes==0 && rx[0]==(0x86^0xa5));
}}
}}
'''))
        fixture=PREFIX+"\n".join(code for _,code in cases)
        fixture+="\nint main() {"+"".join(f"{name}::check();" for name,_ in cases)+"}\n"
        self.compile_run("NRF52_PLATFORM",fixture)


if __name__=="__main__":
    unittest.main()
