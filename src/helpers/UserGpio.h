#pragma once

#include <stddef.h>
#include <stdint.h>

#include <MeshCore.h>

class UserGpio {
public:
  enum State : uint8_t {
    STATE_OFF,
    STATE_ON,
    STATE_RESET
  };

  explicit UserGpio(mesh::MainBoard& board);

  // args is the text following "get gpio" or "set gpio".
  void handleGet(const char* args, char* reply, size_t reply_size);
  void handleSet(const char* args, char* reply, size_t reply_size);

  // Applies expired, non-blocking timed transitions.
  void loop();
  bool hasActiveTimer() const { return _timer_mask != 0; }

private:
  static const uint8_t MAX_GPIO_PIN = 63;

  mesh::MainBoard* _board;
  uint64_t _controlled_mask;
  uint64_t _timer_mask;
  uint64_t _timer_final_on_mask;
  uint64_t _timer_final_reset_mask;
  uint32_t _timer_deadline[MAX_GPIO_PIN + 1];

  static uint64_t pinMask(uint8_t pin) { return UINT64_C(1) << pin; }
  static const char* stateName(State state);

  bool isAvailable(uint32_t pin) const;
  State currentState(uint8_t pin) const;
  State timerFinalState(uint8_t pin) const;
  void writeState(uint8_t pin, State state);
  void cancelTimer(uint8_t pin);
  void scheduleTimer(uint8_t pin, uint32_t seconds, State final_state);
};
