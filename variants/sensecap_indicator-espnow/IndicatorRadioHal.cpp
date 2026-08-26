#include "IndicatorRadioHal.h"

#ifdef SENSECAP_INDICATOR_LORA

IndicatorRadioHal::IndicatorRadioHal(SPIClass& spi) : ArduinoHal(spi) {}

bool IndicatorRadioHal::isExpanderPin(uint32_t pin) {
  return pin != RADIOLIB_NC && (pin & EXPANDER_FLAG) != 0;
}

uint8_t IndicatorRadioHal::expanderIndex(uint32_t pin) {
  return static_cast<uint8_t>(pin & ~EXPANDER_FLAG);
}

bool IndicatorRadioHal::readRegister(uint8_t reg, uint16_t& value) {
  uint8_t data[2];
  if (!lgfx::i2c::readRegister(I2C_PORT, EXPANDER_ADDRESS, reg, data,
                               sizeof(data), I2C_FREQUENCY)
           .has_value()) {
    return false;
  }
  value = data[0];
  value |= static_cast<uint16_t>(data[1]) << 8;
  return true;
}

bool IndicatorRadioHal::writeRegister(uint8_t reg, uint16_t value) {
  uint8_t data[] = {
      reg,
      static_cast<uint8_t>(value),
      static_cast<uint8_t>(value >> 8),
  };
  return lgfx::i2c::transactionWrite(I2C_PORT, EXPANDER_ADDRESS, data,
                                     sizeof(data), I2C_FREQUENCY)
      .has_value();
}

bool IndicatorRadioHal::beginExpander() {
  if (_expanderReady) return true;

  uint16_t inputs;
  if (!readRegister(OUTPUT_REGISTER, _output)
      || !readRegister(CONFIG_REGISTER, _config)
      || !readRegister(INPUT_REGISTER, inputs)) {
    // The display normally initializes this shared bus first. Reinitialize it
    // here as a fallback when display or touch probing ended early.
    if (!lgfx::i2c::init(I2C_PORT, PIN_BOARD_SDA, PIN_BOARD_SCL).has_value()
        || !readRegister(OUTPUT_REGISTER, _output)
        || !readRegister(CONFIG_REGISTER, _config)
        || !readRegister(INPUT_REGISTER, inputs)) {
      return false;
    }
  }

  ::pinMode(EXPANDER_INTERRUPT_GPIO, INPUT_PULLUP);
  _irqLevel = (inputs & (1U << RADIO_IRQ_INDEX)) != 0;
  _expanderReady = true;
  return true;
}

bool IndicatorRadioHal::readInputs(uint16_t& value) {
  if (!beginExpander() || !readRegister(INPUT_REGISTER, value)) return false;
  processInterruptLevel(value);
  return true;
}

void IndicatorRadioHal::processInterruptLevel(uint16_t inputs) {
  bool level = (inputs & (1U << RADIO_IRQ_INDEX)) != 0;
  if (level == _irqLevel) return;

  _irqLevel = level;
  if (_irqCallback == nullptr) return;
  if ((_irqMode == GpioInterruptRising && level)
      || (_irqMode == GpioInterruptFalling && !level)) {
    _irqCallback();
  }
}

void IndicatorRadioHal::serviceInterrupt() {
  if (_irqCallback == nullptr || !_expanderReady
      || ::digitalRead(EXPANDER_INTERRUPT_GPIO) != LOW) {
    return;
  }

  uint16_t inputs;
  readInputs(inputs);
}

void IndicatorRadioHal::pinMode(uint32_t pin, uint32_t mode) {
  if (!isExpanderPin(pin)) {
    ArduinoHal::pinMode(pin, mode);
    return;
  }

  uint8_t index = expanderIndex(pin);
  if (index >= 16 || !beginExpander()) return;
  uint16_t next = _config;
  if (mode == GpioModeOutput) {
    next &= ~(1U << index);
  } else {
    next |= 1U << index;
  }
  if (next != _config && writeRegister(CONFIG_REGISTER, next)) _config = next;
}

void IndicatorRadioHal::digitalWrite(uint32_t pin, uint32_t value) {
  if (!isExpanderPin(pin)) {
    ArduinoHal::digitalWrite(pin, value);
    return;
  }

  uint8_t index = expanderIndex(pin);
  if (index >= 16 || !beginExpander()) return;
  uint16_t next = value == GpioLevelHigh ? _output | (1U << index)
                                         : _output & ~(1U << index);
  if (next != _output && writeRegister(OUTPUT_REGISTER, next)) _output = next;
}

uint32_t IndicatorRadioHal::digitalRead(uint32_t pin) {
  if (!isExpanderPin(pin)) return ArduinoHal::digitalRead(pin);

  uint8_t index = expanderIndex(pin);
  uint16_t inputs;
  if (index >= 16 || !readInputs(inputs)) {
    // A failed BUSY read must prevent an unsafe SPI command. Other failed
    // reads are treated as inactive.
    return pin == P_LORA_BUSY ? GpioLevelHigh : GpioLevelLow;
  }
  return (inputs & (1U << index)) != 0 ? GpioLevelHigh : GpioLevelLow;
}

void IndicatorRadioHal::attachInterrupt(uint32_t interrupt_num,
                                        void (*callback)(void), uint32_t mode) {
  if (!isExpanderPin(interrupt_num)) {
    ArduinoHal::attachInterrupt(interrupt_num, callback, mode);
    return;
  }

  if (expanderIndex(interrupt_num) != RADIO_IRQ_INDEX
      || !beginExpander()) {
    return;
  }

  _irqCallback = callback;
  _irqMode = mode;
  uint16_t inputs;
  if (readRegister(INPUT_REGISTER, inputs)) {
    _irqLevel = (inputs & (1U << RADIO_IRQ_INDEX)) != 0;
  }
}

void IndicatorRadioHal::detachInterrupt(uint32_t interrupt_num) {
  if (!isExpanderPin(interrupt_num)) {
    ArduinoHal::detachInterrupt(interrupt_num);
    return;
  }
  if (expanderIndex(interrupt_num) == RADIO_IRQ_INDEX) {
    _irqCallback = nullptr;
  }
}

uint32_t IndicatorRadioHal::pinToInterrupt(uint32_t pin) {
  return isExpanderPin(pin) ? pin : ArduinoHal::pinToInterrupt(pin);
}

#endif
