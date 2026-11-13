"""The partition migrator must retain SHA-256 error checking on both APIs."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MigrationSha256Test(unittest.TestCase):
    def test_mbedtls_2_and_3_status_returning_functions(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="mesh-migration-sha256-") as directory:
            base = Path(directory)
            headers = base / "mbedtls"
            headers.mkdir()
            (headers / "version.h").write_text("#define MBEDTLS_VERSION_NUMBER TEST_VERSION\n")
            (headers / "sha256.h").write_text("""\
#pragma once
#include <stddef.h>
struct mbedtls_sha256_context { int started; size_t updated; };
#if TEST_VERSION >= 0x03000000
#define starts mbedtls_sha256_starts
#define update mbedtls_sha256_update
#define finish mbedtls_sha256_finish
#else
#define starts mbedtls_sha256_starts_ret
#define update mbedtls_sha256_update_ret
#define finish mbedtls_sha256_finish_ret
#endif
inline int starts(mbedtls_sha256_context* ctx, int is224) {
  ctx->started = is224 + 1; return is224 == 0 ? 0 : -1;
}
inline int update(mbedtls_sha256_context* ctx, const unsigned char* input, size_t length) {
  if (!input || !length) return -2;
  ctx->updated += length; return 0;
}
inline int finish(mbedtls_sha256_context* ctx, unsigned char* output) {
  if (!output || !ctx->updated) return -3;
  output[0] = static_cast<unsigned char>(ctx->updated); return 0;
}
#undef starts
#undef update
#undef finish
""")
            source = base / "test.cpp"
            source.write_text("""\
#include <helpers/ESP32MigrationSha256.h>
int main() {
  namespace m = mesh::esp32_partition_migration;
  mbedtls_sha256_context ctx = {};
  const unsigned char data[] = {1, 2, 3};
  unsigned char digest[32] = {};
  if (m::sha256Starts(&ctx, 0) != 0 || ctx.started != 1) return 1;
  if (m::sha256Update(&ctx, data, sizeof(data)) != 0 || ctx.updated != 3) return 2;
  if (m::sha256Finish(&ctx, digest) != 0 || digest[0] != 3) return 3;
  if (m::sha256Starts(&ctx, 1) != -1) return 4;
  if (m::sha256Update(&ctx, nullptr, 0) != -2) return 5;
  if (m::sha256Finish(&ctx, nullptr) != -3) return 6;
  return 0;
}
""")
            for version in ("0x021C0000", "0x03000000"):
                with self.subTest(version=version):
                    binary = base / ("verify-" + version)
                    subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                    "-DTEST_VERSION=" + version, "-I", str(base),
                                    "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                                   check=True, capture_output=True, text=True, timeout=30)
                    subprocess.run([str(binary)], check=True, capture_output=True,
                                   text=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
