#pragma once

#include <stdint.h>
#include <string.h>

// Keeps the authenticated remote-CLI route associated with each GPIO timer.
// The timer engine remains transport-independent; roles use this small tracker
// to send the final transition back to the client that scheduled it.
class UserGpioReplyTracker {
public:
  static const uint8_t MAX_GPIO_PIN = 63;
  static const uint8_t CLIENT_TAG_SIZE = 8;

  UserGpioReplyTracker() : _active_client_index(-1), _active_path_hash_size(1),
                           _active_client_valid(false) {
    memset(_active_client_tag, 0, sizeof(_active_client_tag));
    for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) clear(pin);
  }

  void beginCommand(int client_index, uint8_t path_hash_size,
                    const uint8_t* client_public_key = NULL) {
    _active_client_index = client_index >= 0 && client_index <= INT8_MAX
        ? (int8_t)client_index : -1;
    _active_path_hash_size = path_hash_size == 0 ? 1 : path_hash_size;
    _active_client_valid = _active_client_index >= 0 && client_public_key != NULL;
    if (_active_client_valid) {
      memcpy(_active_client_tag, client_public_key, CLIENT_TAG_SIZE);
    } else {
      memset(_active_client_tag, 0, sizeof(_active_client_tag));
    }
  }

  uint32_t requestSource() const {
    if (!_active_client_valid) return 0;
    uint32_t source = 2166136261UL;
    for (uint8_t i = 0; i < CLIENT_TAG_SIZE; i++) {
      source ^= _active_client_tag[i];
      source *= 16777619UL;
    }
    return source == 0 ? 1U : source;
  }

  void timerScheduled(uint8_t pin, uint32_t request_id) {
    if (pin > MAX_GPIO_PIN) return;
    _routes[pin].request_id = request_id;
    _routes[pin].client_index = _active_client_valid ? _active_client_index : -1;
    _routes[pin].path_hash_size = _active_path_hash_size;
    memcpy(_routes[pin].client_tag, _active_client_tag, CLIENT_TAG_SIZE);
  }

  void timerCancelled(uint8_t pin) {
    if (pin <= MAX_GPIO_PIN) clear(pin);
  }

  bool takeRoute(uint8_t pin, uint32_t request_id, int& client_index,
                 uint8_t& path_hash_size,
                 uint8_t client_tag[CLIENT_TAG_SIZE]) {
    if (pin > MAX_GPIO_PIN) return false;
    Route& route = _routes[pin];
    if (route.client_index < 0 || route.request_id != request_id) return false;

    client_index = route.client_index;
    path_hash_size = route.path_hash_size;
    memcpy(client_tag, route.client_tag, CLIENT_TAG_SIZE);
    clear(pin);
    return true;
  }

  static bool matchesClient(const uint8_t* client_public_key,
                            const uint8_t client_tag[CLIENT_TAG_SIZE]) {
    return client_public_key != NULL &&
        memcmp(client_public_key, client_tag, CLIENT_TAG_SIZE) == 0;
  }

private:
  struct Route {
    uint32_t request_id;
    uint8_t client_tag[CLIENT_TAG_SIZE];
    int8_t client_index;
    uint8_t path_hash_size;
  };

  Route _routes[MAX_GPIO_PIN + 1];
  int8_t _active_client_index;
  uint8_t _active_path_hash_size;
  uint8_t _active_client_tag[CLIENT_TAG_SIZE];
  bool _active_client_valid;

  void clear(uint8_t pin) {
    _routes[pin].request_id = 0;
    memset(_routes[pin].client_tag, 0, CLIENT_TAG_SIZE);
    _routes[pin].client_index = -1;
    _routes[pin].path_hash_size = 1;
  }
};
