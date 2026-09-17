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

// Keep the decode buffer out of the parser's frame, including under LTO:
// unrelated terminal commands must not reserve another 2 KiB of stack.
__attribute__((noinline)) void printVerse(bible::Reference ref, Stream& output) {
  char scratch[bible::kBlockSize];
  const char* text = nullptr;
  const bible::LookupResult result = readReaderVerse(ref, scratch, sizeof(scratch), text);
  if (result == bible::LookupResult::Found) {
    output.printf("  Reader %u:%u (%s)\r\n", static_cast<unsigned>(ref.chapter),
                  static_cast<unsigned>(ref.verse), bible::generated::readerCorpus.translation);
    // Do not feed long text through Adafruit Print::printf's 256-byte scratch.
    output.print(text);
    output.print("\r\n");
    output.print(bible::generated::readerCorpus.attribution);
    output.print("\r\n");
  } else if (result == bible::LookupResult::Missing) {
    output.print("  This verse is not present in the supplied translation.\r\n");
  } else {
    output.print("  ERROR: invalid compressed Reader data\r\n");
  }
}

} // namespace

bool handleReaderCommand(const char* command, Stream& output) {
  bible::Reference ref = {};
  const bible::ParseResult parsed = bible::parse(command, ref);
  if (parsed == bible::ParseResult::NoMatch) return false;
  if (parsed == bible::ParseResult::Invalid) {
    output.print("  ERROR: use get reader <chapter>:<verse> (example: get reader 3:16)\r\n");
  } else {
    printVerse(ref, output);
  }
  return true;
}

} // namespace mesh
#endif
