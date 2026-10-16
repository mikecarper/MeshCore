"""Exercise the S3 watchdog-finish adapter against inert flasher functions.

No serial device is opened. fuser/USB/DFU tools are replaced with explicit
non-hardware stubs; the fake flasher records only phase ordering and failures.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools/hil/mercer_mt_flash.sh"
PORT = "/dev/ttyACM99"
MAC = "aabbccddeeff"

FAKE_FUNCTIONS = r'''
esptool_set_variables() {
    NORESET=no-reset
    WATCHDOGRESET=${SIM_RESET_MODE-watchdog-reset}
    READMAC=read-mac
    printf 'variables\n' >> "$SIM_LOG"
}
prepare_esp32_flash_session() {
    printf 'prepare\n' >> "$SIM_LOG"
    ESP32_SESSION_IS_S3=${SIM_S3:-1}
    ESP32_FLASH_EXPECTED_MAC=${SIM_ACTUAL_MAC:-aabbccddeeff}
}
selected_flash_serial_port() {
    printf '%s\n' "$1"
}
esp32_verified_destructive_port() {
    printf 'verify:%s\n' "$2" >> "$SIM_LOG"
    if [[ "${SIM_FAIL_PHASE:-}" == "$2" ]]; then return 47; fi
    printf '%s\n' "$1"
}
run_esp32_session_esptool() {
    printf 'run:%s\n' "$*" >> "$SIM_LOG"
    case " $* " in
        *' flash-id '*) printf 'Detected flash size: %sMB\n' "${SIM_FLASH_MB:-16}" ;;
        *' write-flash '*)
            if [[ "${SIM_FAIL_PHASE:-}" == write ]]; then return 42; fi
            # Model the production wrapper's positive verification contract:
            # a missing verification marker is failure, even after exit zero.
            if [[ "${SIM_FAIL_PHASE:-}" == unverified-write ]]; then return 43; fi
            printf 'Hash of data verified.\n'
            printf 'verified-write\n' >> "$SIM_LOG" ;;
        *' write-mem '*)
            if [[ "${SIM_FAIL_PHASE:-}" == volatile ]]; then return 44; fi ;;
        *' --after watchdog-reset '*)
            if [[ "${SIM_FAIL_PHASE:-}" == reset ]]; then return 45; fi ;;
    esac
}
finish_esp32_flash_session() {
    printf 'ordinary-finish\n' >> "$SIM_LOG"
}
'''


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"),
                     "POSIX Bash required; run on Linux/WSL")
class MercerMtWatchdogFinishTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="meshcore-mt-finish-mock-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.log = self.work / "actions.log"
        self.startup = self.work / "FORBIDDEN-STARTUP"
        self.flasher = self.work / "mock-mcfirmware.sh"
        self.image = self.work / "application.bin"
        self.image.write_bytes(b"INERT MOCK IMAGE; NEVER FLASH")
        # Startup commands and traps must never be sourced by the adapter.
        source = ('#!/usr/bin/env bash\n'
                  'printf startup > "$SIM_STARTUP"\n'
                  'trap \'printf unwanted-trap >> "$SIM_LOG"\' EXIT\n'
                  'exit 98\n') + FAKE_FUNCTIONS
        # The adapter deliberately requires a complete definitions inventory.
        source += "".join(f"inert_function_{i}() {{\n    return 0\n}}\n"
                          for i in range(100))
        self.flasher.write_text(source, encoding="utf-8")
        bindir = self.work / "bin"
        bindir.mkdir()
        for name in ("python3", "pipx", "timeout", "udevadm"):
            path = bindir / name
            path.write_text("#!/bin/sh\nprintf 'FORBIDDEN-TOOL\\n' >> \"$SIM_LOG\"\nexit 99\n",
                            encoding="utf-8")
            path.chmod(0o700)
        fuser = bindir / "fuser"
        fuser.write_text("#!/bin/sh\nprintf 'owner-check:%s\\n' \"$*\" >> \"$SIM_LOG\"\n"
                         'if [ "${SIM_BUSY:-0}" = 1 ]; then printf "1234\\n"; exit 0; fi\nexit 1\n',
                         encoding="utf-8")
        fuser.chmod(0o700)
        self.env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                        SIM_LOG=str(self.log), SIM_STARTUP=str(self.startup))
        for name in ("SIM_RESET_MODE", "SIM_S3", "SIM_ACTUAL_MAC", "SIM_FAIL_PHASE",
                     "SIM_BUSY", "SIM_FLASH_MB"):
            self.env.pop(name, None)

    def invoke(self, *, mode="write-flash", watchdog=True, allow=True,
               allow_reset=False, operation_args=None, expected_mac=MAC,
               flash_bytes="16777216", environment=None):
        command = [shutil.which("bash"), str(ADAPTER), "--flasher", str(self.flasher),
                   "--port", PORT, "--workdir", str(self.work),
                   "--expect-mac", expected_mac, "--flash-bytes", flash_bytes]
        if watchdog:
            command.append("--watchdog-finish")
        if allow:
            command.extend(("--allow-write", "--allow-erase"))
        if allow_reset:
            command.append("--allow-reset")
        command.extend(("--", mode))
        if mode == "write-flash":
            command.extend(("0x10000", str(self.image)))
        elif mode == "read-flash":
            command.extend(("0x10000", "32", str(self.work / "unused-read.bin")))
        elif mode == "erase-region":
            command.extend(("0x10000", "4096"))
        if operation_args:
            command.extend(operation_args)
        result = subprocess.run(command, text=True, capture_output=True,
                                env=dict(self.env, **(environment or {})),
                                timeout=10, check=False)
        self.assertFalse(self.startup.exists(), "the flasher's global startup ran")
        actions = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        self.assertNotIn("FORBIDDEN-TOOL", actions)
        self.assertNotIn("unwanted-trap", actions)
        return result, actions

    def test_watchdog_refuses_every_nonwrite_mode_before_flasher_or_serial_tools(self):
        for mode in ("probe", "read-flash", "erase-flash", "erase-region"):
            with self.subTest(mode=mode):
                result, actions = self.invoke(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--watchdog-finish requires write-flash", result.stderr)
                self.assertEqual(actions, "")

    def test_watchdog_does_not_grant_write_permission(self):
        result, actions = self.invoke(allow=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("write-flash requires --allow-write", result.stderr)
        self.assertEqual(actions, "")

    def test_s3_and_supported_reset_are_required_before_any_flash_write(self):
        for environment, message in (({"SIM_S3": "0"}, "did not identify ESP32-S3"),
                                     ({"SIM_RESET_MODE": ""}, "supported S3 watchdog reset"),
                                     ({"SIM_RESET_MODE": "hard-reset"}, "supported S3 watchdog reset")):
            with self.subTest(environment=environment):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertIn("prepare", actions)
                self.assertNotIn("write-flash", actions)
                self.assertNotIn("write-mem", actions)
                self.assertNotIn("--after watchdog-reset", actions)

    def test_expected_mac_is_checked_before_write_and_finish(self):
        result, actions = self.invoke(environment={"SIM_ACTUAL_MAC": "112233445566"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MAC mismatch", result.stderr)
        self.assertNotIn("write-flash", actions)
        self.assertNotIn("write-mem", actions)

    def test_verified_write_precedes_masked_clear_identity_recheck_and_one_watchdog(self):
        result, actions = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        phases = ["verify:write-flash", " write-flash ", "verified-write",
                  "verify:S3 volatile forced-ROM bit clear", "write-mem 0x6000812c 0 0x1",
                  "verify:S3 watchdog finish", "--after watchdog-reset --baud 115200 read-mac"]
        positions = [actions.index(phase) for phase in phases]
        self.assertEqual(positions, sorted(positions), actions)
        self.assertEqual(actions.count("--after watchdog-reset"), 1)
        self.assertNotIn("ordinary-finish", actions)
        self.assertNotIn("erase-flash", actions)
        self.assertIn("Application boot still requires an application-level check", result.stdout)

    def test_failed_or_unverified_write_cannot_clear_rom_or_reset(self):
        for phase in ("write", "unverified-write"):
            with self.subTest(phase=phase):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(environment={"SIM_FAIL_PHASE": phase})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(" write-flash ", actions)
                self.assertNotIn("write-mem", actions)
                self.assertNotIn("--after watchdog-reset", actions)
                self.assertNotIn("ordinary-finish", actions)

    def test_either_finish_identity_failure_prevents_the_next_phase(self):
        for phase, clear_expected in (("S3 volatile forced-ROM bit clear", False),
                                      ("S3 watchdog finish", True)):
            with self.subTest(phase=phase):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(environment={"SIM_FAIL_PHASE": phase})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual("write-mem" in actions, clear_expected)
                self.assertNotIn("--after watchdog-reset", actions)
                self.assertNotIn("ordinary-finish", actions)

    def test_volatile_clear_failure_cannot_watchdog_or_fall_back_to_hard_reset(self):
        result, actions = self.invoke(environment={"SIM_FAIL_PHASE": "volatile"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("write-mem", actions)
        self.assertNotIn("--after watchdog-reset", actions)
        self.assertNotIn("ordinary-finish", actions)

    def test_failed_watchdog_is_not_retried_or_followed_by_another_reset(self):
        result, actions = self.invoke(environment={"SIM_FAIL_PHASE": "reset"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actions.count("--after watchdog-reset"), 1)
        self.assertNotIn("ordinary-finish", actions)
        self.assertNotIn("Application boot still requires", result.stdout)

    def test_without_flag_the_original_finish_is_retained(self):
        result, actions = self.invoke(watchdog=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ordinary-finish", actions)
        self.assertNotIn("write-mem", actions)
        self.assertNotIn("--after watchdog-reset", actions)

    def test_runtime_reset_requires_its_own_consent_and_zero_arguments_before_tools(self):
        for options, message in (({}, "reset-runtime requires --allow-reset"),
                                 ({"allow_reset": True, "operation_args": ["0x10000", str(self.image)]},
                                  "reset-runtime takes no arguments"),
                                 ({"allow_reset": True, "watchdog": True},
                                  "--watchdog-finish requires write-flash")):
            with self.subTest(options=options):
                self.log.unlink(missing_ok=True)
                config = {"mode": "reset-runtime", "watchdog": False, "allow": False, **options}
                result, actions = self.invoke(**config)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(actions, "")

    def test_runtime_reset_requires_exact_mac_and_size_and_never_grants_flash_authority(self):
        for field in ("expected_mac", "flash_bytes"):
            with self.subTest(field=field):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(mode="reset-runtime", watchdog=False,
                    allow=False, allow_reset=True, **{field: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("supply the verified expected MAC and flash bytes", result.stderr)
                self.assertEqual(actions, "")
        result, actions = self.invoke(mode="reset-runtime", watchdog=False,
            allow=False, allow_reset=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        phases = [" flash-id", "verify:S3 volatile forced-ROM bit clear",
                  "write-mem 0x6000812c 0 0x1", "verify:S3 watchdog finish",
                  "--after watchdog-reset --baud 115200 read-mac"]
        positions = [actions.index(phase) for phase in phases]
        self.assertEqual(positions, sorted(positions), actions)
        self.assertEqual(actions.count("--after watchdog-reset"), 1)
        for forbidden in ("write-flash", "erase-flash", "erase-region", "efuse",
                          "reset-runtime", "ordinary-finish"):
            self.assertNotIn(forbidden, actions)

    def test_runtime_reset_wrong_identity_chip_or_size_cannot_clear_or_watchdog(self):
        for environment, size in (({"SIM_ACTUAL_MAC": "112233445566"}, "16777216"),
                                  ({"SIM_S3": "0"}, "16777216"),
                                  ({"SIM_RESET_MODE": "hard-reset"}, "16777216"),
                                  ({}, "8388608")):
            with self.subTest(environment=environment, size=size):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(mode="reset-runtime", watchdog=False,
                    allow=False, allow_reset=True, flash_bytes=size, environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("write-mem", actions)
                self.assertNotIn("--after watchdog-reset", actions)
                self.assertNotIn("ordinary-finish", actions)

    def test_runtime_reset_rechecks_identity_and_fails_closed_without_fallback(self):
        for phase in ("S3 volatile forced-ROM bit clear", "volatile",
                      "S3 watchdog finish", "reset"):
            with self.subTest(phase=phase):
                self.log.unlink(missing_ok=True)
                result, actions = self.invoke(mode="reset-runtime", watchdog=False,
                    allow=False, allow_reset=True, environment={"SIM_FAIL_PHASE": phase})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(actions.count("--after watchdog-reset"), int(phase == "reset"))
                self.assertNotIn("ordinary-finish", actions)
                self.assertNotIn("write-flash", actions)
                self.assertNotIn("erase-flash", actions)

    def test_runtime_reset_busy_owner_is_never_killed_or_reset(self):
        result, actions = self.invoke(mode="reset-runtime", watchdog=False,
            allow=False, allow_reset=True, environment={"SIM_BUSY": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no owner will be killed", result.stderr)
        self.assertNotIn("write-mem", actions)
        self.assertNotIn("--after watchdog-reset", actions)
        self.assertNotIn("ordinary-finish", actions)

    def test_runtime_reset_accepts_verified_eight_mib_without_write_or_erase_consent(self):
        result, actions = self.invoke(mode="reset-runtime", watchdog=False,
            allow=False, allow_reset=True, flash_bytes="8388608",
            environment={"SIM_FLASH_MB": "8"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(actions.count("--after watchdog-reset"), 1)
        self.assertNotIn("write-flash", actions)
        self.assertNotIn("erase-flash", actions)


if __name__ == "__main__":
    unittest.main()
