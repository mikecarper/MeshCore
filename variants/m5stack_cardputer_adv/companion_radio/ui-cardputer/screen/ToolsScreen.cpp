#include "ToolsScreen.h"

int ToolsScreen::render(CardputerDisplay &lcd) {
  char buf[64];

  lcd.setTextSize(1);
  lcd.setColor(CardputerDisplay::P_GREEN);
  const int row = lcd.getRowHeight();
  const int title_y = 3;
  const int list_y = title_y + row + 2;
  const int rows = std::max(1, (lcd.height() - 1 - list_y) / row);
  const int item_x = 5 + lcd.getTextWidth(">") + 6;

  if (page == ToolsPage::MenuPage) {
    lcd.drawTextCentered(lcd.width() / 2, title_y, "Tools");

    int real_idx = 0;
    int list_page = menu_index / rows;
    int list_idx = menu_index % rows;

    for (int i = 0; i < rows; i++) {
      real_idx = list_page * rows + i;

      if (real_idx >= ToolsMenuItem::Count) {
        break;
      }

      const char *label = menu_item_labels[real_idx];
      lcd.setColor(label[0] == '-' ? CardputerDisplay::P_GREEN : CardputerDisplay::P_YELLOW);
      lcd.drawTextEllipsized(item_x, list_y + i * row, lcd.width() - item_x, label);

      if (i == list_idx) {
        lcd.setColor(CardputerDisplay::P_GREEN);
        lcd.drawTextLeftAlign(5, list_y + i * row, ">");
      }
    }
  } else if (page == ToolsPage::DiscoverPage) {
    lcd.drawTextCentered(lcd.width() / 2, title_y, "Discover");

    for (int i = 0; i < discovered_repeaters_count && i < rows; i++) {
      DiscoveredRepeater *rep = &discovered_repeaters[i];
      snprintf(buf, sizeof(buf), "%.1fdB", rep->snr);
      const int snr_w = lcd.getTextWidth(buf);

      lcd.setColor(CardputerDisplay::P_YELLOW);
      lcd.drawTextEllipsized(0, list_y + i * row, lcd.width() - snr_w - 6, rep->name);

      if (rep->snr > 0) {
        lcd.setColor(CardputerDisplay::P_GREEN);
      } else {
        lcd.setColor(CardputerDisplay::P_RED);
      }

      lcd.drawTextRightAlign(lcd.width() - 2, list_y + i * row, buf);
    }

    return 1000;
  }

  return 5000;
}

void ToolsScreen::menuItemEnter(ToolsMenuItem item) {
  switch (item) {
    case ToolsMenuItem::AdvertFlood:
      if (the_mesh_cp.sendAdvert(true)) {
        _task->showAlert("Flood Advert sent", 3000);
      }

      break;
    case ToolsMenuItem::AdvertZeroHop:
      if (the_mesh_cp.sendAdvert(false)) {
        _task->showAlert("Advert sent", 3000);
      }
      break;

    case ToolsMenuItem::DiscoverRepeaters:
      discovered_repeaters_count = 0;

      if (the_mesh_cp.sendRepeatersDiscover()) {
        page = ToolsPage::DiscoverPage;
        _task->showAlert("Waiting for response...", 10000);
      }

      break;

    case ToolsMenuItem::PowerOff:
      power_off_pending = true;
      power_off_released = false;
      power_off_deadline = static_cast<uint32_t>(millis()) + POWER_OFF_RELEASE_TIMEOUT_MS;
      _task->showAlert("Release Enter to power off", POWER_OFF_RELEASE_TIMEOUT_MS);
      break;

    case ToolsMenuItem::Restart:
      _task->shutdown(true);
      break;

    default:
      break;
  }
}

bool ToolsScreen::handlePowerOffInput(const Keyboard::Event &e) {
  if (!power_off_pending) return false;
  if (e.changed && e.key == Keyboard::KEY_RETURN && !e.down) {
    power_off_released = true;
  } else if (e.changed && e.key == Keyboard::KEY_ESC && e.down) {
    power_off_pending = false;
    _task->dismissAlert();
  }
  return true;
}

void ToolsScreen::poll() {
  if (!power_off_pending) return;
  if (power_off_released) {
    power_off_pending = false;
    _task->dismissAlert();
    _task->shutdown();
  } else if (static_cast<int32_t>(static_cast<uint32_t>(millis()) - power_off_deadline) >= 0) {
    power_off_pending = false;
    _task->showAlert("Power off cancelled: release Enter", 2000);
  }
}

bool ToolsScreen::handleInput(Keyboard::Event &e) {
  if (_task->isAlertActive()) {
    return false;
  }

  if (page == ToolsPage::MenuPage) {
    if (e.key == Keyboard::KEY_ESC) {
      _task->gotoMainScreen();
      return true;
    }

    if (e.key == Keyboard::ARROW_UP) {
      if (menu_index == 0) {
        menu_index = ToolsMenuItem::Count - 1;
      } else {
        menu_index--;
      }
      return true;
    }

    if (e.key == Keyboard::ARROW_DOWN) {
      if (menu_index < ToolsMenuItem::Count - 1) {
        menu_index++;
      } else {
        menu_index = 0;
      }
      return true;
    }

    if (e.key == Keyboard::KEY_RETURN) {
      menuItemEnter(static_cast<ToolsMenuItem>(menu_index));
      return true;
    }
  } else if (page == ToolsPage::DiscoverPage) {
    if (e.key == Keyboard::KEY_ESC) {
      page = ToolsPage::MenuPage;
      return true;
    }
  }
  return false;
}

void ToolsScreen::discoverRecv(const mesh::Identity &id, float snr) {
  if (page == ToolsPage::DiscoverPage && discovered_repeaters_count < MAX_DISCOVERED_REPEATERS) {
    DiscoveredRepeater rep;
    rep.snr = snr;
    rep.name[0] = '\0';

    ContactInfo *contact = the_mesh_cp.lookupContactByPubKey(id.pub_key, CONTACT_LOOKUP_BYTES);

    if (contact) {
      snprintf(rep.name, sizeof(rep.name), "%s", contact->name);
    } else {
      snprintf(rep.name, sizeof(rep.name), "[%02X %02X %02X %02X]", id.pub_key[0], id.pub_key[1],
               id.pub_key[2], id.pub_key[3]);
    }

    discovered_repeaters[discovered_repeaters_count] = rep;
    discovered_repeaters_count++;

    _task->dismissAlert();
    _task->playSound(SoundType::DiscoveryResult);
  }
}
