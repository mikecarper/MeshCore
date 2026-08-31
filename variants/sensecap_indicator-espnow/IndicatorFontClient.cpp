#include "IndicatorFontClient.h"

#include <Arduino.h>
#include <esp32-hal-psram.h>

#include "IndicatorFontRecoveryPolicy.h"

#ifdef INDICATOR_WIFI_FONT_RECOVERY
#include <atomic>
#include <helpers/IndicatorFontStageV2Protocol.h>
#include <helpers/UsbLogging.h>
#include <helpers/esp32/SntpOperationCoordinator.h>
#include <helpers/esp32/TlsClockValidity.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_sntp.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>
#include <strings.h>
#include <time.h>
#endif

namespace {

static const uint32_t FONT_UART_BAUD = 1000000;
static const int FONT_UART_RX = 20;
static const int FONT_UART_TX = 19;
static const size_t MAX_FONT_BYTES = 1536 * 1024;

#ifdef INDICATOR_WIFI_FONT_RECOVERY
// api.github.com's current all-ECC chain terminates at Sectigo Public Server
// Authentication Root E46. Trusting this P-384 root per client avoids both the
// process-global compressed-bundle state and the RSA-4096 allocation failures
// seen on Arduino-ESP32 2.x. Source-certificate SHA-256:
// c90f26f0fb1b4018b22227519b5ca2b53e2ca5b3be5cf18efe1bef47380c5383
// The self-signed root expires 2046-03-21.
static const char FONT_TLS_GITHUB_ROOT_CA[] PROGMEM = R"CERT(
-----BEGIN CERTIFICATE-----
MIICOjCCAcGgAwIBAgIQQvLM2htpN0RfFf51KBC49DAKBggqhkjOPQQDAzBfMQsw
CQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1T
ZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwHhcN
MjEwMzIyMDAwMDAwWhcNNDYwMzIxMjM1OTU5WjBfMQswCQYDVQQGEwJHQjEYMBYG
A1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1TZWN0aWdvIFB1YmxpYyBT
ZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwdjAQBgcqhkjOPQIBBgUrgQQA
IgNiAAR2+pmpbiDt+dd34wc7qNs9Xzjoq1WmVk/WSOrsfy2qw7LFeeyZYX8QeccC
WvkEN/U0NSt3zn8gj1KjAIns1aeibVvjS5KToID1AZTc8GgHHs3u/iVStSBDHBv+
6xnOQ6OjQjBAMB0GA1UdDgQWBBTRItpMWfFLXyY4qp3W7usNw/upYTAOBgNVHQ8B
Af8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAwNnADBkAjAn7qRa
qCG76UeXlImldCBteU/IvZNeWBj7LRoAasm4PdCkT0RHlAFWovgzJQxC36oCMB3q
4S6ILuH5px0CMk7yn2xVdOOurvulGu7t0vzCAxHrRVxgED1cf5kDW21USAGKcw==
-----END CERTIFICATE-----
)CERT";

static const uint8_t EXPECTED_SHA256[32] = {
    0x61, 0xbc, 0xe9, 0x66, 0x2d, 0xb3, 0x14, 0x05,
    0x4e, 0x7b, 0xcf, 0xaa, 0x26, 0x14, 0x7a, 0x28,
    0xad, 0x7b, 0x50, 0x0b, 0x51, 0xba, 0xac, 0x4c,
    0xae, 0x1c, 0xaa, 0xcc, 0xe9, 0x0b, 0x74, 0x21,
};
#endif

enum class FontInfoResult : uint8_t {
  Present,
  Missing,
  Unavailable,
};

struct LoadedFont {
  uint8_t* data = nullptr;
  size_t size = 0;
  FontInfoResult info = FontInfoResult::Unavailable;
  bool currentAsset = false;
};

uint32_t updateCrc32(uint32_t crc, const uint8_t* data, size_t size) {
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
    }
  }
  return crc;
}

bool readLine(HardwareSerial& serial, char* line, size_t capacity,
              uint32_t timeout_millis) {
  size_t length = 0;
  uint32_t started = millis();
  while (millis() - started < timeout_millis) {
    while (serial.available()) {
      int value = serial.read();
      if (value < 0) break;
      if (value == '\n') {
        line[length] = 0;
        return true;
      }
      if (value != '\r' && length + 1 < capacity) {
        line[length++] = (char)value;
      }
    }
    delay(1);
  }
  line[0] = 0;
  return false;
}

FontInfoResult parseInfo(const char* line, size_t& size, uint32_t& crc) {
  unsigned long parsed_size = 0;
  unsigned long parsed_crc = 0;
  if (sscanf(line, "MCFONT 1 %lu %lx", &parsed_size, &parsed_crc) == 2) {
    if (parsed_size < 64 || parsed_size > MAX_FONT_BYTES) {
      return FontInfoResult::Unavailable;
    }
    size = (size_t)parsed_size;
    crc = (uint32_t)parsed_crc;
    return FontInfoResult::Present;
  }

  int present = -1;
  if (sscanf(line, "MCFONT %d", &present) == 1 && present == 0) {
    return FontInfoResult::Missing;
  }
  return FontInfoResult::Unavailable;
}

FontInfoResult requestInfo(HardwareSerial& serial, size_t& size,
                           uint32_t& crc) {
  char line[64];
  for (int attempt = 0; attempt < 3; ++attempt) {
    while (serial.available()) serial.read();
    serial.print("MCFONT INFO\n");
    serial.flush();
    if (readLine(serial, line, sizeof(line), 1000)) {
      FontInfoResult result = parseInfo(line, size, crc);
      if (result != FontInfoResult::Unavailable) return result;
    }
    delay(100);
  }
  return FontInfoResult::Unavailable;
}

void synchronizeCommandParser(HardwareSerial& serial) {
  // End a command fragment left by an ESP/RP reset or an interrupted prior
  // transaction. The RP2040's parser answers the empty/partial line, which we
  // discard before sending a real command.
  serial.print('\n');
  serial.flush();
  delay(25);
  while (serial.available()) serial.read();
}

bool receiveFont(HardwareSerial& serial, uint8_t* data, size_t size,
                 uint32_t expected_crc, uint8_t digest[32]) {
  char line[64];
  serial.print("MCFONT GET\n");
  serial.flush();

  size_t response_size = 0;
  uint32_t response_crc = 0;
  if (!readLine(serial, line, sizeof(line), 1500)
      || parseInfo(line, response_size, response_crc) != FontInfoResult::Present
      || response_size != size
      || response_crc != expected_crc) {
    return false;
  }

#ifdef INDICATOR_WIFI_FONT_RECOVERY
  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  if (mbedtls_sha256_starts_ret(&sha, 0) != 0) {
    mbedtls_sha256_free(&sha);
    return false;
  }
#else
  (void)digest;
#endif

  size_t received = 0;
  uint32_t crc = 0xFFFFFFFFUL;
  uint32_t last_progress = millis();
#ifdef INDICATOR_WIFI_FONT_RECOVERY
  bool sha_ok = true;
#endif
  while (received < size && millis() - last_progress < 2000) {
    int available = serial.available();
    if (available <= 0) {
      delay(1);
      continue;
    }
    size_t count = (size_t)available;
    if (count > size - received) count = size - received;
    size_t actual = serial.readBytes(data + received, count);
    if (actual == 0) continue;
    crc = updateCrc32(crc, data + received, actual);
#ifdef INDICATOR_WIFI_FONT_RECOVERY
    if (mbedtls_sha256_update_ret(&sha, data + received, actual) != 0) {
      sha_ok = false;
      break;
    }
#endif
    received += actual;
    last_progress = millis();
  }
#ifdef INDICATOR_WIFI_FONT_RECOVERY
  if (sha_ok) sha_ok = mbedtls_sha256_finish_ret(&sha, digest) == 0;
  mbedtls_sha256_free(&sha);
  return sha_ok && received == size && ~crc == expected_crc;
#else
  return received == size && ~crc == expected_crc;
#endif
}

LoadedFont loadFromService() {
  LoadedFont loaded;
  if (!psramFound()) return loaded;

  Serial2.begin(FONT_UART_BAUD, SERIAL_8N1, FONT_UART_RX, FONT_UART_TX);
  delay(50);
  synchronizeCommandParser(Serial2);

  uint32_t expected_crc = 0;
  loaded.info = requestInfo(Serial2, loaded.size, expected_crc);
  if (loaded.info != FontInfoResult::Present) {
    Serial2.end();
    loaded.size = 0;
    return loaded;
  }

  loaded.data = (uint8_t*)ps_malloc(loaded.size);
  uint8_t digest[32] = {};
  if (loaded.data == nullptr
      || !receiveFont(Serial2, loaded.data, loaded.size, expected_crc, digest)) {
    free(loaded.data);
    loaded.data = nullptr;
    Serial2.end();
    return loaded;
  }
  Serial2.end();
#ifdef INDICATOR_WIFI_FONT_RECOVERY
  loaded.currentAsset = loaded.size == mesh::indicator_font::kAssetSize
      && expected_crc == mesh::indicator_font::kAssetCrc32
      && memcmp(digest, EXPECTED_SHA256, sizeof(digest)) == 0;
#endif
  return loaded;
}

#ifdef INDICATOR_WIFI_FONT_RECOVERY

FontInfoResult probeServiceInfo(size_t& size, uint32_t& crc) {
  size = 0;
  crc = 0;
  Serial2.begin(FONT_UART_BAUD, SERIAL_8N1, FONT_UART_RX, FONT_UART_TX);
  delay(50);
  synchronizeCommandParser(Serial2);
  const FontInfoResult result = requestInfo(Serial2, size, crc);
  Serial2.end();
  return result;
}

enum class RecoveryState : uint8_t {
  Dormant,
  ProbeWaiting,
  ProbeRunning,
  PostCommitProbeWaiting,
  PostCommitProbeRunning,
  Waiting,
  Running,
  Ready,
  Complete,
  Exhausted,
};

portMUX_TYPE recoveryMux = portMUX_INITIALIZER_UNLOCKED;
RecoveryState recoveryState = RecoveryState::Dormant;
mesh::indicator_font::RecoveryNeed recoveryNeed =
    mesh::indicator_font::RecoveryNeed::None;
uint8_t recoveryAttempts = 0;
uint8_t serviceProbeAttempts = 0;
uint8_t postCommitProbeAttempts = 0;
uint32_t recoveryNextAttempt = 0;
bool runtimeFontInstalled = false;
bool recoveryActivateLive = false;
uint8_t* recoveredFont = nullptr;
size_t recoveredFontSize = 0;
std::atomic<bool> fontNtpTimeReceived{false};
std::atomic<uint32_t> fontNtpProofMillis{0};
std::atomic<uint32_t> fontNtpProofGeneration{0};
std::atomic<uint32_t> fontNtpExpectedGeneration{0};
std::atomic<uint32_t> fontNtpOperationGeneration{0};

static constexpr uint32_t HTTP_LINE_TIMEOUT_MS = 15000UL;
static constexpr uint32_t HTTP_HEADER_TOTAL_TIMEOUT_MS = 30000UL;
// Stay below the RP2040's 10-second staged-write idle timeout so a network
// failure is recognized before that peer leaves binary receive mode.
static constexpr uint32_t DOWNLOAD_IDLE_TIMEOUT_MS = 8000UL;
static constexpr uint32_t DOWNLOAD_TOTAL_TIMEOUT_MS = 180000UL;
// A normal buffered download completes in a few seconds. Two bounded Range
// reconnects cover transient early closes without letting one recovery
// attempt consume an unbounded share of GitHub's anonymous API quota.
static constexpr uint8_t DOWNLOAD_MAX_RESUME_RECONNECTS = 2;
static constexpr size_t HTTP_ETAG_CAPACITY = 96;
// Count fields, not the terminating empty line. GitHub's normal response is
// already fairly header-heavy, so leave room for future extension fields.
static constexpr size_t HTTP_HEADER_LIMIT = 64;
// Retain only the prefix needed to classify a field, but never drain an
// unbounded wire line. The per-line deadline is also checked for every byte
// while an oversized extension field is being discarded.
static constexpr size_t HTTP_WIRE_LINE_LIMIT = 2048;
static constexpr uint32_t HTTP_RATE_LIMIT_MIN_DELAY_MS = 60000UL;
static constexpr uint32_t HTTP_RATE_LIMIT_FALLBACK_DELAY_MS = 900000UL;
// deadlineReached() uses signed wrap-safe subtraction, so custom delays must
// stay comfortably below INT32_MAX. Larger server delays stop retries for the
// rest of this boot instead.
static constexpr uint32_t HTTP_RATE_LIMIT_MAX_DELAY_MS = 86400000UL;
static constexpr uint32_t STAGE_ACK_TIMEOUT_MS = 5000UL;
static constexpr uint32_t STAGE_TOTAL_TIMEOUT_MS = 170000UL;
// Older RP2040 font services accept only the original unacknowledged stream.
// Leave enough receiver-idle time after each 512-byte burst for a 1 MHz SD
// write. Updated services use an ACK after every write and do not need this.
static constexpr uint32_t LEGACY_STAGE_PACE_MS = 20UL;
// Arduino-ESP32 2.x mbedTLS allocates two 16 KiB record buffers from internal
// RAM.  A 24 KiB worker stack left the Indicator's largest block too small and
// made every handshake fail with MBEDTLS_ERR_SSL_ALLOC_FAILED.  The normal
// Arduino loop stack is 8 KiB; this worker has the same bounded call depth.
static constexpr uint32_t RECOVERY_TASK_STACK_BYTES = 8192UL;
// The longest HTTP body attempt is three minutes. Leave bounded headroom for
// the initial and one deadline-edge TLS/header exchange, but never let a proof
// become an unbounded process-lifetime boolean.
static constexpr uint32_t FONT_TLS_PROOF_MAX_AGE_MS = 300000UL;

#define FONT_RECOVERY_LOG(...)                                                \
  do {                                                                        \
    if (mesh::isUsbLoggingEnabled()) {                                        \
      mesh::usbLoggingPort().printf("Indicator font: " __VA_ARGS__);          \
    }                                                                         \
  } while (0)

void clearFontNtpCallback() {
  // This hook runs while the coordinator still owns the process-global SNTP
  // slot, so it cannot clear a newer feature's callback.
  fontNtpOperationGeneration.store(0, std::memory_order_release);
  sntp_set_time_sync_notification_cb(nullptr);
}

void noteFontNtpTime(struct timeval* value) {
  const uint32_t generation =
      fontNtpOperationGeneration.load(std::memory_order_acquire);
  if (!mesh::sntp_coord::processWideCoordinator().owns(generation)) return;
  if (value != nullptr
      && value->tv_sec >= (time_t)mesh::indicator_font::kAssetPublishedEpoch) {
    fontNtpProofMillis.store(millis(), std::memory_order_release);
    fontNtpProofGeneration.store(generation, std::memory_order_release);
    fontNtpTimeReceived.store(true, std::memory_order_release);
  }
}

bool fontTlsClockProofValid(time_t now) {
  const bool fresh = fontNtpTimeReceived.load(std::memory_order_acquire);
  const uint32_t proven_at =
      fontNtpProofMillis.load(std::memory_order_acquire);
  const uint32_t proof_generation =
      fontNtpProofGeneration.load(std::memory_order_acquire);
  const uint32_t expected_generation =
      fontNtpExpectedGeneration.load(std::memory_order_acquire);
  return mesh::tls_clock::proofGenerationIsValid(
             fresh, proof_generation, expected_generation)
      && mesh::tls_clock::proofIsValid(
             fresh, WiFi.status() == WL_CONNECTED, now)
      && mesh::tls_clock::proofAgeIsValid(
             fresh, millis(), proven_at, FONT_TLS_PROOF_MAX_AGE_MS);
}

bool prepareTlsClock() {
  if (WiFi.status() != WL_CONNECTED) {
    FONT_RECOVERY_LOG("NTP skipped; WiFi is disconnected\n");
    return false;
  }

  mesh::sntp_coord::OperationLease sntp_operation(
      mesh::sntp_coord::processWideCoordinator(), clearFontNtpCallback);
  if (!sntp_operation.tryAcquire()) {
    FONT_RECOVERY_LOG("NTP busy in another firmware service\n");
    return false;
  }

  // A plausible retained RTC or mesh timestamp is not enough for this path.
  // Observe a fresh SNTP response in this recovery attempt before opening the
  // HTTPS socket. NTP is not a content trust anchor: the CA bundle and the
  // compiled asset SHA-256 remain mandatory below.
  fontNtpTimeReceived.store(false, std::memory_order_release);
  fontNtpProofMillis.store(0, std::memory_order_release);
  fontNtpProofGeneration.store(0, std::memory_order_release);
  fontNtpExpectedGeneration.store(
      sntp_operation.generation(), std::memory_order_release);
  fontNtpOperationGeneration.store(
      sntp_operation.generation(), std::memory_order_release);
  sntp_set_time_sync_notification_cb(noteFontNtpTime);
  sntp_set_sync_status(SNTP_SYNC_STATUS_RESET);
  FONT_RECOVERY_LOG("requesting fresh NTP time before download\n");
  configTime(0, 0, "time.cloudflare.com", "time.google.com", "pool.ntp.org");
  const uint32_t started = millis();
  while (millis() - started < mesh::indicator_font::kNtpSyncWaitMillis) {
    if (WiFi.status() != WL_CONNECTED) {
      FONT_RECOVERY_LOG("NTP aborted after %lu ms; WiFi disconnected\n",
                        (unsigned long)(millis() - started));
      return false;
    }
    const time_t now = time(nullptr);
    if (fontTlsClockProofValid(now)
        && now >= (time_t)mesh::indicator_font::kAssetPublishedEpoch) {
      FONT_RECOVERY_LOG("fresh NTP time %lu received in %lu ms\n",
                        (unsigned long)now,
                        (unsigned long)(millis() - started));
      return true;
    }
    delay(100);
  }
  FONT_RECOVERY_LOG("NTP timed out after %lu ms (clock %lu)\n",
                    (unsigned long)(millis() - started),
                    (unsigned long)time(nullptr));
  return false;
}

uint32_t remainingHttpHeaderTimeout(uint32_t started) {
  const uint32_t elapsed = millis() - started;
  if (elapsed >= HTTP_HEADER_TOTAL_TIMEOUT_MS) return 0;
  const uint32_t remaining = HTTP_HEADER_TOTAL_TIMEOUT_MS - elapsed;
  return remaining < HTTP_LINE_TIMEOUT_MS ? remaining : HTTP_LINE_TIMEOUT_MS;
}

void armRecovery(mesh::indicator_font::RecoveryNeed need,
                 bool activateLive = false) {
  if (!mesh::indicator_font::shouldFetch(need)) return;
  portENTER_CRITICAL(&recoveryMux);
  recoveryNeed = need;
  recoveryAttempts = 0;
  postCommitProbeAttempts = 0;
  recoveryNextAttempt = 0;
  recoveryActivateLive = activateLive
      || mesh::indicator_font::shouldActivateLive(need);
  recoveryState = RecoveryState::Waiting;
  portEXIT_CRITICAL(&recoveryMux);
}

void armServiceProbe() {
  portENTER_CRITICAL(&recoveryMux);
  recoveryNeed = mesh::indicator_font::RecoveryNeed::None;
  recoveryAttempts = 0;
  serviceProbeAttempts = 0;
  postCommitProbeAttempts = 0;
  recoveryNextAttempt = 0;
  recoveryActivateLive = false;
  recoveryState = RecoveryState::ProbeWaiting;
  portEXIT_CRITICAL(&recoveryMux);
}

void armPostCommitProbe(bool activateLive) {
  portENTER_CRITICAL(&recoveryMux);
  // COMMIT has already accepted the integrity-checked local pair. Preserve
  // the network-attempt count and move to a disjoint local-only budget so a
  // transient INFO/GET failure cannot launch the same HTTPS download again.
  recoveryNeed = mesh::indicator_font::RecoveryNeed::None;
  postCommitProbeAttempts = 0;
  recoveryNextAttempt = 0;
  recoveryActivateLive = activateLive;
  recoveryState = RecoveryState::PostCommitProbeWaiting;
  portEXIT_CRITICAL(&recoveryMux);
}

enum class HttpLineResult : uint8_t {
  Complete,
  Overflow,
  TooLong,
  Failed,
};

HttpLineResult readHttpLine(Client& client, char* line, size_t capacity,
                            uint32_t timeout_millis) {
  if (capacity == 0) return HttpLineResult::Failed;
  size_t length = 0;
  size_t wire_length = 0;
  bool overflow = false;
  uint32_t started = millis();
  while (millis() - started < timeout_millis) {
    while (client.available()) {
      // client.available() can remain nonzero while a peer streams an
      // attacker-controlled line. Enforce the deadline inside the drain loop,
      // not just while waiting for another byte.
      if (millis() - started >= timeout_millis) {
        line[0] = 0;
        return HttpLineResult::Failed;
      }
      int value = client.read();
      if (value < 0) break;
      if (++wire_length > HTTP_WIRE_LINE_LIMIT) {
        line[0] = 0;
        return HttpLineResult::TooLong;
      }
      if (value == '\n') {
        if (length && line[length - 1] == '\r') --length;
        line[length] = 0;
        return overflow ? HttpLineResult::Overflow
                        : HttpLineResult::Complete;
      }
      if (length + 1 < capacity) {
        line[length++] = (char)value;
      } else {
        overflow = true;
      }
    }
    if (!client.connected() && !client.available()) break;
    delay(1);
  }
  if (capacity) line[0] = 0;
  return HttpLineResult::Failed;
}

bool isSecurityCriticalHttpHeader(const char* line) {
  return strncasecmp(line, "Content-Length:", 15) == 0
      || strncasecmp(line, "Transfer-Encoding:", 18) == 0
      || strncasecmp(line, "Content-Encoding:", 17) == 0
      || strncasecmp(line, "Content-Range:", 14) == 0
      || strncasecmp(line, "ETag:", 5) == 0
      || strncasecmp(line, "Retry-After:", 12) == 0
      || strncasecmp(line, "X-RateLimit-Reset:", 18) == 0
      || line[0] == ' ' || line[0] == '\t';
}

bool parseUnsignedDecimalHeader(const char* value, uint64_t& parsed) {
  while (*value == ' ' || *value == '\t') ++value;
  if (*value < '0' || *value > '9') return false;

  uint64_t result = 0;
  do {
    const uint8_t digit = (uint8_t)(*value - '0');
    if (result > (UINT64_MAX - digit) / 10U) return false;
    result = result * 10U + digit;
    ++value;
  } while (*value >= '0' && *value <= '9');

  while (*value == ' ' || *value == '\t') ++value;
  if (*value != 0) return false;
  parsed = result;
  return true;
}

bool parseDecimalToken(const char*& value, uint64_t& parsed) {
  if (*value < '0' || *value > '9') return false;
  uint64_t result = 0;
  do {
    const uint8_t digit = (uint8_t)(*value - '0');
    if (result > (UINT64_MAX - digit) / 10U) return false;
    result = result * 10U + digit;
    ++value;
  } while (*value >= '0' && *value <= '9');
  parsed = result;
  return true;
}

bool parseContentRangeHeader(const char* value, size_t& first, size_t& last,
                             size_t& total) {
  while (*value == ' ' || *value == '\t') ++value;
  if (strncasecmp(value, "bytes ", 6) != 0) return false;
  value += 6;

  uint64_t parsed_first = 0;
  uint64_t parsed_last = 0;
  uint64_t parsed_total = 0;
  if (!parseDecimalToken(value, parsed_first) || *value++ != '-'
      || !parseDecimalToken(value, parsed_last) || *value++ != '/'
      || !parseDecimalToken(value, parsed_total)) {
    return false;
  }
  while (*value == ' ' || *value == '\t') ++value;
  if (*value != 0 || parsed_first > parsed_last
      || parsed_last >= parsed_total || parsed_total > MAX_FONT_BYTES) {
    return false;
  }
  first = (size_t)parsed_first;
  last = (size_t)parsed_last;
  total = (size_t)parsed_total;
  return true;
}

bool parseStrongEtagHeader(const char* value, char* etag, size_t capacity) {
  if (etag == nullptr || capacity == 0) return false;
  etag[0] = 0;
  while (*value == ' ' || *value == '\t') ++value;
  const char* end = value + strlen(value);
  while (end > value && (end[-1] == ' ' || end[-1] == '\t')) --end;
  const size_t length = (size_t)(end - value);
  if (length < 2 || length >= capacity || value[0] != '"'
      || value[length - 1] != '"') {
    return false;
  }
  // A strong entity tag is a quoted opaque value, never W/"...". Restrict
  // the reflected If-Range value to printable RFC etagc bytes and reject an
  // embedded quote so response data cannot create another request header.
  for (size_t i = 1; i + 1 < length; ++i) {
    const uint8_t byte = (uint8_t)value[i];
    if (byte < 0x21 || byte > 0x7e || byte == '"') return false;
  }
  memcpy(etag, value, length);
  etag[length] = 0;
  return true;
}

bool parseHttpStatusCode(const char* line, int& status_code) {
  if (strncmp(line, "HTTP/", 5) != 0) return false;
  const char* status = strchr(line, ' ');
  if (status == nullptr) return false;
  ++status;
  if (status[0] < '0' || status[0] > '9'
      || status[1] < '0' || status[1] > '9'
      || status[2] < '0' || status[2] > '9'
      || (status[3] != 0 && status[3] != ' ' && status[3] != '\t')) {
    return false;
  }
  status_code = (status[0] - '0') * 100
      + (status[1] - '0') * 10 + (status[2] - '0');
  return status_code >= 100 && status_code <= 599;
}

enum class RecoveryAttemptDisposition : uint8_t {
  Succeeded,
  CommittedNeedsProbe,
  RetryableFailure,
  PermanentFailure,
  RateLimited,
};

struct RecoveryAttemptOutcome {
  RecoveryAttemptDisposition disposition;
  uint32_t retryDelayMillis;
};

RecoveryAttemptOutcome recoveryOutcome(
    RecoveryAttemptDisposition disposition,
    uint32_t retry_delay_millis = 0) {
  RecoveryAttemptOutcome outcome = {disposition, retry_delay_millis};
  return outcome;
}

RecoveryAttemptOutcome classifyHttpResponse(
    int status_code, bool retry_after_valid, uint64_t retry_after_seconds,
    bool rate_reset_valid, uint64_t rate_reset_epoch) {
  if (status_code == 200 || status_code == 206) {
    return recoveryOutcome(RecoveryAttemptDisposition::Succeeded);
  }
  if (status_code == 403 || status_code == 429) {
    uint64_t delay_seconds = 0;
    bool delay_from_server = false;
    if (retry_after_valid) {
      delay_seconds = retry_after_seconds;
      delay_from_server = true;
    }
    if (rate_reset_valid) {
      const time_t now = time(nullptr);
      uint64_t reset_delay = 0;
      if (now > 0 && rate_reset_epoch > (uint64_t)now) {
        // Avoid retrying on the exact reset boundary.
        reset_delay = rate_reset_epoch - (uint64_t)now + 2U;
      }
      if (!delay_from_server || reset_delay > delay_seconds) {
        delay_seconds = reset_delay;
      }
      delay_from_server = true;
    }
    if (!delay_from_server) {
      FONT_RECOVERY_LOG(
          "HTTP %d rate limit had no usable reset header; waiting 15 minutes\n",
          status_code);
      return recoveryOutcome(RecoveryAttemptDisposition::RateLimited,
                             HTTP_RATE_LIMIT_FALLBACK_DELAY_MS);
    }

    const uint64_t maximum_seconds =
        HTTP_RATE_LIMIT_MAX_DELAY_MS / 1000UL;
    if (delay_seconds > maximum_seconds) {
      FONT_RECOVERY_LOG(
          "HTTP %d rate-limit delay exceeds scheduler range; stopping this boot\n",
          status_code);
      return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);
    }
    uint32_t delay_millis = (uint32_t)delay_seconds * 1000UL;
    if (delay_millis < HTTP_RATE_LIMIT_MIN_DELAY_MS) {
      delay_millis = HTTP_RATE_LIMIT_MIN_DELAY_MS;
    }
    FONT_RECOVERY_LOG("HTTP %d rate limited; next attempt in %lu ms\n",
                      status_code, (unsigned long)delay_millis);
    return recoveryOutcome(RecoveryAttemptDisposition::RateLimited,
                           delay_millis);
  }
  if (status_code >= 500 && status_code <= 599) {
    FONT_RECOVERY_LOG("HTTP %d server failure is retryable\n", status_code);
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  // Redirects are deliberately refused: only the commit-pinned TLS origin is
  // trusted. Other success codes cannot carry the requested representation,
  // and all remaining 4xx responses are permanent for this boot.
  FONT_RECOVERY_LOG("HTTP %d response is permanent for this boot\n",
                    status_code);
  return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);
}

bool splitHttpsUrl(const char* url, char* host, size_t host_capacity,
                   char* path, size_t path_capacity) {
  static const char prefix[] = "https://";
  if (strncmp(url, prefix, sizeof(prefix) - 1) != 0) return false;
  const char* authority = url + sizeof(prefix) - 1;
  const char* slash = strchr(authority, '/');
  if (slash == nullptr) return false;
  size_t host_length = (size_t)(slash - authority);
  size_t path_length = strlen(slash);
  if (host_length == 0 || host_length >= host_capacity
      || path_length == 0 || path_length >= path_capacity
      || memchr(authority, '@', host_length) != nullptr
      || memchr(authority, ':', host_length) != nullptr) {
    return false;
  }
  memcpy(host, authority, host_length);
  host[host_length] = 0;
  memcpy(path, slash, path_length + 1);
  return true;
}

RecoveryAttemptOutcome openAssetResponse(
    WiFiClientSecure& client, size_t requested_offset,
    const char* expected_etag, char* captured_etag,
    size_t captured_etag_capacity) {
  // An interrupted first block resumes at byte zero, so the presence of the
  // captured entity tag—not a nonzero offset—distinguishes a Range request.
  const bool is_resume = expected_etag != nullptr;
  if (requested_offset >= mesh::indicator_font::kAssetSize
      || (is_resume && expected_etag[0] == 0)
      || (!is_resume
          && (captured_etag == nullptr || captured_etag_capacity == 0))) {
    FONT_RECOVERY_LOG("invalid HTTP range request state\n");
    return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);
  }
  if (captured_etag != nullptr && captured_etag_capacity != 0) {
    captured_etag[0] = 0;
  }

  char host[96];
  char path[224];
  if (!splitHttpsUrl(mesh::indicator_font::kAssetUrl, host, sizeof(host),
                     path, sizeof(path))) {
    FONT_RECOVERY_LOG("asset URL is invalid\n");
    return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);
  }

  // WiFiClientSecure retains this per-client TLS configuration across stop()
  // and reconnect. Reapplying Stream::setTimeout() to Arduino-ESP32 2.x's
  // just-closed socket emits a misleading EBADF diagnostic, so configure the
  // client once for the initial request and reuse it for validated ranges.
  if (!is_resume) {
    client.setCACert(FONT_TLS_GITHUB_ROOT_CA);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    client.setTimeout(HTTP_LINE_TIMEOUT_MS);
#else
    // Arduino-ESP32 2.x takes seconds here, unlike Arduino 3.x's millisecond
    // overload. Passing 15000 would leave a failed socket blocked for 4.2 hours.
    client.setTimeout(HTTP_LINE_TIMEOUT_MS / 1000UL);
#endif
    client.setHandshakeTimeout(15);
  }
  // prepareTlsClock() observes one fresh SNTP reply for this bounded recovery
  // operation.  Recheck that proof, WiFi, and the signed wall clock directly
  // before every handshake, including each Range reconnect, without adding a
  // second NTP round trip to a transient HTTP recovery.
  const time_t tls_now = time(nullptr);
  if (!fontTlsClockProofValid(tls_now)
      || tls_now < (time_t)mesh::indicator_font::kAssetPublishedEpoch) {
    FONT_RECOVERY_LOG("TLS clock proof is no longer valid\n");
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  FONT_RECOVERY_LOG("opening TLS connection to %s\n", host);
  if (!client.connect(host, 443)) {
    FONT_RECOVERY_LOG("TLS connection failed\n");
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  client.printf(
      "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: MeshCore-Indicator-Font\r\n"
      "Accept: application/vnd.github.raw+json\r\n"
      "X-GitHub-Api-Version: 2026-03-10\r\nAccept-Encoding: identity\r\n"
      "Cache-Control: no-cache\r\n",
      path, host);
  if (is_resume) {
    client.printf("Range: bytes=%lu-%lu\r\nIf-Range: %s\r\n",
                  (unsigned long)requested_offset,
                  (unsigned long)(mesh::indicator_font::kAssetSize - 1),
                  expected_etag);
  }
  client.print("Connection: close\r\n\r\n");

  char line[256];
  const uint32_t headerStarted = millis();
  uint32_t lineTimeout = remainingHttpHeaderTimeout(headerStarted);
  int status_code = 0;
  if (lineTimeout == 0
      || readHttpLine(client, line, sizeof(line), lineTimeout)
          != HttpLineResult::Complete
      || !parseHttpStatusCode(line, status_code)) {
    FONT_RECOVERY_LOG("HTTP status line failed\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  bool headers_complete = false;
  bool content_length_seen = false;
  bool content_length_valid = true;
  size_t content_length = 0;
  bool transfer_encoding_seen = false;
  bool content_encoding_seen = false;
  bool content_encoding_identity = true;
  bool content_range_seen = false;
  bool content_range_valid = true;
  size_t content_range_first = 0;
  size_t content_range_last = 0;
  size_t content_range_total = 0;
  bool etag_seen = false;
  bool etag_valid = true;
  char response_etag[HTTP_ETAG_CAPACITY] = {};
  bool critical_header_overflow = false;
  bool retry_after_valid = false;
  uint64_t retry_after_seconds = 0;
  bool rate_reset_valid = false;
  uint64_t rate_reset_epoch = 0;
  size_t header_count = 0;
  while (header_count <= HTTP_HEADER_LIMIT) {
    lineTimeout = remainingHttpHeaderTimeout(headerStarted);
    if (lineTimeout == 0) {
      break;
    }
    const HttpLineResult line_result =
        readHttpLine(client, line, sizeof(line), lineTimeout);
    if (line_result == HttpLineResult::Failed) break;
    if (line_result == HttpLineResult::TooLong) {
      FONT_RECOVERY_LOG("HTTP header exceeds %lu wire bytes\n",
                        (unsigned long)HTTP_WIRE_LINE_LIMIT);
      client.stop();
      return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
    }
    // RFC 7230 obsolete line folding is ambiguous to intermediaries. Reject
    // every continuation line, including a normal-size one, before deciding
    // whether an oversized field would otherwise be harmless.
    if (line[0] == ' ' || line[0] == '\t') {
      FONT_RECOVERY_LOG("refusing obsolete folded HTTP header\n");
      client.stop();
      return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
    }
    if (line_result == HttpLineResult::Overflow) {
      // GitHub currently returns a long Access-Control-Expose-Headers field.
      // Drain and ignore oversized extension headers, but never permit a
      // truncated framing header or obsolete folded continuation to hide body
      // boundaries from a successful response. Keep parsing so a non-200
      // response can still provide rate-limit metadata later in the block.
      if (isSecurityCriticalHttpHeader(line)) {
        critical_header_overflow = true;
      }
      ++header_count;
      continue;
    }
    if (line[0] == 0) {
      headers_complete = true;
      break;
    }
    if (++header_count > HTTP_HEADER_LIMIT) break;
    if (strncasecmp(line, "Content-Length:", 15) == 0) {
      if (content_length_seen) {
        content_length_valid = false;
      } else {
        uint64_t parsed = 0;
        if (!parseUnsignedDecimalHeader(line + 15, parsed)
            || parsed > MAX_FONT_BYTES) {
          content_length_valid = false;
        } else {
          content_length = (size_t)parsed;
        }
        content_length_seen = true;
      }
    } else if (strncasecmp(line, "Transfer-Encoding:", 18) == 0) {
      transfer_encoding_seen = true;
    } else if (strncasecmp(line, "Content-Encoding:", 17) == 0) {
      const char* value = line + 17;
      while (*value == ' ' || *value == '\t') ++value;
      content_encoding_identity = !content_encoding_seen
          && strcasecmp(value, "identity") == 0;
      content_encoding_seen = true;
    } else if (strncasecmp(line, "Content-Range:", 14) == 0) {
      if (content_range_seen
          || !parseContentRangeHeader(line + 14, content_range_first,
                                      content_range_last,
                                      content_range_total)) {
        content_range_valid = false;
      }
      content_range_seen = true;
    } else if (strncasecmp(line, "ETag:", 5) == 0) {
      if (etag_seen
          || !parseStrongEtagHeader(line + 5, response_etag,
                                    sizeof(response_etag))) {
        etag_valid = false;
      }
      etag_seen = true;
    } else if (strncasecmp(line, "Retry-After:", 12) == 0) {
      uint64_t parsed = 0;
      if (parseUnsignedDecimalHeader(line + 12, parsed)) {
        if (!retry_after_valid || parsed > retry_after_seconds) {
          retry_after_seconds = parsed;
        }
        retry_after_valid = true;
      } else {
        FONT_RECOVERY_LOG("ignoring malformed Retry-After header\n");
      }
    } else if (strncasecmp(line, "X-RateLimit-Reset:", 18) == 0) {
      uint64_t parsed = 0;
      if (parseUnsignedDecimalHeader(line + 18, parsed)) {
        if (!rate_reset_valid || parsed > rate_reset_epoch) {
          rate_reset_epoch = parsed;
        }
        rate_reset_valid = true;
      } else {
        FONT_RECOVERY_LOG("ignoring malformed X-RateLimit-Reset header\n");
      }
    }
  }
  if (!headers_complete) {
    FONT_RECOVERY_LOG("HTTP headers incomplete or over limit\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  // Classify only after the complete header block has been parsed. In
  // particular, GitHub's 403/429 rate-limit metadata lives in those headers.
  const RecoveryAttemptOutcome response = classifyHttpResponse(
      status_code, retry_after_valid, retry_after_seconds,
      rate_reset_valid, rate_reset_epoch);
  if (response.disposition != RecoveryAttemptDisposition::Succeeded) {
    client.stop();
    return response;
  }

  if (critical_header_overflow) {
    FONT_RECOVERY_LOG("oversized critical HTTP header\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  const int expected_status = is_resume ? 206 : 200;
  if (status_code != expected_status) {
    FONT_RECOVERY_LOG(
        "HTTP %d rejected for %s request (expected %d)\n", status_code,
        is_resume ? "resumed" : "initial", expected_status);
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  const size_t expected_content_length =
      mesh::indicator_font::kAssetSize - requested_offset;
  if (!content_length_seen || !content_length_valid
      || content_length != expected_content_length) {
    FONT_RECOVERY_LOG(
        "HTTP headers invalid (length_seen=%u length_valid=%u length=%lu expected=%lu)\n",
        content_length_seen ? 1U : 0U, content_length_valid ? 1U : 0U,
        (unsigned long)content_length,
        (unsigned long)expected_content_length);
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  if (transfer_encoding_seen) {
    FONT_RECOVERY_LOG("refusing transfer-encoded response\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  if (!content_encoding_identity) {
    FONT_RECOVERY_LOG("refusing encoded response\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  if (!etag_seen || !etag_valid) {
    FONT_RECOVERY_LOG("missing, duplicate, or weak HTTP ETag\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  if (is_resume) {
    if (!content_range_seen || !content_range_valid
        || content_range_first != requested_offset
        || content_range_last != mesh::indicator_font::kAssetSize - 1
        || content_range_total != mesh::indicator_font::kAssetSize
        || strcmp(response_etag, expected_etag) != 0) {
      FONT_RECOVERY_LOG(
          "resumed HTTP range or ETag mismatch (first=%lu last=%lu total=%lu)\n",
          (unsigned long)content_range_first,
          (unsigned long)content_range_last,
          (unsigned long)content_range_total);
      client.stop();
      return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
    }
  } else {
    if (content_range_seen) {
      FONT_RECOVERY_LOG("unexpected Content-Range on initial response\n");
      client.stop();
      return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
    }
    const size_t etag_length = strlen(response_etag);
    if (etag_length + 1 > captured_etag_capacity) {
      FONT_RECOVERY_LOG("HTTP ETag output buffer is too small\n");
      client.stop();
      return recoveryOutcome(RecoveryAttemptDisposition::PermanentFailure);
    }
    memcpy(captured_etag, response_etag, etag_length + 1);
  }
  FONT_RECOVERY_LOG("TLS/HTTP verified; downloading %lu bytes at offset %lu\n",
                    (unsigned long)content_length,
                    (unsigned long)requested_offset);
  return response;
}

bool writeAll(HardwareSerial& serial, const uint8_t* data, size_t size) {
  size_t written = 0;
  uint32_t last_progress = millis();
  while (written < size && millis() - last_progress < 5000UL) {
    size_t count = serial.write(data + written, size - written);
    if (count == 0) {
      delay(1);
      continue;
    }
    written += count;
    last_progress = millis();
  }
  return written == size;
}

bool commandReply(HardwareSerial& serial, const char* command, char* line,
                  size_t line_capacity, uint32_t timeout_millis) {
  if (line_capacity == 0) return false;
  line[0] = 0;
  while (serial.available()) serial.read();
  serial.print(command);
  serial.print('\n');
  serial.flush();
  return readLine(serial, line, line_capacity, timeout_millis);
}

enum class StageTransferMode : uint8_t {
  Acknowledged,
  LegacyPaced,
};

bool beginStagedUpload(HardwareSerial& serial, StageTransferMode& mode) {
  char command[72];
  char reply[64];
  // STAGEV2 intentionally is not prefixed by the legacy "STAGE <size>"
  // grammar. Older sscanf-based services would otherwise parse the `2` in
  // STAGE2 as a two-byte file and answer ERROR SIZE instead of the explicit
  // ERROR COMMAND compatibility signal.
  snprintf(command, sizeof(command), "MCFONT STAGEV2 %lu %08lx %lu",
           (unsigned long)mesh::indicator_font::kAssetSize,
           (unsigned long)mesh::indicator_font::kAssetCrc32,
           (unsigned long)mesh::indicator_font::kStageV2ChunkBytes);
  const bool replied = commandReply(
      serial, command, reply, sizeof(reply), 2000);
  const mesh::indicator_font::StageV2BeginAction action =
      mesh::indicator_font::classifyStageV2BeginReply(replied, reply);
  if (action
      == mesh::indicator_font::StageV2BeginAction::UseAcknowledged) {
    mode = StageTransferMode::Acknowledged;
    FONT_RECOVERY_LOG("RP2040 staging protocol 2 ready (512-byte ACKs)\n");
    return true;
  }

  // Existing RP2040 releases answer an unknown command with ERROR COMMAND.
  // Fall back only on that explicit pre-transfer rejection. A delayed READY
  // must never be mistaken for permission to inject a legacy command into a
  // receiver that has already entered protocol-2 binary mode.
  if (action != mesh::indicator_font::StageV2BeginAction::UseLegacy) {
    if (!replied) {
      FONT_RECOVERY_LOG("RP2040 staging protocol 2 timed out\n");
    } else {
      FONT_RECOVERY_LOG("RP2040 rejected staging protocol 2: %s\n", reply);
    }
    return false;
  }

  FONT_RECOVERY_LOG(
      "RP2040 staging protocol 2 unavailable; using paced legacy stream\n");
  snprintf(command, sizeof(command), "MCFONT STAGE %lu %08lx",
           (unsigned long)mesh::indicator_font::kAssetSize,
           (unsigned long)mesh::indicator_font::kAssetCrc32);
  if (!commandReply(serial, command, reply, sizeof(reply), 2000)
      || strcmp(reply, "READY") != 0) {
    FONT_RECOVERY_LOG("RP2040 rejected legacy staging: %s\n",
                      reply[0] ? reply : "<timeout>");
    return false;
  }
  mode = StageTransferMode::LegacyPaced;
  return true;
}

bool stagedChunkAcknowledged(HardwareSerial& serial, size_t expected_offset) {
  char reply[64];
  serial.flush();
  if (!readLine(serial, reply, sizeof(reply), STAGE_ACK_TIMEOUT_MS)) {
    FONT_RECOVERY_LOG("RP2040 stage ACK timed out at byte %lu\n",
                      (unsigned long)expected_offset);
    return false;
  }
  if (!mesh::indicator_font::parseStageV2Ack(reply, expected_offset)) {
    FONT_RECOVERY_LOG(
        "RP2040 stage ACK invalid or out of sequence at byte %lu: %s\n",
        (unsigned long)expected_offset, reply);
    return false;
  }
  return true;
}

RecoveryAttemptOutcome downloadAndInstallAsset() {
  WiFiClientSecure client;
  if (!prepareTlsClock()) {
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }
  char asset_etag[HTTP_ETAG_CAPACITY] = {};
  RecoveryAttemptOutcome response = openAssetResponse(
      client, 0, nullptr, asset_etag, sizeof(asset_etag));
  if (response.disposition != RecoveryAttemptDisposition::Succeeded) {
    return response;
  }

  // Do not hold the HTTPS response open while the RP2040 performs SD writes.
  // GitHub can close a slow response near its service deadline, and the
  // Indicator has enough PSRAM to keep this immutable 1.3 MiB object separate
  // even when an older runtime font is still active.
  uint8_t* asset = static_cast<uint8_t*>(
      ps_malloc(mesh::indicator_font::kAssetSize));
  if (asset == nullptr) {
    FONT_RECOVERY_LOG("PSRAM allocation failed for download\n");
    client.stop();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  bool ok = mbedtls_sha256_starts_ret(&sha, 0) == 0;
  const char* failure = ok ? nullptr : "SHA-256 initialization";
  size_t received = 0;
  uint32_t started = millis();
  uint32_t last_progress = started;
  size_t next_progress = 256 * 1024;
  uint8_t resume_reconnects = 0;
  bool response_failure = false;
  while (ok && received < mesh::indicator_font::kAssetSize) {
    bool reconnect_needed = false;
    size_t discarded_partial = 0;
    while (ok && received < mesh::indicator_font::kAssetSize
           && !reconnect_needed) {
      if (millis() - started >= DOWNLOAD_TOTAL_TIMEOUT_MS) {
        failure = "HTTP body total timeout";
        ok = false;
        break;
      }

      // `received` is both the SHA stream offset and the only legal Range
      // resume boundary. Bytes from a short 16 KiB block remain un-hashed and
      // are deliberately overwritten if this response closes early.
      size_t wanted = mesh::indicator_font::kAssetSize - received;
      if (wanted > 16 * 1024) wanted = 16 * 1024;
      size_t filled = 0;
      while (filled < wanted) {
        if (millis() - started >= DOWNLOAD_TOTAL_TIMEOUT_MS) {
          failure = "HTTP body total timeout";
          ok = false;
          break;
        }
        if (millis() - last_progress >= DOWNLOAD_IDLE_TIMEOUT_MS) {
          failure = "HTTP body idle timeout";
          discarded_partial = filled;
          reconnect_needed = true;
          break;
        }
        int available = client.available();
        if (available <= 0) {
          if (!client.connected() && !client.available()) {
            failure = "HTTP body ended early";
            discarded_partial = filled;
            reconnect_needed = true;
            break;
          }
          delay(1);
          continue;
        }
        size_t count_wanted = wanted - filled;
        if (count_wanted > (size_t)available) {
          count_wanted = (size_t)available;
        }
        int count = client.read(asset + received + filled, count_wanted);
        if (count <= 0) continue;
        filled += (size_t)count;
        last_progress = millis();
      }
      if (!ok || reconnect_needed) break;

      if (mbedtls_sha256_update_ret(&sha, asset + received, wanted) != 0) {
        failure = "SHA-256 update";
        ok = false;
        break;
      }
      received += wanted;
      if (received >= next_progress) {
        FONT_RECOVERY_LOG("downloaded %lu of %lu bytes in %lu ms\n",
                          (unsigned long)received,
                          (unsigned long)mesh::indicator_font::kAssetSize,
                          (unsigned long)(millis() - started));
        next_progress += 256 * 1024;
      }
    }

    if (!ok || received == mesh::indicator_font::kAssetSize) break;
    client.stop();
    if (!reconnect_needed) {
      failure = "HTTP body incomplete";
      ok = false;
      break;
    }
    if (millis() - started >= DOWNLOAD_TOTAL_TIMEOUT_MS) {
      failure = "HTTP body total timeout";
      ok = false;
      break;
    }
    if (resume_reconnects >= DOWNLOAD_MAX_RESUME_RECONNECTS) {
      failure = "HTTP Range reconnect limit";
      ok = false;
      break;
    }

    bool range_open = false;
    while (resume_reconnects < DOWNLOAD_MAX_RESUME_RECONNECTS
           && millis() - started < DOWNLOAD_TOTAL_TIMEOUT_MS) {
      ++resume_reconnects;
      FONT_RECOVERY_LOG(
          "response interrupted; Range resume %u/%u at byte %lu (discarding %lu unverified bytes)\n",
          (unsigned int)resume_reconnects,
          (unsigned int)DOWNLOAD_MAX_RESUME_RECONNECTS,
          (unsigned long)received, (unsigned long)discarded_partial);
      response = openAssetResponse(
          client, received, asset_etag, nullptr, 0);
      if (response.disposition == RecoveryAttemptDisposition::Succeeded) {
        range_open = true;
        break;
      }
      if (response.disposition
          != RecoveryAttemptDisposition::RetryableFailure) {
        failure = "HTTP Range response rejected";
        response_failure = true;
        break;
      }
      client.stop();
      if (resume_reconnects < DOWNLOAD_MAX_RESUME_RECONNECTS
          && millis() - started < DOWNLOAD_TOTAL_TIMEOUT_MS) {
        FONT_RECOVERY_LOG(
            "Range connection failed; retrying the same verified offset\n");
        delay(250);
      }
    }
    if (!range_open) {
      if (failure == nullptr || !response_failure) {
        failure = millis() - started >= DOWNLOAD_TOTAL_TIMEOUT_MS
            ? "HTTP body total timeout" : "HTTP Range reconnect limit";
      }
      ok = false;
      break;
    }
    // Header parsing and a resumed TLS handshake have their own deadlines.
    // Only the body-idle clock restarts; `started` remains the one deadline for
    // all response bodies and reconnects in this recovery attempt.
    last_progress = millis();
  }
  const bool network_connected_at_end = client.connected();
  client.stop();

  uint8_t digest[32] = {};
  if (ok && mbedtls_sha256_finish_ret(&sha, digest) != 0) {
    failure = "SHA-256 finish";
    ok = false;
  }
  mbedtls_sha256_free(&sha);
  if (!ok || received != mesh::indicator_font::kAssetSize) {
    FONT_RECOVERY_LOG(
        "download failed (%s; bytes=%lu elapsed=%lu idle=%lu connected=%u)\n",
        failure ? failure : "incomplete body", (unsigned long)received,
        (unsigned long)(millis() - started),
        (unsigned long)(millis() - last_progress),
        network_connected_at_end ? 1U : 0U);
    free(asset);
    return response_failure
        ? response
        : recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  bool digest_ok = memcmp(digest, EXPECTED_SHA256, sizeof(digest)) == 0;
  FONT_RECOVERY_LOG("download complete in %lu ms; SHA-256=%s\n",
                    (unsigned long)(millis() - started),
                    digest_ok ? "verified" : "mismatch");
  if (!digest_ok) {
    free(asset);
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  Serial2.begin(FONT_UART_BAUD, SERIAL_8N1, FONT_UART_RX, FONT_UART_TX);
  delay(50);
  synchronizeCommandParser(Serial2);
  StageTransferMode stage_mode = StageTransferMode::Acknowledged;
  if (!beginStagedUpload(Serial2, stage_mode)) {
    Serial2.end();
    free(asset);
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  bool stage_ok = true;
  const char* stage_failure = nullptr;
  size_t staged_bytes = 0;
  const uint32_t stage_started = millis();
  next_progress = 256 * 1024;
  while (staged_bytes < mesh::indicator_font::kAssetSize) {
    if (millis() - stage_started >= STAGE_TOTAL_TIMEOUT_MS) {
      stage_failure = "total timeout";
      stage_ok = false;
      break;
    }
    const size_t wanted = mesh::indicator_font::stageV2ChunkSize(
        mesh::indicator_font::kAssetSize, staged_bytes);
    if (!writeAll(Serial2, asset + staged_bytes, wanted)) {
      stage_failure = "ESP32 UART write";
      stage_ok = false;
      break;
    }
    size_t next_staged = 0;
    if (!mesh::indicator_font::advanceStageV2Offset(
            mesh::indicator_font::kAssetSize, staged_bytes, wanted,
            next_staged)) {
      stage_failure = "offset accounting";
      stage_ok = false;
      break;
    }
    if (stage_mode == StageTransferMode::Acknowledged) {
      if (!stagedChunkAcknowledged(Serial2, next_staged)) {
        stage_failure = "RP2040 acknowledgement";
        stage_ok = false;
        break;
      }
    } else {
      // write() may return after queueing bytes. Drain the UART before the
      // receiver-safe pause so each legacy burst stays bounded to one block.
      Serial2.flush();
      delay(LEGACY_STAGE_PACE_MS);
    }
    staged_bytes = next_staged;
    if (staged_bytes >= next_progress) {
      FONT_RECOVERY_LOG("staged %lu of %lu bytes in %lu ms\n",
                        (unsigned long)staged_bytes,
                        (unsigned long)mesh::indicator_font::kAssetSize,
                        (unsigned long)(millis() - stage_started));
      next_progress += 256 * 1024;
    }
  }
  free(asset);
  asset = nullptr;
  if (!stage_ok) {
    // The RP2040's bounded receive removes its separate staging file. Do not
    // inject ABORT into a peer that may still be in its binary receive state.
    FONT_RECOVERY_LOG("staging failed (%s; bytes=%lu elapsed=%lu)\n",
                      stage_failure ? stage_failure : "unknown",
                      (unsigned long)staged_bytes,
                      (unsigned long)(millis() - stage_started));
    Serial2.end();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  char line[64];
  Serial2.flush();
  bool staged = readLine(Serial2, line, sizeof(line), 20000)
      && strcmp(line, "STAGED") == 0;
  FONT_RECOVERY_LOG("staging complete in %lu ms; RP2040 reply='%s'\n",
                    (unsigned long)(millis() - stage_started),
                    line[0] ? line : "<timeout>");
  if (!staged) {
    Serial2.end();
    return recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure);
  }

  char commitCommand[64];
  snprintf(commitCommand, sizeof(commitCommand), "MCFONT COMMIT %lu %08lx",
           (unsigned long)mesh::indicator_font::kAssetSize,
           (unsigned long)mesh::indicator_font::kAssetCrc32);
  char commitReply[64];
  const bool commitReplied = commandReply(
      Serial2, commitCommand, commitReply, sizeof(commitReply), 30000);
  const bool committed = commitReplied && strcmp(commitReply, "OK") == 0;
  const bool commitExplicitlyRejected = commitReplied
      && strncmp(commitReply, "ERROR ", 6) == 0;
  FONT_RECOVERY_LOG("RP2040 commit %s (reply='%s')\n",
                    committed ? "accepted"
                    : commitExplicitlyRejected ? "rejected" : "uncertain",
                    commitReplied ? commitReply : "<timeout>");
  bool postCommitVerified = false;
  if (committed) {
    size_t size = 0;
    uint32_t crc = 0;
    postCommitVerified =
        requestInfo(Serial2, size, crc) == FontInfoResult::Present
        && size == mesh::indicator_font::kAssetSize
        && crc == mesh::indicator_font::kAssetCrc32;
    FONT_RECOVERY_LOG("post-commit font size=%lu crc=%08lx result=%s\n",
                      (unsigned long)size, (unsigned long)crc,
                      postCommitVerified ? "verified" : "probe required");
  }
  Serial2.end();
  if (!committed) {
    // A missing or malformed reply is not proof that COMMIT failed: the
    // RP2040 can finish its durable rename just as the UART response is lost.
    // Reclassify that ambiguous result using only the local service so it can
    // never trigger another download of the identical immutable blob. A
    // syntactically explicit ERROR remains a retryable rejected transaction.
    return recoveryOutcome(commitExplicitlyRejected
        ? RecoveryAttemptDisposition::RetryableFailure
        : RecoveryAttemptDisposition::CommittedNeedsProbe);
  }
  return recoveryOutcome(postCommitVerified
      ? RecoveryAttemptDisposition::Succeeded
      : RecoveryAttemptDisposition::CommittedNeedsProbe);
}

void finishRecoveryFailure(const RecoveryAttemptOutcome& outcome) {
  uint32_t delay_millis =
      mesh::indicator_font::retryDelayAfter(recoveryAttempts);
  if (outcome.disposition == RecoveryAttemptDisposition::RateLimited
      && outcome.retryDelayMillis > delay_millis) {
    delay_millis = outcome.retryDelayMillis;
  }
  uint32_t next = millis() + delay_millis;
  if (next == 0) next = 1;
  portENTER_CRITICAL(&recoveryMux);
  if (outcome.disposition
          == RecoveryAttemptDisposition::CommittedNeedsProbe
      || outcome.disposition == RecoveryAttemptDisposition::PermanentFailure
      || recoveryAttempts >= mesh::indicator_font::kMaximumAttemptsPerBoot) {
    recoveryState = RecoveryState::Exhausted;
  } else {
    recoveryNextAttempt = next;
    recoveryState = RecoveryState::Waiting;
  }
  portEXIT_CRITICAL(&recoveryMux);
}

void finishRecoveryFailure() {
  finishRecoveryFailure(
      recoveryOutcome(RecoveryAttemptDisposition::RetryableFailure));
}

void finishServiceProbeFailure() {
  uint32_t next = millis()
      + mesh::indicator_font::serviceProbeRetryDelayAfter(
          serviceProbeAttempts);
  if (next == 0) next = 1;
  portENTER_CRITICAL(&recoveryMux);
  if (serviceProbeAttempts
      >= mesh::indicator_font::kMaximumServiceProbeAttemptsPerBoot) {
    recoveryState = RecoveryState::Exhausted;
  } else {
    recoveryNextAttempt = next;
    recoveryState = RecoveryState::ProbeWaiting;
  }
  portEXIT_CRITICAL(&recoveryMux);
}

void finishPostCommitProbeFailure() {
  uint32_t next = millis()
      + mesh::indicator_font::postCommitProbeRetryDelayAfter(
          postCommitProbeAttempts);
  if (next == 0) next = 1;
  portENTER_CRITICAL(&recoveryMux);
  if (postCommitProbeAttempts
      >= mesh::indicator_font::kMaximumPostCommitProbeAttemptsPerBoot) {
    // COMMIT was acknowledged, so redownloading the identical immutable blob
    // is neither a repair nor safe progress. Leave the fallback/old runtime
    // font in place and let the next boot reclassify the local service.
    recoveryState = RecoveryState::Exhausted;
  } else {
    recoveryNextAttempt = next;
    recoveryState = RecoveryState::PostCommitProbeWaiting;
  }
  portEXIT_CRITICAL(&recoveryMux);
}

void serviceProbeTask(void*) {
  LoadedFont loaded = loadFromService();
  if (loaded.data != nullptr) {
    if (loaded.currentAsset) {
      portENTER_CRITICAL(&recoveryMux);
      recoveredFont = loaded.data;
      recoveredFontSize = loaded.size;
      recoveryState = RecoveryState::Ready;
      portEXIT_CRITICAL(&recoveryMux);
    } else {
      // Startup is still using the fallback because the first INFO exchange
      // was inconclusive. Replace an older valid file live after recovery;
      // unlike the normal version-mismatch path, no old PSRAM font is active.
      free(loaded.data);
      armRecovery(mesh::indicator_font::RecoveryNeed::VersionMismatch, true);
    }
  } else if (loaded.info == FontInfoResult::Missing) {
    armRecovery(mesh::indicator_font::RecoveryNeed::Missing);
  } else if (loaded.info == FontInfoResult::Present) {
    armRecovery(mesh::indicator_font::RecoveryNeed::Corrupt);
  } else {
    finishServiceProbeFailure();
  }
  vTaskDelete(nullptr);
}

void postCommitProbeTask(void*) {
  bool activateLive;
  portENTER_CRITICAL(&recoveryMux);
  activateLive = recoveryActivateLive;
  portEXIT_CRITICAL(&recoveryMux);

  if (!activateLive) {
    // A valid older runtime font is deliberately retained until reboot. The
    // RP2040's COMMIT path re-reads the complete staged file and verifies its
    // CRC before publishing this exact metadata, so an INFO match is enough to
    // classify the local install without allocating a second 1.3 MiB buffer.
    size_t size = 0;
    uint32_t crc = 0;
    const FontInfoResult info = probeServiceInfo(size, crc);
    if (info == FontInfoResult::Present
        && size == mesh::indicator_font::kAssetSize
        && crc == mesh::indicator_font::kAssetCrc32) {
      portENTER_CRITICAL(&recoveryMux);
      recoveryState = RecoveryState::Complete;
      portEXIT_CRITICAL(&recoveryMux);
    } else {
      FONT_RECOVERY_LOG(
          "committed asset INFO re-probe failed (info=%u size=%lu crc=%08lx)\n",
          (unsigned int)info, (unsigned long)size, (unsigned long)crc);
      finishPostCommitProbeFailure();
    }
    vTaskDelete(nullptr);
    return;
  }

  LoadedFont loaded = loadFromService();
  if (loaded.data != nullptr && loaded.currentAsset) {
    portENTER_CRITICAL(&recoveryMux);
    recoveredFont = loaded.data;
    recoveredFontSize = loaded.size;
    recoveryState = RecoveryState::Ready;
    portEXIT_CRITICAL(&recoveryMux);
  } else {
    FONT_RECOVERY_LOG(
        "committed asset re-probe failed (info=%u data=%u current=%u)\n",
        (unsigned int)loaded.info, loaded.data != nullptr ? 1U : 0U,
        loaded.currentAsset ? 1U : 0U);
    free(loaded.data);
    finishPostCommitProbeFailure();
  }
  vTaskDelete(nullptr);
}

void recoveryTask(void*) {
  bool hadRuntimeFont;
  bool activateLive;
  portENTER_CRITICAL(&recoveryMux);
  hadRuntimeFont = runtimeFontInstalled;
  activateLive = recoveryActivateLive;
  portEXIT_CRITICAL(&recoveryMux);

  const RecoveryAttemptOutcome outcome = downloadAndInstallAsset();
  const bool activateCommittedLive = !hadRuntimeFont && activateLive;
  if (outcome.disposition
      == RecoveryAttemptDisposition::CommittedNeedsProbe) {
    armPostCommitProbe(activateCommittedLive);
    vTaskDelete(nullptr);
    return;
  }
  if (outcome.disposition != RecoveryAttemptDisposition::Succeeded) {
    finishRecoveryFailure(outcome);
    vTaskDelete(nullptr);
    return;
  }

  uint8_t* data = nullptr;
  size_t size = 0;
  if (activateCommittedLive) {
    LoadedFont loaded = loadFromService();
    if (loaded.data == nullptr || !loaded.currentAsset) {
      free(loaded.data);
      // The RP2040 already acknowledged COMMIT and its INFO metadata matched.
      // A failed follow-up GET is a local transport/service problem, not
      // authorization to download the same immutable Git blob again.
      armPostCommitProbe(true);
      vTaskDelete(nullptr);
      return;
    }
    data = loaded.data;
    size = loaded.size;
  }

  portENTER_CRITICAL(&recoveryMux);
  recoveredFont = data;
  recoveredFontSize = size;
  recoveryState = data != nullptr ? RecoveryState::Ready
                                  : RecoveryState::Complete;
  portEXIT_CRITICAL(&recoveryMux);
  vTaskDelete(nullptr);
}

#endif  // INDICATOR_WIFI_FONT_RECOVERY

}  // namespace

uint8_t* IndicatorFontClient::load(size_t& size) {
  LoadedFont loaded = loadFromService();
  size = loaded.size;
#ifdef INDICATOR_WIFI_FONT_RECOVERY
  if (loaded.data != nullptr) {
    if (!loaded.currentAsset) {
      armRecovery(mesh::indicator_font::RecoveryNeed::VersionMismatch);
    }
  } else if (loaded.info == FontInfoResult::Missing) {
    armRecovery(mesh::indicator_font::RecoveryNeed::Missing);
  } else if (loaded.info == FontInfoResult::Present) {
    armRecovery(mesh::indicator_font::RecoveryNeed::Corrupt);
  } else {
    // The RP2040 can still be mounting SD when the ESP32 first asks. Once
    // station Wi-Fi is connected, re-probe it on the background worker before
    // deciding whether a network repair is needed.
    armServiceProbe();
  }
#endif
  return loaded.data;
}

#ifdef INDICATOR_WIFI_FONT_RECOVERY
uint8_t* IndicatorFontClient::serviceRecovery(size_t& size) {
  size = 0;
  uint8_t* ready = nullptr;
  bool launchProbe = false;
  bool launchPostCommitProbe = false;
  bool launchRecovery = false;
  uint32_t now = millis();
  const bool wifiConnected = WiFi.status() == WL_CONNECTED;

  portENTER_CRITICAL(&recoveryMux);
  if (recoveryState == RecoveryState::Ready) {
    ready = recoveredFont;
    size = recoveredFontSize;
    recoveredFont = nullptr;
    recoveredFontSize = 0;
    recoveryState = RecoveryState::Complete;
  } else if (recoveryState == RecoveryState::PostCommitProbeWaiting
             && mesh::indicator_font::deadlineReached(
                 now, recoveryNextAttempt)) {
    // This path is local UART/SD recovery after an acknowledged COMMIT. It
    // deliberately does not require Wi-Fi and cannot transition to Waiting.
    recoveryState = RecoveryState::PostCommitProbeRunning;
    ++postCommitProbeAttempts;
    launchPostCommitProbe = true;
  } else if (recoveryState == RecoveryState::ProbeWaiting
             && wifiConnected
             && mesh::indicator_font::deadlineReached(
                 now, recoveryNextAttempt)) {
    recoveryState = RecoveryState::ProbeRunning;
    ++serviceProbeAttempts;
    launchProbe = true;
  } else if (recoveryState == RecoveryState::Waiting
             && wifiConnected
             && mesh::indicator_font::deadlineReached(
                 now, recoveryNextAttempt)) {
    recoveryState = RecoveryState::Running;
    ++recoveryAttempts;
    launchRecovery = true;
  }
  portEXIT_CRITICAL(&recoveryMux);

  if (launchProbe) {
    TaskHandle_t task = nullptr;
    if (xTaskCreatePinnedToCore(serviceProbeTask, "indicator-font-probe",
                                RECOVERY_TASK_STACK_BYTES, nullptr, 2, &task,
                                1) != pdPASS) {
      finishServiceProbeFailure();
    }
  } else if (launchPostCommitProbe) {
    TaskHandle_t task = nullptr;
    if (xTaskCreatePinnedToCore(
            postCommitProbeTask, "indicator-font-committed-probe",
            RECOVERY_TASK_STACK_BYTES, nullptr, 2, &task, 1) != pdPASS) {
      finishPostCommitProbeFailure();
    }
  } else if (launchRecovery) {
    TaskHandle_t task = nullptr;
    if (xTaskCreatePinnedToCore(recoveryTask, "indicator-font",
                                RECOVERY_TASK_STACK_BYTES, nullptr, 2, &task,
                                1) != pdPASS) {
      finishRecoveryFailure();
    }
  }
  return ready;
}

void IndicatorFontClient::noteRuntimeFontInstalled() {
  portENTER_CRITICAL(&recoveryMux);
  runtimeFontInstalled = true;
  portEXIT_CRITICAL(&recoveryMux);
}

void IndicatorFontClient::noteRuntimeFontInvalid() {
  portENTER_CRITICAL(&recoveryMux);
  runtimeFontInstalled = false;
  portEXIT_CRITICAL(&recoveryMux);
  armRecovery(mesh::indicator_font::RecoveryNeed::Corrupt);
}

void IndicatorFontClient::noteRecoveredFontInvalid() {
  // Every buffer returned by serviceRecovery() has already matched the exact
  // compiled size, CRC32, and SHA-256. If the runtime parser rejects those
  // immutable bytes, neither another local stream nor another HTTPS download
  // can change the result. Stop for this boot instead of wasting bandwidth or
  // creating a download/activation loop; the built-in fallback remains live.
  portENTER_CRITICAL(&recoveryMux);
  runtimeFontInstalled = false;
  recoveryNeed = mesh::indicator_font::RecoveryNeed::None;
  recoveryActivateLive = false;
  recoveryState = RecoveryState::Exhausted;
  portEXIT_CRITICAL(&recoveryMux);
}
#endif
