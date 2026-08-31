#include <gtest/gtest.h>

#include <helpers/ui/CompanionMessageHistory.h>

#include <cstdint>
#include <cstring>

using History = mesh::ui::CompanionMessageHistory<3, 16>;
using LargerHistory = mesh::ui::CompanionMessageHistory<8, 32>;

TEST(CompanionMessageHistory, RetainsNewestEntriesAndOverwritesOldest) {
  History history;
  history.add(100, 0, "Public", "Ch 0 Public [0h]:", "one");
  history.add(200, 1, "#testing", "Ch 1 #testing [0h]:", "two");
  history.add(300, 2, "#bot", "Ch 2 #bot [0h]:", "three");
  history.add(400, 3, "#alerts", "Ch 3 #alerts [0h]:", "four");

  ASSERT_EQ(3U, history.count());
  EXPECT_STREQ("four", history.newest(0)->message);
  EXPECT_STREQ("three", history.newest(1)->message);
  EXPECT_STREQ("two", history.newest(2)->message);
  EXPECT_EQ(nullptr, history.newest(3));
}

TEST(CompanionMessageHistory, CopiesEphemeralInputAndTerminatesFields) {
  History history;
  char channel[] = "temporary";
  char origin[] = "sender [direct]:";
  char message[] = "0123456789abcdef-overflow";
  history.add(10, -1, channel, origin, message);

  channel[0] = 'X';
  origin[0] = 'X';
  message[0] = 'X';
  const History::Entry* entry = history.newest(0);
  ASSERT_NE(nullptr, entry);
  EXPECT_STREQ("temporary", entry->channel_name);
  EXPECT_STREQ("sender [direct]:", entry->origin);
  EXPECT_STREQ("0123456789abcde", entry->message);
}

TEST(CompanionMessageHistory, SelectsOnlyLatestEntryPerChannelOrDirectPeer) {
  LargerHistory history;
  history.add(100, 0, "Public", "Ch 0 Public [0h]:", "old public");
  history.add(200, -1, "", "Alice [direct]:", "old direct");
  history.add(300, 1, "#testing", "Ch 1 #testing [0h]:", "testing");
  history.add(400, 0, "Public", "Ch 0 Public [1h]:", "new public");
  history.add(500, -1, "", "Alice [2h]:", "new direct");

  EXPECT_FALSE(history.hasNewerEntryForThread(0));
  EXPECT_FALSE(history.hasNewerEntryForThread(1));
  EXPECT_FALSE(history.hasNewerEntryForThread(2));
  EXPECT_TRUE(history.hasNewerEntryForThread(3));
  EXPECT_TRUE(history.hasNewerEntryForThread(4));
}

TEST(CompanionMessageHistory, FormatsChannelAndDirectLabels) {
  History history;
  history.add(100, 7, "#bot-pdx", "Ch 7 #bot-pdx [0h]:", "ack");
  history.add(200, -1, "", "Alice [direct]:", "hello");

  char label[32];
  History::threadLabel(*history.newest(1), label, sizeof(label));
  EXPECT_STREQ("#bot-pdx", label);
  History::threadLabel(*history.newest(0), label, sizeof(label));
  EXPECT_STREQ("Direct: Alice", label);
}

TEST(CompanionMessageHistory, LaysOutFiveIndicatorRows) {
  const mesh::ui::CompanionMessageListLayout layout =
      mesh::ui::makeCompanionMessageListLayout(160);
  EXPECT_EQ(20, layout.top);
  EXPECT_EQ(26, layout.row_height);
  EXPECT_EQ(5, layout.visible_rows);
  EXPECT_LT(layout.top + (layout.visible_rows - 1) * layout.row_height
                + layout.divider_offset,
            160);
}

TEST(CompanionMessageHistory, CompactsOnlyMessageScreenChrome) {
  const mesh::ui::CompanionMessageChromeLayout normal =
      mesh::ui::makeCompanionMessageChromeLayout(false);
  EXPECT_FALSE(normal.compact_text);
  EXPECT_EQ(11, normal.header_divider_y);
  EXPECT_EQ(24, normal.filter_height);

  const mesh::ui::CompanionMessageChromeLayout compact =
      mesh::ui::makeCompanionMessageChromeLayout(true);
  EXPECT_TRUE(compact.compact_text);
  EXPECT_EQ(8, compact.header_divider_y);
  EXPECT_EQ(11, compact.origin_y);
  EXPECT_EQ(22, compact.message_y);
  EXPECT_EQ(14, compact.filter_height);
  EXPECT_EQ(3, compact.filter_text_offset);
}

TEST(CompanionMessageHistory, FormatsRelativeAgeAndMillisRollover) {
  char age[16];
  mesh::ui::formatCompanionMessageAge(age, sizeof(age), 0);
  EXPECT_STREQ("now", age);
  mesh::ui::formatCompanionMessageAge(age, sizeof(age), 12'000);
  EXPECT_STREQ("12s ago", age);
  mesh::ui::formatCompanionMessageAge(age, sizeof(age), 60'000);
  EXPECT_STREQ("1m ago", age);
  mesh::ui::formatCompanionMessageAge(age, sizeof(age), 3'600'000);
  EXPECT_STREQ("1h ago", age);
  mesh::ui::formatCompanionMessageAge(
      age, sizeof(age), 50ULL * 24ULL * 60ULL * 60ULL * 1000ULL);
  EXPECT_STREQ("50d ago", age);

  const uint32_t heard = UINT32_MAX - 999U;
  const uint32_t now = 1000U;
  mesh::ui::formatCompanionMessageAge(age, sizeof(age), now - heard);
  EXPECT_STREQ("2s ago", age);
}

TEST(CompanionMessageHistory, MakesPreviewSingleLine) {
  char preview[] = "first\nsecond\rthird\tfourth";
  mesh::ui::makeCompanionMessagePreviewSingleLine(preview);
  EXPECT_STREQ("first second third fourth", preview);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
