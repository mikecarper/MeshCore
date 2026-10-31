#include <gtest/gtest.h>

#include <cstring>

#include "helpers/Radio2DiscoverTrailer.h"

// The DISCOVER_RESP radio2 trailer (docs/radio_profiles.md). Golden bytes: the
// MediaMesh preset, 911.150 MHz, 250 kHz, SF7, CR 4/5, rxtx (flags 0x05).
static const uint8_t kMediaMesh[] = {1, 0x05, 0x2E, 0xE7, 0x0D, 0x00, 0x90, 0xD0, 0x03, 0x00, 7, 5};

TEST(Radio2DiscoverTrailer, ParsesTheMediaMeshPreset) {
  Radio2DiscoverTrailer t = Radio2DiscoverTrailer::parse(kMediaMesh, sizeof(kMediaMesh));
  EXPECT_EQ(Radio2DiscoverTrailer::On, t.state);
  EXPECT_EQ(2, t.mode);
  EXPECT_STREQ("rxtx", t.modeName());
  EXPECT_FALSE(t.temporary);
  EXPECT_EQ(911150U, t.freq_khz);
  EXPECT_EQ(250000U, t.bwHz());
  EXPECT_EQ(7, t.sf);
  EXPECT_EQ(5, t.cr);
  char radio[48];
  t.formatRadio(radio, sizeof(radio));
  EXPECT_STREQ("911.150000,250.0,7,5", radio);
}

TEST(Radio2DiscoverTrailer, ListenOnlyTemporaryAndFractionalBandwidth) {
  uint8_t b[12];
  memcpy(b, kMediaMesh, sizeof(b));
  b[1] = 0x01 | (1 << 1) | 0x08;   // configured, rx, temporary
  b[6] = 0x24; b[7] = 0xF4; b[8] = 0x00; b[9] = 0x00;   // 62500 = 62.5 kHz
  Radio2DiscoverTrailer t = Radio2DiscoverTrailer::parse(b, sizeof(b));
  EXPECT_EQ(Radio2DiscoverTrailer::On, t.state);
  EXPECT_STREQ("rx", t.modeName());
  EXPECT_TRUE(t.temporary);
  char radio[48];
  t.formatRadio(radio, sizeof(radio));
  EXPECT_STREQ("911.150000,62.5,7,5", radio);
}

TEST(Radio2DiscoverTrailer, OffIsOff) {
  const uint8_t off[] = {1, 0};
  Radio2DiscoverTrailer t = Radio2DiscoverTrailer::parse(off, sizeof(off));
  EXPECT_EQ(Radio2DiscoverTrailer::Off, t.state);
  EXPECT_STREQ("off", t.modeName());
}

TEST(Radio2DiscoverTrailer, MissingOrBrokenIsUnknownNotOff) {
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::parse(nullptr, 0).state);
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::parse(kMediaMesh, 0).state);
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::parse(kMediaMesh, 1).state);
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::parse(kMediaMesh, 11).state);
  const uint8_t v0[] = {0, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 7, 5};
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::parse(v0, sizeof(v0)).state);
}

TEST(Radio2DiscoverTrailer, ConfiguredWithModeOffIsOff) {
  uint8_t b[12];
  memcpy(b, kMediaMesh, sizeof(b));
  b[1] = 0x01;   // configured, mode 0
  EXPECT_EQ(Radio2DiscoverTrailer::Off, Radio2DiscoverTrailer::parse(b, sizeof(b)).state);
}

TEST(Radio2DiscoverTrailer, LaterVersionsMayAppend) {
  uint8_t b[16] = {};
  memcpy(b, kMediaMesh, sizeof(kMediaMesh));
  b[0] = 2;
  Radio2DiscoverTrailer t = Radio2DiscoverTrailer::parse(b, sizeof(b));
  EXPECT_EQ(Radio2DiscoverTrailer::On, t.state);
  EXPECT_EQ(911150U, t.freq_khz);
}

TEST(Radio2DiscoverTrailer, FitsInEightBytes) {
  // One per cached neighbour and per discovery result on DRAM-tight builds.
  EXPECT_EQ(8u, sizeof(Radio2DiscoverTrailer));
}

TEST(Radio2DiscoverTrailer, UnknownIsZero) {
  Radio2DiscoverTrailer zero;
  memset(&zero, 0, sizeof(zero));
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, zero.state);   // memset neighbour tables start unknown
  EXPECT_EQ(Radio2DiscoverTrailer::Unknown, Radio2DiscoverTrailer::unknown().state);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
