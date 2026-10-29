#include "ChannelsPage.h"

int ChannelsPage::getChannelCount() {
  ChannelDetails channel;
  int num = 0;

  for (int i = 0; i < MAX_GROUP_CHANNELS; i++) {
    if (the_mesh_cp.getChannel(i, channel) && channel.name[0]) num++;
  }

  return num;
}

bool ChannelsPage::getChannelIndex(int list_idx, int &real_idx) {
  ChannelDetails channel;
  int match_count = 0;
  for (int i = 0; i < MAX_GROUP_CHANNELS; i++) {
    if (the_mesh_cp.getChannel(i, channel) && channel.name[0]) {
      if (match_count++ == list_idx) {
        real_idx = i;
        return true;
      }
    }
  }
  return false;
}

int ChannelsPage::render(CardputerDisplay &lcd) {
  const int row = lcd.getRowHeight();
  const int title_y = 20;
  const int list_y = title_y + row + 1;
  const int rows = std::max(1, (lcd.height() - 1 - list_y) / row);
  const int name_x = 5 + lcd.getTextWidth(">") + 6;

  lcd.setColor(CardputerDisplay::P_GREEN);
  lcd.drawTextCentered(lcd.width() / 2, title_y, "Channels");

  int real_idx = 0;
  int list_page = channel_list_idx / rows;
  int list_idx = channel_list_idx % rows;
  char buf[6];

  for (int i = 0; i < rows; i++) {
    lcd.setColor(CardputerDisplay::P_GREEN);
    const int y = list_y + i * row;

    ChannelDetails channel;
    if (!getChannelIndex(list_page * rows + i, real_idx)
        || !the_mesh_cp.getChannel(real_idx, channel)) {
      break;
    }

    int unread_count = 0;
    uint8_t history_id[PUB_KEY_SIZE];
    if (!CardputerMesh::channelHistoryId(channel, history_id)) continue;

    for (int j = 0; j < _p->getUnread()->countChats(); j++) {
      const UnreadCounterItem *item = _p->getUnread()->get(j);
      if (item && item->is_channel && memcmp(item->pkey, history_id, sizeof(history_id)) == 0) {
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

    lcd.drawTextEllipsized(name_x, y, lcd.width() - name_x - right_pad, channel.name);
  }

  return 5000;
}

bool ChannelsPage::handleInput(Keyboard::Event &e) {
  if (e.key == Keyboard::ARROW_UP) {
    if (channel_list_idx == 0) {
      channel_list_idx = std::max(getChannelCount() - 1, 0);
    } else {
      channel_list_idx--;
    }
    return true;
  }
  if (e.key == Keyboard::ARROW_DOWN) {
    if (channel_list_idx < getChannelCount() - 1) {
      channel_list_idx++;
    } else {
      channel_list_idx = 0;
    }
    return true;
  }
  if (e.key == Keyboard::KEY_RETURN) {
    ChannelDetails c;
    int real_idx;
    if (getChannelIndex(channel_list_idx, real_idx) && the_mesh_cp.getChannel(real_idx, c)) {
      _p->selectChannel(c, real_idx);
    }
    return true;
  }

  return false;
}
