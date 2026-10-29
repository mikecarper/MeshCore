#include "ChatPage.h"

#include "helpers.h"

#include <stdarg.h>

namespace {

__attribute__((format(printf, 4, 5)))
size_t appendf(char *out, size_t cap, size_t pos, const char *fmt, ...) {
  if (cap == 0 || pos >= cap - 1) {
    return pos;
  }
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(out + pos, cap - pos, fmt, ap);
  va_end(ap);
  if (n < 0) {
    out[pos] = '\0';
    return pos;
  }
  pos += (size_t)n;
  return pos < cap ? pos : cap - 1;
}

// Epoch seconds -> "hh:mm" (UTC); "--:--" when unknown.
void formatClock(uint32_t ts, char *out, size_t cap) {
  if (ts == 0) {
    snprintf(out, cap, "--:--");
  } else {
    snprintf(out, cap, "%02u:%02u", (unsigned)((ts / 3600) % 24), (unsigned)((ts / 60) % 60));
  }
}

int messageLines(CardputerDisplay &lcd, const HistoryMessage *m) {
  return m ? (lcd.getTextWidth(m->text) / lcd.width()) + 1 : 0;
}

} // namespace

const HistoryMessage *ChatPage::messageAt(int from_newest) const {
  const int count = (int)chat_history.count();
  const HistoryMessage *m = nullptr;
  if (from_newest < 0 || from_newest >= count || !chat_history.get((size_t)(count - 1 - from_newest), m)) {
    return nullptr;
  }
  return m;
}

int ChatPage::render(CardputerDisplay &lcd) {
  char buf[64];
  const int row = display.getRowHeight();
  const int line_h = display.getFontLineHeight();  // advance used when print() wraps
  const int title_y = 20;
  const int input_y = display.height() - row - 2;
  const int history_top = title_y + row + 2;
  const int history_bottom = input_y - 3;
  const int max_lines = std::max(1, (history_bottom - history_top) / line_h);

  display.setColor(CardputerDisplay::P_GREEN);

  if (current_contact_idx == -1 && current_channel_idx == -1) {
    display.setColor(CardputerDisplay::P_ORANGE);
    display.drawTextCentered(display.width() / 2, title_y, "No chat selected");
  } else if (current_contact_idx != -1) {
    // out_path_len packs the path hash count in the lower 6 bits, and (hash size - 1) in the upper 2 bits
    uint8_t path_hops = current_contact.out_path_len & 63;
    if (current_contact.out_path_len == OUT_PATH_UNKNOWN) {
      snprintf(buf, sizeof(buf), "%s F", current_contact.name);
    } else if (path_hops == 0) {
      snprintf(buf, sizeof(buf), "%s D", current_contact.name);
    } else {
      snprintf(buf, sizeof(buf), "%s %uH", current_contact.name, path_hops);
    }
    display.drawTextCentered(display.width() / 2, title_y, buf);
  } else if (current_channel_idx != -1) {
    display.drawTextCentered(display.width() / 2, title_y, current_channel.name);
  }

  display.setColor(CardputerDisplay::P_GREEN);

  const int count = (int)chat_history.count();
  const HistoryMessage *focused_msg =
      focus.target == ChatFocus::Target::TextBox ? nullptr : messageAt(focus.msg);

  // Scroll so the focused message is on screen; the text box shows the newest.
  if (!focused_msg) {
    chat_history_offset = 0;
  } else {
    if (chat_history_offset > focus.msg) {
      chat_history_offset = focus.msg;
    }
    int used = 0;
    for (int k = chat_history_offset; k <= focus.msg; k++) {
      used += messageLines(lcd, messageAt(k));
    }
    while (used > max_lines && chat_history_offset < focus.msg) {
      used -= messageLines(lcd, messageAt(chat_history_offset));
      chat_history_offset++;
    }
  }
  if (chat_history_offset > count - 1) {
    chat_history_offset = count > 0 ? count - 1 : 0;
  }

  int current_y = history_top + (max_lines - 1) * line_h;
  int total_lines_drawn = 0;

  int start_index = count - 1 - chat_history_offset;

  for (int i = start_index; i >= 0; i--) {
    if (total_lines_drawn >= max_lines) {
      break;
    }

    const HistoryMessage *msg;

    if (!chat_history.get(i, msg)) {
      break;
    }

    int text_w = display.getTextWidth(msg->text);
    int message_lines = (text_w / display.width()) + 1;

    if (total_lines_drawn + message_lines > max_lines) {
      message_lines = max_lines - total_lines_drawn;
    }

    int message_top_y = current_y - ((message_lines - 1) * line_h);

    const bool is_focused = msg == focused_msg;
    if (is_focused) { // highlight bar behind the focused message
      display.setColor(CardputerDisplay::P_BLUE);
      display.fillRect(0, message_top_y, display.width(), message_lines * line_h);
      display.setColor(CardputerDisplay::P_WHITE);
    }

    if (msg->out) {
      if (text_w <= display.width()) {
        display.setCursor(display.width() - text_w - 5, message_top_y);
      } else {
        display.setCursor(5, message_top_y);
      }
    } else {
      display.setCursor(5, message_top_y);
    }

    if ((text_w / display.width()) + 1 == message_lines) {
      display.print(msg->text); // wraps text automatically
    } else {
      display.drawTextEllipsized(5, message_top_y, display.width(), msg->text);
    }

    if (is_focused) {
      display.setColor(CardputerDisplay::P_GREEN);
    }

    current_y -= (message_lines * line_h);
    total_lines_drawn += message_lines;
  }

  int start_x = 5;
  if (focus.target == ChatFocus::Target::TextBox) { // bright border on the focused text box
    display.setColor(CardputerDisplay::P_WHITE);
    display.drawRect(0, input_y - 2, display.width(), display.height() - (input_y - 2) - 1);
  } else {
    display.drawRect(1, input_y - 2, display.width() - 1, 1);
  }

  display.setColor(CardputerDisplay::P_WHITE);

  int available_width = display.width() - start_x;

  if (chat_text_box.isEmpty()) {
    lcd.setColor(CardputerDisplay::P_GRAY);
    lcd.drawTextLeftAlign(5, input_y, "Write a message...");
  } else {
    display.drawTextLeftAlignWithScroll(start_x, input_y, available_width,
                                        chat_text_box.c_str());
  }

  if (focus.target == ChatFocus::Target::Menu && focused_msg) {
    if (focus.info) {
      renderInfo(lcd, history_top, *focused_msg);
    } else {
      renderMenu(lcd, history_top, history_bottom, focused_msg->out);
    }
  }

  return 15000;
}

void ChatPage::renderMenu(CardputerDisplay &lcd, int top, int bottom, bool own) {
  lcd.setSmallText();
  const int row = lcd.getRowHeight();
  const int pad = 4;
  int items = 0;
  int label_w = 0;
  for (int it = 0; it < ChatFocus::MENU_ITEMS; it++) {
    if (!ChatFocus::menuItemShown(it, own)) {
      continue;
    }
    items++;
    int w = lcd.getTextWidth(ChatFocus::menuLabel(it));
    label_w = std::max(label_w, w);
  }
  const int box_w = label_w + pad * 2 + 2;
  const int box_h = items * row + pad;
  const int x = lcd.width() - box_w - 4;
  const int y = std::max(0, top + ((bottom - top) - box_h) / 2);

  lcd.setColor(CardputerDisplay::P_BLACK);
  lcd.fillRect(x, y, box_w, box_h);
  lcd.setColor(CardputerDisplay::P_WHITE);
  lcd.drawRect(x, y, box_w, box_h);

  int ry = y + pad / 2;
  for (int it = 0; it < ChatFocus::MENU_ITEMS; it++) {
    if (!ChatFocus::menuItemShown(it, own)) {
      continue;
    }
    if (it == focus.menu_item) {
      lcd.setColor(CardputerDisplay::P_BLUE);
      lcd.fillRect(x + 1, ry, box_w - 2, row);
    }
    lcd.setColor(CardputerDisplay::P_WHITE);
    lcd.drawTextLeftAlign(x + pad + 1, ry, ChatFocus::menuLabel(it));
    ry += row;
  }
  lcd.setTextSize(1);
}

void ChatPage::buildInfo(const HistoryMessage &m, char *out, size_t cap) {
  size_t pos = 0;
  out[0] = '\0';

  if (!m.out) {
    if (!m.meta.has) {
      appendf(out, cap, 0, "No details");
      return;
    }
    char name[sizeof(current_contact.name)];
    if (current_channel_idx != -1) {
      if (splitChannelSender(m.text, name, sizeof(name))) {
        pos = appendf(out, cap, pos, "From %s\n", name);
      }
    } else if (current_contact_idx != -1) {
      pos = appendf(out, cap, pos, "From %s\n", current_contact.name);
    }
    char sent[16], rcvd[16];
    formatClock(m.meta.sent_ts, sent, sizeof(sent));
    formatClock(m.meta.recv_ts, rcvd, sizeof(rcvd));
    pos = appendf(out, cap, pos, "Sent %s Rcvd %s\n", sent, rcvd);
    if (m.meta.hops == 0) {
      pos = appendf(out, cap, pos, "Direct\n");
    } else if (m.meta.hops == 0xFF) {
      pos = appendf(out, cap, pos, "Routed via path\n"); // direct-routed packet: hops not carried
    } else {
      pos = appendf(out, cap, pos, "%u hop%s\n", (unsigned)m.meta.hops, m.meta.hops == 1 ? "" : "s");
    }
    pos = appendf(out, cap, pos, "SNR %.1f RSSI %d", m.meta.snr_q4 / 4.0f, (int)m.meta.rssi);
    if (m.meta.path_len > 0) {
      const uint8_t hs = m.meta.hash_size ? m.meta.hash_size : 1;
      pos = appendf(out, cap, pos, "\nPath");
      for (uint8_t k = 0; k < m.meta.path_len && (size_t)(k + 1) * hs <= MESSAGE_META_PATH_BYTES; k++) {
        pos = appendf(out, cap, pos, "%s%02X", k ? " > " : " ", m.meta.path[k * hs]);
      }
    }
    return;
  }

  const OwnMessageInfo *it = the_mesh_cp.ownMessages().find(m.own_id);
  if (!it) {
    appendf(out, cap, 0, "No details");
    return;
  }
  if (it->is_channel) {
    pos = appendf(out, cap, pos, "Heard %u repeats", (unsigned)it->repeats);
    if (it->repeater_count > 0) {
      pos = appendf(out, cap, pos, "\nBy");
      for (uint8_t r = 0; r < it->repeater_count; r++) {
        pos = appendf(out, cap, pos, " %02X", it->repeaters[r]);
      }
    }
    return;
  }
  switch (it->state) {
    case OwnMessageInfo::Delivered:
      pos = appendf(out, cap, pos, "Delivered (%lu ms)\n", (unsigned long)it->rtt_ms);
      break;
    case OwnMessageInfo::Failed:
      pos = appendf(out, cap, pos, "Failed\n");
      break;
    default:
      pos = appendf(out, cap, pos, "Pending\n");
      break;
  }
  // Route of the contact (the tracker does not record the route per message).
  if (current_contact.out_path_len == OUT_PATH_UNKNOWN) {
    pos = appendf(out, cap, pos, "Route Flood");
  } else if ((current_contact.out_path_len & 63) == 0) {
    pos = appendf(out, cap, pos, "Route Direct");
  } else {
    pos = appendf(out, cap, pos, "Route %u hops", (unsigned)(current_contact.out_path_len & 63));
  }
}

void ChatPage::renderInfo(CardputerDisplay &lcd, int top, const HistoryMessage &m) {
  char info[200];
  buildInfo(m, info, sizeof(info));

  lcd.setSmallText();
  const int row = lcd.getRowHeight();
  const int pad = 4;
  int lines = 1;
  for (const char *c = info; *c; c++) {
    if (*c == '\n') {
      lines++;
    }
  }
  const int x = 4;
  const int w = lcd.width() - 8;
  const int h = lines * row + pad * 2;
  int y = top;
  if (y + h > lcd.height() - 2) {
    y = std::max(0, lcd.height() - 2 - h);
  }

  lcd.setColor(CardputerDisplay::P_BLACK);
  lcd.fillRect(x, y, w, h);
  lcd.setColor(CardputerDisplay::P_WHITE);
  lcd.drawRect(x, y, w, h);

  int ry = y + pad;
  char *line = info;
  while (line) {
    char *nl = strchr(line, '\n');
    if (nl) {
      *nl = '\0';
    }
    lcd.drawTextEllipsized(x + pad, ry, w - pad * 2, line);
    ry += row;
    line = nl ? nl + 1 : nullptr;
  }
  lcd.setTextSize(1);
}

size_t ChatPage::maxInputLength() {
  size_t name_len = strlen(_p->getUiTask()->getNodeName());
  return name_len < MAX_MESSAGE_LENGTH ? MAX_MESSAGE_LENGTH - name_len : 0;
}

// Text box = prefix + text, cut to the input limit on a UTF-8 boundary.
void ChatPage::setTextBox(const char *prefix, const char *text) {
  char tmp[MAX_MESSAGE_LENGTH + 1];
  snprintf(tmp, sizeof(tmp), "%s%s", prefix, text); // text may alias chat_text_box
  size_t maxlen = maxInputLength();
  size_t len = strlen(tmp);
  if (len > maxlen) {
    len = maxlen;
    while (len > 0 && ((uint8_t)tmp[len] & 0xC0) == 0x80) {
      len--;
    }
    tmp[len] = '\0';
  }
  chat_text_box = tmp;
}

void ChatPage::onHistoryPushed() {
  if (focus.target == ChatFocus::Target::TextBox) {
    chat_history_offset = 0;
  } else { // keep the focused message (and the view) where it was
    focus.onMessageAdded((int)chat_history.count());
    chat_history_offset++;
  }
}

bool ChatPage::sendText(const char *text) {
  if (!text || !text[0]) {
    return false;
  }

  uint32_t ts = the_mesh_cp.getRTCClock()->getCurrentTime();
  bool sent = false;
  bool is_channel = false;

  if (current_contact_idx != -1 && current_contact.type == ADV_TYPE_CHAT) { // direct msg
    sent = the_mesh_cp.sendDirectMessage(current_contact, ts, text) != MSG_SEND_FAILED;
  } else if (current_channel_idx != -1) { // channel msg
    sent = the_mesh_cp.sendGroupMessage(ts, current_channel.channel, _p->getUiTask()->getNodeName(), text,
                                        strlen(text));
    is_channel = true;
  }

  if (!sent) {
    return false;
  }

  HistoryMessage *msg = chat_history.push_ref();
  msg->out = true;
  msg->meta = MessageMeta();
  msg->own_id = the_mesh_cp.lastOwnMessageId();
  snprintf(msg->text, MAX_MESSAGE_LENGTH, "%s", text);

  last_sent_message = text;
  onHistoryPushed();
  if (is_channel) {
    _p->getUiTask()->showAlert("Waiting for repeats...", 2000);
  }
  return true;
}

void ChatPage::sendChatMessage() {
  if (chat_text_box.isEmpty()) {
    return;
  }
  if (sendText(chat_text_box.c_str())) {
    chat_text_box.clear();
  }
}

void ChatPage::doMenuAction(ChatFocus::Action action, int msg_idx) {
  const HistoryMessage *m = messageAt(msg_idx);
  if (!m) {
    return;
  }
  switch (action) {
    case ChatFocus::Action::Reply: // channel: "@[Sender] "; DM: just focus the text box
      if (current_channel_idx != -1 && !m->out) {
        char name[sizeof(current_contact.name)];
        if (splitChannelSender(m->text, name, sizeof(name))) {
          char prefix[sizeof(name) + 8];
          snprintf(prefix, sizeof(prefix), "@[%s] ", name);
          if (!chat_text_box.startsWith(prefix)) {
            setTextBox(prefix, chat_text_box.c_str());
          }
        }
      }
      break;
    case ChatFocus::Action::Copy:
      setTextBox("", m->text);
      break;
    case ChatFocus::Action::Resend: {
      // Copy first: pushing the resent message may reuse this ring slot.
      char text[MAX_MESSAGE_LENGTH];
      snprintf(text, sizeof(text), "%s", m->text);
      sendText(text);
      break;
    }
    default:
      break;
  }
}

bool ChatPage::handleInput(Keyboard::Event &e) {
  CardputerLayout *lay = _p->getUiTask()->getBoard()->getLayout();

  if (e.modifiers.ctrl && e.key == Keyboard::KEY_T) {
    the_mesh_cp.cancelResending();
    return true;
  }

  if (the_mesh_cp.isMessageSending()) {
    return true;
  }

  const ChatFocus::Target before = focus.target;

  if (before == ChatFocus::Target::TextBox) {
    if (e.key == Keyboard::KEY_BACKSPACE) {
      Helpers::removeLastStringChar(chat_text_box);
      return true;
    }

    if (e.modifiers.ctrl && e.key == Keyboard::ARROW_UP && chat_text_box.isEmpty()) {
      chat_text_box = last_sent_message;
      return true;
    }

    // As before: ',' / '/' switch pages from an empty box (Fn types them),
    // and type in a non-empty box (Fn switches pages).
    if ((e.key == Keyboard::ARROW_LEFT || e.key == Keyboard::ARROW_RIGHT) &&
        chat_text_box.isEmpty() != e.modifiers.fn) {
      return false;
    }
  }

  ChatFocus::Key key;
  switch (e.key) {
    case Keyboard::ARROW_UP:
      key = e.modifiers.fn ? ChatFocus::Key::FnUp : ChatFocus::Key::Up;
      break;
    case Keyboard::ARROW_DOWN:
      key = ChatFocus::Key::Down;
      break;
    case Keyboard::KEY_RETURN:
      key = ChatFocus::Key::Enter;
      break;
    case Keyboard::KEY_ESC:
      key = ChatFocus::Key::Esc;
      break;
    default:
      key = ChatFocus::Key::Char;
      break;
  }

  const int msg_idx = focus.msg;
  const HistoryMessage *focused = messageAt(msg_idx);
  const ChatFocus::Action action =
      focus.press(key, chat_text_box.isEmpty(), (int)chat_history.count(), focused && focused->out);

  switch (action) {
    case ChatFocus::Action::Send:
      sendChatMessage();
      return true;
    case ChatFocus::Action::LeaveChat:
      _p->returnToLastPage();
      return true;
    case ChatFocus::Action::TypeChar: {
      const char *repl = lay->lookup(e);
      if (repl[0]) {
        if (chat_text_box.length() + strlen(repl) <= maxInputLength()) {
          chat_text_box += repl;
        }
        return true;
      }
      return false;
    }
    case ChatFocus::Action::Reply:
    case ChatFocus::Action::Copy:
    case ChatFocus::Action::Resend:
      doMenuAction(action, msg_idx);
      return true;
    default:
      break;
  }

  // Other keys on a message do nothing here; let the main screen use them
  // (',' / '/' page switching).
  if (key == ChatFocus::Key::Char && before == ChatFocus::Target::Message) {
    return false;
  }
  return true;
}

void ChatPage::selectContact(const ContactInfo &contact, int idx) {
  current_contact = contact;
  current_contact_idx = idx;
  current_channel_idx = -1;
  the_mesh_cp.loadMessageHistory(contact.id.pub_key, false, chat_history);
  chat_history_offset = 0;
  focus = ChatFocus();

  const HistoryMessage *msg;
  for (size_t i = chat_history.count(); i > 0; i--) {
    if (chat_history.get(i - 1, msg) && msg->out) {
      last_sent_message = msg->text;
      break;
    }
  }
}

void ChatPage::selectChannel(const ChannelDetails &channel, int idx) {
  current_channel = channel;
  current_channel_idx = idx;
  current_contact_idx = -1;
  the_mesh_cp.loadMessageHistory(channel.channel.secret, true, chat_history);
  chat_history_offset = 0;
  focus = ChatFocus();

  const HistoryMessage *msg;
  for (size_t i = chat_history.count(); i > 0; i--) {
    if (chat_history.get(i - 1, msg) && msg->out) {
      last_sent_message = msg->text;
      break;
    }
  }
}

bool ChatPage::onChannelMessageRecv(const mesh::GroupChannel &channel, const char *text) {

  if (current_channel_idx != -1 &&
      memcmp(channel.secret, current_channel.channel.secret, CONTACT_LOOKUP_BYTES) == 0) {
    HistoryMessage *msg = chat_history.push_ref();
    msg->out = false;
    msg->meta = the_mesh_cp.lastReceivedMeta();
    msg->own_id = 0;
    snprintf(msg->text, MAX_MESSAGE_LENGTH, "%s", text);
    onHistoryPushed();
    return true;
  }
  return false;
}

bool ChatPage::onContactMessageRecv(const ContactInfo &contact, const char *text) {
  if (current_contact_idx != -1 &&
      memcmp(contact.id.pub_key, current_contact.id.pub_key, CONTACT_LOOKUP_BYTES) == 0) {
    HistoryMessage *msg = chat_history.push_ref();
    msg->out = false;
    msg->meta = the_mesh_cp.lastReceivedMeta();
    msg->own_id = 0;
    snprintf(msg->text, MAX_MESSAGE_LENGTH, "%s", text);
    onHistoryPushed();
    return true;
  }
  return false;
}

void ChatPage::refreshSelectedContact() {
  if (current_contact_idx != -1) {
    ContactInfo c;
    if (the_mesh_cp.getContactByIdx(current_contact_idx, c)) {
      current_contact = c;
    }
  }
}
