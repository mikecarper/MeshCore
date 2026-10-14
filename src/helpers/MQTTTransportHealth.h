#pragma once

#include <stdint.h>

// Owner-task transport recovery, separate from the reconnect/backoff policy.
// A CONNECTED callback or accepted/enqueued publish is not proof of delivery.
namespace MQTTTransportHealth {

// Twelve times the normal 10-second socket budget gives DNS/TLS/WebSocket/
// CONNACK setup ample room, while bounding a lost final callback. PUBACK uses
// the same generous budget: longer than the 15-second retry and SDK outbox
// expiry, so an ordinary retransmit cannot trigger a premature reconnect.
static const uint32_t kAttemptTimeoutMs = 120000UL;
static const uint32_t kAckTimeoutMs = 120000UL;

enum class RecoveryReason : uint8_t {
  None, TransportError, AdapterDisconnected, HandshakeTimeout,
  PublishFailure, AckTimeout
};

inline const char* reasonLabel(RecoveryReason reason) {
  switch (reason) {
    case RecoveryReason::TransportError: return "error";
    case RecoveryReason::AdapterDisconnected: return "state";
    case RecoveryReason::HandshakeTimeout: return "handshake";
    case RecoveryReason::PublishFailure: return "write";
    case RecoveryReason::AckTimeout: return "puback";
    default: return "none";
  }
}

struct State {
  bool attempt_active = false;
  uint32_t attempt_started_ms = 0;
  bool ack_pending = false;
  uint32_t ack_started_ms = 0;
  uint32_t ack_snapshot = 0;
  RecoveryReason recovery_reason = RecoveryReason::None;
  RecoveryReason last_recovery_reason = RecoveryReason::None;
  uint32_t recoveries = 0;

  void beginAttempt(uint32_t now) {
    attempt_active = true;
    attempt_started_ms = now;
  }
  void completeAttempt() { attempt_active = false; }
  void observeAck(uint32_t count) {
    if (ack_pending && count != ack_snapshot) ack_pending = false;
  }
  void acceptedQos1(uint32_t now, uint32_t ack_before) {
    observeAck(ack_before);
    if (!ack_pending) {
      ack_pending = true;
      ack_started_ms = now;
      ack_snapshot = ack_before;
    }
  }
  bool attemptExpired(uint32_t now) const {
    return attempt_active && now - attempt_started_ms >= kAttemptTimeoutMs;
  }
  bool ackExpired(uint32_t now) const {
    return ack_pending && now - ack_started_ms >= kAckTimeoutMs;
  }
  void requestRecovery(RecoveryReason reason) {
    if (recovery_reason == RecoveryReason::None) recovery_reason = reason;
  }
  void resetSession() {
    attempt_active = false;
    ack_pending = false;
  }
  void recovered() {
    if (recoveries != UINT32_MAX) ++recoveries;
    last_recovery_reason = recovery_reason;
    recovery_reason = RecoveryReason::None;
    resetSession();
  }
};

} // namespace MQTTTransportHealth
