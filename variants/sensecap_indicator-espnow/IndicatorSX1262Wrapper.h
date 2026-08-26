#pragma once

#include <helpers/radiolib/CustomSX1262Wrapper.h>
#include "IndicatorRadioHal.h"

class IndicatorSX1262Wrapper : public CustomSX1262Wrapper {
public:
  IndicatorSX1262Wrapper(CustomSX1262& radio, mesh::MainBoard& board,
                         IndicatorRadioHal& hal)
      : CustomSX1262Wrapper(radio, board), _hal(hal) {}

  void loop() override {
    _hal.serviceInterrupt();
    CustomSX1262Wrapper::loop();
  }

private:
  IndicatorRadioHal& _hal;
};
