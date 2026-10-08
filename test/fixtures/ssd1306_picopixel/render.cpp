#include <helpers/ui/SSD1306Display.h>
#include <helpers/ui/SmallMessageText.h>
#include <Fonts/Picopixel.h>
#include <cassert>
#include <fstream>
#include <iostream>
#include <string>

void save(const char* file) {
  auto& c=*Adafruit_SSD1306::last;
  std::ofstream f(file);
  f << "P1\n128 64\n";
  for (int y=0;y<64;y++) { for (int x=0;x<128;x++) f << c.getPixel(x,y) << ' '; f << '\n'; }
}
class TestSSD1306Display : public SSD1306Display {
public:
  void setCanvasSize(int w, int h) { setDimensions(w, h); }
};

int main(int argc, char** argv) {
  TestSSD1306Display d;
  assert(d.begin());
  auto& c=*Adafruit_SSD1306::last;
  assert(d.width()==128 && d.height()==64);
  assert(c.width()==128 && c.height()==64);
  static_assert(SSD1306Display::FRAMEBUFFER_BYTES==1024);
  const char* message="GIANT KILLER: Out on the Mercerwood mesh today, just the V4 and a small battery Checking the smaller font so the rest of this message is visible. 0123456789 END";
  assert(std::string(message).size()==160);
  d.startFrame();
  d.setCursor(0,0); d.print("Message 3/29");
  d.setCursor(86,0); d.print("1m ago");
  d.drawRect(0,11,128,1);
#if UI_SMALL_MESSAGE_FONT == 1
  mesh::ui::drawSmallMessageBody(d,"Ch 0 Public [4h]:",message);
#else
  d.setCursor(0,14); d.print("Ch 0 Public [4h]:");
  d.setCursor(0,25); d.print(std::string(message,77).c_str());
#endif
  if (argc > 1) save(argv[1]);
#if UI_SMALL_MESSAGE_FONT == 1
  assert(c.outside==0);
  assert(d.getTextWidth("ABC")==18); // regular driver font untouched
  mesh::ui::SmallMessageText six(d);
  assert(six.capitalHeight()==6);
  for (int ch=32;ch<=126;ch++) {
    d.startFrame(); six.setCursor(0,0);
    const std::string glyph(1,char(ch));
    six.print(glyph.c_str());
    // Compared with the independent upstream BDF in the Python harness.
    std::cout << "six " << ch << ' ' << six.getTextWidth(glyph.c_str()) << ' ';
    for (int y=0;y<8;y++) for (int x=0;x<8;x++)
      std::cout << int(c.getPixel(x,y));
    std::cout << '\n';
  }
  d.setCanvasSize(72,40);
  mesh::ui::SmallMessageText text(d);
  assert(text.capitalHeight()==5);
  GFXcanvas1 reference(128,64);
  reference.setFont(&Picopixel);
  reference.setTextColor(1);
  for (int ch=32;ch<=126;ch++) {
    d.startFrame(); text.setCursor(3,25);
    text.print(std::string(1,char(ch)).c_str());
    reference.fillScreen(0); reference.setCursor(3,29); reference.write(ch);
    for (int y=0;y<64;y++) for (int x=0;x<128;x++)
      assert(c.getPixel(x,y)==reference.getPixel(x,y));
    assert(text.getTextWidth(std::string(1,char(ch)).c_str())==PicopixelGlyphs[ch-32].xAdvance);
  }
  std::cout << "95 tiny-screen glyphs match Adafruit GFX Picopixel; normal font preserved\n";
#endif
}
