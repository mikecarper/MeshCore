#include <Arduino.h>
#include <helpers/CompanionReader.h>
#include <helpers/bible/ReaderData.generated.h>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

using namespace mesh::bible;

template <typename Offset>
static void checkOffsetWidth(uint32_t offset) {
  static const uint8_t stored[] = {0x01, 0x02, 0x00, 0xfd, 0xff, 'x', 0x00};
  std::vector<uint8_t> data(offset + sizeof(stored));
  memcpy(data.data() + offset, stored, sizeof(stored));
  const BlockIndexT<Offset> index = {static_cast<Offset>(offset), sizeof(stored), 2, 0};
  CorpusT<Offset> corpus = {"test", "test", data.data(), uint32_t(data.size()), &index, 1, kVerseCount};
  char scratch[kBlockSize];
  const char* text;
  assert(lookup(corpus, {1, 1}, scratch, sizeof(scratch), text) == LookupResult::Found);
  assert(strcmp(text, "x") == 0);
  corpus.data_size--;
  assert(lookup(corpus, {1, 1}, scratch, sizeof(scratch), text) == LookupResult::Corrupt);
  assert(text == nullptr);
}

static void drain(Stream& output) {
  unsigned calls = 0;
  while (mesh::readerCommandActive()) {
    const size_t writes = output.writes, bytes = output.text.size();
    mesh::serviceReaderCommand();
    assert(output.writes <= writes + 1 && output.text.size() <= bytes + 96);
    assert(++calls < 20000);
  }
}

static void checkSelection(unsigned chapter, unsigned first, unsigned last, bool whole) {
  char command[64];
  if (whole) snprintf(command, sizeof(command), "get reader %u", chapter);
  else snprintf(command, sizeof(command), "get reader %u:%u-%u", chapter, first, last);
  std::string expected, terminal_expected;
  for (unsigned verse = first; verse <= last; ++verse) {
    char scratch[kBlockSize], header[48];
    const char* text;
    assert(mesh::readReaderVerse({uint8_t(chapter), uint8_t(verse)}, scratch,
                                sizeof(scratch), text) == LookupResult::Found);
    expected += text;
    snprintf(header, sizeof(header), "  Reader %u:%u (WEB)\r\n", chapter, verse);
    terminal_expected += std::string(header) + text + "\r\n";
  }
  terminal_expected += generated::readerCorpus.attribution;
  terminal_expected += "\r\n> ";
  Stream terminal;
  terminal.max_write = 7; // Genuine short writes, including header/newline cuts.
  assert(mesh::handleReaderCommand(command, terminal, true));
  assert(mesh::readerCommandActive(&terminal));
  for (unsigned tick = 0; mesh::readerCommandActive(); ++tick) {
    assert(tick < 20000);
    terminal.credit = tick % 3 == 0 ? 0 : 11; // Stops accepting bytes regularly.
    const size_t before = terminal.text.size();
    mesh::serviceReaderCommand();
    assert(terminal.text.size() - before <= (tick % 3 == 0 ? 0 : 7));
  }
  assert(terminal.text == terminal_expected);
  for (const size_t capacity : {size_t(160), size_t(157)}) {
    std::string next = command, reconstructed;
    for (unsigned replies = 0; ; ++replies) {
      assert(replies < 1000);
      char reply[162];
      memset(reply, '!', sizeof(reply));
      assert(mesh::handleReaderReply(next.c_str(), reply, capacity));
      assert(reply[capacity] == '!' && strlen(reply) < capacity);
      std::string page(reply);
      assert(page.find("Reader ") == 0);
      const size_t body = page.find('\n') + 1;
      const size_t continuation = page.find("\nNext: ");
      reconstructed += page.substr(body, continuation == std::string::npos
          ? continuation : continuation - body);
      if (continuation == std::string::npos) break;
      const std::string command_next = page.substr(continuation + 7);
      assert(command_next != next);
      next = command_next;
    }
    assert(reconstructed == expected);
  }
}

int main(int argc, char**) {
  static_assert(sizeof(BlockIndexT<uint16_t>) == 8, "halfword index alignment");
  static_assert(sizeof(BlockIndexT<uint32_t>) == 12, "wide index alignment");
  checkOffsetWidth<uint16_t>(UINT16_MAX);
  checkOffsetWidth<uint32_t>(uint32_t(UINT16_MAX) + 1);
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
  const auto& corpus = generated::readerCorpus;
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
      drain(output);
      const std::string expected = "\r\n" + std::string(text) + "\r\n";
      assert(output.text.find(expected) != std::string::npos);
      for (const unsigned char* c = reinterpret_cast<const unsigned char*>(text); *c; ++c)
        assert(*c >= 32 && *c < 127);
      // Real CLI replies must fit both the untagged and AA|-tagged budgets.
      // Following every advertised next command must reconstruct every byte,
      // including word separators, with no truncation or duplicated text.
      for (const size_t capacity : {size_t(160), size_t(157)}) {
        struct ReplyGuarded { uint32_t pre; char reply[160]; uint32_t post; } response;
        response.pre = 0x11223344; response.post = 0xaabbccdd;
        std::string reconstructed;
        unsigned page = 1;
        for (;;) {
          memset(response.reply, '!', sizeof(response.reply));
          assert(mesh::handleReaderReply(cmd, response.reply, capacity));
          assert(response.pre == 0x11223344 && response.post == 0xaabbccdd);
          assert(strlen(response.reply) < capacity);
          for (size_t i = capacity; i < sizeof(response.reply); ++i)
            assert(response.reply[i] == '!');
          const std::string reply = response.reply;
          for (const unsigned char c : reply) assert(c == '\n' || (c >= 32 && c < 127));
          const size_t body_start = reply.find('\n');
          assert(body_start != std::string::npos && reply.find("Reader ") == 0);
          const size_t next = reply.find("\nNext: ");
          reconstructed += reply.substr(body_start + 1,
              next == std::string::npos ? next : next - body_start - 1);
          if (next == std::string::npos) break;
          snprintf(cmd, sizeof(cmd), "get reader %u:%u %u", chapter, verse, ++page);
          assert(reply.substr(next + strlen("\nNext: ")) == cmd);
          assert(page < 32);
        }
        assert(reconstructed == text);
        snprintf(cmd, sizeof(cmd), "get reader %u:%u %u", chapter, verse, page + 1);
        assert(mesh::handleReaderReply(cmd, response.reply, capacity));
        assert(std::string(response.reply).find("ERROR: Reader page must be 1-") == 0);
        snprintf(cmd, sizeof(cmd), "get reader %u:%u", chapter, verse);
      }
      // A machine-readable dump lets Python compare every decoded byte to
      // the independently ASCII-normalized pinned plaintext source.
      if (argc > 1) std::printf("%u:%u\t%s\n", chapter, verse, text);
      ++count;
    }
  }
  assert(count == 879);
  for (unsigned chapter = 1; chapter <= 21; ++chapter)
    checkSelection(chapter, 1, kChapterVerses[chapter - 1], true);
  checkSelection(2, 2, 12, false);
  checkSelection(21, 16, 18, false); // Includes a long, multi-page verse.
  checkSelection(6, 1, 71, false); // Larger than the real 4 KiB USB queue.
  checkSelection(2, 25, 25, false);
  Stream cancelled, unrelated;
  assert(mesh::handleReaderCommand("get reader 6", cancelled));
  mesh::serviceReaderCommand();
  mesh::cancelReaderCommand(&unrelated);
  assert(mesh::readerCommandActive(&cancelled));
  mesh::cancelReaderCommand(&cancelled);
  const std::string before_cancel = cancelled.text;
  mesh::serviceReaderCommand();
  assert(cancelled.text == before_cancel);
  assert(mesh::handleReaderCommand("get reader 6", cancelled));
  assert(!mesh::handleReaderCommand("get name", unrelated));
  assert(!mesh::readerCommandActive());
  // TCP bypasses its blocking Stream::write using one socket attempt. Keep
  // the original owner for session cancellation, and retry zero/short writes.
  Stream socket_owner, socket_bytes, reference;
  assert(mesh::handleReaderCommand("get reader 2:2-12", reference, true));
  drain(reference);
  static Stream* callback_bytes = nullptr;
  callback_bytes = &socket_bytes;
  auto try_write = [](Stream& owner, const uint8_t* data, size_t size) -> size_t {
    assert(owner.writes == 0);
    return callback_bytes->write(data, size);
  };
  assert(mesh::handleReaderCommand("get reader 2:2-12", socket_owner, true, try_write));
  assert(mesh::readerCommandActive(&socket_owner));
  for (unsigned tick = 0; mesh::readerCommandActive(); ++tick) {
    assert(tick < 20000);
    socket_bytes.max_write = 5;
    socket_bytes.credit = tick % 3 == 0 ? 0 : 9;
    mesh::serviceReaderCommand();
  }
  assert(socket_owner.writes == 0 && socket_bytes.text == reference.text);
  assert(mesh::handleReaderCommand("get reader 6", socket_owner, false, try_write));
  mesh::cancelReaderCommand(&socket_owner);
  assert(!mesh::readerCommandActive());
  const char* text;
  assert(lookup(corpus, {22, 1}, buf.scratch, kBlockSize, text) == LookupResult::Invalid);
  assert(lookup(corpus, {1, 1}, buf.scratch, kBlockSize - 1, text) == LookupResult::Corrupt);
  auto bad = corpus;
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
  const generated::ReaderBlockIndex stored_index = {0, sizeof(stored), 1, 0};
  bad = corpus; bad.data = stored; bad.data_size = sizeof(stored);
  bad.blocks = &stored_index; bad.block_count = 1;
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Missing);
  assert(text == nullptr);
  stored[5] = 'x';
  assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  assert(text == nullptr);
  assert(lookup(bad, {1, 2}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
  std::vector<generated::ReaderBlockIndex> blocks(corpus.blocks, corpus.blocks + corpus.block_count);
  bad = corpus; bad.blocks = blocks.data();
  for (int mutation = 0; mutation != 5; ++mutation) {
    blocks[0] = corpus.blocks[0];
    switch (mutation) {
      case 0: blocks[0].plain = kBlockSize + 1; break;
      case 1: blocks[0].packed--; break;
      case 2: blocks[0].packed++; break; // Trailing bytes are rejected.
      case 3: blocks[0].offset = static_cast<decltype(blocks[0].offset)>(-1); break;
      case 4: blocks[0].first_verse = 1; break;
    }
    assert(lookup(bad, {1, 1}, buf.scratch, kBlockSize, text) == LookupResult::Corrupt);
    assert(text == nullptr && buf.pre == 0x11223344 && buf.post == 0xaabbccdd);
  }
  Stream invalid;
  assert(mesh::handleReaderCommand("get reader 3:99", invalid));
  assert(invalid.text.find("ERROR") != std::string::npos);
  for (const char* cmd : {"get reader", "get reader 3:16 0", "get reader 3:16 -1",
       "get reader 3:16 100", "get reader 3:16 99999999999", "get reader 3:16 1 junk",
       "get reader 3:16 1:2", "get reader 3:16 1\x1b", "get reader 22:1",
       "get reader 0", "get reader 22", "get reader 2:12-2", "get reader 2:0-12",
       "get reader 2:2-26", "get reader 2:2-99999999", "get reader 2:2-",
       "get reader 2:2-12-14", "get reader 2:2-12junk", "get reader 2x",
       "get reader 2:2--12", "get reader 2:2-3:12"}) {
    char reply[160];
    assert(mesh::handleReaderReply(cmd, reply, sizeof(reply)));
    assert(std::string(reply).find("ERROR: use get reader") == 0);
  }
  for (const char* cmd : {"get name", "get readerny 3:16", "forget reader 3:16", ""}) {
    char reply[160] = "untouched";
    assert(!mesh::handleReaderReply(cmd, reply, sizeof(reply)));
    assert(strcmp(reply, "untouched") == 0);
  }
  char tiny[2] = {'!', '!'};
  assert(!mesh::handleReaderReply("get reader 3:16", tiny, 0) && tiny[0] == '!');
  assert(!mesh::handleReaderReply("get reader 3:16", nullptr, 160));
  assert(mesh::handleReaderReply("get reader 21:17", tiny, 1));
  assert(tiny[0] == 0 && tiny[1] == '!');
  char reply[160];
  assert(mesh::handleReaderReply(" GET\treader 03:16 01 \t", reply, sizeof(reply)));
  assert(std::string(reply).find("For God so loved the world") != std::string::npos);
  assert(std::string(reply).find("eternal life.") != std::string::npos);
  assert(std::string(reply).find("Next:") == std::string::npos);
  assert(mesh::handleReaderReply("get reader 21:17 2", reply, sizeof(reply)));
  Stream page_output;
  assert(mesh::handleReaderCommand("get reader 21:17 2", page_output));
  assert(page_output.text == "  " + std::string(reply) + "\r\n");
  if (argc == 1) std::puts("All 879 verses, terminal output, parser and corruption checks passed");
}
