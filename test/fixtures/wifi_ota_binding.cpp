// Production identity, lifecycle and HTTP methods are inserted by the host test.
#include <cassert>
#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/esp32/WiFiOtaSession.h>
#include <openssl/evp.h>
class SHA256 {
  EVP_MD_CTX* context = EVP_MD_CTX_new();
public:
  SHA256() { reset(); }
  ~SHA256() { EVP_MD_CTX_free(context); }
  void reset() { assert(context && EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1); }
  void update(const void* bytes, size_t count) { assert(EVP_DigestUpdate(context, bytes, count) == 1); }
  void finalize(void* out, size_t count) {
    EVP_MD_CTX* copy = EVP_MD_CTX_new();
    assert(copy && EVP_MD_CTX_copy_ex(copy, context) == 1);
    uint8_t digest[32];
    unsigned length = 0;
    assert(EVP_DigestFinal_ex(copy, digest, &length) == 1 && length == sizeof(digest));
    EVP_MD_CTX_free(copy);
    assert(count <= sizeof(digest));
    memcpy(out, digest, count);
  }
};
#include <helpers/esp32/WiFiOtaStreamValidator.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)

constexpr int ESP_OK = 0, pdPASS = 1;
static uint32_t now_ms = 0;
uint32_t millis() { return now_ms; }
void delay(unsigned ms) { now_ms += ms; }
using TaskHandle_t = void*;
static bool allow_task = true;
int xTaskCreate(void (*)(void*), const char*, unsigned, void*, unsigned, TaskHandle_t* task) {
  *task = nullptr; return allow_task ? pdPASS : 0;
}
void vTaskDelete(void*) {}
static unsigned reboots = 0;
void esp_restart() { ++reboots; }
static unsigned nonce_fills = 0;
void esp_fill_random(void* out, size_t count) {
  ++nonce_fills;
  for (size_t i = 0; i < count; ++i) static_cast<uint8_t*>(out)[i] = i + nonce_fills;
}
static bool mac_valid = true;
int esp_efuse_mac_get_default(uint8_t* out) {
  const uint8_t mac[] = {0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x11};
  memcpy(out, mac, sizeof(mac));
  return mac_valid ? ESP_OK : -1;
}
struct esp_partition_t { uint32_t address, size; };
static bool active_valid = true, next_valid = true, same_partition = false;
const esp_partition_t* esp_ota_get_running_partition() {
  static esp_partition_t value{0x10000, 0x300000};
  return active_valid ? &value : nullptr;
}
const esp_partition_t* esp_ota_get_next_update_partition(void*) {
  static esp_partition_t value{0x340000, 0x300000};
  value.address = same_partition ? 0x10000 : 0x340000;
  return next_valid ? &value : nullptr;
}
namespace mesh { namespace ota {
struct SelfFwInfo { bool valid = false; char hw_id[33] = {}; };
static bool self_valid = true;
static const char* self_hardware = "heltec_v4";
bool ota_self_firmware(SelfFwInfo& out) {
  out.valid = self_valid;
  strncpy(out.hw_id, self_hardware, sizeof(out.hw_id) - 1);
  return self_valid;
}
} }
struct File {
  explicit operator bool() const { return false; }
  size_t size() const { return 0; }
  int available() const { return 0; }
  size_t read(uint8_t*, size_t) { return 0; }
  void close() {}
};
static int SPIFFS;
namespace mesh { File openFileRead(int*, const char*) { return {}; } }

struct WiFiClient {
  std::string incoming, outgoing;
  size_t position = 0, visible = SIZE_MAX;
  bool open = true;
  explicit WiFiClient(std::string input = {}) : incoming(std::move(input)) {}
  explicit operator bool() const { return !incoming.empty(); }
  bool connected() const { return open; }
  int available() const {
    const size_t end = std::min(incoming.size(), visible);
    return end > position ? static_cast<int>(end - position) : 0;
  }
  int read() {
    return available() ? static_cast<unsigned char>(incoming[position++]) : -1;
  }
  int read(uint8_t* out, size_t count) {
    count = std::min(count, static_cast<size_t>(available()));
    memcpy(out, incoming.data() + position, count);
    position += count;
    return static_cast<int>(count);
  }
  size_t write(const uint8_t* out, size_t count) {
    outgoing.append(reinterpret_cast<const char*>(out), count); return count;
  }
  void printf(const char* format, ...) {
    char out[1024];
    va_list args;
    va_start(args, format);
    const int count = vsnprintf(out, sizeof(out), format, args);
    va_end(args);
    assert(count >= 0 && static_cast<size_t>(count) < sizeof(out));
    outgoing.append(out, count);
  }
  void stop() { open = false; }
  void setTimeout(unsigned) {}
};
struct WiFiServer {
  explicit WiFiServer(unsigned) {}
  void begin() {}
  void stop() {}
  void setNoDelay(bool) {}
  WiFiClient available() { return WiFiClient(); }
};
static WiFiClient* reveal_trailing = nullptr;
struct UpdateFake {
  unsigned begins = 0, writes = 0, aborts = 0, commits = 0;
  size_t bytes = 0, expected = 0;
  bool begin(size_t size, int) { ++begins; expected = size; bytes = 0; return true; }
  const char* errorString() const { return "fixture"; }
  size_t write(const uint8_t*, size_t count) {
    ++writes; bytes += count;
    if (reveal_trailing) reveal_trailing->visible = SIZE_MAX;
    return count;
  }
  void abort() { ++aborts; }
  bool end() { ++commits; return bytes == expected; }
} Update;
constexpr int U_FLASH = 0;
struct { void softAPdisconnect(bool) {} } WiFi;
namespace mesh { namespace wifi {
void stopTemporaryAccessPointRadio(bool) {}
} }
static const uint8_t MESH_HTML_LIGHTWEIGHT_OTA[] = {'o', 'k'};
static constexpr size_t MESH_HTML_LIGHTWEIGHT_OTA_LEN = 2;
static constexpr unsigned LIGHTWEIGHT_OTA_PORT = 8080;
struct ESP32Board {
  void* ota_server = nullptr;
  mesh::wifi::OtaSessionIdentity ota_identity;
  bool inhibit_sleep = false;
  bool ota_started_ap = false, ota_started_radio = false;
  void setInhibitSleep(bool value) { inhibit_sleep = value; }
  bool getOTAIdentity(char*, size_t);
  bool stopOTAUpdate(char*);
};
@SERVER@;
static LightweightOTAServer lightweight_ota_server;
@IDENTITY@
@STOP@

static void put32(std::string& bytes, size_t offset, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) bytes[offset + i] = static_cast<char>(value >> (8 * i));
}
static std::string firmware(unsigned chip, const char* hardware, bool appended = true) {
  std::string image(32, 0);
  image[0] = static_cast<char>(0xe9); image[1] = 1; image[2] = 2;
  image[12] = chip; image[13] = chip >> 8; image[23] = appended ? 1 : 0;
  put32(image, 4, 0x40370000);
  put32(image, 24, 0x3fc90000);
  put32(image, 28, 128);
  uint8_t checksum = 0xef;
  for (unsigned i = 0; i < 128; ++i) { image += static_cast<char>(i); checksum ^= i; }
  const size_t checksum_offset = (image.size() / 16 + 1) * 16 - 1;
  image.resize(checksum_offset, 0);
  image += static_cast<char>(checksum);
  if (appended) {
    SHA256 sha; sha.update(image.data(), image.size());
    uint8_t digest[32]; sha.finalize(digest, sizeof(digest));
    image.append(reinterpret_cast<const char*>(digest), sizeof(digest));
  }
  const size_t body_length = image.size();
  SHA256 sha; sha.update(image.data(), image.size());
  uint8_t digest[32]; sha.finalize(digest, sizeof(digest));
  image.resize(body_length + 56, 0);
  memcpy(&image[body_length], "EndF", 4);
  put32(image, body_length + 4, body_length);
  memcpy(&image[body_length + 8], digest, 8);
  memcpy(&image[body_length + 24], hardware, strlen(hardware));
  return image;
}
static const std::string kFirmware = firmware(EXPECTED_CHIP_ID, "heltec_v4");
static std::string request(const std::string& headers, const std::string& body = kFirmware) {
  return "POST /update HTTP/1.1\r\nHost: mesh\r\n" + headers + "\r\n" + body;
}
static std::string goodHeaders(const ESP32Board& board, size_t bytes = kFirmware.size()) {
  return "Content-Type: application/octet-stream\r\nContent-Length: " + std::to_string(bytes) +
      "\r\nX-MeshCore-OTA-Session: " + board.ota_identity.session + "\r\n";
}
static void resetUpdate() { Update = UpdateFake(); reboots = 0; reveal_trailing = nullptr; }
int main() {
  ESP32Board board;
  char cli[160], json[256];
  assert(board.getOTAIdentity(cli, sizeof(cli)) && nonce_fills == 1);
  assert(board.ota_identity.chip == EXPECTED_CHIP_ID);
  const std::string first = cli;
  assert(first.find("OTA1 s=") == 0 && first.find(" d=02aabbccdd11 ") != std::string::npos);
  assert(first.find(" hw=heltec_v4") != std::string::npos);
  assert(first.find(" c=" + std::to_string(EXPECTED_CHIP_ID) + " ") != std::string::npos);
  assert(board.getOTAIdentity(cli, sizeof(cli)) && first == cli && nonce_fills == 1);
  assert(board.ota_identity.writeJson(json, sizeof(json)));
  assert(std::string(json).find("{\"version\":1,\"protocol\":\"lightweight\"") == 0);
  assert(std::string(json).find("\\\"") == std::string::npos);
  char small[12];
  assert(!board.getOTAIdentity(small, sizeof(small)) && small[11] == 0);
  assert(board.ota_identity.session[0] != 0); // A small reply does not invent a new session.
  for (int fault = 0; fault < 5; ++fault) {
    active_valid = fault != 0;
    next_valid = fault != 1;
    same_partition = fault == 2;
    mac_valid = fault != 3;
    mesh::ota::self_valid = fault != 4;
    assert(!board.getOTAIdentity(cli, sizeof(cli)));
  }
  active_valid = next_valid = mac_valid = mesh::ota::self_valid = true;
  same_partition = false;
  mesh::ota::self_hardware = "bad\"identity";
  assert(!board.getOTAIdentity(cli, sizeof(cli)));
  mesh::ota::self_hardware = "heltec_v4";
  assert(lightweight_ota_server.begin(&board));
  board.ota_server = &lightweight_ota_server;

  WiFiClient info("GET /ota/info HTTP/1.1\r\nHost: mesh\r\n\r\n");
  lightweight_ota_server.handleClient(info);
  assert(info.outgoing.find("200 OK") != std::string::npos &&
      info.outgoing.find(board.ota_identity.session) != std::string::npos);
  assert(nonce_fills == 1);
  const std::vector<std::string> rejected = {
    request("Content-Type: application/octet-stream\r\nContent-Length: 8\r\n"),
    request("Content-Type: application/octet-stream\r\nContent-Length: 8\r\nX-MeshCore-OTA-Session: 00000000000000000000000000000000\r\n"),
    request(goodHeaders(board) + "content-length: 8\r\n"),
    request(goodHeaders(board) + "Transfer-Encoding: chunked\r\n"),
    request(goodHeaders(board) + "Content-Encoding: gzip\r\n"),
    request(goodHeaders(board) + "Expect: 100-continue\r\n"),
    request(goodHeaders(board) + "X-MeshCore-OTA-Session: " + board.ota_identity.session + "\r\n"),
    request("Content-Type: application/octet-stream\r\nContent-Length: +8\r\nX-MeshCore-OTA-Session: " + std::string(board.ota_identity.session) + "\r\n"),
    request("Content-Type: application/octet-stream\r\nContent-Length: 8junk\r\nX-MeshCore-OTA-Session: " + std::string(board.ota_identity.session) + "\r\n"),
    request("Content-Type: application/octet-stream\r\nContent-Length: 999999999999999999999999999\r\n"),
    request(goodHeaders(board, 0)),
    request(goodHeaders(board, 0x300001)),
    request(goodHeaders(board), kFirmware + "EXTRA"),
    request(goodHeaders(board) + "Long: " + std::string(280, 'x') + "\r\n"),
    request(goodHeaders(board) + "Host: duplicate\r\n"),
    request(goodHeaders(board) + "X-Bad : value\r\n"),
    "POST /update HTTP/1.1\nHost: mesh\n\nfirmware",
  };
  for (const auto& bytes : rejected) {
    resetUpdate();
    WiFiClient client(bytes);
    lightweight_ota_server.handleClient(client);
    assert(Update.begins == 0 && Update.writes == 0 && Update.commits == 0 && reboots == 0);
    assert(client.outgoing.find("200 OK") == std::string::npos);
  }

  resetUpdate();
  WiFiClient good(request(goodHeaders(board)));
  lightweight_ota_server.handleClient(good);
  assert(Update.begins == 1 && Update.writes == 1 && Update.bytes == kFirmware.size() &&
      Update.commits == 1 && reboots == 1 && good.outgoing.find("200 OK") != std::string::npos);

  resetUpdate();
  WiFiClient truncated(request(goodHeaders(board), "short"));
  lightweight_ota_server.handleClient(truncated);
  assert(Update.begins == 1 && Update.aborts == 1 && Update.commits == 0 && reboots == 0);

  resetUpdate();
  WiFiClient extra(request(goodHeaders(board), kFirmware + "EXTRA"));
  extra.visible = extra.incoming.size() - 5;
  reveal_trailing = &extra;
  lightweight_ota_server.handleClient(extra);
  assert(Update.begins == 1 && Update.writes == 1 && Update.aborts == 1 &&
      Update.commits == 0 && reboots == 0);
  reveal_trailing = nullptr;

  std::vector<std::string> bad_images = {
    firmware(EXPECTED_CHIP_ID == 9 ? 5 : 9, "heltec_v4"),
    firmware(EXPECTED_CHIP_ID, "heltec_v4_tft"),
    kFirmware.substr(0, kFirmware.size() - 56), // No EndF.
    kFirmware.substr(0, kFirmware.size() - 1), // Truncated EndF.
  };
  auto hash_bad = kFirmware; hash_bad[hash_bad.size() - 48] ^= 1;
  bad_images.push_back(hash_bad);
  auto crc_bad = kFirmware; crc_bad[175] ^= 1;
  bad_images.push_back(crc_bad);
  auto app_hash_bad = kFirmware; app_hash_bad[176] ^= 1;
  bad_images.push_back(app_hash_bad);
  auto length_bad = kFirmware; put32(length_bad, length_bad.size() - 52, 123);
  bad_images.push_back(length_bad);
  for (const auto& image : bad_images) {
    resetUpdate();
    WiFiClient bad(request(goodHeaders(board, image.size()), image));
    lightweight_ota_server.handleClient(bad);
    assert(Update.commits == 0 && reboots == 0 && Update.aborts == 1);
    assert(bad.outgoing.find("200 OK") == std::string::npos);
  }
  // Header, segment, digest and footer boundaries can fall in any ATT/TCP chunk.
  for (const auto& image : {kFirmware, firmware(EXPECTED_CHIP_ID, "heltec_v4", false)}) {
    for (size_t chunk : {1u, 7u, 24u, 55u, 56u, 57u, 113u, 1024u}) {
      mesh::wifi::OtaStreamValidator<SHA256> parser(board.ota_identity, image.size());
      for (size_t pos = 0; pos < image.size(); pos += chunk) {
        const size_t count = std::min(chunk, image.size() - pos);
        assert(parser.update(reinterpret_cast<const uint8_t*>(image.data() + pos), count));
      }
      assert(parser.finish());
      assert(!parser.update(nullptr, 0) && !parser.finish());
    }
  }

  const std::string old_session = board.ota_identity.session;
  assert(board.stopOTAUpdate(cli));
  assert(!board.ota_identity.prepared() && !board.ota_server);
  assert(board.getOTAIdentity(cli, sizeof(cli)) && nonce_fills == 2 &&
      old_session != board.ota_identity.session);
  assert(lightweight_ota_server.begin(&board));
  board.ota_server = &lightweight_ota_server;
  resetUpdate();
  WiFiClient stale(request("Content-Type: application/octet-stream\r\nContent-Length: 8\r\nX-MeshCore-OTA-Session: " + old_session + "\r\n"));
  lightweight_ota_server.handleClient(stale);
  assert(Update.begins == 0 && Update.writes == 0 && reboots == 0);
  assert(board.stopOTAUpdate(cli));
  assert(!board.ota_identity.prepared());
  assert(board.getOTAIdentity(cli, sizeof(cli)) && nonce_fills == 3);
  assert(board.stopOTAUpdate(cli)); // Stop also clears a prepared, unstarted session.
  assert(!board.ota_identity.prepared());
}
