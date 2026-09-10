#pragma once
#include <helpers/CompanionJohn.h>
#include <helpers/bible/JohnReader.h>
#include <helpers/ui/ReaderNavigationHint.h>
#if UI_SMALL_MESSAGE_FONT || UI_BUTTON_READER_HINT
#include <helpers/ui/SmallMessageText.h>
#endif

// Included by UITask.cpp after the task and MyMesh declarations. Only a small
// cursor/checkpoint object persists; the 2 KiB decode buffer is on demand.
class JohnReaderScreen : public UIScreen {
  UITask* _task;
  DisplayDriver* _display;
  mesh::bible::ReaderBookmark _bookmark;
  uint32_t _retry_at = 0;
  bool _loaded = false;
#if UI_READER_TOUCH_BAR
  mesh::ui::TouchNavigationBar _touch_bar;
#endif

  __attribute__((noinline)) void process(int direction, bool draw) {
    using namespace mesh::bible;
    char scratch[kBlockSize];
    const char* text = nullptr;
    Position pos = _bookmark.position();
    _display->setCompactText(false);
    _display->setTextSize(1);
    const int width = _display->width();
    const int header_height = _display->textLineHeight();
    // Narrow/rotated panels need separate reference and progress lines.
    const bool stacked_header = width < _display->getTextWidth("88:88")
        + _display->getTextWidth("88/88") + 4;
    const int header_content_height = header_height * (stacked_header ? 2 : 1);
#if UI_READER_TOUCH_BAR
    const int padded_header_height = mesh::ui::readerTouchHeaderHeight(header_content_height);
#else
    const int padded_header_height = header_content_height;
#endif
    const int header_text_y = (padded_header_height - header_content_height) / 2;
    const int top = padded_header_height + 2;
#if UI_SMALL_MESSAGE_FONT || UI_BUTTON_READER_HINT
    // Share message font selection and metrics, including rotated/tiny panels.
    mesh::ui::SmallMessageText compact(*_display);
#endif
    int bottom = _display->height();
#if UI_BUTTON_READER_HINT
    // Keep the hint compact on tiny displays even if the body font was
    // overridden. Reserve it before pagination so no verse text is hidden.
    const bool small_hint = _display->useSmallMessageFont();
    DisplayDriver& hint_text = small_hint
        ? static_cast<DisplayDriver&>(compact) : *_display;
#if UI_READER_TOUCH_BAR
    _touch_bar = mesh::ui::makeReaderTouchBar(hint_text, bottom, top - 1);
    bottom = _touch_bar.top;
#else
    const auto hint = mesh::ui::makeButtonReaderHintLayout(hint_text,
        small_hint ? compact.glyphHeight() : header_height, bottom,
        (millis() / 3000U) % 2 != 0);
    bottom = hint.top;
#endif
#endif
#if UI_SMALL_MESSAGE_FONT
    const bool small = _display->useSmallMessageFont();
    DisplayDriver& body = small ? static_cast<DisplayDriver&>(compact) : *_display;
    const int line_height = small ? compact.lineHeight() : header_height;
    int rows = small ? compact.lineCount(top, bottom) : (bottom - top) / line_height;
#else
    DisplayDriver& body = *_display;
    const int line_height = header_height;
    int rows = (bottom - top) / line_height;
#endif
    if (rows < 1) rows = 1;
    auto measure = [&body](const char* line) {
      char filtered[kReaderLineBytes];
      body.translateUTF8ToBlocks(filtered, line, sizeof(filtered));
      return body.getTextWidth(filtered);
    };

    // At most two lookups: the current verse, then its adjacent verse when
    // navigation crosses a boundary. No recursive decode or second buffer.
    for (int pass = 0; pass < 2; ++pass) {
      const LookupResult result = mesh::readJohnVerse(
          referenceAt(pos.verse), scratch, sizeof(scratch), text);
      if (result != LookupResult::Found) {
        if (draw) {
          body.setColor(UIColor::warning_txt);
          body.drawTextEllipsized(0, top, width, "John unavailable");
#if UI_BUTTON_READER_HINT
#if UI_READER_TOUCH_BAR
          mesh::ui::drawReaderTouchBar(hint_text, _touch_bar);
#else
          mesh::ui::drawButtonReaderHint(hint_text, hint);
#endif
#endif
        } else _task->showAlert("John data error", 1500);
        return;
      }
      memmove(scratch, text, strlen(text) + 1);
      const ReaderPage page = readerPage(scratch, pos.offset, width, rows, measure);
      pos.offset = page.start; // clamp stale bookmarks after a layout change
      if (direction > 0) {
        if (scratch[page.next]) pos.offset = page.next;
        else if (pos.verse + 1 < kVerseCount) { ++pos.verse; pos.offset = 0; }
        direction = 0;
        continue;
      }
      if (direction < 0) {
        if (page.start) pos.offset = page.previous;
        else if (pos.verse) { --pos.verse; pos.offset = kBlockSize - 1; }
        direction = 0;
        continue;
      }
      _bookmark.move(pos, millis());
      if (!draw) return;
      char label[32], progress[16];
      const Reference ref = referenceAt(pos.verse);
      snprintf(label, sizeof(label), "John %u:%u", ref.chapter, ref.verse);
      snprintf(progress, sizeof(progress), "%u/%u", page.part, page.parts);
      const int progress_width = _display->getTextWidth(progress);
      if (stacked_header || _display->getTextWidth(label) > width - progress_width - 4)
        snprintf(label, sizeof(label), "%u:%u", ref.chapter, ref.verse);
      _display->setColor(UIColor::title_txt);
      _display->drawTextEllipsized(0, header_text_y, stacked_header ? width : width - progress_width - 4, label);
      _display->drawTextRightAlign(width, header_text_y + (stacked_header ? header_height : 0), progress);
      _display->drawRect(0, top - 2, width, 1);
      body.setColor(UIColor::primary_txt);
      uint16_t offset = page.start;
      for (int row = 0; row < rows && scratch[offset]; ++row) {
        char line[kReaderLineBytes], filtered[kReaderLineBytes];
        offset = readerLine(scratch, offset, width, measure, line);
        body.translateUTF8ToBlocks(filtered, line, sizeof(filtered));
        body.setCursor(0, top + row * line_height);
        body.print(filtered);
      }
#if UI_BUTTON_READER_HINT
#if UI_READER_TOUCH_BAR
      mesh::ui::drawReaderTouchBar(hint_text, _touch_bar);
#else
      mesh::ui::drawButtonReaderHint(hint_text, hint);
#endif
#endif
      return;
    }
  }

public:
#if UI_READER_TOUCH_BAR
  const mesh::ui::TouchNavigationBar* readerTouchBar() const { return &_touch_bar; }
#endif
  JohnReaderScreen(UITask* task, DisplayDriver* display) : _task(task), _display(display) {}
  void open() {
    if (!_loaded) {
      mesh::bible::Position pos;
      the_mesh.loadJohnBookmark(pos);
      _bookmark.restore(pos);
      _loaded = true;
    }
  }
  bool flush() {
    if (!_bookmark.dirty()) return true;
    if (!the_mesh.saveJohnBookmark(_bookmark.position())) {
      _retry_at = millis() + 30000;
      return false;
    }
    _bookmark.saved();
    _retry_at = 0;
    return true;
  }
  void poll() override {
    const uint32_t now = millis();
    if (_bookmark.due(now) && (!_retry_at || int32_t(now - _retry_at) >= 0)) flush();
  }
  int render(DisplayDriver&) override { process(0, true); return 1000; }
  bool handleInput(char c) override {
    const uint8_t key = static_cast<uint8_t>(c);
    if (key == KEY_NEXT || key == KEY_RIGHT) { process(1, false); return true; }
    if (key == KEY_PREV || key == KEY_LEFT) { process(-1, false); return true; }
    if (key == KEY_DOWN || key == KEY_UP) {
      using namespace mesh::bible;
      const Reference current = referenceAt(_bookmark.position().verse);
      const int chapter = current.chapter + (key == KEY_DOWN ? 1 : -1);
      Position pos;
      if (chapter >= 1 && chapter <= int(sizeof(kChapterVerses))
          && verseNumber({static_cast<uint8_t>(chapter), 1}, pos.verse)) {
        _bookmark.move(pos, millis());
      }
      return true;
    }
    if (key == KEY_ENTER || key == KEY_CANCEL) { _task->closeJohnReader(); return true; }
    return false;
  }
};
