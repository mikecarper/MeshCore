#pragma once

class StationG3Board;

namespace station_g3 {

// Call only while radio initialization has failed, with primary USB already
// started. Every command needs the literal "!g3 " prefix. This is deliberately
// not a replacement for the working role's CLI or binary KISS protocol.
// firstNotice starts a new recovery interval and forgets any partial input.
// Each call performs bounded, zero-wait USB work; the retry loop still owns
// watchdog servicing, yield/delay and the next attempt to initialize the radio.
void serviceBootRecovery(StationG3Board& board, bool firstNotice);

}  // namespace station_g3
