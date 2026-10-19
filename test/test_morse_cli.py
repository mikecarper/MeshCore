#!/usr/bin/env python3
"""Compile exact shared Morse CLI and checked persistent settings with faults."""
from pathlib import Path
import unittest
import zlib
import re
from test_morse_tx import compile_run, portable_radio_header
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class MorseCLITests(unittest.TestCase):
    def test_settings_codec_matches_independent_images_and_unaligned_input(self):
        cases = []
        for frequency in (100000, 0x020304, 902321, 1000000):
            for speed in (1, 25, 60):
                prefix = b"MC\x01" + bytes([speed]) + frequency.to_bytes(4, "little")
                golden = prefix + (zlib.crc32(prefix) ^ 0xffffffff).to_bytes(4, "little")
                cases.append("{%d,%d,{%s}}" % (
                    frequency, speed, ",".join(str(byte) for byte in golden)))
        code = r'''
#include <helpers/MorseSettings.h>
#include <cassert>
struct Case { uint32_t frequency; uint8_t speed; uint8_t expected[12]; };
int main() {
  const Case cases[] = {@CASES@};
  for (const auto& item : cases) {
    mesh::MorseSettings prefs;
    prefs.frequency_khz = item.frequency; prefs.wpm = item.speed;
    uint8_t encoded[mesh::MorseSettingsImageSize];
    mesh::encodeMorseSettings(prefs, encoded);
    assert(memcmp(encoded, item.expected, sizeof(encoded)) == 0);
    alignas(4) uint8_t unaligned[13];
    memcpy(unaligned + 1, item.expected, sizeof(item.expected));
    mesh::MorseSettings decoded;
    assert(mesh::decodeMorseSettings(unaligned + 1, sizeof(item.expected), decoded));
    assert(decoded.frequency_khz == item.frequency && decoded.wpm == item.speed);
  }
}
'''.replace("@CASES@", ",".join(cases))
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        compile_run(self, code, headers={"helpers/IdentityStore.h": fs_mock})

    def test_exact_shared_handler_strict_arguments_bounded_replies_and_rollback(self):
        code = (ROOT / "test/fixtures/morse_cli.cpp").read_text(encoding="ascii")
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        compile_run(self, code, flags=("-Wno-unused-parameter",),
                    headers={"Dispatcher.h": portable_radio_header(), "helpers/IdentityStore.h": fs_mock})

    def test_exact_receive_cli_timed_sessions_retained_paging_and_recovery(self):
        code = (ROOT / "test/fixtures/morse_cli.cpp").read_text(encoding="ascii").split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_rx_cli.cpp").read_text(encoding="ascii")
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        compile_run(self, code, flags=("-Wno-unused-parameter",),
                    headers={"Dispatcher.h": portable_radio_header(), "helpers/IdentityStore.h": fs_mock})

    def test_hil_only_trace_ring_sparse_edges_paging_and_reflected_prefix_canary(self):
        code = (ROOT / "test/fixtures/morse_cli.cpp").read_text(encoding="ascii").split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_trace_cli.cpp").read_text(encoding="ascii")
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        for hil in (0, 1):
            with self.subTest(hil=hil):
                compile_run(self, code, flags=("-Wno-unused-parameter", f"-DMESH_MORSE_HIL={hil}"),
                            headers={"Dispatcher.h": portable_radio_header(), "helpers/IdentityStore.h": fs_mock})

    def test_actual_common_dispatch_reserves_reflected_prefix_for_bulk_capture_pages(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="utf-8")
        dispatch = re.search(r"if \(mesh::handleMorseCommand\(_callbacks->getProfileRadio\(\),"
                             r"\s*command,\s*reply,\s*[^\n]+\)\) return;", common)
        self.assertIsNotNone(dispatch, "the real CommonCLI Morse dispatch is required")
        code = (ROOT / "test/fixtures/morse_cli.cpp").read_text(encoding="ascii").split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_reply_prefix.cpp").read_text(encoding="ascii").replace(
            "@COMMON_MORSE_CALL@", dispatch.group(0))
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        compile_run(self, code, flags=("-Wno-unused-parameter",),
                    headers={"Dispatcher.h": portable_radio_header(), "helpers/IdentityStore.h": fs_mock})

    def test_actual_settings_codec_boot_recovery_and_checked_writers_on_all_backends(self):
        fs_mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        fs_mock = fs_mock.replace("bool fail_write = false;", "bool fail_write = false; bool fail_presence = false;\n  std::vector<std::string> fail_read_paths;")
        fs_mock = fs_mock.replace("if (*mode == 'r' && fail_read_open) return {};",
            "if (*mode == 'r' && (fail_read_open || std::find(fail_read_paths.begin(), fail_read_paths.end(), path) != fail_read_paths.end())) return {};")
        presence = ("#pragma once\nnamespace mesh { template <typename FS> bool filePresence(FS* fs, "
                    "const char* path, bool& present) { if (!fs || fs->fail_presence) return false; "
                    "present = fs->exists(path); return true; } }\n")
        transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii").replace(
            '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>').replace(
            '#include "PersistentStoreFormat.h"', '#include <helpers/PersistentStoreFormat.h>')
        headers = {
            "helpers/IdentityStore.h": fs_mock,
            "FilePresence.h": presence,
            "AtomicFileWriter.h": (ROOT / "src/helpers/AtomicFileWriter.h").read_text(encoding="ascii"),
            "ContactFileTransaction.h": transaction,
        }
        prefix = b"MC\x01\x19" + (902100).to_bytes(4, "little")
        golden = prefix + (zlib.crc32(prefix) ^ 0xffffffff).to_bytes(4, "little")
        main = (ROOT / "test/fixtures/morse_settings.cpp").read_text(encoding="ascii").replace(
            "@DEFAULT_IMAGE@", ",".join(str(byte) for byte in golden))
        code = (ROOT / "src/helpers/MorseSettings.cpp").read_text(encoding="ascii").replace(
            '#include "MorseSettings.h"', '#include <helpers/MorseSettings.h>') + "\n" + main
        for platform in ("NRF52_PLATFORM", "STM32_PLATFORM", "ESP32_PLATFORM", "RP2040_PLATFORM"):
            with self.subTest(platform=platform):
                compile_run(self, code, flags=("-D" + platform, "-DATOMIC_FILE_WRITER_IMPLEMENTATION",
                                              "-Wno-sign-compare"), headers=headers)

    def test_actual_companion_binary_webconfig_and_common_cli_fem_guards(self):
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        code = (ROOT / "test/fixtures/morse_fem_guard.cpp").read_text(encoding="ascii")
        code = code.replace("@COMPANION_METHODS@", "\n".join(method(companion, signature) for signature in (
            "bool MyMesh::applyAndSaveFemRxGain(", "bool MyMesh::applyAndSaveFemTxGain(")))
        branches = "\nelse ".join(method(common, signature) for signature in (
            'if (memcmp(config, "radio.fem.rxgain ", 17) == 0)',
            'if (memcmp(config, "radio.fem.txgain ", 17) == 0)'))
        compile_run(self, code.replace("@COMMON_BRANCHES@", branches))


if __name__ == "__main__":
    unittest.main()
