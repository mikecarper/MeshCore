#pragma once
#include <helpers/IdentityStore.h>
#include "RegulatorMode.h"

namespace mesh {
class MainBoard;
class Radio;
namespace power {
// Called once after the primary filesystem and board ADC are ready, before
// display/radio/wireless initialization. Choices never alter an active radio.
bool beginRegulatorSettings(MainBoard& board, Radio& radio, FILESYSTEM* fs);
void serviceRegulatorSettings();
bool handleRegulatorCommand(const char* command, char* reply, size_t capacity);
const char* regulatorModeName(RegulatorMode mode);
uint8_t configuredRegulatorModes(bool mcu);
RegulatorMode requestedRegulatorMode(bool mcu);
RegulatorMode activeRegulatorMode(bool mcu);
const char* activeRegulatorModeName(bool mcu);
bool regulatorFallbackActive(bool mcu);
bool regulatorRecoveryAvailable();
}}

extern "C" void meshRegulatorCleanReset();
