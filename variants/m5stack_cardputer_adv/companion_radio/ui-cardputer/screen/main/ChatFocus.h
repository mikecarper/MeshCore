#pragma once

// Pure (host-testable) focus / message-menu state machine for ChatPage.
//
// Exactly one thing has focus: the text box, a history message, or the
// message menu opened on a message. On the Cardputer Up is the ';' key and
// Down is the '.' key, so in the text box they type characters; Up leaves the
// box only when it is empty, or with Fn+Up (FnUp) at any time. Enter on a
// message never sends: it opens the menu.

#include <stdint.h>

struct ChatFocus {
  enum class Target : uint8_t { TextBox, Message, Menu };
  enum class Action : uint8_t { None, Send, OpenMenu, Reply, Info, Resend, Copy, LeaveChat, TypeChar };
  enum class Key : uint8_t { Up, Down, Enter, Esc, Char, FnUp };

  enum MenuItem : uint8_t { MENU_REPLY = 0, MENU_INFO = 1, MENU_RESEND = 2, MENU_COPY = 3, MENU_ITEMS = 4 };

  Target target = Target::TextBox;
  int msg = -1;       // focused message, index from newest (0 = newest); -1 with the text box
  int menu_item = 0;  // MenuItem; MENU_RESEND is skipped for received messages
  bool info = false;  // Menu target only: the Info view of menu_item is shown

  static const char *menuLabel(int item) {
    switch (item) {
      case MENU_REPLY: return "Reply";
      case MENU_INFO: return "Info";
      case MENU_RESEND: return "Resend";
      case MENU_COPY: return "Copy";
      default: return "";
    }
  }
  static bool menuItemShown(int item, bool own) { return item != MENU_RESEND || own; }

  void focusTextBox() {
    target = Target::TextBox;
    msg = -1;
    info = false;
  }

  // A message was appended to the history (count = new history size): keep
  // the focus on the same message. If the full ring evicted the focused
  // message, close any menu / Info and return to the text box so no action
  // can target a different message than the one chosen.
  void onMessageAdded(int message_count) {
    if (target == Target::TextBox) {
      return;
    }
    msg++;
    if (msg > message_count - 1) {
      focusTextBox();
    }
  }

  Action press(Key k, bool text_empty, int message_count, bool focused_is_own) {
    if (target != Target::TextBox && message_count <= 0) {
      focusTextBox();
      return Action::None;
    }
    if (target != Target::TextBox && msg > message_count - 1) {
      msg = message_count - 1;
    }

    switch (target) {
      case Target::TextBox:
        switch (k) {
          case Key::Up:
            if (!text_empty) {
              return Action::TypeChar;
            }
            [[fallthrough]]; // empty box: same as Fn+Up
          case Key::FnUp:
            if (message_count > 0) {
              target = Target::Message;
              msg = 0;
            }
            return Action::None;
          case Key::Down:
          case Key::Char:
            return Action::TypeChar;
          case Key::Enter:
            return Action::Send;
          case Key::Esc:
            return Action::LeaveChat;
        }
        return Action::None;

      case Target::Message:
        switch (k) {
          case Key::Up:
          case Key::FnUp:
            if (msg < message_count - 1) {
              msg++;
            }
            return Action::None;
          case Key::Down:
            if (msg <= 0) {
              focusTextBox();
            } else {
              msg--;
            }
            return Action::None;
          case Key::Enter:
            target = Target::Menu;
            menu_item = MENU_REPLY;
            info = false;
            return Action::OpenMenu;
          case Key::Esc:
            focusTextBox();
            return Action::None;
          case Key::Char:
            return Action::None;
        }
        return Action::None;

      case Target::Menu:
        if (info) { // Info view: Esc or Enter closes it back to the list
          if (k == Key::Esc || k == Key::Enter) {
            info = false;
          }
          return Action::None;
        }
        switch (k) {
          case Key::Up:
          case Key::FnUp:
          case Key::Down: {
            int step = k == Key::Down ? 1 : MENU_ITEMS - 1;
            do {
              menu_item = (menu_item + step) % MENU_ITEMS;
            } while (!menuItemShown(menu_item, focused_is_own));
            return Action::None;
          }
          case Key::Enter:
            switch (menu_item) {
              case MENU_REPLY:
                focusTextBox();
                return Action::Reply;
              case MENU_INFO:
                info = true;
                return Action::Info;
              case MENU_RESEND:
                if (!focused_is_own) {
                  return Action::None;
                }
                target = Target::Message;
                return Action::Resend;
              case MENU_COPY:
                focusTextBox();
                return Action::Copy;
            }
            return Action::None;
          case Key::Esc:
            target = Target::Message;
            return Action::None;
          case Key::Char:
            return Action::None;
        }
        return Action::None;
    }
    return Action::None;
  }
};
