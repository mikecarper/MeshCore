"""Execute actual color boundaries, indexed palettes and night emoji rendering."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'src/helpers/ui'

PREFIX = r'''
#include <helpers/ui/ColorTheme.h>
#include <cassert>
#include <cstdint>
#include <vector>
#include <type_traits>
static bool night = false;
namespace mesh { namespace ui { bool displayNightActive() { return night; } } }
ColorVal UIColor::window_bkg=0x0885, UIColor::title_bkg=0x1299, UIColor::title_txt=0xffff;
ColorVal UIColor::primary_txt=0xe73c, UIColor::secondary_txt=0x8d59, UIColor::warning_txt=0xfd20;
ColorVal UIColor::popup_bkg=0x1299, UIColor::popup_txt=0xffff, UIColor::corp_blue=0x5dbf;
struct Canvas {
  uint32_t fill=0, text=0;
  std::vector<uint32_t> marks;
  void fillScreen(uint32_t c) { fill=c; }
  void setTextColor(uint32_t c) { text=c; }
  void setBaseColor(uint32_t) {}
  void setTextSize(int) {}
  void setTextWrap(bool) {}
  void setFreeFont() {}
  void cp437(bool) {}
  void fillRect(int,int,int,int,uint32_t c) { marks.push_back(c); }
};
'''


class DisplayNightRenderingTests(unittest.TestCase):
    def run_cpp(self, source, *defines):
        with tempfile.TemporaryDirectory(prefix='meshcore-night-render-') as directory:
            executable = Path(directory) / 'render'
            compiled = subprocess.run([
                os.environ.get('CXX', 'c++'), '-std=c++11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-unused-variable',
                '-DDISPLAY_CLASS=LGFXDisplay', *('-D' + value for value in defines),
                '-I', str(ROOT / 'src'), '-x', 'c++', '-', '-o', str(executable),
            ], input=source, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_all_rgb565_values_and_variant_semantic_backgrounds(self):
        self.run_cpp(PREFIX + r'''
int main() {
  using namespace mesh::ui::color_theme;
  for (unsigned c=0;c<65536;++c) assert(render(c)==c && background(c)==c);
  night=true;
  for (unsigned c=0;c<65536;++c) {
    const bool b=c==0 || c==0x0885 || c==0x1299 || c==WINDOW_BACKGROUND
        || c==TITLE_BACKGROUND || c==POPUP_BACKGROUND;
    assert(render(c)==(b ? NIGHT_BACKGROUND : NIGHT_FOREGROUND));
    assert(background(c)==NIGHT_BACKGROUND);
  }
  assert(render(TOUCH_OUTLINE)==0xf800 && render(TOUCH_PRESSED)==0xf800);
  assert(UIColor::window_bkg==0x0885 && UIColor::primary_txt==0xe73c);
  night=false;
  assert(render(UIColor::window_bkg)==0x0885 && render(UIColor::primary_txt)==0xe73c);
}
''')

    def test_actual_direct_and_buffered_color_boundaries(self):
        for driver in ('ST7735Display', 'ST7789LCDDisplay', 'NV3001BDisplay'):
            with self.subTest(driver=driver):
                source = (UI / (driver + '.cpp')).read_text()
                if driver == 'ST7735Display':
                    harness = r'''
static Canvas canvas; static Canvas* sprite=&canvas; static ColorVal curr_color;
bool spriteReady() { return true; }
struct ST7735Display {
  void startFrame(ColorVal); void setColor(ColorVal); void fillRect(int,int,int,int);
};
'''
                    inspect = 'canvas'
                elif driver == 'ST7789LCDDisplay':
                    harness = r'''
// Mirror ST7789LCDDisplay.h when compiling the extracted methods with a fake panel.
#define MESHCORE_BUFFERED_SPI_DISPLAY 1
struct ST7789LCDDisplay {
  Canvas display, canvas; Canvas* _canvas=&canvas;
  bool buffered=false, _isOn=true, _frame_active=false; ColorVal _color=0;
  bool isBufferedRendering() const { return buffered; }
  int width() const { return 8; } int height() const { return 8; }
  void serviceRender(bool) {} void finishCanvasPrimitive(int,int,int,int) {}
  void startFrame(ColorVal); void setColor(ColorVal); void fillRect(int,int,int,int);
};
'''
                    inspect = '(d.buffered ? d.canvas : d.display)'
                else:
                    harness = r'''
static const int NV3001B_SCREEN_WIDTH=8, NV3001B_SCREEN_HEIGHT=8;
struct NV3001BDisplay {
  Canvas canvas; ColorVal color=0; int text_size=1,cursor_x=0,cursor_y=0;
  void fillPhysicalRect(int x,int y,int w,int h) {
    canvas.fillRect(x,y,w,h,color); if (w==8 && h==8) canvas.fill=color;
  }
  void startFrame(ColorVal); void setColor(ColorVal); void fillRect(int,int,int,int);
};
'''
                    inspect = 'd.canvas'
                harness += '\n'.join(body(source, f'void {driver}::{name}(')
                                     for name in ('startFrame', 'setColor', 'fillRect'))
                harness += '\nint main() { ' + driver + ' d;\n'
                if driver == 'ST7789LCDDisplay':
                    harness += 'for (bool buffered : {false,true}) { d.buffered=buffered;\n'
                harness += 'auto& c=' + inspect + ';\n'
                harness += r'''
  night=false; d.startFrame(0x3456); assert(c.fill==0x3456);
  d.setColor(0x07e0); d.fillRect(1,1,1,1); assert(c.marks.back()==0x07e0);
  night=true; d.startFrame(0x3456); assert(c.fill==0);
  d.setColor(UIColor::primary_txt); d.fillRect(1,1,1,1); assert(c.marks.back()==0xf800);
  d.setColor(0xffe0); d.fillRect(1,1,1,1); assert(c.marks.back()==0xf800);
  d.setColor(UIColor::popup_bkg); d.fillRect(1,1,1,1); assert(c.marks.back()==0);
  night=false; d.startFrame(0x3456); assert(c.fill==0x3456);
'''
                if driver == 'ST7789LCDDisplay':
                    harness += '}\n'
                harness += '}\n'
                self.run_cpp(PREFIX + harness, 'LILYGO_TDECK=1')

    def test_actual_lgfx_indexed_and_truecolor_emoji_paths(self):
        source = (UI / 'LGFXDisplay.cpp').read_text()
        palette = body(source, 'static const uint32_t UI_PALETTE[16]') + ';\n'
        harness = r'''
namespace lgfx { namespace v1 { struct LGFXBase {}; } }
struct Sprite : Canvas, lgfx::v1::LGFXBase {
  uint32_t palette[16]={};
  void setPaletteColor(unsigned i,uint32_t c) { palette[i]=c; }
  template<class T> void fillRect(int,int,int,int,T c) {
    // LovyanGFX treats signed32 as RGB565, unsigned32 as RGB888.
#if UI_BUFFER_COLOR_DEPTH < 8
    marks.push_back(c);
#else
    marks.push_back(std::is_signed<T>::value
        ? mesh::ui::color_theme::rgb888(static_cast<ColorVal>(c)) : c);
#endif
  }
};
struct Atlas {
  const uint8_t* glyph(uint16_t) const { static uint8_t p[]={255,0xe0,0x1c,3}; return p; }
  int width() const { return 2; } int height() const { return 2; }
  uint8_t transparent() const { return 255; }
};
struct LGFXDisplay {
  Canvas panel; Canvas* display=&panel; Sprite buffer; Atlas _emojiAtlas;
  struct EmojiOverlay { uint16_t codepoint; int16_t x,y; uint16_t size; } _emojiOverlays[2];
  static const unsigned MAX_EMOJI_OVERLAYS=2;
  unsigned _emojiOverlayCount=0,_presentedEmojiOverlayCount=0;
  bool _hasTransparentEmojiPixels=false,_nightFrame=false,_hasLastFrame=true;
  uint32_t _color=0;
  bool wasEmojiPresented(const EmojiOverlay&) { return false; }
  void drawEmojiMask(int,int,int,const uint8_t*) { assert(false); }
  void drawEmojiUnderlay(int,int,int,const uint8_t*) {}
  void startFrame(ColorVal); void setColor(ColorVal); void applyNightPalette();
  uint32_t renderColor(ColorVal) const;
  int32_t queueEmoji(lgfx::v1::LGFXBase*,int32_t,int32_t,uint32_t,int32_t);
};
'''
        harness += '\n'.join(body(source, signature) for signature in (
            'void LGFXDisplay::startFrame(', 'void LGFXDisplay::setColor(',
            'void LGFXDisplay::applyNightPalette(', 'uint32_t LGFXDisplay::renderColor(',
            'int32_t LGFXDisplay::queueEmoji('))
        harness += r'''
int main() {
  LGFXDisplay d; night=true; d.startFrame(0xffff);
  assert(!d._hasLastFrame && d.panel.fill==0 && d.buffer.fill==0);
#if UI_BUFFER_COLOR_DEPTH < 8
  assert(d.buffer.palette[0]==0 && d.buffer.palette[1]==0xff0000);
  for (unsigned i=1;i<16;++i) assert(d.buffer.palette[i]==0xff0000);
  const uint32_t red=1;
  const uint32_t physical_red=1;
#else
  const uint32_t red=0xf800;
  const uint32_t physical_red=0xff0000;
#endif
  assert(d.buffer.text==red && d.renderColor(0x07e0)==red);
  assert(d.queueEmoji(&d.buffer,0,0,0x1234,4)==4);
  assert(d._emojiOverlayCount==0 && !d._hasTransparentEmojiPixels);
  assert(d.buffer.marks.size()==3);
  for (uint32_t color : d.buffer.marks) assert(color==physical_red);
  night=false; d.startFrame(0x3456);
  assert(!d._hasLastFrame && d.panel.fill==UIColor::window_bkg);
#if UI_BUFFER_COLOR_DEPTH < 8
  assert(d.buffer.palette[0]==UI_PALETTE[0] && d.buffer.palette[1]==UI_PALETTE[1]);
  for (unsigned i=0;i<16;++i) assert(d.buffer.palette[i]==UI_PALETTE[i]);
#else
  assert(d.buffer.fill==0x3456);
#endif
  assert(d.queueEmoji(&d.buffer,0,0,0x1234,4)==4 && d._emojiOverlayCount==1);
}
'''
        for depth in (4, 8, 16):
            with self.subTest(depth=depth):
                self.run_cpp(PREFIX + palette + harness, f'UI_BUFFER_COLOR_DEPTH={depth}')

    def test_color_capability_excludes_monochrome_eink_and_null(self):
        colors = ('ST7735Display', 'ST7789Display', 'ST7789LCDDisplay', 'NV3001BDisplay',
                  'LGFXDisplay', 'SCIndicatorDisplay', 'WioTrackerL2Display')
        others = ('SSD1306Display', 'SH1106Display', 'SH1107Display', 'U8g2Display',
                  'GxEPDDisplay', 'E213Display', 'E290Display', 'NullDisplayDriver')
        for driver in colors + others:
            with self.subTest(driver=driver):
                code = '#define DISPLAY_CLASS ' + driver + '\n#include <helpers/ui/DisplayBuildFlags.h>\n'
                code += '#ifdef MESHCORE_HAS_COLOR_DISPLAY\nconstexpr bool color=true;\n#else\nconstexpr bool color=false;\n#endif\n'
                code += 'static_assert(color == ' + ('true' if driver in colors else 'false') + ', "capability");\n'
                compiled = subprocess.run(['c++', '-std=c++11', '-fsyntax-only', '-I', str(ROOT / 'src'),
                                           '-x', 'c++', '-'], input=code, text=True, capture_output=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)

    def test_actual_qr_paths_keep_red_quiet_zone_and_black_modules_at_night(self):
        qr_common = r'''
#include <helpers/ui/QrCodeVersion.h>
struct QRCode { uint8_t size=21; };
const int ECC_LOW=0;
static uint8_t encodedVersion;
static const char protectedWifi[] = "WIFI:T:WPA;S:MC-69C2;P:testpass;;";
int qrcode_initText(QRCode* qr,uint8_t*,uint8_t version,int,const char*) {
  encodedVersion=version; qr->size=mesh::ui::qrModuleCount(version); return 0;
}
bool qrcode_getModule(const QRCode*,unsigned col,unsigned row) { return col==row || col==0; }
int lgfx_qrcode_initText(QRCode* q,uint8_t* b,uint8_t v,int e,const char* t) { return qrcode_initText(q,b,v,e,t); }
bool lgfx_qrcode_getModule(const QRCode* q,unsigned c,unsigned r) { return qrcode_getModule(q,c,r); }
'''
        source = (UI / 'DisplayDriver.cpp').read_text()
        generic = r'''
struct QrDisplay {
  Canvas canvas; uint32_t color=0;
  int width() const { return 128; } int height() const { return 128; }
  bool isEink() const { return false; }
  void setColor(ColorVal c) { color=mesh::ui::color_theme::render(c); }
  void fillRect(int x,int y,int w,int h) { canvas.fillRect(x,y,w,h,color); }
  bool drawQrCodeWithBorder(const char*,int,int,int,int);
};
'''
        generic += body(source, 'bool DisplayDriver::drawQrCodeWithBorder(').replace('DisplayDriver::', 'QrDisplay::')
        generic += r'''
int main() {
  for (bool active : {false,true}) {
    night=active; QrDisplay d;
    assert(d.drawQrCodeWithBorder("WIFI:S:MC-69C2;;",0,0,64,-1));
    assert(encodedVersion==1);
    assert(d.canvas.marks.size()==42);
    assert(d.canvas.marks[0]==(night ? 0xf800 : UIColor::popup_txt));
    for (unsigned i=1;i<d.canvas.marks.size();++i)
      assert(d.canvas.marks[i]==(night ? 0 : UIColor::window_bkg));
    assert(d.drawQrCodeWithBorder(protectedWifi,0,0,60,1));
    assert(encodedVersion==3);
  }
}
'''
        self.run_cpp(PREFIX + qr_common + generic)
        source = (UI / 'LGFXDisplay.cpp').read_text()
        lgfx = r'''
struct LGFXDisplay {
  Canvas buffer; int _coordinateScale=2;
  int width() const { return 128; } int height() const { return 128; }
  uint32_t renderColor(ColorVal c) const;
  bool drawQrCode(const char*,int,int,int);
};
'''
        lgfx += body(source, 'uint32_t LGFXDisplay::renderColor(')
        lgfx += body(source, 'bool LGFXDisplay::drawQrCode(')
        lgfx += r'''
int main() {
  for (bool active : {false,true}) {
    night=active; LGFXDisplay d;
    assert(d.drawQrCode("WIFI:S:MC-69C2;;",0,0,64));
    assert(encodedVersion==1);
    assert(d.buffer.marks.size()==42);
#if UI_BUFFER_COLOR_DEPTH < 8
    const uint32_t light = 1u;
#else
    const uint32_t light = night ? 0xff0000u : UIColor::primary_txt;
#endif
#if UI_BUFFER_COLOR_DEPTH < 8
    const uint32_t dark = 0u;
#else
    const uint32_t dark = night ? 0u : UIColor::window_bkg;
#endif
    assert(d.buffer.marks[0]==light);
    for (unsigned i=1;i<d.buffer.marks.size();++i)
      assert(d.buffer.marks[i]==dark);
    assert(d.drawQrCode(protectedWifi,0,0,64));
    assert(encodedVersion==3);
  }
}
'''
        for depth in (4, 8, 16):
            with self.subTest(qr_depth=depth):
                self.run_cpp(PREFIX + qr_common + lgfx, f'UI_BUFFER_COLOR_DEPTH={depth}')


if __name__ == '__main__':
    unittest.main()
