#include <gtest/gtest.h>

#include <helpers/HostCliBridge.h>

TEST(HostCliBridge, ParsesPlainAndCorrelatedRequests) {
  mesh::HostCliBridge::RequestView request;
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("host cpu-temp", request));
  ASSERT_NE(nullptr, request.text);
  EXPECT_STREQ("cpu-temp", request.text);
  EXPECT_EQ(8U, request.text_len);
  EXPECT_EQ(0U, request.correlation_len);

  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("A7|host get status", request));
  ASSERT_NE(nullptr, request.correlation);
  EXPECT_EQ(0, memcmp("A7|", request.correlation, 3));
  EXPECT_EQ(3U, request.correlation_len);
  EXPECT_STREQ("get status", request.text);

  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest(
                "host cpu-temp \t\r\n", request));
  EXPECT_EQ(8U, request.text_len);
  char record[mesh::HostCliBridge::USB_SIGNED_CONTENT_MAX + 1U];
  ASSERT_TRUE(mesh::HostCliBridge::formatUsbRequest(
      record, sizeof(record), 1U, 2U, request.text, request.text_len));
  EXPECT_STREQ("HOSTCLI/1 REQUEST 00000001 0000000000000002 "
               "Y3B1LXRlbXA", record);
}

TEST(HostCliBridge, DistinguishesInvalidHostRequestsFromOtherCommands) {
  mesh::HostCliBridge::RequestView request;
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("host", request));
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("host    ", request));
  EXPECT_EQ(mesh::HostCliBridge::NOT_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("host.reply 12345678 OK", request));
  EXPECT_EQ(mesh::HostCliBridge::NOT_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest("get host", request));
}

TEST(HostCliBridge, EnforcesRemoteCommandLengthWithAndWithoutCorrelation) {
  char command[mesh::HostCliBridge::REMOTE_COMMAND_MAX + 2U];
  memcpy(command, "host ", 5);
  memset(command + 5, 'x', mesh::HostCliBridge::REQUEST_TEXT_MAX);
  command[mesh::HostCliBridge::REMOTE_COMMAND_MAX] = 0;

  mesh::HostCliBridge::RequestView request;
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest(command, request));

  command[mesh::HostCliBridge::REMOTE_COMMAND_MAX] = 'x';
  command[mesh::HostCliBridge::REMOTE_COMMAND_MAX + 1U] = 0;
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest(command, request));

  memcpy(command, "Q2|host ", 8);
  memset(command + 8, 'y',
         mesh::HostCliBridge::REQUEST_TEXT_WITH_CORRELATION_MAX);
  command[mesh::HostCliBridge::REMOTE_COMMAND_MAX] = 0;
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseRequest(command, request));
}

TEST(HostCliBridge, FormatsLineSafeBase64UrlUsbRecord) {
  char record[mesh::HostCliBridge::USB_SIGNED_CONTENT_MAX + 1U];
  ASSERT_TRUE(mesh::HostCliBridge::formatUsbRequest(
      record, sizeof(record), 0x89ABCDEFU, 0x0123456789ABCDEFULL,
      "cpu-temp", 8));
  EXPECT_STREQ(
      "HOSTCLI/1 REQUEST 89ABCDEF 0123456789ABCDEF Y3B1LXRlbXA",
      record);

  const char unsafe[] = "line one\nline two";
  ASSERT_TRUE(mesh::HostCliBridge::formatUsbRequest(
      record, sizeof(record), 1U, 2U, unsafe, strlen(unsafe)));
  EXPECT_EQ(nullptr, strchr(record, '\n'));
  EXPECT_EQ(nullptr, strchr(record, '='));
}

TEST(HostCliBridge, ParsesAndFormatsServiceClaimProof) {
  uint64_t challenge = 0;
  EXPECT_TRUE(mesh::HostCliBridge::parseServiceClaim(
      "@claim=0123456789ABCDEF", 23U, challenge));
  EXPECT_EQ(0x0123456789ABCDEFULL, challenge);
  EXPECT_FALSE(mesh::HostCliBridge::parseServiceClaim(
      "@claim=0123456789ABCDEG", 23U, challenge));
  EXPECT_FALSE(mesh::HostCliBridge::parseServiceClaim(
      "@claim=0000000000000000", 23U, challenge));
  EXPECT_FALSE(mesh::HostCliBridge::parseServiceClaim(
      "@claim=0123456789ABCDE", 22U, challenge));

  char record[mesh::HostCliBridge::CLAIMED_SIGNED_CONTENT_MAX + 1U];
  ASSERT_TRUE(mesh::HostCliBridge::formatUsbClaim(
      record, sizeof(record), 0x89ABCDEFU, 0x0123456789ABCDEFULL,
      0xFEDCBA9876543210ULL));
  EXPECT_STREQ(
      "HOSTCLI/1 CLAIMED 89ABCDEF 0123456789ABCDEF FEDCBA9876543210",
      record);
}

TEST(HostCliBridge, ParsesOnlyBoundedLineSafeUtf8Replies) {
  mesh::HostCliBridge::ReplyView reply;
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(
                "host.reply 89abcdef 0123456789abcdef CPU 47.2 C", reply));
  EXPECT_EQ(0x89ABCDEFU, reply.request_id);
  EXPECT_EQ(0x0123456789ABCDEFULL, reply.request_nonce);
  EXPECT_STREQ("CPU 47.2 C", reply.text);

  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(
                "host.reply 89ABCDE 0123456789ABCDEF CPU 1 C", reply));
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply("host.reply ", reply));
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(
                "host.reply 89ABCDEF 0123456789ABCDEG CPU 1 C", reply));
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(
                "host.reply 89ABCDEF 0123456789ABCDEF first\nsecond", reply));
  EXPECT_EQ(mesh::HostCliBridge::NOT_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(
                "host.replyx 89ABCDEF 0123456789ABCDEF OK", reply));

  const char valid_utf8[] =
      "host.reply 12345678 FEDCBA9876543210 " "\xE2\x82\xAC";
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(valid_utf8, reply));
  const char invalid_utf8[] =
      "host.reply 12345678 FEDCBA9876543210 " "\xE2\x82";
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(invalid_utf8, reply));
}

TEST(HostCliBridge, EnforcesMaximumReplyLength) {
  char command[mesh::HostCliBridge::SERIAL_REPLY_COMMAND_MAX + 2U];
  const int prefix_len = snprintf(command, sizeof(command),
                                  "host.reply 12345678 FEDCBA9876543210 ");
  ASSERT_GT(prefix_len, 0);
  memset(command + prefix_len, 'r', mesh::HostCliBridge::REMOTE_REPLY_MAX);
  command[prefix_len + mesh::HostCliBridge::REMOTE_REPLY_MAX] = 0;

  mesh::HostCliBridge::ReplyView reply;
  EXPECT_EQ(mesh::HostCliBridge::VALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(command, reply));
  command[prefix_len + mesh::HostCliBridge::REMOTE_REPLY_MAX] = 'r';
  command[prefix_len + mesh::HostCliBridge::REMOTE_REPLY_MAX + 1U] = 0;
  EXPECT_EQ(mesh::HostCliBridge::INVALID_HOST_COMMAND,
            mesh::HostCliBridge::parseReply(command, reply));
}

TEST(HostCliBridge, PreservesCorrelationAndDoesNotSplitUtf8AtLimit) {
  char output[mesh::HostCliBridge::REMOTE_REPLY_MAX + 1U];
  EXPECT_EQ(5U, mesh::HostCliBridge::formatRemoteReply(
                    output, sizeof(output), "B4|host cpu-temp", "OK"));
  EXPECT_STREQ("B4|OK", output);

  char short_output[5];
  const char service_reply[] = "abc" "\xE2\x82\xAC";
  EXPECT_EQ(3U, mesh::HostCliBridge::formatRemoteReply(
                    short_output, sizeof(short_output), "host test",
                    service_reply));
  EXPECT_STREQ("abc", short_output);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
