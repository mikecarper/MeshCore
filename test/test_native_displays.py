#!/usr/bin/env python3
"""Exercise actual non-Indicator driver geometry/drawing with recording panels."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"


def method(source, cls, name):
    start = source.index(f"{cls}::{name}(")
    start = source.rfind("\n", 0, start) + 1
    end = source.index("\n}", start) + 2
    return source[start:end] + "\n"


class NativeDisplaysTest(unittest.TestCase):
    def test_real_headers_and_rendering(self):
        cases = [
            ("ST7789Display", 240, 135, []),
            ("ST7789Display", 320, 170, ["HELTEC_VISION_MASTER_T190"]),
            ("ST7789Display", 320, 240, ["THINKNODE_M9"]),
            ("ST7789LCDDisplay", 320, 240, []),
            ("ST7789LCDDisplay", 320, 240, ["USE_PIN_TFT"]),
            ("ST7789LCDDisplay", 320, 240, ["LILYGO_TDECK"]),
            ("ST7789LCDDisplay", 240, 320, ["HELTEC_V4_R8_TFT", "ST7789_PORTRAIT_PROFILE", "DISPLAY_ROTATION=0"]),
            ("NV3001BDisplay", 220, 128, []),
            ("GxEPDDisplay", 200, 200, []),
            ("GxEPDDisplay", 250, 122, ["EINK_DISPLAY_MODEL=WidePanel"]),
            ("GxEPDDisplay", 250, 122, ["EINK_DISPLAY_MODEL=WidePanel", "DISPLAY_ROTATION=1"]),
            ("GxEPDDisplay", 192, 256, ["EINK_DISPLAY_MODEL=TallPanel", "DISPLAY_ROTATION=4"]),
        ]
        with tempfile.TemporaryDirectory(prefix="meshcore-native-panels-") as temp:
            for cls, width, height, defines in cases:
                with self.subTest(driver=cls, defines=defines):
                    source = (ROOT / "test/fixtures/native_displays/recording.cpp").read_text()
                    header = (UI / (cls + ".h")).read_text()
                    source += re.sub(r'^\s*#(?:include|pragma).*$', '', header, flags=re.M)
                    driver = (UI / (cls + ".cpp")).read_text()
                    if cls == "NV3001BDisplay":
                        source += "Canvas nv_canvas;\n"
                        source += f"bool {cls}::begin() {{ is_on=true; return true; }}\n"
                        source += f"void {cls}::fillPhysicalRect(int x,int y,int w,int h) {{ Canvas::active->fillRect(x,y,w,h); }}\n"
                        a = driver.index("static const uint8_t font5x7")
                        b = driver.index("static void setupOptionalOutput", a)
                        source += driver[a:b]
                        source += method(driver, cls, "drawChar")
                    else:
                        source += f"bool {cls}::begin() {{ return true; }}\n"
                    for name in ("turnOn", "turnOff", "clear", "endFrame"):
                        source += f"void {cls}::{name}() {{}}\n"
                    source += f"void {cls}::startFrame(ColorVal) {{}}\n"
                    if cls == "ST7789LCDDisplay":
                        source += f"void {cls}::setFlipped(bool) {{}}\n"
                    names = ["setTextSize", "setColor", "setCursor", "print", "fillRect", "drawRect", "drawXbm", "getTextWidth"]
                    if cls == "ST7789LCDDisplay":
                        names += ["setRotationDegrees"]
                    if cls == "ST7789Display":
                        names += ["printWordWrap"]
                    source += "\n".join(method(driver, cls, name) for name in names)
                    source += f"\nusing Driver = {cls};\n"
                    source += r'''
int main() {
  Driver d; d.begin(); d.setColor(1); d.setTextSize(1);
  auto& c=*Canvas::active;
  assert(d.width()==EXPECTED_WIDTH && d.height()==EXPECTED_HEIGHT);
  assert(!d.useSmallMessageFont());
  c.reset();
  for(int y=0;y<d.height();++y) for(int x=0;x<d.width();++x) d.fillRect(x,y,1,1);
  for(int pixel:c.pixels) assert(pixel==1);
  c.reset(); d.drawRect(0,0,d.width(),d.height());
  for(int y=0;y<d.height();++y) for(int x=0;x<d.width();++x)
    assert((c.pixels[y*d.width()+x]>0)==(x==0||y==0||x==d.width()-1||y==d.height()-1));
  // Non-byte-aligned bitmap and the bottom/right panel boundary.
  const uint8_t bitmap[]={0x81,0x80,0x42,0x00};
  c.reset(); d.drawXbm(d.width()-9,d.height()-2,bitmap,9,2);
  assert(std::count(c.pixels.begin(),c.pixels.end(),1)==5);
  assert(c.pixels.back()==0 && c.pixels[(d.height()-2)*d.width()+d.width()-1]==1);
  assert(d.getTextWidth("MMMM") >= 24);
  assert(d.getTextWidth("MMMMMMMM") > d.getTextWidth("MMMM"));
  assert(d.getTextWidth("")==0);
  d.setCursor(7,9); d.print("M");
#ifdef NV_NATIVE_TEST
  // Native 5x7 glyph: no historical vertical doubling.
  c.reset(); d.setCursor(0,0); d.print("M");
  for(int y=7;y<d.height();++y) for(int x=0;x<d.width();++x) assert(c.pixels[y*d.width()+x]==0);
#else
  assert(c.x==7 && c.y==9+c.ascent);
#endif
}
'''
                    flags = [f"-D{x}" for x in defines]
                    if cls == "NV3001BDisplay":
                        flags += ["-DNV_NATIVE_TEST"]
                    if os.name != "nt":
                        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                    binary = Path(temp) / "display.exe"
                    result = subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-type-limits", *flags,
                        f"-DEXPECTED_WIDTH={width}", f"-DEXPECTED_HEIGHT={height}",
                        "-I", str(ROOT / "src"), "-x", "c++", "-", "-o", str(binary)],
                        input=source, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_obsolete_panel_scaling_flags(self):
        for driver in ("ST7735Display", "ST7789Display", "ST7789LCDDisplay", "NV3001BDisplay", "GxEPDDisplay"):
            for suffix in (".h", ".cpp"):
                source = (UI / (driver + suffix)).read_text()
                self.assertNotRegex(source, r"DISPLAY_SCALE|EINK_SCALE|SCALE_[XY]|portraitViewport")


if __name__ == "__main__":
    unittest.main()
