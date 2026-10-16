#pragma once

#include "WioTrackerL2Board.h"
#include <helpers/sensors/MicroNMEALocationProvider.h>

// The GNSS enable/reset pins are TCA9535 channels, not ESP32 GPIOs. Keep this
// adaptation local to the board while retaining the normal NMEA/GpsTap stream.
class WioTrackerL2LocationProvider : public MicroNMEALocationProvider {
  WioTrackerL2Board& _board;
  bool _powered = false;

  static void releaseUart() {
    Serial1.end();
    // The MCU's TX idle level must not feed an unpowered GNSS through its RX.
    pinMode(PIN_GPS_TX, INPUT);
    pinMode(PIN_GPS_RX, INPUT);
  }

public:
  WioTrackerL2LocationProvider(WioTrackerL2Board& board, Stream& stream,
                              mesh::RTCClock* clock)
      : MicroNMEALocationProvider(stream, clock, -1, -1), _board(board) {}

  void begin() override {
    if (_powered) return;
    // The manager starts Serial1 before provider begin(). Release its pads
    // until the board-specific power/reset sequence has made GNSS ready.
    releaseUart();
    if (!_board.expanderOK()) return;
    _board.setGnssPower(true);
    Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);
#ifdef GPS_BAUD_RATE
    Serial1.begin(GPS_BAUD_RATE);
#else
    Serial1.begin(9600);
#endif
    MicroNMEALocationProvider::begin();
    clearPositionFix();
    _powered = true;
  }

  void stop() override {
    if (!_powered) return;
    MicroNMEALocationProvider::stop();
    releaseUart();
    _board.setGnssPower(false);
    _powered = false;
  }

  bool isEnabled() override { return _powered; }
};
