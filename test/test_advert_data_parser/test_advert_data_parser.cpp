#include <gtest/gtest.h>

#include <cstdint>
#include <cstring>
#include <vector>

// The advert helper is also included by the existing contact-import suite;
// keep each native test executable on the same production implementation.
#include "../../src/helpers/AdvertDataHelpers.cpp"

TEST(AdvertDataParser, RejectsNullEmptyAndOversizedInputsBeforeReading) {
  EXPECT_FALSE(AdvertDataParser(nullptr, 0).isValid());
  EXPECT_FALSE(AdvertDataParser(nullptr, 1).isValid());
  const uint8_t byte = ADV_NAME_MASK;
  EXPECT_FALSE(AdvertDataParser(&byte, 0).isValid());
  EXPECT_FALSE(AdvertDataParser(&byte, MAX_ADVERT_DATA_SIZE + 1).isValid());
  EXPECT_FALSE(AdvertDataParser(&byte, UINT8_MAX).isValid());
}

TEST(AdvertDataParser, RejectsEveryTruncatedOptionalFieldCombination) {
  for (unsigned combination = 0; combination < 8; ++combination) {
    const bool location = (combination & 1) != 0;
    const bool feature1 = (combination & 2) != 0;
    const bool feature2 = (combination & 4) != 0;
    const size_t required = 1 + (location ? 8 : 0)
        + (feature1 ? 2 : 0) + (feature2 ? 2 : 0);
    const uint8_t flags = ADV_TYPE_REPEATER | (location ? ADV_LATLON_MASK : 0)
        | (feature1 ? ADV_FEAT1_MASK : 0) | (feature2 ? ADV_FEAT2_MASK : 0);
    for (size_t length = 0; length <= required; ++length) {
      // Allocate only the supplied length so sanitizers detect even one read
      // past a short field, rather than hiding it in a padded scratch buffer.
      std::vector<uint8_t> bytes(length, 0x5a);
      if (length) bytes[0] = flags;
      AdvertDataParser parser(bytes.data(), static_cast<uint8_t>(length));
      EXPECT_EQ(length == required, parser.isValid())
          << "flags=" << unsigned(flags) << " length=" << length;
      if (length < required) {
        EXPECT_EQ(0, parser.getIntLat());
        EXPECT_EQ(0, parser.getIntLon());
        EXPECT_EQ(0, parser.getFeat1());
        EXPECT_EQ(0, parser.getFeat2());
        EXPECT_FALSE(parser.hasName());
      }
    }
  }
}

TEST(AdvertDataParser, PreservesCompleteLocationFeaturesAndName) {
  const int32_t latitude = 47606200, longitude = -122332100;
  const uint16_t feature1 = 0x1234, feature2 = 0xabcd;
  const char name[] = "SEA Bellevue";
  std::vector<uint8_t> bytes(1 + 8 + 4 + sizeof(name) - 1);
  bytes[0] = ADV_TYPE_REPEATER | ADV_LATLON_MASK | ADV_FEAT1_MASK
      | ADV_FEAT2_MASK | ADV_NAME_MASK;
  size_t offset = 1;
  memcpy(bytes.data() + offset, &latitude, sizeof(latitude)); offset += sizeof(latitude);
  memcpy(bytes.data() + offset, &longitude, sizeof(longitude)); offset += sizeof(longitude);
  memcpy(bytes.data() + offset, &feature1, sizeof(feature1)); offset += sizeof(feature1);
  memcpy(bytes.data() + offset, &feature2, sizeof(feature2)); offset += sizeof(feature2);
  memcpy(bytes.data() + offset, name, sizeof(name) - 1);

  AdvertDataParser parser(bytes.data(), static_cast<uint8_t>(bytes.size()));
  ASSERT_TRUE(parser.isValid());
  EXPECT_EQ(ADV_TYPE_REPEATER, parser.getType());
  EXPECT_TRUE(parser.hasLatLon());
  EXPECT_EQ(latitude, parser.getIntLat());
  EXPECT_EQ(longitude, parser.getIntLon());
  EXPECT_EQ(feature1, parser.getFeat1());
  EXPECT_EQ(feature2, parser.getFeat2());
  EXPECT_STREQ(name, parser.getName());
}

TEST(AdvertDataParser, TerminatesMaximumNameWithinItsBuffer) {
  std::vector<uint8_t> bytes(MAX_ADVERT_DATA_SIZE, 'a');
  bytes[0] = ADV_TYPE_CHAT | ADV_NAME_MASK;
  AdvertDataParser parser(bytes.data(), static_cast<uint8_t>(bytes.size()));
  ASSERT_TRUE(parser.isValid());
  EXPECT_EQ(size_t(MAX_ADVERT_DATA_SIZE - 1), strlen(parser.getName()));
  EXPECT_EQ('\0', parser.getName()[MAX_ADVERT_DATA_SIZE - 1]);
}

TEST(AdvertDataParser, PreservesNamesProducedByTheBuilder) {
  const char name[] = "Bellevue \xe2\x98\x80 mesh with a long name";
  AdvertDataBuilder builder(ADV_TYPE_REPEATER, name, 47.0, -122.0);
  builder.setFeat1(0x1234);
  builder.setFeat2(0x5678);
  uint8_t bytes[MAX_ADVERT_DATA_SIZE];
  const uint8_t length = builder.encodeTo(bytes);
  AdvertDataParser parser(bytes, length);
  ASSERT_TRUE(parser.isValid());
  EXPECT_EQ(ADV_TYPE_REPEATER, parser.getType());
  EXPECT_EQ(47000000, parser.getIntLat());
  EXPECT_EQ(-122000000, parser.getIntLon());
  EXPECT_EQ(0x1234, parser.getFeat1());
  EXPECT_EQ(0x5678, parser.getFeat2());
  EXPECT_STREQ("Bellevue \xe2\x98\x80 mesh w", parser.getName());
}

TEST(AdvertDataParser, AllFlagAndLengthCombinationsStayWithinSuppliedBytes) {
  for (unsigned flags = 0; flags <= UINT8_MAX; ++flags) {
    const size_t required = 1 + ((flags & ADV_LATLON_MASK) ? 8 : 0)
        + ((flags & ADV_FEAT1_MASK) ? 2 : 0) + ((flags & ADV_FEAT2_MASK) ? 2 : 0);
    for (size_t length = 0; length <= MAX_ADVERT_DATA_SIZE; ++length) {
      std::vector<uint8_t> bytes(length, 'x');
      if (length) bytes[0] = static_cast<uint8_t>(flags);
      AdvertDataParser parser(bytes.data(), static_cast<uint8_t>(length));
      EXPECT_EQ(length >= required, parser.isValid())
          << "flags=" << flags << " length=" << length;
      if (parser.isValid()) {
        EXPECT_EQ(flags & 15, parser.getType());
        EXPECT_EQ((flags & ADV_NAME_MASK) ? length - required : 0,
                  strlen(parser.getName()));
      }
    }
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
