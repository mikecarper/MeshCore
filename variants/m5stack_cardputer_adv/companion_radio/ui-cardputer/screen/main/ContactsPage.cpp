#include "ContactsPage.h"

#include "helpers.h"

int ContactsPage::getFilteredContactCount() {
  ContactInfo contact;
  int num = 0;
  // Start iterating from MAX_ANON_CONTACTS to skip anonymous contacts
  for (int i = MAX_ANON_CONTACTS; i < the_mesh_cp.getTotalContactSlots(); i++) {
    if (!the_mesh_cp.getContactByIdx(i, contact)) {
      break;
    }

    if (contact_search_box.isEmpty() ||
        Helpers::containsIgnoreCase(contact.name, contact_search_box.c_str())) {
      num++;
    }
  }

  return num;
}

bool ContactsPage::getFilteredContactIndex(int list_idx, int &real_idx) {
  ContactInfo contact;
  int match_count = 0;
  // Start iterating from MAX_ANON_CONTACTS to skip anonymous contacts
  for (int i = MAX_ANON_CONTACTS; i < the_mesh_cp.getTotalContactSlots(); i++) {
    if (!the_mesh_cp.getContactByIdx(i, contact)) {
      break;
    }

    if (contact_search_box.isEmpty() ||
        Helpers::containsIgnoreCase(contact.name, contact_search_box.c_str())) {
      if (match_count == list_idx) {
        real_idx = i;
        return true;
      }
      match_count++;
    }
  }

  return false;
}

int ContactsPage::render(CardputerDisplay &lcd) {
  const int row = lcd.getRowHeight();
  const int title_y = 20;
  const int list_y = title_y + row + 1;
  const int search_y = lcd.height() - row - 2;
  const int rows = std::max(1, (search_y - 3 - list_y) / row);
  const int type_x = 5 + lcd.getTextWidth(">") + 4;
  const int name_x = type_x + lcd.getTextWidth("C") + 4;

  lcd.setColor(CardputerDisplay::P_GREEN);
  lcd.drawTextCentered(lcd.width() / 2, title_y, "Contacts");
  int real_idx = 0;
  int list_page = contact_list_idx / rows;
  int list_idx = contact_list_idx % rows;
  char buf[6];

  for (int i = 0; i < rows; i++) {
    lcd.setColor(CardputerDisplay::P_GREEN);
    const int y = list_y + i * row;

    ContactInfo contact;
    int display_idx = list_page * rows + i;

    if (!getFilteredContactIndex(display_idx, real_idx)) {
      break;
    }

    the_mesh_cp.getContactByIdx(real_idx, contact);

    int unread_count = 0;

    for (int j = 0; j < _p->getUnread()->countChats(); j++) {
      const UnreadCounterItem *item = _p->getUnread()->get(j);
      if (item && !item->is_channel && memcmp(item->pkey, contact.id.pub_key, PUB_KEY_SIZE) == 0) {
        unread_count = item->count;
        break;
      }
    }

    if (i == list_idx) {
      lcd.drawTextLeftAlign(5, y, ">");
    }

    int right_pad = 0;

    if (unread_count > 0) {
      snprintf(buf, sizeof(buf), "%d", unread_count);
      right_pad = lcd.getTextWidth(buf) + 5;
      lcd.setColor(CardputerDisplay::P_ORANGE);
      lcd.drawTextRightAlign(lcd.width() - 1, y, buf);
    }

    if (contact.type == ADV_TYPE_CHAT) {
      lcd.drawTextLeftAlign(type_x, y, "C");
    }

    lcd.drawTextEllipsized(name_x, y, lcd.width() - name_x - right_pad, contact.name);
  }

  lcd.drawRect(1, search_y - 2, lcd.width() - 1, 1);
  if (contact_search_box.isEmpty()) {
    lcd.setColor(CardputerDisplay::P_GRAY);
    lcd.drawTextLeftAlign(5, search_y, "Search");
  } else {
    lcd.setColor(CardputerDisplay::P_WHITE);
    lcd.drawTextLeftAlignWithScroll(5, search_y, lcd.width() - 10,
                                    contact_search_box.c_str());

  }
  return 5000;
}

bool ContactsPage::handleInput(Keyboard::Event &e) {
  CardputerLayout *lay = _p->getUiTask()->getBoard()->getLayout();

  if (e.key == Keyboard::KEY_BACKSPACE) {
    if (contact_search_box.length() > 0) {
      Helpers::removeLastStringChar(contact_search_box);
      contact_list_idx = 0;
    }
    return true;
  }

  if (e.key == Keyboard::ARROW_UP) {
    if (contact_list_idx == 0) {
      contact_list_idx = std::max(getFilteredContactCount() - 1, 0);
    } else {
      contact_list_idx--;
    }
    return true;
  }

  if (e.key == Keyboard::ARROW_DOWN) {
    if (contact_list_idx < getFilteredContactCount() - 1) {
      contact_list_idx++;
    } else {
      contact_list_idx = 0;
    }
    return true;
  }

  if (e.key == Keyboard::KEY_RETURN) {
    int real_idx = -1;
    if (getFilteredContactIndex(contact_list_idx, real_idx)) {
      ContactInfo c;
      if (the_mesh_cp.getContactByIdx(real_idx, c)) {
        if (c.type == ADV_TYPE_CHAT) {
          _p->selectContact(c, real_idx);
        } else if ((c.type == ADV_TYPE_REPEATER || c.type == ADV_TYPE_ROOM) &&
                   !_p->getUiTask()->isAlertActive()) {
          the_mesh_cp.sendPing(c);
          _p->getUiTask()->showAlert("Waiting for response...", 4000);
        }
      }
    }
    return true;
  }

  if (e.modifiers.ctrl && e.key == Keyboard::KEY_R) { // reset path
    int real_idx = -1;
    if (getFilteredContactIndex(contact_list_idx, real_idx)) {
      ContactInfo c;
      if (the_mesh_cp.getContactByIdx(real_idx, c)) {
        if (c.type == ADV_TYPE_CHAT) {
          // getContactByIdx does not return a reference
          ContactInfo *ref = the_mesh_cp.lookupPersistentContactByPubKey(c.id.pub_key, PUB_KEY_SIZE);
          if (ref) {
            const uint8_t previous_path_len = ref->out_path_len;
            ref->out_path_len = OUT_PATH_UNKNOWN;
            if (the_mesh_cp.queueUiContactWrite(*ref)) {
              _p->getUiTask()->showAlert("Path cleared", 1000);
              _p->refreshSelectedContact();
            } else {
              ref->out_path_len = previous_path_len;
              _p->getUiTask()->showAlert("Unable to save path", 2000);
            }
          }
        }
      }
    }
    return true;
  }

  bool skip_input = e.key == Keyboard::KEY_ESC || e.key == Keyboard::ARROW_LEFT ||
                    e.key == Keyboard::ARROW_RIGHT || e.key == Keyboard::KEY_TAB;

  if (!skip_input) {
    const char *repl = lay->lookup(e);
    if (repl[0]) {
      if (contact_search_box.length() + strlen(repl) <= UI_CHANNEL_SEARCH_MAX_CHARS) {
        contact_search_box += repl;
      }
      contact_list_idx = 0;
      return true;
    }
  }

  return false;
}
