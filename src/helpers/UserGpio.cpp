#include "UserGpio.h"

#include <Arduino.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

namespace {

// Keep deadlines within the signed half of the millis() range so wrap-safe
// comparisons remain unambiguous. This is about 24.8 days.
const uint32_t MAX_TIMER_SECONDS = 2147483UL;

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
      _timer_final_reset_mask(0) {
  memset(_timer_deadline, 0, sizeof(_timer_deadline));
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
  _timer_deadline[pin] = 0;
}

void UserGpio::scheduleTimer(uint8_t pin, uint32_t seconds, State final_state) {
  const uint64_t mask = pinMask(pin);
  _timer_deadline[pin] = millis() + seconds * 1000UL;
  _timer_mask |= mask;

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

void UserGpio::loop() {
  if (_timer_mask == 0) return;

  const uint32_t now = millis();
  for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
    const uint64_t mask = pinMask(pin);
    if ((_timer_mask & mask) == 0) continue;
    if ((int32_t)(now - _timer_deadline[pin]) < 0) continue;

    const State final_state = timerFinalState(pin);
    cancelTimer(pin);
    writeState(pin, final_state);
  }
}

void UserGpio::handleGet(const char* args, char* reply, size_t reply_size) {
  loop();
  args = skipSpaces(args);

  if (*args == '\0') {
    size_t used = (size_t)snprintf(reply, reply_size, "> available GPIOs:");
    bool any = false;
    for (uint8_t pin = 0; pin <= MAX_GPIO_PIN; pin++) {
      if (!_board->isUserGpioAvailable(pin)) continue;
      if (used >= reply_size) break;
      const int written = snprintf(reply + used, reply_size - used, "%s%u", any ? "," : " ", pin);
      if (written < 0) break;
      used += (size_t)written;
      any = true;
    }
    if (!any) snprintf(reply, reply_size, "> available GPIOs: none");
    return;
  }

  uint32_t parsed_pin;
  if (!parseUnsigned(args, parsed_pin) || !atEnd(args)) {
    snprintf(reply, reply_size, "Error: use get gpio [pin]");
    return;
  }
  if (!isAvailable(parsed_pin)) {
    snprintf(reply, reply_size, "Error: GPIO %lu is unavailable", (unsigned long)parsed_pin);
    return;
  }

  const uint8_t pin = (uint8_t)parsed_pin;
  const State current = currentState(pin);
  const uint64_t mask = pinMask(pin);
  if ((_timer_mask & mask) == 0) {
    snprintf(reply, reply_size, "> GPIO %u %s", pin, stateName(current));
    return;
  }

  const uint32_t remaining_ms = _timer_deadline[pin] - millis();
  const uint32_t remaining_seconds = (remaining_ms + 999UL) / 1000UL;
  snprintf(reply, reply_size, "> GPIO %u %s, %lus -> %s", pin, stateName(current),
           (unsigned long)remaining_seconds, stateName(timerFinalState(pin)));
}

void UserGpio::handleSet(const char* args, char* reply, size_t reply_size) {
  loop();

  uint32_t parsed_pin;
  State initial_state;
  if (!parseUnsigned(args, parsed_pin) || !parseState(args, initial_state)) {
    snprintf(reply, reply_size, "Error: use set gpio <pin> on|off|reset [seconds on|off|reset]");
    return;
  }
  if (!isAvailable(parsed_pin)) {
    snprintf(reply, reply_size, "Error: GPIO %lu is unavailable", (unsigned long)parsed_pin);
    return;
  }

  const uint8_t pin = (uint8_t)parsed_pin;
  args = skipSpaces(args);
  if (*args == '\0') {
    cancelTimer(pin);
    writeState(pin, initial_state);
    snprintf(reply, reply_size, "OK - GPIO %u %s", pin, stateName(initial_state));
    return;
  }

  uint32_t seconds;
  State final_state;
  if (initial_state == STATE_RESET || !parseUnsigned(args, seconds) ||
      !parseState(args, final_state) || !atEnd(args)) {
    snprintf(reply, reply_size, "Error: use set gpio <pin> on|off [seconds on|off|reset]");
    return;
  }
  if (seconds == 0 || seconds > MAX_TIMER_SECONDS) {
    snprintf(reply, reply_size, "Error: timer must be 1-%lu seconds", (unsigned long)MAX_TIMER_SECONDS);
    return;
  }

  cancelTimer(pin);
  writeState(pin, initial_state);
  scheduleTimer(pin, seconds, final_state);
  snprintf(reply, reply_size, "OK - GPIO %u %s for %lus, then %s", pin,
           stateName(initial_state), (unsigned long)seconds, stateName(final_state));
}
