#include <gtest/gtest.h>

#include <helpers/ui/DisplayDriver.h>

#include <string>

namespace {

bool isValidUTF8(const char* text) {
  for (size_t offset = 0; text[offset] != 0;) {
    uint8_t first = (uint8_t)text[offset];
    size_t length = 1;
    if (first >= 0xC2 && first <= 0xDF) {
      length = 2;
    } else if (first >= 0xE0 && first <= 0xEF) {
      length = 3;
    } else if (first >= 0xF0 && first <= 0xF4) {
      length = 4;
    } else if (first >= 0x80) {
      return false;
    }
    for (size_t i = 1; i < length; ++i) {
      if (text[offset + i] == 0
          || ((uint8_t)text[offset + i] & 0xC0) != 0x80) {
        return false;
      }
    }
    offset += length;
  }
  return true;
}

class TestDisplay : public DisplayDriver {
public:
  std::string printed;

  TestDisplay() : DisplayDriver(100, 100) {}

  bool isOn() override { return true; }
  void turnOn() override {}
  void turnOff() override {}
  void clear() override {}
  void startFrame(ColorVal) override {}
  void setTextSize(int) override {}
  void setColor(ColorVal) override {}
  void setCursor(int, int) override {}
  void print(const char* str) override { printed = str; }
  void fillRect(int, int, int, int) override {}
  void drawRect(int, int, int, int) override {}
  void drawXbm(int, int, const uint8_t*, int, int) override {}
  uint16_t getTextWidth(const char* str) override {
    uint16_t width = 0;
    for (size_t i = 0; str[i] != 0; ++i) {
      if (((uint8_t)str[i] & 0xC0) != 0x80) ++width;
    }
    return width;
  }
  void endFrame() override {}
};

}  // namespace

TEST(DisplayDriver, EllipsizesOnlyAtUTF8CodepointBoundaries) {
  TestDisplay display;
  display.drawTextEllipsized(0, 0, 5, "AB\xF0\x9F\x98\x80" "CDE");
  EXPECT_EQ("AB...", display.printed);
  EXPECT_TRUE(isValidUTF8(display.printed.c_str()));
}

TEST(DisplayDriver, FixedBufferDoesNotSplitUTF8Codepoint) {
  TestDisplay display;
  std::string text(254, 'A');
  text += "\xF0\x9F\x98\x80";
  text += 'B';
  display.drawTextEllipsized(0, 0, 255, text.c_str());
  EXPECT_EQ(std::string(254, 'A'), display.printed);
  EXPECT_TRUE(isValidUTF8(display.printed.c_str()));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
