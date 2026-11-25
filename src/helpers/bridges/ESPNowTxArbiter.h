#pragma once

#include <stdint.h>

namespace mesh { namespace bridge {

// One coordinator owns the air between this pair. A peer can use a grant only
// inside its original request window. The coordinator's hold starts later,
// when it hears the request, so loss of the completion cannot cause overlap.
class EspNowTxArbiter {
public:
  enum class Kind : uint8_t { None, Request, Grant, Done };
  struct Message {
    Kind kind;
    uint64_t id;
    Message(Kind message_kind = Kind::None, uint64_t message_id = 0)
        : kind(message_kind), id(message_id) {}
  };
  static constexpr uint32_t WindowMs = 2500;
  static constexpr uint32_t ClockMarginMs = 50;
  static constexpr uint32_t RetryMs = 8;
  // Leave the downstream Companion time to resume scanning after a packet.
  static constexpr uint8_t TurnaroundMs = 180;
  static constexpr uint8_t MaxTurnaroundMs = 250;

  void begin(bool coordinator, uint32_t now) {
    const uint8_t turnaround = turnaround_ms_;
    *this = EspNowTxArbiter{};
    turnaround_ms_ = turnaround;
    ready_ = true;
    coordinator_ = coordinator;
    startup_ = next_message_ = now;
  }

  void stop() { ready_ = false; }

  bool ready() const { return ready_; }
  bool coordinator() const { return coordinator_; }
  uint8_t turnaround() const { return turnaround_ms_; }
  void setTurnaround(uint8_t milliseconds) { turnaround_ms_ = milliseconds; }

  void receive(Message message, uint32_t now) {
    if (!ready_ || !message.id) return;
    expire(now);
    if (coordinator_) {
      if (message.kind == Kind::Request && !remote_id_ && message.id != completed_) {
        remote_id_ = message.id;
        remote_at_ = now;
        next_message_ = now;
      } else if (message.kind == Kind::Done && message.id == remote_id_) {
        completed_ = remote_id_;
        remote_id_ = 0;
        remote_granted_ = false;
        quiet_until_ = now + turnaround_ms_;
        quiet_ = true;
        local_priority_ = true;
        local_until_ = quiet_until_ + RetryMs;
      }
    } else if (message.kind == Kind::Grant) {
      if (message.id == request_id_) have_grant_ = true;
      if (message.id == completed_) done_pending_ = true;
    }
  }

  // Leave the chip in receive while waiting for a grant. CAD runs only when
  // the peer has finished transmitting and this board has a turn.
  bool permitted(uint32_t now, uint32_t airtime_ms, uint64_t new_id) {
    if (!ready_ || !airtime_ms || airtime_ms >= WindowMs - ClockMarginMs) return false;
    expire(now);
    if (active_ || quiet_) return false;
    if (coordinator_) {
      if (uint32_t(now - startup_) < WindowMs ||
          (remote_id_ && (remote_granted_ || !local_priority_))) return false;
    } else {
      if (!request_id_) {
        request_id_ = new_id;
        request_at_ = now;
        next_message_ = now;
      }
      if (!have_grant_
          || uint32_t(now - request_at_) >= WindowMs - ClockMarginMs - airtime_ms) return false;
    }
    return true;
  }

  bool acquire(uint32_t now, uint32_t airtime_ms, uint64_t new_id) {
    if (!permitted(now, airtime_ms, new_id)) return false;
    local_priority_ = false;
    active_ = true;
    started_ = now;
    return true;
  }

  // A busy channel can defer a queued send after the grant has arrived.
  // Return the unused turn without waiting for the failure deadline.
  void yield(uint32_t now) {
    if (!coordinator_ && !active_ && have_grant_) releasePeerTurn(now);
  }

  void complete(uint32_t now) {
    if (!active_) return;
    active_ = false;
    quiet_ = true;
    quiet_until_ = now + turnaround_ms_;
    if (!coordinator_) releasePeerTurn(now);
  }

  Message poll(uint32_t now) {
    if (!ready_) return {};
    expire(now);
    if (int32_t(now - next_message_) < 0) return {};
    if (coordinator_ && !active_ && !quiet_ && !local_priority_ && remote_id_
        && uint32_t(now - startup_) >= WindowMs) {
      remote_granted_ = true;
      next_message_ = now + RetryMs;
      return {Kind::Grant, remote_id_};
    }
    if (!coordinator_) {
      if (done_pending_) {
        done_pending_ = false;
        return {Kind::Done, completed_};
      }
      if (request_id_ && !have_grant_ && !active_) {
        next_message_ = now + RetryMs;
        return {Kind::Request, request_id_};
      }
    }
    return {};
  }

private:
  void releasePeerTurn(uint32_t now) {
    completed_ = request_id_;
    request_id_ = 0;
    have_grant_ = false;
    done_pending_ = true;
    next_message_ = now;
  }

  void expire(uint32_t now) {
    if (quiet_ && int32_t(now - quiet_until_) >= 0) quiet_ = false;
    if (local_priority_ && int32_t(now - local_until_) >= 0) local_priority_ = false;
    // A TX-start error or stalled firmware may lose its completion callback.
    // Ordinary transmissions fit inside this bounded reservation window.
    if (active_ && uint32_t(now - started_) >= WindowMs) active_ = false;
    if (remote_id_ && uint32_t(now - remote_at_) >= WindowMs) {
      completed_ = remote_id_;
      remote_id_ = 0;
      remote_granted_ = false;
    }
    if (!active_ && request_id_ && uint32_t(now - request_at_) >= WindowMs) {
      request_id_ = 0;
      have_grant_ = false;
    }
  }

  uint64_t request_id_ = 0, remote_id_ = 0, completed_ = 0;
  uint32_t startup_ = 0, started_ = 0, request_at_ = 0, remote_at_ = 0;
  uint32_t next_message_ = 0, quiet_until_ = 0, local_until_ = 0;
  bool ready_ = false, coordinator_ = false, active_ = false, quiet_ = false;
  bool have_grant_ = false, done_pending_ = false;
  bool remote_granted_ = false, local_priority_ = false;
  uint8_t turnaround_ms_ = TurnaroundMs;
};

#if defined(MESH_ESPNOW_TXSYNC_LAB)
// Implemented by the ESP-NOW bridge in the opt-in bench repeater build.
bool permitEspNowTx(uint32_t now, uint32_t airtime_ms);
bool acquireEspNowTx(uint32_t now, uint32_t airtime_ms);
void yieldEspNowTx(uint32_t now);
void completeEspNowTx(uint32_t now);
uint8_t espNowTxTurnaround();
void setEspNowTxTurnaround(uint8_t milliseconds);
#endif

}}  // namespace mesh::bridge
