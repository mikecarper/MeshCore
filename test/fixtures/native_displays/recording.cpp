// Host doubles for panel libraries only. The test appends real driver headers
// and real drawing methods; no coordinate or font mapping is reimplemented.
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/ColorTheme.h>
#define pgm_read_byte(p) (*(const uint8_t*)(p))
#define PROGMEM
#define HSPI 1
#define GEOMETRY_RAWMODE 0
#define PIN_TFT_CS 1
#define PIN_TFT_DC 2
#define PIN_TFT_SDA 3
#define PIN_TFT_SCL 4
#define PIN_TFT_RST 5
#define ST7789_RESET 1
#define ST7789_RS 2
#define ST7789_CS 3
#define ST7789_SDA 4
#define ST7789_MISO 5
#define ST7789_SCK 6
#define PIN_DISPLAY_CS 1
#define PIN_DISPLAY_DC 2
#define PIN_DISPLAY_RST 3
#define PIN_DISPLAY_BUSY 4
#define DISP_CS 1
#define DISP_DC 2
#define DISP_RST 3
#define DISP_BUSY 4
#define ST77XX_WHITE 1
#define ST77XX_BLACK 0
struct SPIClass { explicit SPIClass(int = 0) {} } SPI, SPI1;
struct TwoWire {};
struct RefCountedDigitalPin {};
enum OLEDDISPLAY_COLOR { BLACK, WHITE };
const int ArialMT_Plain_16 = 16, ArialMT_Plain_24 = 24;
const int FreeSans9pt7b = 13, FreeSansBold12pt7b = 18, FreeSans18pt7b = 26;
struct CRC32 {
  template<class T> void update(T) {}
  template<class T> void update(const T*, size_t) {}
};
ColorVal UIColor::window_bkg=0, UIColor::title_bkg=0, UIColor::title_txt=1;
ColorVal UIColor::primary_txt=1, UIColor::secondary_txt=1, UIColor::warning_txt=1;
ColorVal UIColor::popup_bkg=0, UIColor::popup_txt=1, UIColor::corp_blue=1;

struct Canvas {
  static Canvas* active;
  int w=EXPECTED_WIDTH, h=EXPECTED_HEIGHT, x=0, y=0, size=1, ascent=0;
  std::vector<int> pixels = std::vector<int>(w*h);
  Canvas() { active=this; }
  int width() const { return w; }
  int height() const { return h; }
  void setCursor(int xx, int yy) { x=xx; y=yy; }
  void setTextSize(int s) { size=s; }
  void setTextColor(int) {}
  void setColor(OLEDDISPLAY_COLOR) {}
  void setRGB(int) {}
  void setRenderYieldCallback(void (*)()) {}
  void setFont(int) {}
  void setFont(const int* font) { ascent=*font; }
  void print(const char*) {}
  void drawString(int xx,int yy,const char*) { x=xx; y=yy; }
  void drawStringMaxWidth(int xx,int yy,int ww,const char*) { x=xx; y=yy; assert(ww==w); }
  int getStringWidth(const char* s) { return std::strlen(s)*6*size; }
  void getTextBounds(const char* s,int,int,int16_t* xx,int16_t* yy,uint16_t* ww,uint16_t* hh) {
    *xx=0; *yy=-ascent; *ww=getStringWidth(s); *hh=ascent ? ascent+4 : 8*size;
  }
  void fillRect(int xx,int yy,int ww,int hh,int=1) {
    for(int row=std::max(yy,0); row<std::min(yy+hh,h); ++row)
      for(int col=std::max(xx,0); col<std::min(xx+ww,w); ++col) ++pixels[row*w+col];
  }
  void drawRect(int xx,int yy,int ww,int hh,int c=1) {
    if(ww<=0 || hh<=0) return;
    fillRect(xx,yy,ww,1,c); fillRect(xx,yy+hh-1,ww,1,c);
    fillRect(xx,yy,1,hh,c); fillRect(xx+ww-1,yy,1,hh,c);
  }
  void drawBitmap(int xx,int yy,const uint8_t* bits,int ww,int hh,int c) {
    for(int row=0;row<hh;++row) for(int col=0;col<ww;++col)
      if(bits[row*((ww+7)/8)+col/8] & (0x80>>(col&7))) fillRect(xx+col,yy+row,1,1,c);
  }
  void reset() { std::fill(pixels.begin(),pixels.end(),0); }
};
Canvas* Canvas::active=nullptr;
// This fixture exercises direct panel geometry; the full buffered T-Deck
// renderer and its PSRAM canvas run in test_tdeck_display_cooperation.py.
class TDeckCanvas : public Canvas {
public:
  int16_t getCursorX() const { return x; }
  int16_t getCursorY() const { return y; }
};
struct ST7789Spi : Canvas { template<class... T> ST7789Spi(T...) {} };
struct Adafruit_ST7789 : Canvas { template<class... T> Adafruit_ST7789(T...) {} };
struct GxEPD2_150_BN { static const int WIDTH=200,HEIGHT=200; template<class... T> GxEPD2_150_BN(T...) {} };
struct WidePanel { static const int WIDTH=122,HEIGHT=250; template<class... T> WidePanel(T...) {} };
struct TallPanel { static const int WIDTH=192,HEIGHT=256; template<class... T> TallPanel(T...) {} };
template<class T,int H> struct GxEPD2_BW : Canvas { explicit GxEPD2_BW(T) {} };
