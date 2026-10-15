#pragma once
#include <WiFi.h>
#include <errno.h>
#include <string.h>

static constexpr int MSG_DONTWAIT = 0x40;

inline int recv(int fd, void* destination, size_t length, int flags) {
  if (flags != MSG_DONTWAIT) { errno = EINVAL; return -1; }
  FakeSocket& socket = *fake::sockets.at(fd);
  ++socket.reads;
  if (socket.readError) { errno = socket.readError; return -1; }
  if (!socket.connected || (socket.eof && socket.input.empty())) return 0;
  if (socket.input.empty()) { errno = EAGAIN; return -1; }
  length = std::min(length, socket.input.size());
  for (size_t i = 0; i < length; ++i) {
    static_cast<uint8_t*>(destination)[i] = socket.input.front();
    socket.input.pop_front();
  }
  return static_cast<int>(length);
}

inline int send(int fd, const void* source, size_t length, int flags) {
  if (flags != MSG_DONTWAIT) { errno = EINVAL; return -1; }
  FakeSocket& socket = *fake::sockets.at(fd);
  ++socket.writes;
  if (socket.writeError) { errno = socket.writeError; return -1; }
  if (!socket.connected) { errno = EPIPE; return -1; }
  if (!socket.writeQuota) { errno = EAGAIN; return -1; }
  length = std::min(length, socket.writeQuota);
  const uint8_t* bytes = static_cast<const uint8_t*>(source);
  socket.output.insert(socket.output.end(), bytes, bytes + length);
  return static_cast<int>(length);
}
