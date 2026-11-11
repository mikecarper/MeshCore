#pragma once
#include <stdint.h>

// Only diagnostic builds retain this snapshot. The phase identifies the last
// GetErrors read, not a later SPI command which may have failed afterward.
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
namespace mesh { namespace lr2021 {
struct CalibrationDeviceStatus {
  enum Phase : uint8_t { None=0, Before=1, AfterBase=2, AfterFrontEnd=3 };
  uint16_t errors=0;
  uint8_t phase=None;
  bool valid=false;
  void begin() { errors=0;phase=None;valid=false; }
  void record(Phase at, uint16_t word, int16_t result) {
    errors=word;phase=at;valid=result==0;
  }
};
inline CalibrationDeviceStatus& calibrationDeviceStatus() {
  static CalibrationDeviceStatus value;
  return value;
}
}} // namespace mesh::lr2021
#endif
