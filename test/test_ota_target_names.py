"""All OTA names survive front coding; original Companion API stays unchanged."""
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools" / "mota"), str(ROOT / "scripts")]
import gen_targets

HEADER = ROOT / "src" / "helpers" / "ota" / "OtaTargets.h"
HARNESS = ROOT / "test" / "fixtures" / "ota_target_names" / "test.cpp"


class OtaTargetNameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = HEADER.read_text(encoding="utf-8")
        cls.rows = [(int(target, 16), name) for target, name in re.findall(
            r'\{ 0x([0-9a-fA-F]{8}), "([^"]+)" \},', cls.text)]

    def compile_and_lookup(self, flags, ids, header=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "target_names"
            if header is not None:
                alternate = Path(temp_dir) / "helpers/ota"
                alternate.mkdir(parents=True)
                (alternate / "OtaTargets.h").write_text(header, encoding="utf-8")
                (alternate / "tinf").mkdir()
                (alternate / "tinf/tinf.h").write_text(
                    (ROOT / "src/helpers/ota/tinf/tinf.h").read_text(), encoding="utf-8")
            decoder = Path(temp_dir) / "tinf.o"
            subprocess.run([
                os.environ.get("CC") or shutil.which("cc") or "gcc",
                "-Os", "-DMESHCORE_TINF_IMPLEMENTATION=1", "-c",
                str(ROOT / "src/helpers/ota/tinf/tinflate.c"), "-o", str(decoder),
            ], check=True)
            subprocess.run([
                "c++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                *flags, f"-I{temp_dir}", f"-I{ROOT / 'src'}", str(HARNESS), str(decoder), "-o", str(executable),
            ], check=True)
            result = subprocess.run(
                [str(executable)], input="\n".join(f"{target:08x}" for target in ids),
                text=True, capture_output=True, check=True)
            return result.stdout.splitlines()

    def test_generator_reproduces_checked_in_header_and_all_ids(self):
        self.assertGreater(len(self.rows), 600)
        self.assertEqual(self.rows, gen_targets.target_rows(name for _, name in self.rows))
        self.assertEqual(self.text, gen_targets.generate_header(self.rows))

    def test_zopfli_chunks_retain_every_name_and_have_bounded_output(self):
        data, offsets, raw_lengths, firsts = gen_targets.compressed_name_chunks(self.rows)
        actual = []
        for group, raw_length in enumerate(raw_lengths):
            raw = zlib.decompress(data[offsets[group]:offsets[group + 1]], -15)
            self.assertEqual(len(raw), raw_length)
            self.assertLessEqual(raw_length, 512)
            position = 0
            previous = b""
            for row in range(firsts[group], firsts[group + 1]):
                prefix = raw[position]
                self.assertLessEqual(prefix, len(previous))
                end = raw.index(0, position + 1)
                previous = previous[:prefix] + raw[position + 1:end]
                position = end + 1
                actual.append(previous.decode("utf-8"))
            self.assertEqual(position, len(raw))
        self.assertEqual(actual, [name for _, name in self.rows])
        original_size = sum(8 + len(name.encode("utf-8")) + 1 for _, name in self.rows)
        compressed_size = 4 * len(self.rows) + 2 * (len(offsets) + len(raw_lengths) + len(firsts)) + len(data)
        self.assertGreater(original_size - compressed_size, 19000)

    def test_generator_requires_google_zopfli_1000(self):
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / "scripts"))
        import zopfli_compress
        gen_targets._compressed_name_chunks.cache_clear()
        original = zopfli_compress.raw_deflate_compress
        with patch.object(zopfli_compress, "raw_deflate_compress", wraps=original) as compress:
            gen_targets.compressed_name_chunks([(1, "unique_test_target")])
            self.assertEqual(compress.call_count, 1)
            self.assertEqual(compress.call_args.kwargs, {"numiterations": 1000})

    def test_cpp_standard_and_compact_match_all_names(self):
        ids = [target for target, _ in self.rows] + [0, 0xffffffff]
        expected = [name for _, name in self.rows] + ["?", "?"]
        for flags in ([], ["-DOTA_TARGET_NAME_FRONT_CODED=1"],
                      ["-DOTA_TARGET_NAME_FRONT_CODED=1", "-DOTA_RAK15002_SD_STORE=1", "-DCOMPANION_RADIO_FULL=1"]):
            with self.subTest(flags=flags):
                self.assertEqual(self.compile_and_lookup(flags, ids), expected)

    def test_rak_full_lookup_fails_closed_without_heap_and_keeps_every_name(self):
        flags = ["-DOTA_TARGET_NAME_FRONT_CODED=1", "-DOTA_RAK15002_SD_STORE=1", "-DCOMPANION_RADIO_FULL=1"]
        data, offsets, lengths, firsts = gen_targets.compressed_name_chunks(
            self.rows, gen_targets.RAK_FULL_NAME_DECODE_CHUNK_SIZE)
        ordinary = gen_targets.compressed_name_chunks(self.rows)
        self.assertGreater(len(ordinary[0]) - len(data), 1500)
        self.assertLessEqual(max(lengths), 4096)
        self.assertEqual(firsts[-1], len(self.rows))
        self.assertEqual(offsets[-1], len(data))
        no_heap = '#include <stdlib.h>\n#define malloc(bytes) nullptr\n' + self.text
        self.assertEqual(self.compile_and_lookup(flags, [self.rows[0][0], 0], no_heap), ["?", "?"])

    def test_table_disabled_preserves_optional_local_name(self):
        local_id, local_name = self.rows[0]
        ids = [local_id, self.rows[1][0], 0]
        for compressed in (0, 1):
            flags = ["-DOTA_TARGET_NAME_TABLE=0", f"-DOTA_TARGET_NAME_FRONT_CODED={compressed}"]
            with self.subTest(compressed=compressed):
                self.assertEqual(self.compile_and_lookup(flags, ids), ["?", "?", "?"])
                self.assertEqual(self.compile_and_lookup(flags + [
                    f"-DOTA_LOCAL_TARGET_ID=0x{local_id:08x}",
                    f'-DOTA_LOCAL_TARGET_NAME="{local_name}"',
                ], ids), [local_name, "?", "?"])

    def test_cpp_decoder_fails_closed_on_invalid_records_and_trailing_bytes(self):
        from zopfli_compress import raw_deflate_compress
        rows = [(1, "hello")]
        header = gen_targets.generate_header(rows)
        original, _, _, _ = gen_targets.compressed_name_chunks(rows)
        invalid = [
            raw_deflate_compress(b"\xffhello\0", numiterations=1000),  # invalid restart prefix
            raw_deflate_compress(b"\0hello!", numiterations=1000),     # missing terminator
            original + b"\0",                                       # trailing whole byte
            original[:-1],                                           # truncated input
        ]
        for stream in invalid:
            altered = re.sub(
                r"(static const uint8_t NAMES\[\] = \{).*?(\n  \};)",
                lambda match: match[1] + "\n    " + ", ".join(f"0x{byte:02x}" for byte in stream) + "," + match[2],
                header, flags=re.S)
            altered = re.sub(
                r"(static const uint16_t OFFSETS\[\] = \{).*?(\n  \};)",
                lambda match: match[1] + f"\n    0, {len(stream)}," + match[2],
                altered, flags=re.S)
            with self.subTest(stream=stream.hex()):
                for flags in (["-DOTA_TARGET_NAME_FRONT_CODED=1"],
                              ["-DOTA_TARGET_NAME_FRONT_CODED=1", "-DOTA_RAK15002_SD_STORE=1", "-DCOMPANION_RADIO_FULL=1"]):
                    self.assertEqual(self.compile_and_lookup(flags, [1], altered), ["?"])

    def test_generator_rejects_unencodable_names(self):
        for name in ("x\0y", "x" * 256):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    gen_targets.compressed_name_chunks([(1, name)])


if __name__ == "__main__":
    unittest.main()
