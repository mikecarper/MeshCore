#include <helpers/wifi/BrowserOtaSocket.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <esp_heap_caps.h>
#include <string>
#include <vector>

uint32_t mock_millis = 100;
std::deque<WiFiClient> WiFiServer::incoming;

class BrowserOtaSocketTest {
  static std::shared_ptr<MockSocket> makeSocket() {
    auto socket = std::make_shared<MockSocket>(IPAddress(192, 168, 1, 2));
    socket->write_limit = 1024;
    return socket;
  }
  static void request(BrowserOtaSocket& gateway, const char* text) {
    assert(gateway.ensureWorkspace());
    auto socket = makeSocket();
    gateway.candidate = WiFiClient(socket);
    gateway.candidate_active = true;
    gateway.http_len = strlen(text);
    assert(gateway.http_len < gateway.MaxHttp);
    memcpy(gateway.work->http, text, gateway.http_len + 1);
    gateway.handleHttp();
  }
  static std::vector<uint8_t> frame(uint8_t opcode, bool fin, const char* payload) {
    size_t len = strlen(payload); assert(len < 126);
    std::vector<uint8_t> data{uint8_t((fin ? 0x80 : 0) | opcode), uint8_t(0x80 | len), 1, 2, 3, 4};
    for (size_t i = 0; i < len; ++i) data.push_back(uint8_t(payload[i]) ^ data[2 + (i & 3)]);
    return data;
  }
public:
  static void run() {
    SerialWifiInterface source; source.begin(5000); source.enable();
    BrowserOtaSocket gateway(source);
    static_assert(sizeof(BrowserOtaSocket) <= 512, "Gateway idle storage grew");
    assert(!gateway.work);
    mock_heap_free = 32768;
    assert(!gateway.ensureWorkspace() && !gateway.work);
    mock_heap_free = 65536; mock_heap_largest = 1;
    assert(!gateway.ensureWorkspace() && !gateway.work);
    mock_heap_largest = 65536; mock_heap_failure = true;
    assert(!gateway.ensureWorkspace() && !gateway.work);
    mock_heap_failure = false;
    const char info[] = "GET /ota-info HTTP/1.1\r\nOrigin: https://mikecarper.github.io\r\n\r\n";
    const char options[] = "OPTIONS /ota-info HTTP/1.1\r\nOrigin: https://mikecarper.github.io\r\n\r\n";
    const char valid[] = "GET /ota HTTP/1.1\r\nOrigin: https://mikecarper.github.io\r\n"
      "Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n";
    const char wrong[] = "GET /ota HTTP/1.1\r\nOrigin: https://evil.example\r\n"
      "Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n";

    request(gateway, info);
    std::string reply((char*)gateway.work->reply, gateway.reply_len);
    assert(reply.find("{\"meshcoreOta\":1}") != std::string::npos);
    assert(reply.find("Access-Control-Allow-Origin: https://mikecarper.github.io") != std::string::npos);
    assert(!gateway.reserved && !gateway.upstream);
    gateway.closeCandidate(); request(gateway, options);
    reply.assign((char*)gateway.work->reply, gateway.reply_len);
    assert(reply.find("Access-Control-Allow-Private-Network: true") != std::string::npos);
    assert(!gateway.reserved && !gateway.upstream);
    gateway.closeCandidate(); request(gateway, wrong);
    assert(!gateway.candidate && !gateway.reserved);

    // A discovery scan while a WebSocket session exists never acquires or
    // releases the current Companion connection.
    auto peer_socket = makeSocket(); gateway.peer = WiFiClient(peer_socket); gateway.peer_active = true;
    request(gateway, info);
    assert(gateway.reply_len && gateway.peer.connected() && !gateway.reserved);
    gateway.closeCandidate(); gateway.closePeer();

    // An already queued TCP client blocks WS acquisition instead of being
    // replaced; a clean listener can be reserved exclusively.
    auto other = makeSocket(); WiFiServer::incoming.push_back(WiFiClient(other));
    request(gateway, valid);
    assert(!gateway.candidate && !gateway.reserved && other->connected);
    WiFiServer::incoming.clear(); request(gateway, valid);
    assert(gateway.reserved && gateway.promote && gateway.upstream.connected());
    reply.assign((char*)gateway.work->reply, gateway.reply_len);
    assert(reply.find("101 Switching Protocols") != std::string::npos);
    assert(reply.find("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") != std::string::npos);
    other = makeSocket(); WiFiServer::incoming.push_back(WiFiClient(other));
    uint8_t rejected[188] = {};
    assert(source.checkRecvFrame(rejected, sizeof(rejected)) == 0 && !other->connected);
    gateway.serviceCandidate(); // Completes the HTTP 101 without moving the owner.
    assert(gateway.peer_active && gateway.upstream.connected() && gateway.reserved);
    request(gateway, info); // The scanner runs concurrently without taking the owner.
    assert(gateway.reply_len && gateway.peer_active && gateway.upstream.connected() && gateway.reserved);
    gateway.closeCandidate();
    assert(gateway.peer_active && gateway.upstream.connected() && gateway.reserved);
    gateway.closePeer(); assert(!gateway.reserved);
    assert(!gateway.work);

    assert(gateway.ensureWorkspace());
    // Binary/continuation fragments become the original TCP byte stream.
    peer_socket = makeSocket(); auto upstream_socket = makeSocket();
    gateway.peer = WiFiClient(peer_socket); gateway.peer_active = true; gateway.upstream = WiFiClient(upstream_socket);
    auto first = frame(2, false, "ab"), second = frame(0, true, "cd");
    for (uint8_t b : first) peer_socket->received.push_back(b);
    for (uint8_t b : second) peer_socket->received.push_back(b);
    for (int i = 0; i < 8; ++i) gateway.servicePeer();
    assert(std::string(upstream_socket->sent.begin(), upstream_socket->sent.end()) == "abcd");
    assert(!gateway.fragment);

    // An unmasked frame is rejected; a browser cannot inject malformed data.
    peer_socket->received.push_back(0x82); peer_socket->received.push_back(1);
    gateway.servicePeer(); assert(!gateway.peer && !gateway.upstream);

    // A physical peer disconnect immediately releases the TCP reservation;
    // scanning cannot keep stale session state alive after a socket loss.
    request(gateway, valid); gateway.serviceCandidate();
    assert(gateway.peer_active && gateway.reserved);
    gateway.peer.stop();
    gateway.servicePeer();
    assert(!gateway.peer_active && !gateway.reserved);
    assert(!gateway.work && mock_heap_allocations == mock_heap_releases);
    puts("PASS: browser OTA WebSocket security and framing");
  }
};

int main() { BrowserOtaSocketTest::run(); }
