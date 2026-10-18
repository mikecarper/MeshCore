#pragma once

#include <stdint.h>

// Pure, platform-independent failed-login accounting for WebConfig's
// /api/login. Each client address is locked on its own after kClientFails
// failures, so one client that keeps failing cannot lock the owner out from
// another address.
//
// Addresses are cheap to change, so a shared budget also caps the failures of
// all clients together. It refills at the rate one client may fail
// (kClientFails per kClientLockMs, the old global rate) and holds one failure
// more than a single client can spend, so one failing client alone never
// empties it. While it is empty every client waits, as under the old global
// lockout.
namespace WebConfigLoginThrottle {

static const uint8_t kClientFails = 5;
static const uint32_t kClientLockMs = 30000;
static const uint8_t kSharedBudget = kClientFails + 1;
static const uint32_t kSharedRefillMs = kClientLockMs / kClientFails;
// Tracked failing clients. Evicting one only resets that client's count; the
// shared budget still bounds the total.
static const uint8_t kClients = 4;

enum Verdict { ALLOW, CLIENT_LOCKED, SHARED_LOCKED };

class Tracker {
public:
  Tracker() : _spent(0), _refill_at(0) {}

  Verdict check(uint32_t client, uint32_t now) {
    refill(now);
    // A client at kClientFails is locked until kClientLockMs after its last
    // failure; expire every slot here so a stale lock is not kept around.
    for (uint8_t i = 0; i < kClients; i++) {
      if (_slots[i].fails >= kClientFails && now - _slots[i].last >= kClientLockMs) {
        _slots[i].fails = 0;
      }
    }
    const Slot* slot = find(client);
    if (slot && slot->fails >= kClientFails) return CLIENT_LOCKED;
    if (_spent >= kSharedBudget) return SHARED_LOCKED;
    return ALLOW;
  }

  void noteFailure(uint32_t client, uint32_t now) {
    refill(now);
    if (_spent == 0) _refill_at = now;
    if (_spent < kSharedBudget) _spent++;
    Slot* slot = find(client);
    if (!slot) slot = claim(client, now);
    slot->last = now;
    if (slot->fails < kClientFails) slot->fails++;
  }

  // Clears only this client's count. The shared budget keeps draining on its
  // own, so a success does not hand other clients a fresh burst.
  void noteSuccess(uint32_t client) {
    Slot* slot = find(client);
    if (slot) slot->fails = 0;
  }

private:
  struct Slot {
    uint32_t client = 0;
    uint32_t last = 0;
    uint8_t fails = 0;  // 0 = free slot
  };

  Slot _slots[kClients];
  uint8_t _spent;
  uint32_t _refill_at;

  void refill(uint32_t now) {
    const uint32_t steps = (now - _refill_at) / kSharedRefillMs;
    if (steps >= _spent) {
      _spent = 0;
      return;
    }
    _spent -= (uint8_t)steps;
    _refill_at += steps * kSharedRefillMs;
  }

  Slot* find(uint32_t client) {
    for (uint8_t i = 0; i < kClients; i++) {
      if (_slots[i].fails > 0 && _slots[i].client == client) return &_slots[i];
    }
    return nullptr;
  }

  // A free slot, else the one whose last failure is oldest.
  Slot* claim(uint32_t client, uint32_t now) {
    Slot* victim = &_slots[0];
    for (uint8_t i = 0; i < kClients; i++) {
      if (_slots[i].fails == 0) {
        victim = &_slots[i];
        break;
      }
      if (now - _slots[i].last > now - victim->last) victim = &_slots[i];
    }
    victim->client = client;
    victim->fails = 0;
    return victim;
  }
};

} // namespace WebConfigLoginThrottle
