#pragma once

#include "EmergencyPowerConfig.h"

#if MESH_ENABLE_EMERGENCY_POWER
#include <Arduino.h>
#include "WirelessControl.h"

namespace mesh { namespace power {

class EmergencyPowerRuntime {
  bool radio_asleep_ = false;
  bool radio_was_available_ = false;
public:
  template <typename Mesh, typename Radio, typename Board, typename CLI>
  bool service(Mesh& mesh, Radio& radio, Board& board, CLI& cli,
               bool entry_busy, bool work_required = false) {
    const uint32_t budget = cli.emergencySleepMilliseconds();
    if (!board.supportsEmergencySleep()) return false;
    const bool active_operation = work_required || board.isOTAUpdateRunning()
        || board.isRadioTestActive() || radio.isMorseTxActive() || radio.isMorseRxActive();
    if (budget == 0 || active_operation) {
      if (radio_asleep_) {
        if (radio_was_available_) {
          board.setEmergencyRadioSleeping(false);
          if (!radio.setTrackerSleep(false)) radio.recoverRadio(true);
          mesh.setRadioAvailable(true);
        }
        radio_asleep_ = false;
        cli.setEmergencyRadioSleeping(false);
      }
      wireless::control().resumeAfterEmergency();
      return false;
    }
    if (!radio_asleep_) {
      // Complete live operations and command replies before taking ownership.
      // Queued future traffic cannot keep a critically low node awake forever.
      if (entry_busy || mesh.hasOutbound()) return false;
      if (!wireless::control().suspendForEmergency()) return false;
      radio_was_available_ = mesh.isRadioAvailable();
      if (radio_was_available_) {
        if (!radio.setTrackerSleep(true)) return false;
        mesh.setRadioAvailable(false);
        board.setEmergencyRadioSleeping(true);
      }
      radio_asleep_ = true;
      cli.setEmergencyRadioSleeping(true);
    }
    // Brief maintenance wakes retain voltage protection and watchdogs. Normal
    // radio/mesh/sensor/UI work remains suspended throughout the off window.
    if (!board.emergencySleepMilliseconds(budget > 10000 ? 10000 : budget)) {
      // A transient SDK/driver refusal must not turn battery protection into
      // a busy loop. Keep RF ownership and retry after yielding the CPU.
      delay(10);
    }
    return true;
  }
};

}} // namespace mesh::power
#endif
