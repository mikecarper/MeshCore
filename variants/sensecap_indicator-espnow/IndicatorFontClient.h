#pragma once

#include <stddef.h>
#include <stdint.h>

class IndicatorFontClient {
public:
  static uint8_t* load(size_t& size);
};
