#include <gtest/gtest.h>

#include <helpers/ui/EmojiFontMap.h>

#include <stdint.h>
#include <string.h>
#include <vector>

using mesh::ui::EmojiFontMap;

namespace {

void append16(std::vector<uint8_t>& data, uint16_t value) {
  data.push_back((uint8_t)value);
  data.push_back((uint8_t)(value >> 8));
}

void append32(std::vector<uint8_t>& data, uint32_t value) {
  data.push_back((uint8_t)value);
  data.push_back((uint8_t)(value >> 8));
  data.push_back((uint8_t)(value >> 16));
  data.push_back((uint8_t)(value >> 24));
}

uint32_t crc32(const uint8_t* data, size_t size) {
  uint32_t crc = 0xFFFFFFFFUL;
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
    }
  }
  return ~crc;
}

std::vector<uint8_t> sampleFont() {
  std::vector<uint8_t> font(32, 0x55);
  const uint32_t map_offset = font.size();
  std::vector<uint8_t> map;
  map.insert(map.end(), {'E', 'M', '0', '1'});
  append16(map, 6);  // nodes
  append16(map, 5);  // edges
  append32(map, 0);

  // Root: F0 -> 1
  append16(map, 0); append16(map, 1); append16(map, 0);
  // F0: 9F -> 2
  append16(map, 1); append16(map, 1); append16(map, 0);
  // F0 9F: 98 -> 3
  append16(map, 2); append16(map, 1); append16(map, 0);
  // F0 9F 98: 80 -> 4
  append16(map, 3); append16(map, 1); append16(map, 0);
  // Grinning face, also prefix of the test sequence below.
  append16(map, 4); append16(map, 1); append16(map, 0xE000);
  // Grinning face followed by '!'.
  append16(map, 5); append16(map, 0); append16(map, 0xE001);

  map.push_back(0xF0); append16(map, 1);
  map.push_back(0x9F); append16(map, 2);
  map.push_back(0x98); append16(map, 3);
  map.push_back(0x80); append16(map, 4);
  map.push_back('!'); append16(map, 5);

  font.insert(font.end(), map.begin(), map.end());
  font.insert(font.end(), {'M', 'C', 'E', 'M', 'A', 'P', '1', 0});
  append32(font, map_offset);
  append32(font, map.size());
  append32(font, crc32(map.data(), map.size()));
  append32(font, 0);
  return font;
}

std::vector<uint8_t> sampleVersion2Font() {
  std::vector<uint8_t> font = sampleFont();
  font.resize(font.size() - 24);
  const uint32_t map_offset = 32;
  const uint32_t map_size = font.size() - map_offset;
  const uint32_t map_crc = crc32(font.data() + map_offset, map_size);
  font.insert(font.end(), {'M', 'C', 'E', 'M', 'A', 'P', '2', 0});
  append32(font, map_offset);
  append32(font, map_size);
  append32(font, map_crc);
  for (int i = 0; i < 5; ++i) append32(font, 0);
  return font;
}

}  // namespace

TEST(EmojiFontMap, UsesLongestSequenceMatch) {
  std::vector<uint8_t> font = sampleFont();
  EmojiFontMap map;
  ASSERT_TRUE(map.begin(font.data(), font.size()));

  const uint8_t text[] = {0xF0, 0x9F, 0x98, 0x80, '!', 'x'};
  uint16_t output = 0;
  EXPECT_EQ(5U, map.longestMatch(text, sizeof(text), output));
  EXPECT_EQ(0xE001, output);

  EXPECT_EQ(4U, map.longestMatch(text, 4, output));
  EXPECT_EQ(0xE000, output);
}

TEST(EmojiFontMap, RejectsCorruptMappingData) {
  std::vector<uint8_t> font = sampleFont();
  font[40] ^= 0x80;
  EmojiFontMap map;
  EXPECT_FALSE(map.begin(font.data(), font.size()));
  EXPECT_FALSE(map.isReady());
}

TEST(EmojiFontMap, RejectsMissingFooter) {
  uint8_t data[64] = {};
  EmojiFontMap map;
  EXPECT_FALSE(map.begin(data, sizeof(data)));
}

TEST(EmojiFontMap, AcceptsVersion2Footer) {
  std::vector<uint8_t> font = sampleVersion2Font();
  EmojiFontMap map;
  ASSERT_TRUE(map.begin(font.data(), font.size()));

  const uint8_t text[] = {0xF0, 0x9F, 0x98, 0x80};
  uint16_t output = 0;
  EXPECT_EQ(sizeof(text), map.longestMatch(text, sizeof(text), output));
  EXPECT_EQ(0xE000, output);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
