#include <gtest/gtest.h>

#include <MeshCore.h>
#include <Packet.h>
#include <helpers/ESPNowRawFragmentation.h>

#include <array>
#include <cstring>

namespace {

using mesh::espnow::ESPNowRawFrames;
using mesh::espnow::ESPNowRawReassemblerT;
using mesh::espnow::ESPNowRawReassemblyResult;

static std::array<uint8_t, MAX_TRANS_UNIT> makePacket(uint8_t salt = 0) {
  std::array<uint8_t, MAX_TRANS_UNIT> packet{};
  // Version zero is the only currently valid serialized MeshCore version.
  packet[0] = static_cast<uint8_t>(0x03U | ((salt & 0x0FU) << 2));
  for (size_t i = 1; i < packet.size(); ++i) {
    packet[i] = static_cast<uint8_t>((i * 37U + salt * 19U) & 0xFFU);
  }
  return packet;
}

static const uint8_t MAC_A[mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE] =
    {0x02, 0x11, 0x22, 0x33, 0x44, 0x55};
static const uint8_t MAC_B[mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE] =
    {0x02, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE};

struct ValidWirePacket {
  std::array<uint8_t, MAX_TRANS_UNIT> bytes{};
  size_t length = 0;
};

static ValidWirePacket makeValidWirePacket(size_t target_length) {
  mesh::Packet packet;
  packet.payload_len = MAX_PACKET_PAYLOAD;
  for (size_t i = 0; i < packet.payload_len; ++i) {
    packet.payload[i] = static_cast<uint8_t>((i * 3U + 7U) & 0xFFU);
  }

  if (target_length == 250) {
    packet.header = static_cast<uint8_t>(
        (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD);
    packet.setPathHashSizeAndCount(2, 32);  // 64 path bytes
  } else {
    packet.header = static_cast<uint8_t>(
        (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT)
        | ROUTE_TYPE_TRANSPORT_FLOOD);
    packet.transport_codes[0] = 0x1122;
    packet.transport_codes[1] = 0x3344;
    const size_t path_bytes = target_length - 1U - 4U - 1U
        - MAX_PACKET_PAYLOAD;
    if (path_bytes == 64) {
      packet.setPathHashSizeAndCount(2, 32);
    } else {
      packet.setPathHashSizeAndCount(1,
                                    static_cast<uint8_t>(path_bytes));
    }
  }
  for (size_t i = 0; i < packet.getPathByteLen(); ++i) {
    packet.path[i] = static_cast<uint8_t>((0x80U + i) & 0xFFU);
  }

  ValidWirePacket wire;
  wire.length = packet.writeTo(wire.bytes.data());
  return wire;
}

TEST(ESPNowRawFragmentation, ConstantsMatchMeshCoreAndReserveInvalidVersion) {
  EXPECT_EQ(static_cast<size_t>(MAX_TRANS_UNIT),
            mesh::espnow::ESPNOW_RAW_MAX_PACKET_SIZE);
  EXPECT_EQ(250U, mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE);
  EXPECT_EQ(3U, mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC[0] >> 6);
  EXPECT_LE(mesh::espnow::ESPNOW_RAW_FRAGMENT_HEADER_SIZE
                + mesh::espnow::ESPNOW_RAW_FRAGMENT_DATA_SIZE,
            mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE);
}

TEST(ESPNowRawFragmentation, Crc32UsesTheStandardIEEEVector) {
  static const uint8_t input[] = {'1', '2', '3', '4', '5',
                                  '6', '7', '8', '9'};
  EXPECT_EQ(0xCBF43926UL,
            mesh::espnow::espNowRawCrc32(input, sizeof(input)));
  EXPECT_EQ(0U, mesh::espnow::espNowRawCrc32(nullptr, 0));
}

TEST(ESPNowRawFragmentation, OrdinaryRawFramesRemainExactlyOneUnchangedFrame) {
  const auto packet = makePacket();
  const size_t lengths[] = {2, 249, 250};

  for (size_t len : lengths) {
    ESPNowRawFrames frames;
    ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), len,
                                                    frames));
    ASSERT_EQ(1U, frames.count) << "len=" << len;
    EXPECT_EQ(len, frames.lengths[0]);
    EXPECT_EQ(0, memcmp(packet.data(), frames.data[0], len));
    EXPECT_EQ(0U, frames.lengths[1]);
  }
}

TEST(ESPNowRawFragmentation, RejectsNullEmptyAndOverLimitInput) {
  auto packet = makePacket();
  ESPNowRawFrames frames;

  EXPECT_FALSE(mesh::espnow::encodeEspNowRawFrames(nullptr, 2, frames));
  EXPECT_EQ(0U, frames.count);
  EXPECT_FALSE(mesh::espnow::encodeEspNowRawFrames(packet.data(), 0, frames));
  EXPECT_EQ(0U, frames.count);
  EXPECT_FALSE(mesh::espnow::encodeEspNowRawFrames(
      packet.data(), mesh::espnow::ESPNOW_RAW_MAX_PACKET_SIZE + 1, frames));
  EXPECT_EQ(0U, frames.count);
}

TEST(ESPNowRawFragmentation, SplitsOnlyOversizePacketsIntoTwoBoundedFrames) {
  const auto packet = makePacket();
  const size_t lengths[] = {251, 252, 253, 254, 255};

  for (size_t len : lengths) {
    ESPNowRawFrames frames;
    ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), len,
                                                    frames));
    ASSERT_EQ(2U, frames.count) << "len=" << len;
    EXPECT_EQ(mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE, frames.lengths[0]);
    EXPECT_LE(frames.lengths[1],
              mesh::espnow::ESPNOW_RAW_MAX_FRAME_SIZE);
    EXPECT_EQ(0, memcmp(frames.data[0],
                        mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC,
                        mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC_SIZE));
    EXPECT_EQ(0, memcmp(frames.data[1],
                        mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC,
                        mesh::espnow::ESPNOW_RAW_FRAGMENT_MAGIC_SIZE));
    EXPECT_EQ(len, frames.data[0][7]);
    EXPECT_EQ(len, frames.data[1][7]);
    EXPECT_EQ(0U, frames.data[0][6]);
    EXPECT_EQ(1U, frames.data[1][6]);
    EXPECT_EQ(0U, frames.data[0][8]);
    EXPECT_EQ(mesh::espnow::ESPNOW_RAW_FRAGMENT_DATA_SIZE,
              frames.data[1][8]);
  }
}

TEST(ESPNowRawFragmentation, ValidMeshCorePacketsRoundTripAcrossBoundary) {
  const size_t lengths[] = {250, 251, 254};
  for (size_t expected_length : lengths) {
    const ValidWirePacket wire = makeValidWirePacket(expected_length);
    ASSERT_EQ(expected_length, wire.length);

    mesh::Packet input_check;
    ASSERT_TRUE(input_check.readFrom(
        wire.bytes.data(), static_cast<uint8_t>(wire.length)));

    ESPNowRawFrames frames;
    ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(
        wire.bytes.data(), wire.length, frames));
    ASSERT_EQ(expected_length <= 250 ? 1U : 2U, frames.count);

    ESPNowRawReassemblerT<2> reassembler;
    uint8_t output[MAX_TRANS_UNIT] = {};
    size_t output_len = 0;
    ESPNowRawReassemblyResult result =
        ESPNowRawReassemblyResult::REJECTED;
    for (uint8_t i = 0; i < frames.count; ++i) {
      result = reassembler.acceptFrame(
          MAC_A, frames.data[i], frames.lengths[i], 100U + i,
          output, sizeof(output), output_len);
    }
    EXPECT_EQ(expected_length <= 250
                  ? ESPNowRawReassemblyResult::PASSTHROUGH
                  : ESPNowRawReassemblyResult::PACKET_COMPLETE,
              result);
    ASSERT_EQ(wire.length, output_len);
    EXPECT_EQ(0, memcmp(wire.bytes.data(), output, output_len));

    mesh::Packet decoded;
    EXPECT_TRUE(decoded.readFrom(output, static_cast<uint8_t>(output_len)));
    EXPECT_EQ(MAX_PACKET_PAYLOAD, decoded.payload_len);
  }
}

TEST(ESPNowRawFragmentation, FragmentEnvelopeHasStableGoldenWireOrder) {
  const ValidWirePacket wire = makeValidWirePacket(251);
  ASSERT_EQ(251U, wire.length);
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(
      wire.bytes.data(), wire.length, frames));
  ASSERT_EQ(2U, frames.count);

  // Header fields and CRC32 are network byte order. These bytes intentionally
  // do not derive their expected checksum from the implementation under test.
  static const uint8_t expected_first_header[] = {
      0xFE, 0x4D, 0x43, 0x46, 0x01, 0x02, 0x00,
      0xFB, 0x00, 0x51, 0x24, 0x1C, 0xDC};
  static const uint8_t expected_second_header[] = {
      0xFE, 0x4D, 0x43, 0x46, 0x01, 0x02, 0x01,
      0xFB, 0xED, 0x51, 0x24, 0x1C, 0xDC};
  EXPECT_EQ(0, memcmp(expected_first_header, frames.data[0],
                      sizeof(expected_first_header)));
  EXPECT_EQ(0, memcmp(expected_second_header, frames.data[1],
                      sizeof(expected_second_header)));
}

TEST(ESPNowRawFragmentation, PassesOrdinaryFramesThroughExactly) {
  const auto packet = makePacket();
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 99;
  ESPNowRawReassemblerT<2> reassembler;

  EXPECT_EQ(ESPNowRawReassemblyResult::PASSTHROUGH,
            reassembler.acceptFrame(MAC_A, packet.data(), 250, 10,
                                    output, sizeof(output), output_len));
  EXPECT_EQ(250U, output_len);
  EXPECT_EQ(0, memcmp(packet.data(), output, output_len));
}

TEST(ESPNowRawFragmentation, ReassemblesEveryOversizeLengthInOrder) {
  const auto packet = makePacket();
  for (size_t len = 251; len <= MAX_TRANS_UNIT; ++len) {
    ESPNowRawFrames frames;
    ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), len,
                                                    frames));
    ESPNowRawReassemblerT<2> reassembler;
    uint8_t output[MAX_TRANS_UNIT] = {};
    size_t output_len = 0;

    EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
              reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                      100, output, sizeof(output), output_len));
    EXPECT_EQ(0U, output_len);
    EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
              reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                      101, output, sizeof(output), output_len));
    ASSERT_EQ(len, output_len);
    EXPECT_EQ(0, memcmp(packet.data(), output, len));
  }
}

TEST(ESPNowRawFragmentation, ReassemblesOutOfOrder) {
  const auto packet = makePacket();
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  ESPNowRawReassemblerT<2> reassembler;
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;

  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    1, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    2, output, sizeof(output), output_len));
  ASSERT_EQ(packet.size(), output_len);
  EXPECT_EQ(0, memcmp(packet.data(), output, output_len));
}

TEST(ESPNowRawFragmentation, KeysConcurrentAssembliesBySourceMac) {
  const auto packet = makePacket();
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  ESPNowRawReassemblerT<2> reassembler;
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;

  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    10, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_B, frames.data[0], frames.lengths[0],
                                    11, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    12, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
            reassembler.acceptFrame(MAC_B, frames.data[1], frames.lengths[1],
                                    13, output, sizeof(output), output_len));
}

TEST(ESPNowRawFragmentation, HandlesIdenticalAndConflictingDuplicates) {
  const auto packet = makePacket();
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  ESPNowRawReassemblerT<2> reassembler;
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;

  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    1, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::DUPLICATE,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    2, output, sizeof(output), output_len));

  ESPNowRawFrames conflicting = frames;
  conflicting.data[0][mesh::espnow::ESPNOW_RAW_FRAGMENT_HEADER_SIZE + 3] ^= 1;
  EXPECT_EQ(ESPNowRawReassemblyResult::REJECTED,
            reassembler.acceptFrame(MAC_A, conflicting.data[0],
                                    conflicting.lengths[0], 3, output,
                                    sizeof(output), output_len));

  EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    4, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::DUPLICATE,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    5, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::DUPLICATE,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    6, output, sizeof(output), output_len));
}

TEST(ESPNowRawFragmentation, RejectsCorruptPayloadAtWholePacketIntegrityCheck) {
  const auto packet = makePacket();
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  frames.data[1][mesh::espnow::ESPNOW_RAW_FRAGMENT_HEADER_SIZE] ^= 0x80;

  ESPNowRawReassemblerT<2> reassembler;
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    1, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::REJECTED,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    2, output, sizeof(output), output_len));
  EXPECT_EQ(0U, output_len);
}

TEST(ESPNowRawFragmentation, RejectsMalformedReservedEnvelopes) {
  const auto packet = makePacket();
  ESPNowRawFrames original;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  original));
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;

  auto expectRejected = [&](ESPNowRawFrames frames, size_t frame_len) {
    ESPNowRawReassemblerT<1> reassembler;
    EXPECT_EQ(ESPNowRawReassemblyResult::REJECTED,
              reassembler.acceptFrame(MAC_A, frames.data[0], frame_len, 1,
                                      output, sizeof(output), output_len));
    EXPECT_EQ(0U, output_len);
  };

  ESPNowRawFrames bad = original;
  bad.data[0][1] ^= 1;
  expectRejected(bad, bad.lengths[0]);
  bad = original;
  bad.data[0][4]++;
  expectRejected(bad, bad.lengths[0]);
  bad = original;
  bad.data[0][5] = 3;
  expectRejected(bad, bad.lengths[0]);
  bad = original;
  bad.data[0][6] = 2;
  expectRejected(bad, bad.lengths[0]);
  bad = original;
  bad.data[0][7] = 250;
  expectRejected(bad, bad.lengths[0]);
  bad = original;
  bad.data[0][8] = 1;
  expectRejected(bad, bad.lengths[0]);
  expectRejected(original,
                 mesh::espnow::ESPNOW_RAW_FRAGMENT_HEADER_SIZE - 1);
  expectRejected(original, original.lengths[0] - 1);
}

TEST(ESPNowRawFragmentation, RequiresAdequateOutputWithoutOverflowingIt) {
  const auto packet = makePacket();
  ESPNowRawReassemblerT<1> reassembler;
  uint8_t output[254] = {};
  size_t output_len = 77;

  EXPECT_EQ(ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL,
            reassembler.acceptFrame(MAC_A, packet.data(), 250, 1,
                                    output, 249, output_len));
  EXPECT_EQ(0U, output_len);

  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    2, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::OUTPUT_TOO_SMALL,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    3, output, sizeof(output), output_len));
  EXPECT_EQ(0U, output_len);
}

TEST(ESPNowRawFragmentation, ExpiresAssembliesAcrossMillisRollover) {
  const auto packet = makePacket();
  ESPNowRawFrames frames;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(packet.data(), packet.size(),
                                                  frames));
  ESPNowRawReassemblerT<1> reassembler(20);
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;

  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[0], frames.lengths[0],
                                    0xFFFFFFF0UL, output, sizeof(output),
                                    output_len));
  // 0xFFFFFFF0 -> 0x00000005 is 21 ms, so fragment zero has expired.
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames.data[1], frames.lengths[1],
                                    0x00000005UL, output, sizeof(output),
                                    output_len));
  EXPECT_EQ(0U, output_len);
}

TEST(ESPNowRawFragmentation, BoundedSlotsEvictOldestIncompleteAssembly) {
  const auto packet_a = makePacket(1);
  const auto packet_b = makePacket(2);
  const auto packet_c = makePacket(3);
  ESPNowRawFrames frames_a;
  ESPNowRawFrames frames_b;
  ESPNowRawFrames frames_c;
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(
      packet_a.data(), packet_a.size(), frames_a));
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(
      packet_b.data(), packet_b.size(), frames_b));
  ASSERT_TRUE(mesh::espnow::encodeEspNowRawFrames(
      packet_c.data(), packet_c.size(), frames_c));

  ESPNowRawReassemblerT<2> reassembler(1000);
  uint8_t output[MAX_TRANS_UNIT] = {};
  size_t output_len = 0;
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames_a.data[0], frames_a.lengths[0],
                                    10, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames_b.data[0], frames_b.lengths[0],
                                    20, output, sizeof(output), output_len));
  EXPECT_EQ(ESPNowRawReassemblyResult::FRAGMENT_STORED,
            reassembler.acceptFrame(MAC_A, frames_c.data[0], frames_c.lengths[0],
                                    30, output, sizeof(output), output_len));

  // A was oldest and was evicted; B remains and can complete.
  EXPECT_EQ(ESPNowRawReassemblyResult::PACKET_COMPLETE,
            reassembler.acceptFrame(MAC_A, frames_b.data[1], frames_b.lengths[1],
                                    40, output, sizeof(output), output_len));
  ASSERT_EQ(packet_b.size(), output_len);
  EXPECT_EQ(0, memcmp(packet_b.data(), output, output_len));
}

}  // namespace

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
