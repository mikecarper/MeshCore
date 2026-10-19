#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
#include <helpers/IdentityStore.h>
#include <Dispatcher.h>
uint32_t mock_now = 0;
#include <helpers/RegulatorSettings.cpp>

int main() {
  using namespace mesh::power;
  mesh::MainBoard board;
  mesh::Radio radio;
  MockFS fs;
  assert(beginRegulatorSettings(board, radio, &fs));
  for (bool mcu : {false, true}) {
    assert(configuredRegulatorModes(mcu) == 0);
    assert(requestedRegulatorMode(mcu) == RegulatorMode::Default);
    assert(activeRegulatorMode(mcu) == RegulatorMode::Default);
    assert(strcmp(activeRegulatorModeName(mcu), "unknown") == 0);
    assert(!regulatorFallbackActive(mcu));
  }
  assert(!regulatorRecoveryAvailable());
  assert(strcmp(regulatorModeName(RegulatorMode::Default), "auto") == 0);
  assert(strcmp(regulatorModeName(RegulatorMode::Dcdc), "dcdc") == 0);
  assert(strcmp(regulatorModeName(RegulatorMode::Ldo), "ldo") == 0);
  for (const char* command : {"get mcu.regulator", "set mcu.regulator dcdc",
       "set mcu.regulator ldo", "set mcu.regulator auto", "get radio.regulator",
       "set radio.regulator dcdc", "set radio.regulator ldo", "set radio.regulator auto"}) {
    char reply[80];
    for (size_t capacity = 0; capacity <= sizeof(reply); ++capacity) {
      memset(reply, 0xa5, sizeof(reply));
      assert(!handleRegulatorCommand(command, reply, capacity));
      for (size_t i = 0; i < sizeof(reply); ++i) assert(uint8_t(reply[i]) == 0xa5);
    }
  }
  for (const char* command : {"get mcu.regulatorx", "set radio.regulators ldo", "get name", "help"}) {
    char reply[80];
    assert(!handleRegulatorCommand(command, reply, sizeof(reply)));
  }
  for (unsigned i = 0; i < 10; ++i) {
    mock_now += 30000;
    serviceRegulatorSettings();
    meshRegulatorCleanReset();
    meshRegulatorResumeRecovery();
  }
  assert(board.applies == 0 && board.sample_index == 0);
  assert(radio.configures == 0 && radio.inits == 0);
  assert(fs.files.empty() && fs.writes == 0 && fs.renames == 0);
  assert(mock_now == 300000); // No ADC sampling delays or other hardware work.
}
