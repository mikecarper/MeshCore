import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nrf52_flash_trim", ROOT / "scripts/nrf52_flash_trim.py")
TRIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIM)


class Nrf52FlashTrimTest(unittest.TestCase):
    def policy(self, name="RAK_4631_repeater", role="simple_repeater", **kwargs):
        options = dict(platform="nordicnrf52", mcu="nrf52840", env_name=name,
                       src_filter="+<*.cpp> +<../examples/%s>" % role)
        options.update(kwargs)
        return TRIM.trim_policy(**options)

    def test_every_infrastructure_role_keeps_compact_crypto_and_names(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor", "kiss_modem"):
            with self.subTest(role=role):
                defines = self.policy(role=role)
                for name in ("MESH_NRF52_FLASH_TRIM", "ED25519_COMPACT_BASE",
                             "ED25519_COMPACT_SHA512", "OTA_TARGET_NAME_FRONT_CODED",
                             "SSD1306_NO_SPLASH"):
                    self.assertEqual(defines[name], 1)
                self.assertEqual(defines["CFG_TUD_HID"], 0)
                self.assertNotIn("CFG_TUD_CDC", defines)
                self.assertFalse(any(name.startswith("CFG_TUH") for name in defines))

    def test_every_companion_alias_is_excluded(self):
        for suffix in ("companion_radio_full", "companion_radio_usb", "companion_radio_ble",
                       "companion_radio_wifi", "companion_radio_serial", "companion_radio_ethernet",
                       "companion_usb", "companion_ble", "comp_radio_usb", "terminal_chat", "term_chat"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.policy(name="RAK_4631_" + suffix), {})

    def test_source_filter_and_synthetic_alias_also_exclude_companion(self):
        for role in ("companion_radio/*.cpp", "simple_secure_chat/main.cpp"):
            self.assertEqual(self.policy(name="custom", role=role), {})
        for flags in ('-DCOMPANION_RADIO_FULL=1', '-D COMPANION_FEATURE_USB_MOTA_SOURCE=1',
                      '-DOTA_VARIANT=\'"RAK_4631_terminal_chat"\'',
                      '-DOTA_VARIANT=\'"RAK_4631_companion_radio_full"\''):
            with self.subTest(flags=flags):
                self.assertEqual(self.policy(flags=flags), {})
        self.assertEqual(self.policy(cppdefines=[("COMPANION_RADIO_FULL", 1)]), {})

    def test_unknown_roles_other_platforms_and_explicit_optout_are_unchanged(self):
        for options in (dict(role="custom"), dict(platform="espressif32", mcu="esp32"),
                        dict(platform="ststm32", mcu="stm32f411"),
                        dict(flags="-DMESH_NRF52_FLASH_TRIM=0")):
            self.assertEqual(self.policy(**options), {})

    def test_effective_filter_is_ordered_and_does_not_include_outside_source_by_wildcard(self):
        self.assertEqual(self.policy(src_filter="+<*> +<../examples/simple_repeater> -<../examples/simple_repeater>"), {})
        self.assertEqual(self.policy(src_filter="+<*>"), {})
        self.assertTrue(self.policy(src_filter="+<../examples/companion_radio> -<../examples/companion_radio> +<../examples/simple_repeater>"))

    def test_board_and_user_defines_are_not_overridden(self):
        defines = self.policy(flags="-D CFG_TUD_HID=1 -UED25519_COMPACT_BASE -USSD1306_NO_SPLASH")
        self.assertNotIn("CFG_TUD_HID", defines)
        self.assertNotIn("ED25519_COMPACT_BASE", defines)
        self.assertNotIn("SSD1306_NO_SPLASH", defines)
        self.assertEqual(TRIM.flag_defines("-DMESH_NRF52_FLASH_TRIM=1 -UMESH_NRF52_FLASH_TRIM")["MESH_NRF52_FLASH_TRIM"], None)

    def test_rak_repeater_trims_follow_hardware_and_effective_role(self):
        for board in ("RAK_3401", "RAK_4631"):
            for suffix in ("repeater", "repeater_lora_ota_no_external_sensors",
                           "repeater_unified_lora_ota", "repeater_bridge_rs232_serial1"):
                with self.subTest(board=board, suffix=suffix):
                    defines = self.policy(name=board + "_" + suffix, flags="-D" + board)
                    for name in ("ED25519_COMPACT_SCALAR", "MESH_CLI_SHARE_REPLIES"):
                        self.assertEqual(defines[name], 1)
                    self.assertNotIn("ED25519_COMPACT_FIELD", defines)
            for role in ("simple_room_server", "simple_sensor", "kiss_modem", "companion_radio"):
                defines = self.policy(role=role, flags="-D" + board)
                for name in TRIM.RAK_REPEATER_TRIM_DEFINES:
                    self.assertNotIn(name, defines)
        for options in (dict(flags="-DRAK_3401=0"), dict(mcu="nrf52832", flags="-DRAK_3401"),
                        dict(src_filter="+<../examples/simple_repeater> +<../examples/simple_sensor>",
                             flags="-DRAK_4631")):
            for name in TRIM.RAK_REPEATER_TRIM_DEFINES:
                self.assertNotIn(name, self.policy(**options))
        defines = self.policy(flags="-DRAK_3401 -DED25519_COMPACT_SCALAR=0 "
                              "-UED25519_COMPACT_FIELD -DMESH_CLI_SHARE_REPLIES=0")
        for name in TRIM.RAK_REPEATER_TRIM_DEFINES:
            self.assertNotIn(name, defines)

    def test_local_names_honor_overlay_identity_and_explicit_defines(self):
        flags = ('-DMESH_OTA_LOCAL_TARGET_NAME_ONLY=1 '
                 '-DMOTA_TARGET_ID=0x29a0da19 '
                 '-DOTA_VARIANT=\'"RAK_4631_repeater_lora_ota_no_external_sensors"\'')
        defines = self.policy(flags=flags)
        self.assertEqual(defines["OTA_TARGET_NAME_TABLE"], 0)
        self.assertEqual(defines["OTA_LOCAL_TARGET_ID"], "0x29a0da19")
        self.assertEqual(defines["OTA_LOCAL_TARGET_NAME"], "OTA_VARIANT")
        explicit = self.policy(flags=flags + ' -DOTA_TARGET_NAME_TABLE=1 '
                               '-DOTA_LOCAL_TARGET_ID=7 -DOTA_LOCAL_TARGET_NAME=custom_name')
        for name in ("OTA_TARGET_NAME_TABLE", "OTA_LOCAL_TARGET_ID", "OTA_LOCAL_TARGET_NAME"):
            self.assertNotIn(name, explicit)
        for flags in ("", "-DMESH_OTA_LOCAL_TARGET_NAME_ONLY=0"):
            self.assertNotIn("OTA_TARGET_NAME_TABLE", self.policy(flags=flags))

    def test_cli_reply_copy_flag_is_attached_only_to_exact_cli_object(self):
        class Node:
            def __init__(self, path): self.path = path
            def srcnode(self): return self
            def get_abspath(self): return str(self.path)

        class Env(dict):
            def subst(self, value): return value.replace("$PROJECT_DIR", str(ROOT))
            def Object(self, node, **flags): return node, flags

        env = Env(CCFLAGS=["-Os", "-flto"])
        cli = Node(ROOT / "src/helpers/CommonCLI.cpp")
        result, flags = TRIM.compile_shared_cli_replies(env, cli)
        self.assertIs(result, cli)
        self.assertEqual(flags["CCFLAGS"], ["-Os", "-flto", "-fno-builtin-strcpy"])
        self.assertEqual(env["CCFLAGS"], ["-Os", "-flto"])
        other = Node(ROOT / "test/fixtures/CommonCLI.cpp")
        self.assertIs(TRIM.compile_shared_cli_replies(env, other), other)

    def test_companion_shared_replies_are_limited_to_the_qualified_rak_full_image(self):
        defaults = dict(platform="nordicnrf52", mcu="nrf52840",
                        env_name="RAK_4631_companion_radio_full",
                        src_filter="+<../examples/companion_radio/*.cpp>")
        self.assertTrue(TRIM.companion_shared_replies_policy(**defaults))
        for changes in (dict(env_name="RAK_3401_companion_radio_full"),
                        dict(env_name="RAK_4631_companion_radio_usb"),
                        dict(env_name="custom_companion_radio_full"),
                        dict(mcu="nrf52832"), dict(platform="ststm32"),
                        dict(src_filter="+<../examples/simple_repeater>"),
                        dict(src_filter="+<../examples/companion_radio> +<../examples/simple_sensor>"),
                        dict(flags="-DMESH_CLI_SHARE_REPLIES=0"),
                        dict(flags="-UMESH_CLI_SHARE_REPLIES"),
                        dict(flags="-DMESH_CLI_SHARE_REPLY_COPIES=0"),
                        dict(flags="-UMESH_CLI_SHARE_REPLY_COPIES"),
                        dict(flags="-DMESH_NRF52_COMPANION_TRIM=0")):
            with self.subTest(changes=changes):
                self.assertFalse(TRIM.companion_shared_replies_policy(**(defaults | changes)))

    @unittest.skipUnless(shutil.which("g++") and shutil.which("nm") and shutil.which("objdump"),
                         "native GNU C++ and binutils required")
    def test_shared_reply_copy_keeps_libc_bytes_return_and_one_out_of_line_lto_copy(self):
        first = r'''
#include <cstdio>
#include <cstring>
#include <helpers/CLIReplyCopy.h>
extern char* other(char*, const char*);
int main() {
  const char* values[] = {"", "OK", "Error: saved; pending", "UTF8: \xc3\xa9"};
  for (const char* value : values) {
    char buffer[164];
    memset(buffer, 0x5a, sizeof(buffer));
    if (strcpy(buffer + 3, value) != buffer + 3) return 1;
    if (strcmp(buffer + 3, value) || buffer[2] != 0x5a ||
        buffer[4 + strlen(value)] != 0x5a) return 2;
    memset(buffer, 0x5a, sizeof(buffer));
    if (other(buffer + 3, value) != buffer + 3) return 3;
    if (strcmp(buffer + 3, value) || buffer[2] != 0x5a ||
        buffer[4 + strlen(value)] != 0x5a) return 4;
  }
  return 0;
}
'''
        second = r'''
#include <cstring>
#include <helpers/CLIReplyCopy.h>
char* other(char* destination, const char* source) { return strcpy(destination, source); }
'''
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "first.cpp").write_text(first)
            (folder / "second.cpp").write_text(second)
            for enabled in (0, 1):
                binary = folder / ("copies-" + str(enabled))
                subprocess.run(["g++", "-std=c++11", "-Os", "-flto", "-Wall", "-Wextra", "-Werror",
                    "-DMESH_CLI_SHARE_REPLY_COPIES=" + str(enabled), "-I" + str(ROOT / "src"),
                    str(folder / "first.cpp"), str(folder / "second.cpp"),
                    str(ROOT / "src/helpers/CLIReplyCopy.cpp"), "-o", str(binary)], check=True)
                subprocess.run([str(binary)], check=True)
                symbols = subprocess.check_output(["nm", "-C", "--defined-only", str(binary)], text=True)
                copies = [line for line in symbols.splitlines() if "mesh::cli::copyReply(" in line]
                self.assertEqual(len(copies), enabled)
                if enabled:
                    self.assertNotIn("clone", copies[0])
                    assembly = subprocess.check_output(["objdump", "-d", "-C", str(binary)], text=True)
                    self.assertRegex(assembly, r'(?:call|jmp)[^\n]*mesh::cli::copyReply\(')

        for name in ("src/helpers/CommonCLI.cpp", "examples/companion_radio/MyMesh.cpp"):
            text = (ROOT / name).read_text()
            following = text.split("CLIReplyCopy.h", 1)[1]
            self.assertNotRegex(following, r'\bstd::strcpy\b|(?<!\w)::strcpy\b')
            self.assertFalse(any("#include" in line and "CompanionHelp.inc" not in line
                                 for line in following.splitlines()))

    def test_local_names_do_not_change_companion_or_unqualified_roles(self):
        for options in (dict(name="RAK_4631_companion_radio_full"),
                        dict(role="custom"), dict(platform="espressif32", mcu="esp32")):
            self.assertEqual(self.policy(flags="-DMESH_OTA_LOCAL_TARGET_NAME_ONLY=1", **options), {})

    def test_cc310_archive_is_exact_and_requires_supported_mcu_and_enabled_crypto(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "libraries/Adafruit_nRFCrypto" / TRIM.CC310_ARCHIVE
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"future-sdk")
            enabled = {"USE_CC310_HW_CRYPTO": "1"}
            self.assertFalse(TRIM.supported_cc310(temp_dir, "nrf52840", enabled))
            with patch.object(TRIM, "CC310_ARCHIVE_SHA256", TRIM.hashlib.sha256(b"future-sdk").hexdigest()):
                self.assertTrue(TRIM.supported_cc310(temp_dir, "nrf52840", enabled))
                self.assertFalse(TRIM.supported_cc310(temp_dir, "nrf52832", enabled))
                self.assertFalse(TRIM.supported_cc310(temp_dir, "nrf52840", {"USE_CC310_HW_CRYPTO": "0"}))
                self.assertFalse(TRIM.supported_cc310(temp_dir, "nrf52840", {}))

    def test_profile_wiring_is_nrf52_only_and_companion_common_flags_unchanged(self):
        config = (ROOT / "platformio.ini").read_text()
        nrf = config.split("[nrf52_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("pre:scripts/nrf52_flash_trim.py", nrf)
        self.assertEqual(config.count("pre:scripts/nrf52_flash_trim.py"), 1)
        self.assertNotIn("ED25519_COMPACT", nrf)
        self.assertNotIn("CFG_TUD_HID", nrf)
        self.assertNotIn("SSD1306_NO_SPLASH", nrf)
        self.assertIn("-D MESH_NRF52_LOOP_STACK_WORDS=2048", nrf)

    def test_tft_force_include_is_per_library_and_preserves_existing_include_pair(self):
        self.assertTrue(TRIM.source_selected("+<helpers/ui/ST7735Display.cpp>",
                                             "helpers/ui/ST7735Display.cpp"))
        self.assertFalse(TRIM.source_selected("+<helpers/ui/*.cpp> -<helpers/ui/ST7735Display.cpp>",
                                              "helpers/ui/ST7735Display.cpp"))
        class LibraryEnvironment(dict):
            def Append(self, **kwargs):
                for key, value in kwargs.items():
                    self.setdefault(key, []).extend(value)

            def subst(self, text):
                return text.replace("$PROJECT_DIR", str(ROOT))

        library = LibraryEnvironment(CCFLAGS=["-include", "SoftDeviceSvcCompat.h"])
        source = object()
        self.assertIs(TRIM.trim_tft_library(library, source), source)
        self.assertEqual(library["CCFLAGS"].count("-include"), 2)
        before = list(library["CCFLAGS"])
        TRIM.trim_tft_library(library, source)
        self.assertEqual(library["CCFLAGS"], before)

    def test_install_does_not_mutate_excluded_roles_and_scopes_wrapper_build(self):
        class Platform:
            name = "nordicnrf52"

            def get_package_dir(self, _name):
                return "sdk"

        class BuildEnvironment(dict):
            def PioPlatform(self):
                return Platform()

            def BoardConfig(self):
                return {"build.mcu": "nrf52840"}

            def AppendUnique(self, **kwargs):
                for name, values in kwargs.items():
                    for value in values:
                        if value not in self.setdefault(name, []):
                            self[name].append(value)

            def BuildSources(self, *args):
                self.setdefault("BUILT_SOURCES", []).append(args)

            def subst(self, value):
                return value.replace("$PROJECT_DIR", str(ROOT))

            def AddBuildMiddleware(self, callback, pattern):
                self.setdefault("MIDDLEWARE", []).append((callback, pattern))

        base = dict(BUILD_FLAGS=["-DUSE_CC310_HW_CRYPTO=1"],
                    SRC_FILTER=["+<../examples/simple_repeater>"], CPPDEFINES=[])
        excluded = BuildEnvironment(**base, PIOENV="RAK_4631_companion_radio_full")
        before = dict(excluded)
        TRIM.install(excluded)
        self.assertEqual(excluded, before)
        active = BuildEnvironment(BUILD_FLAGS=list(base["BUILD_FLAGS"]),
                                  SRC_FILTER=list(base["SRC_FILTER"]), CPPDEFINES=[],
                                  PIOENV="RAK_4631_repeater")
        with patch.object(TRIM, "supported_cc310", return_value=True):
            TRIM.install(active)
        self.assertTrue(active["MESH_NRF52_FLASH_TRIM_ACTIVE"])
        self.assertIn("-Wl,--wrap=CRYS_ECPKI_GetEcDomain", active["LINKFLAGS"])
        self.assertEqual(active["BUILT_SOURCES"][0][-1], "+<Cc310DomainTrim.c>")
        self.assertEqual(active["MIDDLEWARE"][0][1], "*Adafruit_VL53L0X*/*.cpp")

        companion = BuildEnvironment(BUILD_FLAGS=list(base["BUILD_FLAGS"]),
            SRC_FILTER=["+<../examples/companion_radio/*.cpp>"], CPPDEFINES=[],
            PIOENV="RAK_4631_companion_radio_full")
        TRIM.install(companion)
        self.assertIn(("MESH_CLI_SHARE_REPLY_COPIES", 1), companion["CPPDEFINES"])
        self.assertEqual(companion["BUILD_FLAGS"], base["BUILD_FLAGS"])

        for name, flag in (("RAK_4631_companion_radio_usb", ""),
                           ("Xiao_nrf52_companion_radio_full", ""),
                           ("RAK_4631_companion_radio_full", "-DMESH_CLI_SHARE_REPLIES=0"),
                           ("RAK_4631_companion_radio_full", "-DMESH_CLI_SHARE_REPLY_COPIES=0"),
                           ("RAK_4631_companion_radio_full", "-UMESH_CLI_SHARE_REPLY_COPIES")):
            with self.subTest(name=name, flag=flag):
                unchanged = BuildEnvironment(BUILD_FLAGS=[flag] if flag else [],
                    SRC_FILTER=["+<../examples/companion_radio/*.cpp>"], CPPDEFINES=[],
                    PIOENV=name)
                TRIM.install(unchanged)
                callbacks = [callback for callback, _ in unchanged.get("MIDDLEWARE", [])]
                self.assertNotIn(TRIM.compile_shared_cli_replies, callbacks)
                self.assertNotIn(("MESH_CLI_SHARE_REPLY_COPIES", 1), unchanged["CPPDEFINES"])

        explicit = BuildEnvironment(BUILD_FLAGS=["-DMESH_CLI_SHARE_REPLY_COPIES=1"],
            SRC_FILTER=["+<../examples/companion_radio/*.cpp>"], CPPDEFINES=[],
            PIOENV="RAK_4631_companion_radio_full")
        TRIM.install(explicit)
        self.assertNotIn(("MESH_CLI_SHARE_REPLY_COPIES", 1), explicit["CPPDEFINES"])
        self.assertEqual(explicit["BUILD_FLAGS"], ["-DMESH_CLI_SHARE_REPLY_COPIES=1"])

    @unittest.skipUnless(shutil.which("cc"), "a native C compiler is required")
    def test_wrapped_domain_preserves_p256_and_rejects_unused_curves(self):
        fixture = ROOT / "test/fixtures/nrf52_flash_trim"
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "cc310-trim-test"
            subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-DMESH_NRF52_FLASH_TRIM=1", "-I" + str(fixture),
                            str(ROOT / "src/helpers/nrf52/Cc310DomainTrim.c"),
                            str(fixture / "test_domain_trim.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_companion_keeps_small_crypto_stack_fonts_reader_and_usb_transports(self):
        for platform, mcu, expected in (("nordicnrf52", "nrf52840", True),
                                        ("nordicnrf52", "nrf52832", True),
                                        ("espressif32", "esp32s3", False)):
            with self.subTest(platform=platform, mcu=mcu):
                defines = TRIM.companion_trim_policy(platform, mcu,
                    "+<*> +<../examples/companion_radio/*.cpp>")
                self.assertEqual(bool(defines), expected)
                if not expected:
                    continue
                self.assertEqual(defines["MESH_NRF52_COMPANION_TRIM"], 1)
                self.assertEqual(defines["ED25519_SMALL_BASE"], 1)
                self.assertEqual(defines["ED25519_COMPACT_SHA512"], 1)
                self.assertEqual(defines["ED25519_COMPACT_SCALAR"], 1)
                self.assertEqual(defines["CFG_TUD_MSC"], 0)
                self.assertEqual(defines["CFG_TUD_HID"], 0)
                for name in ("MESH_NRF52_FLASH_TRIM", "ED25519_COMPACT_BASE",
                             "CFG_TUD_CDC", "CFG_TUD_DFU",
                             "CFG_TUD_DFU_RUNTIME", "COMPANION_FEATURE_READER"):
                    self.assertNotIn(name, defines)
                self.assertFalse(any(name.startswith("CFG_TUH") for name in defines))

    def test_companion_role_requires_selected_source_and_honors_explicit_options(self):
        for sources in ("+<*>", "+<../examples/simple_secure_chat>",
                        "+<../examples/companion_radio> -<../examples/companion_radio>",
                        "+<../examples/companion_radio> +<../examples/simple_repeater>"):
            self.assertEqual(TRIM.companion_trim_policy("nordicnrf52", "nrf52840", sources), {})
        for flags in ("-DMESH_NRF52_COMPANION_TRIM=0", "-UMESH_NRF52_COMPANION_TRIM",
                      "-DMESH_NRF52_FLASH_TRIM=0"):
            self.assertEqual(TRIM.companion_trim_policy("nordicnrf52", "nrf52840",
                "+<../examples/companion_radio>", flags), {})
        result = TRIM.companion_trim_policy("nordicnrf52", "nrf52840",
            "+<../examples/companion_radio>", "-DCFG_TUD_MSC=1 -USSD1306_NO_SPLASH")
        self.assertNotIn("CFG_TUD_MSC", result)
        self.assertNotIn("SSD1306_NO_SPLASH", result)
        result = TRIM.companion_trim_policy("nordicnrf52", "nrf52840",
            "+<../examples/companion_radio>",
            "-DED25519_SMALL_BASE=0 -UED25519_COMPACT_SHA512 -UED25519_COMPACT_SCALAR")
        for name in ("ED25519_SMALL_BASE", "ED25519_COMPACT_SHA512", "ED25519_COMPACT_SCALAR"):
            self.assertNotIn(name, result)
        for value in (0, 1):
            result = TRIM.companion_trim_policy("nordicnrf52", "nrf52840",
                "+<../examples/companion_radio>", "-DED25519_COMPACT_BASE=%d" % value)
            self.assertNotIn("ED25519_SMALL_BASE", result)

    @unittest.skipUnless(shutil.which("cc"), "a native C compiler is required")
    def test_companion_uses_same_p256_domain_wrapper(self):
        fixture = ROOT / "test/fixtures/nrf52_flash_trim"
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "cc310-companion-test"
            subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-DMESH_NRF52_COMPANION_TRIM=1", "-I" + str(fixture),
                            str(ROOT / "src/helpers/nrf52/Cc310DomainTrim.c"),
                            str(fixture / "test_domain_trim.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
