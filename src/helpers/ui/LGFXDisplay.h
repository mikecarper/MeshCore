#pragma once

#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/ColorEmojiAtlas.h>
#include <helpers/ui/EmojiFontMap.h>

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#ifndef UI_ZOOM
  #define UI_ZOOM 1
#endif
#ifndef UI_COORD_SCALE
  #define UI_COORD_SCALE 1
#endif
#ifndef UI_BUFFER_COLOR_DEPTH
  #define UI_BUFFER_COLOR_DEPTH 8
#endif

class LGFXDisplay : public DisplayDriver {
protected:
  LGFX_Device* display;
  LGFX_Sprite buffer;

  bool _isOn = false;
  int _color = TFT_WHITE;
  uint8_t* _fontData = nullptr;
  size_t _fontDataSize = 0;
  mesh::ui::EmojiFontMap _emojiMap;
  mesh::ui::ColorEmojiAtlas _emojiAtlas;
  uint8_t _fontNativeScale = 1;

  struct EmojiOverlay {
    uint16_t codepoint;
    int16_t x;
    int16_t y;
    uint16_t size;
  };
  static constexpr size_t MAX_EMOJI_OVERLAYS = 64;
  EmojiOverlay _emojiOverlays[MAX_EMOJI_OVERLAYS];
  size_t _emojiOverlayCount = 0;
  EmojiOverlay _presentedEmojiOverlays[MAX_EMOJI_OVERLAYS];
  size_t _presentedEmojiOverlayCount = 0;
  bool _hasTransparentEmojiPixels = false;
  uint32_t _lastFrameHash = 0;
  bool _hasLastFrame = false;

  String mapText(const char* str) const;
  uint32_t renderColor(ColorVal color) const;
  void configurePalette();
  uint32_t frameHash() const;
  bool wasEmojiPresented(const EmojiOverlay& overlay) const;
  void drawEmojiMask(int32_t x, int32_t y, int32_t size,
                     const uint8_t* pixels);
  void drawEmojiUnderlay(int32_t x, int32_t y, int32_t size,
                         const uint8_t* pixels);
  int32_t queueEmoji(lgfx::v1::LGFXBase* gfx, int32_t x, int32_t y,
                     uint32_t codepoint, int32_t font_height);
  static int32_t drawEmoji(lgfx::v1::LGFXBase* gfx, int32_t x, int32_t y,
                           uint32_t codepoint, int32_t font_height);

public:
  LGFXDisplay(int w, int h, LGFX_Device &disp)
    : DisplayDriver(w/(UI_ZOOM*UI_COORD_SCALE),
                    h/(UI_ZOOM*UI_COORD_SCALE)), display(&disp) {}
  bool begin();
  bool isOn() override { return _isOn; }
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg = UIColor::window_bkg) override;
  void setTextSize(int sz) override;
  void setColor(ColorVal c) override;
  void setCursor(int x, int y) override;
  void print(const char* str) override;
  void fillRect(int x, int y, int w, int h) override;
  void drawRect(int x, int y, int w, int h) override;
  void drawXbm(int x, int y, const uint8_t* bits, int w, int h) override;
  uint16_t getTextWidth(const char* str) override;
  void translateUTF8ToBlocks(char* dest, const char* src,
                             size_t dest_size) override;
  void endFrame() override;
  bool getTouch(int* x, int* y) override;
  bool installRuntimeFont(uint8_t* data, size_t size);
};
