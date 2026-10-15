#include <cassert>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/esp32/EthernetKissServer.h>

using namespace mesh::ethernet;

static std::vector<uint8_t> frame(size_t length = 244) {
  std::vector<uint8_t> data(length);
  for (size_t i = 0; i < length; ++i) data[i] = static_cast<uint8_t>(i);
  return data;
}

static std::vector<uint8_t> encoded(const std::vector<uint8_t>& data, uint8_t command = 0) {
  std::vector<uint8_t> output{Fend, command};
  for (const uint8_t byte : data) {
    if (byte == Fend || byte == Fesc) {
      output.push_back(Fesc);
      output.push_back(byte == Fend ? Tfend : Tfesc);
    } else output.push_back(byte);
  }
  output.push_back(Fend);
  return output;
}

static void feed(KissCodec& codec, const std::vector<uint8_t>& input) {
  for (uint8_t byte : input) assert(codec.feed(byte));
}

static void expect(KissCodec& codec, const std::vector<uint8_t>& expected) {
  const uint8_t* bytes;
  size_t length;
  assert(codec.peekTransmit(bytes, length));
  assert(length == expected.size() && !memcmp(bytes, expected.data(), length));
  codec.consumeTransmit();
}

static void append(int fd, const std::vector<uint8_t>& data) {
  auto& input = fake::sockets.at(fd)->input;
  input.insert(input.end(), data.begin(), data.end());
}

static int connect(KissServer& server) {
  server.loop(true);
  const int fd = fake::newSocket();
  server.loop(true);
  assert(server.status().connected);
  return fd;
}

static void codecRoundtrip() {
  for (size_t length = MinimumFrameSize; length <= MaxFrameSize; ++length) {
    const auto data = frame(length);
    const auto wire = encoded(data);
    for (size_t cut = 0; cut <= wire.size(); ++cut) {
      KissCodec codec;
      for (size_t i = 0; i < cut; ++i) assert(codec.feed(wire[i]));
      for (size_t i = cut; i < wire.size(); ++i) assert(codec.feed(wire[i]));
      expect(codec, data);
      assert(codec.receivedFrames() == 1);
    }
  }
  KissCodec codec;
  const std::vector<uint8_t> worst(MaxFrameSize, Fend);
  assert(codec.queueReceive(worst.data(), worst.size()));
  const uint8_t* bytes;
  size_t length;
  assert(codec.peekOutput(bytes, length) && length == MaxEncodedSize);
  assert(std::vector<uint8_t>(bytes, bytes + length) == encoded(worst));
  codec.consumeOutput(length);
  assert(codec.sentFrames() == 1);
}

static void codecMalformed() {
  KissCodec codec;
  feed(codec, {1, 2, 3, Fesc, Tfend});
  feed(codec, encoded(frame(), 0x10));  // Not port 0.
  feed(codec, encoded(frame(), 0x01));  // Not DATA.
  feed(codec, encoded(frame(13)));
  feed(codec, encoded(frame(256)));
  feed(codec, {Fend, 0, 1, Fesc, 42, 2, Fend});
  feed(codec, {Fend, 0, Fesc, Fend});
  assert(codec.pendingTransmit() == 0 && codec.malformedFrames() == 4);
  feed(codec, encoded(frame()));
  expect(codec, frame());
  assert(!codec.queueReceive(nullptr, 15));
  assert(!codec.queueReceive(frame(13).data(), 13));
  assert(!codec.queueReceive(frame(256).data(), 256));
}

static void codecBackpressure() {
  KissCodec codec;
  const auto data = frame();
  feed(codec, encoded(data));
  feed(codec, encoded(data));
  assert(!codec.canAcceptByte() && !codec.feed(0));
  expect(codec, data);
  assert(codec.canAcceptByte());
  feed(codec, encoded(data));
  expect(codec, data);
  expect(codec, data);
  assert(codec.queueReceive(data.data(), data.size()));
  assert(codec.queueReceive(data.data(), data.size()));
  assert(!codec.queueReceive(data.data(), data.size()));
  std::vector<uint8_t> actual;
  const uint8_t* bytes;
  size_t length;
  while (codec.peekOutput(bytes, length)) {
    const size_t count = length < 7 ? length : 7;
    actual.insert(actual.end(), bytes, bytes + count);
    codec.consumeOutput(count);
  }
  auto expected = encoded(data);
  const auto second = expected;
  expected.insert(expected.end(), second.begin(), second.end());
  assert(actual == expected && codec.sentFrames() == 2 && codec.droppedFrames() == 1);
}

static void codecResetFuzz() {
  KissCodec codec;
  auto data = frame();
  feed(codec, encoded(data));
  feed(codec, {Fend, 0, Fesc});
  assert(codec.queueReceive(data.data(), data.size()));
  codec.consumeOutput(5);
  codec.resetPending();
  assert(!codec.pendingReceive() && !codec.pendingTransmit());
  assert(codec.receivedFrames() == 1);
  feed(codec, {Tfend, 1, 2, 3, Fend});
  assert(!codec.pendingTransmit());
  uint32_t random = 0x12345678;
  for (unsigned i = 0; i < 100000; ++i) {
    random = random * 1664525U + 1013904223U;
    if ((random & 127) == 0) codec.resetPending();
    else if ((random & 7) == 0) codec.consumeTransmit();
    else codec.feed(static_cast<uint8_t>(random >> 24));
    assert(codec.pendingTransmit() <= QueueDepth);
  }
  codec.resetPending();
  feed(codec, encoded(data));
  expect(codec, data);
}

static void serverStation() {
  KissServer server;
  server.loop(false);
  assert(!fake::begins && !server.status().enabled);
  WiFi.mode = WIFI_AP;
  server.loop(true);
  assert(!fake::begins && !server.status().listening);
  WiFi.mode = WIFI_AP_STA;
  WiFi.address = 0;
  server.loop(true);
  assert(!fake::begins);
  WiFi.address = 0x0a020304;
  server.loop(true);
  assert(server.status().listening && fake::boundAddress == WiFi.address);
  assert(fake::boundPort == 5003);
  const int fd = fake::newSocket();
  server.loop(true);
  assert(server.status().accepted == 1);
  const int extra = fake::newSocket();
  server.loop(true);
  assert(!fake::sockets.at(extra)->connected && fake::sockets.at(fd)->connected);
  assert(server.status().rejected == 1);
  server.loop(false);
  assert(!fake::sockets.at(fd)->connected && !server.status().listening);
  assert(server.status().disconnects == 1);
}

static void serverBudgetBackpressure() {
  KissServer server;
  const int fd = connect(server);
  const auto data = frame(14);
  const auto wire = encoded(data);
  for (unsigned i = 0; i < 20; ++i) append(fd, wire);
  const auto socket = fake::sockets.at(fd);
  const size_t before = socket->input.size();
  server.loop(true);
  assert(server.status().pendingTransmit == 2);
  assert(before - socket->input.size() == wire.size() * 2);
  const size_t paused = socket->input.size();
  server.loop(true);
  assert(socket->input.size() == paused);
  const uint8_t* bytes;
  size_t length;
  assert(server.peekTransmit(bytes, length));
  assert(length == data.size() && !memcmp(bytes, data.data(), length));
  server.consumeTransmit();
  server.loop(true);
  assert(server.status().pendingTransmit == 2);
  const int oldPending = fake::newSocket();
  server.resetPending();
  assert(!socket->connected && server.status().pendingTransmit == 0);
  assert(!fake::sockets.at(oldPending)->connected && !server.status().listening);
  const int next = fake::newSocket();
  server.loop(true);
  const auto large = encoded(frame());
  append(next, large);
  server.loop(true);
  assert(fake::sockets.at(next)->reads == KissServer::ReadBudget + 1); // accept poll read + bounded poll
  assert(!server.status().pendingTransmit);
  server.loop(true);
  assert(server.status().pendingTransmit == 1);
}

static void serverPartialWrites() {
  KissServer server;
  const int fd = connect(server);
  const auto socket = fake::sockets.at(fd);
  const auto data = frame();
  assert(server.queueReceive(data.data(), data.size()));
  socket->writeQuota = 0;
  for (unsigned i = 0; i < 50; ++i) server.loop(true);
  assert(socket->output.empty() && server.status().pendingReceive == 1);
  socket->writeQuota = 3;
  while (server.status().pendingReceive) {
    const size_t before = socket->output.size();
    server.loop(true);
    assert(socket->output.size() - before <= 3);
  }
  assert(socket->output == encoded(data) && server.status().txFrames == 1);
  socket->writeQuota = 5000;
  assert(server.queueReceive(data.data(), data.size()));
  const size_t before = socket->output.size();
  server.loop(true);
  assert(socket->output.size() - before == KissServer::WriteBudget);
}

static void serverSessionReset() {
  KissServer server;
  int fd = connect(server);
  const auto data = frame(14);
  append(fd, encoded(data));
  append(fd, {Fend, 0, 1, Fesc});
  server.loop(true);
  assert(server.status().pendingTransmit == 1);
  assert(server.queueReceive(data.data(), data.size()));
  fake::sockets.at(fd)->writeQuota = 1;
  server.loop(true);
  fake::sockets.at(fd)->connected = false;
  const int next = fake::newSocket();
  server.loop(true);
  assert(server.status().connected && server.status().accepted == 2);
  assert(!server.status().pendingTransmit && !server.status().pendingReceive);
  assert(fake::sockets.at(next)->output.empty());
  append(next, {Tfend, 1, 2, Fend});
  server.loop(true);
  assert(!server.status().pendingTransmit);
  append(next, encoded(data));
  server.loop(true);
  assert(server.status().pendingTransmit == 1);
  WiFi.address += 1;
  server.loop(true);
  assert(!server.status().connected && !server.status().pendingTransmit);
  assert(!fake::sockets.at(next)->connected && fake::boundAddress == WiFi.address);
  fd = fake::newSocket();
  server.loop(true);
  append(fd, encoded(data));
  server.loop(true);
  WiFi.state = WL_DISCONNECTED;
  server.loop(true);
  assert(!server.status().listening && !server.status().connected);
  assert(!server.status().pendingTransmit);
}

static void serverFailures() {
  KissServer server;
  fake::listenFails = true;
  server.loop(true);
  assert(server.status().listenFailures == 1);
  for (unsigned i = 0; i < 100; ++i) server.loop(true);
  assert(fake::begins == 1);
  fake::now = 1000;
  server.loop(true);
  assert(fake::begins == 2);
  fake::listenFails = false;
  fake::now = 2000;
  server.loop(true);
  assert(server.status().listening);
  int fd = fake::newSocket();
  server.loop(true);
  fake::sockets.at(fd)->readError = EINTR;
  server.loop(true);
  assert(server.status().connected);
  fake::sockets.at(fd)->readError = ECONNRESET;
  server.loop(true);
  assert(!server.status().connected);
  fd = fake::newSocket();
  server.loop(true);
  const auto data = frame(14);
  assert(server.queueReceive(data.data(), data.size()));
  fake::sockets.at(fd)->writeError = EAGAIN;
  server.loop(true);
  assert(server.status().connected && server.status().pendingReceive == 1);
  fake::sockets.at(fd)->writeError = EPIPE;
  server.loop(true);
  assert(!server.status().connected && !server.status().pendingReceive);
  fd = fake::newSocket();
  server.loop(true);
  fake::sockets.at(fd)->eof = true;
  server.loop(true);
  assert(!server.status().connected);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  if (test == "codec-roundtrip") codecRoundtrip();
  else if (test == "codec-malformed") codecMalformed();
  else if (test == "codec-backpressure") codecBackpressure();
  else if (test == "codec-reset-fuzz") codecResetFuzz();
  else if (test == "server-station") serverStation();
  else if (test == "server-budget-backpressure") serverBudgetBackpressure();
  else if (test == "server-partial-writes") serverPartialWrites();
  else if (test == "server-session-reset") serverSessionReset();
  else if (test == "server-failures") serverFailures();
  else assert(false);
}
