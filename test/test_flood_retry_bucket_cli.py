#!/usr/bin/env python3
"""Run the production flood retry bucket CLI branches with sanitizer checks."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r"""
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <helpers/CLICommandUtils.h>
#include <helpers/TxtDataHelpers.h>

@CONSTANTS@

namespace mesh {
struct Utils {
  static int parseTextParts(char*, const char*[], int, char = ',');
  static void toHex(char*, const uint8_t*, size_t);
  static bool isHexChar(char);
  static bool fromHex(uint8_t*, int, const char*);
};
@UTILS@
}
@STRNCPY@

struct NodePrefs {
  uint32_t before;
  uint8_t flood_retry_bridge_buckets[FLOOD_RETRY_BRIDGE_BUCKETS][FLOOD_RETRY_BUCKET_PREFIXES][FLOOD_RETRY_PREFIX_LEN];
  uint8_t flood_retry_prefixes[FLOOD_RETRY_PREFIX_SLOTS][FLOOD_RETRY_PREFIX_LEN];
  uint32_t unrelated[8];
};

@HELPERS@

struct CommonCLI {
  NodePrefs prefs;
  NodePrefs* _prefs = &prefs;
  unsigned saves = 0;
  CommonCLI() {
    memset(&prefs, 0, sizeof(prefs));
    prefs.before = 0x12345678;
    for (unsigned i = 0; i < 8; ++i) prefs.unrelated[i] = 0xABCDEF00U + i;
    for (unsigned i = 0; i < FLOOD_RETRY_BRIDGE_BUCKETS; ++i) {
      prefs.flood_retry_bridge_buckets[i][0][0] = 0x10 + i;
      prefs.flood_retry_bridge_buckets[i][0][1] = 0x20;
      prefs.flood_retry_bridge_buckets[i][0][2] = 0x30;
    }
    prefs.flood_retry_prefixes[0][0] = 0xE0;
  }
  void savePrefs() { ++saves; }
  std::string run(const std::string& argument, bool setter = true) {
    const std::string input = std::string(setter ? "flood.retry.bucket " : "flood.retry.bucket.") + argument;
    const std::string original = input;
    const char* config = input.c_str();
    struct Reply {
      uint32_t before;
      char text[160];
      uint32_t after;
    } output;
    memset(&output, 0x55, sizeof(output));
    char* reply = output.text;
    char tmp[COMMON_CLI_TMP_LEN];
    if (setter) {
      @SET_BRANCH@
      else assert(false);
    } else {
      @GET_BRANCH@
      else assert(false);
    }
    assert(input == original);
    assert(output.before == 0x55555555 && output.after == 0x55555555);
    for (size_t i = strlen(reply) + 1; i < sizeof(output.text); ++i)
      assert(reply[i] == char(0x55));
    return reply;
  }
};

static void rejected(const std::string& argument, bool setter = true) {
  CommonCLI cli;
  const NodePrefs before = cli.prefs;
  const std::string reply = cli.run(argument, setter);
  assert(reply.rfind("Error, ", 0) == 0);
  assert(cli.saves == 0);
  assert(memcmp(&before, &cli.prefs, sizeof(before)) == 0);
}

static void validSetters() {
  for (unsigned bucket = 1; bucket <= FLOOD_RETRY_BRIDGE_BUCKETS; ++bucket) {
    for (const std::string& token : {std::to_string(bucket), "+" + std::to_string(bucket),
                                   "000" + std::to_string(bucket),
                                   "\t" + std::to_string(bucket) + "\t"}) {
      CommonCLI cli;
      NodePrefs expected = cli.prefs;
      auto& selected = expected.flood_retry_bridge_buckets[bucket - 1];
      memset(selected, 0, sizeof(selected));
      selected[0][0] = 0xA1; selected[0][1] = 0xB2; selected[0][2] = 0xC3;
      selected[1][0] = 0xD4; selected[1][1] = 0xE5; selected[1][2] = 0xF6;
      assert(cli.run(token + " A1B2C3,d4e5f6") == "OK");
      assert(cli.saves == 1 && memcmp(&expected, &cli.prefs, sizeof(expected)) == 0);
      assert(cli.run(std::to_string(bucket), false) == "> A1B2C3,D4E5F6");
      assert(cli.saves == 1);
    }
    for (const char* clear : {"none", "off"}) {
      CommonCLI cli;
      NodePrefs expected = cli.prefs;
      memset(expected.flood_retry_bridge_buckets[bucket - 1], 0,
             sizeof(expected.flood_retry_bridge_buckets[bucket - 1]));
      assert(cli.run(std::to_string(bucket) + " " + clear) == "OK");
      assert(cli.saves == 1 && memcmp(&expected, &cli.prefs, sizeof(expected)) == 0);
      assert(cli.run(std::to_string(bucket), false) == "> none");
    }
  }
  CommonCLI cli;
  assert(cli.run(std::string(512, '0') + "1 none") == "OK");
  assert(cli.saves == 1);
}

static void invalidSetters() {
  for (const char* token : {"0", "7", "255", "256", "257", "262", "513", "65537",
                           "-1", "-250", "-255", "-511", "-4294967295", "1tail", "6tail",
                           "1.0", "1e0", "0x1", "+", "--1", "1\ttail", "1\rjunk",
                           "2147483648", "4294967297", "-2147483649", "9223372036854775808",
                           "18446744073709551617", "-9223372036854775809"}) {
    for (const char* list : {"none", "A1B2C3"}) rejected(std::string(token) + " " + list);
  }
  rejected(std::string(512, '9') + " none");
  for (const char* argument : {"", "1", "1 ", " 1 none", "1\tnone", "none", "1  none"})
    rejected(argument);
}

static void prefixValidation() {
  for (const char* list : {"000000", "ZZZZZZ", "A1B2", "A1B2C3,000000", "off junk"})
    rejected(std::string("1 ") + list);
  std::string list;
  for (unsigned i = 1; i <= FLOOD_RETRY_BUCKET_PREFIXES; ++i) {
    char prefix[7];
    snprintf(prefix, sizeof(prefix), "A1B2%02X", i);
    if (!list.empty()) list += ',';
    list += prefix;
  }
  CommonCLI cli;
  assert(cli.run("6 " + list) == "OK");
  assert(cli.saves == 1);
  assert(cli.run("6", false) == "> " + list);
  for (unsigned i = 0; i < FLOOD_RETRY_BUCKET_PREFIXES; ++i) {
    const auto& prefix = cli.prefs.flood_retry_bridge_buckets[5][i];
    assert(prefix[0] == 0xA1 && prefix[1] == 0xB2 && prefix[2] == i + 1);
  }
}

static void collisionWarnings() {
  CommonCLI cli;
  assert(cli.run("1 112233") == "OK - WARNING: 1-byte 11 matches buckets 1 and 2");
  assert(cli.saves == 1 && cli.run("1", false) == "> 112233");
  assert(cli.run("1 E01122") == "OK - WARNING: 1-byte E0 matches buckets 1 and 7 (other)");
  assert(cli.saves == 2 && cli.run("1", false) == "> E01122");
}

static void getters() {
  CommonCLI cli;
  const NodePrefs before = cli.prefs;
  for (unsigned bucket = 1; bucket <= FLOOD_RETRY_BRIDGE_BUCKETS; ++bucket) {
    char expected[16];
    snprintf(expected, sizeof(expected), "> %02X2030", 0x10 + bucket - 1);
    for (const std::string& token : {std::to_string(bucket), "+" + std::to_string(bucket),
                                   "00" + std::to_string(bucket),
                                   "\t" + std::to_string(bucket) + "\t"})
      assert(cli.run(token, false) == expected);
  }
  assert(cli.saves == 0 && memcmp(&before, &cli.prefs, sizeof(before)) == 0);
  for (const char* token : {"0", "7", "257", "-255", "1tail", "1.0", "1e0", "1 none",
                           "2147483648", "-2147483649", "18446744073709551617"})
    rejected(token, false);
  rejected(std::string(512, '9'), false);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario == "valid") validSetters();
  else if (scenario == "invalid") invalidSetters();
  else if (scenario == "prefixes") prefixValidation();
  else if (scenario == "warnings") collisionWarnings();
  else if (scenario == "getters") getters();
  else rejected(scenario + " none");
  puts("PASS: production flood retry bucket CLI");
}
"""


class FloodRetryBucketCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("Flood bucket CLI tests require a C++17 sanitizer compiler")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
        utils = (ROOT / "src/Utils.cpp").read_text(encoding="ascii")
        constants = header[header.index("#ifndef FLOOD_RETRY_PREFIX_SLOTS"):
                           header.index("#ifndef FLOOD_CHANNEL_KEY_PREFIX_LEN")]
        core = (ROOT / "src/MeshCore.h").read_text(encoding="ascii")
        constants += re.search(r"^#define PRV_KEY_SIZE\s+.+$", core, re.MULTILINE).group()
        helpers = "\n".join(body(common, signature) for signature in (
            "static void formatFloodRetryPrefixList(", "static bool parseFloodRetryPrefixList(",
            "static const uint8_t* floodRetryBucketPrefixAt(",
            "static uint8_t floodRetryBucketPrefixCount(",
            "static bool findFloodRetryFirstByteCollision(",
            "static bool formatFloodRetryBucketCollisionWarning("))
        utils_code = re.search(r"static const char hex_chars\[\][^;]+;", utils).group()
        utils_code += "\n" + "\n".join(body(utils, signature) for signature in (
            "static uint8_t hexVal(", "void Utils::toHex(", "bool Utils::isHexChar(",
            "bool Utils::fromHex(", "int Utils::parseTextParts("))
        setter = body(common, 'if (memcmp(config, "flood.retry.bucket ", 19) == 0)')
        legacy, count = re.subn(
            r"const char\* params = &config\[19\];[\s\S]+?(?=\{\s*sprintf\(reply, "
            r'"Error, usage: set flood.retry.bucket)',
            "const char* params = &config[19];\n"
            "    uint8_t bucket = atoi(params);\n"
            "    const char* list = strchr(params, ' ');\n"
            "    if (bucket < 1 || bucket > FLOOD_RETRY_BRIDGE_BUCKETS || list == NULL || *(list + 1) == 0) ",
            setter, count=1)
        if count != 1:
            raise AssertionError("Review the bucket-parser negative control after source changes")
        replacements = {
            "CONSTANTS": constants, "HELPERS": helpers, "UTILS": utils_code,
            "STRNCPY": body((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(encoding="ascii"),
                            "void StrHelper::strncpy("),
            "GET_BRANCH": body(common, 'if (memcmp(config, "flood.retry.bucket.", 19) == 0)'),
        }
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-flood-retry-bucket-")
        cls.binaries = {}
        for name, branch in (("production", setter), ("legacy", legacy)):
            generated = FIXTURE.replace("@SET_BRANCH@", branch)
            for key, value in replacements.items():
                generated = generated.replace("@" + key + "@", value)
            directory = Path(cls.work.name)
            path, binary = directory / (name + ".cpp"), directory / name
            path.write_text(generated, encoding="ascii")
            result = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", "-I" + str(ROOT / "src"),
                str(path), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, scenario, variant="production"):
        return subprocess.run([str(self.binaries[variant]), scenario], capture_output=True,
                              text=True, timeout=15,
                              env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0"))

    def assert_case(self, scenario):
        result = self.run_case(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: production flood retry bucket CLI", result.stdout)

    def test_valid_bucket_numbers_and_clear_aliases(self):
        self.assert_case("valid")

    def test_invalid_indices_never_change_preferences_or_save(self):
        self.assert_case("invalid")

    def test_prefix_validation_and_maximum_list_remain_intact(self):
        self.assert_case("prefixes")

    def test_cross_bucket_collision_warnings_remain_intact(self):
        self.assert_case("warnings")

    def test_getter_already_requires_a_strict_in_range_index(self):
        self.assert_case("getters")

    def test_previous_narrowing_and_suffix_parser_fail_rejection_checks(self):
        for token in ("257", "262", "-255", "1tail"):
            with self.subTest(token=token):
                result = self.run_case(token, "legacy")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Assertion", result.stderr)


if __name__ == "__main__":
    unittest.main()
