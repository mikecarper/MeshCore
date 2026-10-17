#!/usr/bin/env python3
"""Exercise real ST7735 allocation, RGB332 scanout, and retained wake paths.

The fixture records SPI wire bytes instead of driving a physical panel. It
compiles the production methods for both framebuffer depths and SPI APIs.
"""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_serial_wifi_sessions import SANITIZER_FLAGS


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "src/helpers/ui/ST7735Display.cpp"


HARNESS = r'''
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

constexpr int INPUT = 0, OUTPUT = 1, LOW = 0, HIGH = 1;
constexpr int PIN_TFT_RST = 1, PIN_TFT_CS = 2, PIN_TFT_DC = 3;
constexpr int PIN_TFT_LEDA_CTL = 4, PIN_TFT_SDA = 5, PIN_TFT_SCL = 6;
constexpr int PIN_TFT_LEDA_CTL_ACTIVE = HIGH, DISPLAY_ROTATION = 1;
constexpr int SPI_CLOCK_DIV2 = 2, ST77XX_NORON = 0x13;
constexpr int ST77XX_DISPON = 0x29, ST77XX_DISPOFF = 0x28, ST77XX_SLPIN = 0x10;
constexpr uint16_t ST77XX_BLACK = 0, ST77XX_WHITE = 0xffff;
constexpr int NRF_GPIO_PIN_DIR_OUTPUT = 0, NRF_GPIO_PIN_INPUT_CONNECT = 1;
constexpr int NRF_GPIO_PIN_INPUT_DISCONNECT = 2, NRF_GPIO_PIN_NOPULL = 3;
constexpr int NRF_GPIO_PIN_H0H1 = 4, NRF_GPIO_PIN_NOSENSE = 5;
static unsigned gpio_calls, high_drive_calls, errors, resets;
static unsigned objects, destroyed, allocations, fills, color_calls;
static bool fail_pixels;
static std::vector<uint8_t> wire;
static unsigned transactions, finishes, windows, cs_calls;

void digitalWrite(int, int) { ++gpio_calls; }
void pinMode(int, int) { ++gpio_calls; }
void delay(unsigned) {}
void nrf_gpio_cfg(int, int, int, int, int, int) { ++high_drive_calls; }
void sendCommand(int) {}
void set_CS(int) { ++cs_calls; }
void setAddrWindow(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  assert(x == 0 && y == windows && w == 160 && h == 1);
  ++windows;
}
static void resetWire() {
  wire.clear(); transactions = finishes = windows = cs_calls = color_calls = 0;
}
struct Spi {
  void begin() {}
  void begin(int, int, int, int) {}
  void setClockDivider(int) {}
  void beginTransaction(int) { ++transactions; }
  void endTransaction() { ++finishes; }
  void transfer(const void* data, void* output, size_t length) {
    assert(output == nullptr && length == 320);
    auto* bytes = static_cast<const uint8_t*>(data);
    wire.insert(wire.end(), bytes, bytes + length);
  }
  void transferBytes(const uint8_t* data, uint8_t* output, size_t length) {
    transfer(data, output, length);
  }
} spi;
static Spi* _spi = &spi;
static int _spiSettings;
static int _height, _width, _colstart, _rowstart;
struct TFT_eSPI {
  // Same public RGB332-to-RGB565 mapping as the pinned TFT_eSPI dependency.
  uint16_t color8to16(uint8_t color) {
    ++color_calls;
    const uint8_t blue[] = {0, 11, 21, 31};
    return (color & 0x1c) << 6 | (color & 0xc0) << 5
        | (color & 0xe0) << 8 | (color & 0x1c) << 3 | blue[color & 3];
  }
} lcd;
struct TFT_eSprite {
  int depth = 16;
  bool depth_selected = false;
  bool valid = false;
  std::vector<uint8_t> pixels;
  explicit TFT_eSprite(TFT_eSPI*) { ++objects; }
  ~TFT_eSprite() { ++destroyed; }
  void setColorDepth(int value) {
    assert(!valid && pixels.empty());
    depth = value; depth_selected = true;
  }
  void* createSprite(int width, int height) {
    assert(depth_selected && width == 160 && height == 80);
    ++allocations;
    if (fail_pixels) { fail_pixels = false; return nullptr; }
    pixels.resize((width * height + 1) * (depth / 8));
    valid = true;
    return getPointer();
  }
  void* getPointer() { return valid ? pixels.data() : nullptr; }
  void fillScreen(uint16_t color) {
    assert(valid && color == 0); ++fills;
    std::fill(pixels.begin(), pixels.end(), 0);
  }
  void setTextColor(uint16_t color) { assert(color == 0xffff); }
};
@DEPTH_POLICY@
static TFT_eSprite* sprite;
static uint32_t curr_color;
@READY@
namespace mesh {
struct Logger { void println(const char*) { ++errors; } } logger;
Logger& usbLoggingPort() { return logger; }
}
struct Rail {
  unsigned claims = 0;
  void claim() { ++claims; }
  void release() { assert(claims); --claims; }
};
class ST7735Display {
public:
  bool _isOn = false;
  Rail* _peripher_power;
  explicit ST7735Display(Rail* rail) : _peripher_power(rail) {}
  bool begin();
  void turnOn();
  void turnOff();
  void endFrame();
  void _resetAndInit() { ++resets; }
};
@METHODS@

static uint16_t expandExpected(uint8_t value) {
  // Independent component arithmetic, not the driver's bit-expression.
  unsigned r = value >> 5, g = value >> 2 & 7, b = value & 3;
  unsigned red = r * 4 + r / 2;
  unsigned green = g * 8 + g;
  unsigned blue = (b * 31 + 2) / 3;
  return static_cast<uint16_t>((red << 11) | (green << 5) | blue);
}
static uint8_t pack332(uint16_t value) {
  return static_cast<uint8_t>((value >> 8 & 0xe0)
      | (value >> 6 & 0x1c) | (value >> 3 & 3));
}
static void checkWire() {
  assert(transactions == 1 && finishes == 1 && windows == 80 && cs_calls == 2);
  assert(wire.size() == 160 * 80 * 2);
  for (size_t pixel = 0; pixel < 160 * 80; ++pixel) {
#if ST7735_BUFFER_COLOR_DEPTH == 8
    uint16_t expected = expandExpected(static_cast<uint8_t>(pixel));
    assert(wire[2 * pixel] == expected >> 8);
    assert(wire[2 * pixel + 1] == (expected & 0xff));
#else
    // RGB565 storage is already in panel wire order; retain every byte.
    assert(wire[2 * pixel] == static_cast<uint8_t>(pixel >> 5));
    assert(wire[2 * pixel + 1] == static_cast<uint8_t>(pixel));
#endif
  }
#if ST7735_BUFFER_COLOR_DEPTH == 8
  assert(color_calls == 160 * 80);
#else
  assert(color_calls == 0);
#endif
}
int main() {
  Rail rail;
  ST7735Display display(&rail);
  // An absent or failed canvas must never be dereferenced or touch SPI.
  display.endFrame();
  assert(wire.empty() && transactions == 0);
  fail_pixels = true;
  assert(!display.begin());
  assert(sprite == nullptr && objects == 1 && destroyed == 1);
  assert(errors == 1 && gpio_calls == 0 && rail.claims == 0);

  assert(display.begin());
  assert(objects == 2 && allocations == 2 && fills == 1);
  assert(sprite->pixels.size() == (160 * 80 + 1) * (ST7735_BUFFER_COLOR_DEPTH / 8));
  assert(display._isOn && rail.claims == 1 && resets == 1);
  assert(wire.size() == 160 * 80 * 2);
  assert(std::all_of(wire.begin(), wire.end(), [](uint8_t value) { return value == 0; }));
#if defined(NRF52_PLATFORM)
  assert(high_drive_calls == 2);
#endif
  const unsigned first_gpio_calls = gpio_calls;
  const void* original_pixels = sprite->getPointer();
  assert(display.begin());
  assert(objects == 2 && allocations == 2 && fills == 1 && resets == 1);
  assert(gpio_calls == first_gpio_calls && rail.claims == 1);

  // Every one of the 256 RGB332 codes appears repeatedly, including row
  // boundaries. The sentinel byte/pixel is not transmitted.
  for (size_t pixel = 0; pixel < 160 * 80; ++pixel) {
#if ST7735_BUFFER_COLOR_DEPTH == 8
    sprite->pixels[pixel] = static_cast<uint8_t>(pixel);
#else
    sprite->pixels[2 * pixel] = static_cast<uint8_t>(pixel >> 5);
    sprite->pixels[2 * pixel + 1] = static_cast<uint8_t>(pixel);
#endif
  }
  resetWire();
  display.endFrame();
  checkWire();

  // The UI's actual eight named colors remain exact, not merely similar.
  for (uint16_t color : {0x0000, 0xffff, 0xf800, 0x07e0,
                         0x001f, 0x07ff, 0xf81f, 0xffe0}) {
    assert(expandExpected(pack332(color)) == color);
  }

  // Sleep/wake repairs the bus but does not allocate, clear, or replace the
  // retained frame. A second wake while already on is a complete no-op.
  display.turnOff();
  assert(!display._isOn && rail.claims == 0);
  resetWire();
  display.turnOn();
  checkWire();
  assert(display._isOn && rail.claims == 1 && resets == 2);
  assert(sprite->getPointer() == original_pixels);
  assert(objects == 2 && allocations == 2 && fills == 1);
  resetWire();
  display.turnOn();
  assert(wire.empty() && transactions == 0 && allocations == 2);

  // Corrupt/failed library canvas state also fails closed before any SPI or
  // reinitialization. It is not silently reinterpreted as another depth.
  sprite->valid = false;
  resetWire();
  assert(!display.begin());
  display.endFrame();
  assert(wire.empty() && transactions == 0 && allocations == 2);
  delete sprite;
  sprite = nullptr;
}
'''


class ST7735FramebufferTests(unittest.TestCase):
    def source(self):
        driver = DRIVER.read_text(encoding="utf-8")
        start = driver.index("#ifndef ST7735_BUFFER_COLOR_DEPTH")
        end = driver.index("#if defined(NRF52_PLATFORM)", start)
        methods = "\n".join(body(driver, signature) for signature in (
            "bool ST7735Display::begin()", "void ST7735Display::endFrame()",
            "void ST7735Display::turnOff()", "void ST7735Display::turnOn()"))
        return (HARNESS.replace("@DEPTH_POLICY@", driver[start:end])
                .replace("@READY@", body(driver, "static bool spriteReady()"))
                .replace("@METHODS@", methods))

    def test_depths_spi_wire_order_allocation_and_wake(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-st7735-buffer-") as directory:
            cpp = Path(directory) / "framebuffer.cpp"
            cpp.write_text(self.source(), encoding="utf-8")
            for depth in (8, 16):
                for platform in ("ESP_PLATFORM", "NRF52_PLATFORM"):
                    with self.subTest(depth=depth, platform=platform):
                        binary = Path(directory) / f"framebuffer-{depth}-{platform}.exe"
                        result = subprocess.run([
                            compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-variable", "-Wno-unused-function", *SANITIZER_FLAGS,
                            f"-DST7735_BUFFER_COLOR_DEPTH={depth}", f"-D{platform}=1",
                            str(cpp), "-o", str(binary),
                        ], capture_output=True, text=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        result = subprocess.run([str(binary)], capture_output=True,
                                                text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unsupported_depth_is_rejected(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-st7735-depth-") as directory:
            cpp = Path(directory) / "invalid.cpp"
            cpp.write_text(self.source(), encoding="utf-8")
            result = subprocess.run([
                compiler, "-std=c++17", "-DST7735_BUFFER_COLOR_DEPTH=4", "-c",
                str(cpp), "-o", str(Path(directory) / "invalid.o"),
            ], capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ST7735_BUFFER_COLOR_DEPTH must be 8 or 16", result.stderr)


if __name__ == "__main__":
    unittest.main()
