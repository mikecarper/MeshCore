#include <gtest/gtest.h>

#include "helpers/IdentityGeneration.h"

namespace {

mesh::LocalIdentity identityWithPrefix(uint8_t prefix) {
  mesh::LocalIdentity identity;
  memset(identity.pub_key, 0x5A, sizeof(identity.pub_key));
  identity.pub_key[0] = prefix;
  return identity;
}

} // namespace

TEST(IdentityGeneration, AcceptsFirstUsableIdentity) {
  mesh::LocalIdentity identity;
  size_t calls = 0;

  const bool generated = mesh::generateUsableLocalIdentity(
      identity, [&calls]() {
        ++calls;
        return identityWithPrefix(0x42);
      });

  EXPECT_TRUE(generated);
  EXPECT_EQ(1U, calls);
  EXPECT_EQ(0x42, identity.pub_key[0]);
}

TEST(IdentityGeneration, RetriesBothReservedPrefixes) {
  mesh::LocalIdentity identity;
  size_t calls = 0;

  const bool generated = mesh::generateUsableLocalIdentity(
      identity, [&calls]() {
        const uint8_t prefixes[] = {0x00, 0xFF, 0x7E};
        return identityWithPrefix(prefixes[calls++]);
      });

  EXPECT_TRUE(generated);
  EXPECT_EQ(3U, calls);
  EXPECT_EQ(0x7E, identity.pub_key[0]);
}

TEST(IdentityGeneration, AcceptsFinalProvisionedAttempt) {
  mesh::LocalIdentity identity;
  size_t calls = 0;

  const bool generated = mesh::generateUsableLocalIdentity(
      identity, [&calls]() {
        ++calls;
        return identityWithPrefix(
            calls == mesh::MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS ? 0x23 : 0x00);
      });

  EXPECT_TRUE(generated);
  EXPECT_EQ(mesh::MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS, calls);
  EXPECT_EQ(0x23, identity.pub_key[0]);
}

TEST(IdentityGeneration, FailsClosedAfterProvisionedAttempts) {
  mesh::LocalIdentity identity;
  size_t calls = 0;

  const bool generated = mesh::generateUsableLocalIdentity(
      identity, [&calls]() {
        ++calls;
        return identityWithPrefix(0xFF);
      });

  EXPECT_FALSE(generated);
  EXPECT_EQ(mesh::MAX_LOCAL_IDENTITY_GENERATION_ATTEMPTS, calls);
  EXPECT_TRUE(mesh::hasReservedIdentityPrefix(identity));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
