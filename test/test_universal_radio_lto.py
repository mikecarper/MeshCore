"""Keep LTO scoped to qualified universal images and retain SDK hooks."""
import copy
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("universal_radio_lto", ROOT / "scripts/esp32_universal_radio_lto.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class Environment(dict):
    def __init__(self, *, platform="espressif32", mcu="esp32s3", board="t3_s3_v1_x",
                 target="LilyGo_T3S3_repeater"):
        super().__init__(BOARD=board, PIOENV=target, BUILD_FLAGS=["-flto", "-flto-partition=one"],
                         LINKFLAGS=["-Wl,--gc-sections"], AR="xtensa-esp32s3-elf-gcc-ar",
                         RANLIB="xtensa-esp32s3-elf-gcc-ranlib")
        self.platform, self.mcu = platform, mcu

    def PioPlatform(self):
        return type("Platform", (), {"name": self.platform})()

    def BoardConfig(self):
        return {"build.mcu": self.mcu}

    def AppendUnique(self, **values):
        for key, items in values.items():
            self.setdefault(key, []).extend(item for item in items if item not in self.get(key, []))


class UniversalRadioLtoTests(unittest.TestCase):
    def test_constrained_s3_repeater_bases_have_exact_lto_link_support(self):
        for board, target in (("esp32-s3-devkitc-1", "Heltec_Wireless_Tracker_repeater_observer_mqtt"),
                              ("esp32-s3-devkitc-1", "Heltec_WSL3_repeater_observer_mqtt"),
                              ("heltec_tracker_v2", "heltec_tracker_v2_repeater_observer_mqtt")):
            with self.subTest(target=target):
                env = Environment(board=board, target=target)
                self.assertTrue(POLICY.install(env))
                for flag in POLICY.LINK_FLAGS:
                    self.assertIn(flag, env["LINKFLAGS"])
                self.assertFalse(POLICY.install(Environment(board="other", target=target)))
                self.assertFalse(POLICY.install(Environment(board=board, target=target.replace("repeater", "room_server"))))
                self.assertFalse(POLICY.install(Environment(board=board, target=target.replace("repeater_observer_mqtt", "companion_radio_full"))))

    def test_ebyte_observer_compile_bases_have_lto_link_support(self):
        for target in ("Ebyte_EoRa-S3_repeater_observer_mqtt", "Ebyte_EoRa-S3_room_server_observer_mqtt"):
            env = Environment(board="ebyte_eora-s3", target=target)
            self.assertTrue(POLICY.install(env))
            for flag in POLICY.LINK_FLAGS:
                self.assertIn(flag, env["LINKFLAGS"])
        for target in ("Ebyte_EoRa-S3_room_server", "Ebyte_EoRa-S3_Repeater"):
            with self.subTest(target=target):
                self.assertFalse(POLICY.install(Environment(board="ebyte_eora-s3", target=target)))

    def test_ebyte_lto_is_selected_for_exact_full_observers_and_repeater(self):
        source = (ROOT / "build_legacy.sh").read_text()
        start = source.index("apply_esp32_full_size_profile() {")
        function = source[start:source.index("\n}\n", start) + 2]
        script = r'''
ESP32_FULL_BUILD="$1"
ESP32_FULL_MAX_NEIGHBOURS=254
DRAM_LIMITED_MAX_NEIGHBOURS=50
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_EXTRA_SCRIPTS=""
declare -A PIO_ENV_FULL_WIFI_OTA_BY_NAME=()
supports_esp32_full_build() { return 0; }
is_room_server_role_target() { return 1; }
requires_dram_limited_neighbors() { return 1; }
requires_esp32_companion_full_ota_fallback() { return 1; }
append_platformio_build_unflags() { :; }
append_platformio_extra_script() { PLATFORMIO_EXTRA_SCRIPTS+="$1"; }
record_build_reduction() { :; }
''' + function + r'''
apply_esp32_full_size_profile "$2"
printf '%s\n%s\n' "$PLATFORMIO_BUILD_FLAGS" "$PLATFORMIO_EXTRA_SCRIPTS"
'''
        for full, target, expected in (("1", "Ebyte_EoRa-S3_Repeater", True),
                                       ("1", "Heltec_Wireless_Tracker_repeater", True),
                                       ("1", "Heltec_WSL3_repeater", True),
                                       ("1", "heltec_tracker_v2_repeater", True),
                                       ("0", "Heltec_Wireless_Tracker_repeater", False),
                                       ("1", "Heltec_WSL3_room_server", False),
                                       ("0", "Ebyte_EoRa-S3_Repeater", False),
                                       ("1", "Ebyte_EoRa-S3_room_server", False),
                                       ("1", "Ebyte_EoRa-S3_repeater_observer_mqtt", True),
                                       ("1", "Ebyte_EoRa-S3_room_server_observer_mqtt", True),
                                       ("0", "Ebyte_EoRa-S3_repeater_observer_mqtt", False),
                                       ("0", "Ebyte_EoRa-S3_room_server_observer_mqtt", False)):
            with self.subTest(full=full, target=target):
                result = subprocess.run(["bash", "-c", script, "test", full, target],
                                        capture_output=True, text=True, check=True)
                self.assertEqual("-DMESH_UNIVERSAL_LTO=1" in result.stdout, expected)
                self.assertEqual("post:scripts/esp32_universal_radio_lto.py" in result.stdout, expected)

    def test_link_flags_preserve_existing_linker_and_archive_configuration(self):
        env = Environment()
        before = copy.deepcopy(dict(env))
        self.assertTrue(POLICY.install(env))
        self.assertEqual(env["LINKFLAGS"], ["-Wl,--gc-sections", "-flto", "-flto-partition=one",
                         "-ffunction-sections", "-fdata-sections", "-mtext-section-literals", "-mlongcalls"])
        for key in ("BUILD_FLAGS", "AR", "RANLIB"):
            self.assertEqual(env[key], before[key])
        first = copy.deepcopy(dict(env))
        self.assertTrue(POLICY.install(env))
        self.assertEqual(dict(env), first)

    def test_unknown_legacy_and_other_board_environments_are_unchanged(self):
        for settings in (dict(platform="nordicnrf52"), dict(mcu="esp32"), dict(board="other"),
                         dict(target="LilyGo_T3S3_sx1262_repeater"),
                         dict(target="LilyGo_T3S3_sx1276_repeater"),
                         dict(target="Tbeam_repeater"), dict(target="LilyGo_T3S3_repeater_custom")):
            env = Environment(**settings)
            before = copy.deepcopy(dict(env))
            with self.subTest(settings=settings):
                self.assertFalse(POLICY.install(env))
                self.assertEqual(dict(env), before)

    def test_only_new_tbeam_observers_keep_the_classic_psram_workaround(self):
        for target in ("Tbeam_repeater_observer_mqtt", "Tbeam_room_server_observer_mqtt"):
            env = Environment(mcu="esp32", board="ttgo-t-beam", target=target)
            env["AR"], env["RANLIB"] = "xtensa-esp32-elf-gcc-ar", "xtensa-esp32-elf-gcc-ranlib"
            before = copy.deepcopy(dict(env))
            with self.subTest(target=target):
                self.assertTrue(POLICY.install(env))
                self.assertEqual(env["LINKFLAGS"], [*before["LINKFLAGS"], *POLICY.LINK_FLAGS,
                                                   "-mfix-esp32-psram-cache-issue",
                                                   "-fno-tree-loop-im", "-fno-partial-inlining",
                                                   "-mno-target-align"])
                self.assertFalse(any("rom.newlib-nano" in flag for flag in env["LINKFLAGS"]))
                self.assertEqual(env["AR"], before["AR"])
                self.assertEqual(env["RANLIB"], before["RANLIB"])
                first = copy.deepcopy(dict(env))
                POLICY.install(env)
                self.assertEqual(dict(env), first)
        for target in ("Tbeam_repeater", "Tbeam_room_server", "Tbeam_companion_radio_ble",
                       "Tbeam_companion_radio_full", "Tbeam_kiss_modem",
                       "Tbeam_SX1262_repeater_observer_mqtt", "Tbeam_SX1276_room_server_observer_mqtt"):
            env = Environment(mcu="esp32", board="ttgo-t-beam", target=target)
            before = copy.deepcopy(dict(env))
            with self.subTest(target=target):
                self.assertFalse(POLICY.install(env))
                self.assertEqual(dict(env), before)

    def test_tbeam_observers_preserve_certificate_scripts_and_leave_other_roles_unchanged(self):
        from test_universal_lora_builds import UniversalLoRaBuildTest as Recipes
        Recipes.setUpClass()
        recipes = Recipes()
        hook = "post:scripts/esp32_universal_radio_lto.py"
        selected = {"env:Tbeam_repeater_observer_mqtt", "env:Tbeam_room_server_observer_mqtt"}
        for target in (name for name in recipes.config.sections() if name.startswith("env:Tbeam_")):
            scripts = recipes.option(target, "extra_scripts").split()
            flags = recipes.option(target, "build_flags")
            with self.subTest(target=target):
                if target in selected:
                    legacy = target.replace("env:Tbeam_", "env:Tbeam_SX1262_", 1)
                    original = recipes.option(legacy, "extra_scripts").split()
                    self.assertEqual([item for item in scripts if item != hook], original)
                    self.assertEqual(scripts.count(hook), 1)
                    self.assertIn("pre:scripts/generate_cert_bundle.py", scripts)
                    for flag in (*POLICY.LINK_FLAGS, "-mfix-esp32-psram-cache-issue", "-fno-tree-loop-im",
                                 "-fno-partial-inlining", "-mno-target-align", "-D MESH_UNIVERSAL_LTO=1"):
                        self.assertIn(flag, flags)
                    self.assertNotIn("-fno-inline-small-functions", flags)
                else:
                    self.assertNotIn(hook, scripts)
                    self.assertNotIn("-flto", flags)
                    self.assertNotIn("MESH_UNIVERSAL_LTO", flags)

    def test_every_new_role_keeps_all_legacy_scripts_and_adds_one_lto_hook(self):
        from test_universal_lora_builds import UniversalLoRaBuildTest as Recipes
        Recipes.setUpClass()
        recipes = Recipes()
        hook = "post:scripts/esp32_universal_radio_lto.py"
        targets = [name for name in recipes.config.sections()
                   if name.startswith("env:LilyGo_T3S3_") and "_sx12" not in name]
        self.assertEqual(len(targets), 10)
        for target in targets:
            name = target.removeprefix("env:")
            legacy = target.replace("env:LilyGo_T3S3_", "env:LilyGo_T3S3_sx1262_", 1)
            current = recipes.option(target, "extra_scripts").split()
            old = recipes.option(legacy, "extra_scripts").split()
            with self.subTest(target=target):
                self.assertEqual(current.count(hook), 1)
                self.assertEqual([script for script in current if script != hook], old)
                self.assertTrue(POLICY.install(Environment(target=name)))
                self.assertNotIn(hook, old)
                for flag in ("-flto", "-flto-partition=one", "-ffunction-sections", "-fdata-sections",
                             "-mtext-section-literals", "-mlongcalls"):
                    self.assertIn(flag, recipes.option(target, "build_flags"))
                self.assertIn("-D MESH_UNIVERSAL_LTO=1", recipes.option(target, "build_flags"))
                self.assertNotIn("MESH_UNIVERSAL_LTO", recipes.option(legacy, "build_flags"))
                self.assertNotIn("-fno-tree-loop-im", recipes.option(target, "build_flags"))
                self.assertNotIn("-fno-partial-inlining", recipes.option(target, "build_flags"))
                self.assertNotIn("-fno-inline-small-functions", recipes.option(target, "build_flags"))
                self.assertNotIn("-mno-target-align", recipes.option(target, "build_flags"))

    def test_real_dispatcher_queue_remains_executable_and_visible_after_lto(self):
        from test_meshtext_transport import HARNESS
        compiler, nm = shutil.which("g++"), shutil.which("nm")
        self.assertTrue(compiler and nm)
        fixture = HARNESS.split("int main()", 1)[0]
        fixture = fixture.replace("RadioProfileProtocol::Meshtext", "RadioProfileProtocol::Kiss")
        fixture = fixture.replace("bool supportsMeshtext() const override", "bool supportsHostKiss() const override")
        fixture = fixture.replace("return queueForeignRadioFrame(b,n,1,_radio->profiles()->generation[1]);",
                                  "return submitHostKissFrame(b,n,_radio->profiles()->generation[1]);")
        fixture += r'''
namespace mesh { namespace hostkiss {
void receive(const uint8_t*,size_t,uint8_t,uint32_t,float,float) {}
void sendComplete(bool) {}
} }
int main() {
  Fixture f;
  assert(f.d.queue(bytes));
  assert(!f.d.queue(bytes)); // The real bounded scheduler still rejects overlap.
  f.tick();
  assert(f.radio.sends==1 && f.radio.active==1);
  assert(f.radio.sent==std::vector<uint8_t>(bytes,bytes+sizeof(bytes)));
}
'''
        with tempfile.TemporaryDirectory(prefix="universal-lto-proof-") as directory:
            path = Path(directory)
            source, binary = path / "fixture.cpp", path / "fixture.exe"
            source.write_text(fixture)
            args = [compiler, "-std=c++17", "-Os", "-flto", "-flto-partition=one",
                    "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                    "-DMESH_UNIVERSAL_LTO=1", "-DMESH_ENABLE_HOST_KISS=1",
                    "-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_MESHTEXT_WIRE=0",
                    "-DMESH_ENABLE_ETHERNET_WIRE=0", "-DMESH_ENABLE_TX_BEACONS=0",
                    "-I" + str(ROOT / "test/mocks"), "-I" + str(ROOT / "src"),
                    *[str(ROOT / "src" / name) for name in (
                        "Dispatcher.cpp", "Packet.cpp", "helpers/StaticPoolPacketManager.cpp")],
                    str(source), "-o", str(binary)]
            built = subprocess.run(args, capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            symbols = subprocess.check_output([nm, "--defined-only", str(binary)], text=True)
            self.assertIn("_ZN4mesh10Dispatcher22queueForeignRadioFrameE", symbols)

    def test_real_meshtext_receive_codec_survives_lto_and_decodes_a_frame(self):
        required = ("AESCommon.cpp", "AES128.cpp", "AES256.cpp", "BlockCipher.cpp", "Crypto.cpp")
        override = os.environ.get("MESHCORE_CRYPTO_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if all((path / name).is_file() for name in required)), None)
        if crypto is None:
            self.skipTest("existing rweather/Crypto checkout needed (MESHCORE_CRYPTO_DIR)")
        compiler, nm = shutil.which("g++"), shutil.which("nm")
        self.assertTrue(compiler and nm)
        program = r'''
#include <helpers/MeshtextChat.h>
#include <cassert>
#include <cstring>
int main(int argc,char** argv) {
  using namespace mesh::mt;
  const char* text=argc>1 ? argv[1] : "LTO proof";
  const size_t length=strlen(text);
  uint8_t frame[MaxFrameSize];
  const size_t count=encodeText(0x11223344,0x89abcdef,3,"LongFast",DefaultPsk,16,text,length,frame);
  assert(count>HeaderSize);
  DecodedText decoded;
  assert(decodeText(frame,count,"LongFast",DefaultPsk,16,0x99887766,decoded));
  assert(decoded.text_len==length && !memcmp(decoded.text,text,length));
  assert(!decodeText(frame,0,"LongFast",DefaultPsk,16,0x99887766,decoded));
}
'''
        with tempfile.TemporaryDirectory(prefix="universal-lto-codec-") as directory:
            path = Path(directory)
            source, binary = path / "fixture.cpp", path / "fixture.exe"
            source.write_text(program)
            args = [compiler, "-std=c++11", "-Os", "-flto", "-flto-partition=one",
                    "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                    "-DMESH_UNIVERSAL_LTO=1", "-DMESH_ENABLE_MESHTEXT_CHAT=1",
                    "-I" + str(ROOT / "src"), "-I" + str(crypto), str(source),
                    str(ROOT / "src/helpers/MeshtextChat.cpp"),
                    *[str(crypto / name) for name in required], "-o", str(binary)]
            built = subprocess.run(args, capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary), "LTO encrypted frame"], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            symbols = subprocess.check_output([nm, "--defined-only", str(binary)], text=True)
            self.assertIn("_ZN4mesh2mt10decodeTextE", symbols)


if __name__ == "__main__":
    unittest.main()
