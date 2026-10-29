#include "SettingsScreen.h"

#include "helpers.h"
#include <helpers/CLICommandUtils.h>


void SettingsScreen::renderItem(CardputerDisplay &lcd, SettingsItem item, int x, int y) {
  char tmp[64] = { 0 };
  CardputerDisplay::PaletteColor text_color = CardputerDisplay::P_YELLOW;

  switch (item) {
    case SettingsItem::HdrRadio:
      text_color = CardputerDisplay::P_GREEN;
      snprintf(tmp, sizeof(tmp), "---- RADIO ----");
      break;

    case SettingsItem::HdrDevice:
      text_color = CardputerDisplay::P_GREEN;
      snprintf(tmp, sizeof(tmp), "---- DEVICE ----");
      break;

    case SettingsItem::HdrMesh:
      text_color = CardputerDisplay::P_GREEN;
      snprintf(tmp, sizeof(tmp), "---- MESH ----");
      break;

    case SettingsItem::HdrPublicInfo:
      text_color = CardputerDisplay::P_GREEN;
      snprintf(tmp, sizeof(tmp), "---- PUBLIC INFO ----");
      break;

    case SettingsItem::RadioFreq:
      snprintf(tmp, sizeof(tmp), "FREQ: %06.3f", _node_prefs->freq);
      break;

    case SettingsItem::PublicInfoName:
      snprintf(tmp, sizeof(tmp), "Name: %s", _node_prefs->node_name);
      break;

    case SettingsItem::RadioBw:
      snprintf(tmp, sizeof(tmp), "BW: %03.2f", _node_prefs->bw);
      break;

    case SettingsItem::RadioSf:
      snprintf(tmp, sizeof(tmp), "SF: %d", _node_prefs->sf);
      break;

    case SettingsItem::RadioCr:
      snprintf(tmp, sizeof(tmp), "CR: %d", _node_prefs->cr);
      break;

    case SettingsItem::RadioPwr:
      snprintf(tmp, sizeof(tmp), "PWR: %ddBm", _node_prefs->tx_power_dbm);
      break;

    case SettingsItem::DeviceTextSize:
      snprintf(tmp, sizeof(tmp), "Text size: %s", CardputerDisplay::textLevelName(lcd.getTextLevel()));
      break;

    case SettingsItem::DeviceBeep:
      snprintf(tmp, sizeof(tmp), "Sound: %s", _node_prefs->buzzer_quiet ? "OFF" : "ON");
      break;

    case SettingsItem::DeviceGps:
      snprintf(tmp, sizeof(tmp), "GPS: %s", _task->getGPSState() ? "ON" : "OFF");
      break;

    case SettingsItem::DeviceBluetooth:
      snprintf(tmp, sizeof(tmp), "Bluetooth: %s", _task->isBluetoothEnabled() ? "ON" : "OFF");
      break;

    case SettingsItem::DevicePowersave:
      snprintf(tmp, sizeof(tmp), "Power save: %s", _task->powerSaveEnabled() ? "ON" : "OFF");
      break;

    case SettingsItem::DeviceBatteryCorrection:
      snprintf(tmp, sizeof(tmp), "Batt. correction: %.3f", _custom_prefs->battery_correction);
      break;

    case SettingsItem::MeshPathSize:
      snprintf(tmp, sizeof(tmp), "Path hash size: %d", _node_prefs->path_hash_mode + 1);
      break;

    case SettingsItem::MeshDefaultScope:
      snprintf(tmp, sizeof(tmp), "Default scope: %s",
               _node_prefs->default_scope_name[0] ? _node_prefs->default_scope_name : "none");
      break;

    default:
      text_color = CardputerDisplay::P_RED;
      snprintf(tmp, sizeof(tmp), "???");
      break;
  }

  lcd.setColor(text_color);
  lcd.drawTextEllipsized(x, y, lcd.width() - x, tmp);
}

bool SettingsScreen::enterItemEdit(SettingsItem item) {
  switch (item) {
    case SettingsItem::HdrRadio:
    case SettingsItem::HdrDevice:
    case SettingsItem::HdrMesh:
      return true;

    case SettingsItem::DeviceTextSize:
      _task->cycleTextSize();
      return true;

    case SettingsItem::DeviceBeep:
      _task->toggleBuzzer();
      return true;

    case SettingsItem::DeviceGps:
      _task->toggleGPS();
      return true;

    case SettingsItem::DeviceBluetooth:
      _task->toggleBluetooth();
      return true;

    case SettingsItem::DevicePowersave:
      _task->togglePowerSave();
      return true;

    case SettingsItem::MeshPathSize:
      edit_u8 = _node_prefs->path_hash_mode + 1;
      is_editing = true;
      return true;

    case SettingsItem::RadioSf:
      edit_u8 = _node_prefs->sf;
      is_editing = true;
      return true;

    case SettingsItem::RadioCr:
      edit_u8 = _node_prefs->cr;
      is_editing = true;
      return true;

    case SettingsItem::RadioPwr:
      edit_tx_power = _node_prefs->tx_power_dbm;
      is_editing = true;
      return true;

    case SettingsItem::PublicInfoName:
      edit_buffer = String(_node_prefs->node_name);
      is_editing = true;
      return true;

    case SettingsItem::MeshDefaultScope:
      edit_buffer = String(_node_prefs->default_scope_name);
      is_editing = true;
      return true;

    case SettingsItem::RadioFreq:
      edit_buffer = String(_node_prefs->freq, 3);
      is_editing = true;
      return true;

    case SettingsItem::RadioBw:
      edit_buffer = String(_node_prefs->bw, 2);
      is_editing = true;
      return true;

    case SettingsItem::DeviceBatteryCorrection:
      edit_buffer = String(_task->getBoard()->getBattMilliVolts());
      is_editing = true;
      return true;

    default:
      break;
  }

  return false;
}

void SettingsScreen::cancelItemEdit(SettingsItem item) {
  is_editing = false;
}

bool SettingsScreen::commitCommand(const char *command) {
  char reply[160] = {};
  if (the_mesh_cp.handleLocalCommand(command, reply) && strncmp(reply, "OK", 2) == 0) {
    if (strstr(reply, "pending")) _task->showAlert(reply, 2000);
    return true;
  }
  snprintf(edit_error, sizeof(edit_error), "%.*s", static_cast<int>(sizeof(edit_error) - 1),
           reply[0] ? reply : "Setting could not be saved");
  return false;
}

bool SettingsScreen::commitRadio(float freq, float bw, uint8_t sf, uint8_t cr) {
  if (!validRadioParams(freq, bw, sf, cr)) return false;
  char command[112];
  snprintf(command, sizeof(command), "set radio %.6f,%.6f,%u,%u,%u", freq, bw, sf, cr,
           the_mesh_cp.getPrimaryRadioPreamble());
  if (!commitCommand(command)) return false;
  _task->showAlert("Radio saved; applies when idle", 2000);
  return true;
}

bool SettingsScreen::commitItemEdit(SettingsItem item) {
  snprintf(edit_error, sizeof(edit_error), "Invalid value");
  switch (item) {
    case SettingsItem::PublicInfoName: {
      char command[sizeof(_node_prefs->node_name) + 10];
      if (edit_buffer.length() >= sizeof(_node_prefs->node_name)) return false;
      snprintf(command, sizeof(command), "set name %s", edit_buffer.c_str());
      return commitCommand(command);
    }

    case SettingsItem::MeshDefaultScope: {
      char previous_name[sizeof(_node_prefs->default_scope_name)];
      uint8_t previous_key[sizeof(_node_prefs->default_scope_key)];
      memcpy(previous_name, _node_prefs->default_scope_name, sizeof(previous_name));
      memcpy(previous_key, _node_prefs->default_scope_key, sizeof(previous_key));
      memset(_node_prefs->default_scope_name, 0, sizeof(_node_prefs->default_scope_name));
      memset(_node_prefs->default_scope_key, 0, sizeof(_node_prefs->default_scope_key));
      if (edit_buffer.length() > 0) {
        strncpy(_node_prefs->default_scope_name, edit_buffer.c_str(), sizeof(_node_prefs->default_scope_name) - 1);
        const uint8_t prefix[] = "#";
        mesh::Utils::sha256(_node_prefs->default_scope_key, sizeof(_node_prefs->default_scope_key), prefix, 1,
                            reinterpret_cast<const uint8_t *>(_node_prefs->default_scope_name),
                            strlen(_node_prefs->default_scope_name));
      }
      if (the_mesh_cp.savePrefs()) return true;
      memcpy(_node_prefs->default_scope_name, previous_name, sizeof(previous_name));
      memcpy(_node_prefs->default_scope_key, previous_key, sizeof(previous_key));
      snprintf(edit_error, sizeof(edit_error), "Scope could not be saved");
      return false;
    }

    case SettingsItem::RadioSf:
      return commitRadio(_node_prefs->freq, _node_prefs->bw, edit_u8, _node_prefs->cr);
    case SettingsItem::RadioCr:
      return commitRadio(_node_prefs->freq, _node_prefs->bw, _node_prefs->sf, edit_u8);
    case SettingsItem::RadioPwr: {
      if (edit_tx_power < INT8_MIN || edit_tx_power > INT8_MAX
          || !radio_driver.isTxPowerValid(static_cast<int8_t>(edit_tx_power))) return false;
      char command[24];
      snprintf(command, sizeof(command), "set tx %d", static_cast<int>(edit_tx_power));
      return commitCommand(command);
    }
    case SettingsItem::MeshPathSize: {
      if (edit_u8 < 1 || edit_u8 > 3) return false;
      const uint8_t previous = _node_prefs->path_hash_mode;
      _node_prefs->path_hash_mode = edit_u8 - 1;
      if (the_mesh_cp.savePrefs()) return true;
      _node_prefs->path_hash_mode = previous;
      snprintf(edit_error, sizeof(edit_error), "Path size could not be saved");
      return false;
    }
    case SettingsItem::RadioFreq: {
      float value;
      return inputParsePositiveFloat(value)
          && commitRadio(value, _node_prefs->bw, _node_prefs->sf, _node_prefs->cr);
    }
    case SettingsItem::RadioBw: {
      float value;
      return inputParsePositiveFloat(value)
          && commitRadio(_node_prefs->freq, value, _node_prefs->sf, _node_prefs->cr);
    }
    case SettingsItem::DeviceBatteryCorrection: {
      if (!edit_buffer.length()) return false;
      const uint16_t real = edit_buffer.toInt();
      const uint16_t adc = _task->getBoard()->getBattMilliVolts();
      if (!real || !adc) return false;
      const float correction = static_cast<float>(real) / adc;
      if (!isfinite(correction) || correction < 0.5f || correction > 2.0f) return false;
      _custom_prefs->battery_correction = correction;
      if (the_mesh_cp.saveCustomPrefs()) return true;
      snprintf(edit_error, sizeof(edit_error), "Battery correction could not be saved");
      return false;
    }
    default:
      return false;
  }
}

void SettingsScreen::handleEditInput(SettingsItem item, Keyboard::Event &e) {
  switch (item) {
    case SettingsItem::RadioSf:
      if (e.key == Keyboard::ARROW_LEFT && edit_u8 > 5) {
        edit_u8--;
      } else if (e.key == Keyboard::ARROW_RIGHT && edit_u8 < 12) {
        edit_u8++;
      }
      break;
    case SettingsItem::RadioCr:
      if (e.key == Keyboard::ARROW_LEFT && edit_u8 > 5) {
        edit_u8--;
      } else if (e.key == Keyboard::ARROW_RIGHT && edit_u8 < 8) {
        edit_u8++;
      }
      break;

    case SettingsItem::RadioPwr:
      if (e.key == Keyboard::ARROW_LEFT && edit_tx_power > INT8_MIN
          && radio_driver.isTxPowerValid(static_cast<int8_t>(edit_tx_power - 1))) {
        --edit_tx_power;
      } else if (e.key == Keyboard::ARROW_RIGHT && edit_tx_power < INT8_MAX
          && radio_driver.isTxPowerValid(static_cast<int8_t>(edit_tx_power + 1))) {
        ++edit_tx_power;
      }
      break;
    case SettingsItem::MeshPathSize:
      if (e.key == Keyboard::ARROW_LEFT && edit_u8 > 1) {
        edit_u8--;
      } else if (e.key == Keyboard::ARROW_RIGHT && edit_u8 < 3) {
        edit_u8++;
      }
      break;

    case SettingsItem::RadioFreq:
    case SettingsItem::RadioBw:

      if (e.key == Keyboard::KEY_BACKSPACE) {
        Helpers::removeLastStringChar(edit_buffer);
      } else if (e.key == Keyboard::KEY_PERIOD && edit_buffer.indexOf('.') == -1
                 && edit_buffer.length() < MAX_NUMERIC_EDIT_BYTES) {
        edit_buffer.concat('.');
      } else {
        const char ch = _task->getBoard()->getLayout()->lookupDefault(e)[0];
        if (ch && isdigit(static_cast<unsigned char>(ch))
            && edit_buffer.length() < MAX_NUMERIC_EDIT_BYTES) {
          edit_buffer.concat(ch);
        }
      }
      break;
    case SettingsItem::DeviceBatteryCorrection:
      if (e.key == Keyboard::KEY_BACKSPACE) {
        Helpers::removeLastStringChar(edit_buffer);
      } else {
        const char ch = _task->getBoard()->getLayout()->lookupDefault(e)[0];
        if (ch && isdigit(ch) && edit_buffer.length() < 4) {
          edit_buffer.concat(ch);
        }
      }
      break;
    case SettingsItem::PublicInfoName:
      if (e.key == Keyboard::KEY_BACKSPACE) {
        Helpers::removeLastStringChar(edit_buffer);
      } else if (edit_buffer.length() < sizeof(_node_prefs->node_name) - 2) {
        const char *ch = _task->getBoard()->getLayout()->lookup(e);
        if (ch[0]) {
          edit_buffer.concat(ch);
        }
      }
      break;
    case SettingsItem::MeshDefaultScope:
      if (e.key == Keyboard::KEY_BACKSPACE) {
        Helpers::removeLastStringChar(edit_buffer);
      } else {
        const char ch = _task->getBoard()->getLayout()->lookupDefault(e)[0];
        if (ch && (isalnum(ch) || ch == '_' || ch == '-') &&
            edit_buffer.length() < sizeof(_node_prefs->default_scope_name) - 2) {
          edit_buffer.concat(ch);
        }
      }
      break;

    default:
      break;
  }
}

bool SettingsScreen::inputParsePositiveFloat(float &val) {
  float tmp;
  if (!mesh::cli::parseDecimalStrict(edit_buffer.c_str(), tmp) || !isfinite(tmp) || tmp <= 0) return false;
  val = tmp;
  return true;
}

bool SettingsScreen::validRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) const {
  mesh::RadioProfileParams params;
  params.freq = freq;
  params.bw = bw;
  params.sf = sf;
  params.cr = cr;
  return radio_driver.validateProfile(params);
}

int SettingsScreen::render(CardputerDisplay &display) {
  char buf[64];
  const int row = display.getRowHeight();
  const int title_y = 3;
  const int list_y = title_y + row + 2;
  const int rows = std::max(1, (display.height() - 1 - list_y) / row);
  const int item_x = 5 + display.getTextWidth(">") + 6;

  display.setTextSize(1);
  display.setColor(CardputerDisplay::P_GREEN);
  display.drawTextCentered(display.width() / 2, title_y, "Settings");

  int real_idx = 0;
  int list_page = menu_index / rows;
  int list_idx = menu_index % rows;

  for (int i = 0; i < rows; i++) {
    real_idx = list_page * rows + i;

    if (real_idx >= SettingsItem::Count) {
      break;
    }

    renderItem(display, static_cast<SettingsItem>(real_idx), item_x, list_y + i * row);

    if (i == list_idx) {
      display.setColor(CardputerDisplay::P_GREEN);
      display.drawTextLeftAlign(5, list_y + i * row, ">");
    }
  }

  if (is_editing) {
    // Edit popup: optional hint line, then the value in heading size
    const int x_margin = 5;
    const int box_y = 22;
    const int box_h = display.height() - box_y * 2;
    display.setColor(CardputerDisplay::P_BLACK);
    display.fillRect(x_margin, box_y, display.width() - x_margin * 2, box_h);
    display.setColor(CardputerDisplay::P_WHITE);
    display.drawRect(x_margin, box_y, display.width() - x_margin * 2, box_h);

    display.setTextSize(1);
    switch (menu_index) {
      case SettingsItem::PublicInfoName:
        display.drawTextCentered(display.width() / 2, box_y + 5, _task->getBoard()->getLayout()->getCurrentCode());
        break;
      case SettingsItem::DeviceBatteryCorrection:
        snprintf(buf, sizeof(buf), "Read %umV, real:", _task->getBoard()->getBattMilliVolts());
        display.drawTextCentered(display.width() / 2, box_y + 5, buf);
        break;
      default:
        break;
    }

    const int value_y = box_y + 5 + row + 6;
    switch (menu_index) {
      case SettingsItem::RadioSf:
      case SettingsItem::RadioCr:
      case SettingsItem::MeshPathSize:
        display.setTextSize(2);
        snprintf(buf, sizeof(buf), "- %d +", edit_u8);
        display.drawTextCentered(display.width() / 2, value_y, buf);
        break;
      case SettingsItem::RadioPwr:
        display.setTextSize(2);
        snprintf(buf, sizeof(buf), "- %d +", static_cast<int>(edit_tx_power));
        display.drawTextCentered(display.width() / 2, value_y, buf);
        break;
      case SettingsItem::PublicInfoName:
      case SettingsItem::RadioFreq:
      case SettingsItem::RadioBw:
      case SettingsItem::DeviceBatteryCorrection:
      case SettingsItem::MeshDefaultScope:
        display.setTextSize(menu_index == SettingsItem::PublicInfoName ? 1 : 2);
        snprintf(buf, sizeof(buf), "%s_", edit_buffer.c_str());
        display.drawTextLeftAlignWithScroll(x_margin + 5, value_y, display.width() - x_margin * 2 - 10, buf);
        break;
      default:
        break;
    }

    display.setTextSize(1);
  }

  return 5000;
}

bool SettingsScreen::handleInput(Keyboard::Event &e) {
  if (is_editing) {
    if (e.key == Keyboard::KEY_RETURN) {
      if (!commitItemEdit(static_cast<SettingsItem>(menu_index))) {
        _task->showAlert(edit_error, 2000);
      } else {
        is_editing = false;
      }
      return true;
    }

    if (e.key == Keyboard::KEY_ESC) {
      cancelItemEdit(static_cast<SettingsItem>(menu_index));
      return true;
    }

    if (e.modifiers.ctrl && e.key == Keyboard::KEY_SPACE) {
      _task->getBoard()->getLayout()->switchLayout();
      return true;
    }

    handleEditInput(static_cast<SettingsItem>(menu_index), e);

  } else {
    if (e.key == Keyboard::KEY_ESC || e.modifiers.opt) {
      _task->gotoMainScreen();
      return true;
    }

    if (e.key == Keyboard::ARROW_UP) {
      if (menu_index == 0) {
        menu_index = SettingsItem::Count - 1;
      } else {
        menu_index--;
      }

      return true;
    }
    if (e.key == Keyboard::ARROW_DOWN) {
      if (menu_index < SettingsItem::Count - 1) {
        menu_index++;
      } else {
        menu_index = 0;
      }

      return true;
    }
    if (e.key == Keyboard::KEY_RETURN) {
      if (!enterItemEdit(static_cast<SettingsItem>(menu_index))) {
        _task->showAlert("Not implemented yet", 2000);
      }
      return true;
    }
  }
  return false;
}
