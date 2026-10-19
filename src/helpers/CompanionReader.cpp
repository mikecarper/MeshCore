#include "CompanionReader.h"

#if COMPANION_FEATURE_READER
#include <Arduino.h>
#include "bible/ReaderLookup.h"

#include "bible/ReaderData.generated.h"

namespace mesh {
bible::LookupResult readReaderVerse(bible::Reference ref, char* scratch,
                                 size_t capacity, const char*& text) {
  return bible::lookup(bible::generated::readerCorpus, ref, scratch, capacity, text);
}

namespace {

constexpr size_t kReplyPageTextBytes = 100;
constexpr const char* kUsage = "ERROR: use get reader <chapter>[:<verse>[-<last>]] [page]";

struct Selection {
  bible::Reference first;
  uint8_t last;
};

bible::ParseResult parseReaderRequest(const char* command, Selection& selection,
                                     uint8_t& page) {
  page = 0; // No page argument keeps text terminals' full-verse behavior.
  if (!command) return bible::ParseResult::NoMatch;
  bible::skipSpace(command);
  if (!bible::token(command, "get")) return bible::ParseResult::NoMatch;
  bible::skipSpace(command);
  if (!bible::token(command, "reader")) return bible::ParseResult::NoMatch;
  bible::skipSpace(command);
  bible::Reference& ref = selection.first;
  if (!bible::number(command, ref.chapter) || ref.chapter > sizeof(bible::kChapterVerses)) {
    return bible::ParseResult::Invalid;
  }
  ref.verse = 1;
  selection.last = bible::kChapterVerses[ref.chapter - 1];
  if (*command == ':') {
    ++command;
    if (!bible::number(command, ref.verse)) return bible::ParseResult::Invalid;
    selection.last = ref.verse;
    if (*command == '-') {
      ++command;
      if (!bible::number(command, selection.last)) return bible::ParseResult::Invalid;
    }
    if (ref.verse > selection.last || selection.last > bible::kChapterVerses[ref.chapter - 1])
      return bible::ParseResult::Invalid;
  }
  if (*command) {
    if (!bible::space(*command)) return bible::ParseResult::Invalid;
    bible::skipSpace(command);
    if (*command && !bible::number(command, page)) return bible::ParseResult::Invalid;
    bible::skipSpace(command);
  }
  return *command == 0 ? bible::ParseResult::Valid : bible::ParseResult::Invalid;
}

size_t nextReplyPage(const char* text, size_t start, size_t length, size_t limit) {
  size_t count = length - start;
  if (count > limit) {
    count = limit;
    // Keep the separating space in this page, so following every page
    // reconstructs the supplied verse exactly without lost or repeated words.
    for (size_t n = count; n > 0; --n) {
      if (text[start + n - 1] == ' ') {
        count = n;
        break;
      }
    }
  }
  return start + count;
}

void nextCommand(char* out, size_t capacity, Selection selection, uint8_t page) {
  if (selection.first.verse == selection.last) {
    snprintf(out, capacity, "\nNext: get reader %u:%u %u", selection.first.chapter,
             selection.first.verse, page);
  } else {
    snprintf(out, capacity, "\nNext: get reader %u:%u-%u %u", selection.first.chapter,
             selection.first.verse, selection.last, page);
  }
}

void formatVerseReply(Selection selection, uint8_t page, const char* text,
                      char* reply, size_t capacity) {
  const bible::Reference ref = selection.first;
  char next_command[48] = {};
  if (ref.verse < selection.last) {
    Selection remaining = selection;
    ++remaining.first.verse;
    nextCommand(next_command, sizeof(next_command), remaining, 1);
  }
  char header[40];
  snprintf(header, sizeof(header), "Reader %u:%u (WEB)\n",
           static_cast<unsigned>(ref.chapter), static_cast<unsigned>(ref.verse));
  const size_t length = strlen(text);
  const size_t header_len = strlen(header);
  if (header_len + length + strlen(next_command) < capacity) {
    if (page != 1) {
      snprintf(reply, capacity, "ERROR: Reader page must be 1-1");
    } else {
      memcpy(reply, header, header_len);
      memcpy(reply + header_len, text, length);
      strcpy(reply + header_len + length, next_command);
    }
    return;
  }

  unsigned parts = 0;
  size_t start = 0, end = 0;
  for (size_t offset = 0; offset < length;) {
    // A range needs a longer continuation command. Keep the tagged 157-byte
    // app reply budget, without changing single-verse page boundaries.
    const size_t next = nextReplyPage(text, offset, length,
        ref.verse < selection.last ? 80 : kReplyPageTextBytes);
    if (++parts == page) {
      start = offset;
      end = next;
    }
    offset = next;
  }
  if (page > parts) {
    snprintf(reply, capacity, "ERROR: Reader page must be 1-%u", parts);
    return;
  }
  snprintf(header, sizeof(header), "Reader %u:%u [%u/%u]\n",
           static_cast<unsigned>(ref.chapter), static_cast<unsigned>(ref.verse),
           static_cast<unsigned>(page), parts);
  if (page < parts) {
    nextCommand(next_command, sizeof(next_command), selection, page + 1);
  }
  const size_t prefix_len = strlen(header);
  const size_t text_len = end - start;
  const size_t suffix_len = strlen(next_command);
  if (prefix_len + text_len + suffix_len >= capacity) {
    snprintf(reply, capacity, "ERROR: Reader reply buffer too small");
    return;
  }
  memcpy(reply, header, prefix_len);
  memcpy(reply + prefix_len, text + start, text_len);
  memcpy(reply + prefix_len + text_len, next_command, suffix_len + 1);
}

// Only a recognized Reader request enters this 4 KiB decode frame. Normal
// framed commands must not reserve it, including with link-time optimization.
__attribute__((noinline)) void replyVerse(Selection selection, uint8_t page,
                                        char* reply, size_t capacity) {
  char scratch[bible::kBlockSize];
  const char* text = nullptr;
  const bible::LookupResult result = readReaderVerse(selection.first, scratch, sizeof(scratch), text);
  if (result == bible::LookupResult::Found) {
    formatVerseReply(selection, page, text, reply, capacity);
  } else if (result == bible::LookupResult::Missing) {
    snprintf(reply, capacity, "This verse is not present in the supplied translation.");
  } else {
    snprintf(reply, capacity, "ERROR: invalid compressed Reader data");
  }
}

__attribute__((noinline)) void printReplyPage(Selection selection, uint8_t page,
                                            Stream& output) {
  char reply[160];
  replyVerse(selection, page, reply, sizeof(reply));
  output.print("  ");
  output.print(reply);
  output.print("\r\n");
}

// Only a cursor is retained between loop calls: no chapter buffer or heap.
// A slow/unread USB endpoint must not overflow its fixed queue or hold the
// mesh loop waiting. A new command/session cancels the unfinished cursor.
struct PrintJob {
  Stream* output = nullptr;
  ReaderTryWrite try_write = nullptr;
  Selection selection = {};
  uint16_t offset = 0;
  uint8_t phase = 0;
  bool prompt = false;
} print_job;

bool writePart(const char* text) {
  const size_t remaining = strlen(text) - print_job.offset;
  const size_t count = remaining < 96 ? remaining : 96;
  const uint8_t* data = reinterpret_cast<const uint8_t*>(text + print_job.offset);
  const size_t written = print_job.try_write
      ? print_job.try_write(*print_job.output, data, count)
      : print_job.output->write(data, count);
  print_job.offset += written <= count ? written : count;
  if (written < remaining) return false;
  print_job.offset = 0;
  return true;
}

// Keep decode scratch off the ordinary command/loop frame, including LTO.
__attribute__((noinline)) bool writeVersePart() {
  char scratch[bible::kBlockSize];
  const char* text = nullptr;
  const bible::LookupResult result = readReaderVerse(
      print_job.selection.first, scratch, sizeof(scratch), text);
  if (result == bible::LookupResult::Missing)
    text = "This verse is not present in the supplied translation.";
  else if (result != bible::LookupResult::Found) {
    text = "ERROR: invalid compressed Reader data";
    print_job.selection.last = print_job.selection.first.verse;
  }
  return writePart(text);
}

} // namespace

void cancelReaderCommand(const Stream* output) {
  if (!output || output == print_job.output) print_job.output = nullptr;
}

bool readerCommandActive(const Stream* output) {
  return print_job.output && (!output || output == print_job.output);
}

void serviceReaderCommand() {
  if (!print_job.output) return;
  switch (print_job.phase) {
    case 0: {
      char header[48];
      snprintf(header, sizeof(header), "  Reader %u:%u (%s)\r\n",
          print_job.selection.first.chapter, print_job.selection.first.verse,
          bible::generated::readerCorpus.translation);
      if (writePart(header)) ++print_job.phase;
      break;
    }
    case 1:
      if (writeVersePart()) ++print_job.phase;
      break;
    case 2:
      if (writePart("\r\n")) {
        if (print_job.selection.first.verse < print_job.selection.last) {
          ++print_job.selection.first.verse;
          print_job.phase = 0;
        } else ++print_job.phase;
      }
      break;
    case 3:
      if (writePart(bible::generated::readerCorpus.attribution)) ++print_job.phase;
      break;
    default:
      if (writePart(print_job.prompt ? "\r\n> " : "\r\n")) cancelReaderCommand();
      break;
  }
}

bool handleReaderCommand(const char* command, Stream& output, bool prompt,
                         ReaderTryWrite try_write) {
  cancelReaderCommand();
  Selection selection = {};
  uint8_t page;
  const bible::ParseResult parsed = parseReaderRequest(command, selection, page);
  if (parsed == bible::ParseResult::NoMatch) return false;
  if (parsed == bible::ParseResult::Invalid) {
    output.print("  "); output.print(kUsage); output.print("\r\n");
  } else if (page != 0) {
    printReplyPage(selection, page, output);
  } else {
    print_job.selection = selection;
    print_job.offset = print_job.phase = 0;
    print_job.prompt = prompt;
    print_job.try_write = try_write;
    print_job.output = &output;
  }
  return true;
}

bool handleReaderReply(const char* command, char* reply, size_t capacity) {
  if (reply == nullptr || capacity == 0) return false;
  Selection selection = {};
  uint8_t page;
  const bible::ParseResult parsed = parseReaderRequest(command, selection, page);
  if (parsed == bible::ParseResult::NoMatch) return false;
  if (parsed == bible::ParseResult::Invalid) {
    snprintf(reply, capacity, "%s", kUsage);
  } else {
    replyVerse(selection, page == 0 ? 1 : page, reply, capacity);
  }
  return true;
}

} // namespace mesh
#endif
