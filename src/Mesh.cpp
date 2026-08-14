#include "Mesh.h"
//#include <Arduino.h>
#if defined(ENABLE_OTA)
#include "helpers/ota/OtaContext.h"   // OTA mesh-integration is centralized here so every role gets it
#include "helpers/ota/OtaProtocol.h"  // decode_adv -> the `ota neighbors` discovery table
#include "helpers/ota/OtaSelf.h"      // ota_self_firmware -> auto-advertise our own image
#ifndef OTA_ANNOUNCE_BOOT_MS
#define OTA_ANNOUNCE_BOOT_MS      8000UL      // first self-advert ~8 s after boot (settled, but quick to discover)
#endif
#ifndef OTA_ANNOUNCE_BURST
#define OTA_ANNOUNCE_BURST        4           // a few closely-spaced boot adverts so co-booting peers catch one
#endif
#ifndef OTA_ANNOUNCE_BURST_MS
#define OTA_ANNOUNCE_BURST_MS     20000UL     // spacing during the boot burst (~1 min total), then ...
#endif
// ... then re-announce at a FIXED cadence so a long-running node stays discoverable (a fresh `ota ls`
// neighbour eventually sees it, not at boot only). The cadence is OtaManager::advert_mins() minutes (default
// 24h, runtime-tunable via `ota config advert` + persisted; 0 = disabled = boot burst only). The beacon is
// tiny + lowest-priority + duty-gated, so even a frequent cadence is cheap.
#ifndef OTA_ANNOUNCE_DISABLED_POLL_MS
#define OTA_ANNOUNCE_DISABLED_POLL_MS  600000UL  // when periodic advert is off, re-check config every 10 min
#endif
#endif

namespace mesh {

static const uint8_t DIRECT_RETRY_MAX_ATTEMPTS_DEFAULT = 15;
static const uint8_t DIRECT_RETRY_MAX_ATTEMPTS_HARD_MAX = 21;
static const uint8_t FLOOD_RETRY_MAX_ATTEMPTS_DEFAULT = 15;
static const uint8_t FLOOD_RETRY_MAX_ATTEMPTS_HARD_MAX = 15;
static const uint8_t FLOOD_RETRY_MAX_PATH_DEFAULT = 1;
static const uint32_t ORIGIN_ADVERT_RETRY_EXTRA_DELAY_MS = 60UL * 1000UL;
static const uint32_t RECENT_ADVERT_MAX_AGE_SECONDS = 6UL * 60UL * 60UL;
static const uint32_t FORWARDED_ADVERT_ECHO_WATCH_MS = 5UL * 60UL * 1000UL;

static bool hasValidEncryptedPayloadLength(uint16_t payload_len, uint16_t clear_prefix_len) {
  const uint16_t overhead = clear_prefix_len + CIPHER_MAC_SIZE;
  if (payload_len < overhead + CIPHER_BLOCK_SIZE) return false;
  return ((payload_len - overhead) % CIPHER_BLOCK_SIZE) == 0;
}

static uint8_t decodeTraceHashSize(uint8_t flags, uint8_t route_bytes) {
  uint8_t code = flags & 0x03;
  uint8_t size_pow2 = (uint8_t)(1U << code);   // legacy TRACE interpretation
  uint8_t size_linear = (uint8_t)(code + 1U);  // packed-size interpretation (1..4)

  bool pow2_ok = size_pow2 > 0 && (route_bytes % size_pow2) == 0;
  bool linear_ok = size_linear > 0 && (route_bytes % size_linear) == 0;

  if (pow2_ok && !linear_ok) {
    return size_pow2;
  }
  if (linear_ok && !pow2_ok) {
    return size_linear;
  }
  if (pow2_ok) {
    return size_pow2;
  }
  return size_linear;
}

static uint8_t getTraceRemainingHops(const Packet* packet) {
  if (packet == NULL || packet->payload_len < 9) {
    return 0;
  }

  uint8_t route_bytes = packet->payload_len - 9;
  uint8_t hash_size = decodeTraceHashSize(packet->payload[8], route_bytes);
  if (hash_size == 0) {
    return 0;
  }

  uint8_t route_hops = route_bytes / hash_size;
  if (packet->path_len >= route_hops) {
    return 0;
  }
  return route_hops - packet->path_len;
}

static uint8_t getTraceDirectPriority(const Packet* packet) {
  uint8_t remaining_hops = getTraceRemainingHops(packet);
  if (remaining_hops == 0) {
    return 5;
  }
  if (remaining_hops <= 4) {
    return 1;
  }
  if (remaining_hops <= 8) {
    return 2;
  }
  if (remaining_hops <= 12) {
    return 3;
  }
  return 5;
}

uint8_t Mesh::getDirectRetryCodingRateForAttempt(uint8_t start_cr, uint8_t retry_attempt) {
  if (start_cr < 4 || start_cr > 8) {
    return start_cr;
  }

  if (retry_attempt < 1) {
    retry_attempt = 1;
  }

  if (start_cr >= 8) {
    return 8;
  }
  if (start_cr >= 7) {
    return retry_attempt <= 2 ? 7 : 8;
  }
  if (start_cr <= 4) {
    if (retry_attempt == 1) return 4;
    if (retry_attempt == 2) return 5;
    if (retry_attempt <= 4) return 7;
    return 8;
  }

  if (retry_attempt == 1) return start_cr;
  if (retry_attempt <= 3) return 7;
  return 8;
}

void Mesh::configureDirectRetryPacket(Packet* retry, const Packet* original, uint8_t retry_attempt) {
  (void)original;
  if (retry == NULL) {
    return;
  }

  uint8_t default_cr = getDefaultTxCodingRate();
  if (default_cr < 4 || default_cr > 8) {
    return;
  }

  retry->tx_cr = getDirectRetryCodingRateForAttempt(default_cr, retry_attempt);
}
#if defined(ENABLE_OTA)
// Adapter so the portable OtaManager can emit packets through the mesh (lowest priority, hop-capped).
void Mesh::otaSendAdapter(void* ctx, const uint8_t* msg, uint16_t len, bool /*flood*/) {
  Mesh* m = (Mesh*)ctx;
  if (!m->isTempRadioActive()) return;
  Packet* p = m->createOtaPacket(msg, len);
  if (p) m->sendOtaFlood(p);
}

// Runtime OTA flood reach (`ota config hops`, persisted in NodePrefs): accept packets up to N hops away and
// relay those still under N hops. 0 = direct only. Overridable per-role by subclassing.
uint8_t Mesh::getOtaHopLimit() const { return ota::ota_ctx().manager.max_hops(); }
#endif

void Mesh::begin() {
  _active_direct_retry_count = 0;
  _active_flood_retry_count = 0;
  _waiting_direct_retry_count = 0;
  _waiting_flood_retry_count = 0;
  _next_recent_advert_echo = 0;
  _next_direct_retry_timeout = 0;
  _next_flood_retry_timeout = 0;
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    _direct_retries[i].packet = NULL;
    _direct_retries[i].trigger_packet = NULL;
    _direct_retries[i].retry_started_at = 0;
    _direct_retries[i].echo_wait_started_at = 0;
    _direct_retries[i].retry_at = 0;
    _direct_retries[i].retry_delay = 0;
    _direct_retries[i].retry_attempts_sent = 0;
    memset(_direct_retries[i].retry_key, 0, sizeof(_direct_retries[i].retry_key));
    memset(_direct_retries[i].trace_replacement_key, 0, sizeof(_direct_retries[i].trace_replacement_key));
    memset(_direct_retries[i].next_hop_hash, 0, sizeof(_direct_retries[i].next_hop_hash));
    _direct_retries[i].next_hop_hash_len = 0;
    _direct_retries[i].payload_type = 0;
    _direct_retries[i].priority = 0;
    _direct_retries[i].progress_marker = 0;
    _direct_retries[i].expect_path_growth = false;
    _direct_retries[i].final_hop_retry = false;
    _direct_retries[i].waiting_final_echo = false;
    _direct_retries[i].queued = false;
    _direct_retries[i].active = false;
  }
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    _flood_retries[i].packet = NULL;
    _flood_retries[i].trigger_packet = NULL;
    _flood_retries[i].retry_started_at = 0;
    _flood_retries[i].retry_at = 0;
    _flood_retries[i].retry_delay = 0;
    _flood_retries[i].retry_attempts_sent = 0;
    memset(_flood_retries[i].retry_key, 0, sizeof(_flood_retries[i].retry_key));
    _flood_retries[i].priority = 0;
    _flood_retries[i].progress_marker = 0;
    _flood_retries[i].self_advert = false;
    _flood_retries[i].waiting_final_echo = false;
    _flood_retries[i].queued = false;
    _flood_retries[i].active = false;
  }
  for (int i = 0; i < MAX_RECENT_ADVERT_ECHOS; i++) {
    memset(_recent_advert_echoes[i].packet_hash, 0,
           sizeof(_recent_advert_echoes[i].packet_hash));
    _recent_advert_echoes[i].advert_timestamp = 0;
    _recent_advert_echoes[i].watch_started_at = 0;
    _recent_advert_echoes[i].progress_marker = 0;
    _recent_advert_echoes[i].confirmed = false;
    _recent_advert_echoes[i].valid = false;
  }
  Dispatcher::begin();
#if defined(ENABLE_OTA)
  uint32_t my_tid = 0;
  #ifdef MOTA_TARGET_ID
    my_tid = (uint32_t)(MOTA_TARGET_ID);   // sha2-256:4(env name), injected by build.sh
  #endif
  const char* my_hw = "";
  #ifdef MOTA_HW_ID
    my_hw = MOTA_HW_ID;                     // human-readable hardware tag (per-variant), for the apply hw gate
  #endif
  ota::ota_ctx().begin(my_tid, Mesh::otaSendAdapter, this, my_hw);   // also sets the platform apply codec
  ota::ota_ctx().manager.set_seeder_id(self_id.pub_key);      // node id (pubkey[0:4]) for advert seeder count
#endif
}

void Mesh::loop() {
  Dispatcher::loop();
  serviceLoopMaintenance();
}

void __attribute__((noinline)) Mesh::serviceLoopMaintenance() {
  if (_waiting_direct_retry_count != 0
      && millisHasNowPassed(_next_direct_retry_timeout)) {
    for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
      if (!_direct_retries[i].active || !_direct_retries[i].waiting_final_echo) {
        continue;
      }
      if (!millisHasNowPassed(_direct_retries[i].retry_at)) {
        continue;
      }

      uint32_t elapsed_millis = _direct_retries[i].retry_started_at == 0
        ? 0
        : (uint32_t)(_ms->getMillis() - _direct_retries[i].retry_started_at);
      onDirectRetryEvent("failed_all_tries", _direct_retries[i].packet, elapsed_millis, _direct_retries[i].retry_attempts_sent,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len,
                         _direct_retries[i].payload_type);
      onDirectRetryEvent("failure", _direct_retries[i].packet, elapsed_millis, _direct_retries[i].retry_attempts_sent,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len,
                         _direct_retries[i].payload_type);
      onDirectRetryFailed(_direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      clearDirectRetrySlot(i);
    }
  }

  if (_waiting_flood_retry_count != 0
      && millisHasNowPassed(_next_flood_retry_timeout)) {
    for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
      if (!_flood_retries[i].active || !_flood_retries[i].waiting_final_echo) {
        continue;
      }
      if (!millisHasNowPassed(_flood_retries[i].retry_at)) {
        continue;
      }

      uint32_t elapsed_millis = _flood_retries[i].retry_started_at == 0
        ? 0
        : (uint32_t)(_ms->getMillis() - _flood_retries[i].retry_started_at);
      onFloodRetryEvent("failed_all_tries", _flood_retries[i].packet, elapsed_millis, _flood_retries[i].retry_attempts_sent);
      onFloodRetryEvent("failure", _flood_retries[i].packet, elapsed_millis, _flood_retries[i].retry_attempts_sent);
      clearFloodRetrySlot(i);
    }
  }
#if defined(ENABLE_OTA)
  // Deferred apply-reboot: a verified `ota applydelta` approves the update but does NOT reboot inline,
  // so its "verified; applying" reply can be delivered first (over LoRa that reply is the operator's
  // only confirmation the apply started). Reboot once that reply has actually been transmitted (the
  // outbound queue drains) after a short grace to let it be queued, with a hard cap for a busy node
  // whose queue never idles.
  {
    ota::OtaContext& oc = ota::ota_ctx();
    if (oc.apply_pending) {
      if (oc.apply_at == 0) {
        oc.apply_at = futureMillis(1500);
        oc.apply_hard = futureMillis(15000);
      } else if (millisHasNowPassed(oc.apply_at) &&
                 (_mgr->getOutboundTotal() == 0 || millisHasNowPassed(oc.apply_hard))) {
        ota::ota_reboot_to_apply();          // does not return
      }
    }
  }
  const bool ota_active = isTempRadioActive();
  if (!ota_active) {
    _ota_temp_was_active = false;
    return;
  }
  if (!_ota_temp_was_active) {
    _ota_temp_was_active = true;
    _next_ota_tick = 0;
    _next_ota_announce = 0;
    _ota_announce_count = 0;
  }
  if (millisHasNowPassed(_next_ota_tick)) {
    // one-shot on first tick: resume an interrupted fetch left staged in flash before a reboot. Only adopt
    // a PARTIAL container (continue fetching the holes); a COMPLETE one is left for manual/auto-install,
    // not re-adopted at boot. requestMissing() (inside resumeStaged) drives the rest via REQ/DATA.
    if (!_ota_resumed) {
      _ota_resumed = true;
      ota::OtaContext& oc = ota::ota_ctx();
      if (oc.manager.fetchState() == ota::OtaManager::IDLE && oc.manager.resumeStaged(nullptr)
          && oc.manager.fetchState() == ota::OtaManager::COMPLETE) {
        oc.manager.reset_session();        // don't auto-adopt a complete staged container on boot
      }
    }
    ota::ota_ctx().manager.set_clock(_ms->getMillis());   // for discovery jitter/ages + the pending-query timer
    ota::ota_ctx().manager.loop();         // re-request still-missing OTA blocks + fire scheduled queries
    _next_ota_tick = futureMillis(OTA_RETRY_TICK_MS);
  }
  if (millisHasNowPassed(_next_ota_announce)) {   // auto-advertise so peers discover us (tiny beacon)
    ota::OtaContext& oc = ota::ota_ctx();
    bool in_burst = _ota_announce_count < OTA_ANNOUNCE_BURST;
    uint32_t mins = oc.manager.advert_mins();     // periodic cadence in minutes; 0 = disabled (boot burst only)
    if (in_burst || mins != 0) {
      // To be discoverable as a source of our OWN firmware, set up flash-backed self-serve once; then the
      // beacon (announce) advertises our served set and peers can QUERY + fetch it.
      if (!oc.serving) oc.serving = ota::ota_serve_self(oc, 0);
      oc.manager.announce();
      if (_ota_announce_count < 250) _ota_announce_count++;
    }
    // Re-arm: tight spacing during the boot burst; afterwards the fixed cadence (default 24h). When periodic
    // advert is disabled (0), re-check on a slow timer so a later `ota config advert <mins>` takes effect live.
    uint32_t gap = in_burst       ? OTA_ANNOUNCE_BURST_MS
                 : (mins != 0)    ? mins * 60000UL
                                  : OTA_ANNOUNCE_DISABLED_POLL_MS;
    _next_ota_announce = futureMillis(gap);
  }
  {   // auto-install (once per COMPLETE fetch): only signed images, and apply_fetched enforces trust
    ota::OtaContext& oc = ota::ota_ctx();
    if (oc.manager.fetchState() != ota::OtaManager::COMPLETE) {
      _ota_autoinstall_tried = false;
    } else if (!_ota_autoinstall_tried && !oc.apply_pending
               && oc.autoinstall == ota::OtaContext::AUTOINSTALL_TRUSTED
               && oc.manager.fetched_is_signed()) {
      _ota_autoinstall_tried = true;
      char msg[100];
      oc.apply_fetched(msg);   // arms + sets apply_pending only if signed & allowlisted; refused otherwise
    }
  }
#endif
}

bool Mesh::allowPacketTransmit(const Packet* packet) const {
#if defined(ENABLE_OTA)
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_OTA
      && !isTempRadioActive()) {
    return false;
  }
#endif
  if (packet != NULL && _active_flood_retry_count != 0) {
    for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
      if (!_flood_retries[i].active || !_flood_retries[i].queued
          || _flood_retries[i].packet != packet) {
        continue;
      }
      uint8_t max_attempts = getEligibleFloodRetryMaxAttempts(packet);
      return max_attempts > _flood_retries[i].retry_attempts_sent;
    }
  }
  return true;
}

bool Mesh::allowPacketForward(const mesh::Packet* packet) { 
  return false;  // by default, Transport NOT enabled
}
uint32_t Mesh::getRetransmitDelay(const mesh::Packet* packet) { 
  uint32_t t = (_radio->getEstAirtimeFor(packet->getRawLength()) * 52 / 50) / 2;

  return _rng->nextInt(0, 5)*t;
}
uint32_t Mesh::getDirectRetransmitDelay(const Packet* packet) {
  return 0;  // by default, no delay
}
bool Mesh::allowDirectRetry(const Packet* packet, const uint8_t* next_hop_hash, uint8_t next_hop_hash_len) const {
  (void)packet;
  (void)next_hop_hash;
  (void)next_hop_hash_len;
  return true;
}
uint8_t Mesh::getDirectRetryPacketAirtimeFactor(const Packet* packet) const {
  if (packet == NULL) {
    return 6;
  }

  uint8_t payload_type = packet->getPayloadType();
  if (payload_type == PAYLOAD_TYPE_TRACE || payload_type == PAYLOAD_TYPE_ANON_REQ) {
    return 3;
  }
  if (payload_type == PAYLOAD_TYPE_TXT_MSG) {
    return 7;
  }
  return 6;
}
uint32_t Mesh::getDirectRetryPacketAirtimeDelay(const Packet* packet) const {
  if (packet == NULL || _radio == NULL) {
    return 0;
  }

  return _radio->getEstAirtimeFor(packet->getRawLength()) * (uint32_t)getDirectRetryPacketAirtimeFactor(packet);
}
uint32_t Mesh::getDirectRetryEchoDelay(const Packet* packet) const {
  return 200 + getDirectRetryPacketAirtimeDelay(packet);
}
uint8_t Mesh::getDirectRetryMaxAttempts(const Packet* packet) const {
  if (packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
    return 21;
  }
  return DIRECT_RETRY_MAX_ATTEMPTS_DEFAULT;
}
uint32_t Mesh::getDirectRetryAttemptDelay(const Packet* packet, uint8_t attempt_idx) {
  uint32_t base = getDirectRetryEchoDelay(packet);
  // Keep the historical linear spacing while allowing the base wait to vary by platform/profile.
  return base + ((uint32_t)attempt_idx * 100UL);
}
bool Mesh::allowFloodRetry(const Packet* packet) const {
  (void)packet;
  return true;
}
bool Mesh::isSelfOriginAdvert(const Packet* packet) const {
  return packet != NULL && packet->getPayloadType() == PAYLOAD_TYPE_ADVERT
      && packet->getPathHashCount() == 0 && packet->payload_len >= PUB_KEY_SIZE
      && self_id.matches(packet->payload);
}
bool Mesh::hasFloodRetryTargetPrefix(const Packet* packet) const {
  (void)packet;
  return false;
}
uint8_t Mesh::getFloodRetryMaxPathLength(const Packet* packet) const {
  (void)packet;
  return FLOOD_RETRY_MAX_PATH_DEFAULT;
}
uint8_t Mesh::applyGroupDataFloodRetryPathGate(const Packet* packet,
                                               uint8_t general_gate,
                                               uint8_t group_data_gate) {
  if (packet == NULL || packet->getPayloadType() != PAYLOAD_TYPE_GRP_DATA
      || group_data_gate == FLOOD_RETRY_PATH_GATE_DISABLED) {
    return general_gate;
  }
  if (general_gate == FLOOD_RETRY_PATH_GATE_DISABLED || group_data_gate < general_gate) {
    return group_data_gate;
  }
  return general_gate;
}
uint8_t Mesh::applyFloodRetryAttemptPolicy(const Packet* packet,
                                           uint8_t role_max_attempts) {
  uint8_t attempts = role_max_attempts > FLOOD_RETRY_MAX_ATTEMPTS_HARD_MAX
      ? FLOOD_RETRY_MAX_ATTEMPTS_HARD_MAX
      : role_max_attempts;
  if (attempts == 0 || packet == NULL) {
    return attempts;
  }

  switch (packet->getPayloadType()) {
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_OTA:
      return 0;
    case PAYLOAD_TYPE_GRP_TXT:
      return attempts;
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG:
    case PAYLOAD_TYPE_ANON_REQ:
    case PAYLOAD_TYPE_PATH:
      return packet->getPathHashCount() == 0 || attempts <= 2 ? attempts : 2;
    default:
      return attempts > 1 ? 1 : attempts;
  }
}
uint8_t Mesh::getFloodRetryMaxAttempts(const Packet* packet) const {
  (void)packet;
  return FLOOD_RETRY_MAX_ATTEMPTS_DEFAULT;
}
uint8_t Mesh::getEffectiveFloodRetryMaxAttempts(const Packet* packet) const {
  return applyFloodRetryAttemptPolicy(packet, getFloodRetryMaxAttempts(packet));
}
uint8_t Mesh::getEligibleFloodRetryMaxAttempts(const Packet* packet) const {
  if (packet == NULL || !packet->isRouteFlood()) {
    return 0;
  }

  uint8_t max_attempts = getEffectiveFloodRetryMaxAttempts(packet);
  if (max_attempts == 0 || !allowFloodRetry(packet)
      || hasFloodRetryTargetPrefix(packet)) {
    return 0;
  }

  uint8_t max_path_len = getFloodRetryMaxPathLength(packet);
  if (max_path_len != FLOOD_RETRY_PATH_GATE_DISABLED
      && packet->getPathHashCount() > max_path_len) {
    return 0;
  }
  return max_attempts;
}
uint32_t Mesh::getFloodRetryAttemptDelay(const Packet* packet, uint8_t attempt_idx) {
  (void)attempt_idx;
  if (packet == NULL) {
    return _radio->getEstAirtimeFor(MAX_TRANS_UNIT);
  }

  uint32_t max_packet_airtime = _radio->getEstAirtimeFor(MAX_TRANS_UNIT);
  uint32_t packet_airtime = _radio->getEstAirtimeFor(packet->getRawLength());
  uint32_t jitter_percent = _rng->nextInt(0, 201);
  uint32_t jitter = (packet_airtime * jitter_percent) / 100UL;
  uint32_t delay = max_packet_airtime + (20UL * packet_airtime) + jitter;
  if (isSelfOriginAdvert(packet)) {
    delay += ORIGIN_ADVERT_RETRY_EXTRA_DELAY_MS;
  }
  return delay;
}
uint8_t Mesh::getExtraAckTransmitCount() const {
  return 0;
}

void Mesh::onSendComplete(Packet* packet) {
  watchForwardedAdvertEcho(packet);
  armDirectRetryOnSendComplete(packet);
  armFloodRetryOnSendComplete(packet);
}

void Mesh::onTracePacketQueuedForSend(Packet* packet) {
  replaceQueuedTraceRetries(packet);
}

void Mesh::onSendFail(Packet* packet) {
  clearPendingDirectRetryOnSendFail(packet);
  clearPendingFloodRetryOnSendFail(packet);
}

uint32_t Mesh::getCADFailRetryDelay() const {
  if (!isTempRadioActive()) return _rng->nextInt(1, 4) * 120;
  uint32_t airtime = _radio->getEstAirtimeFor(MAX_TRANS_UNIT);
  uint32_t retry = airtime / 4;
  if (retry < 5) retry = 5;
  if (retry > 50) retry = 50;
  return retry;
}

int Mesh::searchPeersByHash(const uint8_t* hash) {
  return 0;  // not found
}

int Mesh::searchChannelsByHash(const uint8_t* hash, GroupChannel channels[], int max_matches) {
  return 0;  // not found
}

DispatcherAction Mesh::onRecvPacket(Packet* pkt) {
  observeForwardedAdvertEcho(pkt);
  if (pkt->isRouteDirect()) {
    cancelDirectRetryOnEcho(pkt);
  } else if (pkt->isRouteFlood()) {
    cancelFloodRetryOnEcho(pkt);
  }

  if (pkt->isRouteDirect() && pkt->getPayloadType() == PAYLOAD_TYPE_TRACE) {
    if (pkt->payload_len < 9) {
      MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete TRACE packet", getLogDateTime());
      return ACTION_RELEASE;
    }
    if (pkt->path_len < MAX_PATH_SIZE) {
      uint8_t i = 0;
      uint32_t trace_tag;
      memcpy(&trace_tag, &pkt->payload[i], 4); i += 4;
      uint32_t auth_code;
      memcpy(&auth_code, &pkt->payload[i], 4); i += 4;
      uint8_t flags = pkt->payload[i++];
      uint8_t len = pkt->payload_len - i;
      uint8_t hash_size = decodeTraceHashSize(flags, len);
      // path_len*entry_size can exceed 255 (path_len up to 63, entry_size up to 8);
      // a uint8_t offset would wrap and steer the isHashMatch() read to the wrong place.
      uint16_t offset = (uint16_t)pkt->path_len * (uint16_t)hash_size;
      if (offset >= len) {   // TRACE has reached end of given path
        onTraceRecv(pkt, trace_tag, auth_code, flags, pkt->path, &pkt->payload[i], len);
      } else if (hash_size > 0 && offset + hash_size <= len
          && self_id.isHashMatch(&pkt->payload[i + offset], hash_size)
          && allowPacketForward(pkt) && !_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        // append SNR (Not hash!)
        pkt->path[pkt->path_len++] = (int8_t) (pkt->getSNR()*4);

        uint8_t pri = getTraceDirectPriority(pkt);
        uint32_t d = getDirectRetransmitDelay(pkt);
        maybeScheduleDirectRetry(pkt, pri);
        return ACTION_RETRANSMIT_DELAYED(pri, d);
      }
    }
    return ACTION_RELEASE;
  }

  if (pkt->isRouteDirect() && pkt->getPayloadType() == PAYLOAD_TYPE_CONTROL
      && pkt->payload_len >= 1 && (pkt->payload[0] & 0x80) != 0) {
    if (pkt->getPathHashCount() == 0) {
      onControlDataRecv(pkt);
    }
    // just zero-hop control packets allowed (for this subset of payloads)
    return ACTION_RELEASE;
  }

  if (pkt->isRouteDirect() && pkt->getPathHashCount() > 0) {
    // check for 'early received' ACK
    if (pkt->getPayloadType() == PAYLOAD_TYPE_ACK) {
      if (pkt->payload_len >= sizeof(uint32_t)) {
        uint32_t ack_crc;
        memcpy(&ack_crc, pkt->payload, sizeof(ack_crc));
        onAckRecv(pkt, ack_crc);
      } else {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete early ACK packet", getLogDateTime());
        return ACTION_RELEASE;
      }
    }

    if (canDecodeDirectPayloadForSelf(pkt)) {
      // Some path sources include the final node hash, and some packets are
      // heard before all planned hops are consumed. Only stop forwarding once
      // this node proves it can decrypt the payload.
      removePathPrefix(pkt, pkt->getPathHashCount());
    } else if (self_id.isHashMatch(pkt->path, pkt->getPathHashSize()) || maybeShortCircuitDirect(pkt)) {
      if (allowPacketForward(pkt)) {
        if (pkt->getPayloadType() == PAYLOAD_TYPE_MULTIPART) {
          return forwardMultipartDirect(pkt);
        } else if (pkt->getPayloadType() == PAYLOAD_TYPE_ACK) {
          if (!_tables->wasSeen(pkt)) {  // don't retransmit!
            _tables->markSeen(pkt);
            removePathPrefix(pkt, 1);
            routeDirectRecvAcks(pkt, 0);
          }
          return ACTION_RELEASE;
        }

        if (!_tables->wasSeen(pkt)) {
          _tables->markSeen(pkt);
          bool final_hop_retry = pkt->getPathHashCount() == 1
              && pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG
              && hasValidEncryptedPayloadLength(pkt->payload_len, 2);
          removePathPrefix(pkt, 1);

          uint32_t d = getDirectRetransmitDelay(pkt);
          maybeScheduleDirectRetry(pkt, 0, final_hop_retry);
          return ACTION_RETRANSMIT_DELAYED(0, d);  // Routed traffic is HIGHEST priority
        }
      }
    }
    if (pkt->getPathHashCount() > 0) {
      return ACTION_RELEASE;   // this node is NOT the next hop (OR this packet has already been forwarded), so discard.
    }
  }

  if (pkt->isRouteFlood() && filterRecvFloodPacket(pkt)) return ACTION_RELEASE;

  DispatcherAction action = ACTION_RELEASE;

  switch (pkt->getPayloadType()) {
    case PAYLOAD_TYPE_ACK: {
      if (pkt->payload_len < sizeof(uint32_t)) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete ACK packet", getLogDateTime());
      } else if (!_tables->wasSeen(pkt)) {
        uint32_t ack_crc;
        memcpy(&ack_crc, pkt->payload, sizeof(ack_crc));
        _tables->markSeen(pkt);
        onAckRecv(pkt, ack_crc);
        action = routeRecvPacket(pkt);
      }
      break;
    }
    case PAYLOAD_TYPE_PATH:
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG: {
      if (!hasValidEncryptedPayloadLength(pkt->payload_len, 2)) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete data packet", getLogDateTime());
        break;
      }

      int i = 0;
      uint8_t dest_hash = pkt->payload[i++];
      uint8_t src_hash = pkt->payload[i++];

      uint8_t* macAndData = &pkt->payload[i];   // MAC + encrypted data 
      if (!_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        // NOTE: this is a 'first packet wins' impl. When receiving from multiple paths, the first to arrive wins.
        //       For flood mode, the path may not be the 'best' in terms of hops.
        // FUTURE: could send back multiple paths, using createPathReturn(), and let sender choose which to use(?)

        if (self_id.isHashMatch(&dest_hash)) {
          // scan contacts DB, for all matching hashes of 'src_hash' (max 4 matches supported ATM)
          int num = searchPeersByHash(&src_hash);
          // for each matching contact, try to decrypt data
          bool found = false;
          for (int j = 0; j < num; j++) {
            uint8_t secret[PUB_KEY_SIZE];
            getPeerSharedSecret(secret, j);

            // decrypt, checking MAC is valid
            uint8_t data[MAX_PACKET_PAYLOAD];
            int len = Utils::MACThenDecrypt(secret, data, macAndData, pkt->payload_len - i);
            if (len > 0) {  // success!
              if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH) {
                int k = 0;
                uint8_t path_len = data[k++];
                if (!Packet::isValidPathLen(path_len)) {
                  MESH_DEBUG_PRINTLN("%s PAYLOAD_TYPE_PATH, bad path_len: %u", getLogDateTime(), (uint32_t)path_len);
                  break;   // reject bad encoding
                }
                uint8_t hash_size = (path_len >> 6) + 1;
                uint8_t hash_count = path_len & 63;
                uint16_t path_bytes = (uint16_t)hash_size * hash_count;
                if ((uint16_t)k + path_bytes + 1 > (uint16_t)len) {
                  MESH_DEBUG_PRINTLN("%s PAYLOAD_TYPE_PATH, incomplete path data", getLogDateTime());
                  break;
                }
                uint8_t* path = &data[k]; k += path_bytes;
                uint8_t extra_type = data[k++] & 0x0F;   // upper 4 bits reserved for future use
                uint8_t* extra = &data[k];
                uint8_t extra_len = len - k;   // remainder of packet (may be padded with zeroes!)
                if (onPeerPathRecv(pkt, j, secret, path, path_len, extra_type, extra, extra_len)) {
                  if (pkt->isRouteFlood()) {
                    // send a reciprocal return path to sender, but send DIRECTLY!
                    mesh::Packet* rpath = createPathReturn(&src_hash, secret, pkt->path, pkt->path_len, 0, NULL, 0);
                    if (rpath) sendDirect(rpath, path, path_len, 500);
                  }
                }
              } else {
                onPeerDataRecv(pkt, pkt->getPayloadType(), j, secret, data, len);
              }
              found = true;
              break;
            }
          }
          if (found) {
            pkt->markDoNotRetransmit();  // packet was for this node, so don't retransmit
          } else {
            MESH_DEBUG_PRINTLN("%s recv matches no peers, src_hash=%02X", getLogDateTime(), (uint32_t)src_hash);
          }
        }
        action = routeRecvPacket(pkt);
      }
      break;
    }
    case PAYLOAD_TYPE_ANON_REQ: {
      if (!hasValidEncryptedPayloadLength(pkt->payload_len, 1 + PUB_KEY_SIZE)) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete anonymous request", getLogDateTime());
        break;
      }

      int i = 0;
      uint8_t dest_hash = pkt->payload[i++];
      uint8_t* sender_pub_key = &pkt->payload[i]; i += PUB_KEY_SIZE;

      uint8_t* macAndData = &pkt->payload[i];   // MAC + encrypted data 
      if (!_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        if (self_id.isHashMatch(&dest_hash)) {
          Identity sender(sender_pub_key);

          uint8_t secret[PUB_KEY_SIZE];
          self_id.calcSharedSecret(secret, sender);

          // decrypt, checking MAC is valid
          uint8_t data[MAX_PACKET_PAYLOAD];
          int len = Utils::MACThenDecrypt(secret, data, macAndData, pkt->payload_len - i);
          if (len > 0) {  // success!
            onAnonDataRecv(pkt, secret, sender, data, len);
            pkt->markDoNotRetransmit();
          }
        }
        action = routeRecvPacket(pkt);
      }
      break;
    }
    case PAYLOAD_TYPE_GRP_DATA: 
    case PAYLOAD_TYPE_GRP_TXT: {
      if (!hasValidEncryptedPayloadLength(pkt->payload_len, 1)) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete group packet", getLogDateTime());
        break;
      }

      int i = 0;
      uint8_t channel_hash = pkt->payload[i++];

      uint8_t* macAndData = &pkt->payload[i];   // MAC + encrypted data 
      if (!_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        onGroupPacketRecv(pkt);
        // scan channels DB, for all matching hashes of 'channel_hash' (max 4 matches supported ATM)
        GroupChannel channels[4];
        int num = searchChannelsByHash(&channel_hash, channels, 4);
        // for each matching channel, try to decrypt data
        for (int j = 0; j < num; j++) {
          // decrypt, checking MAC is valid
          uint8_t data[MAX_PACKET_PAYLOAD];
          int len = Utils::MACThenDecrypt(channels[j].secret, data, macAndData, pkt->payload_len - i);
          if (len > 0) {  // success!
            onGroupDataRecv(pkt, pkt->getPayloadType(), channels[j], data, len);
            break;
          }
        }
        action = routeRecvPacket(pkt);
      }
      break;
    }
    case PAYLOAD_TYPE_ADVERT: {
      const size_t min_advert_len = PUB_KEY_SIZE + sizeof(uint32_t) + SIGNATURE_SIZE;
      if (pkt->payload_len < min_advert_len) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): incomplete advertisement packet", getLogDateTime());
        break;
      }

      int i = 0;
      Identity id;
      memcpy(id.pub_key, &pkt->payload[i], PUB_KEY_SIZE); i += PUB_KEY_SIZE;

      uint32_t timestamp;
      memcpy(&timestamp, &pkt->payload[i], 4); i += 4;
      const uint8_t* signature = &pkt->payload[i]; i += SIGNATURE_SIZE;

      if (self_id.matches(id.pub_key)) {
        MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): receiving SELF advert packet", getLogDateTime());
      } else if (!_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        uint8_t* app_data = &pkt->payload[i];
        int app_data_len = pkt->payload_len - i;
        if (app_data_len > MAX_ADVERT_DATA_SIZE) { app_data_len = MAX_ADVERT_DATA_SIZE; }

        // check that signature is valid
        bool is_ok;
        {
          uint8_t message[PUB_KEY_SIZE + 4 + MAX_ADVERT_DATA_SIZE];
          int msg_len = 0;
          memcpy(&message[msg_len], id.pub_key, PUB_KEY_SIZE); msg_len += PUB_KEY_SIZE;
          memcpy(&message[msg_len], &timestamp, 4); msg_len += 4;
          memcpy(&message[msg_len], app_data, app_data_len); msg_len += app_data_len;

          is_ok = id.verify(signature, message, msg_len);
        }
        if (is_ok) {
          MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): valid advertisement received!", getLogDateTime());
          onAdvertRecv(pkt, id, timestamp, app_data, app_data_len);
          action = routeRecvPacket(pkt);
        } else {
          MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): received advertisement with forged signature! (app_data_len=%d)", getLogDateTime(), app_data_len);
        }
      }
      break;
    }
    case PAYLOAD_TYPE_RAW_CUSTOM: {
      if (pkt->isRouteDirect() && !_tables->wasSeen(pkt)) {
        _tables->markSeen(pkt);
        onRawDataRecv(pkt);
        //action = routeRecvPacket(pkt);    don't flood route these (yet)
      }
      break;
    }
    case PAYLOAD_TYPE_MULTIPART:
      if (pkt->payload_len > 2) {
        uint8_t remaining = pkt->payload[0] >> 4;  // num of packets in this multipart sequence still to be sent
        uint8_t type = pkt->payload[0] & 0x0F;

        if (type == PAYLOAD_TYPE_ACK && pkt->payload_len >= 5) {    // a multipart ACK
          if (!_tables->wasSeen(pkt)) {
            _tables->markSeen(pkt);
            Packet tmp;
            tmp.header = pkt->header;
            tmp.path_len = Packet::copyPath(tmp.path, pkt->path, pkt->path_len);
            tmp.payload_len = pkt->payload_len - 1;
            memcpy(tmp.payload, &pkt->payload[1], tmp.payload_len);
            uint32_t ack_crc;
            memcpy(&ack_crc, tmp.payload, 4);

            onAckRecv(&tmp, ack_crc);
            //action = routeRecvPacket(&tmp);  // NOTE: currently not needed, as multipart ACKs not sent Flood
          }
        } else {
          // FUTURE: other multipart types??
        }
      }
      break;

#if defined(ENABLE_OTA)
    case PAYLOAD_TYPE_OTA: {
      // OTA is invisible outside an actually-running temporary-radio window. In particular, do not add it
      // to the seen table: a copy heard on the normal channel must not suppress one received after temp radio starts.
      if (!isTempRadioActive()) break;
      uint8_t n = pkt->getPathHashCount();   // hops travelled to reach us (flood path-hash count)
      // Accept-gate (duty-cycle horizon): ignore OTA from further than our hop limit - neither process nor
      // relay it. 0 = only directly-received OTA. Runtime-tunable via `ota config hops`.
      if (n > getOtaHopLimit()) break;
      // ALWAYS process every accepted copy: OTA handlers are idempotent, and "eventually reliable" retries
      // deliberately re-send IDENTICAL requests - if we gated processing on hasSeen(), the dedup would
      // suppress those retries and the transfer could never recover from a lost reply. hasSeen() is used
      // ONLY to avoid re-flooding the same packet more than once.
      bool seen = _tables->wasSeen(pkt);
      if (!seen) _tables->markSeen(pkt);
      ota::ota_ctx().manager.set_clock(_ms->getMillis());                 // discovery jitter/ages
      ota::ota_ctx().manager.on_message(pkt->payload, pkt->payload_len);  // central OTA receive (beacon/query/
                                                                         // have/manifest/data/proof; all roles)
      ota::ota_ctx().track_session(ota::ota_ctx().manager.fetchState(), _ms->getMillis());
      onOtaRecv(pkt);                                                     // optional per-example hook
      // Re-flood at the LOWEST priority and only while still under the hop limit, so OTA never competes with
      // mesh traffic. The free-pool guard keeps heavy OTA from monopolising the shared packet pool - dropping
      // a relay is safe (OTA is best-effort; the source retries).
      if (!seen && pkt->isRouteFlood() && !pkt->isMarkedDoNotRetransmit()
          && n < getOtaHopLimit()
          && (n + 1) * pkt->getPathHashSize() <= MAX_PATH_SIZE
          && _mgr->getFreeCount() > OTA_FWD_MIN_FREE
          && allowPacketForward(pkt)) {
        self_id.copyHashTo(&pkt->path[n * pkt->getPathHashSize()], pkt->getPathHashSize());
        pkt->setPathHashCount(n + 1);
        action = ACTION_RETRANSMIT_DELAYED(OTA_TX_PRIORITY, getRetransmitDelay(pkt));
      }
      break;
    }
#endif
    default:
      MESH_DEBUG_PRINTLN("%s Mesh::onRecvPacket(): unknown payload type, header: %d", getLogDateTime(), (int) pkt->header);
      // Don't flood route unknown packet types!   action = routeRecvPacket(pkt);
      break;
  }
  return action;
}

void Mesh::removePathPrefix(Packet* pkt, uint8_t prefix_count) {
  uint8_t hash_count = pkt->getPathHashCount();
  if (prefix_count == 0 || hash_count == 0) return;
  if (prefix_count > hash_count) prefix_count = hash_count;

  pkt->setPathHashCount(hash_count - prefix_count);
  uint8_t sz = pkt->getPathHashSize();
  uint8_t prefix_bytes = prefix_count * sz;
  for (int k = 0; k < pkt->getPathHashCount()*sz; k += sz) {
    memmove(&pkt->path[k], &pkt->path[k + prefix_bytes], sz);
  }
}

DispatcherAction Mesh::routeRecvPacket(Packet* packet) {
  if (shouldSuppressEchoedAdvertForward(packet)) {
    return ACTION_RELEASE;
  }

  uint8_t n = packet->getPathHashCount();
  if (packet->isRouteFlood() && !packet->isMarkedDoNotRetransmit()
    && (n + 1)*packet->getPathHashSize() <= MAX_PATH_SIZE && allowPacketForward(packet)) {
    // append this node's hash to 'path'
    self_id.copyHashTo(&packet->path[n * packet->getPathHashSize()], packet->getPathHashSize());
    packet->setPathHashCount(n + 1);

    uint32_t d = getRetransmitDelay(packet);
    uint8_t priority = packet->getPathHashCount();
    maybeScheduleFloodRetry(packet, priority);
    // as this propagates outwards, give it lower and lower priority
    return ACTION_RETRANSMIT_DELAYED(priority, d);   // give priority to closer sources, than ones further away
  }
  return ACTION_RELEASE;
}

DispatcherAction Mesh::forwardMultipartDirect(Packet* pkt) {
  if (pkt == NULL || pkt->payload_len < 1) {
    return ACTION_RELEASE;
  }

  uint8_t remaining = pkt->payload[0] >> 4;  // num of packets in this multipart sequence still to be sent
  uint8_t type = pkt->payload[0] & 0x0F;

  if (type == PAYLOAD_TYPE_ACK && pkt->payload_len >= 5) {    // a multipart ACK
    if (!_tables->wasSeen(pkt)) {   // don't retransmit this multipart transmission!
      _tables->markSeen(pkt);
      Packet tmp;
      tmp.header = pkt->header;
      tmp.path_len = Packet::copyPath(tmp.path, pkt->path, pkt->path_len);
      tmp.payload_len = pkt->payload_len - 1;
      memcpy(tmp.payload, &pkt->payload[1], tmp.payload_len);
      removePathPrefix(&tmp, 1);
      routeDirectRecvAcks(&tmp, ((uint32_t)remaining + 1) * 300);  // expect multipart ACKs 300ms apart (x2)
    }
  }
  return ACTION_RELEASE;
}

void Mesh::routeDirectRecvAcks(Packet* packet, uint32_t delay_millis) {
  if (!packet->isMarkedDoNotRetransmit()) {
    uint8_t extra = getExtraAckTransmitCount();
    while (extra > 0) {
      delay_millis += getDirectRetransmitDelay(packet) + 300;
      auto a1 = createMultiAck(packet->payload, packet->payload_len, extra);
      if (a1) {
        a1->path_len = Packet::copyPath(a1->path, packet->path, packet->path_len);
        a1->header &= ~PH_ROUTE_MASK;
        a1->header |= ROUTE_TYPE_DIRECT;
        maybeScheduleDirectRetry(a1, 0);
        sendPacket(a1, 0, delay_millis);
      }
      extra--;
    }

    auto a2 = createAck(packet->payload, packet->payload_len);
    if (a2) {
      a2->path_len = Packet::copyPath(a2->path, packet->path, packet->path_len);
      a2->header &= ~PH_ROUTE_MASK;
      a2->header |= ROUTE_TYPE_DIRECT;
      maybeScheduleDirectRetry(a2, 0);
      sendPacket(a2, 0, delay_millis);
    }
  }
}

void Mesh::clearDirectRetrySlot(int idx) {
  const bool rebuild_timeout = _direct_retries[idx].active
      && _direct_retries[idx].waiting_final_echo
      && _direct_retries[idx].retry_at == _next_direct_retry_timeout;
  if (_direct_retries[idx].active && _direct_retries[idx].waiting_final_echo
      && _waiting_direct_retry_count > 0) {
    _waiting_direct_retry_count--;
  }
  if (_direct_retries[idx].active && _active_direct_retry_count > 0) {
    _active_direct_retry_count--;
  }
  _direct_retries[idx].packet = NULL;
  _direct_retries[idx].trigger_packet = NULL;
  _direct_retries[idx].retry_started_at = 0;
  _direct_retries[idx].echo_wait_started_at = 0;
  _direct_retries[idx].retry_at = 0;
  _direct_retries[idx].retry_delay = 0;
  _direct_retries[idx].retry_attempts_sent = 0;
  memset(_direct_retries[idx].retry_key, 0, sizeof(_direct_retries[idx].retry_key));
  memset(_direct_retries[idx].trace_replacement_key, 0, sizeof(_direct_retries[idx].trace_replacement_key));
  memset(_direct_retries[idx].next_hop_hash, 0, sizeof(_direct_retries[idx].next_hop_hash));
  _direct_retries[idx].next_hop_hash_len = 0;
  _direct_retries[idx].payload_type = 0;
  _direct_retries[idx].priority = 0;
  _direct_retries[idx].progress_marker = 0;
  _direct_retries[idx].expect_path_growth = false;
  _direct_retries[idx].final_hop_retry = false;
  _direct_retries[idx].waiting_final_echo = false;
  _direct_retries[idx].queued = false;
  _direct_retries[idx].active = false;
  if (rebuild_timeout) rebuildNextDirectRetryTimeout();
}

void Mesh::retireDirectRetrySlot(int idx) {
  if (idx < 0 || idx >= MAX_DIRECT_RETRY_SLOTS || !_direct_retries[idx].active) {
    return;
  }

  Packet* retry = _direct_retries[idx].queued ? _direct_retries[idx].packet : NULL;
  if (retry != NULL && retry != getOutboundInFlight()) {
    for (int j = 0; j < _mgr->getOutboundTotal(); j++) {
      if (_mgr->getOutboundByIdx(j) != retry) continue;
      Packet* pending = _mgr->removeOutboundByIdx(j);
      if (pending != NULL) {
        _direct_retries[idx].packet = NULL;
        releasePacket(pending);
      }
      break;
    }
  }
  clearDirectRetrySlot(idx);
}

void Mesh::rebuildNextDirectRetryTimeout() {
  bool found = false;
  uint32_t shortest_delay = 0;
  const uint32_t now = _ms->getMillis();
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active || !_direct_retries[i].waiting_final_echo) continue;
    int32_t signed_delay = (int32_t)(_direct_retries[i].retry_at - now);
    uint32_t delay = signed_delay > 0 ? (uint32_t)signed_delay : 0;
    if (!found || delay < shortest_delay) {
      shortest_delay = delay;
      _next_direct_retry_timeout = _direct_retries[i].retry_at;
      found = true;
    }
  }
  if (!found) _next_direct_retry_timeout = 0;
}

bool Mesh::usePassiveChannelCheck(const Packet* packet) const {
  if (_active_direct_retry_count != 0) {
    for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
      if (_direct_retries[i].active && _direct_retries[i].queued
          && _direct_retries[i].packet == packet) {
        return true;
      }
    }
  }

  // Flood retries use the same receive-side cancellation as direct retries:
  // an overheard downstream forwarding echo removes the queued retry. Avoid
  // CAD here too, since restarting RX can hide that echo. The initial flood
  // has trigger_packet set but queued=false, so ordinary flood forwarding
  // continues to use the normal CAD check.
  if (_active_flood_retry_count != 0) {
    for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
      if (_flood_retries[i].active && _flood_retries[i].queued
          && _flood_retries[i].packet == packet) {
        return true;
      }
    }
  }

  return false;
}

bool Mesh::getNextRetryWakeDelay(uint32_t& delay_millis) const {
  const uint32_t now = _ms->getMillis();
  bool found = false;
  uint32_t shortest_delay = 0;

  if (_waiting_direct_retry_count != 0) {
    int32_t signed_delay = (int32_t)(_next_direct_retry_timeout - now);
    shortest_delay = signed_delay > 0 ? (uint32_t)signed_delay : 0;
    found = true;
  }
  if (_waiting_flood_retry_count != 0) {
    int32_t signed_delay = (int32_t)(_next_flood_retry_timeout - now);
    uint32_t flood_delay = signed_delay > 0 ? (uint32_t)signed_delay : 0;
    if (!found || flood_delay < shortest_delay) shortest_delay = flood_delay;
    found = true;
  }

  if (found) delay_millis = shortest_delay;
  return found;
}

void Mesh::calculateDirectRetryKey(const Packet* packet, uint8_t* dest_key) const {
  uint8_t type = packet->getPayloadType();
  Utils::sha256(dest_key, MAX_HASH_SIZE, &type, 1, packet->payload, packet->payload_len);
}

bool Mesh::calculateTraceReplacementKey(const Packet* packet, uint8_t* dest_key) const {
  if (packet == NULL || dest_key == NULL || !packet->isRouteDirect()
      || packet->getPayloadType() != PAYLOAD_TYPE_TRACE || packet->payload_len < 9) {
    return false;
  }

  uint8_t prefix[3] = {
    PAYLOAD_TYPE_TRACE,
    (uint8_t)(packet->path_len & 0xFF),
    (uint8_t)(packet->path_len >> 8)
  };
  // Ignore tag/auth (payload bytes 0..7), which change for a new request.
  // Keep flags, route, and current progress so an older trace that has already
  // advanced is not mistaken for the stale retry being replaced.
  Utils::sha256(dest_key, MAX_HASH_SIZE, prefix, sizeof(prefix),
                &packet->payload[8], packet->payload_len - 8);
  return true;
}

void Mesh::replaceQueuedTraceRetries(const Packet* packet) {
  uint8_t replacement_key[MAX_HASH_SIZE];
  if (!calculateTraceReplacementKey(packet, replacement_key)) return;

  int replacement_slot = -1;
  bool found_prior = false;
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active || _direct_retries[i].payload_type != PAYLOAD_TYPE_TRACE
        || memcmp(replacement_key, _direct_retries[i].trace_replacement_key, MAX_HASH_SIZE) != 0) {
      continue;
    }
    if (_direct_retries[i].trigger_packet == packet) {
      replacement_slot = i;
    } else {
      found_prior = true;
    }
  }

  if (!found_prior) return;

  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (i == replacement_slot || !_direct_retries[i].active
        || _direct_retries[i].payload_type != PAYLOAD_TYPE_TRACE
        || memcmp(replacement_key, _direct_retries[i].trace_replacement_key, MAX_HASH_SIZE) != 0) {
      continue;
    }
    retireDirectRetrySlot(i);
  }

  // An exact duplicate retry key, or a full retry table, can prevent the new
  // packet from reserving its slot before it is queued. The prior slots are now
  // gone, so register the successfully queued packet as the retry owner.
  if (replacement_slot < 0) {
    maybeScheduleDirectRetry(packet, getTraceDirectPriority(packet));
  }
}

bool Mesh::cancelDirectRetryOnEcho(const Packet* packet) {
  if (_active_direct_retry_count == 0) return false;

  uint8_t recv_key[MAX_HASH_SIZE];
  calculateDirectRetryKey(packet, recv_key);

  bool cleared = false;
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active || memcmp(recv_key, _direct_retries[i].retry_key, MAX_HASH_SIZE) != 0) {
      continue;
    }

    bool is_echo = _direct_retries[i].expect_path_growth
      ? packet->path_len > _direct_retries[i].progress_marker
      : packet->getPathHashCount() < _direct_retries[i].progress_marker;
    if (!is_echo) {
      continue;
    }

    int8_t echo_snr_x4 = packet->_snr;
    onDirectRetrySucceeded(_direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len, echo_snr_x4);
    if (_direct_retries[i].queued || _direct_retries[i].waiting_final_echo) {
      if (_direct_retries[i].packet != NULL) {
        // Success quality comes from the received downstream echo, not the original upstream RX.
        _direct_retries[i].packet->_snr = echo_snr_x4;
      }
      uint32_t echo_millis = _direct_retries[i].echo_wait_started_at == 0
        ? 0
        : (uint32_t)(_ms->getMillis() - _direct_retries[i].echo_wait_started_at);
      uint8_t retry_attempt = _direct_retries[i].waiting_final_echo
        ? _direct_retries[i].retry_attempts_sent
        : _direct_retries[i].retry_attempts_sent + 1;
      onDirectRetryEvent("good", _direct_retries[i].packet, echo_millis, retry_attempt,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len,
                         _direct_retries[i].payload_type);
      if (_direct_retries[i].queued) {
        for (int j = 0; j < _mgr->getOutboundTotal(); j++) {
          if (_mgr->getOutboundByIdx(j) == _direct_retries[i].packet) {
            Packet* pending = _mgr->removeOutboundByIdx(j);
            if (pending) {
              releasePacket(pending);
            }
            break;
          }
        }
      }
      clearDirectRetrySlot(i);
    } else {
      if (_direct_retries[i].trigger_packet != NULL) {
        _direct_retries[i].trigger_packet->_snr = echo_snr_x4;
      }
      uint32_t echo_millis = _direct_retries[i].echo_wait_started_at == 0
        ? 0
        : (uint32_t)(_ms->getMillis() - _direct_retries[i].echo_wait_started_at);
      onDirectRetryEvent("good", _direct_retries[i].trigger_packet, echo_millis, _direct_retries[i].retry_attempts_sent + 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      clearDirectRetrySlot(i);
    }
    cleared = true;
  }

  return cleared;
}

void Mesh::armDirectRetryOnSendComplete(const Packet* packet) {
  if (_active_direct_retry_count == 0) return;

  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active) {
      continue;
    }

    if (_direct_retries[i].queued) {
      if (_direct_retries[i].packet == packet) {
        // The retry packet itself just finished transmitting; Dispatcher will release it after this hook.
        uint32_t elapsed_millis = _direct_retries[i].retry_started_at == 0
          ? 0
          : (uint32_t)(_ms->getMillis() - _direct_retries[i].retry_started_at);
        onDirectRetryEvent("resent", packet, elapsed_millis, _direct_retries[i].retry_attempts_sent + 1,
                           _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
        _direct_retries[i].echo_wait_started_at = _ms->getMillis();
        _direct_retries[i].retry_attempts_sent++;
        if (_direct_retries[i].final_hop_retry) {
          // The destination does not forward the packet, so no downstream echo
          // can confirm this hop. Send exactly one duplicate and finish without
          // treating the lack of an echo as a link failure.
          clearDirectRetrySlot(i);
          continue;
        }
        uint8_t max_attempts = getDirectRetryMaxAttempts(packet);
        if (max_attempts < 1) {
          max_attempts = 1;
        } else if (max_attempts > DIRECT_RETRY_MAX_ATTEMPTS_HARD_MAX) {
          max_attempts = DIRECT_RETRY_MAX_ATTEMPTS_HARD_MAX;
        }
        if (_direct_retries[i].retry_attempts_sent >= max_attempts) {
          // Dispatcher releases the retry packet after this hook. Keep only retry metadata
          // for the final echo window so pool exhaustion cannot force a premature failure.
          _direct_retries[i].packet = NULL;
          _direct_retries[i].retry_at = futureMillis(_direct_retries[i].retry_delay);
          _direct_retries[i].waiting_final_echo = true;
          if (_waiting_direct_retry_count == 0
              || (int32_t)(_direct_retries[i].retry_at - _next_direct_retry_timeout) < 0) {
            _next_direct_retry_timeout = _direct_retries[i].retry_at;
          }
          _waiting_direct_retry_count++;
          _direct_retries[i].queued = false;
          continue;
        }

        Packet* retry = obtainNewPacket();
        if (retry == NULL) {
          onDirectRetryEvent("dropped_no_packet", packet, elapsed_millis, _direct_retries[i].retry_attempts_sent + 1,
                             _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
          onDirectRetryEvent("failure", packet, elapsed_millis, _direct_retries[i].retry_attempts_sent + 1,
                             _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
          clearDirectRetrySlot(i);
          continue;
        }

        *retry = *packet;
        retry->tx_cr = 0;
        uint8_t retry_attempt = _direct_retries[i].retry_attempts_sent + 1;
        configureDirectRetryPacket(retry, packet, retry_attempt);
        uint32_t retry_delay = getDirectRetryAttemptDelay(packet, _direct_retries[i].retry_attempts_sent);
        if (queueOutboundPacket(retry, _direct_retries[i].priority, retry_delay)) {
          _direct_retries[i].packet = retry;
          _direct_retries[i].retry_delay = retry_delay;
          _direct_retries[i].retry_at = futureMillis(retry_delay);
          _direct_retries[i].waiting_final_echo = false;
          onDirectRetryEvent("queued", retry, retry_delay, retry_attempt,
                             _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
        } else {
          onDirectRetryEvent("dropped_queue_full", retry, retry_delay, retry_attempt,
                             _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
          onDirectRetryEvent("failure", retry, elapsed_millis, retry_attempt,
                             _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
          releasePacket(retry);
          clearDirectRetrySlot(i);
        }
      }
      continue;
    }

    if (_direct_retries[i].trigger_packet != packet) {
      continue;
    }

    // Allocate the retry packet only after TX-complete so busy repeaters do not reserve pool slots early.
    Packet* retry = obtainNewPacket();
    if (retry == NULL) {
      onDirectRetryEvent("dropped_no_packet", packet, _direct_retries[i].retry_delay, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      onDirectRetryEvent("failure", packet, 0, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      clearDirectRetrySlot(i);
      continue;
    }

    *retry = *packet;
    retry->tx_cr = 0;
    configureDirectRetryPacket(retry, packet, 1);

    // Start the echo wait only after the initial direct transmission actually completed.
    if (queueOutboundPacket(retry, _direct_retries[i].priority, _direct_retries[i].retry_delay)) {
      unsigned long now = _ms->getMillis();
      _direct_retries[i].packet = retry;
      _direct_retries[i].trigger_packet = NULL;
      _direct_retries[i].queued = true;
      _direct_retries[i].waiting_final_echo = false;
      _direct_retries[i].retry_at = futureMillis(_direct_retries[i].retry_delay);
      _direct_retries[i].retry_started_at = now;
      _direct_retries[i].echo_wait_started_at = now;
      onDirectRetryEvent("queued", retry, _direct_retries[i].retry_delay, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
    } else {
      onDirectRetryEvent("dropped_queue_full", retry, _direct_retries[i].retry_delay, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      onDirectRetryEvent("failure", retry, 0, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      releasePacket(retry);
      clearDirectRetrySlot(i);
    }
  }
}

void Mesh::clearPendingDirectRetryOnSendFail(const Packet* packet) {
  if (_active_direct_retry_count == 0) return;

  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active) {
      continue;
    }

    if (_direct_retries[i].queued) {
      if (_direct_retries[i].packet == packet) {
        // The queued retry itself failed; Dispatcher will release it after this hook.
        onDirectRetryEvent("dropped_send_fail", packet, 0, _direct_retries[i].retry_attempts_sent + 1,
                           _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
        onDirectRetryEvent("failure", packet, 0, _direct_retries[i].retry_attempts_sent + 1,
                           _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
        clearDirectRetrySlot(i);
      }
      continue;
    }

    if (_direct_retries[i].trigger_packet == packet) {
      onDirectRetryEvent("dropped_send_fail", packet, 0, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      onDirectRetryEvent("failure", packet, 0, 1,
                         _direct_retries[i].next_hop_hash, _direct_retries[i].next_hop_hash_len);
      clearDirectRetrySlot(i);
    }
  }
}

bool Mesh::getDirectRetryTarget(const Packet* packet, const uint8_t*& next_hop_hash, uint8_t& next_hop_hash_len,
                                uint8_t& progress_marker, bool& expect_path_growth) const {
  switch (packet->getPayloadType()) {
    case PAYLOAD_TYPE_ACK:
    case PAYLOAD_TYPE_PATH:
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG:
    case PAYLOAD_TYPE_ANON_REQ:
      // Allow retries even when only one downstream hop remains so fixed direct paths
      // (e.g. remote admin/login over 2-hop chains) use the same retry policy.
      if (packet->getPathHashCount() == 0) {
        return false;
      }
      next_hop_hash = packet->path;
      next_hop_hash_len = packet->getPathHashSize();
      progress_marker = packet->getPathHashCount();
      expect_path_growth = false;
      return true;

    case PAYLOAD_TYPE_MULTIPART:
      if (packet->payload_len < 1 || (packet->payload[0] & 0x0F) != PAYLOAD_TYPE_ACK || packet->getPathHashCount() == 0) {
        return false;
      }
      next_hop_hash = packet->path;
      next_hop_hash_len = packet->getPathHashSize();
      progress_marker = packet->getPathHashCount();
      expect_path_growth = false;
      return true;

    case PAYLOAD_TYPE_TRACE: {
      if (packet->payload_len < 9) {
        return false;
      }

      uint8_t route_bytes = packet->payload_len - 9;
      uint8_t hash_size = decodeTraceHashSize(packet->payload[8], route_bytes);
      uint16_t offset = (uint16_t)packet->path_len * (uint16_t)hash_size;
      if (offset + hash_size > route_bytes) {
        return false;
      }

      next_hop_hash = &packet->payload[9 + offset];
      next_hop_hash_len = hash_size;
      progress_marker = packet->path_len;
      expect_path_growth = true;
      return true;
    }

    default:
      return false;
  }
}

bool Mesh::canDecodeDirectPayloadForSelf(const Packet* packet) {
  if (packet == NULL || !packet->isRouteDirect() || packet->getPathHashCount() == 0 || packet->payload_len < 1) {
    return false;
  }

  switch (packet->getPayloadType()) {
    case PAYLOAD_TYPE_PATH:
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG: {
      if (!hasValidEncryptedPayloadLength(packet->payload_len, 2)) {
        return false;
      }

      int i = 0;
      uint8_t dest_hash = packet->payload[i++];
      uint8_t src_hash = packet->payload[i++];
      if (!self_id.isHashMatch(&dest_hash)) {
        return false;
      }

      int num = searchPeersByHash(&src_hash);
      for (int j = 0; j < num; j++) {
        uint8_t secret[PUB_KEY_SIZE];
        getPeerSharedSecret(secret, j);

        uint8_t data[MAX_PACKET_PAYLOAD];
        if (Utils::MACThenDecrypt(secret, data, &packet->payload[i], packet->payload_len - i) > 0) {
          return true;
        }
      }
      return false;
    }

    case PAYLOAD_TYPE_ANON_REQ: {
      if (!hasValidEncryptedPayloadLength(packet->payload_len, 1 + PUB_KEY_SIZE)) {
        return false;
      }

      int i = 0;
      uint8_t dest_hash = packet->payload[i++];
      if (!self_id.isHashMatch(&dest_hash)) {
        return false;
      }

      Identity sender(&packet->payload[i]);
      i += PUB_KEY_SIZE;

      uint8_t secret[PUB_KEY_SIZE];
      self_id.calcSharedSecret(secret, sender);

      uint8_t data[MAX_PACKET_PAYLOAD];
      return Utils::MACThenDecrypt(secret, data, &packet->payload[i], packet->payload_len - i) > 0;
    }

    default:
      return false;
  }
}

void Mesh::maybeScheduleDirectRetry(const Packet* packet, uint8_t priority, bool final_hop_retry) {
  const uint8_t* next_hop_hash = NULL;
  uint8_t next_hop_hash_len = 0;
  uint8_t progress_marker = 0;
  bool expect_path_growth = false;
  if (final_hop_retry) {
    if (packet == NULL || !packet->isRouteDirect()
        || packet->getPayloadType() != PAYLOAD_TYPE_TXT_MSG
        || packet->getPathHashCount() != 0 || packet->payload_len < 2 + CIPHER_MAC_SIZE
        || !allowDirectRetry(packet, NULL, 0)) {
      return;
    }
    // The encrypted payload exposes only the destination hash to a relay. Keep
    // it for diagnostics, but do not apply recent-repeater/SNR eligibility to
    // the destination itself.
    next_hop_hash = packet->payload;
    next_hop_hash_len = 1;
  } else if (!getDirectRetryTarget(packet, next_hop_hash, next_hop_hash_len,
                                   progress_marker, expect_path_growth)
             || !allowDirectRetry(packet, next_hop_hash, next_hop_hash_len)) {
    return;
  }

  uint8_t retry_key[MAX_HASH_SIZE];
  calculateDirectRetryKey(packet, retry_key);
  uint8_t trace_replacement_key[MAX_HASH_SIZE] = { 0 };
  bool has_trace_replacement_key = calculateTraceReplacementKey(packet, trace_replacement_key);
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (_direct_retries[i].active
        && memcmp(retry_key, _direct_retries[i].retry_key, MAX_HASH_SIZE) == 0) {
      return;  // the normal direct send still happens, but only one retry sequence owns this logical packet
    }
  }

  int slot_idx = -1;
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active) {
      slot_idx = i;
      break;
    }
  }
  if (slot_idx < 0) {
    if (has_trace_replacement_key) {
      for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
        if (_direct_retries[i].active && _direct_retries[i].payload_type == PAYLOAD_TYPE_TRACE
            && memcmp(trace_replacement_key, _direct_retries[i].trace_replacement_key,
                      MAX_HASH_SIZE) == 0) {
          // The post-queue hook will retire this older matching TRACE and use
          // the slot for the successfully queued replacement.
          return;
        }
      }
    }
    onDirectRetryEvent("dropped_no_slot", packet, 0, 0, next_hop_hash, next_hop_hash_len);
    onDirectRetryEvent("failure", packet, 0, 0, next_hop_hash, next_hop_hash_len);
    return;
  }

  // Only store retry metadata here; allocate the retry packet after the initial TX really completes.
  uint32_t retry_delay = getDirectRetryAttemptDelay(packet, 0);
  memcpy(_direct_retries[slot_idx].retry_key, retry_key, sizeof(retry_key));
  memcpy(_direct_retries[slot_idx].trace_replacement_key, trace_replacement_key,
         sizeof(trace_replacement_key));
  _direct_retries[slot_idx].packet = NULL;
  _direct_retries[slot_idx].trigger_packet = const_cast<Packet*>(packet);
  _direct_retries[slot_idx].retry_started_at = 0;
  _direct_retries[slot_idx].echo_wait_started_at = 0;
  _direct_retries[slot_idx].retry_at = 0;
  _direct_retries[slot_idx].retry_delay = retry_delay;
  _direct_retries[slot_idx].retry_attempts_sent = 0;
  memset(_direct_retries[slot_idx].next_hop_hash, 0, sizeof(_direct_retries[slot_idx].next_hop_hash));
  memcpy(_direct_retries[slot_idx].next_hop_hash, next_hop_hash, next_hop_hash_len);
  _direct_retries[slot_idx].next_hop_hash_len = next_hop_hash_len;
  _direct_retries[slot_idx].payload_type = packet->getPayloadType();
  _direct_retries[slot_idx].priority = priority;
  _direct_retries[slot_idx].progress_marker = progress_marker;
  _direct_retries[slot_idx].expect_path_growth = expect_path_growth;
  _direct_retries[slot_idx].final_hop_retry = final_hop_retry;
  _direct_retries[slot_idx].waiting_final_echo = false;
  _direct_retries[slot_idx].queued = false;
  _direct_retries[slot_idx].active = true;
  _active_direct_retry_count++;
}

void Mesh::clearFloodRetrySlot(int idx) {
  const bool rebuild_timeout = _flood_retries[idx].active
      && _flood_retries[idx].waiting_final_echo
      && _flood_retries[idx].retry_at == _next_flood_retry_timeout;
  if (_flood_retries[idx].active) {
    if (_active_flood_retry_count > 0) {
      _active_flood_retry_count--;
    }
    if (_flood_retries[idx].waiting_final_echo && _waiting_flood_retry_count > 0) {
      _waiting_flood_retry_count--;
    }
    onFloodRetrySlotReleased(_flood_retries[idx].retry_key);
  }
  if (_flood_retries[idx].waiting_final_echo && _flood_retries[idx].packet != NULL) {
    releasePacket(_flood_retries[idx].packet);
  }
  _flood_retries[idx].packet = NULL;
  _flood_retries[idx].trigger_packet = NULL;
  _flood_retries[idx].retry_started_at = 0;
  _flood_retries[idx].retry_at = 0;
  _flood_retries[idx].retry_delay = 0;
  _flood_retries[idx].retry_attempts_sent = 0;
  memset(_flood_retries[idx].retry_key, 0, sizeof(_flood_retries[idx].retry_key));
  _flood_retries[idx].priority = 0;
  _flood_retries[idx].progress_marker = 0;
  _flood_retries[idx].self_advert = false;
  _flood_retries[idx].waiting_final_echo = false;
  _flood_retries[idx].queued = false;
  _flood_retries[idx].active = false;
  if (rebuild_timeout) rebuildNextFloodRetryTimeout();
}

void Mesh::retireFloodRetrySlot(int idx) {
  if (idx < 0 || idx >= MAX_FLOOD_RETRY_SLOTS || !_flood_retries[idx].active) {
    return;
  }

  Packet* retry = _flood_retries[idx].queued ? _flood_retries[idx].packet : NULL;
  if (retry != NULL && retry != getOutboundInFlight()) {
    for (int j = 0; j < _mgr->getOutboundTotal(); j++) {
      if (_mgr->getOutboundByIdx(j) != retry) continue;
      Packet* pending = _mgr->removeOutboundByIdx(j);
      if (pending != NULL) {
        _flood_retries[idx].packet = NULL;
        releasePacket(pending);
      }
      break;
    }
  }
  clearFloodRetrySlot(idx);
}

void Mesh::replaceQueuedSelfAdvertRetries(const Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood() || !isSelfOriginAdvert(packet)) {
    return;
  }

  int replacement_slot = -1;
  bool found_prior = false;
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active
        || !_flood_retries[i].self_advert) {
      continue;
    }
    if (_flood_retries[i].trigger_packet == packet) {
      replacement_slot = i;
    } else {
      found_prior = true;
    }
  }

  if (!found_prior) return;

  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (i == replacement_slot || !_flood_retries[i].active
        || !_flood_retries[i].self_advert) {
      continue;
    }
    retireFloodRetrySlot(i);
  }

  // A full retry table or an identical retry key can prevent the new advert
  // from reserving a slot before it enters the outbound queue. Older advert
  // retries are gone now, so let the successfully queued advert take over.
  if (replacement_slot < 0) {
    maybeScheduleFloodRetry(packet, 3);
  }
}

void Mesh::rebuildNextFloodRetryTimeout() {
  bool found = false;
  uint32_t shortest_delay = 0;
  const uint32_t now = _ms->getMillis();
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active || !_flood_retries[i].waiting_final_echo) continue;
    int32_t signed_delay = (int32_t)(_flood_retries[i].retry_at - now);
    uint32_t delay = signed_delay > 0 ? (uint32_t)signed_delay : 0;
    if (!found || delay < shortest_delay) {
      shortest_delay = delay;
      _next_flood_retry_timeout = _flood_retries[i].retry_at;
      found = true;
    }
  }
  if (!found) _next_flood_retry_timeout = 0;
}

void Mesh::cancelAllDirectRetries() {
  if (_active_direct_retry_count == 0) return;

  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active) continue;
    retireDirectRetrySlot(i);
  }
}

void Mesh::cancelAllFloodRetries() {
  if (_active_flood_retry_count == 0) return;

  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active) continue;
    retireFloodRetrySlot(i);
  }
}

bool Mesh::cancelActiveRetries(const uint8_t retry_key[MAX_HASH_SIZE]) {
  if (retry_key == NULL || (_active_direct_retry_count == 0 && _active_flood_retry_count == 0)) {
    return false;
  }

  uint8_t key[MAX_HASH_SIZE];
  memcpy(key, retry_key, sizeof(key));  // tolerate callers passing storage owned by a retry slot
  bool cancelled = false;
  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (!_direct_retries[i].active
        || memcmp(key, _direct_retries[i].retry_key, MAX_HASH_SIZE) != 0) {
      continue;
    }

    retireDirectRetrySlot(i);
    cancelled = true;
  }

  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active
        || memcmp(key, _flood_retries[i].retry_key, MAX_HASH_SIZE) != 0) {
      continue;
    }

    retireFloodRetrySlot(i);
    cancelled = true;
  }

  return cancelled;
}

void Mesh::replaceActiveRetries(const Packet* replacement_packet,
                                const uint8_t retry_key[MAX_HASH_SIZE]) {
  cancelActiveRetries(retry_key);
  if (replacement_packet == NULL) return;

  if (replacement_packet->isRouteDirect()) {
    uint8_t priority;
    if (replacement_packet->getPayloadType() == PAYLOAD_TYPE_TRACE) {
      priority = getTraceDirectPriority(replacement_packet);
    } else {
      priority = replacement_packet->getPayloadType() == PAYLOAD_TYPE_PATH ? 1 : 0;
    }
    maybeScheduleDirectRetry(replacement_packet, priority);
  } else if (replacement_packet->isRouteFlood()) {
    uint8_t priority;
    if (replacement_packet->getPayloadType() == PAYLOAD_TYPE_PATH) {
      priority = 2;
    } else if (replacement_packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
      priority = 3;
    } else {
      priority = 1;
    }
    maybeScheduleFloodRetry(replacement_packet, priority);
  }
}

bool Mesh::hasActiveRetries(const uint8_t retry_key[MAX_HASH_SIZE]) const {
  if (retry_key == NULL || (_active_direct_retry_count == 0 && _active_flood_retry_count == 0)) {
    return false;
  }

  for (int i = 0; i < MAX_DIRECT_RETRY_SLOTS; i++) {
    if (_direct_retries[i].active
        && memcmp(retry_key, _direct_retries[i].retry_key, MAX_HASH_SIZE) == 0) {
      return true;
    }
  }
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (_flood_retries[i].active
        && memcmp(retry_key, _flood_retries[i].retry_key, MAX_HASH_SIZE) == 0) {
      return true;
    }
  }
  return false;
}

bool Mesh::isFloodRetryEchoTarget(const Packet* packet, uint8_t progress_marker) const {
  return packet->isRouteFlood() && packet->getPathHashCount() > progress_marker;
}

bool Mesh::getRecentAdvertTimestamp(const Packet* packet, uint32_t& timestamp) const {
  if (packet == NULL || packet->getPayloadType() != PAYLOAD_TYPE_ADVERT
      || packet->payload_len < PUB_KEY_SIZE + sizeof(timestamp) + SIGNATURE_SIZE) {
    return false;
  }
  memcpy(&timestamp, &packet->payload[PUB_KEY_SIZE], sizeof(timestamp));
  return isRecentAdvertTimestamp(timestamp);
}

bool Mesh::isRecentAdvertTimestamp(uint32_t timestamp) const {
  uint32_t now = _rtc->getCurrentTime();
  return now >= timestamp && now - timestamp < RECENT_ADVERT_MAX_AGE_SECONDS;
}

void Mesh::watchForwardedAdvertEcho(const Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood() || packet->getPathHashCount() == 0) {
    return;
  }

  uint32_t advert_timestamp;
  if (!getRecentAdvertTimestamp(packet, advert_timestamp)) {
    return;
  }

  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);
  uint32_t now_millis = _ms->getMillis();
  int slot_idx = -1;
  for (int i = 0; i < MAX_RECENT_ADVERT_ECHOS; i++) {
    RecentAdvertEchoEntry& entry = _recent_advert_echoes[i];
    if (entry.valid && memcmp(entry.packet_hash, packet_hash, MAX_HASH_SIZE) == 0) {
      if (entry.confirmed) {
        return;
      }
      slot_idx = i;
      break;
    }
    bool expired = entry.valid
        && ((entry.confirmed && !isRecentAdvertTimestamp(entry.advert_timestamp))
            || (!entry.confirmed
                && (uint32_t)(now_millis - entry.watch_started_at)
                    > FORWARDED_ADVERT_ECHO_WATCH_MS));
    if (slot_idx < 0 && (!entry.valid || expired)) {
      slot_idx = i;
    }
  }
  if (slot_idx < 0) {
    slot_idx = _next_recent_advert_echo;
  }
  _next_recent_advert_echo = (slot_idx + 1) % MAX_RECENT_ADVERT_ECHOS;

  RecentAdvertEchoEntry& entry = _recent_advert_echoes[slot_idx];
  memcpy(entry.packet_hash, packet_hash, sizeof(entry.packet_hash));
  entry.advert_timestamp = advert_timestamp;
  entry.watch_started_at = now_millis;
  entry.progress_marker = packet->getPathHashCount();
  entry.confirmed = false;
  entry.valid = true;
}

void Mesh::observeForwardedAdvertEcho(const Packet* packet) {
  if (packet == NULL || !packet->isRouteFlood()) {
    return;
  }

  uint32_t advert_timestamp;
  if (!getRecentAdvertTimestamp(packet, advert_timestamp)) {
    return;
  }

  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);
  uint32_t now_millis = _ms->getMillis();
  for (int i = 0; i < MAX_RECENT_ADVERT_ECHOS; i++) {
    RecentAdvertEchoEntry& entry = _recent_advert_echoes[i];
    if (!entry.valid || entry.confirmed
        || memcmp(entry.packet_hash, packet_hash, MAX_HASH_SIZE) != 0) {
      continue;
    }
    if ((uint32_t)(now_millis - entry.watch_started_at) > FORWARDED_ADVERT_ECHO_WATCH_MS) {
      entry.valid = false;
      continue;
    }
    // The exact advert payload is the identity. It may return through a
    // different branch; a longer path still proves a downstream copy exists.
    if (entry.advert_timestamp == advert_timestamp
        && packet->getPathHashCount() > entry.progress_marker) {
      entry.confirmed = true;
      return;
    }
  }
}

bool Mesh::shouldSuppressEchoedAdvertForward(const Packet* packet) const {
  if (packet == NULL || !packet->isRouteFlood()) {
    return false;
  }

  uint32_t advert_timestamp;
  if (!getRecentAdvertTimestamp(packet, advert_timestamp)) {
    return false;
  }

  uint8_t packet_hash[MAX_HASH_SIZE];
  packet->calculatePacketHash(packet_hash);
  for (int i = 0; i < MAX_RECENT_ADVERT_ECHOS; i++) {
    const RecentAdvertEchoEntry& entry = _recent_advert_echoes[i];
    if (entry.valid && entry.confirmed && entry.advert_timestamp == advert_timestamp
        && memcmp(entry.packet_hash, packet_hash, MAX_HASH_SIZE) == 0) {
      return true;
    }
  }
  return false;
}

bool Mesh::cancelFloodRetryOnEcho(const Packet* packet) {
  if (_active_flood_retry_count == 0) return false;

  uint8_t recv_key[MAX_HASH_SIZE];
  packet->calculatePacketHash(recv_key);

  bool cleared = false;
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active || memcmp(recv_key, _flood_retries[i].retry_key, MAX_HASH_SIZE) != 0) {
      continue;
    }
    if (!isFloodRetryEchoTarget(packet, _flood_retries[i].progress_marker)) {
      continue;
    }

    uint32_t echo_millis = _flood_retries[i].retry_started_at == 0
      ? 0
      : (uint32_t)(_ms->getMillis() - _flood_retries[i].retry_started_at);
    uint8_t retry_attempt = _flood_retries[i].waiting_final_echo
      ? _flood_retries[i].retry_attempts_sent
      : _flood_retries[i].retry_attempts_sent + 1;
    onFloodRetryEvent("good", packet, echo_millis, retry_attempt);

    retireFloodRetrySlot(i);
    cleared = true;
  }

  return cleared;
}

void Mesh::armFloodRetryOnSendComplete(const Packet* packet) {
  if (_active_flood_retry_count == 0) return;

  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active) {
      continue;
    }

    if (_flood_retries[i].queued) {
      if (_flood_retries[i].packet != packet) {
        continue;
      }

      uint32_t elapsed_millis = _flood_retries[i].retry_started_at == 0
        ? 0
        : (uint32_t)(_ms->getMillis() - _flood_retries[i].retry_started_at);
      onFloodRetryEvent("resent", packet, elapsed_millis, _flood_retries[i].retry_attempts_sent + 1);
      _flood_retries[i].retry_attempts_sent++;

      uint8_t max_attempts = getEligibleFloodRetryMaxAttempts(packet);
      if (max_attempts == 0) {
        clearFloodRetrySlot(i);
        continue;
      }
      if (_flood_retries[i].retry_attempts_sent >= max_attempts) {
        // Dispatcher releases the transmitted packet after this hook. Keep only
        // retry metadata during the final echo window so RX retains the pool slot.
        _flood_retries[i].packet = NULL;
        _flood_retries[i].retry_at = futureMillis(_flood_retries[i].retry_delay);
        _flood_retries[i].waiting_final_echo = true;
        if (_waiting_flood_retry_count == 0
            || (int32_t)(_flood_retries[i].retry_at - _next_flood_retry_timeout) < 0) {
          _next_flood_retry_timeout = _flood_retries[i].retry_at;
        }
        _waiting_flood_retry_count++;
        _flood_retries[i].queued = false;
        continue;
      }

      Packet* retry = obtainNewPacket();
      if (retry == NULL) {
        onFloodRetryEvent("dropped_no_packet", packet, elapsed_millis, _flood_retries[i].retry_attempts_sent + 1);
        onFloodRetryEvent("failure", packet, elapsed_millis, _flood_retries[i].retry_attempts_sent + 1);
        clearFloodRetrySlot(i);
        continue;
      }

      *retry = *packet;
      retry->tx_cr = getDefaultTxCodingRate();
      uint32_t retry_delay = getFloodRetryAttemptDelay(packet, _flood_retries[i].retry_attempts_sent);
      if (queueOutboundPacket(retry, _flood_retries[i].priority, retry_delay)) {
        _flood_retries[i].packet = retry;
        _flood_retries[i].retry_delay = retry_delay;
        _flood_retries[i].retry_at = futureMillis(retry_delay);
        _flood_retries[i].retry_started_at = _ms->getMillis();
        _flood_retries[i].waiting_final_echo = false;
        onFloodRetryEvent("queued", retry, retry_delay, _flood_retries[i].retry_attempts_sent + 1);
      } else {
        onFloodRetryEvent("dropped_queue_full", retry, retry_delay, _flood_retries[i].retry_attempts_sent + 1);
        onFloodRetryEvent("failure", retry, elapsed_millis, _flood_retries[i].retry_attempts_sent + 1);
        releasePacket(retry);
        clearFloodRetrySlot(i);
      }
      continue;
    }

    if (_flood_retries[i].trigger_packet != packet) {
      continue;
    }

    if (getEligibleFloodRetryMaxAttempts(packet) == 0) {
      clearFloodRetrySlot(i);
      continue;
    }

    Packet* retry = obtainNewPacket();
    if (retry == NULL) {
      onFloodRetryEvent("dropped_no_packet", packet, _flood_retries[i].retry_delay, 1);
      onFloodRetryEvent("failure", packet, 0, 1);
      clearFloodRetrySlot(i);
      continue;
    }

    *retry = *packet;
    retry->tx_cr = getDefaultTxCodingRate();
    if (queueOutboundPacket(retry, _flood_retries[i].priority, _flood_retries[i].retry_delay)) {
      unsigned long now = _ms->getMillis();
      _flood_retries[i].packet = retry;
      _flood_retries[i].trigger_packet = NULL;
      _flood_retries[i].queued = true;
      _flood_retries[i].waiting_final_echo = false;
      _flood_retries[i].retry_at = futureMillis(_flood_retries[i].retry_delay);
      _flood_retries[i].retry_started_at = now;
      onFloodRetryEvent("queued", retry, _flood_retries[i].retry_delay, 1);
    } else {
      onFloodRetryEvent("dropped_queue_full", retry, _flood_retries[i].retry_delay, 1);
      onFloodRetryEvent("failure", retry, 0, 1);
      releasePacket(retry);
      clearFloodRetrySlot(i);
    }
  }
}

void Mesh::clearPendingFloodRetryOnSendFail(const Packet* packet) {
  if (_active_flood_retry_count == 0) return;

  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active) {
      continue;
    }

    if (_flood_retries[i].queued) {
      if (_flood_retries[i].packet == packet) {
        onFloodRetryEvent("dropped_send_fail", packet, 0, _flood_retries[i].retry_attempts_sent + 1);
        onFloodRetryEvent("failure", packet, 0, _flood_retries[i].retry_attempts_sent + 1);
        clearFloodRetrySlot(i);
      }
      continue;
    }

    if (_flood_retries[i].trigger_packet == packet) {
      onFloodRetryEvent("dropped_send_fail", packet, 0, 1);
      onFloodRetryEvent("failure", packet, 0, 1);
      clearFloodRetrySlot(i);
    }
  }
}

void Mesh::maybeScheduleFloodRetry(const Packet* packet, uint8_t priority) {
  if (packet == NULL || !packet->isRouteFlood()) {
    return;
  }

  // Keep all count/type/path gates in one check, which is also reused when a
  // delayed retry reaches the radio so a newly disabled retry stays disabled.
  if (getEligibleFloodRetryMaxAttempts(packet) == 0) {
    return;
  }

  uint8_t retry_key[MAX_HASH_SIZE];
  packet->calculatePacketHash(retry_key);
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (_flood_retries[i].active
        && memcmp(retry_key, _flood_retries[i].retry_key, MAX_HASH_SIZE) == 0) {
      return;  // the normal flood still sends, but only one retry sequence owns this logical packet
    }
  }

  int slot_idx = -1;
  for (int i = 0; i < MAX_FLOOD_RETRY_SLOTS; i++) {
    if (!_flood_retries[i].active) {
      slot_idx = i;
      break;
    }
  }
  if (slot_idx < 0) {
    onFloodRetryEvent("dropped_no_slot", packet, 0, 0);
    onFloodRetryEvent("failure", packet, 0, 0);
    return;
  }

  if (!prepareFloodRetry(packet)) {
    return;
  }

  uint32_t retry_delay = getFloodRetryAttemptDelay(packet, 0);
  memcpy(_flood_retries[slot_idx].retry_key, retry_key, sizeof(retry_key));
  _flood_retries[slot_idx].packet = NULL;
  _flood_retries[slot_idx].trigger_packet = const_cast<Packet*>(packet);
  _flood_retries[slot_idx].retry_started_at = 0;
  _flood_retries[slot_idx].retry_at = 0;
  _flood_retries[slot_idx].retry_delay = retry_delay;
  _flood_retries[slot_idx].retry_attempts_sent = 0;
  _flood_retries[slot_idx].priority = priority;
  _flood_retries[slot_idx].progress_marker = packet->getPathHashCount();
  _flood_retries[slot_idx].self_advert = isSelfOriginAdvert(packet);
  _flood_retries[slot_idx].waiting_final_echo = false;
  _flood_retries[slot_idx].queued = false;
  _flood_retries[slot_idx].active = true;
  _active_flood_retry_count++;
}

Packet* Mesh::createAdvert(const LocalIdentity& id, const uint8_t* app_data, size_t app_data_len) {
  if (app_data_len > MAX_ADVERT_DATA_SIZE) return NULL;

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createAdvert(): error, packet pool empty", getLogDateTime());
    return NULL;
  }

  packet->header = (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);  // ROUTE_TYPE_* is set later

  int len = 0;
  memcpy(&packet->payload[len], id.pub_key, PUB_KEY_SIZE); len += PUB_KEY_SIZE;

  uint32_t emitted_timestamp = _rtc->getCurrentTime();
  memcpy(&packet->payload[len], &emitted_timestamp, 4); len += 4;

  uint8_t* signature = &packet->payload[len]; len += SIGNATURE_SIZE;  // will fill this in later

  memcpy(&packet->payload[len], app_data, app_data_len); len += app_data_len;

  packet->payload_len = len;

  {
    uint8_t message[PUB_KEY_SIZE + 4 + MAX_ADVERT_DATA_SIZE];
    int msg_len = 0;
    memcpy(&message[msg_len], id.pub_key, PUB_KEY_SIZE); msg_len += PUB_KEY_SIZE;
    memcpy(&message[msg_len], &emitted_timestamp, 4); msg_len += 4;
    memcpy(&message[msg_len], app_data, app_data_len); msg_len += app_data_len;

    id.sign(signature, message, msg_len);
  }

  return packet;
}

#define MAX_COMBINED_PATH  (MAX_PACKET_PAYLOAD - 2 - CIPHER_BLOCK_SIZE)

Packet* Mesh::createPathReturn(const Identity& dest, const uint8_t* secret, const uint8_t* path, uint8_t path_len, uint8_t extra_type, const uint8_t*extra, size_t extra_len) {
  uint8_t dest_hash[PATH_HASH_SIZE];
  dest.copyHashTo(dest_hash);
  return createPathReturn(dest_hash, secret, path, path_len, extra_type, extra, extra_len);
}

Packet* Mesh::createPathReturn(const uint8_t* dest_hash, const uint8_t* secret, const uint8_t* path, uint8_t path_len, uint8_t extra_type, const uint8_t*extra, size_t extra_len) {
  uint8_t path_hash_size = (path_len >> 6) + 1;
  uint8_t path_hash_count = path_len & 63;

  if (path_hash_count*path_hash_size + extra_len + 5 > MAX_COMBINED_PATH) return NULL;  // too long!!

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createPathReturn(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_PATH << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  int len = 0;
  memcpy(&packet->payload[len], dest_hash, PATH_HASH_SIZE); len += PATH_HASH_SIZE;  // dest hash
  len += self_id.copyHashTo(&packet->payload[len]);  // src hash

  {
    int data_len = 0;
    uint8_t data[MAX_PACKET_PAYLOAD];

    data[data_len++] = path_len;
    memcpy(&data[data_len], path, path_hash_count*path_hash_size); data_len += path_hash_count*path_hash_size;
    if (extra_len > 0) {
      data[data_len++] = extra_type;
      memcpy(&data[data_len], extra, extra_len); data_len += extra_len;
    } else {
      // append a timestamp, or random blob (to make packet_hash unique)
      data[data_len++] = 0xFF;  // dummy payload type
      getRNG()->random(&data[data_len], 4); data_len += 4;
    }

    len += Utils::encryptThenMAC(secret, &packet->payload[len], data, data_len);
  }

  packet->payload_len = len;

  return packet;
}

Packet* Mesh::createDatagram(uint8_t type, const Identity& dest, const uint8_t* secret, const uint8_t* data, size_t data_len) {
  if (type == PAYLOAD_TYPE_TXT_MSG || type == PAYLOAD_TYPE_REQ || type == PAYLOAD_TYPE_RESPONSE) {
    if (data_len + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE-1 > MAX_PACKET_PAYLOAD) return NULL;
  } else {
    return NULL;  // invalid type
  }

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createDatagram(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (type << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  int len = 0;
  len += dest.copyHashTo(&packet->payload[len]);  // dest hash
  len += self_id.copyHashTo(&packet->payload[len]);  // src hash
  len += Utils::encryptThenMAC(secret, &packet->payload[len], data, data_len);

  packet->payload_len = len;

  return packet;
}

Packet* Mesh::createAnonDatagram(uint8_t type, const LocalIdentity& sender, const Identity& dest, const uint8_t* secret, const uint8_t* data, size_t data_len) {
  if (type == PAYLOAD_TYPE_ANON_REQ) {
    if (data_len + 1 + PUB_KEY_SIZE + CIPHER_BLOCK_SIZE-1 > MAX_PACKET_PAYLOAD) return NULL;
  } else {
    return NULL;  // invalid type
  }

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createAnonDatagram(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (type << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  int len = 0;
  if (type == PAYLOAD_TYPE_ANON_REQ) {
    len += dest.copyHashTo(&packet->payload[len]);  // dest hash
    memcpy(&packet->payload[len], sender.pub_key, PUB_KEY_SIZE); len += PUB_KEY_SIZE;  // sender pub_key
  } else {
    // FUTURE:
  }
  len += Utils::encryptThenMAC(secret, &packet->payload[len], data, data_len);

  packet->payload_len = len;

  return packet;
}

Packet* Mesh::createGroupDatagram(uint8_t type, const GroupChannel& channel, const uint8_t* data, size_t data_len) {
  if (!(type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_GRP_DATA)) return NULL;   // invalid type
  if (data_len + 1 + CIPHER_BLOCK_SIZE-1 > MAX_PACKET_PAYLOAD) return NULL; // too long

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createGroupDatagram(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (type << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  int len = 0;
  memcpy(&packet->payload[len], channel.hash, PATH_HASH_SIZE); len += PATH_HASH_SIZE;
  len += Utils::encryptThenMAC(channel.secret, &packet->payload[len], data, data_len);

  packet->payload_len = len;

  return packet;
}

Packet* Mesh::createAck(const uint8_t* ack_hash, uint8_t ack_len) {
  if (ack_len > sizeof(Packet::payload)) return NULL;

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createAck(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  memcpy(packet->payload, ack_hash, ack_len);
  packet->payload_len = ack_len;

  return packet;
}

Packet* Mesh::createAck(uint32_t ack_crc) {
  return createAck((const uint8_t*)&ack_crc, 4);
}

Packet* Mesh::createMultiAck(const uint8_t* ack_hash, uint8_t ack_len, uint8_t remaining) {
  if (ack_len + 1 > sizeof(Packet::payload)) return NULL;

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createMultiAck(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_MULTIPART << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  packet->payload[0] = (remaining << 4) | PAYLOAD_TYPE_ACK;
  memcpy(&packet->payload[1], ack_hash, ack_len);
  packet->payload_len = 1 + ack_len;

  return packet;
}

Packet* Mesh::createMultiAck(uint32_t ack_crc, uint8_t remaining) {
  return createMultiAck((const uint8_t*)&ack_crc, 4, remaining);
}

Packet* Mesh::createRawData(const uint8_t* data, size_t len) {
  if (len > sizeof(Packet::payload)) return NULL;  // invalid arg

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createRawData(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_RAW_CUSTOM << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  memcpy(packet->payload, data, len);
  packet->payload_len = len;

  return packet;
}

Packet* Mesh::createTrace(uint32_t tag, uint32_t auth_code, uint8_t flags) {
  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createTrace(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_TRACE << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  memcpy(packet->payload, &tag, 4);
  memcpy(&packet->payload[4], &auth_code, 4);
  packet->payload[8] = flags;
  packet->payload_len = 9;  // NOTE: path will be appended to payload[] later

  return packet;
}

Packet* Mesh::createControlData(const uint8_t* data, size_t len) {
  if (len > sizeof(Packet::payload)) return NULL;  // invalid arg

  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createControlData(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_CONTROL << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set later

  memcpy(packet->payload, data, len);
  packet->payload_len = len;

  return packet;
}

#if defined(ENABLE_OTA)
Packet* Mesh::createOtaPacket(const uint8_t* data, size_t len) {
  if (len > sizeof(Packet::payload)) return NULL;
  Packet* packet = obtainNewPacket();
  if (packet == NULL) {
    MESH_DEBUG_PRINTLN("%s Mesh::createOtaPacket(): error, packet pool empty", getLogDateTime());
    return NULL;
  }
  packet->header = (PAYLOAD_TYPE_OTA << PH_TYPE_SHIFT);  // ROUTE_TYPE_* set by sendOtaFlood
  memcpy(packet->payload, data, len);
  packet->payload_len = len;
  return packet;
}

void Mesh::sendOtaFlood(Packet* packet, uint32_t delay_millis) {
  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_FLOOD;
  packet->setPathHashSizeAndCount(1, 0);
  _tables->markSeen(packet);   // mark as sent, in case it floods back to us
  sendPacket(packet, OTA_TX_PRIORITY, delay_millis);
}
#endif

bool Mesh::sendFlood(Packet* packet, uint32_t delay_millis, uint8_t path_hash_size) {
  if (packet->getPayloadType() == PAYLOAD_TYPE_TRACE) {
    MESH_DEBUG_PRINTLN("%s Mesh::sendFlood(): TRACE type not suspported", getLogDateTime());
    releasePacket(packet);
    return false;
  }
  if (path_hash_size == 0 || path_hash_size > 3) {
    MESH_DEBUG_PRINTLN("%s Mesh::sendFlood(): invalid path_hash_size", getLogDateTime());
    releasePacket(packet);
    return false;
  }

  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_FLOOD;
  packet->setPathHashSizeAndCount(path_hash_size, 0);

  _tables->markSent(packet); // mark this packet as already sent in case it is rebroadcast back to us

  uint8_t pri;
  if (packet->getPayloadType() == PAYLOAD_TYPE_PATH) {
    pri = 2;
  } else if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    pri = 3;   // de-prioritie these
  } else {
    pri = 1;
  }
  maybeScheduleFloodRetry(packet, pri);
  bool queued = sendPacket(packet, pri, delay_millis);
  if (queued) replaceQueuedSelfAdvertRetries(packet);
  return queued;
}

bool Mesh::sendFlood(Packet* packet, uint16_t* transport_codes, uint32_t delay_millis, uint8_t path_hash_size) {
  if (packet->getPayloadType() == PAYLOAD_TYPE_TRACE) {
    MESH_DEBUG_PRINTLN("%s Mesh::sendFlood(): TRACE type not suspported", getLogDateTime());
    releasePacket(packet);
    return false;
  }
  if (path_hash_size == 0 || path_hash_size > 3) {
    MESH_DEBUG_PRINTLN("%s Mesh::sendFlood(): invalid path_hash_size", getLogDateTime());
    releasePacket(packet);
    return false;
  }

  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_TRANSPORT_FLOOD;
  packet->transport_codes[0] = transport_codes[0];
  packet->transport_codes[1] = transport_codes[1];
  packet->setPathHashSizeAndCount(path_hash_size, 0);

  _tables->markSent(packet); // mark this packet as already sent in case it is rebroadcast back to us

  uint8_t pri;
  if (packet->getPayloadType() == PAYLOAD_TYPE_PATH) {
    pri = 2;
  } else if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    pri = 3;   // de-prioritie these
  } else {
    pri = 1;
  }
  maybeScheduleFloodRetry(packet, pri);
  bool queued = sendPacket(packet, pri, delay_millis);
  if (queued) replaceQueuedSelfAdvertRetries(packet);
  return queued;
}

bool Mesh::sendDirect(Packet* packet, const uint8_t* path, uint8_t path_len, uint32_t delay_millis) {
  if (packet == NULL) return false;

  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_DIRECT;

  uint8_t pri;
  if (packet->getPayloadType() == PAYLOAD_TYPE_TRACE) {   // TRACE packets are different
    if ((path_len > 0 && path == NULL)
        || packet->payload_len > sizeof(packet->payload)
        || path_len > sizeof(packet->payload) - packet->payload_len) {
      MESH_DEBUG_PRINTLN("%s Mesh::sendDirect(): TRACE path is too long", getLogDateTime());
      releasePacket(packet);
      return false;
    }
    // for TRACE packets, path is appended to end of PAYLOAD. (path is used for SNR's)
    if (path_len > 0) {
      memcpy(&packet->payload[packet->payload_len], path, path_len);  // path_len can be > 64 (TRACE raw route bytes)
    }
    packet->payload_len += path_len;

    packet->path_len = 0;
    pri = getTraceDirectPriority(packet);
  } else {
    uint8_t path_bytes = (path_len & 63) * ((path_len >> 6) + 1);
    if (!Packet::isValidPathLen(path_len) || (path_bytes > 0 && path == NULL)) {
      MESH_DEBUG_PRINTLN("%s Mesh::sendDirect(): invalid path_len=%u", getLogDateTime(), (uint32_t)path_len);
      releasePacket(packet);
      return false;
    }
    packet->path_len = path_bytes > 0 ? Packet::copyPath(packet->path, path, path_len) : path_len;
    if (packet->getPayloadType() == PAYLOAD_TYPE_PATH) {
      pri = 1;   // slightly less priority
    } else {
      pri = 0;
    }
  }
  _tables->markSent(packet); // mark this packet as already sent in case it is rebroadcast back to us
  maybeScheduleDirectRetry(packet, pri);
  return sendPacket(packet, pri, delay_millis);
}

void Mesh::sendZeroHop(Packet* packet, uint32_t delay_millis) {
  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_DIRECT;

  packet->path_len = 0;  // path_len of zero means Zero Hop

  _tables->markSent(packet); // mark this packet as already sent in case it is rebroadcast back to us

  sendPacket(packet, 0, delay_millis);
}

void Mesh::sendZeroHop(Packet* packet, uint16_t* transport_codes, uint32_t delay_millis) {
  packet->header &= ~PH_ROUTE_MASK;
  packet->header |= ROUTE_TYPE_TRANSPORT_DIRECT;
  packet->transport_codes[0] = transport_codes[0];
  packet->transport_codes[1] = transport_codes[1];

  packet->path_len = 0;  // path_len of zero means Zero Hop

  _tables->markSent(packet); // mark this packet as already sent in case it is rebroadcast back to us

  sendPacket(packet, 0, delay_millis);
}

}
