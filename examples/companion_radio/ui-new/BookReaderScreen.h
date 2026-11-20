#pragma once

#include "../BookLibrary.h"
#include <helpers/bible/Reader.h>

class ReaderScreen : public UIScreen {
  enum Level { Books, Chapters, Passages, Text };
  UITask* _task;
  DisplayDriver* _display;
  Level _level = Books;
  uint8_t _slots[mesh::books::kSlots];
  unsigned _count = 0, _book = 0, _chapter = 0, _passage = 0;
  uint16_t _offset = 0, _previous = 0, _next = 0;
  bool _ok = false;
  mesh::books::Book _selected;
  mesh::books::Chapter _section;
  char _text[mesh::books::kPlain + 1];
#if UI_READER_TOUCH_BAR
  mesh::ui::TouchNavigationBar _touch_bar;
#endif

  void read() {
    _offset = _next = _previous = 0;
    _ok = mesh::books::library.passage(the_mesh.readerStore(), _slots[_book],
                                       _section.first + _passage, _text);
  }

  void back() {
    if (_level == Books) _task->closeReader();
    else if (_level == Chapters) open();
    else _level = Level(_level - 1);
  }

  static void move(unsigned& index, unsigned count, bool forward, bool jump) {
    if (!count) return;
    if (!jump) index = (index + (forward ? 1 : count - 1)) % count;
    else if (forward) index = index + 10 < count ? index + 10 : count - 1;
    else index = index > 10 ? index - 10 : 0;
  }

public:
  ReaderScreen(UITask* task, DisplayDriver* display) : _task(task), _display(display) {}
  bool flush() { return true; }
  void open() {
    _level = Books;
    _count = _book = 0;
    mesh::books::Book book;
    for (unsigned slot = 0; slot < mesh::books::kSlots; ++slot)
      if (mesh::books::library.book(the_mesh.readerStore(), slot, book)) _slots[_count++] = slot;
  }
#if UI_READER_TOUCH_BAR
  const mesh::ui::TouchNavigationBar* readerTouchBar() const { return &_touch_bar; }
#endif

  bool handleInput(char c) override {
    const unsigned key = static_cast<uint8_t>(c);
    if (key == KEY_CANCEL) { back(); return true; }
    bool select = key == KEY_ENTER || key == KEY_SELECT;
    const bool previous = key == KEY_PREV || key == KEY_LEFT || key == KEY_UP;
    const bool next = key == KEY_NEXT || key == KEY_RIGHT || key == KEY_DOWN;
#if defined(PIN_USER_BTN) && !defined(UI_HAS_NAV_INPUT) && !UI_HAS_JOYSTICK && !UI_READER_TOUCH_BAR
    // One button: click to advance, double-click to select, hold to go back.
    select |= _level != Text && key == KEY_PREV;
#endif
    if (select) {
      auto& library = mesh::books::library;
      auto& store = the_mesh.readerStore();
      if (_level == Books && _count) {
        if (library.book(store, _slots[_book], _selected)) { _chapter = 0; _level = Chapters; }
        else open();
      } else if (_level == Chapters) {
        if (library.chapter(store, _slots[_book], _chapter, _section)) { _passage = 0; _level = Passages; }
        else open();
      } else if (_level == Passages) { _level = Text; read(); }
      else if (_level == Text) back();
    } else if (next || previous) {
      const bool jump = key == KEY_UP || key == KEY_DOWN;
      if (_level == Books) move(_book, _count, next, jump);
      else if (_level == Chapters) move(_chapter, _selected.chapters, next, jump);
      else if (_level == Passages) move(_passage, _section.count, next, jump);
      else if (!jump && _ok && next && _text[_next]) _offset = _next;
      else if (!jump && _ok && previous && _offset) _offset = _previous;
      else if (next && _passage + 1 < _section.count) { ++_passage; read(); }
      else if (previous && _passage) { --_passage; read(); }
    } else return false;
    return true;
  }

  int render(DisplayDriver&) override {
    using mesh::bible::kReaderLineBytes;
    auto& display = *_display;
    display.setCompactText(false);
    display.setTextSize(1);
    const int lineHeight = display.textLineHeight();
    const int width = display.width();
    int top = lineHeight + 2, bottom = display.height();
#if UI_READER_TOUCH_BAR
    top = mesh::ui::readerTouchHeaderHeight(lineHeight);
    _touch_bar = mesh::ui::makeReaderTouchBar(display, bottom, top);
    _touch_bar.header_back = true;
    bottom = _touch_bar.top;
    const char* buttons[] = {"<<", "<", ">", ">>", "OK"};
    display.setColor(UIColor::secondary_txt);
    for (int i = 0; i < 5; ++i)
      display.drawTextCentered(width * (2 * i + 1) / 10, bottom + 3, buttons[i]);
#endif
    char header[28];
    if (_level == Books) snprintf(header, sizeof(header), "Reader / Books");
    else if (_level == Chapters) snprintf(header, sizeof(header), "Chapters %u/%u", _chapter + 1, _selected.chapters);
    else if (_level == Passages) snprintf(header, sizeof(header), "Passages %u/%u", _passage + 1, _section.count);
    else snprintf(header, sizeof(header), "Passage %u/%u", _passage + 1, _section.count);
    display.setColor(UIColor::secondary_txt);
    display.drawTextEllipsized(0, 0, width, header);
    display.setColor(UIColor::primary_txt);

    char label[kReaderLineBytes];
    char filtered[kReaderLineBytes];
    const int rows = bottom > top ? (bottom - top) / lineHeight : 0;
    if (_level != Text) {
      const unsigned count = _level == Books ? _count : _level == Chapters ? _selected.chapters : _section.count;
      const unsigned index = _level == Books ? _book : _level == Chapters ? _chapter : _passage;
      for (unsigned i = 0; i < unsigned(rows) && i < count - index; ++i) {
        auto& library = mesh::books::library;
        auto& store = the_mesh.readerStore();
        mesh::books::Book book;
        mesh::books::Chapter chapter;
        const char* name = nullptr;
        if (_level == Books && library.book(store, _slots[index + i], book)) name = book.title;
        if (_level == Chapters && library.chapter(store, _slots[_book], index + i, chapter)) name = chapter.title;
        if (name) snprintf(label, sizeof(label), "%c %s", i ? ' ' : '>', name);
        else snprintf(label, sizeof(label), "%c %u", i ? ' ' : '>', index + i + 1);
        display.translateUTF8ToBlocks(filtered, label, sizeof(filtered));
        display.drawTextEllipsized(0, top + i * lineHeight, width, filtered);
      }
      if (!count && rows) display.drawTextEllipsized(0, top, width, "No books loaded");
    } else if (!_ok) display.drawTextEllipsized(0, top, width, "Passage unavailable");
    else {
      auto measure = [&](const char* line) {
        display.translateUTF8ToBlocks(filtered, line, sizeof(filtered));
        return display.getTextWidth(filtered);
      };
      const auto page = mesh::bible::readerPage(_text, _offset, width, rows, measure);
      _previous = page.previous;
      _next = page.next;
      uint16_t cursor = page.start;
      for (int i = 0; i < rows && cursor < _next; ++i) {
        cursor = mesh::bible::readerLine(_text, cursor, width, measure, label);
        display.translateUTF8ToBlocks(filtered, label, sizeof(filtered));
        display.drawTextEllipsized(0, top + i * lineHeight, width, filtered);
      }
    }
    return 1000;
  }
};
