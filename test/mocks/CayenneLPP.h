#pragma once

#include <cstddef>
#include <cstdint>

class CayenneLPP {
public:
  explicit CayenneLPP(size_t) {}
  void addGPS(uint8_t, float latitude, float longitude, float altitude) {
    gps_count++;
    last_latitude = latitude;
    last_longitude = longitude;
    last_altitude = altitude;
  }
  const uint8_t* getBuffer() const { return nullptr; }
  uint16_t getSize() const { return 0; }

  uint16_t gps_count = 0;
  float last_latitude = 0;
  float last_longitude = 0;
  float last_altitude = 0;
};
