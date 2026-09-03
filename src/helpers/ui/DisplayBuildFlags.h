#pragma once

// DISPLAY_CLASS is supplied as a build token (for example SSD1306Display).
// Convert it to a boolean that shared ESP32 code can use without including a
// concrete driver. Undefined identifiers evaluate to zero in a #if expression.
#define MESHCORE_NULL_DISPLAY_CLASS_NullDisplayDriver 1
#define MESHCORE_SMALL_DISPLAY_CLASS_SH1106Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_SSD1306Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_ST7735Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_U8g2Display 1
#define MESHCORE_DISPLAY_TOKEN_JOIN_INNER(prefix, name) prefix##name
#define MESHCORE_DISPLAY_TOKEN_JOIN(prefix, name) \
  MESHCORE_DISPLAY_TOKEN_JOIN_INNER(prefix, name)

#if defined(DISPLAY_CLASS) \
    && !MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_NULL_DISPLAY_CLASS_, DISPLAY_CLASS)
  #define MESHCORE_HAS_REAL_DISPLAY 1
#endif

// These drivers back physically compact panels where the message-channel
// selector consumes too much of the useful text area. Do not infer this from
// DisplayDriver::height(): larger scaled TFTs intentionally expose the same
// 128x64 logical canvas.
#if defined(DISPLAY_CLASS) \
    && MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_SMALL_DISPLAY_CLASS_, DISPLAY_CLASS)
  #define MESHCORE_HAS_SMALL_DISPLAY 1
#endif

#undef MESHCORE_DISPLAY_TOKEN_JOIN
#undef MESHCORE_DISPLAY_TOKEN_JOIN_INNER
#undef MESHCORE_SMALL_DISPLAY_CLASS_U8g2Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_ST7735Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_SSD1306Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_SH1106Display
#undef MESHCORE_NULL_DISPLAY_CLASS_NullDisplayDriver
