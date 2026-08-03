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

  struct SetResult {
    enum Outcome : uint8_t {
      ERROR,
      APPLIED,
      TIMER_STARTED,
      DUPLICATE_IGNORED
    };

    Outcome outcome;
    uint8_t pin;
    bool cancelled_timer;
  };

  struct Completion {
    uint8_t pin;
    State state;
    uint32_t request_id;
  };

  explicit UserGpio(mesh::MainBoard& board);

  // args is the text following "get gpio" or "set gpio".
  void handleGet(const char* args, char* reply, size_t reply_size);
  SetResult handleSet(const char* args, char* reply, size_t reply_size,
                      uint32_t request_id = 0, uint32_t request_source = 0);

  // Applies expired, non-blocking timed transitions.
  void loop();
  bool takeCompletion(Completion& completion);
  bool hasActiveTimer() const { return _timer_mask != 0; }

  static const char* stateName(State state);

private:
  static const uint8_t MAX_GPIO_PIN = 63;
  static const uint8_t RECENT_REQUEST_COUNT = 8;

  struct RecentTimedRequest {
    uint32_t request_id;
    uint32_t request_source;
    uint32_t command_spec;
    uint8_t pin;
    bool valid;
  };

  mesh::MainBoard* _board;
  uint64_t _controlled_mask;
  uint64_t _timer_mask;
  uint64_t _timer_final_on_mask;
  uint64_t _timer_final_reset_mask;
  uint64_t _timer_millisecond_mask;
  uint64_t _completion_mask;
  uint64_t _completion_on_mask;
  uint64_t _completion_reset_mask;
  uint32_t _timer_deadline[MAX_GPIO_PIN + 1];
  uint32_t _timer_request_id[MAX_GPIO_PIN + 1];
  uint32_t _completion_request_id[MAX_GPIO_PIN + 1];
  RecentTimedRequest _recent_requests[RECENT_REQUEST_COUNT];
  uint8_t _next_recent_request;

  static uint64_t pinMask(uint8_t pin) { return UINT64_C(1) << pin; }

  bool isAvailable(uint32_t pin) const;
  State currentState(uint8_t pin) const;
  State timerFinalState(uint8_t pin) const;
  void writeState(uint8_t pin, State state);
  void cancelTimer(uint8_t pin);
  void scheduleTimer(uint8_t pin, uint32_t duration_ms, bool display_milliseconds,
                     State final_state, uint32_t request_id);
  bool isDuplicateTimedRequest(uint8_t pin, uint32_t request_id,
                               uint32_t request_source, uint32_t command_spec) const;
  void rememberTimedRequest(uint8_t pin, uint32_t request_id,
                            uint32_t request_source, uint32_t command_spec);
  void formatAvailablePins(char* reply, size_t reply_size) const;
  void formatActivePins(char* reply, size_t reply_size) const;
  void formatPinStatus(uint8_t pin, const char* prefix, char* reply,
                       size_t reply_size) const;
};
