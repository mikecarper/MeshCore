"""Execute actual ESP32 radio constructors with local and default SPI buses."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_sx126x_buffered_radio_hal import MOCK


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "ebyte_eora_s3", "generic-e22", "heltec_ct62", "heltec_e213",
    "heltec_e290", "heltec_rc32", "heltec_t190", "heltec_tracker",
    "heltec_tracker_v2", "heltec_v3", "heltec_v4", "heltec_v4_r8",
    "heltec_wireless_paper", "lilygo_t3s3", "lilygo_tbeam_1w",
    "lilygo_tbeam_SX1262", "lilygo_tbeam_supreme_SX1262", "lilygo_tdeck",
    "lilygo_teth_elite", "lilygo_tlora_c6", "m5stack_unit_c6l",
    "meshadventurer", "mke_s3", "nibble_screen_connect",
    "nibble_zero_connect", "rak3112", "station_g2", "station_g3_esp32",
    "tenstar_c3", "thinknode_m2", "thinknode_m5", "wio-tracker-l2",
    "xiao_c3", "xiao_c6", "xiao_s3", "xiao_s3_wio",
)
LEGACY_FALLBACKS = {"lilygo_tdeck", "xiao_s3_wio"}


def construction(target):
    filename = {"xiao_c6": "XiaoC6Board.cpp",
                "m5stack_unit_c6l": "UnitC6LBoard.cpp"}.get(target, "target.cpp")
    source = (ROOT / "variants" / target / filename).read_text(encoding="utf-8")
    prefix = source[:source.index("WRAPPER_CLASS radio_driver")]
    # Only the target's actual global constructors are under test. The real
    # portable and platform HAL headers are included once by the harness;
    # unrelated board/sensor/display classes have inert stand-ins.
    prefix = re.sub(r"^\s*#include[^\n]*\n", "", prefix, flags=re.MULTILINE)
    opened = len(re.findall(r"^\s*#if(?:def|ndef)?\b", prefix, re.MULTILINE))
    closed = len(re.findall(r"^\s*#endif\b", prefix, re.MULTILINE))
    prefix += "\n#endif\n" * (opened - closed)
    board = re.search(r"^\s*(\w+) board;", prefix, re.MULTILINE).group(1)
    return prefix, board


class Esp32BufferedRadioTargetTests(unittest.TestCase):
    def test_actual_constructors_keep_bus_pins_clock_and_initialization_ownership(self):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        self.assertIsNotNone(compiler)
        mock = MOCK.replace("struct SPIClass {", """struct SPIClass {
  explicit SPIClass(int = 0) {}""")
        prelude = r'''
#include <helpers/radiolib/SX126xBufferedRadioHal.h>
#include <cassert>
SPIClass SPI;
constexpr unsigned FSPI=2, HSPI=3;
constexpr unsigned P_LORA_NSS=1, P_LORA_DIO_1=2, P_LORA_RESET=3, P_LORA_BUSY=4;
struct InertBoard {};
struct Module {
  ArduinoHal* hal;
  bool owned=false;
  Module(ArduinoHal* h,unsigned cs,unsigned irq,unsigned reset,unsigned busy):hal(h) {
    assert(cs==1 && irq==2 && reset==3 && busy==4);
  }
  Module(unsigned cs,unsigned irq,unsigned reset,unsigned busy)
      :Module(new ArduinoHal(SPI,SPISettings(2000000,MSBFIRST,SPI_MODE0)),cs,irq,reset,busy) {
    owned=true;hal->initInterface=true;
  }
  ~Module() { if(owned) delete hal; }
};
struct Radio {
  Module* module;
  Radio(Module* m):module(m) {}
  ~Radio() { delete module; }
};
#define RADIO_CLASS Radio
'''
        declarations = {8000000: [], 8888888: []}
        calls = {8000000: [], 8888888: []}
        for index, target in enumerate(TARGETS):
            prefix, board = construction(target)
            self.assertNotIn("sensecap_indicator", target)
            for local in (True, False):
                has_local = "static SPIClass spi" in prefix
                if not local and has_local and "defined(P_LORA_SCLK)" not in prefix:
                    continue
                if local and not has_local:
                    continue
                clock = 8888888 if target == "lilygo_tdeck" else 8000000
                buffered = local or target not in LEGACY_FALLBACKS
                expected = (4000000 if local else 2000000) if target == "heltec_rc32" else (
                    clock)
                selected = "spi" if local else "::SPI"
                name = f"target_{index}_{int(local)}"
                options = f"#define P_LORA_SCLK 5\n" if local else ""
                check = f'''
void check() {{
  auto& bus={selected};auto& hal=*radio.module->hal;
  assert(hal.spi==&bus && hal.spiSettings.hz=={expected});
  assert(hal.spiSettings.order==MSBFIRST && hal.spiSettings.mode==SPI_MODE0);
  bus.begins=bus.ends=bus.bursts=bus.bytes=0;
  hal.init();hal.term();
  assert(bus.begins=={int(not local)} && bus.ends=={int(not local)});
  uint8_t tx[]={{1,2,3,4,5,6,7}},rx[7]={{}};
  hal.spiTransfer(tx,sizeof(tx),rx);
  assert(bus.bursts=={int(buffered)} && bus.bytes=={0 if buffered else 7});
  for(unsigned i=0;i<7;++i) assert(rx[i]==uint8_t(tx[i]^0xa5));
}}
'''
                declarations[clock].append(options + f"namespace {name} {{\nusing {board}=InertBoard;\n"
                                           + prefix + check + "}\n#undef P_LORA_SCLK\n")
                calls[clock].append(f"{name}::check();")
        self.assertEqual(len(TARGETS), 36)
        self.assertEqual(sum(len(group) for group in calls.values()), 63)
        with tempfile.TemporaryDirectory(prefix="esp32-buffered-targets-") as folder:
            work = Path(folder)
            (work / "hal/Arduino").mkdir(parents=True)
            (work / "hal/Arduino/ArduinoHal.h").write_text(mock, encoding="ascii")
            cpp, binary = work / "test.cpp", work / "test"
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
            # Parse the real helper's default argument once per recipe clock,
            # without rewriting any production constructor or HAL type.
            for clock in declarations:
                code = (prelude + "\n".join(declarations[clock])
                        + "\nint main(){" + "".join(calls[clock]) + "}\n")
                legacy = code.replace(
                    "new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY)",
                    "new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY)", 1)
                self.assertNotEqual(legacy, code)
                for text, succeeds in ((code, True), (legacy, False)):
                    with self.subTest(clock=clock, buffered=succeeds):
                        cpp.write_text(text, encoding="utf-8")
                        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                            "-DESP32_PLATFORM", f"-DMC_SX1262_SPI_HZ={clock}", *flags,
                            "-I", str(work), "-I", str(ROOT / "src"),
                            str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                        if succeeds:
                            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                        else:
                            self.assertNotEqual(ran.returncode, 0, "unbuffered constructor negative control passed")


if __name__ == "__main__":
    unittest.main()
