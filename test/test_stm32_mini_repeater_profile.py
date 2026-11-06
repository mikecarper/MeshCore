#!/usr/bin/env python3
"""Guard the constrained Mini Repeater recipe without bypassing size/storage gates."""

import configparser
import os
import re
import runpy
import shlex
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TARGET = "wio-e5-mini_repeater"
SECTION = "env:" + TARGET
LINK_SIZE_FLAGS = (
    "-fno-caller-saves",
    "-fno-tree-dominator-opts",
    "-fno-reorder-blocks",
    "-fno-reorder-functions",
    "-fno-tree-switch-conversion",
    "-fno-tree-loop-im",
    "-fno-tree-pre",
    "-fno-tree-slsr",
    "-fno-code-hoisting",
)
LINK_ONLY_SIZE_FLAGS = ("-flto-partition=one",)
OPTIONAL_CUTS = (
    "MESH_ENABLE_CARRIER_TOOLS",
    "MESH_ENABLE_MANAGEMENT_REPORTING",
    "MESH_ENABLE_PACKET_FILE_LOGGING",
    "MESH_ENABLE_BATTERY_FORWARDING",
)


def read_config(path):
    config = configparser.ConfigParser(
        interpolation=None, inline_comment_prefixes=(";",))
    config.read(path)
    return config


class FakeEnvironment(dict):
    def AddBuildMiddleware(self, callback, pattern):
        self.middleware = getattr(self, "middleware", []) + [(callback, pattern)]

    def subst(self, value):
        return value.replace("$PROJECT_DIR", str(ROOT))

    def Object(self, node, **flags):
        return node, flags

    def Append(self, **values):
        for name, items in values.items():
            self.setdefault(name, []).extend(items)

    def AppendUnique(self, **values):
        for name, items in values.items():
            current = self.setdefault(name, [])
            current.extend(item for item in items if item not in current)


class MiniRepeaterProfileTest(unittest.TestCase):
    def setUp(self):
        self.config = read_config(ROOT / "variants/wio-e5-mini/platformio.ini")
        self.repeater = self.config[SECTION]
        self.flags = self.repeater["build_flags"]

    def test_existing_flash_filesystem_and_neighbour_contracts_are_retained(self):
        self.assertEqual(self.repeater.getint("board_upload.maximum_size"), 245760)
        self.assertRegex(self.flags, r"-D\s+LFS_FLASH_TOTAL_SIZE=16384\b")
        self.assertRegex(self.flags, r"-D\s+MAX_NEIGHBOURS=50\b")
        self.assertEqual(245760 + 16384, 256 * 1024)
        self.assertEqual(self.repeater["platform_packages"].split(), [
            "platformio/toolchain-gccarmnoneeabi@^1.140201.0"])
        self.assertEqual(self.repeater["build_unflags"].split(), ["-Os"])
        self.assertIn("+<../examples/simple_repeater/*.cpp>",
                      self.repeater["build_src_filter"])
        self.assertNotIn("MAX_CLIENTS", self.flags)
        self.assertNotIn("--no-check", self.repeater.get("extra_scripts", ""))

    def test_four_opt_outs_apply_only_to_exact_mini_repeater(self):
        for macro in OPTIONAL_CUTS:
            self.assertRegex(self.flags, r"-D\s+" + macro + r"=0\b")
        for path in [ROOT / "platformio.ini", *sorted((ROOT / "variants").rglob("platformio.ini"))]:
            config = read_config(path)
            for name in config.sections():
                flags = config[name].get("build_flags", "")
                for macro in OPTIONAL_CUTS:
                    with self.subTest(path=path.relative_to(ROOT), section=name, macro=macro):
                        if path == ROOT / "variants/wio-e5-mini/platformio.ini" and name == SECTION:
                            continue
                        self.assertNotRegex(flags, r"-D\s*" + macro + r"=0\b")
        # Retain pre-existing trims plus the approved browser-defaults omission.
        disabled = set(re.findall(r"-D\s*(MESH_ENABLE_\w+)=0\b", self.flags))
        self.assertRegex(self.flags, r"-D\s+MESH_ENABLE_BROWSER_RADIO_DEFAULTS=0\b")
        self.assertEqual(disabled, {*OPTIONAL_CUTS, "MESH_ENABLE_BROWSER_RADIO_DEFAULTS",
                                    "MESH_ENABLE_CLOCK_SYNC",
                                    "MESH_ENABLE_FLOOD_GROUP_MODERATION"})

    def test_host_cli_live_logging_and_existing_core_features_remain(self):
        self.assertNotIn("MESH_ENABLE_HOST_CLI", self.flags)
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        self.assertRegex(header, r"#ifndef MESH_ENABLE_HOST_CLI\s+"
                         r"#define MESH_ENABLE_HOST_CLI 1\b")
        self.assertNotRegex(self.flags, r"-D\s*(?:MESH_PACKET_LOGGING|MESH_USB_LOGGING_MERGED)=0\b")
        self.assertRegex(self.flags, r"-D\s+MESH_PACKET_LOGGING_COMPACT=1\b")
        self.assertRegex(self.flags, r"-D\s+MESH_ENABLE_RECENT_REPEATERS=1\b")
        # Numeric shortcut GET is deliberately not part of this size profile.
        self.assertNotIn("NUMERIC", self.flags)

    def test_compact_unsupported_agc_keeps_exact_mini_command_replies(self):
        """Run the real compact parser for supported and malformed command keys."""
        from test_replay_reset_integration import extract_braced
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        self.assertRegex(self.flags, r"-D\s+WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1\b")
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        equals = extract_braced(source, "static bool configKeyEquals(")
        matches = extract_braced(source, "static bool miniUnsupportedWarmAgcGet(")
        spaces = extract_braced(source, "static const char* skipSpacesConst(")
        set_branch = extract_braced(source, 'if (memcmp(config, "agc.old ", 8) == 0)')
        # Keep the old-timer capability branch separate from the following
        # compile-time shortcut for unsupported warm diagnostics.
        start = source.index('  } else if(configKeyEquals(config,"agc.old"))')
        end = source.index('#if defined(WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC)', start)
        get_branch = source[start:end].replace("  } else if", "  if", 1) + "  }\n"
        harness = r'''
#include <cassert>
#include <cstring>
#include <helpers/CLICommandUtils.h>
@EQUALS@
@MATCHES@
@SPACES@
struct Radio {
  bool supported = false;
  uint32_t interval = 0;
  mutable unsigned reads = 0;
  unsigned writes = 0;
  bool supportsOldAGC() const { return supported; }
  uint32_t getOldAGCInterval() const { ++reads; return interval; }
  bool setOldAGCInterval(uint32_t value) { ++writes; interval = value; return true; }
};
struct Callbacks { Radio* radio; Radio* getProfileRadio() const { return radio; } };
struct CLI {
  Callbacks* _callbacks;
  void set_old(const char* config, char* reply) {
@SET_BRANCH@
  }
  void get_old(const char* config, char* reply) {
@GET_BRANCH@
  }
};
int main() {
  assert(!miniUnsupportedWarmAgcGet(nullptr));
  for (const char* key : {"agc.status", "agc.timing", "agc.recovery",
                          "agc.pulse", "agc.sleep", "agc.checks", "agc.scan", "agc.status   "})
    assert(miniUnsupportedWarmAgcGet(key));
  for (const char* key : {"agc", "agc.reset.interval", "agc.old", "agc.old   ", "agc.old.status", "agc.oldx",
                          "agc.status x", "agc.statusx", "agc.old\t", "other"})
    assert(!miniUnsupportedWarmAgcGet(key));
  Radio radio; Callbacks callbacks{&radio}; CLI cli{&callbacks};
  for (Radio* available : {&radio, static_cast<Radio*>(nullptr)}) {
    callbacks.radio = available;
    for (const char* command : {"agc.old ", "agc.old 0", "agc.old 999999999999"}) {
      char reply[160] = {}; cli.set_old(command, reply); assert(!strcmp(reply, "Error: unsupported"));
    }
    for (const char* key : {"agc.old", "agc.old   "}) {
      char reply[160] = {}; cli.get_old(key, reply); assert(!strcmp(reply, "Error: unsupported"));
    }
  }
  assert(!radio.reads && !radio.writes);
  callbacks.radio = &radio; radio.supported = true;
  char reply[160] = {}; cli.get_old("agc.old", reply); assert(!strcmp(reply, "> 0"));
  for (const char* command : {"agc.old 0", "agc.old 16", "agc.old 86400"}) {
    cli.set_old(command, reply); assert(!strncmp(reply, "OK - ", 5));
  }
  assert(radio.interval == 86400 && radio.writes == 3);
  for (const char* command : {"agc.old ", "agc.old -1", "agc.old 86401", "agc.old 999999999999", "agc.old 1x"}) {
    cli.set_old(command, reply); assert(!strncmp(reply, "Error:", 6));
    assert(radio.interval == 86400 && radio.writes == 3);
  }
  reply[0] = 0; cli.set_old("agc.oldx 1", reply); assert(!reply[0]);
  cli.get_old("agc.old extra", reply); assert(!reply[0]);
}
'''.replace('@EQUALS@', equals).replace('@MATCHES@', matches).replace('@SPACES@', spaces).replace(
            '@SET_BRANCH@', set_branch).replace('@GET_BRANCH@', get_branch)
        with tempfile.TemporaryDirectory(prefix='mini-agc-unsupported-') as directory:
            path = Path(directory)
            (path / 'main.cpp').write_text('#include <initializer_list>\n' + harness, encoding='utf-8')
            result = subprocess.run([compiler, '-std=c++17', '-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1',
                                     '-I', str(ROOT / 'src'), str(path / 'main.cpp'), '-o', str(path / 'test')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mini_shortcuts_only_methods_with_inherited_noop_defaults(self):
        """The compact route is valid only while Mini has no maintenance lease."""
        header = (ROOT / 'src/Dispatcher.h').read_text(encoding='utf-8')
        radio = (ROOT / 'src/helpers/radiolib/CustomSTM32WLxWrapper.h').read_text(encoding='utf-8')
        wrapper = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.h').read_text(encoding='utf-8')
        self.assertRegex(header, r'virtual bool isAGCMaintenancePending\(\) const \{ return false; \}')
        self.assertRegex(header, r'virtual uint32_t agcMaintenanceRetryMs\(\) const \{ return 50; \}')
        optional_methods = ('supportsWarmRxRefresh', 'isAGCMaintenanceActive',
            'isAGCMaintenancePending', 'configureAGCMaintenance', 'cancelAGCMaintenance',
            'setAGCPulseSleep', 'getAGCPulseSleep', 'setOldAGCInterval',
            'getOldAGCInterval', 'formatWarmRxRefresh', 'periodicAGCMaintenance',
            'agcMaintenanceRetryMs', 'periodicOldAGCMaintenance')
        for method in optional_methods:
            self.assertNotRegex(radio, r'\b' + method + r'\s*\(', method)
        self.assertNotRegex(self.flags, r'-D\s+MESH_WARM_RX_REFRESH(?:=1)?(?:\s|$)')
        warm_block = wrapper[wrapper.index('  void resetAGC() override;'):wrapper.index('  bool tryResetAGC() override;')]
        self.assertIn('#if MESH_WARM_RX_REFRESH', warm_block)
        self.assertIn('isAGCMaintenancePending() const override', warm_block)
        self.assertIn('agcMaintenanceRetryMs() const override', warm_block)
        for method in optional_methods:
            for match in re.finditer(r'\b' + method + r'\s*\([^;{}]*\)\s*(?:const\s*)?override', wrapper):
                self.assertIn(match.group(), warm_block, method + ' must remain behind the warm feature')

    def test_compact_fem_replies_match_unavailable_board(self):
        """Exercise original and compact production branches with no FEM or Morse."""
        from test_replay_reset_integration import extract_braced
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('host C++ compiler required')
        board = (ROOT / 'variants/wio-e5-mini/target.h').read_text(encoding='utf-8')
        target = (ROOT / 'variants/wio-e5-mini/target.cpp').read_text(encoding='utf-8')
        stm = (ROOT / 'src/helpers/stm32/STM32Board.h').read_text(encoding='utf-8')
        base = (ROOT / 'src/MeshCore.h').read_text(encoding='utf-8')
        dispatcher = (ROOT / 'src/Dispatcher.h').read_text(encoding='utf-8')
        morse = (ROOT / 'src/helpers/MorseRx.h').read_text(encoding='utf-8')
        radio = (ROOT / 'src/helpers/radiolib/CustomSTM32WLxWrapper.h').read_text(encoding='utf-8')
        wrapper = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.h').read_text(encoding='utf-8')
        self.assertIn('WIOE5Board board;', target)
        self.assertIn('class WIOE5Board : public STM32Board', board)
        self.assertIn('class STM32Board : public mesh::MainBoard', stm)
        for name in ('canControlLoRaFemLna', 'canControlLoRaFemPaGain'):
            self.assertNotIn(name, board)
            self.assertNotIn(name, stm)
            self.assertRegex(base, r'virtual bool ' + name + r'\(\) const \{ return false; \}')
        for name in ('isMorseTxActive', 'isMorseRxActive'):
            self.assertNotIn(name, radio)
            self.assertRegex(dispatcher, r'virtual bool ' + name + r'\(\) const \{ return false; \}')
        self.assertRegex(wrapper, r'(?s)#if MESH_ENABLE_CARRIER_TOOLS\s+bool supportsMorseTx.*?isMorseTxActive\(')
        self.assertRegex(wrapper, r'(?s)#if MESH_ENABLE_MORSE_RX\s+bool supportsMorseRx.*?isMorseRxActive\(')
        self.assertRegex(self.flags, r'-D\s+MESH_ENABLE_CARRIER_TOOLS=0\b')
        self.assertRegex(morse, r'#if defined\(STM32_PLATFORM\)\s+#define MESH_ENABLE_MORSE_RX 0')
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        equals = extract_braced(source, 'static bool configKeyEquals(')
        set_pos = source.index('  } else if (memcmp(config, "radio.fem.rxgain ", 17)')
        set_start = source.rfind('#if defined(WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC)', 0, set_pos)
        set_end = source.index('  } else if (strncmp(config, "radio.rxps.rfrx_disabled ", 25)', set_pos)
        get_pos = source.index('  } else if (configKeyEquals(config, "radio.fem.rxgain")')
        get_start = source.rfind('#if defined(WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC)', 0, get_pos)
        get_end = source.index('  } else if (memcmp(config, "tempradioat", 11)', get_pos)
        set_branch, get_branch = source[set_start:set_end] + '  }\n', source[get_start:get_end] + '  }\n'
        harness = r'''
#include <cstdio>
#include <cstring>
#include <helpers/CLICommandUtils.h>
@EQUALS@
struct Radio { bool isMorseTxActive() const { return false; } bool isMorseRxActive() const { return false; } };
struct Board { bool canControlLoRaFemLna() const { return false; } bool canControlLoRaFemPaGain() const { return false; }
  bool isLoRaFemLnaEnabled() const { return false; } bool isLoRaFemPaGainEnabled() const { return false; }
  bool setLoRaFemLnaEnabled(bool) { return false; } bool setLoRaFemPaGainEnabled(bool) { return false; } };
struct Callbacks { Radio* getProfileRadio() { return nullptr; } void recalibrateNoiseFloor() {} };
struct Prefs { int radio_fem_rxgain=0, radio_fem_txgain=0; };
struct Test { Board* _board; Callbacks* _callbacks; Prefs* _prefs; void savePrefs() {}
  void set(const char* config, char* reply) { if (false) {
@SET@
  }
  void get(const char* config, char* reply) { if (false) {
@GET@
  }
};
int main() {
  Board board; Callbacks callbacks; Prefs prefs; Test cli{&board,&callbacks,&prefs};
  for (const char* value : {"on", "off", "bad", "", "on extra"}) for (const char* mode : {"rxgain", "txgain"}) {
    char config[160] = {}, reply[160] = "unchanged";
    snprintf(config,sizeof(config),"radio.fem.%s %s",mode,value); cli.set(config,reply);
    assert(!strcmp(reply,"Error: unsupported")); puts(reply);
  }
  for (const char* key : {"radio.fem.rxgain", "radio.fem.txgain", "radio.fem.rxgain   ",
                          "radio.rxgain", "radio.fem.rxgainx", "radio.fem.txgain extra"}) {
    char config[160] = {}, reply[160] = "unchanged";
    snprintf(config,sizeof(config),"%s",key); cli.get(config,reply); puts(reply);
  }
}
'''.replace('@EQUALS@', equals).replace('@SET@', set_branch).replace('@GET@', get_branch)
        with tempfile.TemporaryDirectory(prefix='mini-fem-unsupported-') as directory:
            path = Path(directory)
            (path / 'main.cpp').write_text('#include <cassert>\n#include <initializer_list>\n' + harness, encoding='utf-8')
            outputs = []
            for compact in (0, 1):
                binary = path / ('test' + str(compact))
                result = subprocess.run([compiler, '-std=c++17', '-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=' + str(compact),
                                         '-I', str(ROOT / 'src'), str(path / 'main.cpp'), '-o', str(binary)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                outputs.append(result.stdout)
            self.assertEqual(outputs[0], outputs[1])

    def test_fixed_ota_relay_pace_is_scoped_and_preserves_dispatch(self):
        # Exercise actual queueing, TX completion, busy-channel fairness,
        # route retirement and receive/watchdog behavior in both policies.
        from test_radio_direction_dispatcher import SOURCE
        self.assertRegex(self.flags, r"-D\s+MESH_OTA_PACKET_PACING=0\b")
        for section in self.config.sections():
            if section != SECTION:
                self.assertNotIn("MESH_OTA_PACKET_PACING", self.config[section].get("build_flags", ""))
        source = SOURCE.replace("unsigned inbound=0;", "unsigned inbound=0, completed=0;")
        source = source.replace("  using mesh::Dispatcher::getTransmitProfileMask;",
                                "  void onSendComplete(mesh::Packet*) override { ++completed; }\n"
                                "  using mesh::Dispatcher::getTransmitProfileMask;")
        source = source.replace("PAYLOAD_TYPE_TXT_MSG<<PH_TYPE_SHIFT", "PAYLOAD_TYPE_OTA<<PH_TYPE_SHIFT")
        source = source.replace("p->header=ROUTE_TYPE_FLOOD;", "p->header=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_OTA<<PH_TYPE_SHIFT);")
        source = source.rsplit("}", 1)[0] + "assert(d.completed == r.tx);\n}\n"
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="mini-fixed-pace-") as directory:
            work = Path(directory)
            (work / "main.cpp").write_text(source)
            (work / "Mesh.h").write_text("#pragma once\n#include <Dispatcher.h>\n")
            flags = ["-DMESH_ENABLE_RADIO_DIRECTION=1", "-DMESH_ENABLE_BRIDGE_INGRESS=1",
                     "-DRADIO_LIVENESS_SOFT_ONLY=1"]
            sanitize = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                         "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            for policy, mini in ((None, False), (0, False), (0, True)):
                with self.subTest(policy=policy, mini=mini):
                    executable = work / ("mini.exe" if mini else "default.exe" if policy is None else "fixed.exe")
                    built = subprocess.run([compiler, "-std=c++17", "-O1", *sanitize, *flags,
                        *([] if policy is None else ["-DMESH_OTA_PACKET_PACING=0"]),
                        *(["-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1"] if mini else []),
                        "-I", str(work), "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        str(work / "main.cpp"), str(ROOT / "src/Dispatcher.cpp"),
                        str(ROOT / "src/Packet.cpp"), str(ROOT / "src/helpers/StaticPoolPacketManager.cpp"),
                        "-o", str(executable)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20,
                                         env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            invalid = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        "-DENABLE_OTA=1", "-DMESH_OTA_PACKET_PACING=0"],
                        input="#include <Dispatcher.h>\n", capture_output=True, text=True, timeout=10)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("OTA-enabled firmware requires variable packet pacing", invalid.stderr)
            pulse = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        "-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1", "-DMESH_WARM_RX_REFRESH=1"],
                        input="#include <Dispatcher.h>\nstatic_assert(MESH_COMPACT_STM32_RADIO == 1, \"Compact storage retained\");\nstatic_assert(MESH_ENABLE_OLD_AGC == 0, \"Legacy timer remains optional\");\n", capture_output=True, text=True, timeout=10)
            self.assertEqual(pulse.returncode, 0, pulse.stderr)

    def test_compact_crypto_and_unused_timer_wrappers_are_explicit(self):
        for macro in ("ED25519_COMPACT_SHA512=1", "ED25519_COMPACT_SCALAR=1",
                      "ED25519_COMPACT_FIELD=1", "RADIOLIB_TONE_UNSUPPORTED",
                      "HAL_TIM_MODULE_ONLY"):
            self.assertRegex(self.flags, r"-D\s+" + macro + r"\b")
        common = read_config(ROOT / "platformio.ini")
        self.assertRegex(common["stm32_base"]["build_flags"],
                         r"-D\s+ED25519_COMPACT_BASE=1\b")
        self.assertIn("post:scripts/check_firmware_ram.py",
                      common["stm32_base"]["extra_scripts"])

    def test_measured_compiler_flags_reach_both_compile_and_lto_link(self):
        tokens = shlex.split(self.flags)
        for flag in ("-Oz", "-flto", *LINK_SIZE_FLAGS):
            self.assertIn(flag, tokens)
        for flag in LINK_ONLY_SIZE_FLAGS:
            self.assertNotIn(flag, tokens)
        scripts = self.repeater["extra_scripts"].split()
        self.assertIn("${stm32_base.extra_scripts}", scripts)
        self.assertIn("post:scripts/stm32_mini_size.py", scripts)
        helper = ROOT / "scripts/stm32_mini_size.py"
        for target in (TARGET, "wio-e5-mini_sensor", "wio-e5-mini_companion_radio_usb",
                       "RAK_3x72_companion_radio_usb", "wio-e5_repeater"):
            with self.subTest(target=target):
                environment = FakeEnvironment(PIOENV=target, LINKFLAGS=["keep-existing-flag"])
                runpy.run_path(str(helper), init_globals={
                    "env": environment,
                    "Import": lambda name: self.assertEqual(name, "env"),
                })
                expected = ["keep-existing-flag",
                            *((LINK_SIZE_FLAGS + LINK_ONLY_SIZE_FLAGS) if target == TARGET else ())]
                self.assertEqual(environment["LINKFLAGS"], expected)
                self.assertEqual(set(environment), {"PIOENV", "LINKFLAGS"})
                self.assertFalse(getattr(environment, "middleware", []))

    def test_shared_reply_copy_policy_changes_only_the_exact_mini_cli_object(self):
        class Node:
            def __init__(self, path): self.path = path
            def srcnode(self): return self
            def get_abspath(self): return str(self.path)

        environment = FakeEnvironment(PIOENV=TARGET, CCFLAGS=["-Oz", "-flto"])
        runpy.run_path(str(ROOT / "scripts/stm32_mini_cli_replies.py"), init_globals={
            "env": environment, "Import": lambda name: self.assertEqual(name, "env")})
        callback, pattern = environment.middleware[0]
        self.assertEqual(pattern, "*CommonCLI.cpp")
        cli = Node(ROOT / "src/helpers/CommonCLI.cpp")
        same, flags = callback(environment, cli)
        self.assertIs(same, cli)
        self.assertEqual(flags["CCFLAGS"], ["-Oz", "-flto", "-fno-builtin-strcpy"])
        self.assertEqual(environment["CCFLAGS"], ["-Oz", "-flto"])
        for path in ("test/fixtures/CommonCLI.cpp", "src/helpers/RadioProfileCLI.cpp",
                     "src/helpers/UsbLogging.cpp"):
            other = Node(ROOT / path)
            self.assertIs(callback(environment, other), other)

    def test_scons_script_context_without_file_still_installs_exact_cli_policy(self):
        script = ROOT / "scripts/stm32_mini_cli_replies.py"
        environment = FakeEnvironment(PIOENV=TARGET)
        namespace = {"env": environment,
                     "Import": lambda name: self.assertEqual(name, "env")}
        exec(compile(script.read_text(), str(script), "exec"), namespace)
        self.assertNotIn("__file__", namespace)
        self.assertNotIn("LINKFLAGS", environment)
        self.assertEqual(len(environment.middleware), 1)
        self.assertEqual(environment.middleware[0][1], "*CommonCLI.cpp")

    def test_middleware_is_registered_before_object_flags_are_captured(self):
        environment = FakeEnvironment(PIOENV=TARGET, CCFLAGS=["-Oz", "-flto"])
        scripts = self.repeater["extra_scripts"].split()
        pre = "pre:scripts/stm32_mini_cli_replies.py"
        post = "post:scripts/stm32_mini_size.py"
        self.assertIn(pre, scripts)
        self.assertIn(post, scripts)
        self.assertNotIn(pre.replace("pre:", "post:"), scripts)

        def run_script(entry):
            runpy.run_path(str(ROOT / entry.split(":", 1)[1]), init_globals={
                "env": environment, "Import": lambda name: self.assertEqual(name, "env")})

        # PlatformIO constructs source objects before executing post scripts.
        run_script(pre)
        callback, _ = environment.middleware[0]
        class Node:
            def srcnode(self): return self
            def get_abspath(self): return str(ROOT / "src/helpers/CommonCLI.cpp")
        _, captured = callback(environment, Node())
        run_script(post)
        self.assertEqual(captured["CCFLAGS"], ["-Oz", "-flto", "-fno-builtin-strcpy"])
        self.assertEqual(environment["CCFLAGS"], ["-Oz", "-flto"])
        self.assertEqual(environment["LINKFLAGS"], list(LINK_SIZE_FLAGS + LINK_ONLY_SIZE_FLAGS))
        for target in ("wio-e5-mini_sensor", "wio-e5-mini_companion_radio_usb", "wio-e5_repeater"):
            environment = FakeEnvironment(PIOENV=target)
            run_script(pre)
            self.assertFalse(getattr(environment, "middleware", []))

    def test_capability_contract_declares_only_exact_mini_repeater_omissions(self):
        command = r'''
set -euo pipefail
source build.sh
pio() { echo 'FORBIDDEN PlatformIO call in host capability test' >&2; return 99; }
target="$1"
BUILD_PROFILE_FOR_TARGET="$2"
PIO_CONFIG_JSON='[]'
PIO_ENV_PLATFORM_BY_NAME["$target"]=STM32_PLATFORM
PIO_ENV_BOARD_BY_NAME["$target"]=lora_e5_mini
BUILD_CAPABILITIES=()
BUILD_EXPECTATIONS=()
BUILD_APPLICATION_EXPECTATIONS=()
BUILD_REDUCTIONS=(caller.record)
PLATFORMIO_BUILD_FLAGS=-DKEEP_CALLER_FLAGS=1
PLATFORMIO_BUILD_UNFLAGS=-DKEEP_CALLER_UNFLAGS=1
declare_build_capability_contract "$target" STM32_PLATFORM
[ "$PLATFORMIO_BUILD_FLAGS" = -DKEEP_CALLER_FLAGS=1 ]
[ "$PLATFORMIO_BUILD_UNFLAGS" = -DKEEP_CALLER_UNFLAGS=1 ]
printf '%s\n' "${BUILD_REDUCTIONS[@]}"
'''
        omissions = [
            "radio.carrier_tools omitted to preserve the Mini Repeater app boundary",
            "management.reporting and data.tx omitted to preserve the Mini Repeater app boundary",
            "logging.file omitted; live USB/MQTT packet logging retained",
            "battery.forwarding omitted; battery alerts and readings retained",
        ]
        for target in (TARGET, "wio-e5-mini_companion_radio_usb", "wio-e5-mini_sensor",
                       "wio-e5_repeater", "RAK_3x72_repeater", TARGET + "_custom"):
            for profile in ("standard", "full"):
                with self.subTest(target=target, profile=profile):
                    result = subprocess.run(
                        ["bash", "-c", command, "mini-capability-contract", target, profile],
                        cwd=ROOT, capture_output=True, text=True, check=True, timeout=10)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(result.stdout.splitlines(),
                                     ["caller.record", *(omissions if target == TARGET else ())])

    def test_battery_forwarding_defaults_on_and_rejects_invalid_feature_values(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required for the battery feature gate")
        source = "#include <helpers/BatteryForwardingConfig.h>\nMESH_ENABLE_BATTERY_FORWARDING\n"
        for flags, expected in (([], "1"), (["-DMESH_ENABLE_BATTERY_FORWARDING=0"], "0"),
                                (["-DMESH_ENABLE_BATTERY_FORWARDING=1"], "1")):
            with self.subTest(flags=flags):
                result = subprocess.run([compiler, "-E", "-P", "-x", "c++", "-",
                                         "-I", str(ROOT / "src"), *flags],
                                        input=source, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        for value in ("-1", "2", "255"):
            with self.subTest(value=value):
                result = subprocess.run([compiler, "-E", "-P", "-x", "c++", "-",
                                         "-I", str(ROOT / "src"),
                                         "-DMESH_ENABLE_BATTERY_FORWARDING=" + value],
                                        input=source, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MESH_ENABLE_BATTERY_FORWARDING", result.stderr)

    def test_ci_links_exact_cable_only_profile_with_release_logging_and_gates(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        job = re.search(r"^  stm32-mini-repeater-smoke:\n(.*?)(?=^  [\w-]+:|\Z)",
                        workflow, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(job)
        script = job[1].replace("\\\n", "")
        self.assertIn("set -euo pipefail", script)
        self.assertIn("bash build.sh build-firmware " + TARGET, script)
        self.assertIn("--firmware-version v1.18.0.1-halo-keymind-cascade-dev", script)
        self.assertIn("--radio-preset usa-cascade-fixed --profile cascade", script)
        self.assertIn("--allow-no-ota", script)
        self.assertIn("DISABLE_DEBUG: '0'", script)
        self.assertNotIn("--require-ota", script)
        self.assertNotIn("pio run", script)
        self.assertNotIn(" &", script)
        self.assertNotRegex(script, r"continue-on-error|maximum_size|LFS_FLASH_TOTAL_SIZE|MAX_NEIGHBOURS")
        for test in ("test_stm32_mini_repeater_profile.py", "test_mini_repeater_logging.py",
                     "test_management_feature_gate.py", "test_carrier_wave.py"):
            self.assertIn("python3 -B test/" + test + " -v", script)
        command = textwrap.dedent(job[1].rsplit("        run: |\n", 1)[1])
        subprocess.run(["bash", "-n"], input=command, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
