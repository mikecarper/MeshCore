#!/usr/bin/env python3
"""Run actual bounded board LoRa/CLI wrappers against their real file store."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_room_board_store import FILESYSTEM_HARNESS, LFS_MOCK, STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]

TESTS = r'''
#define FILE_O_WRITE 1
#include <helpers/RoomBoardProtocol.h>
using Result = mesh::RoomBoardResult;
static unsigned scenarios = 0;
static Result save(MemoryFS& fs, uint8_t id, const std::string& title, const std::string& body) {
  metadata_filesystem = &fs;
  return mesh::saveRoomBoardArticle(&fs, id, title.c_str(), body.size(),
      [&body](size_t offset, uint8_t* out, size_t length) -> size_t {
        memcpy(out, body.data() + offset, length); return length;
      });
}
static std::vector<uint8_t> indexRequest(uint32_t revision = 0, uint8_t start = 0) {
  std::vector<uint8_t> request(7); request[0] = 10;
  mesh::room_board_detail::put32(request.data() + 2, revision); request[6] = start; return request;
}
static std::vector<uint8_t> readRequest(uint8_t id, uint32_t version, uint16_t offset = 0) {
  std::vector<uint8_t> request(9); request[0] = 10; request[1] = 1; request[2] = id;
  mesh::room_board_detail::put32(request.data() + 3, version);
  mesh::room_board_detail::put16(request.data() + 7, offset); return request;
}
static std::vector<uint8_t> request(MemoryFS& fs, const std::vector<uint8_t>& payload, size_t capacity = 160) {
  metadata_filesystem = &fs;
  std::vector<uint8_t> out(capacity + 2, 0x7f);
  const size_t used = mesh::handleRoomBoardRequest(&fs, payload.data(), payload.size(), out.data() + 1, capacity);
  assert(used <= capacity && out.front() == 0x7f && out.back() == 0x7f);
  ++scenarios; return std::vector<uint8_t>(out.begin() + 1, out.begin() + 1 + used);
}
static std::string command(MemoryFS& fs, const std::string& text, size_t capacity = 160, bool handled = true) {
  metadata_filesystem = &fs;
  std::vector<char> out(capacity + 2, 0x7f);
  assert(mesh::handleRoomBoardCommand(&fs, text.c_str(), out.data() + 1, capacity) == handled);
  assert(out.front() == 0x7f && out.back() == 0x7f);
  ++scenarios;
  if (!capacity || !handled) return "";
  const void* terminated = memchr(out.data() + 1, 0, capacity); assert(terminated);
  return out.data() + 1;
}
static std::string decode(const std::string& encoded) {
  static const std::string alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  unsigned bits = 0, held = 0; std::string output;
  for (char byte : encoded) {
    if (byte == '=') break;
    const size_t digit = alphabet.find(byte); assert(digit != std::string::npos);
    bits = (bits << 6) | unsigned(digit); held += 6;
    if (held >= 8) { held -= 8; output += char((bits >> held) & 255); }
  }
  return output;
}
static void checkIndex(const std::vector<uint8_t>& reply, uint8_t start, uint8_t count) {
  assert(reply.size() >= 10 && reply[0] == 10 && reply[1] == 0 && reply[2] == 0);
  assert(reply[8] == start && reply[9] >= start && reply[9] <= start + 2 && reply[7] == count);
  size_t cursor = 10;
  for (uint8_t i = start; i < reply[9]; ++i) {
    assert(cursor + 8 <= reply.size());
    assert(reply[cursor] == i + 1 && reply[cursor + 7] <= 63);
    const size_t title_length = reply[cursor + 7];
    assert(cursor + 8 + title_length <= reply.size()); cursor += 8 + title_length;
  }
  assert(cursor == reply.size());
}
int main() {
  MemoryFS fs;
  auto packet = request(fs, indexRequest()); checkIndex(packet, 0, 0);
  assert(mesh::room_board_detail::get32(packet.data() + 3) == 0 && packet[9] == 0);
  assert(command(fs, "get room.board") == "rev=0 total=0 start=0 next=0");
  assert(command(fs, "room.board.put 1 Instructions|Original body") == "OK rev=1");
  assert(command(fs, "room.board.put 2 Cafe |Unicode café 📡") == "OK rev=2");
  for (uint8_t id = 3; id <= 8; ++id) assert(save(fs, id, "Article " + std::to_string(id), std::string(2048, char('a' + id))) == Result::Success);
  packet = request(fs, indexRequest()); checkIndex(packet, 0, 8); assert(packet[9] == 2);
  assert(mesh::room_board_detail::get32(packet.data() + 3) == 8);
  for (uint8_t start = 0; start <= 8; start += 2) checkIndex(request(fs, indexRequest(8, start)), start, 8);
  packet = request(fs, indexRequest(0, 2)); assert(packet[2] == 5 && packet.size() == 10 && packet[9] == 2);
  packet = request(fs, indexRequest(7)); assert(packet[2] == 5 && packet.size() == 10);
  packet = request(fs, indexRequest(8, 9)); assert(packet[2] == 1 && packet.size() == 10);
  assert(command(fs, "get room.board 2").find("start=2 next=4 3@") != std::string::npos);

  // Every possible transport capacity checks canaries and complete entries.
  for (size_t capacity = 0; capacity <= 256; ++capacity) {
    packet = request(fs, indexRequest(), capacity);
    if (capacity < 3) assert(packet.empty());
    else if (capacity < 10) assert(packet.size() == 3 && packet[2] == 1);
    else if (packet[2] == 0) checkIndex(packet, 0, 8);
    else assert(packet.size() == 10 && packet[2] == 1 && packet[9] == 0);
    packet = request(fs, readRequest(3, 3), capacity);
    if (capacity < 3) assert(packet.empty());
    else if (capacity < 13) assert(packet.size() == 3 && packet[2] == 1);
    else if (capacity == 13) assert(packet.size() == 13 && packet[2] == 1 && packet[12] == 0);
    else {
      const size_t wanted = std::min(size_t(128), capacity - 13);
      assert(packet.size() == 13 + wanted && packet[2] == 0 && packet[12] == wanted);
      assert(std::all_of(packet.begin() + 13, packet.end(), [](uint8_t byte) { return byte == 'd'; }));
    }
  }
  for (size_t capacity = 0; capacity <= 160; ++capacity) {
    const std::string listed = command(fs, "get room.board", capacity);
    if (listed.find("rev=") == 0) assert(listed.find("next=") != std::string::npos);
    const std::string part = command(fs, "get room.board.read 3 3 0", capacity);
    if (part.find("id=") == 0) {
      unsigned id, version, offset, total, length;
      assert(sscanf(part.c_str(), "id=%u v=%u off=%u total=%u n=%u data=", &id, &version, &offset, &total, &length) == 5);
      const size_t data = part.find("data="); assert(data != std::string::npos);
      assert(id == 3 && version == 3 && offset == 0 && total == 2048 && length > 0 && length <= 80);
      assert(decode(part.substr(data + 5)) == std::string(length, 'd'));
    }
  }
  // Maximum titles still page by the emitted cursor, without dropping an item.
  MemoryFS long_titles;
  assert(save(long_titles, 1, std::string(63, 'x'), "a") == Result::Success);
  assert(save(long_titles, 2, std::string(63, 'y'), "b") == Result::Success);
  packet = request(long_titles, indexRequest(), 81); checkIndex(packet, 0, 2); assert(packet[9] == 1 && packet.size() == 81);
  checkIndex(request(long_titles, indexRequest(2, 1), 81), 1, 2);
  packet = request(long_titles, indexRequest(), 151); assert(packet[9] == 1);
  packet = request(long_titles, indexRequest(), 152); assert(packet[9] == 2 && packet.size() == 152);
  std::string listed = command(long_titles, "get room.board"); assert(listed.find("next=1") != std::string::npos);
  listed = command(long_titles, "get room.board 1"); assert(listed.find("next=2") != std::string::npos && listed.find(std::string(63, 'y')) != std::string::npos);

  // Both binary and CLI paging reconstruct a full 2-KiB article exactly.
  std::string assembled;
  for (uint16_t offset = 0; offset < 2048;) {
    packet = request(fs, readRequest(3, 3, offset), 103);
    assert(packet[2] == 0 && packet[3] == 3 && mesh::room_board_detail::get32(packet.data() + 4) == 3);
    assert(mesh::room_board_detail::get16(packet.data() + 8) == offset && mesh::room_board_detail::get16(packet.data() + 10) == 2048);
    assembled.append(reinterpret_cast<const char*>(packet.data() + 13), packet[12]); offset += packet[12];
  }
  assert(assembled == std::string(2048, 'd'));
  assembled.clear();
  for (size_t offset = 0; offset < 2048;) {
    const std::string part = command(fs, "get room.board.read 3 3 " + std::to_string(offset));
    const size_t data = part.find("data="); assert(data != std::string::npos);
    const std::string bytes = decode(part.substr(data + 5)); assert(!bytes.empty() && bytes.size() <= 80);
    assembled += bytes; offset += bytes.size();
  }
  assert(assembled == std::string(2048, 'd'));
  packet = request(fs, readRequest(3, 3, 2048), 13); assert(packet[2] == 0 && packet.size() == 13 && packet[12] == 0);
  packet = request(fs, readRequest(3, 3, 2049)); assert(packet[2] == 1 && packet.size() == 13);
  assert(command(fs, "get room.board.read 3 3 2048").find("n=0 data=") != std::string::npos);
  assert(command(fs, "get room.board.read 2 2 0").find("data=VW5pY29kZSBjYWbDqSDwn5Oh") != std::string::npos);

  // A mutation between index pages or article chunks cannot mix snapshots.
  assert(save(fs, 3, "Changed", "replacement") == Result::Success);
  packet = request(fs, indexRequest(8, 2)); assert(packet[2] == 5 && mesh::room_board_detail::get32(packet.data() + 3) == 9);
  packet = request(fs, readRequest(3, 3, 90)); assert(packet[2] == 5 && packet[12] == 0 && mesh::room_board_detail::get32(packet.data() + 4) == 9);
  assert(command(fs, "get room.board.read 3 3 90") == "Error stale version");
  assert(command(fs, "room.board.del 3 3") == "Error stale version");
  assert(command(fs, "room.board.del 3 9") == "OK rev=10");
  packet = request(fs, readRequest(3, 9)); assert(packet[2] == 4);
  assert(command(fs, "room.board.put 3 Reused|new") == "OK rev=11");
  packet = request(fs, readRequest(3, 9)); assert(packet[2] == 5);

  const auto malformed = readRequest(1, 1);
  for (size_t length = 0; length < malformed.size(); ++length) {
    packet = request(fs, std::vector<uint8_t>(malformed.begin(), malformed.begin() + length));
    assert(packet.size() == 3 && packet[2] == 1);
  }
  packet = request(fs, std::vector<uint8_t>{10, 2}); assert(packet[2] == 1);
  packet = request(fs, std::vector<uint8_t>{9, 0}); assert(packet[2] == 1);
  packet = request(fs, readRequest(0, 1)); assert(packet[2] == 1);
  packet = request(fs, readRequest(1, 0)); assert(packet[2] == 1);
  const unsigned writes = fs.write_opens;
  for (const std::string text : {"room.board.put", "room.board.put 0 Title|body", "room.board.put 9 Title|body", "room.board.put 1 No separator",
      "room.board.put 1 |body", "room.board.del 1", "room.board.del 1 0", "room.board.del 1 -1", "room.board.del 1 4294967296",
      "room.board.del 1 1 trailing", "get room.board -1", "get room.board 9", "get room.board.read 1 1",
      "get room.board.read 1 1 -1", "get room.board.read 1 1 2049", "get room.board.read 1 0 0", "get room.board.read 1 1 0 trailing"}) {
    assert(command(fs, text) == "Error invalid");
  }
  assert(command(fs, "room.board.put 1 " + std::string(64, 't') + "|body") == "Error invalid");
  assert(command(fs, "room.board.put 1 Title|" + std::string(513, 'b')) == "Error invalid");
  assert(command(fs, "room.board.put 1 " + std::string("\xed\xa0\x80") + "|body") == "Error invalid");
  assert(fs.write_opens == writes);
  command(fs, "unrelated", 160, false); command(fs, "get room.boardwalk", 160, false);
  command(fs, "room.board.putaway 1 Title|body", 160, false);

  // Fail-closed CRC checks apply before protocol reads and command writes.
  mesh::RoomBoardIndex index; metadata_filesystem = &fs; assert(mesh::loadRoomBoard(&fs, index));
  char path[24]; mesh::room_board_detail::articlePath(index.articles[0], path);
  auto damaged = fs.get(path); damaged.back() ^= 1; fs.put(path, damaged);
  const auto primary = fs.get(mesh::ROOM_BOARD_PRIMARY_PATH);
  packet = request(fs, indexRequest()); assert(packet[2] == 2 && packet.size() == 10);
  packet = request(fs, readRequest(1, 1)); assert(packet[2] == 2 && packet[12] == 0);
  assert(command(fs, "get room.board") == "Error unavailable");
  assert(command(fs, "get room.board.read 1 1 0") == "Error unavailable");
  assert(command(fs, "room.board.put 1 Changed|body") == "Error unavailable");
  assert(command(fs, "room.board.del 1 1") == "Error unavailable");
  assert(fs.get(mesh::ROOM_BOARD_PRIMARY_PATH) == primary && fs.get(path) == damaged && fs.write_opens == writes);
  printf("PASS: %u actual room board LoRa/CLI protocol, bounds and stale-snapshot scenarios\n", scenarios);
}
'''


class RoomBoardProtocolTest(unittest.TestCase):
    def test_real_protocol_and_store_all_backend_modes(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        filesystem = FILESYSTEM_HARNESS.split("#define FILE_O_WRITE 1")[0]
        filesystem = filesystem.replace("  void _lockFS() {}\n  void _unlockFS() {}\n  MemoryFS* _getFS() { return this; }\n", "", 1)
        filesystem = filesystem.replace("  void point(const std::string& event) {", """  int lock_depth = 0;
  void _lockFS() { assert(lock_depth++ == 0); }
  void _unlockFS() { assert(--lock_depth == 0); }
  MemoryFS* _getFS() { return this; }
  void point(const std::string& event) {""")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="meshcore-room-board-protocol-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "protocol.cpp"
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    source.write_text(filesystem + (LFS_MOCK if platform in ("NRF52_PLATFORM", "STM32_PLATFORM") else "") + TESTS)
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *sanitizers,
                                            *defines, "-I" + str(work), "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("actual room board LoRa/CLI protocol", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
