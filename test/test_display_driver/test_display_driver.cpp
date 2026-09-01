#include <gtest/gtest.h>

#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/CompanionHomeLayout.h>
#include <helpers/ui/DisplayTextLayout.h>
#include <helpers/ui/WiFiSetupQrPayload.h>

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

  struct FilledRect {
    int x;
    int y;
    int width;
    int height;
  };

  std::string printed;
  std::vector<DrawnText> rows;
  std::vector<FilledRect> fills;
  int cursor_x = 0;
  int cursor_y = 0;
  int text_size = 1;
  int glyph_width = 1;

  TestDisplay(int width = 100, int height = 100, int glyph_width = 1)
      : DisplayDriver(width, height), glyph_width(glyph_width) {}

  bool isOn() override { return true; }
  void turnOn() override {}
  void turnOff() override {}
  void clear() override {}
  void startFrame(ColorVal) override {}
  void setTextSize(int size) override { text_size = size > 0 ? size : 1; }
  void setColor(ColorVal) override {}
  void setCursor(int x, int y) override {
    cursor_x = x;
    cursor_y = y;
  }
  void print(const char* str) override {
    printed = str;
    rows.push_back({cursor_x, cursor_y, str});
  }
  void fillRect(int x, int y, int width, int height) override {
    fills.push_back({x, y, width, height});
  }
  void drawRect(int, int, int, int) override {}
  void drawXbm(int, int, const uint8_t*, int, int) override {}
  uint16_t getTextWidth(const char* str) override {
    uint16_t width = 0;
    for (size_t i = 0; str[i] != 0; ++i) {
      if (((uint8_t)str[i] & 0xC0) != 0x80) ++width;
    }
    return width * glyph_width * text_size;
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

TEST(DisplayDriver, QrCodeIsOptionalByDefault) {
  TestDisplay display;
  EXPECT_FALSE(display.drawQrCode("WIFI:T:nopass;S:MC-Set-90DF;;",
                                  27, 55, 105));
}

TEST(WiFiSetupQrPayload, EncodesOpenSetupNetwork) {
  char payload[64];
  EXPECT_TRUE(mesh::ui::buildWiFiSetupQrPayload(
      payload, sizeof(payload), "MC-Set-90DF"));
  EXPECT_STREQ("WIFI:S:MC-Set-90DF;;", payload);

  char small_payload[32];
  EXPECT_TRUE(mesh::ui::buildWiFiSetupQrPayload(
      small_payload, sizeof(small_payload), "MC-90DF"));
  EXPECT_STREQ("WIFI:S:MC-90DF;;", small_payload);
  // MC-XXXX uses 16 payload bytes, within QR Version 1-L's 17-byte capacity.
  EXPECT_LE(strlen(small_payload), 17U);
}

TEST(WiFiSetupQrPayload, EscapesProtectedNetworkFields) {
  char payload[96];
  EXPECT_TRUE(mesh::ui::buildWiFiSetupQrPayload(
      payload, sizeof(payload), "Cafe;West", "p,ass"));
  EXPECT_STREQ("WIFI:T:WPA;S:Cafe\\;West;P:p\\,ass;;", payload);
}

TEST(WiFiSetupQrPayload, RejectsMissingOrTruncatedFields) {
  char payload[8];
  EXPECT_FALSE(mesh::ui::buildWiFiSetupQrPayload(
      payload, sizeof(payload), ""));
  EXPECT_FALSE(mesh::ui::buildWiFiSetupQrPayload(
      payload, sizeof(payload), "MC-Set-90DF"));
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

TEST(DisplayDriver, LongSetupAddressStaysInsideItsRows) {
  TestDisplay display(160, 160);
  const std::string address =
      "http://very-long-indicator-hostname-for-a-setup-page.example.invalid/"
      "path/that/remains/longer/than/one/logical/display/row/and/continues/"
      "through/the/second/bounded/row/without/reaching/the/pairing/status";
  EXPECT_EQ(2, mesh::ui::drawTextWrapped(
      display, 6, 99, 148, 13, 2, address.c_str()));
  ASSERT_EQ(2U, display.rows.size());
  for (const auto& row : display.rows) {
    EXPECT_GE(row.x, 6);
    EXPECT_LE(row.x + display.getTextWidth(row.text.c_str()), 154);
    EXPECT_GE(row.y, 99);
    EXPECT_LT(row.y, 125);
  }
}

TEST(DisplayDriver, IndicatorHomeSeparatesInfoAndPairingGeometry) {
  const mesh::ui::CompanionHomeLayout layout =
      mesh::ui::makeLargeCompanionHomeLayout(160, 160);

  EXPECT_EQ(128, layout.pairing.y + layout.pairing.height / 2);
  EXPECT_EQ(48, layout.pairing.height);
  EXPECT_FALSE(mesh::ui::displayRegionsOverlap(layout.info,
                                               layout.pairing));
  EXPECT_LE(layout.info.bottom() + 12, layout.pairing.y);
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.info, layout.instruction_y, 12));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.info, layout.network_y, 12));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_label_y, 12));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_value_y, 24));
}

TEST(DisplayDriver, NativeIndicatorExpandsHomeTypographyRegions) {
  EXPECT_TRUE(mesh::ui::usesExpandedCompanionHomeTypography(
      160, 160, 480, 480));
  EXPECT_FALSE(mesh::ui::usesExpandedCompanionHomeTypography(
      160, 160, 320, 320));
  EXPECT_FALSE(mesh::ui::usesExpandedCompanionHomeTypography(
      128, 64, 480, 480));

  const mesh::ui::CompanionHomeLayout layout =
      mesh::ui::makeLargeCompanionHomeLayout(160, 160, true);
  EXPECT_EQ(2, layout.info.x);
  EXPECT_EQ(61, layout.info.y);
  EXPECT_EQ(156, layout.info.width);
  EXPECT_EQ(39, layout.info.height);
  EXPECT_EQ(8, layout.pairing.x);
  EXPECT_EQ(102, layout.pairing.y);
  EXPECT_EQ(144, layout.pairing.width);
  EXPECT_EQ(58, layout.pairing.height);
  EXPECT_FALSE(mesh::ui::displayRegionsOverlap(layout.info,
                                               layout.pairing));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.info, layout.instruction_y, 39));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_label_y, 29));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_value_y, 29));
}

TEST(DisplayDriver, IndicatorPairingValuesFitAtRenderedTextSizes) {
  // Model the fallback font's fixed six-logical-pixel cell. It is the wider
  // of the two paths for CONNECTED; the profile test reads the recovered
  // VLW's real advances and independently checks that path.
  TestDisplay display(160, 160, 6);
  const mesh::ui::CompanionHomeLayout layout =
      mesh::ui::makeLargeCompanionHomeLayout(display.width(),
                                             display.height());

  display.setTextSize(3);
  EXPECT_EQ(108, display.getTextWidth("123456"));
  EXPECT_LE(display.getTextWidth("123456"), layout.pairing.width);
  mesh::ui::drawTextCenteredEllipsized(
      display, layout.pairing, layout.pairing_value_y, "123456");
  ASSERT_EQ(1U, display.rows.size());
  EXPECT_EQ("123456", display.rows.back().text);
  EXPECT_GE(display.rows.back().x, layout.pairing.x);
  EXPECT_LE(display.rows.back().x + display.getTextWidth("123456"),
            layout.pairing.right());

  display.setTextSize(2);
  EXPECT_EQ(108, display.getTextWidth("CONNECTED"));
  EXPECT_LE(display.getTextWidth("CONNECTED"), layout.pairing.width);
  mesh::ui::drawTextCenteredEllipsized(
      display, layout.pairing, layout.pairing_value_y, "CONNECTED");
  ASSERT_EQ(2U, display.rows.size());
  EXPECT_EQ("CONNECTED", display.rows.back().text);
  EXPECT_GE(display.rows.back().x, layout.pairing.x);
  EXPECT_LE(display.rows.back().x + display.getTextWidth("CONNECTED"),
            layout.pairing.right());
}

TEST(DisplayDriver, CompactPairingOwnsBounded128x64LowerRegion) {
  EXPECT_TRUE(mesh::ui::usesCompactCompanionPairingLayout(128, 64));
  EXPECT_FALSE(mesh::ui::usesCompactCompanionPairingLayout(160, 160));
  EXPECT_FALSE(mesh::ui::usesCompactCompanionPairingLayout(128, 65));

  const mesh::ui::CompactCompanionPairingLayout layout =
      mesh::ui::makeCompactCompanionPairingLayout(128, 64);
  const mesh::ui::DisplayRegion inbox = {0, 22, 128, 16};

  EXPECT_EQ(4, layout.pairing.x);
  EXPECT_EQ(38, layout.pairing.y);
  EXPECT_EQ(120, layout.pairing.width);
  EXPECT_EQ(26, layout.pairing.height);
  EXPECT_FALSE(mesh::ui::displayRegionsOverlap(inbox, layout.pairing));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_label_y, 8));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(
      layout.pairing, layout.pairing_value_y, 16));

  // The old instruction and Wi-Fi rows are entirely replaced while pairing
  // is active, so neither can remain behind or overprint the new value.
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(layout.pairing, 43, 8));
  EXPECT_TRUE(mesh::ui::displayRegionContainsLine(layout.pairing, 54, 8));
}

TEST(DisplayDriver, CompactPairingValuesFitAtSizeTwo) {
  TestDisplay display(128, 64, 6);
  const mesh::ui::CompactCompanionPairingLayout layout =
      mesh::ui::makeCompactCompanionPairingLayout(
          display.width(), display.height());

  display.setTextSize(2);
  for (const char* value : {"123456", "CONNECTED"}) {
    EXPECT_LE(display.getTextWidth(value), layout.pairing.width);
    mesh::ui::drawTextCenteredEllipsized(
        display, layout.pairing, layout.pairing_value_y, value);
    EXPECT_EQ(value, display.rows.back().text);
    EXPECT_GE(display.rows.back().x, layout.pairing.x);
    EXPECT_LE(display.rows.back().x + display.getTextWidth(value),
              layout.pairing.right());
  }
}

TEST(DisplayDriver, LongHomeStatusCannotEnterPairingBlock) {
  TestDisplay display(160, 160);
  const mesh::ui::CompanionHomeLayout layout =
      mesh::ui::makeLargeCompanionHomeLayout(display.width(),
                                             display.height());
  const std::string instruction(240, 'I');
  const std::string network_status =
      "Connected to a maximum-length-wireless-network-name at an "
      "unexpectedly-long-address.example.invalid";

  mesh::ui::drawTextCenteredEllipsized(
      display, layout.info, layout.instruction_y, instruction.c_str());
  mesh::ui::drawTextCenteredEllipsized(
      display, layout.info, layout.network_y, network_status.c_str());

  ASSERT_EQ(2U, display.rows.size());
  for (const auto& row : display.rows) {
    EXPECT_GE(row.x, layout.info.x);
    EXPECT_LE(row.x + display.getTextWidth(row.text.c_str()),
              layout.info.right());
    EXPECT_LT(row.y, layout.pairing.y);
  }
}

TEST(DisplayDriver, PairingRegionIsClearedBeforeStatusReplacement) {
  TestDisplay display(160, 160);
  const mesh::ui::CompanionHomeLayout layout =
      mesh::ui::makeLargeCompanionHomeLayout(display.width(),
                                             display.height());

  mesh::ui::clearDisplayRegion(display, layout.pairing);
  mesh::ui::drawTextCenteredEllipsized(
      display, layout.pairing, layout.pairing_value_y, "123456");
  mesh::ui::clearDisplayRegion(display, layout.pairing);
  mesh::ui::drawTextCenteredEllipsized(
      display, layout.pairing, layout.pairing_value_y, "CONNECTED");

  ASSERT_EQ(2U, display.fills.size());
  for (const auto& fill : display.fills) {
    EXPECT_EQ(layout.pairing.x, fill.x);
    EXPECT_EQ(layout.pairing.y, fill.y);
    EXPECT_EQ(layout.pairing.width, fill.width);
    EXPECT_EQ(layout.pairing.height, fill.height);
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
