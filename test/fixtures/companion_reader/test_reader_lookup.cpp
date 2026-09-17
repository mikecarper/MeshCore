#include <Arduino.h>
#include <helpers/CompanionReader.h>
#include <helpers/bible/ReaderData.generated.h>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

using namespace mesh::bible;

int main(int argc, char**) {
  Reference ref = {};
  for (const char* cmd : {"get reader 3:16", " GET\treader 3:16 \t", "get reader 03:16"}) {
    assert(parse(cmd, ref) == ParseResult::Valid && ref.chapter == 3 && ref.verse == 16);
  }
  for (const char* cmd : {"get reader", "get reader 0:1", "get reader 22:1",
       "get reader 1:52", "get reader 3:0", "get reader -3:16", "get reader 3:16-18",
       "get reader 3:16 junk", "get reader 9999999999:1", "get reader 1:9999999999",
       "get reader 3:", "get reader 3:16\x1b", "get reader 3 :16", "get reader :16"}) {
    assert(parse(cmd, ref) == ParseResult::Invalid);
  }
  for (const char* cmd : {"get name", "forget reader 3:16", "get readerny 3:16",
       "get 1 reader 3:16", "getreader 3:16", "", "get J", "get", "g"}) {
    assert(parse(cmd, ref) == ParseResult::NoMatch);
    Stream output;
    assert(!mesh::handleReaderCommand(cmd, output) && output.text.empty());
  }
  assert(parse(nullptr, ref) == ParseResult::NoMatch);
  const Corpus& corpus = generated::readerCorpus;
  struct Guarded { uint32_t pre; char scratch[kBlockSize]; uint32_t post; } buf = {};
  buf.pre = 0x11223344; buf.post = 0xaabbccdd;
  unsigned count = 0;
  for (uint8_t chapter = 1; chapter <= 21; ++chapter) {
    for (uint8_t verse = 1; verse <= kChapterVerses[chapter - 1]; ++verse) {
      const char* text;
      assert(lookup(corpus, {chapter, verse}, buf.scratch, kBlockSize, text) == LookupResult::Found);
      assert(buf.pre == 0x11223344 && buf.post == 0xaabbccdd);
      char cmd[32];
      snprintf(cmd, sizeof(cmd), "get reader %u:%u", chapter, verse);
      Stream output;
      assert(mesh::handleReaderCommand(cmd, output));
      const std::string expected = "\r\n" + std::string(text) + "\r\n";
      assert(output.text.find(expected) != std::string::npos);
      for (const unsigned char* c = reinterpret_cast<const unsigned char*>(text); *c; ++c)
        assert(*c >= 32 && *c < 127);
      // A machine-readable dump lets Python compare every decoded byte to
      // the independently ASCII-normalized pinned plaintext source.
      if (argc > 1) std::printf("%u:%u\t%s\n", chapter, verse, text);
      ++count;
    }
  }
  assert(count == 879);
  const char* text;
  assert(lookup(corpus, {22, 1}, buf.scratch, kBlockSize, text) == LookupResult::Invalid);
  assert(lookup(corpus, {1, 1}, buf.scratch, kBlockSize - 1, text) == LookupResult::Corrupt);
  Corpus bad = corpus;
  bad.data_size = 0;
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  bad = corpus; bad.block_count = 0;
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  bad = corpus; bad.verse_count = 878;
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  assert(lookup(corpus, {1, 1}, nullptr, kBlockSize, text) == LookupResult::Corrupt);
  // One-byte stored DEFLATE block: distinguish an absent verse marker from
  // damaged text without a terminator, and never return an unsafe pointer.
  uint8_t stored[] = {0x01, 0x01, 0x00, 0xfe, 0xff, 0x00};
  const BlockIndex stored_index = {0, sizeof(stored), 1, 0};
  bad = corpus; bad.data = stored; bad.data_size = sizeof(stored);
  bad.blocks = &stored_index; bad.block_count = 1;
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Missing);
  assert(text == nullptr);
  stored[5] = 'x';
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  assert(text == nullptr);
  assert(lookup(bad, {1, 2}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  std::vector<BlockIndex> blocks(corpus.blocks, corpus.blocks + corpus.block_count);
  bad = corpus; bad.blocks = blocks.data();
  for (int mutation = 0; mutation != 5; ++mutation) {
    blocks[0] = corpus.blocks[0];
    switch (mutation) {
      case 0: blocks[0].plain = kBlockSize + 1; break;
      case 1: blocks[0].packed--; break;
      case 2: blocks[0].packed++; break; // Trailing bytes are rejected.
      case 3: blocks[0].offset = UINT32_MAX; break;
      case 4: blocks[0].first_verse = 1; break;
    }
    assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
    assert(text == nullptr && buf.pre == 0x11223344 && buf.post == 0xaabbccdd);
  }
  Stream invalid;
  assert(mesh::handleReaderCommand("get reader 3:99", invalid));
  assert(invalid.text.find("ERROR") != std::string::npos);
  if (argc == 1) std::puts("All 879 verses, terminal output, parser and corruption checks passed");
}
