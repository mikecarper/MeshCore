#include <gtest/gtest.h>

#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/DisplayTextLayout.h>

#include <string>
#include <vector>

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
  struct DrawnText {
    int x;
    int y;
    std::string text;
  };

  std::string printed;
  std::vector<DrawnText> rows;
  int cursor_x = 0;
  int cursor_y = 0;

  TestDisplay() : DisplayDriver(100, 100) {}

  bool isOn() override { return true; }
  void turnOn() override {}
  void turnOff() override {}
  void clear() override {}
  void startFrame(ColorVal) override {}
  void setTextSize(int) override {}
  void setColor(ColorVal) override {}
  void setCursor(int x, int y) override {
    cursor_x = x;
    cursor_y = y;
  }
  void print(const char* str) override {
    printed = str;
    rows.push_back({cursor_x, cursor_y, str});
  }
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

TEST(DisplayDriver, WrapsCompleteTextAcrossAvailableRows) {
  TestDisplay display;
  EXPECT_EQ(3, mesh::ui::drawTextWrapped(
      display, 4, 10, 3, 9, 3, "ABCDEFG"));
  ASSERT_EQ(3U, display.rows.size());
  EXPECT_EQ(4, display.rows[0].x);
  EXPECT_EQ(10, display.rows[0].y);
  EXPECT_EQ("ABC", display.rows[0].text);
  EXPECT_EQ(4, display.rows[1].x);
  EXPECT_EQ(19, display.rows[1].y);
  EXPECT_EQ("DEF", display.rows[1].text);
  EXPECT_EQ(28, display.rows[2].y);
  EXPECT_EQ("G", display.rows[2].text);
}

TEST(DisplayDriver, WrappedTextKeepsUTF8CodepointsIntact) {
  TestDisplay display;
  EXPECT_EQ(2, mesh::ui::drawTextWrapped(
      display, 0, 0, 3, 10, 2, "AB\xF0\x9F\x98\x80" "CDE"));
  ASSERT_EQ(2U, display.rows.size());
  EXPECT_EQ("AB\xF0\x9F\x98\x80", display.rows[0].text);
  EXPECT_EQ("CDE", display.rows[1].text);
  EXPECT_TRUE(isValidUTF8(display.rows[0].text.c_str()));
  EXPECT_TRUE(isValidUTF8(display.rows[1].text.c_str()));
}

TEST(DisplayDriver, KeepsMaximumLengthSSIDWithoutEllipsis) {
  TestDisplay display;
  const std::string ssid = "1234567890ABCDEF1234567890ABCDEF";
  EXPECT_EQ(3, mesh::ui::drawTextWrapped(
      display, 0, 0, 12, 10, 3, ssid.c_str()));
  ASSERT_EQ(3U, display.rows.size());
  EXPECT_EQ(ssid,
            display.rows[0].text + display.rows[1].text
                + display.rows[2].text);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
