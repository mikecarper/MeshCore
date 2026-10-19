#!/usr/bin/env python3
"""Execute the bounded Morse encoder and real RadioLib TX transitions."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from test_carrier_wave import production_harness
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]


def portable_radio_header():
    """Use the exact Radio interface without unrelated packet/crypto mocks."""
    dispatcher = (ROOT / "src/Dispatcher.h").read_text(encoding="utf-8")
    return ("#pragma once\n#include <RadioProfiles.h>\n#include <helpers/MorseTx.h>\n#include <helpers/MorseRx.h>\n#include <helpers/RegulatorMode.h>\nnamespace mesh {\n"
            + method(dispatcher, "enum class RadioParamApplyResult") + ";\n"
            + method(dispatcher, "class Radio {") + ";\n}\n")


def compile_run(testcase, code, includes=(), flags=(), headers=None):
    with tempfile.TemporaryDirectory(prefix="morse-tx-") as folder:
        work = Path(folder)
        source, binary = work / "test.cpp", work / "test"
        source.write_text(code, encoding="utf-8")
        for path, text in (headers or {}).items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        command = [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                   "-Werror", *SANITIZERS, *flags, *["-I" + str(path) for path in includes],
                   "-I" + str(work), "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                   str(source), "-o", str(binary)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=60)
        testcase.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        testcase.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout


class MorseTxTests(unittest.TestCase):
    def test_flash_table_preserves_every_byte_of_the_original_itu_lookup(self):
        compile_run(self, r'''
#include <helpers/MorseTx.h>
#include <cassert>
static uint8_t original(char c) {
  if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
  static const uint8_t letters[] = {5,24,26,12,2,18,14,16,4,23,13,20,7,6,15,22,29,10,8,3,9,17,11,25,27,28};
  static const uint8_t digits[] = {63,47,39,35,33,32,48,56,60,62};
  if (c >= 'A' && c <= 'Z') return letters[c - 'A'];
  if (c >= '0' && c <= '9') return digits[c - '0'];
  switch (c) {
    case '.': return 85; case ',': return 115; case '?': return 76;
    case '\'': return 94; case '!': return 107; case '/': return 50;
    case '(': return 54; case ')': return 109; case '&': return 40;
    case ':': return 120; case ';': return 106; case '=': return 49;
    case '+': return 42; case '-': return 97; case '_': return 77;
    case '"': return 82; case '$': return 137; case '@': return 90;
    default: return 0;
  }
}
int main() {
  for (unsigned byte = 0; byte < 256; ++byte)
    assert(mesh::MorseTx::codeFor(char(byte)) == original(char(byte)));
}
''')

    def test_production_encoder_alphabet_punctuation_limits_and_exact_timing(self):
        code = (ROOT / "test/fixtures/morse_encoder.cpp").read_text(encoding="ascii")
        compile_run(self, code)

    def test_actual_native_fsk_driver_and_fault_recovery(self):
        code = production_harness().split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_driver.cpp").read_text(encoding="ascii")
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n")
        compile_run(self, code, flags=("-Wno-unused-parameter",),
                    headers={"Dispatcher.h": portable_radio_header(), "Arduino.h": arduino})

    def test_actual_chip_specific_ramps_band_routes_power_mapping_and_restoration(self):
        code = production_harness().split("int main() {", 1)[0]
        chip = (ROOT / "src/helpers/radiolib/CustomLR2021.h").read_text(encoding="ascii")
        routing = ("struct RoutedModule { unsigned switch_calls=0; uint8_t switch_mode=0; "
                   "int register_address=-1, register_value=-1, register_msb=-1, register_lsb=-1, register_result=0; "
                   "void setRfSwitchState(uint8_t value) { ++switch_calls; switch_mode=value; } "
                   "int SPIsetRegValue(int address,int value,int msb,int lsb) { register_address=address; "
                   "register_value=value; register_msb=msb; register_lsb=lsb; return register_result; } };\n"
                   "struct LR2021 { enum { MODE_TX=2, MODE_TX_HF=6 }; };\n")
        code = code.replace("struct Chip {", routing + "struct Chip {\n  RoutedModule routed_module; RoutedModule* mod=&routed_module; float freqMHz=0; int cw_test_mode=0;\n")
        code = code.replace("int setFrequency(float value) { frequency=value; return frequency_result; }",
                            "int setFrequency(float value) { frequency=freqMHz=value; return frequency_result; }")
        chip_methods = (method(chip, "uint8_t rfSwitchTxMode(") + "\n"
                        + method(chip, "int16_t transmitMorseCarrier(") + "\n"
                        + "int16_t setTxTestMode(int mode) { cw_test_mode=mode; return transmitDirect(); }\n")
        code = code.replace("int clearIrqFlags(", chip_methods + "int clearIrqFlags(")
        code = "#include <RadioLib.h>\n#include <helpers/radiolib/LR2021Band.h>\n" + code
        fixture = (ROOT / "test/fixtures/morse_chip_hooks.cpp").read_text(encoding="ascii")
        specs = {
            "SX1262": ("bool supportsCarrierWave(", "int16_t prepareMorseCarrier(", "int16_t leaveMorseCarrier(", "bool restoreCarrierWaveModem(", "int16_t applyCachedTxPower("),
            "SX1268": ("bool supportsMorseTx(", "bool validateMorseFrequency(", "int16_t prepareMorseCarrier(", "int16_t leaveMorseCarrier(", "bool restoreCarrierWaveModem("),
            "LLCC68": ("bool supportsMorseTx(", "int16_t prepareMorseCarrier(", "int16_t leaveMorseCarrier(", "bool restoreCarrierWaveModem("),
            "STM32WLx": ("bool supportsMorseTx(", "int16_t prepareMorseCarrier(", "int16_t leaveMorseCarrier(", "bool restoreCarrierWaveModem("),
            "LR1110": ("bool supportsCarrierWave(", "int16_t prepareMorseCarrier(", "bool restoreCarrierWaveModem("),
            "LR1121": ("bool supportsMorseTx(", "int16_t prepareMorseCarrier(", "bool restoreCarrierWaveModem("),
            "LR2021": ("bool supportsMorseTx(", "bool validateMorseFrequency(", "int16_t prepareMorseCarrier(", "int16_t enterMorseCarrier(", "bool restoreCarrierWaveModem("),
        }
        for chip_name, signatures in specs.items():
            header = (ROOT / f"src/helpers/radiolib/Custom{chip_name}Wrapper.h").read_text(encoding="ascii")
            fixture = fixture.replace(f"@{chip_name}@", "\n".join(method(header, signature) for signature in signatures))
        sx1276 = (ROOT / "src/helpers/radiolib/CustomSX1276.h").read_text(encoding="ascii")
        fixture = fixture.replace("@SX1276_RAMP@", method(sx1276, "int16_t setMorsePaRamp("))
        code += fixture
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n")
        radio_lib = ("#pragma once\n#define RADIOLIB_SX126X_PA_RAMP_3400U 7\n"
                     "#define RADIOLIB_SX126X_PA_RAMP_10U 0\n#define RADIOLIB_SX126X_PA_RAMP_20U 1\n"
                     "#define RADIOLIB_SX126X_PA_RAMP_40U 2\n#define RADIOLIB_SX126X_PA_RAMP_80U 3\n"
                     "#define RADIOLIB_SX126X_PA_RAMP_800U 5\n#define RADIOLIB_SX126X_PA_RAMP_1700U 6\n"
                     "#define RADIOLIB_SX126X_PA_RAMP_200U 4\n"
                     "#define RADIOLIB_SX127X_REG_PA_RAMP 10\n"
                     "#define RADIOLIB_LR2021_TX_TEST_MODE_CW 2\n"
                     "#define RADIOLIB_SX126X_STANDBY_XOSC 1\n")
        variants=[(None,False,None,None,7),(6,False,None,None,7),(None,True,None,1,7)]
        variants += [(None,False,us,1,index) for index,us in enumerate((10,20,40,80,200,800,1700,3400))]
        variants += [(6,False,20,1,6),(None,False,20,0,7),(None,True,20,1,1)]
        for ramp,warm,requested,hil,expected in variants:
            with self.subTest(board_packet_ramp=ramp, hil_warm_tx=warm,hil_pa_ramp_us=requested,hil=hil):
                flags = ["-Wno-unused-parameter", "-DUSE_LR2021"]
                flags.append(f"-DEXPECTED_MORSE_RAMP={expected}")
                if hil is not None:
                    flags.append(f"-DMESH_MORSE_HIL={hil}")
                if requested is not None:
                    flags.append(f"-DMESH_MORSE_HIL_PA_RAMP_US={requested}")
                if warm:
                    flags.append("-DMESH_MORSE_HIL_WARM_TX=1")
                if ramp is not None:
                    flags.append(f"-DSX126X_PA_RAMP_TIME={ramp}")
                compile_run(self, code, flags=flags, headers={"Dispatcher.h": portable_radio_header(),
                            "Arduino.h": arduino, "RadioLib.h": radio_lib})
        for requested in (-1,0,30,3401):
            with self.subTest(invalid_hil_ramp_us=requested),self.assertRaisesRegex(AssertionError,"HIL SX126x PA ramp must be"):
                compile_run(self,code,flags=("-Wno-unused-parameter","-DUSE_LR2021","-DEXPECTED_MORSE_RAMP=7",
                            "-DMESH_MORSE_HIL=1",f"-DMESH_MORSE_HIL_PA_RAMP_US={requested}"),headers={
                            "Dispatcher.h":portable_radio_header(),"Arduino.h":arduino,"RadioLib.h":radio_lib})


if __name__ == "__main__":
    unittest.main()
