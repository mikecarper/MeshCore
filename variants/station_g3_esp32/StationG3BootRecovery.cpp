#include "StationG3BootRecovery.h"

#include "StationG3Board.h"
#include <helpers/UsbLogging.h>

#include <cstring>

namespace station_g3 {
namespace {

constexpr size_t kRxBudget = 64;
constexpr size_t kLineCapacity = 64;
constexpr size_t kReplyCapacity = 256;
constexpr char kPrefix[] = "!g3 ";
constexpr char kNotice[] =
    "\r\nStation G3 radio unavailable; USB recovery is active.\r\n"
    "Press Enter, then send !g3 help (no erase required).\r\n";
constexpr char kHelp[] =
    "!g3 get radio.slot\r\n"
    "!g3 set radio.slot auto|1|2\r\n"
    "!g3 reboot\r\n"
    "Slot changes are saved for the next boot. Recovery does not erase settings.\r\n";

struct RecoveryState {
  char line[kLineCapacity] = {};
  size_t length = 0;
  bool discardLine = false;
  bool kissQuarantine = false;
  bool immediatelyAfterFend = false;
  char output[kReplyCapacity] = {};
  size_t outputLength = 0;
  size_t outputOffset = 0;

  void clearInput() {
    length = 0;
    discardLine = false;
    kissQuarantine = false;
    immediatelyAfterFend = false;
  }

  void queue(const char* text) {
    size_t count = 0;
    while (count + 1 < sizeof(output) && text[count]) ++count;
    memcpy(output, text, count);
    output[count] = 0;
    outputLength = count;
    outputOffset = 0;
  }

  bool flushOnce(Stream& port) {
    if (outputOffset < outputLength) {
      const size_t remaining = outputLength - outputOffset;
      const size_t sent = port.write(
          reinterpret_cast<const uint8_t*>(output + outputOffset), remaining);
      outputOffset += sent < remaining ? sent : remaining;
    }
    return outputOffset == outputLength;
  }
};

RecoveryState state;

void dispatch(StationG3Board& board) {
  state.line[state.length] = 0;
  if (strncmp(state.line, kPrefix, sizeof(kPrefix) - 1) != 0) return;
  const char* command = state.line + sizeof(kPrefix) - 1;

  if (strcmp(command, "help") == 0) {
    state.queue(kHelp);
    return;
  }
  if (strcmp(command, "reboot") == 0) {
    board.reboot();
    return;
  }
  const bool allowed = strcmp(command, "get radio.slot") == 0
      || strcmp(command, "set radio.slot auto") == 0
      || strcmp(command, "set radio.slot 1") == 0
      || strcmp(command, "set radio.slot 2") == 0;
  if (!allowed) {
    state.queue("Error: recovery accepts only radio.slot, help and reboot.\r\n");
    return;
  }

  char reply[kReplyCapacity] = {};
  if (!board.handleCommand(command, 0, reply)) {
    state.queue("Error: radio.slot recovery unavailable.\r\n");
    return;
  }
  // The board owns persistence and its failure reply. Never turn a failed save
  // into success or reboot automatically before the operator has seen it.
  reply[sizeof(reply) - 3] = 0;
  const size_t length = strlen(reply);
  memcpy(reply + length, "\r\n", 3);
  state.queue(reply);
}

}  // namespace

void serviceBootRecovery(StationG3Board& board, bool firstNotice) {
  if (firstNotice) {
    state.clearInput();
    state.queue(kNotice);
  }

  mesh::serviceUsbLoggingPort();
  mesh::serviceUsbTerminalPort();
  if (mesh::takeUsbTerminalSessionReset()) {
    // An unfinished command must not be completed by a different USB session.
    state.clearInput();
    state.queue(kNotice);
  }
  if (!mesh::tryCompleteUsbTerminalSessionReset()) return;

  Stream& port = mesh::usbConsolePort();
  if (!state.flushOnce(port)) return;

  for (size_t count = 0; count < kRxBudget && port.available() > 0; ++count) {
    const int input = port.read();
    if (input < 0) break;
    const uint8_t byte = static_cast<uint8_t>(input);

    if (byte == 0xc0) {
      // KISS can contain arbitrary newlines and even recovery-looking text.
      // Ignore everything through a FEND followed directly by the operator's
      // Enter. This also tolerates adjacent frames sharing one FEND delimiter.
      state.length = 0;
      state.discardLine = false;
      state.kissQuarantine = true;
      state.immediatelyAfterFend = true;
      continue;
    }
    if (state.kissQuarantine) {
      if ((byte == '\r' || byte == '\n') && state.immediatelyAfterFend) {
        state.clearInput();
      } else {
        state.immediatelyAfterFend = false;
      }
      continue;
    }
    if (byte == '\r' || byte == '\n') {
      const bool complete = !state.discardLine && state.length != 0;
      if (complete) dispatch(board);
      state.length = 0;
      state.discardLine = false;
      if (complete) {
        (void)state.flushOnce(port);
        return;  // At most one command per call, including rejected commands.
      }
      continue;
    }
    if (byte < 0x20 || byte > 0x7e) {
      state.discardLine = true;
      state.length = 0;
      continue;
    }
    if (state.discardLine) continue;
    if (state.length + 1 >= sizeof(state.line)) {
      state.discardLine = true;
      state.length = 0;
      continue;
    }
    state.line[state.length++] = static_cast<char>(byte);
  }
}

}  // namespace station_g3
