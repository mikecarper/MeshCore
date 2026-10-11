#!/usr/bin/env python3
"""Execute owner-bound room mail LoRa/CLI handlers against their real store."""

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
#include <helpers/RoomMailProtocol.h>
using Result = mesh::RoomMailResult;
static unsigned scenarios = 0;
static uint8_t a[32], b[32], c[32];
static std::string key(const uint8_t* bytes) {
  char out[65]; mesh::room_mail_protocol_detail::hex(bytes, out); return out;
}
static std::map<std::string, std::vector<uint8_t>> snapshot(const MemoryFS& fs) {
  std::map<std::string, std::vector<uint8_t>> out;
  for (const auto& entry : fs.files) out[entry.first] = *entry.second;
  return out;
}
static std::vector<uint8_t> request(MemoryFS& fs, const uint8_t* owner,
    const std::vector<uint8_t>& input, size_t capacity = 160,
    bool can_send = true, uint64_t nonce = 1) {
  metadata_filesystem = &fs;
  std::vector<uint8_t> output(capacity + 2, 0x7f);
  const size_t used = mesh::handleRoomMailRequest(&fs, owner, can_send, nonce,
      input.data(), input.size(), output.data() + 1, capacity, 123456);
  assert(used <= capacity && output.front() == 0x7f && output.back() == 0x7f);
  ++scenarios; return std::vector<uint8_t>(output.begin() + 1, output.begin() + 1 + used);
}
static std::string command(MemoryFS& fs, const uint8_t* owner,
    const std::string& text, size_t capacity = 160, bool can_send = true,
    uint64_t nonce = 1, bool handled = true) {
  metadata_filesystem = &fs;
  std::vector<char> output(capacity + 2, 0x7f);
  assert(mesh::handleRoomMailCommand(&fs, owner, can_send, nonce, text.c_str(),
      output.data() + 1, capacity, 123456) == handled);
  assert(output.front() == 0x7f && output.back() == 0x7f); ++scenarios;
  if (!capacity || !handled) return "";
  assert(memchr(output.data() + 1, 0, capacity)); return output.data() + 1;
}
static mesh::RoomMailStatus status(MemoryFS& fs, const uint8_t* owner) {
  metadata_filesystem = &fs; mesh::RoomMailStatus out;
  assert(mesh::getRoomMailStatus(&fs, owner, out) == Result::Success); return out;
}
static std::vector<uint8_t> edit(uint8_t op, uint32_t revision, uint8_t value) {
  std::vector<uint8_t> out(7); out[0] = 11; out[1] = op;
  mesh::room_mail_protocol_detail::put32(out.data() + 2, revision); out[6] = value; return out;
}
static std::vector<uint8_t> editKey(uint8_t op, uint32_t revision, const uint8_t* bytes) {
  std::vector<uint8_t> out(38); out[0] = 11; out[1] = op;
  mesh::room_mail_protocol_detail::put32(out.data() + 2, revision);
  memcpy(out.data() + 6, bytes, 32); return out;
}
static std::vector<uint8_t> indexRequest(uint32_t revision = 0, uint8_t cursor = 0) {
  std::vector<uint8_t> out(7); out[0] = 11; out[1] = 1;
  mesh::room_mail_protocol_detail::put32(out.data() + 2, revision); out[6] = cursor; return out;
}
static std::vector<uint8_t> settings(uint32_t revision = 0, uint8_t cursor = 0) {
  auto out = indexRequest(revision, cursor); out[1] = 0; return out;
}
static std::vector<uint8_t> read(uint32_t id, uint16_t offset = 0) {
  std::vector<uint8_t> out(8); out[0] = 11; out[1] = 2;
  mesh::room_mail_protocol_detail::put32(out.data() + 2, id);
  mesh::room_mail_protocol_detail::put16(out.data() + 6, offset); return out;
}
static std::vector<uint8_t> send(const uint8_t* recipient, const std::string& body) {
  std::vector<uint8_t> out(36 + body.size()); out[0] = 11; out[1] = 3;
  memcpy(out.data() + 2, recipient, 32);
  mesh::room_mail_protocol_detail::put16(out.data() + 34, uint16_t(body.size()));
  memcpy(out.data() + 36, body.data(), body.size()); return out;
}
static std::vector<uint8_t> remove(uint8_t op, uint32_t id) {
  std::vector<uint8_t> out(6); out[0] = 11; out[1] = op;
  mesh::room_mail_protocol_detail::put32(out.data() + 2, id); return out;
}
static std::vector<uint8_t> padded(std::vector<uint8_t> payload) {
  payload.resize(((payload.size() + 4 + 15) / 16) * 16 - 4, 0); return payload;
}
static uint32_t id(const std::vector<uint8_t>& response) {
  assert(response.size() == 7 && (response[2] == 0 || response[2] == 8));
  return mesh::room_mail_protocol_detail::get32(response.data() + 3);
}
int main() {
  memset(a, 0xa1, 32); memset(b, 0xb2, 32); memset(c, 0xc3, 32);
  MemoryFS fs;
  auto response = request(fs, a, settings());
  assert(response.size() == 13 && response[2] == 0 && response[7] == 0
      && response[8] == 0 && response[9] == 0 && response[11] == 255 && response[12] == 0);
  assert(fs.files.empty());
  assert(request(fs, a, indexRequest())[9] == 255 && fs.files.empty());
  assert(command(fs, a, "mail settings").find("mode=closed delivery=chat queued=0") != std::string::npos);
  assert(command(fs, a, "mail mode public", 160, false) == "OK");
  assert(command(fs, a, "mail delivery mailbox", 160, false) == "OK");
  auto own = status(fs, a); assert(own.settings.mailbox_only);
  response = request(fs, a, editKey(6, own.revision, b)); assert(response[2] == 0);
  own = status(fs, a); assert(own.settings.allowed_count == 1);
  const uint32_t before_policy = own.revision;
  response = request(fs, a, edit(8, own.revision, 2), 7, false); assert(response[2] == 0);
  response = request(fs, a, edit(9, before_policy, 0)); assert(response[2] == 5);
  own = status(fs, a); assert(own.settings.mode == mesh::RoomMailMode::Private && own.settings.mailbox_only);
  response = request(fs, a, settings(), 45);
  assert(response.size() == 45 && response[9] == 1 && response[11] == 255
      && memcmp(response.data() + 13, b, 32) == 0);
  assert(request(fs, b, settings())[9] == 0); // Another key cannot enumerate the allow list.
  assert(request(fs, c, send(a, "not authorized"))[2] == 6);
  assert(request(fs, b, send(a, "writer permission required"), 160, false)[2] == 6);
  assert(command(fs, b, "mail send " + key(a) + " blocked by role", 160, false) == "Error permission denied");
  assert(status(fs, a).count == 0);

  const std::string full_body(512, 'p'); const uint64_t nonce = UINT64_C(0x1234567800000009);
  response = request(fs, b, send(a, full_body), 160, true, nonce); const uint32_t first = id(response);
  own = status(fs, a); assert(own.count == 1);
  response = request(fs, b, send(a, full_body), 160, true, nonce);
  assert(response[2] == 8 && id(response) == first && status(fs, a).count == 1);
  assert(request(fs, b, send(a, "changed"), 160, true, nonce)[2] == 9);
  assert(request(fs, b, send(a, "different nonce high bits"), 160, true, nonce + (UINT64_C(1) << 32))[2] == 0);
  assert(status(fs, a).count == 2);

  const auto preserved = snapshot(fs);
  for (const uint8_t* stranger : {b, c}) {
    for (const auto& payload : {read(first), remove(4, first), remove(5, first)}) {
      response = request(fs, stranger, payload);
      assert(response[2] == 4 || response[2] == 6);
      assert(response.size() <= 7);
    }
    const std::string output = command(fs, stranger, "mail read " + std::to_string(first));
    assert(output == "Error not found" || output == "Error permission denied");
  }
  assert(snapshot(fs) == preserved && status(fs, a).count == 2);
  own = status(fs, a);
  response = request(fs, a, indexRequest());
  assert(response[2] == 0 && response[7] == 2 && response[9] == 255 && response[10] == 2);
  assert(response.size() == 87 && memcmp(response.data() + 15, b, 32) == 0);
  response = request(fs, a, indexRequest(own.revision, 1), 49);
  assert(response.size() == 49 && response[9] == 255 && response[10] == 1);
  assert(request(fs, a, indexRequest(own.revision - 1))[2] == 5);
  assert(request(fs, a, indexRequest(0, 1))[2] == 5);
  assert(request(fs, a, indexRequest(own.revision, 3))[2] == 1);

  // Check every route reply capacity, including capacities too short for a
  // complete private header or entry. Response canaries prohibit overflows.
  for (size_t capacity = 0; capacity <= 200; ++capacity) {
    response = request(fs, a, indexRequest(), capacity);
    if (capacity < 3) assert(response.empty());
    else if (capacity < 49) assert(response.size() == 3 && response[2] == 1);
    else {
      assert(response[2] == 0 && response.size() == 11 + size_t(response[10]) * 38);
      assert(response[10] >= 1 && response[10] <= 2);
    }
    response = request(fs, a, read(first), capacity);
    if (capacity < 3) assert(response.empty());
    else if (capacity <= 44) assert(response.size() == 3 && response[2] == 1);
    else {
      assert(response[2] == 0 && response.size() == 44 + size_t(response[11]));
      assert(response[11] == std::min(size_t(128), capacity - 44));
      assert(memcmp(response.data() + 12, b, 32) == 0);
    }
    response = request(fs, a, settings(), capacity);
    if (capacity < 3) assert(response.empty());
    else if (capacity < 45) assert(response.size() == 3 && response[2] == 1);
    else assert(response.size() == 45 && response[2] == 0);
  }
  std::string assembled;
  for (uint16_t offset = 0; offset < 512;) {
    response = request(fs, a, read(first, offset), 90);
    assert(response[2] == 0 && response[11] && response[11] <= 46);
    assert(mesh::room_mail_protocol_detail::get16(response.data() + 7) == offset);
    assert(mesh::room_mail_protocol_detail::get16(response.data() + 9) == 512);
    assembled.append(reinterpret_cast<const char*>(response.data() + 44), response[11]); offset += response[11];
  }
  assert(assembled == full_body);
  response = request(fs, a, read(first, 512), 44); assert(response.size() == 44 && response[11] == 0);
  assert(request(fs, a, read(first, 513))[2] == 1);
  assembled.clear();
  for (size_t offset = 0; offset < 512;) {
    const std::string output = command(fs, a, "mail read " + std::to_string(first) + " " + std::to_string(offset), 157);
    const size_t data = output.find("text="); assert(data != std::string::npos);
    const std::string bytes = output.substr(data + 5); assert(!bytes.empty());
    assembled += bytes; offset += bytes.size();
  }
  assert(assembled == full_body);
  assert(command(fs, a, "mail read " + std::to_string(first) + " 512").find("next=512 total=512 text=") != std::string::npos);
  assert(command(fs, a, "mail inbox") == command(fs, a, "mail list"));
  assert(command(fs, a, "mail list", 80).find("..") != std::string::npos);
  {
    MemoryFS utf_fs;
    assert(command(utf_fs, a, "mail mode public") == "OK");
    std::string unicode;
    for (size_t i = 0; i < 128; ++i) unicode += "\xf0\x9f\x93\xa1";
    const uint32_t unicode_id = id(request(utf_fs, b, send(a, unicode), 160, true, 300));
    std::string text;
    for (size_t offset = 0; offset < unicode.size();) {
      const std::string output = command(utf_fs, a, "mail read " + std::to_string(unicode_id) + " " + std::to_string(offset), 83);
      const size_t marker = output.find("text="); assert(marker != std::string::npos);
      const std::string part = output.substr(marker + 5); assert(!part.empty() && part.size() % 4 == 0);
      assert(mesh::room_mail_detail::validBody(part.data(), part.size()));
      unsigned long got_id, got_offset, next; unsigned total;
      assert(sscanf(output.c_str(), "id=%lu off=%lu next=%lu total=%u text=", &got_id, &got_offset, &next, &total) == 4);
      assert(got_id == unicode_id && got_offset == offset && next == offset + part.size() && total == unicode.size());
      text += part; offset = next;
    }
    assert(text == unicode);
    for (unsigned offset = 1; offset <= 3; ++offset)
      assert(command(utf_fs, a, "mail read " + std::to_string(unicode_id) + " " + std::to_string(offset)) == "Error offset inside UTF-8 character");
    assert(request(utf_fs, a, read(unicode_id, 1))[2] == 0); // Binary reads retain arbitrary byte offsets.
  }
  for (size_t capacity = 0; capacity <= 160; ++capacity) {
    command(fs, a, "mail settings", capacity);
    command(fs, a, "mail list", capacity);
    command(fs, a, "mail read " + std::to_string(first), capacity);
  }

  // No mutation is admitted if its complete confirmation cannot be returned.
  for (size_t capacity = 0; capacity < 7; ++capacity) {
    const auto before = snapshot(fs);
    for (const auto& payload : {send(a, "capacity"), remove(4, first), remove(5, first),
        edit(8, own.revision, 1), edit(9, own.revision, 0), editKey(6, own.revision, c), editKey(7, own.revision, b)})
      request(fs, a, payload, capacity, true, 500);
    assert(snapshot(fs) == before);
  }
  for (size_t capacity = 0; capacity < 32; ++capacity) {
    const auto before = snapshot(fs);
    command(fs, a, "mail mode public", capacity);
    command(fs, a, "mail ack " + std::to_string(first), capacity);
    command(fs, b, "mail send " + key(a) + " short confirmation", capacity, true, 501);
    assert(snapshot(fs) == before);
  }
  response = request(fs, a, remove(4, first)); assert(response[2] == 0);
  assert(status(fs, a).count == 1);
  response = request(fs, b, send(a, full_body), 160, true, nonce);
  assert(response[2] == 8 && id(response) == first && status(fs, a).count == 1);
  assert(request(fs, a, remove(4, first))[2] == 0);
  assert(request(fs, a, remove(5, first))[2] == 0);
  assert(request(fs, a, read(first))[2] == 4);
  {
    MemoryFS air;
    assert(request(air, a, padded(settings()))[2] == 0 && air.files.empty());
    assert(request(air, a, padded(edit(8, 0, 2)))[2] == 0);
    assert(request(air, a, padded(editKey(6, status(air, a).revision, b)))[2] == 0);
    assert(request(air, a, padded(edit(9, status(air, a).revision, 1)))[2] == 0);
    const std::string dog = "dog lost: please call home";
    response = request(air, b, padded(send(a, dog)), 160, true, 700);
    const uint32_t dog_id = id(response);
    assert(request(air, a, padded(indexRequest()))[7] == 1);
    response = request(air, a, padded(read(dog_id)));
    assert(response[11] == dog.size() && std::string(reinterpret_cast<const char*>(response.data() + 44), response[11]) == dog);
    assert(status(air, a).count == 1); // Reading on a later wake leaves queued mail intact.
    assert(request(air, a, padded(remove(4, dog_id)))[2] == 0 && status(air, a).count == 0);
    assert(request(air, a, padded(remove(5, dog_id)))[2] == 0);
    assert(request(air, a, padded(editKey(7, status(air, a).revision, b)))[2] == 0);
    const auto before = snapshot(air);
    for (auto input : {settings(), indexRequest(), read(dog_id), send(a, "changed padding"),
        remove(4, dog_id), remove(5, dog_id), edit(8, status(air, a).revision, 1),
        edit(9, status(air, a).revision, 0), editKey(6, status(air, a).revision, b), editKey(7, status(air, a).revision, b)}) {
      input.push_back(1); assert(request(air, a, input, 160, true, 701)[2] == 1);
    }
    assert(snapshot(air) == before);
  }

  assert(command(fs, a, "mail deny " + key(b)) == "OK");
  assert(request(fs, b, send(a, "denied"), 160, true, 999)[2] == 6);
  assert(command(fs, a, "mail mode public") == "OK");
  response = request(fs, c, send(a, "public mode"), 160, true, 1000); const uint32_t public_id = id(response);
  assert(command(fs, a, "mail delete " + std::to_string(public_id)).find("OK id=") == 0);
  assert(command(fs, a, "mail delivery chat") == "OK" && !status(fs, a).settings.mailbox_only);
  for (uint8_t value = 1; value <= 8; ++value) {
    uint8_t allowed[32]; memset(allowed, value, 32);
    assert(command(fs, a, "mail allow " + key(allowed)) == "OK");
  }
  assert(command(fs, a, "mail allow " + key(b)) == "Error mailbox full");
  assert(command(fs, a, "mail settings").find("next=1") != std::string::npos);
  assert(command(fs, a, "mail settings 7").find("next=8") != std::string::npos);
  assert(request(fs, a, settings(status(fs, a).revision, 7), 45)[11] == 255);
  assert(command(fs, a, "mail check").find("allowed=8") != std::string::npos);
  assert(request(fs, c, send(a, std::string("bad\0body", 8)), 160, true, 1010)[2] == 1);
  assert(request(fs, c, send(a, std::string("\xed\xa0\x80", 3)), 160, true, 1011)[2] == 1);
  assert(request(fs, c, send(a, "cafe\xc3\xa9"), 160, true, 1012)[2] == 0);
  assert(request(fs, c, send(a, "third queued"), 160, true, 1013)[2] == 0);
  assert(request(fs, c, send(a, "fourth queued"), 160, true, 1014)[2] == 0);
  assert(status(fs, a).count == 4);
  assert(request(fs, c, send(a, "over quota"), 160, true, 1015)[2] == 7);
  response = request(fs, a, indexRequest());
  assert(response[7] == 4 && response[9] == 2 && response[10] == 2);
  response = request(fs, a, indexRequest(status(fs, a).revision, 2));
  assert(response[7] == 4 && response[9] == 255 && response[10] == 2);

  for (const std::string& text : std::vector<std::string>{"mail", "mail bogus", "mail mode", "mail mode administrator",
      "mail mode public trailing", "mail delivery radio", "mail allow " + key(b).substr(0, 63),
      "mail deny " + std::string(64, '0'), "mail allow " + std::string(64, 'z'),
      "mail allow " + key(b) + " trailing", "mail send " + key(a),
      "mail send " + key(a) + " " + std::string(513, 'x'), "mail read 0", "mail read -1",
      "mail read 4294967296", "mail read 1 513", "mail read 1 0 trailing", "mail ack 0",
      "mail delete 1 trailing", "mail settings 9", "mail list 17"}) {
    const auto before = snapshot(fs);
    assert(command(fs, a, text).find("Error ") == 0); assert(snapshot(fs) == before);
  }
  command(fs, a, "mailbox settings", 160, true, 1, false);
  command(fs, a, "unrelated", 160, true, 1, false);
  const auto before_invalid = snapshot(fs);
  for (const std::vector<uint8_t>& payload : std::vector<std::vector<uint8_t>>{
      {}, {11}, {11, 255}, {11, 0}, {11, 0, 9}, {11, 0, 0, 1}, {11, 1}, {11, 2},
      {11, 3}, {11, 4}, {11, 5}, edit(8, status(fs, a).revision, 3), edit(9, status(fs, a).revision, 2),
      std::vector<uint8_t>(38, 0)}) {
    response = request(fs, a, payload); assert(response.empty() || response[2] == 1);
  }
  assert(snapshot(fs) == before_invalid);
  uint8_t zero[32] = {};
  assert(request(fs, zero, settings())[2] == 1);
  assert(command(fs, zero, "mail settings") == "Error invalid");
  printf("PASS: %u owner-bound room mail LoRa/CLI protocol, capacity and durable retry scenarios\n", scenarios);
}
'''


class RoomMailProtocolTests(unittest.TestCase):
    def test_actual_protocol_and_store_all_backend_modes(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        candidates = [Path(os.environ["MESHCORE_CRYPTO_DIR"])] if os.environ.get("MESHCORE_CRYPTO_DIR") else []
        candidates += sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if (path / "SHA256.cpp").is_file()), None)
        if crypto is None:
            raise RuntimeError("Install rweather/Crypto or set MESHCORE_CRYPTO_DIR (no hash mock fallback)")
        filesystem = FILESYSTEM_HARNESS.split("#define FILE_O_WRITE 1")[0]
        filesystem = filesystem.replace("  File() = default;", """#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File() = delete;
#else
  File() = default;
#endif
  explicit File(MemoryFS* owner): fs(owner) {}""")
        filesystem = filesystem.replace("return {};", "return File(this);")
        filesystem = filesystem.replace(
            "  void _lockFS() {}\n  void _unlockFS() {}\n  MemoryFS* _getFS() { return this; }\n", "", 1
        )
        filesystem = filesystem.replace("  void point(const std::string& event) {", """  int lock_depth = 0;
  void _lockFS() { assert(lock_depth++ == 0); }
  void _unlockFS() { assert(--lock_depth == 0); }
  MemoryFS* _getFS() { return this; }
  void point(const std::string& event) {""")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="meshcore-room-mail-protocol-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "protocol.cpp"
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    source.write_text(filesystem + (LFS_MOCK if platform in ("NRF52_PLATFORM", "STM32_PLATFORM") else "") + TESTS)
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run(
                        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-DHOST_BUILD", *sanitizers,
                         *defines, "-I" + str(work), "-I" + str(ROOT / "src"), "-I" + str(crypto), str(source),
                         str(crypto / "SHA256.cpp"), str(crypto / "Hash.cpp"), str(crypto / "Crypto.cpp"), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("owner-bound room mail LoRa/CLI protocol", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
