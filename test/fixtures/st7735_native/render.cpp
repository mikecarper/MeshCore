#include <cassert>
#include <cstring>
#include <vector>
#include <helpers/ui/ST7735Display.h>
#include <helpers/ui/SmallMessageText.h>
#include <helpers/ui/CompanionHomeLayout.h>

ColorVal UIColor::window_bkg = 0, UIColor::title_bkg = 0, UIColor::title_txt = 1;
ColorVal UIColor::primary_txt = 1, UIColor::secondary_txt = 1, UIColor::warning_txt = 1;
ColorVal UIColor::popup_bkg = 0, UIColor::popup_txt = 1, UIColor::corp_blue = 1;

struct Canvas {
  std::vector<int> pixels = std::vector<int>(160 * 80);
  int x = 0, y = 0, size = 1, prints = 0;
  void setCursor(int xx, int yy) { x = xx; y = yy; }
  void setTextSize(int s) { size = s; }
  void setTextColor(int) {}
  void print(const char*) { ++prints; }
  int textWidth(const char* s) { return std::strlen(s) * 6 * size; }
  int fontHeight() { return 8 * size; }
  void fillRect(int xx, int yy, int w, int h, int) {
    assert(xx >= 0 && yy >= 0 && xx + w <= 160 && yy + h <= 80);
    for (int row = yy; row < yy + h; ++row)
      for (int col = xx; col < xx + w; ++col) ++pixels[row * 160 + col];
  }
  void drawRect(int xx, int yy, int w, int h, int c) { fillRect(xx, yy, w, h, c); }
  void drawBitmap(int xx, int yy, const uint8_t*, int w, int h, int c) {
    fillRect(xx, yy, w, h, c);
  }
} canvas;
static Canvas* sprite = &canvas;
static int curr_color = 1;
static bool spriteReady() { return true; }

// Only hardware initialization and SPI transmission are doubled. The runner
// appends the real driver's coordinate/text drawing methods verbatim below.
bool ST7735Display::begin() { return true; }
void ST7735Display::turnOn() {}
void ST7735Display::turnOff() {}
void ST7735Display::clear() { canvas.pixels.assign(160 * 80, 0); }
void ST7735Display::startFrame(ColorVal) { clear(); }
void ST7735Display::endFrame() {}

int main() {
  ST7735Display display;
  assert(display.width() == 160 && display.height() == 80);
  display.setCursor(159, 79);
  assert(canvas.x == 159 && canvas.y == 79);
  assert(display.getTextWidth("MMMM") == 24);
  assert(display.textLineHeight() == 8);
  display.setTextSize(2);
  assert(display.textLineHeight() == 16);
  display.setTextSize(1);
  assert(!display.useSmallMessageFont());
  // Every logical pixel covers its own exact physical cell, without gaps or
  // overlap. In native mode each of these cells must be exactly one pixel.
  display.clear();
  for (int y = 0; y < display.height(); ++y)
    for (int x = 0; x < display.width(); ++x) display.fillRect(x, y, 1, 1);
  for (int count : canvas.pixels) assert(count == 1);
  display.clear();
  display.drawRect(0, 0, display.width(), display.height());
  for (int count : canvas.pixels) assert(count == 1);

  mesh::ui::SmallMessageText body(display);
  assert(body.capitalHeight() == 6);
#if defined(HELTEC_T096)
  assert(body.lineCount(12) == 8);
  // Check all 95 real font glyphs against their native bitmap coordinates.
  for (int c = 32; c <= 126; ++c) {
    display.clear();
    char text[] = {static_cast<char>(c), 0};
    body.setCursor(0, 0);
    body.print(text);
    const auto& g = mesh::ui::squeezed6Glyphs[c - 32];
    std::vector<int> expected(160 * 80);
    for (int y = 0; y < g.height; ++y) {
      for (int x = 0; x < g.width; ++x) {
        int bit = y * g.width + x;
        if (mesh::ui::squeezed6Bitmaps[g.bitmap_offset + bit / 8] & (0x80 >> (bit % 8)))
          expected[(5 + g.y_offset + y) * 160 + g.x_offset + x] = 1;
      }
    }
    assert(canvas.pixels == expected);
  }
#endif
  // A 160px message must use the panel's normal print path, not the compact
  // wrapper's per-pixel 5/6px glyphs, even when the feature is compiled in.
  display.clear();
  canvas.prints = 0;
  mesh::ui::drawSmallMessageBody(display, "Sender", "A normal-font message");
  assert(canvas.prints >= 2);
  for (int count : canvas.pixels) assert(count == 0);
}
