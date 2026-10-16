#pragma once

#include <cerrno>
#include <cstring>

struct stat {};

inline int stat(const char* path, struct stat*) {
  if (!metadata_filesystem || std::strncmp(path, "/spiffs", 7) != 0) {
    errno = EIO;
    return -1;
  }
  const std::string relative(path + 7);
  if (metadata_filesystem->metadata_errors.count(relative)) {
    errno = EIO;
    return -1;
  }
  if (!metadata_filesystem->files.count(relative)) {
    errno = ENOENT;
    return -1;
  }
  return 0;
}
