#include <Arduino.h>
#include <variant.h>
// Actual RAK4631 variant.cpp maps Arduino pins 0..47 directly to P0/P1.
const uint32_t g_ADigitalPinMap[] = {
  0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,
  24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47
};
#include <helpers/UserGpio.h>
#include <helpers/PeripheralPinPolicy.h>
#include <helpers/RuntimePinClaims.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>

class NRF52Board : public mesh::MainBoard {
public:
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "RAK test"; }
  void reboot() override {}
  uint8_t getStartupReason() const override { return BD_STARTUP_NORMAL; }
  bool isUserGpioAvailable(uint8_t pin) const override;
  bool isPeripheralOutputAvailable(uint8_t pin) const override;
  uint16_t peripheralPinIdentity(uint8_t pin) const override;
};
@APPROVAL@
@OUTPUT@
@IDENTITY@

static unsigned scenarios = 0;
static void refused(NRF52Board& board, UserGpio& gpio, uint8_t pin) {
  assert(!board.isUserGpioAvailable(pin));
  for (const char* action : {"on", "off", "reset", "on 30 off", "off 1 reset"}) {
    uint8_t levels[64], modes[64];
    memcpy(levels, g_mock_pin_levels, sizeof(levels));
    memcpy(modes, g_mock_pin_modes, sizeof(modes));
    const unsigned writes = gpio_writes, directions = gpio_modes;
    char args[64], reply[160] = {};
    snprintf(args, sizeof(args), "%u %s", unsigned(pin), action);
    assert(gpio.handleSet(args, reply, sizeof(reply)).outcome == UserGpio::SetResult::ERROR);
    assert(!strncmp(reply, "Error:", 6));
    assert(gpio_writes == writes && gpio_modes == directions);
    assert(!memcmp(levels, g_mock_pin_levels, sizeof(levels)));
    assert(!memcmp(modes, g_mock_pin_modes, sizeof(modes)));
    assert(!gpio.hasActiveTimer());
    UserGpio::Completion completion;
    assert(!gpio.takeCompletion(completion));
    ++scenarios;
  }
}

static void available(NRF52Board& board, UserGpio& gpio, uint8_t pin) {
  assert(board.isUserGpioAvailable(pin));
  char args[64], reply[160] = {};
  for (const char* action : {"on", "off", "reset"}) {
    snprintf(args, sizeof(args), "%u %s", unsigned(pin), action);
    assert(gpio.handleSet(args, reply, sizeof(reply)).outcome == UserGpio::SetResult::APPLIED);
    assert(!strncmp(reply, "OK", 2));
    assert(g_mock_pin_modes[pin] == (!strcmp(action, "reset") ? INPUT : OUTPUT));
    if (strcmp(action, "reset"))
      assert(g_mock_pin_levels[pin] == (!strcmp(action, "on") ? HIGH : LOW));
    ++scenarios;
  }
}

int main() {
  NRF52Board board;
  UserGpio gpio(board);
  // Shared supply and existing SD bus claims remain protected independently
  // of the new NOR policy. Refusal must perform no pin writes or timer work.
  for (uint8_t pin : {WB_IO2, WB_IO4, WB_IO5}) refused(board, gpio, pin);
#if defined(OTA_RAK15002_SD_STORE)
  for (uint8_t pin : {3, 10, 26, 29, 30}) refused(board, gpio, pin);
#endif
#if defined(TEST_ARBITRARY)
  for (uint8_t pin : {OTA_QSPI_SCK_ARDUINO_PIN, OTA_QSPI_CS_ARDUINO_PIN,
                     OTA_QSPI_IO0_ARDUINO_PIN, OTA_QSPI_IO1_ARDUINO_PIN,
                     OTA_QSPI_IO2_ARDUINO_PIN, OTA_QSPI_IO3_ARDUINO_PIN}) {
#if defined(TEST_DISCONNECTED_OVERRIDES)
    if (pin == OTA_QSPI_IO2_ARDUINO_PIN || pin == OTA_QSPI_IO3_ARDUINO_PIN) {
      available(board, gpio, pin);
      continue;
    }
#endif
#if TEST_EXPECT_NOR
    refused(board, gpio, pin);
#else
    available(board, gpio, pin);
#endif
  }
  // No blanket GPIO31 ban: this override does not use it as a NOR pad.
  available(board, gpio, 31);
#elif TEST_EXPECT_NOR
  digitalWrite(31, HIGH); pinMode(31, OUTPUT); // Live NOR initially deselected.
  refused(board, gpio, 31);
  assert(g_mock_pin_levels[31] == HIGH && g_mock_pin_modes[31] == OUTPUT);
#else
  // The same valid board GPIO remains usable when no NOR backend is compiled.
  available(board, gpio, 31);
#endif
  available(board, gpio, 33); // Unrelated exposed GPIO is never removed.
  printf("%u RAK NOR GPIO ownership scenarios passed\n", scenarios);
}
