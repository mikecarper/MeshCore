#include <gtest/gtest.h>

#include <helpers/ui/ColorEmojiAtlas.h>

#include <stdint.h>
#include <vector>

using mesh::ui::ColorEmojiAtlas;

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
  std::vector<uint8_t> font(24, 0x55);
  const uint32_t map_offset = font.size();
  std::vector<uint8_t> map = {
    'E', 'M', '0', '1', 1, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0,
  };
  font.insert(font.end(), map.begin(), map.end());

  const uint32_t atlas_offset = font.size();
  std::vector<uint8_t> atlas = {
    'C', 'E', '0', '1',
    2, 0, 2, 2, 0xE3, 3, 4, 0,
    1, 2, 3, 4,
    5, 6, 7, 8,
  };
  font.insert(font.end(), atlas.begin(), atlas.end());

  font.insert(font.end(), {'M', 'C', 'E', 'M', 'A', 'P', '2', 0});
  append32(font, map_offset);
  append32(font, map.size());
  append32(font, crc32(map.data(), map.size()));
  append32(font, atlas_offset);
  append32(font, atlas.size());
  append32(font, crc32(atlas.data(), atlas.size()));
  append32(font, 0);
  append32(font, 0);
  return font;
}

}  // namespace

TEST(ColorEmojiAtlas, ReadsGlyphsAndMetrics) {
  std::vector<uint8_t> font = sampleFont();
  ColorEmojiAtlas atlas;
  ASSERT_TRUE(atlas.begin(font.data(), font.size()));
  EXPECT_EQ(2, atlas.width());
  EXPECT_EQ(2, atlas.height());
  EXPECT_EQ(3, atlas.textScale());
  EXPECT_EQ(0xE3, atlas.transparent());
  ASSERT_NE(nullptr, atlas.glyph(0xE000));
  EXPECT_EQ(1, atlas.glyph(0xE000)[0]);
  EXPECT_EQ(8, atlas.glyph(0xE001)[3]);
  EXPECT_EQ(nullptr, atlas.glyph(0xDFFF));
  EXPECT_EQ(nullptr, atlas.glyph(0xE002));
}

TEST(ColorEmojiAtlas, RejectsCorruptAtlas) {
  std::vector<uint8_t> font = sampleFont();
  font[50] ^= 0x80;
  ColorEmojiAtlas atlas;
  EXPECT_FALSE(atlas.begin(font.data(), font.size()));
  EXPECT_FALSE(atlas.isReady());
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
