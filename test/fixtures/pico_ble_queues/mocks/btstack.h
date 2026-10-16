#pragma once

#include <Arduino.h>
#include <algorithm>
#include <cassert>
#include <deque>
#include <vector>

using bd_addr_t = uint8_t[6];
struct btstack_context_callback_registration_t {
  void* next;
  void (*callback)(void*);
  void* context;
};
constexpr uint8_t ERROR_CODE_SUCCESS = 0;
constexpr uint8_t ERROR_CODE_UNKNOWN_CONNECTION_IDENTIFIER = 2;
constexpr uint8_t ERROR_CODE_COMMAND_DISALLOWED = 0x0c;
constexpr uint8_t BTSTACK_ACL_BUFFERS_FULL = 0x57;
constexpr uint8_t ATT_SECURITY_AUTHENTICATED = 2;
constexpr uint8_t IO_CAPABILITY_DISPLAY_ONLY = 0;
constexpr uint8_t SM_AUTHREQ_MITM_PROTECTION = 4;
constexpr uint8_t SM_AUTHREQ_BONDING = 1;
constexpr uint8_t BLUETOOTH_DATA_TYPE_COMPLETE_LOCAL_NAME = 9;

namespace fake_pico {
using Bytes = std::vector<uint8_t>;
inline unsigned lock_depth = 0, callback_depth = 0, max_callback_depth = 0;
inline unsigned registrations = 0, notification_attempts = 0, disconnects = 0;
inline uint32_t callback_advance_ms = 0;
inline bool synchronous = true, callbacks_enabled = true, connection_live = false;
inline bool duplicate_registration = false, synchronous_disconnect = false;
inline uint8_t registration_error = 0, disconnect_error = 0;
inline uint16_t handle = 0, mtu = 23;
inline std::deque<uint8_t> notify_errors;
inline std::vector<Bytes> notified;
inline std::deque<btstack_context_callback_registration_t*> pending;
inline void (*disconnect_event)() = nullptr;
inline void require_context() { assert(lock_depth || callback_depth); }
inline void fireOne() {
  if (pending.empty()) return;
  auto* node = pending.front(); pending.pop_front();
  ++callback_depth;
  max_callback_depth = std::max(max_callback_depth, callback_depth);
  assert(callback_depth <= 16
      && "blocked notification must defer instead of reentering its callback");
  now += callback_advance_ms;
  node->callback(node->context);
  --callback_depth;
}
}

// Contracts from resolved Arduino-Pico 1.50501.0's BTstack att_server.h/.c:
// registration can invoke its callback inline; a duplicate node is already
// pending; notification returns an error status, not an accepted byte count.
inline uint8_t att_server_register_can_send_now_callback(
    btstack_context_callback_registration_t* node, uint16_t handle) {
  fake_pico::require_context(); ++fake_pico::registrations;
  if (!fake_pico::connection_live || handle != fake_pico::handle)
    return ERROR_CODE_UNKNOWN_CONNECTION_IDENTIFIER;
  if (fake_pico::registration_error) return fake_pico::registration_error;
  auto existing = std::find(fake_pico::pending.begin(), fake_pico::pending.end(), node);
  const bool duplicate = existing != fake_pico::pending.end() || fake_pico::duplicate_registration;
  if (existing == fake_pico::pending.end()) fake_pico::pending.push_back(node);
  if (fake_pico::synchronous && fake_pico::callbacks_enabled) fake_pico::fireOne();
  return duplicate ? ERROR_CODE_COMMAND_DISALLOWED : ERROR_CODE_SUCCESS;
}
inline uint16_t att_server_get_mtu(uint16_t handle) {
  fake_pico::require_context();
  return fake_pico::connection_live && handle == fake_pico::handle ? fake_pico::mtu : 0;
}
inline uint8_t att_server_notify(uint16_t handle, uint16_t, const uint8_t* data, uint16_t length) {
  fake_pico::require_context(); ++fake_pico::notification_attempts;
  if (!fake_pico::connection_live || handle != fake_pico::handle)
    return ERROR_CODE_UNKNOWN_CONNECTION_IDENTIFIER;
  assert(length + 3 <= fake_pico::mtu && "protocol frames must never be truncated or fragmented");
  if (!fake_pico::notify_errors.empty()) {
    const uint8_t error = fake_pico::notify_errors.front(); fake_pico::notify_errors.pop_front();
    if (error) return error;
  }
  fake_pico::notified.emplace_back(data, data + length);
  return ERROR_CODE_SUCCESS;
}
inline uint8_t gap_disconnect(uint16_t) {
  fake_pico::require_context(); ++fake_pico::disconnects;
  if (fake_pico::disconnect_error) return fake_pico::disconnect_error;
  if (fake_pico::synchronous_disconnect && fake_pico::disconnect_event)
    fake_pico::disconnect_event();
  return ERROR_CODE_SUCCESS; // Request accepted, not proof the link closed.
}
inline void gap_local_bd_addr(uint8_t address[6]) { memset(address, 1, 6); }
inline void gap_scan_response_set_data(uint8_t, const uint8_t*) {}
inline void sm_set_io_capabilities(uint8_t) {}
inline void sm_set_authentication_requirements(uint8_t) {}
inline void sm_use_fixed_passkey_in_display_role(uint32_t) {}
