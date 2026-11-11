#pragma once

#include "CLICommandUtils.h"
#include "HardwareCommand.h"

namespace mesh {
namespace cli {

// These handlers only format retained state, preferences or the neighbour
// table. In particular they do not sample RSSI, probe a radio register, run
// ADC/sensor diagnostics, change GPIO or restart RX. Keep this list explicit:
// a new "get" command is not automatically safe during retained sleep.
inline bool maintenanceReadAllowed(const char* command) {
  if (!command) return false;
  while (*command == ' ' || *command == '\t') ++command;
  if (strlen(command) > 3 && command[2] == '|') {
    command += 3;
    while (*command == ' ' || *command == '\t') ++command;
  }
  hardwareCommand::Selector selector;
  if (hardwareCommand::parse(command, selector) == hardwareCommand::Parse::Valid)
    command = selector.command;
  static const char* const allowed[] = {
    "ver", "version", "neighbors", "stats-radio", "stats-radio-diag",
    "stats-packets", "memory", "get public.key", "get name", "get role",
    "get bootloader.ver", "get hardware",
    "get radio", "get freq", "get tx", "get af", "get dutycycle",
    "get rxdelay", "get txdelay", "get direct.txdelay", "get int.thresh",
    "get path.hash.mode", "get multi.acks", "get powersaving",
    "get radio.preamble", "get radio.mode", "get radio.fast",
    "get radio.fast.fs", "get radio.fast.warm", "get radio.fast.packet",
    "get radio.fast.buffer", "get radio.rxps", "get radio.rxps.config",
    "get radio.rxps.status", "get rxps.wd", "get agc", "get agc.reset.interval",
    "get agc.old", "get agc.status", "get agc.timing", "get agc.recovery",
    "get agc.pulse", "get agc.sleep", "get agc.checks", "get agc.scan",
    "get agc.old.status", "get agc.old.timing", "get agc.old.errors",
    "get agc.reset.status", "get agc.reset.timing", "get agc.reset.errors",
    "get agc.test.ms", "get agc.test.sleep_us"
  };
  for (const char* allowed_command : allowed) {
    if (strcmp(command, allowed_command) == 0) return true;
  }
  return false;
}

// A rejected command has not been queued or applied. Returning immediately
// lets the normal radio loop finish the lease; callers may retry afterwards.
inline bool deferForRadioMaintenance(bool pending, const char* command,
                                    char* reply, size_t capacity) {
  if (!pending || maintenanceReadAllowed(command)) return false;
  // Preserve the Companion request ID even when an early direct/terminal
  // entry point rejects a command before the ordinary envelope parser.
  if (command) {
    while (*command == ' ' || *command == '\t') ++command;
    if (strlen(command) > 3 && command[2] == '|' && capacity > 3) {
      memcpy(reply, command, 3);
      reply += 3;
      capacity -= 3;
    }
  }
  formatError(reply, capacity, "radio maintenance pending; retry shortly");
  return true;
}

} // namespace cli
} // namespace mesh
