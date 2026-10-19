#include <cassert>
#include <MeshCore.h>
#include <Dispatcher.h>
using mesh::power::RegulatorMode;

#if MESH_REGULATOR_FIXED_SUPPLY && !defined(REQUIRE_VIRTUAL_REGULATOR_API)
#define REGULATOR_OVERRIDE
#else
#define REGULATOR_OVERRIDE override
#endif

struct BoardDefaults : mesh::MainBoard {
  uint16_t getBattMilliVolts() override { return 0; }
  const char* getManufacturerName() const override { return "test"; }
  uint8_t getStartupReason() const override { return 0; }
  void reboot() override {}
  void sleep(uint32_t) override {} // The host must not assemble the MCU's WFI.
};
struct BoardSelection : BoardDefaults {
  uint8_t mcuRegulatorModes() const REGULATOR_OVERRIDE { return 3; }
  RegulatorMode defaultMcuRegulatorMode() const REGULATOR_OVERRIDE { return RegulatorMode::Dcdc; }
  bool applyMcuRegulator(RegulatorMode) REGULATOR_OVERRIDE { return true; }
  RegulatorMode activeMcuRegulatorMode() const REGULATOR_OVERRIDE { return RegulatorMode::Ldo; }
};
struct RadioDefaults : mesh::Radio {
  int recvRaw(uint8_t*, int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 0; }
  float packetScore(float, int) override { return 0; }
  bool startSendRaw(const uint8_t*, int) override { return false; }
  bool isSendComplete() override { return true; }
  void onSendFinished() override {}
  bool isInRecvMode() const override { return false; }
};
struct RadioSelection : RadioDefaults {
  uint8_t regulatorModes() const REGULATOR_OVERRIDE { return 3; }
  RegulatorMode defaultRegulatorMode() const REGULATOR_OVERRIDE { return RegulatorMode::Dcdc; }
  bool configureBootRegulator(RegulatorMode) REGULATOR_OVERRIDE { return true; }
  RegulatorMode activeRegulatorMode() const REGULATOR_OVERRIDE { return RegulatorMode::Ldo; }
};

// Exercise the actual production base declarations through references. On
// fixed STM32WL the shadows cannot add a runtime capability; everywhere else
// all eight overrides must remain virtual and reachable through the API.
__attribute__((noinline)) void check(mesh::MainBoard& board, mesh::Radio& radio) {
  constexpr bool fixed = EXPECT_FIXED_REGULATOR_API;
  assert(board.mcuRegulatorModes() == (fixed ? 0 : 3));
  assert(radio.regulatorModes() == (fixed ? 0 : 3));
  assert(board.defaultMcuRegulatorMode() == (fixed ? RegulatorMode::Default : RegulatorMode::Dcdc));
  assert(radio.defaultRegulatorMode() == (fixed ? RegulatorMode::Default : RegulatorMode::Dcdc));
  assert(board.applyMcuRegulator(RegulatorMode::Dcdc) == !fixed);
  assert(radio.configureBootRegulator(RegulatorMode::Dcdc) == !fixed);
  assert(board.activeMcuRegulatorMode() == (fixed ? RegulatorMode::Default : RegulatorMode::Ldo));
  assert(radio.activeRegulatorMode() == (fixed ? RegulatorMode::Default : RegulatorMode::Ldo));
}
int main() {
  static_assert(MESH_REGULATOR_FIXED_SUPPLY == EXPECT_FIXED_REGULATOR_API,
                "the fixed supply guard must match this exact platform/driver");
  BoardSelection board;
  RadioSelection radio;
  assert(board.mcuRegulatorModes() == 3 && radio.regulatorModes() == 3);
  check(board, radio);
}
