#pragma once
#include "Reader.h"

namespace mesh { namespace bible {
// A verified replacement plus one previous record also works on SPIFFS,
// whose rename does not replace an existing destination. Never truncate the
// live bookmark. A reset between the two renames is recovered from .bak.
template <typename Files>
bool loadReaderBookmark(Files& files, Position& pos) {
  uint8_t data[kBookmarkBytes];
  pos = Position{};
  if (files.read("/reader.pos", data) && decodeBookmark(data, pos)) return true;
  return files.read("/reader.pos.bak", data) && decodeBookmark(data, pos);
}

template <typename Files>
bool saveReaderBookmark(Files& files, Position pos) {
  if (!pos.valid()) return false;
  if (pos.atStart()) {
    // Remove the fallback before the live record so a completed clear cannot
    // resurrect an earlier place on reboot. Do not create a start marker.
    return files.remove("/reader.pos.tmp") && files.remove("/reader.pos.bak")
        && files.remove("/reader.pos");
  }
  uint8_t data[kBookmarkBytes], verify[kBookmarkBytes];
  encodeBookmark(pos, data);
  if (!files.remove("/reader.pos.tmp") || !files.write("/reader.pos.tmp", data)) return false;
  if (!files.read("/reader.pos.tmp", verify) || memcmp(data, verify, sizeof(data)) != 0)
    return false;
  if (files.exists("/reader.pos")) {
    // Keep a valid fallback if the live record was interrupted/corrupt.
    Position old;
    // A failed read is not proof of corruption. Preserve the live record on
    // transient I/O failure rather than deleting it before publication.
    if (!files.read("/reader.pos", verify)) return false;
    if (decodeBookmark(verify, old)) {
      if (!files.remove("/reader.pos.bak") || !files.rename("/reader.pos", "/reader.pos.bak"))
        return false;
    } else if (!files.remove("/reader.pos")) return false;
  }
  if (!files.rename("/reader.pos.tmp", "/reader.pos")) return false;
  // Retain the last valid fallback until the next successful replacement.
  return true;
}
}} // namespace mesh::bible
