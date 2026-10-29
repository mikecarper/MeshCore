#include <helpers/bridges/ESPNowBridgeDiagnostics.h>
#include <Packet.h>

#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <type_traits>

namespace diag = mesh::bridge::diag;

static diag::Probe sentinel() {
  return {0x76543210U, 0, 6};
}

static void unchanged(const diag::Probe& value) {
  assert(value.nonce == 0x76543210U && value.format == 0 && value.channel == 6);
}

static void makeProbe(uint8_t* bytes) {
  assert(diag::encodeProbe(bytes, diag::ProbeSize, {0x01234567U, 1, 13}));
}

static void checksum(uint8_t* bytes) {
  mesh::espnow::writeEspNowRawUint32(
      bytes + 12, mesh::espnow::espNowRawCrc32(bytes, 12));
}

static void roundtrip() {
  const uint32_t nonces[] = {0, 1, 0x80000000U, UINT32_MAX, 0x01234567U};
  for (uint8_t format = 0; format <= 1; ++format) {
    for (uint8_t channel = 1; channel <= 13; ++channel) {
      for (uint32_t nonce : nonces) {
        uint8_t bytes[18];
        memset(bytes, 0xA5, sizeof(bytes));
        assert(diag::encodeProbe(bytes + 1, 16, {nonce, format, channel}));
        assert(bytes[0] == 0xA5 && bytes[17] == 0xA5);
        diag::Probe parsed = sentinel();
        assert(diag::isProbeCandidate(bytes + 1, 16));
        assert(diag::decodeProbe(bytes + 1, 16, parsed));
        assert(parsed.nonce == nonce && parsed.format == format
               && parsed.channel == channel);
      }
    }
  }
  uint8_t bytes[diag::ProbeSize];
  makeProbe(bytes);
  for (uint8_t byte : bytes) printf("%02X", byte);
  puts("");
}

static void bounds() {
  uint8_t bytes[250];
  memset(bytes, 0xA5, sizeof(bytes));
  makeProbe(bytes);
  for (size_t length = 0; length <= sizeof(bytes); ++length) {
    if (length == diag::ProbeSize) continue;
    diag::Probe parsed = sentinel();
    assert(!diag::decodeProbe(bytes, length, parsed));
    unchanged(parsed);
  }
  diag::Probe parsed = sentinel();
  assert(!diag::decodeProbe(nullptr, diag::ProbeSize, parsed));
  unchanged(parsed);
  assert(!diag::isProbeCandidate(nullptr, diag::ProbeSize));
  for (size_t length = 0; length < diag::ProbeSize; ++length) {
    memset(bytes, 0xA5, sizeof(bytes));
    assert(!diag::encodeProbe(bytes, length, {1, 1, 1}));
    for (uint8_t byte : bytes) assert(byte == 0xA5);
  }
  assert(!diag::encodeProbe(nullptr, diag::ProbeSize, {1, 1, 1}));
}

static void metadata() {
  const uint8_t invalid_formats[] = {2, 127, 255};
  const uint8_t invalid_channels[] = {0, 14, 127, 255};
  for (uint8_t format : invalid_formats) {
    uint8_t bytes[16];
    memset(bytes, 0xA5, sizeof(bytes));
    assert(!diag::encodeProbe(bytes, sizeof(bytes), {0, format, 6}));
    for (uint8_t byte : bytes) assert(byte == 0xA5);
    makeProbe(bytes);
    bytes[6] = format;
    checksum(bytes);
    diag::Probe parsed = sentinel();
    assert(!diag::decodeProbe(bytes, sizeof(bytes), parsed));
    unchanged(parsed);
  }
  for (uint8_t channel : invalid_channels) {
    uint8_t bytes[16];
    memset(bytes, 0xA5, sizeof(bytes));
    assert(!diag::encodeProbe(bytes, sizeof(bytes), {0, 0, channel}));
    for (uint8_t byte : bytes) assert(byte == 0xA5);
    makeProbe(bytes);
    bytes[7] = channel;
    checksum(bytes);
    diag::Probe parsed = sentinel();
    assert(!diag::decodeProbe(bytes, sizeof(bytes), parsed));
    unchanged(parsed);
  }
  for (size_t field : {size_t(4), size_t(5)}) {
    for (uint8_t value : {uint8_t(0), uint8_t(2), uint8_t(255)}) {
      uint8_t bytes[16];
      makeProbe(bytes);
      bytes[field] = value;
      checksum(bytes);
      diag::Probe parsed = sentinel();
      assert(diag::isProbeCandidate(bytes, sizeof(bytes)));
      assert(!diag::decodeProbe(bytes, sizeof(bytes), parsed));
      unchanged(parsed);
    }
  }
}

static void corruption() {
  uint8_t original[16];
  makeProbe(original);
  for (size_t byte = 0; byte < sizeof(original); ++byte) {
    for (uint8_t bit = 0; bit < 8; ++bit) {
      uint8_t bytes[16];
      memcpy(bytes, original, sizeof(bytes));
      bytes[byte] ^= uint8_t(1U << bit);
      diag::Probe parsed = sentinel();
      assert(!diag::decodeProbe(bytes, sizeof(bytes), parsed));
      unchanged(parsed);
    }
  }
}

static void candidate() {
  uint8_t bytes[250];
  memset(bytes, 0, sizeof(bytes));
  makeProbe(bytes);
  for (size_t length = 0; length < 4; ++length) {
    assert(!diag::isProbeCandidate(bytes, length));
  }
  for (size_t length = 4; length <= sizeof(bytes); ++length) {
    assert(diag::isProbeCandidate(bytes, length));
  }
  bytes[3] = 'F';
  assert(!diag::isProbeCandidate(bytes, 16));
  assert(memcmp(bytes, mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC, 4) == 0);
}

static void nonce() {
  struct Valid { const char* text; uint32_t expected; };
  for (const Valid& valid : {Valid{"00000000", 0}, Valid{"00000001", 1},
       Valid{"ffffffff", UINT32_MAX}, Valid{"ABCDef01", 0xABCDEF01U},
       Valid{"01234567", 0x01234567U}}) {
    uint32_t value = 7;
    assert(diag::parseNonce(valid.text, value) && value == valid.expected);
  }
  const char* invalid[] = {nullptr, "", "0", "1234567", "123456789",
      "0x123456", " 1234567", "1234567 ", "+1234567", "-1234567",
      "1234567g", "12345678x", "12345678\n", "1234 678", "\t1234567"};
  for (const char* text : invalid) {
    uint32_t value = 0x89ABCDEFU;
    assert(!diag::parseNonce(text, value) && value == 0x89ABCDEFU);
  }
}

static void legacy() {
  // These are actual production Packet and raw reassembler implementations.
  uint8_t probe[16];
  makeProbe(probe);
  mesh::Packet parsed;
  assert(!parsed.readFrom(probe, sizeof(probe)));
  assert((probe[0] >> PH_VER_SHIFT) > PAYLOAD_VER_1);
  assert((uint16_t(probe[0]) << 8 | probe[1]) != WRAPPED_BRIDGE_MAGIC);
  mesh::espnow::ESPNowRawReassemblerT<1> raw;
  uint8_t output[255];
  uint8_t source[] = {1, 2, 3, 4, 5, 6};
  size_t output_len = 999;
  assert(raw.acceptFrame(source, probe, sizeof(probe), 0, output,
                         sizeof(output), output_len)
         == mesh::espnow::ESPNowRawReassemblyResult::REJECTED);
  assert(output_len == 0);

  // Rejecting a probe must not disturb a real in-progress raw packet.
  mesh::Packet packet;
  packet.header = (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT)
      | ROUTE_TYPE_TRANSPORT_DIRECT;
  packet.setPathHashSizeAndCount(2, 32);
  packet.payload_len = MAX_PACKET_PAYLOAD;
  memset(packet.path, 0x23, sizeof(packet.path));
  memset(packet.payload, 0x42, sizeof(packet.payload));
  uint8_t wire[255];
  const size_t wire_len = packet.writeTo(wire);
  assert(wire_len == 254);
  mesh::espnow::ESPNowRawFrames frames;
  assert(mesh::espnow::encodeEspNowRawFrames(wire, wire_len, frames));
  assert(frames.count == 2);
  assert(raw.acceptFrame(source, frames.data[0], frames.lengths[0], 1,
                         output, sizeof(output), output_len)
         == mesh::espnow::ESPNowRawReassemblyResult::FRAGMENT_STORED);
  assert(raw.acceptFrame(source, probe, sizeof(probe), 2,
                         output, sizeof(output), output_len)
         == mesh::espnow::ESPNowRawReassemblyResult::REJECTED);
  assert(!diag::isProbeCandidate(frames.data[1], frames.lengths[1]));
  assert(raw.acceptFrame(source, frames.data[1], frames.lengths[1], 3,
                         output, sizeof(output), output_len)
         == mesh::espnow::ESPNowRawReassemblyResult::PACKET_COMPLETE);
  assert(output_len == wire_len && memcmp(output, wire, wire_len) == 0);
  assert(parsed.readFrom(output, static_cast<uint8_t>(output_len)));
}

static void counters() {
  static_assert(std::is_trivially_copyable<diag::Stats>::value,
                "Snapshots must copy without heap ownership");
  static_assert(sizeof(diag::Stats) <= 160, "Counter storage is bounded");
  diag::Stats stats;
  assert(stats.rx.frames == 0 && stats.tx.requested == 0
         && stats.probe.rx_ok == 0 && stats.error.stage == diag::Stage::None);
  uint32_t count = UINT32_MAX - 1;
  diag::increment(count);
  assert(count == UINT32_MAX);
  diag::increment(count);
  assert(count == UINT32_MAX);
  count = 0;
  diag::increment(count);
  assert(count == 1);
  for (uint8_t stage = 0; stage <= static_cast<uint8_t>(diag::Stage::WiFiPowerSave);
       ++stage) {
    assert(strcmp(diag::stageName(static_cast<diag::Stage>(stage)), "unknown") != 0);
  }
  assert(strcmp(diag::stageName(static_cast<diag::Stage>(255)), "unknown") == 0);
  printf("counter_bytes=%zu\n", sizeof(stats));
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "roundtrip")) roundtrip();
  else if (!strcmp(argv[1], "bounds")) bounds();
  else if (!strcmp(argv[1], "metadata")) metadata();
  else if (!strcmp(argv[1], "corruption")) corruption();
  else if (!strcmp(argv[1], "candidate")) candidate();
  else if (!strcmp(argv[1], "nonce")) nonce();
  else if (!strcmp(argv[1], "legacy")) legacy();
  else if (!strcmp(argv[1], "counters")) counters();
  else assert(false);
  return 0;
}
