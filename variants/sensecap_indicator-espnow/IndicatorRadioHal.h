#pragma once

#include <Arduino.h>
#include <SPI.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <hal/Arduino/ArduinoHal.h>

// RadioLib HAL for the Indicator's SX1262. Four radio signals are routed
// through a TCA9535 instead of ordinary ESP32 GPIOs. The expander's shared,
// active-low interrupt output is polled from normal task context so I2C is
// never touched by an ISR.
class IndicatorRadioHal : public ArduinoHal {
public:
  explicit IndicatorRadioHal(SPIClass& spi);

  bool beginExpander();
  void serviceInterrupt();

  void pinMode(uint32_t pin, uint32_t mode) override;
  void digitalWrite(uint32_t pin, uint32_t value) override;
  uint32_t digitalRead(uint32_t pin) override;
  void attachInterrupt(uint32_t interrupt_num, void (*callback)(void),
                       uint32_t mode) override;
  void detachInterrupt(uint32_t interrupt_num) override;
  uint32_t pinToInterrupt(uint32_t pin) override;

private:
  static constexpr uint8_t EXPANDER_ADDRESS = 0x20;
  static constexpr uint8_t I2C_PORT = 0;
  static constexpr uint32_t I2C_FREQUENCY = 400000;
  static constexpr uint32_t EXPANDER_FLAG = 0x40;
  static constexpr uint8_t INPUT_REGISTER = 0x00;
  static constexpr uint8_t OUTPUT_REGISTER = 0x02;
  static constexpr uint8_t CONFIG_REGISTER = 0x06;
  static constexpr uint8_t RADIO_IRQ_INDEX = 3;
  static constexpr uint8_t EXPANDER_INTERRUPT_GPIO = 42;

  bool _expanderReady = false;
  uint16_t _output = 0xFFFF;
  uint16_t _config = 0xFFFF;
  bool _irqLevel = false;
  void (*_irqCallback)(void) = nullptr;
  uint32_t _irqMode = RISING;

  static bool isExpanderPin(uint32_t pin);
  static uint8_t expanderIndex(uint32_t pin);
  bool readRegister(uint8_t reg, uint16_t& value);
  bool writeRegister(uint8_t reg, uint16_t value);
  bool readInputs(uint16_t& value);
  void processInterruptLevel(uint16_t inputs);
};
