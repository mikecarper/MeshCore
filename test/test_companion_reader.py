#!/usr/bin/env python3
"""Compare the real firmware decoder and CLI output against every source verse."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from test_message_navigation import PAIRING_PREAMBLE as PREAMBLE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/companion_reader"
spec = importlib.util.spec_from_file_location("pack_reader", ROOT / "tools/bible/pack_reader.py")
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class CompanionReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(packer.SOURCE.read_text(encoding="utf-8"))
        # Independent expected typography, not the packer's conversion helper.
        typography = str.maketrans({"\u00a0": " ", "\u2013": "-", "\u2014": "-",
                                   "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
        cls.expected_verses = {ref: text.translate(typography)
                               for ref, text in cls.document["verses"].items()}

    def test_generated_header_is_current(self):
        self.assertEqual(packer.HEADER.read_text(encoding="utf-8"), packer.render(self.document))

    def test_block_sizes_roundtrip_all_verses(self):
        for size in (1024, 2048, 4096):
            packed, blocks = packer.pack(self.document, size)
            actual = []
            for offset, length, plain_length, first in blocks:
                self.assertEqual(first, len(actual))
                plain = zlib.decompress(packed[offset:offset + length], wbits=-15)
                self.assertEqual(len(plain), plain_length)
                self.assertLessEqual(len(plain), size)
                self.assertEqual(plain[-1], 0)
                actual.extend(part.decode("ascii") for part in plain[:-1].split(b"\0"))
            self.assertEqual(actual, list(self.expected_verses.values()))

    def test_arm_reader_decode_frames_fit_existing_loop_stack(self):
        # Use the actual Cortex-M4 compiler when installed. This does not
        # invoke PlatformIO or reserve decode buffers in ordinary commands.
        tool = shutil.which("arm-none-eabi-g++")
        if not tool:
            candidate = Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++"
            if candidate.is_file():
                tool = str(candidate)
        if not tool:
            self.skipTest("ARM compiler unavailable; firmware stack qualification remains required")
        cc = str(Path(tool).with_name("arm-none-eabi-gcc"))
        with tempfile.TemporaryDirectory(prefix="mesh-reader-arm-stack-") as directory:
            directory = Path(directory)
            (directory / "Arduino.h").write_text(
                "#pragma once\n#include <stdint.h>\n#include <stddef.h>\n#include <stdio.h>\n#include <string.h>\n"
                "class Stream { public: virtual size_t write(const uint8_t*,size_t);size_t print(const char*); };\n")
            flags = ["-mcpu=cortex-m4", "-mthumb", "-Oz", "-fno-inline-small-functions",
                     "-fstack-usage", "-DNRF52_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                     "-DENABLE_USB_INTERFACE=1", "-I" + str(directory),
                     "-I" + str(ROOT / "src"), "-I" + str(FIXTURE)]
            self.run_checked([tool, "-std=gnu++17", "-fno-exceptions", "-fno-rtti", *flags,
                "-c", str(ROOT / "src/helpers/CompanionReader.cpp"), "-o", str(directory / "reader.o")])
            self.run_checked([cc, "-std=gnu11", *flags, "-c",
                str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(directory / "tinf.o")])

            def frames(name):
                result = {}
                for line in (directory / (name + ".su")).read_text().splitlines():
                    function, count, kind = line.rsplit("\t", 2)
                    self.assertEqual(kind, "static", line)
                    result[function] = int(count)
                return result

            def frame(rows, signature):
                found = [count for function, count in rows.items() if signature in function and " [with " not in function]
                self.assertEqual(len(found), 1, signature)
                return found[0]

            reader, decoder = frames("reader"), frames("tinf")
            # Summing all helper frames overbounds mutually exclusive branches
            # (tree building and block decoding), including their leaf calls.
            decoder_bytes = sum(count for name, count in decoder.items()
                if not name.endswith(":tinf_init") and not name.endswith(":tinf_uncompress"))
            lookup = frame(reader, "mesh::readReaderVerse(")
            terminal = (frame(reader, "mesh::handleReaderCommand(")
                + frame(reader, "::printReplyPage(") + frame(reader, "::replyVerse(")
                + lookup + decoder_bytes)
            loop_bytes, caller_reserve = 8192, 1536
            self.assertLessEqual(terminal + caller_reserve, loop_bytes)
            ui_max = 0
            for small_font in (0, 1):
                name = "screen" + str(small_font)
                self.run_checked([tool, "-std=gnu++17", "-fno-exceptions", "-fno-rtti", *flags,
                    f"-DUI_SMALL_MESSAGE_FONT={small_font}", "-DUI_BUTTON_READER_HINT=1",
                    "-DUI_READER_TOUCH_BAR=0", "-c", str(FIXTURE / "test_reader_screen.cpp"),
                    "-o", str(directory / (name + ".o"))])
                ui = frame(frames(name), "ReaderScreen::process(") + lookup + decoder_bytes
                ui_max = max(ui_max, ui)
                self.assertLessEqual(ui + caller_reserve, loop_bytes)
            self.assertEqual(packer.BLOCK_SIZE, 4096)
            print(f"ARM Reader local stack: terminal {terminal}, screen {ui_max}; "
                  f"caller reserve {caller_reserve}, loop {loop_bytes} bytes")

    def test_ascii_typography_preserves_words_case_and_pinned_source(self):
        original = copy.deepcopy(self.document)
        sample = '\u201creader\u2019s\u00a0Word\u201d\u2014\u2018Test\u2019\u2013Yes!'
        self.assertEqual(packer.ascii_verse(sample, 64), b'"reader\'s Word"-\'Test\'-Yes!')
        self.assertEqual(packer.ascii_verse('Already ASCII: reader 3:16.', 64),
                         b'Already ASCII: reader 3:16.')
        # Bounds apply after conversion, not to the original multibyte form.
        self.assertEqual(packer.ascii_verse('\u201cHi\u201d', 4), b'"Hi"')
        with self.assertRaises(ValueError):
            packer.ascii_verse('\u201cHi\u201d', 3)
        packer.pack(self.document)
        self.assertEqual(self.document, original)

    def test_ascii_data_size(self):
        packed, blocks = packer.pack(self.document)
        self.assertEqual(sum(block[2] for block in blocks), 96568)
        self.assertEqual(len(blocks), 24)
        self.assertEqual(len(packed), 38293)
        self.assertEqual(packer.block_index_layout(blocks), ("uint16_t", 8))
        self.assertEqual(len(packed) + 8 * len(blocks), 38485)

    def test_index_offsets_widen_before_any_block_start_can_truncate(self):
        self.assertEqual(packer.block_index_layout([(65535, 7, 2, 0)]), ("uint16_t", 8))
        self.assertEqual(packer.block_index_layout([(65536, 7, 2, 0)]), ("uint32_t", 12))
        self.assertEqual(packer.block_index_layout([(0xFFFFFFFF, 7, 2, 0)]), ("uint32_t", 12))
        for offset in (-1, 0x100000000):
            with self.assertRaises(ValueError):
                packer.block_index_layout([(offset, 7, 2, 0)])
        with patch.object(packer, "pack", return_value=(b"", [(65536, 7, 2, 0)])):
            header = packer.render(self.document)
        self.assertIn("using ReaderBlockIndex = BlockIndexT<uint32_t>;", header)
        self.assertIn("using ReaderCorpus = CorpusT<uint32_t>;", header)
        self.assertIn("sizeof(ReaderBlockIndex) == 12", header)
        self.assertIn("{65536, 7, 2, 0}", header)

    def test_invalid_sources_are_rejected(self):
        for bad_text in ("", "leading\ncommand", "bad\x1b[2J", "bad\0text",
                         "bad\u202eRTL", " too much space", "a" * packer.BLOCK_SIZE,
                         "caf\u00e9", "unmapped\u2026punctuation", "bad\U0001f600text"):
            document = copy.deepcopy(self.document)
            document["verses"]["1:1"] = bad_text
            with self.assertRaises(ValueError):
                packer.pack(document)
        for key in ("1:1", "21:25"):
            document = copy.deepcopy(self.document)
            del document["verses"][key]
            with self.assertRaises(ValueError):
                packer.pack(document)

    def test_firmware_decoder_and_terminal_on_both_platform_profiles(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        with tempfile.TemporaryDirectory(prefix="meshcore-reader-") as directory:
            directory = Path(directory)
            for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
                flags = [f"-D{platform}=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
                obj, binary = directory / "tinf.o", directory / "reader.exe"
                self.run_checked([cc, "-std=c11", "-Os", *flags, "-c",
                                  str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(obj)])
                self.run_checked([cxx, "-std=c++17", "-Os", "-Wall", "-Wextra", "-Werror",
                                  *flags, "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                  str(FIXTURE / "test_reader_lookup.cpp"),
                                  str(ROOT / "src/helpers/CompanionReader.cpp"), str(obj), "-o", str(binary)])
                result = self.run_checked([str(binary), "--dump"])
                actual = dict(line.split("\t", 1) for line in result.splitlines())
                self.assertEqual(actual, self.expected_verses)

    def test_other_profiles_exclude_corpus(self):
        cxx = shutil.which("g++")
        if not cxx:
            self.skipTest("host G++ required")
        for flags in ([], ["-DESP32_PLATFORM=1"], ["-DNRF52_PLATFORM=1"],
                      ["-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1"],
                      ["-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                       "-DENABLE_USB_INTERFACE=1", "-DCOMPANION_FEATURE_READER=0"]):
            result = self.run_checked([cxx, "-E", "-P", *flags,
                                      str(ROOT / "src/helpers/CompanionReader.cpp")])
            self.assertNotIn("readerData", result)
            self.assertNotIn("handleReaderCommand", result)

    def test_tcp_reader_write_returns_immediately_under_socket_backpressure(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("host G++ required")
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        callback = extract_braced(source, "static size_t ota_console_try_reader_write(")
        harness = r'''
#include <Arduino.h>
#include <cassert>
#include <sys/socket.h>
#include <unistd.h>
class WiFiClient : public Stream {
  int socket_;
public:
  explicit WiFiClient(int socket) : socket_(socket) {}
  int fd() const { return socket_; }
};
@CALLBACK@
int main() {
  alarm(3); // A blocking write to a full socket fails the test, never hangs CI.
  int sockets[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  int capacity = 1024;
  assert(setsockopt(sockets[0], SOL_SOCKET, SO_SNDBUF, &capacity, sizeof(capacity)) == 0);
  uint8_t data[96] = {};
  while (send(sockets[0], data, sizeof(data), MSG_DONTWAIT) > 0) {}
  WiFiClient client(sockets[0]), invalid(-1);
  for (int i = 0; i < 100; ++i)
    assert(ota_console_try_reader_write(client, data, sizeof(data)) == 0);
  assert(client.writes == 0); // The blocking Stream path must never be used.
  uint8_t drained[8192];
  assert(recv(sockets[1], drained, sizeof(drained), 0) > 0);
  assert(ota_console_try_reader_write(client, data, sizeof(data)) > 0);
  assert(ota_console_try_reader_write(invalid, data, sizeof(data)) == 0);
  close(sockets[0]);
  close(sockets[1]);
}
'''.replace("@CALLBACK@", callback)
        with tempfile.TemporaryDirectory(prefix="mesh-reader-socket-") as directory:
            cpp = Path(directory) / "socket.cpp"
            binary = Path(directory) / "socket-test"
            cpp.write_text(harness)
            self.run_checked([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                              "-I" + str(FIXTURE), str(cpp), "-o", str(binary)])
            self.run_checked([str(binary)])

    def test_terminal_dispatch_is_separate_from_bounded_api_replies(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        start = source.index("void MyMesh::handleTerminalCommand(")
        end = source.index("\nvoid MyMesh::enterCLIRescue()", start)
        terminal = source[start:end]
        self.assertEqual(source.count("mesh::handleReaderCommand("), 2)
        self.assertLess(terminal.index("mesh::handleReaderCommand("),
                        terminal.index("char local_reply[160]"))
        self.assertIn("get reader <chapter>[:<verse>[-<last>]] [page] (World English Bible, offline)", terminal)
        self.assertEqual(terminal.count("#if COMPANION_FEATURE_READER"), 2)
        rescue = extract_braced(source, "void MyMesh::checkCLIRescueCmd()")
        self.assertLess(rescue.index("mesh::handleReaderCommand(cli_command, output)"),
                        rescue.index("handleCommand(cli_command, 0, reply_buf)"))

    def test_production_bounded_reader_dispatch_preserves_tags_and_local_access(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        handler = extract_braced(source, "bool MyMesh::handleCommand(")
        # Compile the actual dispatch prefix, including its AA| correlation
        # handling and local-console gate, against the real Reader handler.
        handler = handler[:handler.index("#if defined(NRF52_POWER_MANAGEMENT)")]
        handler += "  return false;\n}\n"
        predicate = extract_braced(source, "static bool isCompanionReaderRequest(")
        harness = r'''
#include <Arduino.h>
#include <helpers/CompanionReader.h>
#include <cassert>
#include <cstring>
#include <string>
struct Radio { bool active = false; };
namespace mesh {
bool morseTimingActive(const Radio* radio) { return radio && radio->active; }
namespace power { bool handleRegulatorCommand(const char*,char*,size_t) { return false; } }
}
@PREDICATE@
struct MyMesh {
  Radio radio;
  Radio* _radio = &radio;
  bool handleCommand(const char*, uint32_t, char*);
};
@HANDLER@
int main() {
  MyMesh mesh;
  struct Guarded { char reply[160]; uint32_t after; } out{};
  out.after = 0xaabbccdd;
  assert(mesh.handleCommand("AA|get reader 3:16", 0, out.reply));
  assert(std::string(out.reply).find("AA|Reader 3:16 (WEB)\n") == 0);
  assert(std::string(out.reply).find("eternal life.") != std::string::npos);
  assert(std::string(out.reply).find("Next:") == std::string::npos);
  assert(strlen(out.reply) < sizeof(out.reply) && out.after == 0xaabbccdd);
  assert(mesh.handleCommand("B2|get reader 21:17", 0, out.reply));
  assert(std::string(out.reply).find("B2|Reader 21:17 [1/") == 0);
  assert(std::string(out.reply).find("Next: get reader 21:17 2") != std::string::npos);
  assert(strlen(out.reply) < sizeof(out.reply) && out.after == 0xaabbccdd);
  assert(mesh.handleCommand("C3|get reader 21:17 2", 0, out.reply));
  assert(std::string(out.reply).find("C3|Reader 21:17 [2/") == 0);
  assert(strlen(out.reply) < sizeof(out.reply) && out.after == 0xaabbccdd);
  assert(mesh.handleCommand("D4|get reader", 0, out.reply));
  assert(std::string(out.reply).find("D4|ERROR: use get reader") == 0);
  assert(!mesh.handleCommand("get reader 3:16", 123, out.reply));
  assert(!mesh.handleCommand("AA|get reader 3:16", 123, out.reply));
  assert(!mesh.handleCommand("get name", 0, out.reply));
  // Block synchronous 4 KiB decompression before it can stall Morse sampling,
  // including tagged bounded replies. Ordinary readers resume unchanged.
  mesh.radio.active = true;
  assert(mesh.handleCommand("E5|get reader 21:17 2", 0, out.reply));
  assert(std::string(out.reply) == "E5|Error: Reader unavailable during Morse");
  assert(strlen(out.reply) < sizeof(out.reply) && out.after == 0xaabbccdd);
  assert(!mesh.handleCommand("get name", 0, out.reply));
  assert(!mesh.handleCommand("get reader 3:16", 123, out.reply));
  mesh.radio.active = false;
  assert(mesh.handleCommand("F6|get reader 3:16", 0, out.reply));
  assert(std::string(out.reply).find("F6|Reader 3:16 (WEB)\n") == 0);
}
'''.replace("@HANDLER@", handler).replace("@PREDICATE@", predicate)
        with tempfile.TemporaryDirectory(prefix="meshcore-reader-dispatch-") as directory:
            directory = Path(directory)
            generated = directory / "dispatch.cpp"
            generated.write_text(harness)
            for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
                flags = [f"-D{platform}=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
                obj, binary = directory / "tinf.o", directory / "dispatch.exe"
                self.run_checked([cc, "-std=c11", "-Os", *flags, "-c",
                                  str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(obj)])
                self.run_checked([cxx, "-std=c++17", "-Os", "-Wall", "-Wextra", "-Werror",
                                  *flags, "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                  str(generated), str(ROOT / "src/helpers/CompanionReader.cpp"),
                                  str(obj), "-o", str(binary)])
                self.run_checked([str(binary)])

    def test_reader_navigation_and_bookmark_recovery(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        with tempfile.TemporaryDirectory(prefix="meshcore-reader-reader-") as directory:
            directory = Path(directory)
            flags = ["-DNRF52_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
            obj, binary = directory / "tinf.o", directory / "reader.exe"
            self.run_checked([cc, "-Os", *flags, "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                              "-o", str(obj)])
            for small_font in (0, 1):
                for button_hint, touch_bar in ((0, 0), (1, 0), (1, 1)):
                    self.run_checked([cxx, "-std=c++11", "-Os", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                                      *flags, f"-DUI_SMALL_MESSAGE_FONT={small_font}",
                                      f"-DUI_BUTTON_READER_HINT={button_hint}",
                                      f"-DUI_READER_TOUCH_BAR={touch_bar}",
                                      "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                      str(FIXTURE / "test_reader_screen.cpp"), str(ROOT / "src/helpers/CompanionReader.cpp"),
                                      str(obj), "-o", str(binary)])
                    self.run_checked([str(binary)])

    def test_actual_reader_stream_pauses_and_decode_entrypoints_reject_during_morse(self):
        cc, cxx = shutil.which("gcc"), shutil.which("g++")
        if not cc or not cxx:
            self.skipTest("host GCC and G++ required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        predicate = extract_braced(source, "static bool isCompanionReaderRequest(")

        def reader_block(method, marker):
            body = extract_braced(source, method)
            position = body.index(marker)
            start = body.rfind("#if COMPANION_FEATURE_READER", 0, position)
            return body[start:body.index("#endif", position) + len("#endif")]

        terminal = reader_block("void MyMesh::handleTerminalCommand(", "mesh::handleReaderCommand(")
        rescue = reader_block("void MyMesh::checkCLIRescueCmd()", "mesh::handleReaderCommand(cli_command, output)")
        service = reader_block("void MyMesh::loop()", "mesh::serviceReaderCommand();")
        harness = r'''
#include <Arduino.h>
#include <helpers/CompanionReader.h>
#include <cassert>
#include <cstring>
#include <string>
struct Radio { bool active = false; };
namespace mesh {
bool morseTimingActive(const Radio* radio) { return radio && radio->active; }
}
@PREDICATE@
struct MyMesh {
  Radio radio;
  Radio* _radio = &radio;
  Stream output;
  unsigned ordinary = 0;
  Stream& terminalOutput() { return output; }
  void terminalCommand(const char* command, bool reader_prompt = true,
                       mesh::ReaderTryWrite reader_try_write = nullptr) {
@TERMINAL@
    ++ordinary;
  }
  void rescueCommand(const char* cli_command) {
    Stream& output = this->output;
@RESCUE@
    { ++ordinary; }
  }
  void loopReader() {
@SERVICE@
  }
};
int main() {
  assert(isCompanionReaderRequest("  GET\tREADER 3:16 2"));
  assert(isCompanionReaderRequest("get reader"));
  assert(!isCompanionReaderRequest("get readership"));
  assert(!isCompanionReaderRequest("morse stop"));
  assert(!isCompanionReaderRequest(nullptr));
  MyMesh mesh;
  mesh.terminalCommand("get reader 3:16");
  assert(mesh::readerCommandActive(&mesh.output));
  mesh.loopReader(); // A real chapter header is partially/fully admitted.
  const auto before = mesh.output.text;
  const auto writes = mesh.output.writes;
  mesh.radio.active = true;
  for (int n = 0; n < 1000; ++n) mesh.loopReader();
  assert(mesh.output.text == before && mesh.output.writes == writes);
  assert(mesh::readerCommandActive(&mesh.output)); // Pausing retains the cursor.
  mesh.radio.active = false;
  for (int n = 0; mesh::readerCommandActive(); ++n) {
    assert(n < 1000); mesh.loopReader();
  }
  assert(mesh.output.text.find("eternal life.") != std::string::npos);
  assert(mesh.output.text.find("Reader 3:16", mesh.output.text.find("Reader 3:16") + 1) == std::string::npos);
  assert(mesh.output.text.size() > before.size());

  mesh.terminalCommand("get reader 3:16");
  assert(mesh::readerCommandActive());
  mesh.radio.active = true; mesh.output.text.clear();
  mesh.terminalCommand("get reader 21:17 2"); // Synchronous page must not inflate.
  assert(mesh.output.text == "  ERROR: Reader unavailable during Morse\r\n");
  assert(!mesh::readerCommandActive());
  mesh.output.text.clear(); mesh.rescueCommand("get reader 3:16 1");
  assert(mesh.output.text == "  ERROR: Reader unavailable during Morse\r\n");
  assert(!mesh::readerCommandActive());
  mesh.output.text.clear(); mesh.terminalCommand("morse stop");
  mesh.rescueCommand("morse stop");
  assert(mesh.ordinary == 2 && mesh.output.text.empty()); // Stop/input still dispatch.

  mesh.radio.active = false;
  mesh.rescueCommand("get reader 3:16");
  assert(mesh::readerCommandActive());
  for (int n = 0; mesh::readerCommandActive(); ++n) {
    assert(n < 1000); mesh.loopReader();
  }
  assert(mesh.output.text.find("eternal life.") != std::string::npos);
}
'''.replace("@PREDICATE@", predicate).replace("@TERMINAL@", terminal).replace("@RESCUE@", rescue).replace("@SERVICE@", service)
        with tempfile.TemporaryDirectory(prefix="meshcore-reader-morse-") as directory:
            directory = Path(directory)
            generated = directory / "interlock.cpp"
            generated.write_text(harness)
            for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
                flags = [f"-D{platform}=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1"]
                obj, binary = directory / "tinf.o", directory / "interlock.exe"
                self.run_checked([cc, "-std=c11", "-Os", *flags, "-c",
                                  str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(obj)])
                self.run_checked([cxx, "-std=c++17", "-Os", "-Wall", "-Wextra", "-Werror",
                                  *flags, "-I" + str(ROOT / "src"), "-I" + str(FIXTURE),
                                  str(generated), str(ROOT / "src/helpers/CompanionReader.cpp"),
                                  str(obj), "-o", str(binary)])
                self.run_checked([str(binary)])

    def test_reader_button_routing_and_profile_gate(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(encoding="utf-8")
        long_press = ui[ui.index("char UITask::handleLongPress("):]
        self.assertLess(long_press.index("isReaderActive()"), long_press.index("enterCLIRescue()"))
        self.assertLess(long_press.index("isRadioPage()"), long_press.index("enterCLIRescue()"))
        self.assertIn("#if COMPANION_FEATURE_READER\n#include \"ReaderScreen.h\"", ui)
        self.assertLess(ui.index("#define UI_BUTTON_READER_HINT 1"),
                        ui.index('#include "ReaderScreen.h"'))
        # Queue downloads preserve the current screen unconditionally now;
        # test_companion_inbox exercises this with the reader enabled.
        self.assertIn("else if (isReaderActive())", ui)
        self.assertIn("c = handleLongPress(KEY_ENTER);", ui)
        self.assertIn("handleDoubleClick(KEY_PREV)", ui)

    def test_reader_bookmark_follows_display_power_transitions(self):
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text(encoding="utf-8")
        stubs = r'''
struct Board {
  bool external = false, host = false;
  bool isExternalPowered() const { return external; }
  bool isUsbHostConnected() const { return host; }
};
struct Interfaces { bool takePairingRequest() { return false; } };
struct ReaderScreen : Screen { int saves = 0; void flush() { ++saves; } };
'''
        members = r'''
  Board* _board;
  Interfaces* _interfaceManager;
  uint32_t _pairing_screen_until = 0;
  bool hasConnection() const { return false; }
  bool isPairingScreenActive() const { return false; }
  void finishPairingScreen(bool) {}
  void servicePairingState();
'''
        source = PREAMBLE.replace("class UITask {", stubs + "class UITask {")
        source = source.replace("int getMsgCount() const { return 0; }", members)
        source += extract_braced(ui, "void UITask::servicePairingState(") + "\n"
        source += extract_braced(ui, "char UITask::checkDisplayOn(") + r'''
int main() {
  using namespace mesh::ui;
  resetArduinoMock();
  displayPowerPrefs().battery = {DisplayMode::Button, 5};
  displayPowerPrefs().usb = {DisplayMode::On, 5};
  Display display;
  Screen home;
  ReaderScreen reader;
  Board board;
  Interfaces interfaces;
  UITask task(display);
  task._board = &board; task._interfaceManager = &interfaces;
  task.curr = task.home = &home; task.reader = &reader;
  task.servicePairingState();
  assert(!display.isOn() && reader.saves == 0);
  task.curr = &reader;
  assert(task.checkDisplayOn(KEY_NEXT) == 0); // Wake consumes navigation.
  assert(display.isOn());
  g_mock_millis = 4999; task.servicePairingState();
  assert(display.isOn() && reader.saves == 0);
  g_mock_millis = 5000; task.servicePairingState();
  assert(!display.isOn());
  const int expected_saves = COMPANION_FEATURE_READER ? 1 : 0;
  assert(reader.saves == expected_saves);
  task.servicePairingState();
  assert(reader.saves == expected_saves); // No repeated writes while dark.
  for (bool* plugged : {&board.external, &board.host}) {
    *plugged = true; task.servicePairingState();
    assert(display.isOn());
    g_mock_millis += 10000; task.servicePairingState();
    assert(display.isOn()); // USB's always-on policy applies to the reader.
    int before = reader.saves;
    *plugged = false; task.servicePairingState();
    assert(!display.isOn() && reader.saves == before + expected_saves);
  }
  task.curr = &home;
  task.checkDisplayOn(KEY_NEXT);
  int before = reader.saves;
  g_mock_millis += 5000; task.servicePairingState();
  assert(!display.isOn() && reader.saves == before);
  assert(task.pairing_previews == 0); // Bluetooth-disabled reader wakes stay ordinary.
  task.bluetooth_enabled = true;
  task.curr = &reader;
  before = reader.saves;
  assert(task.checkDisplayOn(KEY_NEXT) == 0 && display.isOn());
  assert(task.pairing_previews == 1 && task.pairing_from_button);
  assert(reader.saves == before); // PIN preview does not write the bookmark.
  task.bluetooth_connected = true;
  display.turnOff();
  assert(task.checkDisplayOn(KEY_NEXT) == 0 && display.isOn());
  assert(task.pairing_previews == 1); // No new PIN for a connected BLE peer.
  task._display = nullptr; task.servicePairingState();
}
'''
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "host G++ required")
        with tempfile.TemporaryDirectory(prefix="mesh-reader-power-") as directory:
            directory = Path(directory)
            (directory / "power.cpp").write_text(source, encoding="utf-8")
            for enabled in (0, 1):
                with self.subTest(reader_enabled=enabled):
                    binary = directory / "power.exe"
                    self.run_checked([compiler, "-std=c++17", f"-DCOMPANION_FEATURE_READER={enabled}",
                                      "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                                      str(directory / "power.cpp"),
                                      str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                                      str(ROOT / "src/helpers/ui/DisplayDriver.cpp"), "-o", str(binary)])
                    self.run_checked([str(binary)])

    def run_checked(self, command):
        result = subprocess.run(command, capture_output=True, encoding="utf-8",
                                errors="backslashreplace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
