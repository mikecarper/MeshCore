#pragma once
#include "CompanionReaderConfig.h"

#if COMPANION_FEATURE_READER
#include "bible/ReaderLookup.h"
class Stream;
namespace mesh {
// Caller-owned scratch lets the terminal and screen share one flash corpus.
bible::LookupResult readReaderVerse(bible::Reference ref, char* scratch,
                                 size_t capacity, const char*& text);
// Terminal-only: verses can exceed the framed/remote CLI's 160-byte reply.
bool handleReaderCommand(const char* command, Stream& output);
}
#endif
