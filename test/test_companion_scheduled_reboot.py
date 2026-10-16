#!/usr/bin/env python3
"""Execute the three real Companion reboot arms and the loop service guard."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/companion_radio/MyMesh.cpp"
FIXTURE = ROOT / "test/fixtures/companion_scheduled_reboot/test.cpp"
NORMALIZE = "if (_scheduled_reboot_at == 0) _scheduled_reboot_at = 1;"


def production_methods(source):
    terminal = extract_braced(source, '} else if (strcmp(command, "reboot") == 0)')
    terminal = terminal.replace("} else if", "if", 1)
    # This directive controls the next terminal else-if, outside the reboot
    # statements, but precedes the closing brace in the source text.
    terminal = terminal.replace(
        "#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)\n", "")
    logging = extract_braced(source, 'if (strncmp(command, "set usb.logging", 15) == 0')
    service = extract_braced(source,
        "if (!mesh::morseTimingActive(_radio) && _scheduled_reboot_at != 0")
    methods = "void MyMesh::terminalCommand(const char* command) {\n" + terminal + "\n}\n"
    methods += "bool MyMesh::loggingCommand(const char* command, char* reply, size_t reply_size) {\n"
    methods += logging + "\nreturn false;\n}\n"
    methods += re.search(r"^#define LAZY_CONTACTS_WRITE_DELAY\s+\d+", source,
                         re.MULTILINE).group(0) + "\n"
    for signature in ("void MyMesh::onContactCacheFlushed()",
                      "void MyMesh::scheduleContactWriteRetry()",
                      "bool MyMesh::flushContactsBeforeReboot()",
                      "bool MyMesh::prepareForUiShutdown()"):
        methods += extract_braced(source, signature) + "\n"
    methods += extract_braced(source, "void MyMesh::repairInternalExtraFS(") + "\n"
    methods += "void MyMesh::service() {\n" + service + "\n}\n"
    dispatcher = (ROOT / "src/Dispatcher.cpp").read_text(encoding="ascii")
    for signature in ("bool Dispatcher::millisHasNowPassed(",
                      "unsigned long Dispatcher::futureMillis("):
        methods += extract_braced(dispatcher, signature).replace("Dispatcher::", "MyMesh::") + "\n"
    return methods


class CompanionScheduledRebootTests(unittest.TestCase):
    def execute(self, legacy=False):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++17 compiler required")
        source = SOURCE.read_text(encoding="ascii")
        self.assertEqual(source.count(NORMALIZE), 3)
        if legacy:
            source = source.replace(NORMALIZE, "")
        with tempfile.TemporaryDirectory(prefix="companion-scheduled-reboot-") as directory:
            work = Path(directory)
            (work / "production.inc").write_text(production_methods(source), encoding="ascii")
            binary = work / "reboot.exe"
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitizer,
                f"-I{work}", f"-I{ROOT / 'src'}", str(FIXTURE), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for command in ("usb", "repair", "terminal"):
                with self.subTest(command=command):
                    checked = subprocess.run([str(binary), command], capture_output=True,
                                             text=True, timeout=10)
                    if legacy:
                        self.assertNotEqual(checked.returncode, 0)
                        self.assertIn("a wrapped reboot deadline must still reboot exactly once",
                                      checked.stderr)
                    else:
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn("PASS: scheduled Companion reboot", checked.stdout)

    def test_actual_commands_wrap_equality_morse_and_contact_save_gate(self):
        self.execute()

    def test_missing_zero_normalization_reproduces_lost_reboot_at_each_arm(self):
        self.execute(legacy=True)


if __name__ == "__main__":
    unittest.main()
