#include <helpers/CompanionJohn.h>
#include <helpers/bible/JohnBookmarkFiles.h>
#include <helpers/ui/UIScreen.h>
#include <cassert>
#include <cstdio>
#include <map>
#include <string>
#include <vector>
#include <algorithm>

using namespace mesh::bible;
static uint32_t test_now = 100;
uint32_t millis() { return test_now; }
ColorVal UIColor::window_bkg = 0, UIColor::title_bkg = 0, UIColor::title_txt = 1;
ColorVal UIColor::primary_txt = 1, UIColor::secondary_txt = 1, UIColor::warning_txt = 1;
ColorVal UIColor::popup_bkg = 0, UIColor::popup_txt = 1, UIColor::corp_blue = 1;

struct Files {
  std::map<std::string, std::vector<uint8_t>> data;
  int calls = 0, fail_at = -1;
  bool fail() { return calls++ == fail_at; }
  bool exists(const char* p) { return data.count(p); }
  bool remove(const char* p) {
    if (fail()) return false;
    data.erase(p); return true;
  }
  bool rename(const char* a, const char* b) {
    if (fail() || !exists(a) || exists(b)) return false;
    data[b] = data[a]; data.erase(a); return true;
  }
  bool read(const char* p, uint8_t (&out)[kBookmarkBytes]) {
    if (fail() || !exists(p)) return false;
    if (data[p].size() != sizeof(out)) { memset(out, 0, sizeof(out)); return true; }
    memcpy(out, data[p].data(), sizeof(out)); return true;
  }
  bool write(const char* p, const uint8_t (&in)[kBookmarkBytes]) {
    if (fail()) { data[p] = std::vector<uint8_t>(in, in + 3); return false; }
    data[p] = std::vector<uint8_t>(in, in + sizeof(in)); return true;
  }
};
struct FakeMesh {
  Files files;
  int saves = 0;
  bool loadJohnBookmark(Position& p) { return loadReaderBookmark(files, p); }
  bool saveJohnBookmark(Position p) { ++saves; return saveReaderBookmark(files, p); }
} the_mesh;

class UITask {
public:
  bool closed = false;
  std::string alert;
  void closeJohnReader() { closed = true; }
  void showAlert(const char* text, int) { alert = text; }
};
#include "../../../examples/companion_radio/ui-new/JohnReaderScreen.h"

class Display : public DisplayDriver {
  int x = 0, y = 0;
  ColorVal color = 1;
public:
  struct Line { int x, y; std::string text; };
  std::vector<Line> lines;
  std::vector<uint8_t> pixels;
  Display(int w, int h) : DisplayDriver(w, h), pixels(w * h, 0) {}
  int bodyY() const {
    const int content_height = width() < 64 ? 20 : 10;
#if UI_READER_TOUCH_BAR
    return mesh::ui::readerTouchHeaderHeight(content_height) + 2;
#else
    return content_height + 2;
#endif
  }
  int bodyBottom() {
#if UI_READER_TOUCH_BAR
    return mesh::ui::makeReaderTouchBar(*this, height()).top;
#elif UI_BUTTON_READER_HINT
    mesh::ui::SmallMessageText compact(*this);
    const bool small = useSmallMessageFont();
    DisplayDriver& hint_text = small ? static_cast<DisplayDriver&>(compact) : *this;
    return mesh::ui::makeButtonReaderHintLayout(hint_text,
        small ? compact.glyphHeight() : 10, height()).top;
#else
    return height();
#endif
  }
  void resize(int w, int h) { setDimensions(w, h); pixels.assign(w * h, 0); clear(); }
  bool isOn() override { return true; }
  void turnOn() override {}
  void turnOff() override {}
  void clear() override { lines.clear(); std::fill(pixels.begin(), pixels.end(), 0); }
  void startFrame(ColorVal = 0) override { clear(); }
  void endFrame() override {}
  void setTextSize(int) override {}
  void setColor(ColorVal value) override { color = value; }
  void setCursor(int a, int b) override { x = a; y = b; }
  uint16_t getTextWidth(const char* str) override { return strlen(str) * 6; }
  void print(const char* str) override {
    assert(x >= 0 && x + getTextWidth(str) <= width());
    assert(y >= 0 && y + 8 <= height());
    lines.push_back({x, y, str});
  }
  void fillRect(int a, int b, int w, int h) override {
    assert(a >= 0 && b >= 0 && a + w <= width() && b + h <= height());
    for (int row = b; row < b + h; ++row)
      for (int col = a; col < a + w; ++col) pixels[row * width() + col] = color != 0;
  }
  void drawRect(int a, int b, int w, int h) override {
    assert(a >= 0 && b >= 0 && a + w <= width() && b + h <= height());
    fillRect(a, b, w, 1); fillRect(a, b + h - 1, w, 1);
    fillRect(a, b, 1, h); fillRect(a + w - 1, b, 1, h);
  }
  void drawXbm(int, int, const uint8_t*, int, int) override {}
  std::string body() {
#if UI_SMALL_MESSAGE_FONT
    if (useSmallMessageFont())
      return std::string(pixels.begin() + bodyY() * width(), pixels.begin() + bodyBottom() * width());
#endif
    std::string result;
    for (const auto& line : lines)
      if (line.y >= bodyY() && line.y < bodyBottom()) result += line.text;
    return result;
  }
  void dump() const {
    for (const auto& line : lines)
      std::printf("%d\t%d\t%s\n", line.x, line.y, line.text.c_str());
    for (int row = 0; row < height(); ++row)
      for (int col = 0; col < width(); ++col)
        if (pixels[row * width() + col]) std::printf("PIXEL %d %d\n", col, row);
  }
};

static std::string noSpaces(std::string text) {
  text.erase(std::remove(text.begin(), text.end(), ' '), text.end()); return text;
}
static Position pos(uint16_t verse, uint16_t offset = 0) {
  Position p; p.verse = verse; p.offset = offset; return p;
}

int main(int argc, char** argv) {
  if (argc > 1) {
    if (argc > 2) assert(std::freopen(argv[2], "w", stdout));
    Display display(128, 64);
    UITask task;
    JohnReaderScreen first(&task, &display);
    first.open(); first.render(display);
    std::puts("FRAME 128 64 John 1:1");
    display.dump();
    assert(the_mesh.saveJohnBookmark(pos(91))); // John 3:16
    JohnReaderScreen verse(&task, &display);
    verse.open();
    for (int part = 0; part < (UI_SMALL_MESSAGE_FONT ? 1 : 2); ++part) {
      display.clear(); verse.render(display);
      std::printf("FRAME 128 64 John 3:16 part %d\n", part + 1);
      display.dump();
      verse.handleInput(KEY_NEXT);
    }
#if UI_SMALL_MESSAGE_FONT
    Display tiny(72, 40);
    assert(the_mesh.saveJohnBookmark(pos(91)));
    JohnReaderScreen tiny_verse(&task, &tiny);
    tiny_verse.open(); tiny_verse.render(tiny);
    std::puts("FRAME 72 40 John 3:16 on a tiny screen (5px)");
    tiny.dump();
    tiny.resize(40, 72);
    tiny_verse.render(tiny);
    std::puts("FRAME 40 72 Same reader rotated (5px)");
    tiny.dump();
#endif
    return 0;
  }
  // Every verse/part, in both directions, at common small-screen sizes.
  const int cases[][3] = {
    {128,64,6}, {64,128,6}, {160,80,0}, {80,160,0}, {240,135,0},
    {159,80,6}, {80,159,6}, {320,240,0}, {240,320,0}, {250,122,0}, {200,200,0},
    {72,40,5}, {40,72,5}, {128,32,5}, {32,128,5}, {64,48,5}, {48,64,5},
    {160,160,0},
  };
  for (const auto& dimensions : cases) {
#if UI_READER_TOUCH_BAR
    if (dimensions[0] != 160 || dimensions[1] != 160) continue;
#endif
    Display display(dimensions[0], dimensions[1]);
    Display expected(dimensions[0], dimensions[1]);
    const int top = expected.bodyY();
#if UI_SMALL_MESSAGE_FONT
    mesh::ui::SmallMessageText compact(expected);
    const bool small = dimensions[2] != 0;
    assert(expected.useSmallMessageFont() == small);
    if (small) assert(compact.capitalHeight() == dimensions[2]);
    DisplayDriver& expected_text = small ? static_cast<DisplayDriver&>(compact) : expected;
    const int line_height = small ? compact.lineHeight() : 10;
    const int rows = small ? compact.lineCount(top, expected.bodyBottom())
                          : (expected.bodyBottom() - top) / line_height;
#else
    DisplayDriver& expected_text = expected;
    const int line_height = 10;
    const int rows = (display.bodyBottom() - top) / line_height;
#endif
    auto measure = [&expected_text](const char* line) { return expected_text.getTextWidth(line); };
    UITask task;
    the_mesh = FakeMesh{};
    JohnReaderScreen screen(&task, &display);
    screen.open();
    assert(screen.flush() && the_mesh.saves == 0); // don't create John 1:1
#if UI_BUTTON_READER_HINT
    display.clear(); screen.render(display);
    Display footer(display.width(), display.height());
    mesh::ui::SmallMessageText hint_compact(footer);
    const bool small_hint = footer.useSmallMessageFont();
    DisplayDriver& hint_text = small_hint
        ? static_cast<DisplayDriver&>(hint_compact) : footer;
#if UI_READER_TOUCH_BAR
    const auto hint = mesh::ui::makeReaderTouchBar(hint_text, footer.height());
    mesh::ui::drawReaderTouchBar(hint_text, hint);
#else
    const auto hint = mesh::ui::makeButtonReaderHintLayout(hint_text,
        small_hint ? hint_compact.glyphHeight() : 10, footer.height());
    mesh::ui::drawButtonReaderHint(hint_text, hint);
#endif
    if (small_hint) {
      // Compare the real reader's hint pixels independently of the body.
      auto first = footer.pixels.begin() + hint.top * footer.width();
      assert(std::find(first, footer.pixels.end(), 1) != footer.pixels.end());
      assert(std::equal(first, footer.pixels.end(),
                        display.pixels.begin() + hint.top * display.width()));
    } else {
      for (const auto& hint_line : footer.lines) {
        assert(std::any_of(display.lines.begin(), display.lines.end(),
            [&hint_line](const Display::Line& line) {
              return line.x == hint_line.x && line.y == hint_line.y
                  && line.text == hint_line.text;
            }));
      }
    }
#endif
    std::vector<std::string> expected_pages;
    char scratch[kBlockSize];
    for (uint16_t verse = 0; verse < kVerseCount; ++verse) {
      const char* text;
      assert(mesh::readJohnVerse(referenceAt(verse), scratch, sizeof(scratch), text) == LookupResult::Found);
      memmove(scratch, text, strlen(text) + 1);
      std::string rebuilt;
      uint16_t offset = 0;
      do {
        const auto page = readerPage(scratch, offset, display.width(), rows, measure);
        assert(page.start == offset && page.next > offset);
        expected.clear();
        uint16_t next = offset;
        for (int row = 0; row < rows && scratch[next]; ++row) {
          char line[kReaderLineBytes];
          next = readerLine(scratch, next, display.width(), measure, line);
          rebuilt += line;
          expected_text.setCursor(0, top + row * line_height);
          expected_text.print(line);
        }
        assert(next == page.next);
        display.clear(); screen.render(display);
        const std::string body = display.body();
        assert(!body.empty());
        assert(body == expected.body());
        assert(display.getTextWidth("ABC") == 18); // header/driver font unchanged
        expected_pages.push_back(body);
        screen.handleInput(KEY_NEXT);
        offset = page.next;
      } while (scratch[offset]);
      if (noSpaces(rebuilt) != noSpaces(scratch)) {
        std::fprintf(stderr, "%dx%d verse index %u\nExpected: %s\nActual: %s\n",
                     display.width(), display.height(), verse, scratch, rebuilt.c_str());
        return 1;
      }
    }
    display.clear(); screen.render(display);
    assert(display.body() == expected_pages.back()); // no wrap at end
    for (size_t page = expected_pages.size(); page > 0; --page) {
      display.clear(); screen.render(display);
      assert(display.body() == expected_pages[page - 1]);
      screen.handleInput(KEY_PREV);
    }
    display.clear(); screen.render(display);
    assert(display.body() == expected_pages.front()); // no wrap before start
    screen.handleInput(KEY_ENTER);
    assert(task.closed);
#if UI_READER_TOUCH_BAR
    task.closed = false;
    mesh::ui::TouchInput input(true, true, 70, true, false);
    auto touch_gesture = [&](int sx, int sy, int ex, int ey) {
      display.clear(); screen.render(display);
      const auto* bar = screen.readerTouchBar();
      input.update(true, display.width()-1-sx, sy, display.width(), display.height(), false, nullptr, bar);
      input.update(true, display.width()-1-ex, ey, display.width(), display.height(), false, nullptr, bar);
      input.update(false, -1, -1, display.width(), display.height(), false, nullptr, bar);
      const auto action = input.update(false, -1, -1, display.width(), display.height(), false, nullptr, bar);
      switch (action) {
        case mesh::ui::TouchAction::Next: screen.handleInput(KEY_NEXT); break;
        case mesh::ui::TouchAction::Previous: screen.handleInput(KEY_PREV); break;
        case mesh::ui::TouchAction::VerticalNext: screen.handleInput(KEY_DOWN); break;
        case mesh::ui::TouchAction::VerticalPrevious: screen.handleInput(KEY_UP); break;
        case mesh::ui::TouchAction::Select: screen.handleInput(KEY_ENTER); break;
        default: assert(false);
      }
    };
    auto tap = [&](int cell) {
      const int x = display.width() * (2 * cell + 1) / 10;
      touch_gesture(x, screen.readerTouchBar()->top, x, screen.readerTouchBar()->top);
    };
    tap(2); assert(screen.flush());
    Position saved; assert(the_mesh.loadJohnBookmark(saved) && saved.verse == 1);
    tap(1); assert(screen.flush());
    assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());
    tap(3); assert(screen.flush());
    assert(the_mesh.loadJohnBookmark(saved) && referenceAt(saved.verse).chapter == 2);
    tap(0); assert(screen.flush());
    assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());
    tap(4); assert(task.closed);
    task.closed = false;
    touch_gesture(120,70,120,70); assert(screen.flush());
    assert(the_mesh.loadJohnBookmark(saved) && saved.verse == 1);
    touch_gesture(40,70,40,70); assert(screen.flush());
    assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());
    touch_gesture(120,70,40,70); assert(screen.flush());
    assert(the_mesh.loadJohnBookmark(saved) && saved.verse == 1);
    touch_gesture(40,70,120,70); assert(screen.flush());
    assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());
    touch_gesture(80,100,80,50); assert(screen.flush());
    assert(the_mesh.loadJohnBookmark(saved) && referenceAt(saved.verse).chapter == 2);
    touch_gesture(80,50,80,100); assert(screen.flush());
    assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());
    touch_gesture(80,0,80,0); assert(task.closed);
#endif
  }
#if UI_READER_TOUCH_BAR
  return 0; // The remaining fault-injection cases use tiny non-touch viewports.
#endif

  // Group navigation starts at verse one of the adjacent chapter, resets
  // the page offset, stops at book boundaries, and checkpoints normally.
  the_mesh = FakeMesh{};
  Display chapters_display(128, 64);
  UITask chapters_task;
  JohnReaderScreen chapters(&chapters_task, &chapters_display);
  chapters.open();
  assert(chapters.handleInput(KEY_UP));
  assert(chapters.flush() && the_mesh.saves == 0);
  for (uint8_t chapter = 2; chapter <= sizeof(kChapterVerses); ++chapter) {
    assert(chapters.handleInput(KEY_DOWN));
    assert(chapters.flush());
    Position saved;
    assert(the_mesh.loadJohnBookmark(saved));
    assert(referenceAt(saved.verse).chapter == chapter && saved.offset == 0);
    assert(referenceAt(saved.verse).verse == 1);
  }
  const int writes = the_mesh.saves;
  assert(chapters.handleInput(KEY_DOWN));
  assert(chapters.flush() && the_mesh.saves == writes);
  // Moving backwards from the middle of a chapter reaches the preceding one.
  assert(the_mesh.saveJohnBookmark(pos(91, 12))); // 3:16, with a stale page offset
  JohnReaderScreen middle(&chapters_task, &chapters_display);
  middle.open();
  assert(middle.handleInput(KEY_UP) && middle.flush());
  Position chapter_saved;
  assert(the_mesh.loadJohnBookmark(chapter_saved));
  assert(referenceAt(chapter_saved.verse).chapter == 2);
  assert(referenceAt(chapter_saved.verse).verse == 1 && chapter_saved.offset == 0);
  assert(middle.handleInput(KEY_UP) && middle.flush());
  assert(!the_mesh.loadJohnBookmark(chapter_saved) && chapter_saved.atStart());

  // Resume within a split verse; independent screen and store lifecycle.
  the_mesh = FakeMesh{};
  Display display(64,32);
  UITask task;
  JohnReaderScreen first(&task, &display);
  first.open(); first.handleInput(KEY_NEXT);
  display.clear(); first.render(display); const auto body = display.body();
  first.poll(); assert(the_mesh.saves == 0);
  test_now += 2000; first.poll(); assert(the_mesh.saves == 1);
  first.poll(); assert(the_mesh.saves == 1);
  Position saved;
  assert(the_mesh.loadJohnBookmark(saved) && saved.verse == 0 && saved.offset > 0);
  JohnReaderScreen rebooted(&task, &display);
  rebooted.open(); display.clear(); rebooted.render(display);
  assert(display.body() == body);
  rebooted.handleInput(KEY_PREV); assert(rebooted.flush());
  assert(!the_mesh.files.exists("/john.pos") && !the_mesh.files.exists("/john.pos.bak"));
  assert(!the_mesh.loadJohnBookmark(saved) && saved.atStart());

#if UI_SMALL_MESSAGE_FONT
  // A bookmark from the old 6-pixel-wide font resumes on the new page that
  // contains those words; it does not advance to another verse or lose them.
  char scratch[kBlockSize];
  const char* text;
  assert(mesh::readJohnVerse({3, 16}, scratch, sizeof(scratch), text) == LookupResult::Found);
  const auto legacy_measure = [](const char* line) { return strlen(line) * 6; };
  const auto legacy = readerPage(text, 0, 128, 5, legacy_measure);
  assert(text[legacy.next]);
  the_mesh = FakeMesh{};
  assert(the_mesh.saveJohnBookmark(pos(91, legacy.next)));
  Display migrated_display(128,64);
  mesh::ui::SmallMessageText compact(migrated_display);
  const auto compact_measure = [&compact](const char* line) { return compact.getTextWidth(line); };
  const auto compact_page = readerPage(text, legacy.next, 128,
      compact.lineCount(12, migrated_display.bodyBottom()), compact_measure);
  assert(compact_page.start <= legacy.next && compact_page.next > legacy.next);
  JohnReaderScreen migrated(&task, &migrated_display);
  migrated.open(); migrated.render(migrated_display); assert(migrated.flush());
  assert(the_mesh.loadJohnBookmark(saved) && saved == pos(91, compact_page.start));
  assert(compact_page.parts == 1); // John 3:16 now fits in one V4 screen

  // Reflow an already-open reader when panel geometry crosses the 5px/6px
  // threshold, and when a tiny panel rotates into the stacked-header layout.
  for (const auto& dimensions : {std::make_pair(72,40), {128,64}, {40,72}, {64,128}, {32,128}, {160,80}, {80,160}, {159,80}}) {
    assert(the_mesh.saveJohnBookmark(pos(91, legacy.next)));
    Display changed(72,40);
    JohnReaderScreen active(&task, &changed);
    active.open(); active.render(changed); assert(active.flush());
    Position before; assert(the_mesh.loadJohnBookmark(before));
    changed.resize(dimensions.first, dimensions.second);
    mesh::ui::SmallMessageText small_font(changed);
    const bool small = changed.useSmallMessageFont();
    DisplayDriver& new_font = small ? static_cast<DisplayDriver&>(small_font) : changed;
    auto new_measure = [&new_font](const char* line) { return new_font.getTextWidth(line); };
    const auto new_page = readerPage(text, before.offset, changed.width(),
        small ? small_font.lineCount(changed.bodyY(), changed.bodyBottom())
              : (changed.bodyBottom() - changed.bodyY()) / 10,
        new_measure);
    assert(new_page.start <= before.offset && new_page.next > before.offset);
    active.render(changed); assert(active.flush());
    Position after; assert(the_mesh.loadJohnBookmark(after));
    assert(after == pos(91, new_page.start));
    const auto rendered = changed.body();
    JohnReaderScreen reopened(&task, &changed);
    reopened.open(); changed.clear(); reopened.render(changed);
    assert(changed.body() == rendered);
  }
#endif

  // Checksum/version/bounds checks, including validly checksummed bad values.
  uint8_t record[kBookmarkBytes];
  for (Position p : {pos(1), pos(878, 2000)}) {
    encodeBookmark(p, record); Position decoded;
    assert(decodeBookmark(record, decoded) && decoded == p);
    for (unsigned i = 0; i < sizeof(record); ++i) {
      record[i] ^= 1; assert(!decodeBookmark(record, decoded)); record[i] ^= 1;
    }
  }
  for (Position p : {pos(0), pos(879), pos(1, 2048)}) {
    encodeBookmark(p, record); assert(!decodeBookmark(record, saved));
  }

  // Inject failure at each I/O step; reboot must find either the old or the
  // new verified bookmark, never a partially written record or an empty file.
  Files original; assert(saveReaderBookmark(original, pos(4, 12)));
  for (int failure = 0; failure < 12; ++failure) {
    Files files = original; files.calls = 0; files.fail_at = failure;
    const bool ok = saveReaderBookmark(files, pos(30, 45));
    files.fail_at = -1;
    Position recovered; assert(loadReaderBookmark(files, recovered));
    assert(recovered == pos(4, 12) || recovered == pos(30, 45));
    if (ok) assert(recovered == pos(30, 45));
  }
  // A reset between renames recovers the previous record, not the .tmp.
  Files interrupted = original;
  assert(interrupted.rename("/john.pos", "/john.pos.bak"));
  assert(loadReaderBookmark(interrupted, saved) && saved == pos(4, 12));
  assert(saveReaderBookmark(interrupted, pos(20)));
  assert(saveReaderBookmark(interrupted, pos(0)) && interrupted.data.empty());
  Files damaged;
  damaged.data["/john.pos"] = {1, 2, 3};
  assert(!loadReaderBookmark(damaged, saved) && saved.atStart());
  assert(saveReaderBookmark(damaged, pos(10)));
  assert(loadReaderBookmark(damaged, saved) && saved == pos(10));

  ReaderBookmark bookmark;
  bookmark.move(pos(1), UINT32_MAX - 1000);
  assert(!bookmark.due(998) && bookmark.due(999));
  assert(bookmark.dirty()); bookmark.saved(); assert(!bookmark.dirty());
  bookmark.move(pos(0), 2000); assert(bookmark.dirty());
  std::puts("Reader: all 879 verses paginated forward/back, resume, bounds and save-failure tests passed");
}
