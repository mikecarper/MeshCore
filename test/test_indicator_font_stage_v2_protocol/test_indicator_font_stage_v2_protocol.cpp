#include <gtest/gtest.h>

#include <helpers/IndicatorFontStageV2Protocol.h>

using mesh::indicator_font::StageV2BeginAction;

TEST(IndicatorFontStageV2Protocol, BeginReplyIsExactAndFallbackFailsClosed) {
  using mesh::indicator_font::classifyStageV2BeginReply;

  EXPECT_EQ(classifyStageV2BeginReply(true, "READY 2 512"),
            StageV2BeginAction::UseAcknowledged);
  EXPECT_EQ(classifyStageV2BeginReply(true, "ERROR COMMAND"),
            StageV2BeginAction::UseLegacy);

  // A missing response is ambiguous: the receiver may already be waiting for
  // binary data after its READY was lost. It must never trigger legacy bytes.
  EXPECT_EQ(classifyStageV2BeginReply(false, nullptr),
            StageV2BeginAction::Fail);
  EXPECT_EQ(classifyStageV2BeginReply(false, "ERROR COMMAND"),
            StageV2BeginAction::Fail);
  EXPECT_EQ(classifyStageV2BeginReply(true, nullptr),
            StageV2BeginAction::Fail);

  for (const char* reply : {
           "", "READY", "READY 2", "READY 2 0512", "READY 2 512 ",
           "ERROR COMMAND ", "ERROR SD", "ERROR OPEN", "ERROR CHUNK",
           "STAGED",
       }) {
    EXPECT_EQ(classifyStageV2BeginReply(true, reply),
              StageV2BeginAction::Fail)
        << reply;
  }
}

TEST(IndicatorFontStageV2Protocol, ChunkBoundariesIncludeShortFinalBlock) {
  using mesh::indicator_font::stageV2ChunkSize;

  EXPECT_EQ(stageV2ChunkSize(0, 0), 0u);
  EXPECT_EQ(stageV2ChunkSize(64, 0), 64u);
  EXPECT_EQ(stageV2ChunkSize(64, 64), 0u);
  EXPECT_EQ(stageV2ChunkSize(511, 0), 511u);
  EXPECT_EQ(stageV2ChunkSize(512, 0), 512u);
  EXPECT_EQ(stageV2ChunkSize(512, 512), 0u);
  EXPECT_EQ(stageV2ChunkSize(513, 0), 512u);
  EXPECT_EQ(stageV2ChunkSize(513, 512), 1u);
  EXPECT_EQ(stageV2ChunkSize(513, 513), 0u);
  EXPECT_EQ(stageV2ChunkSize(513, 514), 0u);
}

TEST(IndicatorFontStageV2Protocol, RealAssetHasCanonicalAckSchedule) {
  using mesh::indicator_font::advanceStageV2Offset;
  using mesh::indicator_font::kStageV2ChunkBytes;
  using mesh::indicator_font::stageV2ChunkSize;

  constexpr size_t assetSize = 1302608;
  size_t offset = 0;
  size_t fullBlocks = 0;
  size_t shortBlocks = 0;
  size_t blocks = 0;
  while (offset < assetSize) {
    const size_t chunk = stageV2ChunkSize(assetSize, offset);
    ASSERT_GT(chunk, 0u);
    size_t next = 0;
    ASSERT_TRUE(advanceStageV2Offset(assetSize, offset, chunk, next));
    ASSERT_GT(next, offset);
    ASSERT_LE(next, assetSize);
    if (chunk == kStageV2ChunkBytes) {
      ++fullBlocks;
    } else {
      ++shortBlocks;
      EXPECT_EQ(chunk, 80u);
      EXPECT_EQ(next, assetSize);
    }
    offset = next;
    ASSERT_LT(++blocks, 3000u);
  }

  EXPECT_EQ(offset, assetSize);
  EXPECT_EQ(fullBlocks, 2544u);
  EXPECT_EQ(shortBlocks, 1u);
  EXPECT_EQ(blocks, 2545u);
}

TEST(IndicatorFontStageV2Protocol, OffsetAdvancesOnlyForACompleteBlock) {
  using mesh::indicator_font::advanceStageV2Offset;

  size_t next = 999;
  EXPECT_FALSE(advanceStageV2Offset(1025, 0, 0, next));
  EXPECT_FALSE(advanceStageV2Offset(1025, 0, 511, next));
  EXPECT_FALSE(advanceStageV2Offset(1025, 0, 513, next));
  EXPECT_EQ(next, 999u);

  ASSERT_TRUE(advanceStageV2Offset(1025, 0, 512, next));
  EXPECT_EQ(next, 512u);
  ASSERT_TRUE(advanceStageV2Offset(1025, next, 512, next));
  EXPECT_EQ(next, 1024u);
  ASSERT_TRUE(advanceStageV2Offset(1025, next, 1, next));
  EXPECT_EQ(next, 1025u);
  EXPECT_FALSE(advanceStageV2Offset(1025, next, 1, next));
}

TEST(IndicatorFontStageV2Protocol, AckMustBeCanonicalAndCumulative) {
  using mesh::indicator_font::parseStageV2Ack;

  EXPECT_TRUE(parseStageV2Ack("ACK 512", 512));
  EXPECT_TRUE(parseStageV2Ack("ACK 1024", 1024));
  EXPECT_TRUE(parseStageV2Ack("ACK 1302608", 1302608));

  EXPECT_FALSE(parseStageV2Ack(nullptr, 512));
  EXPECT_FALSE(parseStageV2Ack("", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK ", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK 0", 0));
  EXPECT_FALSE(parseStageV2Ack("ACK 0512", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK +512", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK -512", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK  512", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK 512 ", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK 512x", 512));
  EXPECT_FALSE(parseStageV2Ack("ACK 184467440737095516160", 512));
  EXPECT_FALSE(parseStageV2Ack("READY 2 512", 512));
  EXPECT_FALSE(parseStageV2Ack("STAGED", 512));

  // A prior or future block's otherwise valid ACK cannot release this block.
  EXPECT_FALSE(parseStageV2Ack("ACK 512", 1024));
  EXPECT_FALSE(parseStageV2Ack("ACK 1024", 512));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
