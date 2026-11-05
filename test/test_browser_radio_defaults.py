"""Host-only validation of the fixed browser-patchable radio-defaults ABI."""

from pathlib import Path
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from cpp_source import body, _uncommented


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "test/fixtures/browser_radio_defaults.cpp"
MAGIC = b"MCRDEFAULTS0001\0"


class BrowserRadioDefaultsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise AssertionError("a host C++17 compiler is required")
        cls.compiler = compiler
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-browser-radio-defaults-")
        cls.addClassCleanup(cls.temp.cleanup)
        suffix = ".exe" if os.name == "nt" else ""
        cls.decoder = Path(cls.temp.name) / ("decoder" + suffix)
        cls.linked = Path(cls.temp.name) / ("linked-image" + suffix)
        cls.startup = {}
        sanitizers = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-pie", "-no-pie",
        ]
        common = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                  *sanitizers, "-I", str(ROOT / "src"), str(SOURCE)]
        # No production .cpp is linked here: the decoder/checksum must remain
        # usable by a portable host-only consumer of the public header.
        cls.compile([*common, "-o", str(cls.decoder)])
        # Confirm linker-plugin support, not merely acceptance of -flto at
        # compilation. Use LTO on the actual production read()/image build
        # whenever the selected host toolchain can link a minimal LTO program.
        probe = subprocess.run(
            [compiler, "-x", "c++", "-", "-flto", "-o",
             str(Path(cls.temp.name) / ("lto-probe" + suffix))],
            input="int main() { return 0; }\n", capture_output=True,
            text=True, timeout=40)
        cls.lto_flags = ["-flto"] if probe.returncode == 0 else []
        cls.compile([*common, "-DBROWSER_RADIO_DEFAULTS_IMAGE_ONLY",
                     *cls.lto_flags,
                     str(ROOT / "src/helpers/BrowserRadioDefaults.cpp"),
                     "-o", str(cls.linked)])
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        infrastructure = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        # Compile the real startup functions/branch without pulling in boards,
        # Arduino or RF drivers. Mocks record acceptance and checked writes.
        extracted = Path(cls.temp.name) / "startup-functions.h"
        extracted.write_text(
            "#if defined(BROWSER_RADIO_DEFAULTS_STORE_GATE)\n" +
            body(store, "bool DataStore::canSeedBrowserRadioDefaults() const")
            .replace("DataStore::", "") +
            "\n#elif defined(BROWSER_RADIO_DEFAULTS_COMPANION_SEED)\n" +
            body(companion, "void MyMesh::seedBrowserRadioDefaults(bool prefs_ready)")
            .replace("MyMesh::", "") +
            "\n#elif defined(BROWSER_RADIO_DEFAULTS_INFRASTRUCTURE_SEED)\n" +
            body(infrastructure, "bool CommonCLI::acceptsPrimaryRadioParams(")
            .replace("CommonCLI::", "") +
            "\nvoid seedInfrastructure(bool is_fresh_install, MockFS* fs) {\n" +
            body(body(infrastructure, "void CommonCLI::loadPrefs(FILESYSTEM* fs)"),
                 "if (is_fresh_install) {") + "\n}\n#endif\n", encoding="utf-8")
        for name, flags in {
            "generic": [], "nrf52": ["-DNRF52_PLATFORM"],
            "generic_no_tx_validator": ["-DBROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR"],
            "nrf52_no_tx_validator": ["-DNRF52_PLATFORM",
                                      "-DBROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR"],
        }.items():
            binary = Path(cls.temp.name) / ("startup-" + name + suffix)
            cls.compile([*common, "-DBROWSER_RADIO_DEFAULTS_STARTUP_ONLY", *flags,
                         '-DBROWSER_RADIO_DEFAULTS_STARTUP_IMPLEMENTATION="startup-functions.h"',
                         "-I", cls.temp.name, "-o", str(binary)])
            cls.startup[name] = binary

    @classmethod
    def compile(cls, command):
        built = subprocess.run(command, capture_output=True, text=True, timeout=40)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        ran = subprocess.run([str(self.decoder), case], capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_little_endian_fields_and_ieee_crc_with_every_single_bit_corruption(self):
        self.run_case("endian_crc")

    def test_exact_magic_version_record_and_buffer_lengths_null_and_unaligned_input(self):
        self.run_case("metadata")

    def test_unknown_flags_reserved_bytes_and_tx_without_override_fail_closed(self):
        self.run_case("flags_reserved")

    def test_all_radio_bounds_and_entire_sf_cr_tx_and_preamble_domains(self):
        self.run_case("bounds")

    def test_disabled_and_tx_only_records_never_change_output(self):
        self.run_case("disabled")

    def test_valid_minimum_maximum_and_automatic_preamble_combinations(self):
        self.run_case("boundaries")

    def test_fresh_store_requires_confirmed_absence_of_every_known_settings_file(self):
        self.run_case("fresh_store")

    def run_startup_case(self, case):
        for platform, binary in self.startup.items():
            with self.subTest(platform=platform):
                ran = subprocess.run([str(binary), case], capture_output=True,
                                     text=True, timeout=10)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_actual_companion_store_gate_blocks_incomplete_and_nrf_storage_ambiguity(self):
        self.run_startup_case("store_gate")

    def test_actual_companion_seed_accepts_before_writes_and_checks_both_rollback_results(self):
        self.run_startup_case("companion")

    def test_actual_infrastructure_seed_restores_all_fields_after_rx_or_save_failure(self):
        self.run_startup_case("infrastructure")

    def test_actual_infrastructure_tx_validation_rejects_unsupported_power_before_mutation(self):
        self.run_startup_case("infrastructure_tx")

    def test_companion_startup_seeds_after_load_and_before_any_radio_configuration(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        startup = _uncommented(body(source, "void MyMesh::begin("))
        self.assertLess(startup.index("_store->loadPrefs("),
                        startup.index("seedBrowserRadioDefaults(prefs_ready);"))
        self.assertLess(startup.index("seedBrowserRadioDefaults(prefs_ready);"),
                        startup.index("configureRadioFromPrefs();"))
        self.assertEqual(startup.count("seedBrowserRadioDefaults(prefs_ready);"), 1)

    def test_optional_feature_excludes_marker_and_read_from_linked_image(self):
        probe = Path(self.temp.name) / "optional-feature.cpp"
        probe.write_text('''#include "helpers/BrowserRadioDefaults.h"
int main() {
#if MESH_ENABLE_BROWSER_RADIO_DEFAULTS
  mesh::browserRadioDefaults::Settings settings{};
  return mesh::browserRadioDefaults::read(settings) ? 1 : 0;
#else
  return 0;
#endif
}
''', encoding="utf-8")
        for mode, flag, expected in (("default", [], 1),
                                     ("enabled", ["-DMESH_ENABLE_BROWSER_RADIO_DEFAULTS=1"], 1),
                                     ("disabled", ["-DMESH_ENABLE_BROWSER_RADIO_DEFAULTS=0"], 0)):
            with self.subTest(mode=mode):
                output = Path(self.temp.name) / ("optional-" + mode + (".exe" if os.name == "nt" else ""))
                self.compile([self.compiler, "-std=c++17", "-O2", *flag,
                              "-I", str(ROOT / "src"), str(probe),
                              str(ROOT / "src/helpers/BrowserRadioDefaults.cpp"), "-o", str(output)])
                self.assertEqual(output.read_bytes().count(MAGIC), expected)
                self.assertEqual(subprocess.run([str(output)], timeout=10).returncode, 0)

    def test_disabled_feature_omits_both_real_startup_seeding_paths(self):
        def gated_region(source, anchor):
            # Extract the actual surrounding #if, including nested directives.
            start = source.rfind("#if MESH_ENABLE_BROWSER_RADIO_DEFAULTS", 0, source.index(anchor))
            self.assertNotEqual(start, -1)
            depth = 0
            result = []
            for line in source[start:].splitlines(keepends=True):
                stripped = line.strip()
                if stripped.startswith(("#if ", "#ifdef ", "#ifndef ")):
                    depth += 1
                elif stripped.startswith("#endif"):
                    depth -= 1
                result.append(line)
                if depth == 0:
                    return "".join(result)
            self.fail("unterminated feature gate")

        for file, anchor in (("examples/companion_radio/MyMesh.cpp", "seedBrowserRadioDefaults(prefs_ready);"),
                             ("src/helpers/CommonCLI.cpp", "mesh::browserRadioDefaults::read(value)")):
            region = gated_region((ROOT / file).read_text(), anchor)
            for enabled in (0, 1):
                with self.subTest(file=file, enabled=enabled):
                    result = subprocess.run(
                        [self.compiler, "-E", "-P", "-x", "c++", "-",
                         "-DMESH_ENABLE_BROWSER_RADIO_DEFAULTS=" + str(enabled)],
                        input=region, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(anchor in result.stdout, bool(enabled))

    def test_only_space_limited_profiles_opt_out(self):
        for path, profile in (("variants/rak3x72/platformio.ini", "RAK_3x72_companion_radio_usb"),
                              ("variants/wio-e5-dev/platformio.ini", "wio-e5_companion_radio_usb"),
                              ("variants/wio-e5-mini/platformio.ini", "wio-e5-mini_repeater"),
                              ("variants/wio-e5-mini/platformio.ini", "wio-e5-mini_companion_radio_usb")):
            section = (ROOT / path).read_text().split("[env:" + profile + "]", 1)[1].split("[", 1)[0]
            self.assertIn("-D MESH_ENABLE_BROWSER_RADIO_DEFAULTS=0", section)

    def test_linked_image_is_one_canonical_disabled_patch_template(self):
        ran = subprocess.run([str(self.linked)], capture_output=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stderr.decode(errors="replace"))
        image = ran.stdout
        self.assertEqual(len(image), 64)
        self.assertEqual(image[:16], MAGIC)
        self.assertEqual(struct.unpack_from("<HHI", image, 16), (1, 64, 0))
        self.assertEqual(image[24:60], bytes(36))
        self.assertEqual(struct.unpack_from("<I", image, 60)[0],
                         zlib.crc32(image[:60]))
        self.assertEqual(self.linked.read_bytes().count(MAGIC), 1,
                         "the linked image must contain exactly one full patch marker")

    def test_production_read_observes_binary_patched_after_linking_and_lto(self):
        # Keep the original executable unchanged so its canonical disabled
        # default and uniqueness are tested independently of execution order.
        original = self.linked.read_bytes()
        self.assertEqual(original.count(MAGIC), 1)
        offset = original.index(MAGIC)
        pristine = subprocess.run([str(self.linked)], capture_output=True, timeout=10)
        self.assertEqual(pristine.returncode, 0, pristine.stderr.decode(errors="replace"))
        self.assertEqual(original[offset:offset + 64], pristine.stdout)
        self.assertEqual(struct.unpack_from("<HHI", pristine.stdout, 16), (1, 64, 0))

        enabled = bytearray(64)
        enabled[:16] = MAGIC
        struct.pack_into("<HHIII", enabled, 16, 1, 64, 3, 915250000, 125000)
        struct.pack_into("<BBbBH", enabled, 32, 10, 7, -3, 0, 257)
        struct.pack_into("<I", enabled, 60, zlib.crc32(enabled[:60]))
        patched = original[:offset] + enabled + original[offset + 64:]
        self.assertEqual(len(patched), len(original))
        self.assertEqual(patched.count(MAGIC), 1)
        binary = self.linked.with_name("patched-" + self.linked.name)
        shutil.copy2(self.linked, binary)  # Preserve executable mode on Linux.
        binary.write_bytes(patched)
        ran = subprocess.run([str(binary), "patched-read"], capture_output=True,
                             text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout, "patched-read OK\n")
        self.assertEqual(self.linked.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
