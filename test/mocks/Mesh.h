#pragma once

#include <cstdint>
#include <RadioProfiles.h>

namespace mesh {

enum class RadioParamApplyResult : uint8_t {
  APPLIED,
  BUSY,
  FAILED
};

class Radio {
public:
  virtual ~Radio() = default;
  virtual RadioProfiles* profiles() { return nullptr; }
  virtual const RadioProfiles* profiles() const { return nullptr; }
  virtual bool validateProfile(const RadioProfileParams&) const { return false; }
  virtual uint8_t receiveProfile() const { return 0; }
  virtual uint32_t receiveProfileGeneration() const {
    const RadioProfiles* p = profiles();
    return p ? p->generation[receiveProfile()] : 0;
  }
  virtual RadioParamApplyResult prepareTransmitProfile(uint8_t profile) {
    return profile == 0 ? RadioParamApplyResult::APPLIED : RadioParamApplyResult::FAILED;
  }
  virtual RadioParamApplyResult trySetPrimaryParams(const RadioProfileParams&,
      bool, const uint32_t* = nullptr) {
    return RadioParamApplyResult::FAILED;
  }
  virtual bool isReceiving() { return false; }
  virtual uint32_t getEstAirtimeFor(uint16_t) { return 10; }
  virtual uint32_t getProfileAirtime(uint8_t, int len, uint8_t = 0) {
    return getEstAirtimeFor((uint16_t)len);
  }
  virtual bool startSendRaw(const uint8_t*, uint16_t) { return true; }
  virtual bool isSendComplete() { return true; }
  virtual void onSendFinished() {}
  virtual int16_t getNoiseFloor() { return -120; }
};

class MainBoard {
public:
  virtual ~MainBoard() = default;
  virtual uint16_t getBattMilliVolts() { return 4200; }
  virtual float getMCUTemperature() { return 25.0f; }
  virtual const char* getManufacturerName() { return "mock-board"; }
  virtual void reboot() {}
};

}
