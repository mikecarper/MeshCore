"""Exercise bounded, random-access Reader blocks with the firmware inflater."""

from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]

TEST = r'''
#include "examples/companion_radio/BookPack.h"
#include <helpers/ota/tinf/tinf.h>
#include <cassert>
#include <fstream>
#include <iterator>
#include <vector>

using namespace mesh::books;
int main(int argc, char** argv) {
  assert(argc == 2);
  std::ifstream in(argv[1], std::ios::binary);
  std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(in), {}};
  auto read = [&](size_t offset, uint8_t* out, size_t count) {
    if (offset > bytes.size() || count > bytes.size() - offset) return false;
    memcpy(out, bytes.data() + offset, count);
    return true;
  };
  Book book;
  Chapter chapter;
  Passage passage;
  assert(header(read, bytes.size(), book));
  assert(!strcmp(book.title, "Notes") && book.chapters == 1 && book.passages == 2);
  assert(!header(read, bytes.size() - 1, book));
  assert(chapterAt(read, book, 0, chapter));
  assert(!chapterAt(read, book, 1, chapter));
  assert(!strcmp(chapter.title, "Maps") && chapter.count == 2 && chapter.first == 0);
  assert(passageAt(read, book, 1, passage));
  unsigned plain = passage.plain;
  char text[kPlain];
  assert(tinf_uncompress_exact(text, &plain, bytes.data() + passage.offset, passage.packed) == TINF_OK);
  assert(plain == passage.plain && !strcmp(text + passage.start, "Second."));
  assert(!passageAt(read, book, 2, passage));
  const uint8_t check[] = "123456789";
  assert(~crcStep(UINT32_MAX, check, 9) == 0xcbf43926U);
  bytes[110] = 0xff;  // Invalid payload pointer in passage 2.
  assert(!passageAt(read, book, 1, passage));
  bytes[12] = '\n';
  assert(!header(read, bytes.size(), book));
}
'''


class BookPackTest(unittest.TestCase):
    def test_index_and_bounded_inflate(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            plain = b"First.\0Second.\0"
            payload = zlib.compress(plain, wbits=-15)
            pack = directory / "notes.mrb"
            pack.write_bytes(
                struct.pack("<4sIHH48sI", b"MRB1", 120 + len(payload), 1, 2, b"Notes", 0)
                + struct.pack("<HH32s", 0, 2, b"Maps")
                + struct.pack("<IHHH", 120, len(payload), len(plain), 0)
                + struct.pack("<IHHH", 120, len(payload), len(plain), 7)
                + payload
            )
            cpp = directory / "check.cpp"
            cpp.write_text(TEST)
            core = directory / "core.o"
            check = directory / "check"
            subprocess.run([
                "cc", "-DCOMPANION_FEATURE_BOOKS=1", "-c",
                ROOT / "src/helpers/ota/OtaTinf.c", "-o", core,
            ], check=True)
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", ROOT,
                "-I", ROOT / "src", cpp, core, "-o", check,
            ], check=True)
            subprocess.run([check, pack], check=True)
