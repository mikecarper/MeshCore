#include "ThinkNodeM7Board.h"

void ThinkNodeM7Board::begin() {
  ESP32Board::begin();
}

const char* ThinkNodeM7Board::getManufacturerName() const {
  return "Elecrow ThinkNode M7";
}
