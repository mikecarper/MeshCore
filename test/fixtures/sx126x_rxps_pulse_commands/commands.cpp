#include <helpers/radiolib/SX126xRxpsPulse.h>

#include <cassert>
#include <climits>
#include <cstring>
#include <iostream>
#include <map>
#include <string>
#include <vector>

namespace pulse = mesh::rxpspulse;

struct Call {
  bool read;
  std::vector<uint8_t> command;
  std::vector<uint8_t> data;
  size_t length;
};

// This owner intentionally offers no legacy readRegister/standby API, pin
// writes, FIFO, calibration or cache hooks. The real helper must use only the
// checked streams and leave ownership/context decisions to its caller.
struct FakeModule {
  std::vector<Call> calls;
  std::map<uint16_t, uint8_t> registers;
  uint16_t irq = 0x1255;
  size_t fail_at = SIZE_MAX;
  int16_t failure = RADIOLIB_ERR_SPI_CMD_TIMEOUT;

  FakeModule() {
    registers[0x029F] = 0x01;
    registers[0x02A0] = 0x08;
    registers[0x02A1] = 0xAC;
    registers[0x08AC] = 0x96;
    registers[0x08B5] = 0xA5;
    registers[0x0902] = 0x7F;
    registers[0x0944] = 0xA5;
  }

  int16_t SPIreadStream(uint16_t command, uint8_t* data, size_t length,
                        bool wait, bool verify) {
    assert(command <= 0xFF);
    const uint8_t bytes[] = {uint8_t(command)};
    return SPIreadStream(bytes, sizeof(bytes), data, length, wait, verify);
  }

  int16_t SPIreadStream(const uint8_t* command, uint8_t command_length,
                        uint8_t* data, size_t length, bool wait, bool verify) {
    assert(wait && !verify);
    const size_t index = calls.size();
    calls.push_back(Call{true, std::vector<uint8_t>(command, command + command_length),
                         {}, length});
    // Real transport can copy bytes even when it reports a failure. Poisoning
    // them exercises the helper's validity gate instead of assuming no payload.
    if (index == fail_at) {
      std::memset(data, 0, length);
      return failure;
    }
    if (command_length == 1 && command[0] == 0x12) {
      assert(length == 2);
      data[0] = uint8_t(irq >> 8);
      data[1] = uint8_t(irq);
    } else {
      assert(command_length == 3 && command[0] == 0x1D);
      assert(length == 1);
      const uint16_t address = (uint16_t(command[1]) << 8) | command[2];
      data[0] = registers.at(address);
    }
    return RADIOLIB_ERR_NONE;
  }

  int16_t SPIwriteStream(uint16_t command, const uint8_t* data, size_t length,
                         bool wait, bool verify) {
    assert(command <= 0xFF);
    const uint8_t bytes[] = {uint8_t(command)};
    return SPIwriteStream(bytes, sizeof(bytes), data, length, wait, verify);
  }

  int16_t SPIwriteStream(const uint8_t* command, uint8_t command_length,
                         const uint8_t* data, size_t length, bool wait, bool verify) {
    assert(wait && !verify);
    const size_t index = calls.size();
    calls.push_back(Call{false, std::vector<uint8_t>(command, command + command_length),
                         std::vector<uint8_t>(data, data + length), length});
    if (index == fail_at) return failure;
    if (command_length == 3 && command[0] == 0x0D) {
      assert(length == 1);
      const uint16_t address = (uint16_t(command[1]) << 8) | command[2];
      registers[address] = data[0];
    }
    return RADIOLIB_ERR_NONE;
  }
};

static void success(pulse::Result result) {
  assert(result.ok());
  assert(result.error == RADIOLIB_ERR_NONE);
  assert(result.step == pulse::Step::None);
}

static void error(pulse::Result result, int16_t value, pulse::Step step) {
  assert(!result.ok());
  assert(result.error == value);
  assert(result.step == step);
}

static pulse::Periods periods(uint32_t rx, uint32_t sleep) {
  pulse::Periods value;
  value.rxTicks = rx;
  value.sleepTicks = sleep;
  return value;
}

static void call(const Call& actual, bool read, std::vector<uint8_t> command,
                 std::vector<uint8_t> data, size_t length) {
  assert(actual.read == read);
  assert(actual.command == command);
  assert(actual.data == data);
  assert(actual.length == length);
}

static void labPeriods() {
  pulse::Periods value;
  for (uint32_t sleep : {6554UL, 4000UL, 3000UL}) {
    success(pulse::encodePeriods(117844, sleep, 2600, value));
    assert(value.rxTicks == 7542);
    assert(value.sleepTicks == (sleep == 6554 ? 253U : sleep == 4000 ? 89U : 25U));
  }
  // Caller provides the resolved transition, not an assumed fixed TCXO value.
  success(pulse::encodePeriods(117844, 4000, 1000, value));
  assert(value.sleepTicks == 192);
}

static void periodBoundaries() {
  pulse::Periods value = periods(42, 43);
  error(pulse::encodePeriods(15, 2616, 2600, value),
        RADIOLIB_ERR_INVALID_RX_PERIOD, pulse::Step::ValidateRx);
  assert(value.rxTicks == 0 && value.sleepTicks == 0);
  success(pulse::encodePeriods(16, 2616, 2600, value));
  assert(value.rxTicks == 1 && value.sleepTicks == 1);
  error(pulse::encodePeriods(16, 2615, 2600, value),
        RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
  assert(value.rxTicks == 0 && value.sleepTicks == 0);
  success(pulse::encodePeriods(262143999, 262146599, 2600, value));
  assert(value.rxTicks == 0xFFFFFF && value.sleepTicks == 0xFFFFFF);
  error(pulse::encodePeriods(262144000, 2616, 2600, value),
        RADIOLIB_ERR_INVALID_RX_PERIOD, pulse::Step::ValidateRx);
  error(pulse::encodePeriods(16, 262146600, 2600, value),
        RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
}

static void periodOverflow() {
  pulse::Periods value;
  for (uint32_t sleep : {0UL, 1UL, 2599UL, 2600UL}) {
    error(pulse::encodePeriods(117844, sleep, 2600, value),
          RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
    assert(value.rxTicks == 0 && value.sleepTicks == 0);
  }
  error(pulse::encodePeriods(UINT32_MAX, 4000, 2600, value),
        RADIOLIB_ERR_INVALID_RX_PERIOD, pulse::Step::ValidateRx);
  error(pulse::encodePeriods(117844, UINT32_MAX, 2600, value),
        RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
  error(pulse::encodePeriods(117844, 4000, UINT32_MAX, value),
        RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
  error(pulse::encodePeriods(0, UINT32_MAX, UINT32_MAX, value),
        RADIOLIB_ERR_INVALID_RX_PERIOD, pulse::Step::ValidateRx);
  success(pulse::encodePeriods(16, UINT32_MAX, UINT32_MAX - 16, value));
  assert(value.rxTicks == 1 && value.sleepTicks == 1);
}

static void irq() {
  FakeModule module;
  uint16_t observation = 0xBEEF;
  success(pulse::readIrq(&module, observation));
  assert(observation == 0x1255 && module.irq == 0x1255);
  assert(module.calls.size() == 1);
  call(module.calls[0], true, {0x12}, {}, 2);
  for (int16_t failure : {-705, -706, -707, -999}) {
    FakeModule broken;
    broken.fail_at = 0;
    broken.failure = failure;
    observation = 0xBEEF;
    error(pulse::readIrq(&broken, observation), failure, pulse::Step::ReadIrq);
    assert(observation == 0xBEEF && broken.calls.size() == 1);
    assert(broken.irq == 0x1255);
  }
}

static void standby() {
  FakeModule module;
  success(pulse::standbyRc(&module));
  assert(module.calls.size() == 1);
  call(module.calls[0], false, {0x80}, {0}, 1);
  for (int16_t failure : {-705, -706, -707, -999}) {
    FakeModule broken;
    broken.fail_at = 0;
    broken.failure = failure;
    error(pulse::standbyRc(&broken), failure, pulse::Step::StandbyRc);
    assert(broken.calls.size() == 1);
    call(broken.calls[0], false, {0x80}, {0}, 1);
  }
}

static void rtcBits() {
  for (uint8_t before : {0x00, 0x02, 0xFF, 0xA5, 0x80, 0xFD}) {
    FakeModule module;
    module.registers[0x0944] = before;
    success(pulse::stopRtc(&module));
    assert(module.calls.size() == 3);
    call(module.calls[0], false, {0x0D, 0x09, 0x02}, {0}, 1);
    call(module.calls[1], true, {0x1D, 0x09, 0x44}, {}, 1);
    call(module.calls[2], false, {0x0D, 0x09, 0x44}, {uint8_t(before | 2)}, 1);
    assert(module.registers.at(0x0902) == 0);
    assert(module.registers.at(0x0944) == uint8_t(before | 2));
  }
}

static void rtcFailures() {
  const pulse::Step steps[] = {pulse::Step::StopRtc, pulse::Step::ReadRtcEvent,
                               pulse::Step::ClearRtcEvent};
  for (size_t index = 0; index < 3; ++index) {
    for (int16_t failure : {-705, -706, -707, -999}) {
      FakeModule module;
      module.fail_at = index;
      module.failure = failure;
      error(pulse::stopRtc(&module), failure, steps[index]);
      assert(module.calls.size() == index + 1);
      // In particular a failed read must not write a fabricated zero|2 event.
      assert(module.registers.at(0x0944) == 0xA5);
    }
  }
}

static void arm() {
  FakeModule module;
  pulse::Periods value;
  success(pulse::encodePeriods(117844, 4000, 2600, value));
  success(pulse::armDutyCycle(&module, value));
  assert(module.calls.size() == 1);
  call(module.calls[0], false, {0x94}, {0x00, 0x1D, 0x76, 0, 0, 0x59}, 6);
  FakeModule maximum;
  success(pulse::armDutyCycle(&maximum, periods(0xFFFFFF, 0xFFFFFF)));
  call(maximum.calls[0], false, {0x94}, {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF}, 6);
  for (int16_t failure : {-705, -706, -707, -999}) {
    FakeModule broken;
    broken.fail_at = 0;
    broken.failure = failure;
    error(pulse::armDutyCycle(&broken, value), failure, pulse::Step::ArmDutyCycle);
    assert(broken.calls.size() == 1);
  }
}

static void invalidArm() {
  FakeModule module;
  const uint32_t invalid_values[] = {0, 0x1000000, UINT32_MAX};
  for (uint32_t invalid : invalid_values) {
    error(pulse::armDutyCycle(&module, periods(invalid, 1)),
          RADIOLIB_ERR_INVALID_RX_PERIOD, pulse::Step::ValidateRx);
    error(pulse::armDutyCycle(&module, periods(1, invalid)),
          RADIOLIB_ERR_INVALID_SLEEP_PERIOD, pulse::Step::ValidateSleep);
  }
  assert(module.calls.empty());
}

static void sequence() {
  FakeModule module;
  const auto before = module.registers;
  uint16_t flags = 0;
  pulse::Periods value;
  success(pulse::encodePeriods(117844, 6554, 2600, value));
  success(pulse::readIrq(&module, flags));
  success(pulse::standbyRc(&module));
  // Ordinary guarded RX staging is the caller's responsibility. This helper
  // may send only the duty command after that staging has already succeeded.
  success(pulse::armDutyCycle(&module, value));
  success(pulse::readIrq(&module, flags));
  success(pulse::standbyRc(&module));
  success(pulse::stopRtc(&module));
  assert(module.calls.size() == 8);
  call(module.calls[0], true, {0x12}, {}, 2);
  call(module.calls[1], false, {0x80}, {0}, 1);
  call(module.calls[2], false, {0x94}, {0, 0x1D, 0x76, 0, 0, 0xFD}, 6);
  call(module.calls[3], true, {0x12}, {}, 2);
  call(module.calls[4], false, {0x80}, {0}, 1);
  call(module.calls[5], false, {0x0D, 0x09, 0x02}, {0}, 1);
  call(module.calls[6], true, {0x1D, 0x09, 0x44}, {}, 1);
  call(module.calls[7], false, {0x0D, 0x09, 0x44}, {0xA7}, 1);
  for (uint16_t address : {0x029F, 0x02A0, 0x02A1, 0x08AC, 0x08B5}) {
    assert(module.registers.at(address) == before.at(address));
  }
  for (const auto& item : module.calls) {
    assert(item.command[0] != 0x00);  // no wakeup NOP
    assert(item.command[0] != 0x84);  // no SetSleep
    assert(item.command[0] != 0x89);  // no calibration
    assert(item.command[0] != 0x98);  // no image calibration
  }
  assert(module.irq == 0x1255);  // no clear or FIFO ownership claim
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string name = argv[1];
  if (name == "lab_periods") labPeriods();
  else if (name == "period_boundaries") periodBoundaries();
  else if (name == "period_overflow") periodOverflow();
  else if (name == "irq") irq();
  else if (name == "standby") standby();
  else if (name == "rtc_bits") rtcBits();
  else if (name == "rtc_failures") rtcFailures();
  else if (name == "arm") arm();
  else if (name == "invalid_arm") invalidArm();
  else if (name == "sequence") sequence();
  else assert(false);
  std::cout << "PASS " << name << '\n';
}
