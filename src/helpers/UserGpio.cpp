#include "UserGpio.h"

#include <Arduino.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

namespace {

const uint32_t MAX_TIMER_MILLIS = 24UL * 60UL * 60UL * 1000UL;

const char* skipSpaces(const char* text) {
  while (*text == ' ') text++;
  return text;
}

bool atEnd(const char* text) {
  return *skipSpaces(text) == '\0';
}

bool parseUnsigned(const char*& cursor, uint32_t& value) {
  const char* p = skipSpaces(cursor);
  if (*p < '0' || *p > '9') return false;

  uint32_t parsed = 0;
  do {
    const uint8_t digit = (uint8_t)(*p - '0');
    if (parsed > (UINT32_MAX - digit) / 10U) return false;
    parsed = parsed * 10U + digit;
    p++;
  } while (*p >= '0' && *p <= '9');

  if (*p != '\0' && *p != ' ') return false;
  cursor = p;
  value = parsed;
  return true;
}

bool parseDuration(const char*& cursor, uint32_t& duration_ms,
                   bool& display_milliseconds) {
  const char* p = skipSpaces(cursor);
  if (*p < '0' || *p > '9') return false;

  uint32_t parsed = 0;
  do {
    const uint8_t digit = (uint8_t)(*p - '0');
    if (parsed > (UINT32_MAX - digit) / 10U) return false;
    parsed = parsed * 10U + digit;
    p++;
  } while (*p >= '0' && *p <= '9');

  display_milliseconds = false;
  if ((p[0] == 'm' || p[0] == 'M') && (p[1] == 's' || p[1] == 'S')) {
    display_milliseconds = true;
    p += 2;
  } else if (*p == 's' || *p == 'S') {
    p++;
  }

  if (*p != '\0' && *p != ' ') return false;
  if (display_milliseconds) {
    duration_ms = parsed;
  } else {
    if (parsed > UINT32_MAX / 1000UL) return false;
    duration_ms = parsed * 1000UL;
  }

  cursor = p;
  return true;
}

bool tokenEquals(const char* start, size_t length, const char* expected) {
  size_t i = 0;
  for (; i < length && expected[i] != '\0'; i++) {
    if (tolower((unsigned char)start[i]) != tolower((unsigned char)expected[i])) return false;
  }
  return i == length && expected[i] == '\0';
}

bool parseState(const char*& cursor, UserGpio::State& state) {
  const char* start = skipSpaces(cursor);
  const char* end = start;
  while (*end != '\0' && *end != ' ') end++;
  const size_t length = (size_t)(end - start);

  if (tokenEquals(start, length, "on")) {
    state = UserGpio::STATE_ON;
  } else if (tokenEquals(start, length, "off")) {
    state = UserGpio::STATE_OFF;
  } else if (tokenEquals(start, length, "reset")) {
    state = UserGpio::STATE_RESET;
  } else {
    return false;
  }

  cursor = end;
  return true;
}

} // namespace

UserGpio::UserGpio(mesh::MainBoard& board)
    : _board(&board),
      _controlled_mask(0),
      _timer_mask(0),
      _timer_final_on_mask(0),
      _timer_final_reset_mask(0),
      _timer_millisecond_mask(0),
      _completion_mask(0),
      _completion_on_mask(0),
      _completion_reset_mask(0),
      _next_recent_request(0) {
  memset(_timer_deadline, 0, sizeof(_timer_deadline));
  memset(_timer_request_id, 0, sizeof(_timer_request_id));
  memset(_completion_request_id, 0, sizeof(_completion_request_id));
  memset(_recent_requests, 0, sizeof(_recent_requests));
}

const char* UserGpio::stateName(State state) {
  switch (state) {
    case STATE_ON: return "on";
    case STATE_OFF: return "off";
    default: return "reset";
  }
}

bool UserGpio::isAvailable(uint32_t pin) const {
  return pin <= MAX_GPIO_PIN && _board->isUserGpioAvailable((uint8_t)pin);
}

UserGpio::State UserGpio::currentState(uint8_t pin) const {
  if ((_controlled_mask & pinMask(pin)) == 0) return STATE_RESET;
  return digitalRead(pin) == HIGH ? STATE_ON : STATE_OFF;
}

UserGpio::State UserGpio::timerFinalState(uint8_t pin) const {
  const uint64_t mask = pinMask(pin);
  if (_timer_final_reset_mask & mask) return STATE_RESET;
  if (_timer_final_on_mask & mask) return STATE_ON;
  return STATE_OFF;
}

void UserGpio::writeState(uint8_t pin, State state) {
  const uint64_t mask = pinMask(pin);
  if (state == STATE_RESET) {
    pinMode(pin, INPUT);
    _controlled_mask &= ~mask;
    return;
  }

  const uint8_t level = state == STATE_ON ? HIGH : LOW;
  // Prime the output latch before changing direction to minimize relay glitches.
  digitalWrite(pin, level);
  pinMode(pin, OUTPUT);
  digitalWrite(pin, level);
  _controlled_mask |= mask;
}

void UserGpio::cancelTimer(uint8_t pin) {
  const uint64_t mask = pinMask(pin);
  _timer_mask &= ~mask;
  _timer_final_on_mask &= ~mask;
  _timer_final_reset_mask &= ~mask;
  _timer_millisecond_mask &= ~mask;
  _timer_deadline[pin] = 0;
  _timer_request_id[pin] = 0;
}

void UserGpio::scheduleTimer(uint8_t pin, uint32_t duration_ms,
                             bool display_milliseconds, State final_state,
                             uint32_t request_id) {
  const uint64_t mask = pinMask(pin);
  _timer_deadline[pin] = millis() + duration_ms;
  _timer_request_id[pin] = request_id;
  _timer_mask |= mask;

  if (display_milliseconds) {
    _timer_millisecond_mask |= mask;
  } else {
    _timer_millisecond_mask &= ~mask;
  }

  if (final_state == STATE_ON) {
    _timer_final_on_mask |= mask;
  } else {
    _timer_final_on_mask &= ~mask;
  }

  if (final_state == STATE_RESET) {
    _timer_final_reset_mask |= mask;
  } else {
    _timer_final_reset_mask &= ~mask;
  }
}

bool UserGpio::isDuplicateTimedRequest(uint8_t pin, uint32_t request_id,
                                       uint32_t request_source,
                                       uint32_t command_spec) const {
  if (request_id == 0 || request_source == 0) return false;
  for (uint8_t i = 0; i < RECENT_REQUEST_COUNT; i++) {
    const RecentTimedRequest& recent = _recent_requests[i];
    if (recent.valid && recent.pin == pin && recent.request_id == request_id &&
        recent.request_source == request_source &&
        recent.command_spec == command_spec) {
      return true;
    }
  }
  return false;
}

void UserGpio::rememberTimedRequest(uint8_t pin, uint32_t request_id,
                                    uint32_t request_source,
                                    uint32_t command_spec) {
  if (request_id == 0 || request_source == 0) return;
  RecentTimedRequest& recent = _recent_requests[_next_recent_request];
  recent.request_id = request_id;
  recent.request_source = request_source;
  recent.command_spec = command_spec;
  recent.pin = pin;
  recent.valid = true;
  _next_recent_request = (uint8_t)((_next_recent_request + 1U) % RECENT_REQUEST_COUNT);
}

void UserGpio::loop() {
  if (_timer_mask == 0) return;

  const uint32_t now = millis();
  for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
    const uint64_t mask = pinMask(pin);
    if ((_timer_mask & mask) == 0) continue;
    if ((int32_t)(now - _timer_deadline[pin]) < 0) continue;

    const State final_state = timerFinalState(pin);
    const uint32_t request_id = _timer_request_id[pin];
    cancelTimer(pin);
    writeState(pin, final_state);

    _completion_request_id[pin] = request_id;
    _completion_mask |= mask;
    if (final_state == STATE_ON) {
      _completion_on_mask |= mask;
    } else {
      _completion_on_mask &= ~mask;
    }
    if (final_state == STATE_RESET) {
      _completion_reset_mask |= mask;
    } else {
      _completion_reset_mask &= ~mask;
    }
  }
}

bool UserGpio::takeCompletion(Completion& completion) {
  if (_completion_mask == 0) return false;

  for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
    const uint64_t mask = pinMask(pin);
    if ((_completion_mask & mask) == 0) continue;

    completion.pin = pin;
    completion.request_id = _completion_request_id[pin];
    if (_completion_reset_mask & mask) {
      completion.state = STATE_RESET;
    } else if (_completion_on_mask & mask) {
      completion.state = STATE_ON;
    } else {
      completion.state = STATE_OFF;
    }

    _completion_mask &= ~mask;
    _completion_on_mask &= ~mask;
    _completion_reset_mask &= ~mask;
    _completion_request_id[pin] = 0;
    return true;
  }
  return false;
}

void UserGpio::formatPinStatus(uint8_t pin, const char* prefix, char* reply,
                               size_t reply_size) const {
  const State current = currentState(pin);
  const uint64_t mask = pinMask(pin);
  if ((_timer_mask & mask) == 0) {
    snprintf(reply, reply_size, "%sGPIO %u %s", prefix, pin, stateName(current));
    return;
  }

  const int32_t remaining_delta = (int32_t)(_timer_deadline[pin] - millis());
  const uint32_t remaining_ms = remaining_delta > 0
      ? (uint32_t)remaining_delta : 0;
  if (_timer_millisecond_mask & mask) {
    snprintf(reply, reply_size, "%sGPIO %u %s, %lums -> %s", prefix, pin,
             stateName(current), (unsigned long)remaining_ms,
             stateName(timerFinalState(pin)));
  } else {
    const uint32_t remaining_seconds = (remaining_ms + 999UL) / 1000UL;
    snprintf(reply, reply_size, "%sGPIO %u %s, %lus -> %s", prefix, pin,
             stateName(current), (unsigned long)remaining_seconds,
             stateName(timerFinalState(pin)));
  }
}

void UserGpio::formatAvailablePins(char* reply, size_t reply_size) const {
  size_t used = (size_t)snprintf(reply, reply_size, "> available GPIOs:");
  bool any = false;
  for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
    if (!_board->isUserGpioAvailable(pin)) continue;
    if (used >= reply_size) break;
    const int written = snprintf(reply + used, reply_size - used, "%s%u",
                                 any ? "," : " ", pin);
    if (written < 0) break;
    used += (size_t)written;
    any = true;
  }
  if (!any) snprintf(reply, reply_size, "> available GPIOs: none");
}

void UserGpio::formatActivePins(char* reply, size_t reply_size) const {
  size_t used = (size_t)snprintf(reply, reply_size, "> active GPIOs:");
  bool any = false;
  for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
    const uint64_t mask = pinMask(pin);
    if ((_controlled_mask & mask) == 0 || !_board->isUserGpioAvailable(pin)) continue;
    if (used >= reply_size) break;
    const State current = currentState(pin);
    int written;
    if ((_timer_mask & mask) == 0) {
      written = snprintf(reply + used, reply_size - used, "%s%u=%s",
                         any ? "," : " ", pin, stateName(current));
    } else {
      const int32_t remaining_delta = (int32_t)(_timer_deadline[pin] - millis());
      const uint32_t remaining_ms = remaining_delta > 0
          ? (uint32_t)remaining_delta : 0;
      if (_timer_millisecond_mask & mask) {
        written = snprintf(reply + used, reply_size - used, "%s%u=%s(%lums->%s)",
                           any ? "," : " ", pin, stateName(current),
                           (unsigned long)remaining_ms,
                           stateName(timerFinalState(pin)));
      } else {
        const uint32_t remaining_seconds = (remaining_ms + 999UL) / 1000UL;
        written = snprintf(reply + used, reply_size - used, "%s%u=%s(%lus->%s)",
                           any ? "," : " ", pin, stateName(current),
                           (unsigned long)remaining_seconds,
                           stateName(timerFinalState(pin)));
      }
    }
    if (written < 0) break;
    used += (size_t)written;
    any = true;
  }
  if (!any) snprintf(reply, reply_size, "> active GPIOs: none");
}

void UserGpio::handleGet(const char* args, char* reply, size_t reply_size) {
  loop();
  args = skipSpaces(args);

  if (*args == '\0') {
    formatAvailablePins(reply, reply_size);
    return;
  }

  const char* token_end = args;
  while (*token_end != '\0' && *token_end != ' ') token_end++;
  if (tokenEquals(args, (size_t)(token_end - args), "available") && atEnd(token_end)) {
    formatAvailablePins(reply, reply_size);
    return;
  }

  const size_t token_length = (size_t)(token_end - args);
  const bool is_state_query = tokenEquals(args, token_length, "state") ||
      tokenEquals(args, token_length, "states") ||
      tokenEquals(args, token_length, "status");
  if (is_state_query) {
    const char* state_args = skipSpaces(token_end);
    if (*state_args == '\0') {
      formatActivePins(reply, reply_size);
      return;
    }

    uint32_t state_pin;
    if (!parseUnsigned(state_args, state_pin) || !atEnd(state_args)) {
      snprintf(reply, reply_size, "Error: use get gpio state [pin]");
      return;
    }
    if (!isAvailable(state_pin)) {
      snprintf(reply, reply_size, "Error: GPIO %lu is unavailable",
               (unsigned long)state_pin);
      return;
    }
    formatPinStatus((uint8_t)state_pin, "> ", reply, reply_size);
    return;
  }

  uint32_t parsed_pin;
  if (!parseUnsigned(args, parsed_pin) || !atEnd(args)) {
    snprintf(reply, reply_size, "Error: use get gpio [state|states|status [pin]|pin]");
    return;
  }
  if (!isAvailable(parsed_pin)) {
    snprintf(reply, reply_size, "Error: GPIO %lu is unavailable", (unsigned long)parsed_pin);
    return;
  }

  formatPinStatus((uint8_t)parsed_pin, "> ", reply, reply_size);
}

UserGpio::SetResult UserGpio::handleSet(const char* args, char* reply,
                                        size_t reply_size, uint32_t request_id,
                                        uint32_t request_source) {
  SetResult result = {SetResult::ERROR, 0, false};
  loop();

  uint32_t parsed_pin;
  State initial_state;
  if (!parseUnsigned(args, parsed_pin) || !parseState(args, initial_state)) {
    snprintf(reply, reply_size, "Error: use set gpio <pin> on|off|reset [duration on|off|reset]");
    return result;
  }
  if (!isAvailable(parsed_pin)) {
    snprintf(reply, reply_size, "Error: GPIO %lu is unavailable", (unsigned long)parsed_pin);
    return result;
  }

  const uint8_t pin = (uint8_t)parsed_pin;
  result.pin = pin;
  args = skipSpaces(args);
  if (*args == '\0') {
    result.cancelled_timer = (_timer_mask & pinMask(pin)) != 0;
    cancelTimer(pin);
    writeState(pin, initial_state);
    snprintf(reply, reply_size, "OK - GPIO %u %s", pin, stateName(initial_state));
    result.outcome = SetResult::APPLIED;
    return result;
  }

  uint32_t duration_ms;
  bool display_milliseconds;
  State final_state;
  if (initial_state == STATE_RESET || !parseDuration(args, duration_ms, display_milliseconds) ||
      !parseState(args, final_state) || !atEnd(args)) {
    snprintf(reply, reply_size, "Error: use set gpio <pin> on|off [duration on|off|reset]");
    return result;
  }
  if (duration_ms == 0 || duration_ms > MAX_TIMER_MILLIS) {
    snprintf(reply, reply_size, "Error: timer must be 1ms-24h");
    return result;
  }

  const uint32_t command_spec = duration_ms
      | ((uint32_t)(initial_state == STATE_ON) << 27)
      | ((uint32_t)final_state << 28)
      | ((uint32_t)display_milliseconds << 30);
  if (isDuplicateTimedRequest(pin, request_id, request_source, command_spec)) {
    formatPinStatus(pin, "OK - duplicate ignored; ", reply, reply_size);
    result.outcome = SetResult::DUPLICATE_IGNORED;
    return result;
  }

  result.cancelled_timer = (_timer_mask & pinMask(pin)) != 0;
  cancelTimer(pin);
  writeState(pin, initial_state);
  scheduleTimer(pin, duration_ms, display_milliseconds, final_state, request_id);
  rememberTimedRequest(pin, request_id, request_source, command_spec);
  if (display_milliseconds) {
    snprintf(reply, reply_size, "OK - GPIO %u %s for %lums, then %s", pin,
             stateName(initial_state), (unsigned long)duration_ms,
             stateName(final_state));
  } else {
    snprintf(reply, reply_size, "OK - GPIO %u %s for %lus, then %s", pin,
             stateName(initial_state), (unsigned long)(duration_ms / 1000UL),
             stateName(final_state));
  }
  result.outcome = SetResult::TIMER_STARTED;
  return result;
}
