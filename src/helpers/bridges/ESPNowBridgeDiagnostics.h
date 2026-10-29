#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <helpers/ESPNowRawFragmentation.h>

namespace mesh {
namespace bridge {
namespace diag {

// These records have fixed storage and no synchronization or I/O. The bridge
// owns their callback/loop locking. Counters survive bridge stop/start; an
// explicit local reset or reboot starts a new observation window.
struct RxStats {
  uint32_t frames = 0, foreign = 0, badframe = 0, queue_drop = 0;
  uint32_t checksum_bad = 0, packet_bad = 0, packet_ok = 0;
  uint32_t filtered = 0, duplicate = 0, delivered = 0;
};

struct TxStats {
  uint32_t requested = 0, filtered = 0, duplicate = 0, oversize = 0;
  uint32_t queued = 0, start_ok = 0, start_fail = 0;
  uint32_t callback_ok = 0, callback_fail = 0, callback_timeout = 0, drops = 0;
};

struct ProbeStats {
  uint32_t tx_queued = 0, tx_start = 0, tx_ok = 0, tx_fail = 0, tx_timeout = 0;
  uint32_t rx_ok = 0, rx_bad = 0, last_tx_nonce = 0, last_rx_nonce = 0;
};

enum class Stage : uint8_t {
  None, Policy, Channel, ChannelConflict, WiFiInit, WiFiStorage, WiFiMode,
  WiFiStart, WiFiProtocol, WiFiChannel, EspNowInit, RecvCallback, SendCallback,
  PeerAdd, PeerRate, PeerRemove, EspNowDeinit, TxStart, TxCallback, TxTimeout,
  Secret, TestQueue, PeerConfig, WiFiPowerSave
};

inline const char* stageName(Stage stage) {
  switch (stage) {
    case Stage::None: return "none";
    case Stage::Policy: return "policy";
    case Stage::Channel: return "channel";
    case Stage::ChannelConflict: return "channel.conflict";
    case Stage::WiFiInit: return "wifi.init";
    case Stage::WiFiStorage: return "wifi.storage";
    case Stage::WiFiMode: return "wifi.mode";
    case Stage::WiFiStart: return "wifi.start";
    case Stage::WiFiProtocol: return "wifi.protocol";
    case Stage::WiFiChannel: return "wifi.channel";
    case Stage::EspNowInit: return "espnow.init";
    case Stage::RecvCallback: return "callback.rx";
    case Stage::SendCallback: return "callback.tx";
    case Stage::PeerAdd: return "peer.add";
    case Stage::PeerRate: return "peer.rate";
    case Stage::PeerRemove: return "peer.remove";
    case Stage::EspNowDeinit: return "espnow.deinit";
    case Stage::TxStart: return "tx.start";
    case Stage::TxCallback: return "tx.callback";
    case Stage::TxTimeout: return "tx.timeout";
    case Stage::Secret: return "secret";
    case Stage::TestQueue: return "test.queue";
    case Stage::PeerConfig: return "peer.config";
    case Stage::WiFiPowerSave: return "wifi.ps";
  }
  return "unknown";
}

struct Error {
  Stage stage = Stage::None;
  int32_t code = 0;
  uint32_t at_ms = 0;
};

struct Stats {
  RxStats rx;
  TxStats tx;
  ProbeStats probe;
  Error error;
};

static_assert(sizeof(Stats) <= 160,
              "ESP-NOW diagnostics must keep bounded counter storage");

// Saturation keeps a long-running observation from looking newly empty after
// integer wrap. Call only while the bridge owns the corresponding counter.
inline void increment(uint32_t& value) {
  if (value != UINT32_MAX) ++value;
}

// Reachability-only probe, not a MeshCore packet and not a secret/key test.
// No authentication is implied by the CRC or nonce. Never echo, relay onto
// LoRa, admit to PacketManager, or automatically retry a diagnostic probe.
//
// 0xFE has unsupported MeshCore payload-version bits. Existing raw receivers
// reserve the entire 0xFE prefix and reject this distinct MCD magic; existing
// wrapped receivers reject it because their magic is C0 3E.
// Wire: magic[4], version[1], kind[1], format[1], channel[1], nonce[4], crc[4].
// Both integers use network byte order; CRC32 covers exactly the first12 bytes.
static constexpr uint8_t ProbeMagic[] = {0xFE, 'M', 'C', 'D'};
static constexpr uint8_t ProbeVersion = 1;
static constexpr uint8_t ProbeKind = 1;
static constexpr size_t ProbeSize = 16;

struct Probe {
  uint32_t nonce = 0;
  uint8_t format = 0;
  uint8_t channel = 0;
};

inline bool validProbeMetadata(const Probe& probe) {
  return probe.format <= 1 && probe.channel >= 1 && probe.channel <= 13;
}

// Candidate detection is intentionally broader than decoding. Consume a full
// MCD prefix even when its version, length, metadata or CRC is malformed.
inline bool isProbeCandidate(const uint8_t* frame, size_t len) {
  return frame != nullptr && len >= sizeof(ProbeMagic)
      && memcmp(frame, ProbeMagic, sizeof(ProbeMagic)) == 0;
}

// No output byte is changed when validation fails. Larger output buffers are
// accepted, but only the fixed16-byte frame is written.
inline bool encodeProbe(uint8_t* output, size_t capacity, const Probe& probe) {
  if (output == nullptr || capacity < ProbeSize || !validProbeMetadata(probe)) {
    return false;
  }
  memcpy(output, ProbeMagic, sizeof(ProbeMagic));
  output[4] = ProbeVersion;
  output[5] = ProbeKind;
  output[6] = probe.format;
  output[7] = probe.channel;
  mesh::espnow::writeEspNowRawUint32(output + 8, probe.nonce);
  mesh::espnow::writeEspNowRawUint32(
      output + 12, mesh::espnow::espNowRawCrc32(output, 12));
  return true;
}

// No output field is changed on rejection, including truncated/oversize input.
inline bool decodeProbe(const uint8_t* frame, size_t len, Probe& output) {
  if (len != ProbeSize || !isProbeCandidate(frame, len)
      || frame[4] != ProbeVersion || frame[5] != ProbeKind) return false;
  Probe decoded;
  decoded.format = frame[6];
  decoded.channel = frame[7];
  decoded.nonce = mesh::espnow::readEspNowRawUint32(frame + 8);
  if (!validProbeMetadata(decoded)
      || mesh::espnow::readEspNowRawUint32(frame + 12)
          != mesh::espnow::espNowRawCrc32(frame, 12)) return false;
  output = decoded;
  return true;
}

// Exact8 hexadecimal digits; no signs, whitespace, 0x prefix or trailing text.
// Zero is valid. As with the wire decoder, failure preserves the caller's value.
inline bool parseNonce(const char* text, uint32_t& output) {
  if (text == nullptr) return false;
  uint32_t parsed = 0;
  for (size_t i = 0; i < 8; ++i) {
    const char c = text[i];
    uint8_t digit;
    if (c >= '0' && c <= '9') digit = static_cast<uint8_t>(c - '0');
    else if (c >= 'a' && c <= 'f') digit = static_cast<uint8_t>(c - 'a' + 10);
    else if (c >= 'A' && c <= 'F') digit = static_cast<uint8_t>(c - 'A' + 10);
    else return false;
    parsed = (parsed << 4) | digit;
  }
  if (text[8] != '\0') return false;
  output = parsed;
  return true;
}

}  // namespace diag
}  // namespace bridge
}  // namespace mesh
