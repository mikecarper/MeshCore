#include "MeshClockSync.h"

#include <Arduino.h>
#include <RTClib.h>
#include <Utils.h>
#include <helpers/ClockSyncUtils.h>
#include <helpers/TxtDataHelpers.h>
#include <helpers/sensors/LocationProvider.h>

#include <stdlib.h>
#include <string.h>

#ifndef CLOCK_SYNC_MESH_DEFAULT_ENABLED
  #define CLOCK_SYNC_MESH_DEFAULT_ENABLED 1
#endif
#ifndef CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED
  #define CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED 1
#endif
#ifndef FIRMWARE_BUILD_EPOCH
  #define FIRMWARE_BUILD_EPOCH 0UL
#endif
#ifndef MESH_CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT
  #define MESH_CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT 9
#endif
#ifndef MESH_CLOCK_SYNC_STARTUP_DELAY_MILLIS
  #define MESH_CLOCK_SYNC_STARTUP_DELAY_MILLIS (30ULL * 60ULL * 1000ULL)
#endif

namespace {

constexpr char PREFS_FILE[] = "/clock_sync";
constexpr uint8_t REQUIRED_SAMPLES_MIN = 3;
constexpr uint8_t REQUIRED_SAMPLES_MAX = 16;
constexpr uint8_t REQUIRED_SAMPLES_DEFAULT =
    MESH_CLOCK_SYNC_REQUIRED_SAMPLES_DEFAULT;
constexpr uint64_t STARTUP_DELAY_MILLIS =
    MESH_CLOCK_SYNC_STARTUP_DELAY_MILLIS;
constexpr uint64_t RETRY_INTERVAL_MILLIS = 30ULL * 60ULL * 1000ULL;
constexpr uint64_t RESYNC_INTERVAL_MILLIS = 7ULL * 24ULL * 60ULL * 60ULL * 1000ULL;
constexpr uint32_t SAMPLE_MAX_AGE_MILLIS = 2UL * 60UL * 60UL * 1000UL;
constexpr uint32_t CONSENSUS_WINDOW_SECONDS = 600UL;
constexpr uint32_t DRIFT_MIN_SECONDS = 30UL;
constexpr uint32_t DRIFT_MAX_SECONDS = 86400UL;
constexpr uint16_t VALID_YEARS = 10;

static_assert(REQUIRED_SAMPLES_DEFAULT >= REQUIRED_SAMPLES_MIN
                  && REQUIRED_SAMPLES_DEFAULT <= REQUIRED_SAMPLES_MAX,
              "mesh clock-sync sample default is outside the supported range");
static_assert(STARTUP_DELAY_MILLIS > 0,
              "mesh clock-sync startup delay must be positive");

// Public-channel AES key, zero-padded to the shared-secret buffer width used
// by Utils::MACThenDecrypt().
const uint8_t PUBLIC_CHANNEL_SECRET[PUB_KEY_SIZE] = {
  0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
  0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72,
};

bool leapYear(uint16_t year) {
  return (year % 4U == 0 && year % 100U != 0) || year % 400U == 0;
}

uint32_t minimumValidEpoch() {
#if FIRMWARE_BUILD_EPOCH > 0
  return (uint32_t)FIRMWARE_BUILD_EPOCH;
#else
  static uint32_t minimum = 0;
  if (minimum == 0) minimum = DateTime(__DATE__, __TIME__).unixtime();
  return minimum;
#endif
}

uint32_t maximumValidEpoch() {
  static uint32_t maximum = 0;
  if (maximum == 0) {
    DateTime built(minimumValidEpoch());
    uint16_t upper_year = built.year() + VALID_YEARS;
    uint8_t upper_day = built.day();
    if (built.month() == 2 && upper_day == 29 && !leapYear(upper_year)) upper_day = 28;
    maximum = DateTime(upper_year, built.month(), upper_day,
                       built.hour(), built.minute(), built.second()).unixtime();
  }
  return maximum;
}

bool validEpoch(uint32_t epoch) {
  return epoch >= minimumValidEpoch() && epoch <= maximumValidEpoch();
}

File openRead(FILESYSTEM* fs, const char* filename) {
#if defined(RP2040_PLATFORM)
  return fs->open(filename, "r");
#else
  return fs->open(filename);
#endif
}

File openWrite(FILESYSTEM* fs, const char* filename) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs->remove(filename);
  return fs->open(filename, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return fs->open(filename, "w");
#else
  return fs->open(filename, "w", true);
#endif
}

const char* skipSpaces(const char* text) {
  while (text != nullptr && *text == ' ') text++;
  return text;
}

bool parseUnsigned(const char* text, uint32_t maximum, uint32_t& value) {
  text = skipSpaces(text);
  if (text == nullptr || *text < '0' || *text > '9') return false;
  uint32_t parsed = 0;
  while (*text >= '0' && *text <= '9') {
    uint8_t digit = (uint8_t)(*text++ - '0');
    if (parsed > (maximum - digit) / 10U) return false;
    parsed = parsed * 10U + digit;
  }
  text = skipSpaces(text);
  if (*text != 0) return false;
  value = parsed;
  return true;
}

bool parsePositiveSelector(const char* text, int& value) {
  uint32_t parsed = 0;
  if (!parseUnsigned(text, 255, parsed) || parsed == 0) return false;
  value = (int)parsed;
  return true;
}

void derivePathId(const mesh::Packet* packet, uint8_t path_id[8]) {
  uint8_t material[2 + MAX_PATH_SIZE];
  uint8_t count = packet->getPathHashCount();
  uint8_t path_bytes = packet->getPathByteLen();
  material[0] = count == 0 ? 0 : packet->getPathHashSize();
  material[1] = count;
  if (path_bytes > 0) memcpy(&material[2], packet->path, path_bytes);
  mesh::Utils::sha256(path_id, 8, material, 2 + path_bytes);
}

bool decodePublicPlainText(const mesh::Packet* packet, uint32_t& timestamp,
                           char* sender, size_t sender_len) {
  if (sender != nullptr && sender_len > 0) sender[0] = 0;
  if (packet == nullptr || sender == nullptr || sender_len < 2
      || !packet->isRouteFlood()
      || packet->getPayloadType() != PAYLOAD_TYPE_GRP_TXT
      || packet->payload_len <= PATH_HASH_SIZE + CIPHER_MAC_SIZE) return false;

  uint8_t channel_hash = 0;
  mesh::Utils::sha256(&channel_hash, sizeof(channel_hash),
                      PUBLIC_CHANNEL_SECRET, CIPHER_KEY_SIZE);
  if (packet->payload[0] != channel_hash) return false;

  uint8_t data[MAX_PACKET_PAYLOAD];
  int len = mesh::Utils::MACThenDecrypt(
      PUBLIC_CHANNEL_SECRET, data, &packet->payload[PATH_HASH_SIZE],
      packet->payload_len - PATH_HASH_SIZE);
  if (len <= 5 || (data[4] >> 2) != TXT_TYPE_PLAIN) return false;

  memcpy(&timestamp, data, sizeof(timestamp));
  const uint8_t* text = &data[5];
  size_t text_len = (size_t)len - 5;
  const uint8_t* colon = (const uint8_t*)memchr(text, ':', text_len);
  if (colon == nullptr) return false;

  size_t parsed_len = (size_t)(colon - text);
  while (parsed_len > 0 && text[parsed_len - 1] == ' ') parsed_len--;
  if (parsed_len == 0 || parsed_len >= sender_len) return false;
  for (size_t i = 0; i < parsed_len; i++) {
    if (text[i] == '\r' || text[i] == '\n' || text[i] < 0x20) return false;
  }
  memcpy(sender, text, parsed_len);
  sender[parsed_len] = 0;
  return true;
}

uint32_t rebaseTimestamp(uint32_t timestamp, uint32_t old_now,
                         uint32_t new_now) {
  if (timestamp == 0) return 0;
  uint32_t age = old_now >= timestamp ? old_now - timestamp : 0;
  return new_now > age ? new_now - age : 1;
}

const char* sampleKindName(uint8_t source_kind) {
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT) return "advert";
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL) return "public";
  return "unknown";
}

char sampleKindCode(uint8_t source_kind) {
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT) return 'A';
  if (source_kind == mesh::CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL) return 'P';
  return '?';
}

}  // namespace

namespace mesh {

MeshClockSync::MeshClockSync(Radio& radio, MillisecondClock& millis,
                             RTCClock& rtc, ClientACL& acl,
                             SensorManager& sensors,
                             const float& tx_delay_factor,
                             MeshClockSyncCallbacks* callbacks)
    : _radio(&radio), _millis(&millis), _rtc(&rtc), _acl(&acl),
      _sensors(&sensors), _tx_delay_factor(&tx_delay_factor),
      _callbacks(callbacks), _fs(nullptr) {
  _last_millis = 0;
  _uptime_millis = 0;
  resetDefaults();
}

void MeshClockSync::resetDefaults() {
  memset(_samples, 0, sizeof(_samples));
  _mesh_enabled = CLOCK_SYNC_MESH_DEFAULT_ENABLED != 0;
  _mesh_edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0;
  _internet_enabled = false;
  _complete = false;
  _force_mesh_pending = false;
  _suppressed_by = SUPPRESS_NONE;
  _last_result = RESULT_WAITING;
  _last_source_sample_count = 0;
  _last_fresh_count = 0;
  _last_required_count = REQUIRED_SAMPLES_DEFAULT;
  _required_samples = REQUIRED_SAMPLES_DEFAULT;
  _drift_seconds = mesh::CLOCK_SYNC_DRIFT_DEFAULT_SECONDS;
  _last_estimate = 0;
  _last_abs_drift = 0;
  _next_attempt_uptime = STARTUP_DELAY_MILLIS;
}

void MeshClockSync::begin(FILESYSTEM* fs) {
  _fs = fs;
  _last_millis = _millis->getMillis();
  loadPrefs();
}

void MeshClockSync::loadPrefs() {
  _mesh_enabled = CLOCK_SYNC_MESH_DEFAULT_ENABLED != 0;
  _mesh_edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0;
  _internet_enabled = false;
  _drift_seconds = mesh::CLOCK_SYNC_DRIFT_DEFAULT_SECONDS;
  _required_samples = REQUIRED_SAMPLES_DEFAULT;

  if (_fs != nullptr && _fs->exists(PREFS_FILE)) {
    File file = openRead(_fs, PREFS_FILE);
    if (file) {
      uint8_t magic[4];
      uint8_t mesh_enabled = 0;
      uint8_t edge_enabled = CLOCK_SYNC_MESH_EDGE_DEFAULT_ENABLED != 0 ? 1 : 0;
      uint8_t internet_enabled = 0;
      uint8_t required_samples = REQUIRED_SAMPLES_DEFAULT;
      uint32_t drift_seconds = 0;
      bool valid = file.read(magic, sizeof(magic)) == sizeof(magic);
      bool version3 = valid && memcmp(magic, "CTS3", sizeof(magic)) == 0;
      bool version4 = valid && memcmp(magic, "CTS4", sizeof(magic)) == 0;
      valid = valid && (version3 || version4)
          && file.read(&mesh_enabled, sizeof(mesh_enabled)) == sizeof(mesh_enabled)
          && file.read(&internet_enabled, sizeof(internet_enabled)) == sizeof(internet_enabled)
          && file.read((uint8_t*)&drift_seconds, sizeof(drift_seconds)) == sizeof(drift_seconds)
          && file.read(&required_samples, sizeof(required_samples)) == sizeof(required_samples);
      if (valid && version4) {
        valid = file.read(&edge_enabled, sizeof(edge_enabled)) == sizeof(edge_enabled);
      }
      valid = valid && mesh_enabled <= 1 && edge_enabled <= 1
          && internet_enabled <= 1
          && drift_seconds >= DRIFT_MIN_SECONDS
          && drift_seconds <= DRIFT_MAX_SECONDS
          && required_samples >= REQUIRED_SAMPLES_MIN
          && required_samples <= REQUIRED_SAMPLES_MAX;
      file.close();
      if (valid) {
        _mesh_enabled = mesh_enabled != 0;
        _mesh_edge_enabled = edge_enabled != 0;
        _internet_enabled = internet_enabled != 0;
        _drift_seconds = drift_seconds;
        _required_samples = required_samples;
      }
    }
  }
  resetAttempt();
}

bool MeshClockSync::savePrefs() {
  if (_fs == nullptr) return false;
  File file = openWrite(_fs, PREFS_FILE);
  if (!file) return false;
  const uint8_t magic[4] = {'C', 'T', 'S', '4'};
  const uint8_t mesh_enabled = _mesh_enabled ? 1 : 0;
  const uint8_t edge_enabled = _mesh_edge_enabled ? 1 : 0;
  const uint8_t internet_enabled = _internet_enabled ? 1 : 0;
  bool success = file.write(magic, sizeof(magic)) == sizeof(magic)
      && file.write(&mesh_enabled, sizeof(mesh_enabled)) == sizeof(mesh_enabled)
      && file.write(&internet_enabled, sizeof(internet_enabled)) == sizeof(internet_enabled)
      && file.write((const uint8_t*)&_drift_seconds, sizeof(_drift_seconds)) == sizeof(_drift_seconds)
      && file.write(&_required_samples, sizeof(_required_samples)) == sizeof(_required_samples)
      && file.write(&edge_enabled, sizeof(edge_enabled)) == sizeof(edge_enabled);
  file.close();
  return success;
}

void MeshClockSync::resetAttempt() {
  _complete = false;
  _force_mesh_pending = false;
  _last_result = RESULT_WAITING;
  _last_source_sample_count = 0;
  _last_fresh_count = 0;
  _last_required_count = _required_samples;
  _last_estimate = 0;
  _last_abs_drift = 0;
  _next_attempt_uptime = _uptime_millis < STARTUP_DELAY_MILLIS
      ? STARTUP_DELAY_MILLIS : _uptime_millis;
}

const char* MeshClockSync::suppressionName(uint8_t source) {
  switch (source) {
    case MeshClockSync::SUPPRESS_CLI: return "cli";
    case MeshClockSync::SUPPRESS_GPS: return "gps";
    case MeshClockSync::SUPPRESS_INTERNET: return "internet";
    default: return "none";
  }
}

void MeshClockSync::suppressForBoot(uint8_t source) {
  if (source == SUPPRESS_NONE || _suppressed_by != SUPPRESS_NONE) return;
  _suppressed_by = source;
  _force_mesh_pending = false;
  memset(_samples, 0, sizeof(_samples));
  MESH_DEBUG_PRINTLN("Clock sync: LoRa estimate suppressed by %s until reboot",
      suppressionName(source));
}

void MeshClockSync::onManualClockSet() {
  suppressForBoot(SUPPRESS_CLI);
}

void MeshClockSync::onInternetClockSet() {
  suppressForBoot(SUPPRESS_INTERNET);
}

void MeshClockSync::checkGpsOverride() {
  LocationProvider* location = _sensors->getLocationProvider();
  if (location != nullptr && location->consumeTimeSyncApplied()) {
    suppressForBoot(SUPPRESS_GPS);
  }
}

bool MeshClockSync::collectionActive() const {
  if (!_mesh_enabled || _suppressed_by != SUPPRESS_NONE) return false;
  if (!_complete) return true;
  if (_next_attempt_uptime == 0) return false;
  if (_uptime_millis >= _next_attempt_uptime) return true;
  return _next_attempt_uptime - _uptime_millis
      <= (uint64_t)SAMPLE_MAX_AGE_MILLIS;
}

uint32_t MeshClockSync::estimateTransitMillis(const Packet* packet) const {
  if (packet == nullptr || _radio == nullptr) return 0;
  uint8_t hops = packet->getPathHashCount();
  uint8_t hash_size = packet->getPathHashSize();
  int base_length = packet->getRawLength() - packet->getPathByteLen();
  if (base_length < 2) return 0;

  uint64_t total = _radio->getEstAirtimeFor(base_length);
  const uint64_t maximum = (uint64_t)CONSENSUS_WINDOW_SECONDS * 1000ULL;
  for (uint8_t relay = 1; relay <= hops; relay++) {
    uint32_t airtime = _radio->getEstAirtimeFor(base_length + relay * hash_size);
    if (*_tx_delay_factor > 0.0f) {
      float expected_delay = (float)airtime * *_tx_delay_factor * 2.5f;
      if (expected_delay > 0.0f) total += (uint32_t)(expected_delay + 0.5f);
    }
    total += airtime;
    if (total >= maximum) return (uint32_t)maximum;
  }
  return (uint32_t)total;
}

void MeshClockSync::recordSample(uint8_t source_kind,
                                 const uint8_t source_id[4], uint32_t epoch,
                                 const Packet* packet) {
  if (!collectionActive() || source_id == nullptr || packet == nullptr
      || !packet->isRouteFlood() || !validEpoch(epoch)) return;

  uint32_t transit_seconds = (estimateTransitMillis(packet) + 500UL) / 1000UL;
  uint32_t maximum = maximumValidEpoch();
  if (epoch > maximum - transit_seconds) return;
  epoch += transit_seconds;

  uint8_t path_id[PATH_ID_SIZE];
  derivePathId(packet, path_id);
  uint32_t now = _millis->getMillis();
  uint32_t received_millis = _radio->getLastRecvMillis();
  if (received_millis == 0 || now - received_millis > 60000UL) received_millis = now;

  int source_slot = -1;
  for (int i = 0; i < SAMPLE_SLOTS; i++) {
    const Sample& sample = _samples[i];
    if (sample.active && sample.source_kind == source_kind
        && memcmp(sample.source_id, source_id, sizeof(sample.source_id)) == 0) {
      source_slot = i;
      break;
    }
  }
  if (source_slot >= 0) {
    const Sample& prior = _samples[source_slot];
    if (prior.epoch == epoch
        && memcmp(prior.path_id, path_id, sizeof(prior.path_id)) == 0) return;
  }

  if (clockSyncRequiresUniquePath(_mesh_edge_enabled)) {
    for (int i = 0; i < SAMPLE_SLOTS; i++) {
      const Sample& sample = _samples[i];
      if (i == source_slot || !sample.active
          || now - sample.received_millis > SAMPLE_MAX_AGE_MILLIS) continue;
      if (memcmp(sample.path_id, path_id, sizeof(sample.path_id)) == 0) return;
    }
  }

  int slot = source_slot;
  int reusable = -1;
  int oldest = 0;
  uint32_t oldest_age = 0;
  for (int i = 0; slot < 0 && i < SAMPLE_SLOTS; i++) {
    const Sample& sample = _samples[i];
    uint32_t age = sample.active ? now - sample.received_millis : 0;
    if ((!sample.active || age > SAMPLE_MAX_AGE_MILLIS) && reusable < 0) reusable = i;
    if (sample.active && age >= oldest_age) {
      oldest_age = age;
      oldest = i;
    }
  }
  if (slot < 0) slot = reusable >= 0 ? reusable : oldest;

  Sample& sample = _samples[slot];
  sample.active = true;
  sample.source_kind = source_kind;
  memcpy(sample.source_id, source_id, sizeof(sample.source_id));
  memcpy(sample.path_id, path_id, sizeof(sample.path_id));
  sample.epoch = epoch;
  sample.received_millis = received_millis;

  if (!_complete) {
    uint32_t estimate_epoch = 0;
    uint8_t fresh = 0;
    uint8_t agreeing = 0;
    uint8_t required = _required_samples;
    estimate(estimate_epoch, fresh, agreeing, required);
    if (fresh >= _required_samples) {
      _last_fresh_count = fresh;
      _last_source_sample_count = agreeing;
      _last_required_count = required;
      _next_attempt_uptime = _uptime_millis;
    }
  }
}

void MeshClockSync::recordPublicChannel(const Packet* packet) {
  if (!collectionActive()) return;
  uint32_t timestamp = 0;
  char sender[32];
  if (!decodePublicPlainText(packet, timestamp, sender, sizeof(sender))) return;
  for (char* p = sender; *p; p++) {
    if (*p >= 'A' && *p <= 'Z') *p = (char)(*p - 'A' + 'a');
  }
  uint8_t source_id[4];
  Utils::sha256(source_id, sizeof(source_id),
                (const uint8_t*)sender, strlen(sender));
  recordSample(CLOCK_SYNC_SAMPLE_SOURCE_PUBLIC_CHANNEL,
               source_id, timestamp, packet);
}

void MeshClockSync::observeVerifiedAdvert(const Packet* packet,
                                          const Identity& id,
                                          uint32_t timestamp) {
  if (!_mesh_edge_enabled || !collectionActive()) return;
  uint8_t source_id[4];
  Utils::sha256(source_id, sizeof(source_id), id.pub_key, PUB_KEY_SIZE);
  recordSample(CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT,
               source_id, timestamp, packet);
}

void MeshClockSync::observeGroupPacket(const Packet* packet) {
  if (_mesh_edge_enabled) recordPublicChannel(packet);
}

void MeshClockSync::observeAcceptedFlood(const Packet* packet) {
  if (_mesh_edge_enabled || !collectionActive() || packet == nullptr) return;
  if (packet->getPayloadType() == PAYLOAD_TYPE_GRP_TXT) {
    recordPublicChannel(packet);
  } else if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    const size_t minimum = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
    if (packet->payload_len < minimum) return;
    uint8_t source_id[4];
    Utils::sha256(source_id, sizeof(source_id), packet->payload, PUB_KEY_SIZE);
    uint32_t timestamp = 0;
    memcpy(&timestamp, &packet->payload[PUB_KEY_SIZE], sizeof(timestamp));
    recordSample(CLOCK_SYNC_SAMPLE_SOURCE_SIGNED_ADVERT,
                 source_id, timestamp, packet);
  }
}

bool MeshClockSync::estimate(uint32_t& epoch, uint8_t& fresh_count,
                             uint8_t& agreeing_count,
                             uint8_t& required_count) const {
  uint32_t values[SAMPLE_SLOTS];
  uint8_t count = 0;
  uint32_t now = _millis->getMillis();
  uint32_t maximum = maximumValidEpoch();
  for (int i = 0; i < SAMPLE_SLOTS; i++) {
    const Sample& sample = _samples[i];
    if (!sample.active) continue;
    uint32_t age_millis = now - sample.received_millis;
    if (age_millis > SAMPLE_MAX_AGE_MILLIS) continue;
    uint32_t age_seconds = age_millis / 1000UL;
    if (sample.epoch > maximum - age_seconds) continue;
    values[count++] = sample.epoch + age_seconds;
  }
  ClockSyncConsensusResult result = evaluateClockSyncConsensus(
      values, count, _required_samples, CONSENSUS_WINDOW_SECONDS);
  fresh_count = result.fresh_count;
  agreeing_count = result.agreeing_count;
  required_count = result.required_count;
  epoch = result.estimate;
  return result.consensus;
}

bool MeshClockSync::applyEstimate(uint32_t epoch, uint8_t sample_count) {
  if (!validEpoch(epoch)) return false;
  if (_callbacks != nullptr && _callbacks->hasAuthoritativeClock()) {
    suppressForBoot(SUPPRESS_INTERNET);
    return true;
  }

  uint32_t old_now = _rtc->getCurrentTime();
  int64_t delta = (int64_t)epoch - (int64_t)old_now;
  uint64_t magnitude = delta < 0 ? (uint64_t)(-delta) : (uint64_t)delta;
  _last_source_sample_count = sample_count;
  _last_estimate = epoch;
  _last_abs_drift = magnitude > UINT32_MAX ? UINT32_MAX : (uint32_t)magnitude;
  _complete = true;
  _next_attempt_uptime = _uptime_millis + RESYNC_INTERVAL_MILLIS;

  if (magnitude <= _drift_seconds) {
    _last_result = RESULT_WITHIN_DRIFT;
    MESH_DEBUG_PRINTLN("Clock sync: within drift (%lu seconds, source=mesh)",
                       (unsigned long)_last_abs_drift);
    return true;
  }

  _rtc->setCurrentTime(epoch);
  _rtc->resetUniqueTime(epoch);
  _last_result = delta > 0 ? RESULT_CORRECTED_FORWARD : RESULT_CORRECTED_BACKWARD;
  for (int i = 0; i < _acl->getNumClients(); i++) {
    ClientInfo* client = _acl->getClientByIdx(i);
    client->last_activity = rebaseTimestamp(client->last_activity, old_now, epoch);
  }
  if (_callbacks != nullptr) _callbacks->onMeshClockAdjusted(old_now, epoch);

  MESH_DEBUG_PRINTLN("Clock sync: corrected %s by %lu seconds (source=mesh samples=%u)",
                     delta > 0 ? "forward" : "backward",
                     (unsigned long)_last_abs_drift,
                     (unsigned int)sample_count);
  return true;
}

void MeshClockSync::checkClock() {
  if (_callbacks != nullptr && _callbacks->hasAuthoritativeClock()) {
    suppressForBoot(SUPPRESS_INTERNET);
  }
  bool mesh_available = _mesh_enabled && _suppressed_by == SUPPRESS_NONE;
  bool force_mesh = _force_mesh_pending && mesh_available;
  if ((!mesh_available && !_internet_enabled)
      || (!force_mesh && _uptime_millis < _next_attempt_uptime)) return;
  if (force_mesh) _force_mesh_pending = false;
  if (_complete) _complete = false;

  if (_internet_enabled && !force_mesh) {
    _last_result = RESULT_INTERNET_UNAVAILABLE;
  }
  if (mesh_available) {
    uint32_t estimate_epoch = 0;
    uint8_t fresh = 0;
    uint8_t agreeing = 0;
    uint8_t required = _required_samples;
    bool consensus = estimate(estimate_epoch, fresh, agreeing, required);
    _last_fresh_count = fresh;
    _last_source_sample_count = agreeing;
    _last_required_count = required;
    if (consensus && applyEstimate(estimate_epoch, agreeing)) return;
    _last_result = fresh < _required_samples
        ? RESULT_COLLECTING : RESULT_NO_CONSENSUS;
  }
  _next_attempt_uptime = _uptime_millis + RETRY_INTERVAL_MILLIS;
}

void MeshClockSync::loop() {
  uint32_t now = _millis->getMillis();
  _uptime_millis += now - _last_millis;
  _last_millis = now;
  checkGpsOverride();
  checkClock();
}

void MeshClockSync::formatSample(int index, char* reply, size_t reply_len) const {
  if (index < 0 || index >= SAMPLE_SLOTS) {
    snprintf(reply, reply_len, "Err - clock sample slot must be 1-%u",
             (unsigned int)SAMPLE_SLOTS);
    return;
  }
  const Sample& sample = _samples[index];
  if (!sample.active) {
    snprintf(reply, reply_len, "> %d empty", index + 1);
    return;
  }

  uint32_t age_seconds = (_millis->getMillis() - sample.received_millis) / 1000UL;
  bool fresh = age_seconds <= SAMPLE_MAX_AGE_MILLIS / 1000UL;
  uint32_t current_epoch = sample.epoch;
  if (age_seconds <= UINT32_MAX - current_epoch) current_epoch += age_seconds;
  uint32_t local_epoch = _rtc->getCurrentTime();
  char delta_sign = current_epoch >= local_epoch ? '+' : '-';
  uint32_t delta = current_epoch >= local_epoch
      ? current_epoch - local_epoch : local_epoch - current_epoch;
  char source_id[sizeof(sample.source_id) * 2 + 1];
  char path_id[sizeof(sample.path_id) * 2 + 1];
  Utils::toHex(source_id, sample.source_id, sizeof(sample.source_id));
  Utils::toHex(path_id, sample.path_id, sizeof(sample.path_id));
  snprintf(reply, reply_len,
           "> %d %s id=%s path=%s age=%lus epoch=%lu delta=%c%lus fresh=%s",
           index + 1, sampleKindName(sample.source_kind), source_id, path_id,
           (unsigned long)age_seconds, (unsigned long)current_epoch,
           delta_sign, (unsigned long)delta, fresh ? "yes" : "no");
}

void MeshClockSync::formatTable(char* reply, size_t reply_len) const {
  uint32_t now = _millis->getMillis();
  uint8_t fresh = 0;
  uint8_t active = 0;
  for (int i = 0; i < SAMPLE_SLOTS; i++) {
    if (!_samples[i].active) continue;
    active++;
    if (now - _samples[i].received_millis <= SAMPLE_MAX_AGE_MILLIS) fresh++;
  }

  const char* mode = _mesh_edge_enabled ? "edge" : "paths";
  size_t used = (size_t)snprintf(
      reply, reply_len, "> %s collect=%s fresh=%u/%u", mode,
      collectionActive() ? "active" : "inactive", (unsigned int)fresh,
      (unsigned int)_required_samples);
  if (active == 0 || used >= reply_len) {
    if (active == 0 && used + 5 < reply_len) {
      StrHelper::strncpy(&reply[used], " none", reply_len - used);
    }
    return;
  }

  for (int i = 0; i < SAMPLE_SLOTS && used + 1 < reply_len; i++) {
    const Sample& sample = _samples[i];
    if (!sample.active) continue;
    uint32_t age_seconds = (now - sample.received_millis) / 1000UL;
    unsigned long age_value = age_seconds < 120UL
        ? (unsigned long)age_seconds : (unsigned long)(age_seconds / 60UL);
    char age_unit = age_seconds < 120UL ? 's' : 'm';
    bool sample_fresh = age_seconds <= SAMPLE_MAX_AGE_MILLIS / 1000UL;
    char item[24];
    snprintf(item, sizeof(item), " %d:%c:%02X%02X:%lu%c%s", i + 1,
             sampleKindCode(sample.source_kind), sample.source_id[0],
             sample.source_id[1], age_value, age_unit,
             sample_fresh ? "" : "!");
    size_t item_len = strlen(item);
    if (used + item_len >= reply_len - 4) {
      StrHelper::strncpy(&reply[used], " ...", reply_len - used);
      return;
    }
    memcpy(&reply[used], item, item_len + 1);
    used += item_len;
  }
}

void MeshClockSync::formatStatus(const char* args, char* reply,
                                 size_t reply_len) const {
  const char* selector = skipSpaces(args);
  if (selector != nullptr && *selector == '.') selector = skipSpaces(selector + 1);
  if (selector != nullptr && *selector != 0) {
    if (strcmp(selector, "table") == 0) {
      formatTable(reply, reply_len);
      return;
    }
    int slot = 0;
    if (parsePositiveSelector(selector, slot) && slot <= SAMPLE_SLOTS) {
      formatSample(slot - 1, reply, reply_len);
      return;
    }
    snprintf(reply, reply_len,
             "Err - use get clock.sync.status[.table|.1-.%u]",
             (unsigned int)SAMPLE_SLOTS);
    return;
  }

  uint8_t active = 0;
  for (int i = 0; i < SAMPLE_SLOTS; i++) {
    if (_samples[i].active) active++;
  }
  uint32_t live_estimate = 0;
  uint8_t fresh = 0;
  uint8_t agreeing = 0;
  uint8_t required = _required_samples;
  bool live_consensus = estimate(live_estimate, fresh, agreeing, required);
  bool mesh_available = _mesh_enabled && _suppressed_by == SUPPRESS_NONE;
  const char* mesh_state = !_mesh_enabled ? "off"
      : (mesh_available ? "on"
          : (_suppressed_by == SUPPRESS_CLI ? "suppressed-cli"
              : (_suppressed_by == SUPPRESS_GPS ? "suppressed-gps"
                  : "suppressed-internet")));
  const char* mode = _mesh_edge_enabled ? "edge" : "paths";
  const char* evidence = _mesh_edge_enabled ? "sources" : "paths";
  uint64_t remaining_ms = _next_attempt_uptime > _uptime_millis
      ? _next_attempt_uptime - _uptime_millis : 0;
  unsigned long next_seconds = (unsigned long)((remaining_ms + 999ULL) / 1000ULL);
  if (!mesh_available && !_internet_enabled) {
    const char* reason = _mesh_enabled ? mesh_state : "mesh-off";
    snprintf(reply, reply_len,
             "> not-set reason=%s collect=inactive mode=%s %s=%u/%u table=%u",
             reason, mode, evidence, (unsigned int)fresh,
             (unsigned int)_required_samples, (unsigned int)active);
    return;
  }

  const char* result = "waiting";
  switch (_last_result) {
    case RESULT_COLLECTING: result = "collecting"; break;
    case RESULT_INTERNET_UNAVAILABLE: result = "internet-unavailable"; break;
    case RESULT_NO_CONSENSUS: result = "no-consensus"; break;
    case RESULT_WITHIN_DRIFT: result = "within-drift"; break;
    case RESULT_CORRECTED_FORWARD: result = "corrected-forward"; break;
    case RESULT_CORRECTED_BACKWARD: result = "corrected-backward"; break;
    default: break;
  }
  if (_complete) {
    bool clock_was_set = _last_result == RESULT_CORRECTED_FORWARD
        || _last_result == RESULT_CORRECTED_BACKWARD;
    snprintf(reply, reply_len,
             "> %s reason=%s difference=%lus threshold=%lus via=mesh collect=%s mode=%s table=%u next=%lus",
             clock_was_set ? "set" : "not-set", result,
             (unsigned long)_last_abs_drift, (unsigned long)_drift_seconds,
             collectionActive() ? "active" : "inactive", mode,
             (unsigned int)active, next_seconds);
  } else if (_last_result == RESULT_NO_CONSENSUS
             || (fresh >= _required_samples && !live_consensus)) {
    snprintf(reply, reply_len,
             "> not-set reason=no-consensus collect=%s mode=%s %s=%u agree=%u/%u table=%u next=%lus",
             collectionActive() ? "active" : "inactive", mode, evidence,
             (unsigned int)fresh, (unsigned int)agreeing,
             (unsigned int)required, (unsigned int)active, next_seconds);
  } else if (live_consensus) {
    snprintf(reply, reply_len,
             "> not-set reason=ready collect=%s mode=%s %s=%u agree=%u/%u table=%u next=%lus",
             collectionActive() ? "active" : "inactive", mode, evidence,
             (unsigned int)fresh, (unsigned int)agreeing,
             (unsigned int)required, (unsigned int)active, next_seconds);
  } else {
    const char* reason = result;
    if (_last_result == RESULT_WAITING) reason = "waiting-deadline";
    else if (_last_result == RESULT_COLLECTING) {
      reason = _mesh_edge_enabled ? "need-more-sources" : "need-more-paths";
    }
    snprintf(reply, reply_len,
             "> not-set reason=%s collect=%s mesh=%s mode=%s %s=%u/%u table=%u next=%lus",
             reason, collectionActive() ? "active" : "inactive", mesh_state,
             mode, evidence, (unsigned int)fresh,
             (unsigned int)_required_samples, (unsigned int)active,
             next_seconds);
  }
}

bool MeshClockSync::handleCommand(const char* command, char* reply,
                                  size_t reply_len) {
  if (command == nullptr || reply == nullptr || reply_len == 0) return false;
  if (strncmp(command, "get clock.sync.status", 21) == 0
      && (command[21] == 0 || command[21] == '.')) {
    formatStatus(command + 21, reply, reply_len);
  } else if (strcmp(command, "get clock.sync") == 0) {
    formatStatus("", reply, reply_len);
  } else if (strcmp(command, "get clock.sync.mesh") == 0) {
    if (_mesh_enabled && _suppressed_by != SUPPRESS_NONE) {
      snprintf(reply, reply_len, "> on (suppressed by %s until reboot)",
               suppressionName(_suppressed_by));
    } else {
      snprintf(reply, reply_len, "> %s", _mesh_enabled ? "on" : "off");
    }
  } else if (strcmp(command, "get clock.sync.mesh.edge") == 0) {
    snprintf(reply, reply_len, "> %s", _mesh_edge_enabled ? "on" : "off");
  } else if (strcmp(command, "get clock.sync.internet") == 0) {
    snprintf(reply, reply_len, "> %s (unavailable on this build)",
             _internet_enabled ? "on" : "off");
  } else if (strcmp(command, "get clock.sync.drift") == 0) {
    snprintf(reply, reply_len, "> %lu", (unsigned long)_drift_seconds);
  } else if (strcmp(command, "get clock.sync.samples") == 0) {
    snprintf(reply, reply_len, "> %u", (unsigned int)_required_samples);
  } else if (strcmp(command, "clock.sync.mesh now") == 0) {
    if (!_mesh_enabled) {
      StrHelper::strncpy(reply, "Err - mesh clock sync is off", reply_len);
    } else if (_suppressed_by != SUPPRESS_NONE) {
      snprintf(reply, reply_len, "Err - mesh sync suppressed by %s until reboot",
               suppressionName(_suppressed_by));
    } else {
      resetAttempt();
      _force_mesh_pending = true;
      _next_attempt_uptime = _uptime_millis;
      StrHelper::strncpy(reply, "OK - mesh clock sync queued", reply_len);
    }
  } else if (strncmp(command, "set clock.sync.mesh ", 20) == 0) {
    const char* value = command + 20;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      StrHelper::strncpy(reply, "Err - usage: set clock.sync.mesh <on|off>", reply_len);
      return true;
    }
    bool previous = _mesh_enabled;
    _mesh_enabled = enabled;
    if (!savePrefs()) {
      _mesh_enabled = previous;
      StrHelper::strncpy(reply, "Err - unable to save clock sync settings", reply_len);
    } else {
      if (enabled && !previous) memset(_samples, 0, sizeof(_samples));
      resetAttempt();
      if (enabled && _suppressed_by != SUPPRESS_NONE) {
        snprintf(reply, reply_len, "OK - enabled; suppressed by %s until reboot",
                 suppressionName(_suppressed_by));
      } else {
        StrHelper::strncpy(reply, enabled ? "OK - mesh clock sync enabled"
                                           : "OK - mesh clock sync disabled",
                           reply_len);
      }
    }
  } else if (strncmp(command, "set clock.sync.mesh.edge ", 25) == 0) {
    const char* value = command + 25;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      StrHelper::strncpy(reply, "Err - usage: set clock.sync.mesh.edge <on|off>", reply_len);
      return true;
    }
    bool previous = _mesh_edge_enabled;
    _mesh_edge_enabled = enabled;
    if (!savePrefs()) {
      _mesh_edge_enabled = previous;
      StrHelper::strncpy(reply, "Err - unable to save clock sync settings", reply_len);
    } else {
      if (enabled != previous) memset(_samples, 0, sizeof(_samples));
      resetAttempt();
      StrHelper::strncpy(reply, enabled ? "OK - edge clock sync enabled"
                                       : "OK - edge clock sync disabled",
                         reply_len);
    }
  } else if (strncmp(command, "set clock.sync.internet ", 24) == 0) {
    const char* value = command + 24;
    bool enabled;
    if (strcmp(value, "on") == 0) enabled = true;
    else if (strcmp(value, "off") == 0) enabled = false;
    else {
      StrHelper::strncpy(reply, "Err - usage: set clock.sync.internet <on|off>", reply_len);
      return true;
    }
    bool previous = _internet_enabled;
    _internet_enabled = enabled;
    if (!savePrefs()) {
      _internet_enabled = previous;
      StrHelper::strncpy(reply, "Err - unable to save clock sync settings", reply_len);
    } else {
      resetAttempt();
      StrHelper::strncpy(reply,
          enabled ? "OK - enabled (internet unavailable on this build)"
                  : "OK - internet clock sync disabled",
          reply_len);
    }
  } else if (strncmp(command, "set clock.sync.drift ", 21) == 0) {
    uint32_t drift = 0;
    if (!parseUnsigned(command + 21, DRIFT_MAX_SECONDS, drift)
        || drift < DRIFT_MIN_SECONDS) {
      StrHelper::strncpy(reply, "Err - drift must be 30-86400 seconds", reply_len);
    } else {
      uint32_t previous = _drift_seconds;
      _drift_seconds = drift;
      if (!savePrefs()) {
        _drift_seconds = previous;
        StrHelper::strncpy(reply, "Err - unable to save clock sync settings", reply_len);
      } else {
        resetAttempt();
        snprintf(reply, reply_len, "OK - clock drift threshold %lu seconds",
                 (unsigned long)drift);
      }
    }
  } else if (strncmp(command, "set clock.sync.samples ", 23) == 0) {
    uint32_t required = 0;
    if (!parseUnsigned(command + 23, REQUIRED_SAMPLES_MAX, required)
        || required < REQUIRED_SAMPLES_MIN) {
      StrHelper::strncpy(reply, "Err - samples must be 3-16", reply_len);
    } else {
      uint8_t previous = _required_samples;
      _required_samples = (uint8_t)required;
      if (!savePrefs()) {
        _required_samples = previous;
        StrHelper::strncpy(reply, "Err - unable to save clock sync settings", reply_len);
      } else {
        resetAttempt();
        snprintf(reply, reply_len, "OK - clock sync requires %u samples",
                 (unsigned int)required);
      }
    }
  } else {
    return false;
  }
  return true;
}

}  // namespace mesh
