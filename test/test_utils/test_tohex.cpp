#include <gtest/gtest.h>
#include "Utils.h"

using namespace mesh;

#define HEX_BUFFER_SIZE(input) (sizeof(input) * 2 + 1)

TEST(UtilsToHex, ConvertSingleByte) {
    uint8_t input[] = {0xAB};
    char output[HEX_BUFFER_SIZE(input)];

    Utils::toHex(output, input, sizeof(input));

    EXPECT_STREQ("AB", output);
}

TEST(UtilsToHex, ConvertMultipleBytes) {
    uint8_t input[] = {0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF};
    char output[HEX_BUFFER_SIZE(input)];

    Utils::toHex(output, input, sizeof(input));

    EXPECT_STREQ("0123456789ABCDEF", output);
}

TEST(UtilsToHex, ConvertZeroByte) {
    uint8_t input[] = {0x00};
    char output[HEX_BUFFER_SIZE(input)];

    Utils::toHex(output, input, sizeof(input));

    EXPECT_STREQ("00", output);
}

TEST(UtilsToHex, ConvertMaxByte) {
    uint8_t input[] = {0xFF};
    char output[HEX_BUFFER_SIZE(input)];

    Utils::toHex(output, input, sizeof(input));

    EXPECT_STREQ("FF", output);
}

TEST(UtilsToHex, NullTerminatesOnEmptyInput) {
    uint8_t input[] = {0xAB};
    char output[] = "X";  // Pre-fill with X.

    Utils::toHex(output, input, 0);

    // Should just null-terminate at position 0
    EXPECT_EQ('\0', output[0]);
}

TEST(UtilsFromHex, ConvertsUpperAndLowerCase) {
    uint8_t output[3] = {};

    ASSERT_TRUE(Utils::fromHex(output, sizeof(output), "0aBCfF"));
    EXPECT_EQ(0x0A, output[0]);
    EXPECT_EQ(0xBC, output[1]);
    EXPECT_EQ(0xFF, output[2]);
}

TEST(UtilsFromHex, RejectsInvalidCharacters) {
    uint8_t output[2] = {0xAA, 0xAA};

    EXPECT_FALSE(Utils::fromHex(output, sizeof(output), "0G!1"));
}

TEST(UtilsFromHex, RejectsWrongLength) {
    uint8_t output[2] = {};

    EXPECT_FALSE(Utils::fromHex(output, sizeof(output), "001"));
    EXPECT_FALSE(Utils::fromHex(output, sizeof(output), "001122"));
}

int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
