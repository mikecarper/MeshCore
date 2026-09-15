#pragma once
#include <cerrno>
#include <cstring>
struct stat {};
inline int stat(const char* path, struct stat*) {
  if (!metadata_filesystem || metadata_filesystem->metadata_error) {
    errno = EIO;
    return -1;
  }
  if (strncmp(path, "/spiffs", 7) != 0) { errno = EINVAL; return -1; }
  if (!metadata_filesystem->files.count(path + 7)) { errno = ENOENT; return -1; }
  return 0;
}
