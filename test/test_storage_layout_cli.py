#!/usr/bin/env python3

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StorageLayoutCliTest(unittest.TestCase):
    def test_shared_matcher_formatter_and_host_fallback(self):
        source = r'''
#include <helpers/StorageLayout.h>
#include <string.h>

namespace mesh { class MainBoard {}; }

using mesh::cli::Esp32StorageLayoutWriter;
using mesh::cli::StorageLayoutGetMatch;
using mesh::cli::classifyStorageLayoutGet;

int main() {
  if (classifyStorageLayoutGet(nullptr) !=
      StorageLayoutGetMatch::NotMatched) return 1;
  if (classifyStorageLayoutGet("storage.layout") !=
      StorageLayoutGetMatch::Valid) return 2;
  if (classifyStorageLayoutGet("storage.layout extra") !=
      StorageLayoutGetMatch::InvalidArguments) return 3;
  if (classifyStorageLayoutGet("storage.layout\textra") !=
      StorageLayoutGetMatch::InvalidArguments) return 4;
  if (classifyStorageLayoutGet("storage.layoutx") !=
      StorageLayoutGetMatch::NotMatched) return 5;

  char layout[160];
  Esp32StorageLayoutWriter writer(layout, sizeof(layout), 8UL * 1024UL * 1024UL);
  if (!writer.append("nvs", 0x9000, 20UL * 1024UL, false)) return 6;
  if (!writer.append("app0", 0x10000, 1984UL * 1024UL, true)) return 7;
  writer.finish();
  if (strcmp(layout,
             "> int:esp32=8192K ext:none; nvs@0x9000+20K,app0*@0x10000+1984K") != 0)
    return 8;

  char empty[80];
  Esp32StorageLayoutWriter empty_writer(empty, sizeof(empty), 4UL * 1024UL * 1024UL);
  empty_writer.finish();
  if (strcmp(empty, "> int:esp32=4096K ext:none; partitions=none") != 0)
    return 9;

  char bounded[64];
  Esp32StorageLayoutWriter bounded_writer(
      bounded, sizeof(bounded), 4UL * 1024UL * 1024UL);
  if (!bounded_writer.append("nvs", 0x9000, 20UL * 1024UL, false)) return 10;
  if (bounded_writer.append("large_app", 0x10000, 1984UL * 1024UL, true)) return 11;
  bounded_writer.finish();
  if (!bounded_writer.truncated() || strstr(bounded, ",...") == nullptr) return 12;
  if (strlen(bounded) >= sizeof(bounded)) return 13;

  char bytes[24];
  mesh::cli::formatStorageBytes(bytes, sizeof(bytes), 1536);
  if (strcmp(bytes, "1.5 KiB") != 0) return 14;

  mesh::MainBoard board;
  char reply[80];
  if (!mesh::cli::handleStorageLayoutGet(
          "storage.layout", board, reply, sizeof(reply))) return 15;
  if (strcmp(reply,
             "Error: storage layout unsupported on this platform") != 0)
    return 16;
  if (!mesh::cli::handleStorageLayoutGet(
          "storage.layout bad", board, reply, sizeof(reply))) return 17;
  if (strcmp(reply, "Error: use get storage.layout") != 0) return 18;
  if (mesh::cli::handleStorageLayoutGet(
          "storage.layoutx", board, reply, sizeof(reply))) return 19;
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "storage_layout"
            compiled = subprocess.run(
                [
                    "c++",
                    "-std=c++11",
                    f"-I{ROOT / 'src'}",
                    "-x",
                    "c++",
                    "-",
                    str(ROOT / "src/helpers/StorageLayout.cpp"),
                    "-o",
                    str(executable),
                ],
                input=source,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_common_and_companion_use_one_platform_formatter(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        common_header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        formatter = (ROOT / "src/helpers/StorageLayout.cpp").read_text()

        stm32_include = formatter.index("#elif defined(STM32_PLATFORM)")
        stm32_include_end = formatter.index("#endif", stm32_include)
        stm32_headers = formatter[stm32_include:stm32_include_end]
        self.assertLess(
            stm32_headers.index("#include <Arduino.h>"),
            stm32_headers.index("#include <InternalFileSystem.h>"),
        )

        self.assertIn('#include "StorageLayout.h"', common)
        self.assertIn(
            "mesh::cli::handleStorageLayoutGet(config, *_board, reply, 160)",
            common,
        )
        self.assertNotIn("handleStorageLayoutGetCmd", common)
        self.assertNotIn("handleStorageLayoutGetCmd", common_header)
        self.assertNotIn("esp_partition_find", common)

        local_start = companion.index("bool MyMesh::handleLocalControlCommand(")
        local_end = companion.index(
            "\n#if COMPANION_FEATURE_TEMP_RADIO\nvoid MyMesh::serviceTempRadio()",
            local_start,
        )
        local = companion[local_start:local_end]
        self.assertRegex(
            local,
            re.compile(
                r'if \(strncmp\(command, "get ", 4\) == 0 &&\s+'
                r'mesh::cli::handleStorageLayoutGet\(command \+ 4, board, reply,\s+'
                r'reply_size\)\)',
                re.DOTALL,
            ),
        )
        self.assertIn(
            'terminalOutput().print("  get storage.layout\\r\\n");',
            companion,
        )

        # ESP-IDF discovery and every platform-specific result live only in the
        # shared formatter; neither role owns a second copy.
        self.assertEqual(formatter.count("esp_partition_find("), 1)
        self.assertEqual(formatter.count('> int:esp32=%luK ext:none; '), 0)
        writer = (ROOT / "src/helpers/StorageLayout.h").read_text()
        self.assertEqual(writer.count('> int:esp32=%luK ext:none; '), 1)

    def test_companion_availability_is_documented(self):
        availability = (
            ROOT / "docs/cli_command_availability.md"
        ).read_text()
        companion_start = availability.index("## Companion framed CLI")
        companion_end = availability.index("\n## nRF52", companion_start)
        companion_section = availability[companion_start:companion_end]
        self.assertIn("[`get storage.layout`]", companion_section)
        self.assertIn("Every Companion text terminal and command `0x42`", companion_section)


if __name__ == "__main__":
    unittest.main()
