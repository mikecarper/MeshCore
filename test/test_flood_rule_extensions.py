"""Run the production extended-rule parser, matching, and wire-field helpers."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UTILS = r'''#pragma once
#include <stddef.h>
#include <stdint.h>
namespace mesh { struct Utils {
  static int MACThenDecrypt(const uint8_t*, uint8_t*, const uint8_t*, int);
}; }
'''
PROGRAM = r'''
#include <algorithm>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <Utils.h>
#include <helpers/FloodRuleExtensions.h>
using namespace FloodRuleExtensions;
mesh::Packet::Packet() : header(0), payload_len(0), path_len(0), transport_codes{}, path{}, payload{},
    _rssi(0), _snr(0), tx_cr(0), flood_retry_policy(0) {}
static std::vector<uint8_t> plaintext;
static unsigned decrypts;
static bool decrypt_ok = true;
int mesh::Utils::MACThenDecrypt(const uint8_t* secret, uint8_t* out, const uint8_t*, int) {
  ++decrypts;
  if (!decrypt_ok || secret[0] != 0x42) return 0;
  assert(plaintext.size() <= MAX_PACKET_PAYLOAD);
  memcpy(out, plaintext.data(), plaintext.size());
  return int(plaintext.size());
}
struct StringWriter {
  std::string text;
  void append(const char* format, ...) {
    char buffer[512];
    va_list args; va_start(args, format);
    int n = vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    assert(n >= 0 && n < int(sizeof(buffer)));
    text.append(buffer, size_t(n));
  }
};
static Fields parse(const char* command) {
  char buffer[512], reply[160] = {};
  assert(strlen(command) < sizeof(buffer));
  strcpy(buffer, command);
  char* tokens[24];
  int count = tokenize(buffer, tokens, 24);
  assert(count >= 0);
  Fields fields{}; uint8_t seen = 0;
  for (int i = 0; i < count; ++i) assert(parseToken(tokens[i], fields, seen, reply) == 1);
  assert(validFields(fields));
  return fields;
}
static void parsing() {
  Fields normal = parse(R"(sender="^(Alice|Bob) [0-9]+$" text='[Ww]eather: rain?' len=0-60 route=any prob=25 enabled=off)");
  Fields compact = parse(R"(n="^(Alice|Bob) [0-9]+$" x='[Ww]eather: rain?' l=0-60 rt=a pr=25 e=0)");
  assert(sameFields(normal, compact));
  assert(normal.payload_set && normal.min_payload == 0 && normal.max_payload == 60);
  assert(normal.route == AnyRoute && normal.probability == 25 && normal.disabled);
  assert(contentRequested(normal) && hasFields(normal));
  const Fields folded = parse(R"(n="(?i)^(Jeffery Epstein|Ghislaine Maxwell)$" x="(?i)^weather: [A-Z]+$")");
  assert(!strcmp(folded.sender, "(?i)^(Jeffery Epstein|Ghislaine Maxwell)$"));
  Fields empty{};
  assert(validFields(empty) && !hasFields(empty) && !contentRequested(empty));
  assert(sameFields(parse("prob=100"), empty));
  Fields terminator = empty;
  memset(terminator.sender, 'a', sizeof(terminator.sender));
  assert(!validFields(terminator));
  terminator = empty; memset(terminator.text, 'a', sizeof(terminator.text));
  assert(!validFields(terminator));
  for (const char* token : {"sender=", "text=", "sender=[", "text=a**", "sender=Alice(?i)", "text=(?i)(?i)rain",
       "len=", "len=-1", "len=3-2",
       "len=185", "len=184-185", "len=1+2", "len=1+-2", "len=1-2+", "len=1--2", "len=99999999999999999999",
       "route=bad", "prob=0", "prob=101", "prob=-1", "prob=50%", "enabled=yes"}) {
    Fields row{}; uint8_t seen = 0; char reply[160] = {};
    assert(parseToken(token, row, seen, reply) == -1 && strncmp(reply, "Err -", 5) == 0);
  }
  const std::string oversized = "sender=" + std::string(64, 'a');
  uint8_t seen = 0; char reply[160] = {}; Fields row{};
  assert(parseToken(oversized.c_str(), row, seen, reply) == -1);
  const std::string foldedMaximum = "sender=(?i)" + std::string(59, 'a');
  seen = 0;
  assert(parseToken(foldedMaximum.c_str(), row, seen, reply) == 1);
  assert(strlen(row.sender) == 63);
  seen = 0;
  assert(parseToken((foldedMaximum + "a").c_str(), row, seen, reply) == -1);
  seen = 0;
  assert(parseToken("sender=Alice", row, seen, reply) == 1);
  assert(parseToken("n=Bob", row, seen, reply) == -1 && !strcmp(row.sender, "Alice"));
  assert(parseToken("mode=radio", row, seen, reply) == 0);
  assert(parseToken("drop", row, seen, reply) == 0);
  char malformed[] = "text=\"unterminated"; char* tokens[2];
  assert(tokenize(malformed, tokens, 2) == -1);
  char excess[] = "a b c"; assert(tokenize(excess, tokens, 2) == -1);
  char whitespace[] = "\t  "; assert(tokenize(whitespace, tokens, 2) == 0);
  for (const char* syntax : {"len=0", "l=0-0"}) {
    const auto zero = parse(syntax);
    mesh::Packet packet; packet.header = ROUTE_TYPE_FLOOD;
    assert(matchesEnvelope(zero, &packet, 0));
    packet.payload_len = 1; assert(!matchesEnvelope(zero, &packet, 0));
  }
  for (bool abbreviated : {false, true}) {
    for (const char* syntax : {R"(text='has "quote"')", R"(text='has \"quote\"')",
                               R"(text='\\server\ folder')", R"(text="it\'s both")"}) {
      Fields source = parse(syntax);
      StringWriter writer; appendFields(writer, source, abbreviated);
      Fields restored = parse(writer.text.c_str());
      for (const char* sample : {"has \"quote\"", "has quote", "\\server folder", "it's both", "unrelated"}) {
        using mesh::flood::matchTextPattern;
        const auto* bytes = reinterpret_cast<const uint8_t*>(sample);
        assert(matchTextPattern(source.text, strlen(source.text), bytes, strlen(sample))
            == matchTextPattern(restored.text, strlen(restored.text), bytes, strlen(sample)));
      }
    }
    StringWriter writer; appendFields(writer, normal, abbreviated);
    assert(sameFields(normal, parse(writer.text.c_str())));
    StringWriter foldedWriter; appendFields(foldedWriter, folded, abbreviated);
    assert(sameFields(folded, parse(foldedWriter.text.c_str())));
  }
}
static void envelopesAndProbability() {
  Fields row{};
  assert(!matchesEnvelope(row, nullptr, 0));
  for (uint8_t route = 0; route < 4; ++route) {
    mesh::Packet packet; packet.header = route; packet.payload_len = 44;
    const bool flood = packet.isRouteFlood();
    assert(matchesEnvelope(row, &packet, 0) == flood);
    assert(matchesEnvelope(row, &packet, 1));
    row.route = FloodRoute; assert(matchesEnvelope(row, &packet, 0) == flood);
    row.route = DirectRoute; assert(matchesEnvelope(row, &packet, 0) == !flood);
    row.route = AnyRoute; assert(matchesEnvelope(row, &packet, 0));
    row.payload_set = 1; row.min_payload = 44; row.max_payload = 44;
    assert(matchesEnvelope(row, &packet, 0));
    packet.payload_len = 43; assert(!matchesEnvelope(row, &packet, 0));
    packet.payload_len = 45; assert(!matchesEnvelope(row, &packet, 0));
    packet.payload_len = 44; row.disabled = 1; assert(!matchesEnvelope(row, &packet, 0));
    row = {};
  }
  for (unsigned probability = 0; probability <= 100; ++probability) {
    row.probability = uint8_t(probability);
    unsigned selected = 0;
    for (uint32_t roll = 0; roll < 10000; ++roll) selected += matchesProbability(row, roll);
    assert(selected == (probability ? probability * 100 : 10000));
  }
}
static void paths() {
  mesh::Packet packet; packet.header = ROUTE_TYPE_FLOOD;
  packet.setPathHashSizeAndCount(1, 5);
  const uint8_t bytes[] = {0x11, 0x22, 0x33, 0x44, 0x55}; memcpy(packet.path, bytes, sizeof(bytes));
  for (const char* syntax : {"22>33", "22,33"}) {
    Fields row{}; uint8_t width, hops, first[9];
    assert(parsePath(syntax, width, hops, first, row, false));
    assert(width == 1 && hops == 2 && row.path_position == Anywhere);
    assert(pathMatches(row, &packet, width, hops, first));
    row.path_position = Prefix; assert(!pathMatches(row, &packet, width, hops, first));
    row.path_position = Suffix; assert(!pathMatches(row, &packet, width, hops, first));
  }
  for (const char* syntax : {"^11>22", "44>55$", "^11>22>33>44$"}) {
    Fields row{}; uint8_t width, hops, first[9];
    assert(parsePath(syntax, width, hops, first, row, false));
    assert(pathMatches(row, &packet, width, hops, first) == (row.path_position != Exact));
  }
  for (unsigned width = 1; width <= 3; ++width) {
    Fields row{}; uint8_t parsedWidth, hops, first[9];
    StringWriter wanted;
    for (unsigned hop = 0; hop < 4; ++hop) {
      if (hop) wanted.append(">");
      for (unsigned byte = 0; byte < width; ++byte) wanted.append("%02X", 0x11 + hop * width + byte);
    }
    assert(parsePath(wanted.text.c_str(), parsedWidth, hops, first, row, false));
    assert(parsedWidth == width && hops == 4);
    packet.setPathHashSizeAndCount(uint8_t(width), 6);
    memset(packet.path, 0x99, sizeof(packet.path));
    memcpy(packet.path + width, first, std::min(9U, 4 * width));
    if (width == 3) memcpy(packet.path + width + 9, row.path_tail, 3);
    assert(pathMatches(row, &packet, parsedWidth, hops, first));
    for (bool compact : {false, true}) {
      StringWriter writer; appendPath(writer, row, parsedWidth, hops, first, compact);
      const char* syntax = strchr(writer.text.c_str(), '=') + 1;
      Fields copy{}; uint8_t copiedWidth, copiedHops, copiedFirst[9];
      assert(parsePath(syntax, copiedWidth, copiedHops, copiedFirst, copy, false));
      assert(copiedWidth == parsedWidth && copiedHops == hops && sameFields(row, copy));
      assert(!memcmp(first, copiedFirst, 9));
    }
    packet.header = ROUTE_TYPE_DIRECT; assert(!pathMatches(row, &packet, parsedWidth, hops, first));
    packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);
    assert(!pathMatches(row, &packet, parsedWidth, hops, first));
    packet.header = ROUTE_TYPE_FLOOD;
    packet.setPathHashSizeAndCount(uint8_t(width), 63);
    if (width > 1) assert(!pathMatches(row, &packet, parsedWidth, hops, first));
  }
  for (const char* invalid : {"", "^", "$", "^$", "AA>", ">AA", "AA>>BB", "GG", "A", "AAAAA", "AA>BBBB", "AA>BB>CC>DD>EE"}) {
    Fields row{}; uint8_t width, hops, first[9];
    assert(!parsePath(invalid, width, hops, first, row, false));
  }
  Fields row{}; uint8_t width, hops, first[9];
  assert(parsePath("*", width, hops, first, row, false) && !width && !hops);
  assert(pathMatches(row, &packet, width, hops, first));
}
static void content() {
  uint8_t key[32] = {0x42};
  mesh::Packet packet; packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT); packet.payload_len = 35;
  Fields row = parse(R"(sender=^Alice$ text='weather (sun|rain)')");
  const auto setPlain = [](const std::string& text) {
    plaintext.assign(5, 0); plaintext.insert(plaintext.end(), text.begin(), text.end()); plaintext.push_back(0);
  };
  setPlain("Alice: weather rain");
  assert(matchesContent(row, &packet, key, 16));
  assert(matchesContent(row, &packet, key, 32));
  uint32_t work = 1000;
  assert(matchesContent(row, &packet, key, 16, &work));
  assert(work > 0 && work < 1000);
  const uint32_t afterFirst = work;
  assert(matchesContent(row, &packet, key, 16, &work) && work < afterFirst);
  work = 0;
  assert(!matchesContent(row, &packet, key, 16, &work, false));
  assert(matchesContent(row, &packet, key, 16, &work, true));
  bool exhausted = false;
  assert(!matchesContent(row, &packet, key, 16, &work, false, &exhausted) && exhausted);
  // An allow rule cannot become an unconditional exemption when work runs
  // out. A deny rule may conservatively suppress uncertain authenticated data.
  Fields textOnly = row; textOnly.sender[0] = 0;
  assert(!matchesContent(textOnly, &packet, key, 16, &work, false));
  assert(matchesContent(textOnly, &packet, key, 16, &work, true));
  exhausted = false;
  assert(!matchesContent(textOnly, &packet, key, 16, &work, false, &exhausted) && exhausted);
  work = 1000;
  assert(matchesContent(textOnly, &packet, key, 16, &work, false, &exhausted) && !exhausted);
  assert(!matchesContent(row, &packet, key, 1));
  assert(!matchesContent(row, &packet, nullptr, 16));
  assert(!matchesContent(row, static_cast<const mesh::Packet*>(nullptr), key, 16));
  decrypt_ok = false;
  assert(!matchesContent(row, &packet, key, 16));
  assert(!matchesContent(row, &packet, key, 16, &work, true));
  exhausted = true;
  assert(!matchesContent(row, &packet, key, 16, &work, false, &exhausted) && !exhausted);
  decrypt_ok = true;
  setPlain("alice: weather rain"); assert(!matchesContent(row, &packet, key, 16));
  const Fields folded = parse(R"(n="(?i)^(Jeffery Epstein|Ghislaine Maxwell)$" x="(?i)^weather (sun|rain)$")");
  setPlain("jEfFeRy ePsTeIn: WEATHER RAIN"); assert(matchesContent(folded, &packet, key, 16));
  setPlain("GHISLAINE MAXWELL: weather Sun"); assert(matchesContent(folded, &packet, key, 16));
  setPlain("Jeffery Epstein Jr: weather sun"); assert(!matchesContent(folded, &packet, key, 16));
  Fields senderOnlyFold = folded; strcpy(senderOnlyFold.text, "^weather (sun|rain)$");
  setPlain("GHISLAINE MAXWELL: WEATHER RAIN"); assert(!matchesContent(senderOnlyFold, &packet, key, 16));
  setPlain("Alice: dry"); assert(!matchesContent(row, &packet, key, 16));
  setPlain("weather rain"); assert(!matchesContent(row, &packet, key, 16));
  row.sender[0] = 0; assert(matchesContent(row, &packet, key, 16));
  setPlain(std::string(140, 'x') + "Alice: weather sun");
  strcpy(row.sender, "Alice$"); assert(matchesContent(row, &packet, key, 16));
  plaintext[4] = TXT_TYPE_CLI_COMMAND << 2; assert(!matchesContent(row, &packet, key, 16));
  packet.header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT);
  assert(!matchesContent(row, &packet, key, 16));
  row = {}; decrypts = 0;
  assert(matchesContent(row, static_cast<const mesh::Packet*>(nullptr), nullptr, 0) && decrypts == 0);
}
struct ByteReader {
  std::vector<uint8_t> bytes; size_t position = 0;
  size_t read(uint8_t* out, size_t size) {
    size = std::min(size, bytes.size() - position);
    if (size) memcpy(out, bytes.data() + position, size);
    position += size; return size;
  }
};
static void storage() {
  Fields source = parse(R"(sender=Alice text='rain storm' len=0-80 route=direct prob=33 enabled=off)");
  source.path_position = Suffix; source.path_tail[2] = 0x99;
  std::vector<uint8_t> bytes;
  auto writer = [&bytes](const void* data, size_t size) {
    const auto* first = static_cast<const uint8_t*>(data); bytes.insert(bytes.end(), first, first + size); return true;
  };
  assert(writeFields(writer, source));
  const std::vector<uint8_t> expected = {0, 0, 80, 0, DirectRoute, 33, Suffix, 1, 1, 0, 0, 0x99,
      5, 'A', 'l', 'i', 'c', 'e', 10, 'r', 'a', 'i', 'n', ' ', 's', 't', 'o', 'r', 'm'};
  assert(bytes == expected);
  ByteReader full{bytes}; Fields loaded{};
  assert(readFields(full, loaded) && full.position == bytes.size() && sameFields(source, loaded));
  for (size_t length = 0; length < bytes.size(); ++length) {
    ByteReader shortRead{{bytes.begin(), bytes.begin() + length}};
    assert(!readFields(shortRead, loaded));
  }
  const size_t senderLength = 12; // Four uint16 bytes, five control bytes, three tail bytes.
  assert(bytes[senderLength] == strlen(source.sender));
  for (const auto& change : std::vector<std::pair<size_t, uint8_t>>{{4, 4}, {5, 101}, {6, 4}, {7, 2}, {8, 2}, {senderLength, 64}, {senderLength + 1, '*'}}) {
    ByteReader corrupt{bytes}; corrupt.bytes[change.first] = change.second;
    assert(!readFields(corrupt, loaded));
  }
  unsigned successfulCalls = 0;
  auto counter = [&successfulCalls](const void*, size_t) { ++successfulCalls; return true; };
  assert(writeFields(counter, source));
  for (unsigned failure = 0; failure < successfulCalls; ++failure) {
    unsigned calls = 0;
    auto broken = [&calls, failure](const void*, size_t) { return calls++ != failure; };
    assert(!writeFields(broken, source));
  }
  // Case folding is stored in the existing pattern bytes, with no new field
  // or format. Both matching and CLI output must survive a reload.
  source = parse(R"(n="(?i)^(Jeffery Epstein|Ghislaine Maxwell)$" x="(?i)^hello$")");
  bytes.clear();
  assert(writeFields(writer, source));
  ByteReader foldedRead{bytes};
  assert(readFields(foldedRead, loaded) && sameFields(source, loaded));
  StringWriter restored; appendFields(restored, loaded, true);
  assert(sameFields(source, parse(restored.text.c_str())));
  const char* sample = "gHiSlAiNe mAxWeLl";
  assert(mesh::flood::matchTextPattern(loaded.sender, strlen(loaded.sender),
      reinterpret_cast<const uint8_t*>(sample), strlen(sample)) == mesh::flood::PatternMatchResult::Match);
}
int main() { parsing(); envelopesAndProbability(); paths(); content(); storage(); }
'''


class FloodRuleExtensionsTests(unittest.TestCase):
    def test_real_parser_matching_and_storage(self):
        with tempfile.TemporaryDirectory(prefix="flood-rule-extensions-") as folder:
            folder = Path(folder)
            (folder / "Utils.h").write_text(UTILS)
            source = folder / "extensions.cpp"
            source.write_text(PROGRAM)
            binary = folder / "extensions"
            flags = []
            if os.name != "nt":
                flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", *flags,
                            "-I" + str(folder), "-I" + str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
