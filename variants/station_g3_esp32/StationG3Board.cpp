#include "StationG3Board.h"
#include <helpers/ESP32GpioHold.h>

void StationG3Board::shutdownPeripherals() {
  ESP32Board::shutdownPeripherals();

  loRaFEMControl.setSleepModeEnable();
#ifdef P_PA1_EN
  mesh::esp32::holdOutputForDeepSleep((gpio_num_t)P_PA1_EN, !P_PA1_EN_ACTIVE);
#endif

#ifdef P_PRIMARY_LNA_EN
  for (uint8_t slot = 1; slot <= 2; ++slot)
    mesh::esp32::holdOutputForDeepSleep(
        (gpio_num_t)station_g3::pinsForSlot(slot).lna, !P_PRIMARY_LNA_EN_ACTIVE);
#endif
}

bool StationG3Board::setLoRaFemLnaEnabled(bool enable) {
  if (!loRaFEMControl.canControlLNA()) {
    return false;
  }
  loRaFEMControl.setLNAEnable(enable);
  return true;
}

bool StationG3Board::canControlLoRaFemLna() const {
  return loRaFEMControl.canControlLNA();
}

bool StationG3Board::isLoRaFemLnaEnabled() const {
  return loRaFEMControl.isLNAEnabled();
}

bool StationG3Board::setLoRaFemPaGainEnabled(bool enable) {
  if (!loRaFEMControl.canControlPAGain()) {
    return false;
  }
  loRaFEMControl.setPAGainEnable(enable);
  return true;
}

bool StationG3Board::canControlLoRaFemPaGain() const {
  return loRaFEMControl.canControlPAGain();
}

bool StationG3Board::isLoRaFemPaGainEnabled() const {
  return loRaFEMControl.isPAGainEnabled();
}

void StationG3Board::attachDynamicPrefs(KeyValueStore* prefs) {
  _prefs = prefs;
  if (_prefs == nullptr) {
    return;
  }

  char gain[8];

  gain[0] = 0;
  _prefs->getByKey("fem_rxgain", gain, 7);  // get initial values
  setLoRaFemLnaEnabled(strcmp(gain, "1") == 0);

  gain[0] = 0;
  _prefs->getByKey("fem_txgain", gain, 7);  // get initial values
  setLoRaFemPaGainEnabled(strcmp(gain, "1") == 0);
}

bool StationG3Board::handleCommand(const char* command, uint32_t sender_timestamp, char* reply) {
  (void)sender_timestamp;
  if (command == nullptr || reply == nullptr) {
    return false;
  }

  if (strcmp(command, "get radio.slot") == 0) {
    const auto& state = station_g3::slotState();
    snprintf(reply, 160, "> %s; active=%u; detected=%s%s%s",
        station_g3::slotModeName(state.savedMode), state.active,
        state.detected == 3 ? "1,2" : state.detected == 1 ? "1" : state.detected == 2 ? "2" : "none",
        state.savedMode != state.bootMode ? "; reboot required" : "",
        state.settingsHealthy ? "" : "; WARN settings unavailable/invalid");
    return true;
  }
  if (strncmp(command, "set radio.slot ", 15) == 0) {
    const char* value = command + 15;
    const int mode = strcmp(value, "auto") == 0 ? 0 : strcmp(value, "1") == 0 ? 1 : strcmp(value, "2") == 0 ? 2 : -1;
    if (mode < 0) strcpy(reply, "Error: radio.slot must be auto, 1, or 2");
    else if (!station_g3::saveSlotMode(static_cast<uint8_t>(mode)))
      strcpy(reply, "Error: radio.slot could not be saved; running slot unchanged");
    else snprintf(reply, 160, "OK - radio.slot %s saved; reboot to apply (active=%u)",
        station_g3::slotModeName(mode), station_g3::slotState().active);
    return true;
  }

  if (strcmp(command, "get radio.fem.rxgain") == 0) {
    if (!loRaFEMControl.canControlLNA()) {
      strcpy(reply, "Error: unsupported");
    } else {
      sprintf(reply, "> %s", isLoRaFemLnaEnabled() ? "on" : "off");
    }
    return true;
  }
  if (strncmp(command, "set radio.fem.rxgain ", 21) == 0) {
    if (!loRaFEMControl.canControlLNA()) {
      strcpy(reply, "Error: unsupported");
    } else if (_prefs == nullptr) {
      strcpy(reply, "Error: preferences unavailable");
    } else if (strcmp(&command[21], "on") == 0) {
      if (setLoRaFemLnaEnabled(true)) {
        _prefs->setByKey("fem_rxgain", "1");
        strcpy(reply, "OK - LoRa FEM RX gain on");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM RX gain");
      }
    } else if (strcmp(&command[21], "off") == 0) {
      if (setLoRaFemLnaEnabled(false)) {
        _prefs->setByKey("fem_rxgain", "0");
        strcpy(reply, "OK - LoRa FEM RX gain off");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM RX gain");
      }
    } else {
      strcpy(reply, "Error: state must be on or off");
    }
    return true;
  }

  if (strcmp(command, "get radio.fem.txgain") == 0) {
    if (!loRaFEMControl.canControlPAGain()) {
      strcpy(reply, "Error: unsupported");
    } else {
      sprintf(reply, "> %s", isLoRaFemPaGainEnabled() ? "on" : "off");
    }
    return true;
  }
  if (strncmp(command, "set radio.fem.txgain ", 21) == 0) {
    if (!loRaFEMControl.canControlPAGain()) {
      strcpy(reply, "Error: unsupported");
    } else if (_prefs == nullptr) {
      strcpy(reply, "Error: preferences unavailable");
    } else if (strcmp(&command[21], "on") == 0) {
      if (setLoRaFemPaGainEnabled(true)) {
        _prefs->setByKey("fem_txgain", "1");
        strcpy(reply, "OK - LoRa FEM TX gain on");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM TX gain");
      }
    } else if (strcmp(&command[21], "off") == 0) {
      if (setLoRaFemPaGainEnabled(false)) {
        _prefs->setByKey("fem_txgain", "0");
        strcpy(reply, "OK - LoRa FEM TX gain off");
      } else {
        strcpy(reply, "Error: failed to apply LoRa FEM TX gain");
      }
    } else {
      strcpy(reply, "Error: state must be on or off");
    }
    return true;
  }

  return false; // not handled
}
