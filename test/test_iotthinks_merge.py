"""PowerSaving-v17 target names and FEM defaults must survive integration."""
import configparser
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class IotthinksMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None,
                                               inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))],
                        encoding="utf-8")

    def flags(self, section, trail=()):
        self.assertNotIn(section, trail, "cyclic PlatformIO flag inheritance")
        config = self.config[section]
        value = config.get("build_flags")
        if value is None:
            # PlatformIO aliases inherit an option from their extends chain.
            # The last parent defining that option takes precedence.
            parents = re.split(r"[,\n]", config.get("extends", ""))
            for parent in reversed(parents):
                if parent.strip():
                    inherited = self.flags(parent.strip(), trail + (section,))
                    if inherited:
                        return inherited
            return ""
        return re.sub(r"\$\{([^}]+)\}",
                      lambda match: self.flags(match[1].rsplit(".", 1)[0],
                                               trail + (section,)), value)

    def test_old_v43_femoff_target_remains_alias_for_new_name(self):
        self.assertEqual(self.config["env:heltec_v4_3_companion_radio_ble_femoff"]["extends"],
                         "env:heltec_v4_companion_radio_ble_femoff")
        self.assertEqual(self.config["env:heltec_v4_3_companion_radio_ble_ps_femoff"]["extends"],
                         "env:heltec_v4_3_companion_radio_ble_femoff")
        self.assertIn("-D RADIO_FEM_RXGAIN=0", self.flags("env:heltec_v4_companion_radio_ble_femoff"))

    def test_all_shipping_fem_profiles_and_plain_aliases_default_off(self):
        profiles = [name for name in self.config.sections()
                    if name.startswith("env:") and (
                        "femon" in name or "femoff" in name or
                        ("companion_radio" in name and name.startswith((
                            "env:heltec_v4", "env:Heltec_t096", "env:heltec_tracker_v2"))))]
        self.assertGreater(len(profiles), 40)
        for section in profiles:
            with self.subTest(section=section):
                definitions = re.findall(r"-D\s*RADIO_FEM_RXGAIN\s*=\s*([01])\b",
                                         self.flags(section))
                # Undefined now means OFF; legacy femon IDs remain published
                # for update compatibility while their shipping default is OFF.
                self.assertEqual(definitions[-1] if definitions else "0", "0")
        for section in ("env:Heltec_t096_companion_radio_ble_femon",
                        "env:heltec_v4_companion_radio_ble_femon",
                        "env:heltec_tracker_v2_companion_radio_ble_femon"):
            self.assertIn("-D RADIO_FEM_RXGAIN=0", self.flags(section))

    def test_actual_companion_default_and_loaded_user_override(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        start = source.index("#if defined(RADIO_FEM_RXGAIN)")
        default = source[start:source.index("#endif", start) + len("#endif")]
        start = source.index("  _prefs.radio_fem_rxgain_override = constrain(")
        normalization = source[start:source.index("  _prefs.radio_fem_txgain = constrain(", start)]
        initialization = "_prefs.radio_fem_rxgain = DEFAULT_FEM_RX_GAIN;"
        self.assertIn(initialization, source)
        harness = r"""
#include <cassert>
#include <cstdint>
#define constrain(value, low, high) ((value) < (low) ? (low) : ((value) > (high) ? (high) : (value)))
@DEFAULT@
struct Prefs { uint8_t radio_fem_rxgain = 99, radio_fem_rxgain_override = 0; };
struct MyMesh {
  Prefs _prefs;
  void fresh() { @INITIALIZATION@ }
  void loaded() { @NORMALIZATION@ }
};
int main() {
  static_assert(DEFAULT_FEM_RX_GAIN == EXPECTED_DEFAULT, "FEM build default");
  MyMesh fresh; fresh.fresh();
  assert(fresh._prefs.radio_fem_rxgain == EXPECTED_DEFAULT);
  assert(fresh._prefs.radio_fem_rxgain_override == 0);
  for (uint8_t saved : {0, 1}) for (uint8_t explicit_choice : {0, 1}) {
    MyMesh updated;
    updated._prefs.radio_fem_rxgain = saved;
    updated._prefs.radio_fem_rxgain_override = explicit_choice;
    updated.loaded();
    assert(updated._prefs.radio_fem_rxgain == (explicit_choice ? saved : EXPECTED_DEFAULT));
    assert(updated._prefs.radio_fem_rxgain_override == explicit_choice);
  }
}
""".replace("#include <cstdint>", "#include <cstdint>\n#include <initializer_list>")
        harness = harness.replace("@DEFAULT@", default).replace("@INITIALIZATION@", initialization)
        harness = harness.replace("@NORMALIZATION@", normalization)
        with tempfile.TemporaryDirectory(prefix="companion-fem-default-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(harness)
            for flags, expected in (([], 0), (["-DRADIO_FEM_RXGAIN=0"], 0),
                                    (["-DRADIO_FEM_RXGAIN=1"], 1)):
                with self.subTest(flags=flags):
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                            *flags, "-DEXPECTED_DEFAULT=" + str(expected),
                                            str(cpp), "-o", str(binary)], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_actual_common_legacy_tail_default_and_saved_values(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        defaults = source[source.index("// Build-profile defaults - overwritten below"):]
        initialization = re.search(r"_prefs->radio_fem_rxgain = [01];", defaults).group()
        reader = body(source, "if (file.available() >= (int)sizeof(_prefs->radio_fem_rxgain))")
        harness = r"""
#include <cassert>
#include <cstdint>
#include <initializer_list>
struct Prefs { uint8_t radio_fem_rxgain = 1; };
struct File {
  int saved;
  int available() const { return saved < 0 ? 0 : 1; }
  void read(uint8_t* value, unsigned) { *value = uint8_t(saved); }
};
struct CommonCLI {
  Prefs prefs;
  Prefs* _prefs = &prefs;
  void load(File& file) { @INITIALIZATION@ @READER@ }
};
int main() {
  for (int saved : {-1, 0, 1}) {
    CommonCLI cli;
    File file{saved};
    cli.load(file);
    assert(cli.prefs.radio_fem_rxgain == (saved < 0 ? 0 : saved));
    assert(file.saved == saved); // Reading legacy preferences must not rewrite them.
  }
}
""".replace("@INITIALIZATION@", initialization).replace("@READER@", reader)
        with tempfile.TemporaryDirectory(prefix="common-fem-tail-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(harness)
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    str(cpp), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_actual_fem_controllers_construct_with_lna_off(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="fem-controller-default-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            for variant in ("heltec_v4", "heltec_v4_r8", "heltec_t096",
                            "heltec_tracker_v2", "station_g3_esp32"):
                with self.subTest(variant=variant):
                    cpp.write_text('#include <cassert>\n#include "variants/' + variant +
                                   '/LoRaFEMControl.h"\nint main() { LoRaFEMControl fem; '
                                   'assert(!fem.isLNAEnabled()); }\n')
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                            "-I" + str(ROOT), str(cpp), "-o", str(binary)],
                                           capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_new_serial_bridge_targets_keep_distinct_uarts(self):
        rak = self.flags("env:RAK_3401_repeater_bridge_rs232_serial1")
        tracker = self.flags("env:heltec_tracker_v2_repeater_bridge_rs232")
        self.assertIn("-D WITH_RS232_BRIDGE=Serial1", rak)
        self.assertIn("-D WITH_RS232_BRIDGE_UART=1", rak)
        self.assertIn("-UENV_INCLUDE_GPS", rak)
        self.assertIn("-D ENV_INCLUDE_GPS=0", rak)
        self.assertIn("-D WITH_RS232_BRIDGE=Serial2", tracker)
        self.assertIn("-D WITH_RS232_BRIDGE_UART=2", tracker)
        self.assertIn("-D WITH_RS232_BRIDGE_RX=44", tracker)
        self.assertIn("-D WITH_RS232_BRIDGE_TX=43", tracker)


if __name__ == "__main__":
    unittest.main()
