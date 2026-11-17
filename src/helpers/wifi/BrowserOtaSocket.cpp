#include "BrowserOtaSocket.h"
#if defined(ESP32)
#include <mbedtls/base64.h>
#include <mbedtls/sha1.h>
#include <mbedtls/version.h>
#include <esp_heap_caps.h>
#include <lwip/sockets.h>
#include <errno.h>
#include <string.h>
#include <strings.h>
#include <new>

namespace {
constexpr char Origin[] = "https://mikecarper.github.io";
constexpr uint32_t HeaderTimeout = 5000, StallTimeout = 15000;

char* trim(char* text) {
  while (*text == ' ' || *text == '\t') ++text;
  size_t len = strlen(text);
  while (len && (text[len - 1] == ' ' || text[len - 1] == '\t')) text[--len] = 0;
  return text;
}

// One bounded nonblocking write; zero means retry in the next loop, -1 means
// the socket has failed. WiFiClient::write may block the radio loop for seconds.
int writeSome(WiFiClient& client, const uint8_t* bytes, size_t len) {
  int n = ::send(client.fd(), bytes, len, MSG_DONTWAIT);
  if (n >= 0) return n;
  return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ? 0 : -1;
}
}

BrowserOtaSocket* BrowserOtaSocket::create(SerialWifiInterface& source) {
  static_assert(sizeof(BrowserOtaSocket) <= 512, "Recheck browser gateway object budget");
  if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
      < sizeof(BrowserOtaSocket) + 32768) return nullptr;
  return new (std::nothrow) BrowserOtaSocket(source);
}

bool BrowserOtaSocket::ensureWorkspace() {
  if (work) return true;
  static_assert(sizeof(Workspace) <= 3072, "Recheck browser gateway admission budget");
  // Prefer external memory, with no unchecked fallback into internal RAM.
  work = static_cast<Workspace*>(heap_caps_calloc(1, sizeof(Workspace), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!work) {
    const uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    if (heap_caps_get_free_size(caps) < sizeof(Workspace) + 32768
        || heap_caps_get_largest_free_block(caps) < sizeof(Workspace)) return false;
    work = static_cast<Workspace*>(heap_caps_calloc(1, sizeof(Workspace), caps));
  }
  return work != nullptr;
}

void BrowserOtaSocket::releaseWorkspaceIfIdle() {
  if (!work || candidate_active || peer_active) return;
  volatile uint8_t* bytes = reinterpret_cast<volatile uint8_t*>(work);
  for (size_t i = 0; i < sizeof(Workspace); ++i) bytes[i] = 0;
  heap_caps_free(work); work = nullptr;
}

void BrowserOtaSocket::begin() {
  if (listening) return;
  if (attempted && (uint32_t)(millis() - last_attempt) < 5000) return;
  attempted = true; last_attempt = millis();
  server.begin();
  listening = static_cast<bool>(server);
  if (!listening) server.end();
}

void BrowserOtaSocket::closeCandidate() {
  if (candidate) candidate.stop();
  candidate = WiFiClient(); candidate_active = false; http_len = reply_len = reply_at = 0;
  accepted_at = 0; promote = false;
  if (reserved && !peer_active) {
    if (upstream) upstream.stop();
    companion.releaseLoopbackClient();
    reserved = false;
  }
  releaseWorkspaceIfIdle();
}

void BrowserOtaSocket::closePeer() {
  if (peer) peer.stop();
  if (upstream) upstream.stop();
  peer = WiFiClient(); upstream = WiFiClient();
  peer_active = false;
  if (reserved) companion.releaseLoopbackClient();
  reserved = false; fragment = false;
  rx_len = tx_len = tx_at = forward_at = forward_len = 0;
  frame_started = tx_started = 0;
  releaseWorkspaceIfIdle();
}

void BrowserOtaSocket::stop() {
  closeCandidate(); closePeer(); server.end(); listening = false; attempted = false;
}

void BrowserOtaSocket::loop() {
  if (listening && !static_cast<bool>(server)) { server.end(); listening = false; attempted = false; }
  if (!listening) { begin(); if (!listening) return; }
  if (!candidate_active) {
    candidate = server.available();
    if (candidate) {
      candidate_active = true;
      if (!ensureWorkspace()) { closeCandidate(); return; }
      accepted_at = millis(); http_len = reply_len = reply_at = 0;
    }
  }
  if (candidate_active) serviceCandidate();
  if (peer_active) servicePeer();
}

void BrowserOtaSocket::serviceCandidate() {
  if (!candidate.connected() || (uint32_t)(millis() - accepted_at) > HeaderTimeout) {
    closeCandidate(); return;
  }
  if (reply_len) {
    int n = writeSome(candidate, work->reply + reply_at, reply_len - reply_at);
    if (n < 0) { closeCandidate(); return; }
    reply_at += n;
    if (reply_at != reply_len) return;
    if (!promote) { closeCandidate(); return; }
    peer = candidate; peer_active = true; candidate = WiFiClient(); candidate_active = false;
    http_len = reply_len = reply_at = 0; promote = false; accepted_at = 0;
    return;
  }
  for (unsigned budget = 0; budget < 256 && candidate.available(); ++budget) {
    int byte = candidate.read();
    if (byte < 0 || http_len >= MaxHttp) { closeCandidate(); return; }
    work->http[http_len++] = (char)byte;
    if (http_len >= 4 && !memcmp(work->http + http_len - 4, "\r\n\r\n", 4)) {
      work->http[http_len] = 0; handleHttp(); return;
    }
  }
}

void BrowserOtaSocket::handleHttp() {
  char* save = nullptr;
  char* line = strtok_r(work->http, "\r\n", &save);
  const bool get_info = line && !strcmp(line, "GET /ota-info HTTP/1.1");
  const bool options = line && !strcmp(line, "OPTIONS /ota-info HTTP/1.1");
  const bool upgrade_path = line && !strcmp(line, "GET /ota HTTP/1.1");
  bool origin = false, origin_seen = false, upgrade = false, version = false;
  char key[40] = {};
  while ((line = strtok_r(nullptr, "\r\n", &save))) {
    char* colon = strchr(line, ':');
    if (!colon) { closeCandidate(); return; }
    *colon++ = 0; char* value = trim(colon);
    if (!strcasecmp(line, "Origin")) {
      if (origin_seen) { closeCandidate(); return; }
      origin_seen = true; origin = !strcmp(value, Origin);
    } else if (!strcasecmp(line, "Upgrade")) upgrade = !strcasecmp(value, "websocket");
    else if (!strcasecmp(line, "Sec-WebSocket-Version")) version = !strcmp(value, "13");
    else if (!strcasecmp(line, "Sec-WebSocket-Key")) {
      if (*key || strlen(value) >= sizeof(key)) { closeCandidate(); return; }
      strcpy(key, value);
    }
  }
  if (!origin || (!get_info && !options && !upgrade_path)) { closeCandidate(); return; }
  if (get_info || options) {
    const char* body = get_info ? "{\"meshcoreOta\":1}" : "";
    int n = snprintf((char*)work->reply, sizeof(work->reply),
        "HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: %s\r\n"
        "Access-Control-Allow-Methods: GET, OPTIONS\r\n"
        "Access-Control-Allow-Headers: *\r\nAccess-Control-Allow-Private-Network: true\r\n"
        "Cache-Control: no-store\r\nContent-Type: application/json\r\n"
        "Content-Length: %u\r\nConnection: close\r\n\r\n%s",
        Origin, (unsigned)strlen(body), body);
    if (n <= 0 || (size_t)n >= sizeof(work->reply)) { closeCandidate(); return; }
    reply_len = n; return; // No Companion client or session is touched.
  }
  uint8_t decoded[24]; size_t decoded_len = 0;
  if (!upgrade || !version || strlen(key) != 24 ||
      mbedtls_base64_decode(decoded, sizeof(decoded), &decoded_len,
                            (const unsigned char*)key, strlen(key)) || decoded_len != 16 ||
      peer_active || !companion.reserveLoopbackClient()) { closeCandidate(); return; }
  reserved = true;
  if (!upstream.connect(IPAddress(127, 0, 0, 1), 5000, 100)) { closeCandidate(); return; }
  char material[64];
  int material_len = snprintf(material, sizeof(material), "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key);
  uint8_t digest[20], encoded[32]; size_t encoded_len = 0;
  if (material_len <= 0 || (size_t)material_len >= sizeof(material) ||
#if MBEDTLS_VERSION_MAJOR >= 3
      mbedtls_sha1((const unsigned char*)material, material_len, digest) ||
#else
      mbedtls_sha1_ret((const unsigned char*)material, material_len, digest) ||
#endif
      mbedtls_base64_encode(encoded, sizeof(encoded), &encoded_len, digest, sizeof(digest)) ||
      encoded_len >= sizeof(encoded)) { closeCandidate(); return; }
  encoded[encoded_len] = 0;
  int n = snprintf((char*)work->reply, sizeof(work->reply),
      "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
      "Sec-WebSocket-Accept: %s\r\n\r\n", encoded);
  if (n <= 0 || (size_t)n >= sizeof(work->reply)) { closeCandidate(); return; }
  reply_len = n; promote = true;
}

bool BrowserOtaSocket::queueFrame(uint8_t opcode, const uint8_t* data, size_t len) {
  if (tx_len || len > MaxPayload) return false;
  work->tx[0] = 0x80 | opcode;
  size_t header = len < 126 ? 2 : 4;
  if (header == 2) work->tx[1] = len;
  else { work->tx[1] = 126; work->tx[2] = len >> 8; work->tx[3] = len; }
  if (len) memcpy(work->tx + header, data, len);
  tx_len = header + len; tx_at = 0; tx_started = millis();
  return true;
}

bool BrowserOtaSocket::parseFrame() {
  if (rx_len < 2) return true;
  uint8_t flags = work->rx[0], op = flags & 15;
  if ((flags & 0x70) || !(work->rx[1] & 0x80)) return false;
  size_t len = work->rx[1] & 127, head = 2;
  if (len == 127) return false;
  if (len == 126) {
    if (rx_len < 4) return true;
    len = ((size_t)work->rx[2] << 8) | work->rx[3]; head = 4;
    if (len < 126) return false;
  }
  if (len > MaxPayload || ((op & 8) && (len > 125 || !(flags & 0x80)))) return false;
  size_t total = head + 4 + len;
  if (rx_len < total) return true;
  const uint8_t* mask = work->rx + head;
  for (size_t i = 0; i < len; ++i) work->rx[head + 4 + i] ^= mask[i & 3];
  if (op == 8) return false;
  if (op == 9) {
    if (!queueFrame(10, work->rx + head + 4, len)) return false;
  } else if (op == 10) {
    // Pong does not alter an in-progress fragmented binary message.
  } else if (op == 2 || op == 0) {
    if ((op == 2 && fragment) || (op == 0 && !fragment)) return false;
    fragment = !(flags & 0x80);
    memmove(work->rx, work->rx + head + 4, len);
    forward_at = 0; forward_len = len;
  } else return false;
  rx_len = 0; frame_started = forward_len ? millis() : 0;
  return true;
}

void BrowserOtaSocket::servicePeer() {
  if (!peer.connected() || !upstream.connected()) { closePeer(); return; }
  const uint32_t now = millis();
  if (tx_len) {
    int n = writeSome(peer, work->tx + tx_at, tx_len - tx_at);
    if (n < 0 || (!n && (uint32_t)(now - tx_started) > StallTimeout)) { closePeer(); return; }
    if (n) { tx_at += n; tx_started = now; }
    if (tx_at == tx_len) tx_len = tx_at = 0;
  }
  if (forward_len) {
    int n = writeSome(upstream, work->rx + forward_at, forward_len - forward_at);
    if (n < 0 || (!n && (uint32_t)(now - frame_started) > StallTimeout)) { closePeer(); return; }
    if (n) { forward_at += n; frame_started = now; }
    if (forward_at == forward_len) forward_len = forward_at = frame_started = 0;
  }
  if (!forward_len && !tx_len) {
    for (unsigned budget = 0; budget < 256 && peer.available(); ++budget) {
      if (!rx_len) frame_started = now;
      int byte = peer.read();
      if (byte < 0 || rx_len >= sizeof(work->rx)) { closePeer(); return; }
      work->rx[rx_len++] = byte;
      if (!parseFrame()) { closePeer(); return; }
      if (forward_len || tx_len) break;
    }
  }
  if (rx_len && (uint32_t)(now - frame_started) > StallTimeout) { closePeer(); return; }
  if (!tx_len && upstream.available()) {
    uint8_t buffer[MaxPayload];
    int n = upstream.read(buffer, sizeof(buffer));
    if (n < 0 || (n > 0 && !queueFrame(2, buffer, n))) closePeer();
  }
}
#endif
