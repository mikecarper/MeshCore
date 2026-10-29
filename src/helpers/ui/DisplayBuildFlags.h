#pragma once

// DISPLAY_CLASS is supplied as a build token (for example SSD1306Display).
// Convert it to a boolean that shared ESP32 code can use without including a
// concrete driver. Undefined identifiers evaluate to zero in a #if expression.
#define MESHCORE_NULL_DISPLAY_CLASS_NullDisplayDriver 1
#define MESHCORE_SMALL_DISPLAY_CLASS_SH1106Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_SSD1306Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_HeltecV4Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_ST7735Display 1
#define MESHCORE_SMALL_DISPLAY_CLASS_U8g2Display 1
#define MESHCORE_EINK_DISPLAY_CLASS_GxEPDDisplay 1
#define MESHCORE_EINK_DISPLAY_CLASS_E213Display 1
#define MESHCORE_EINK_DISPLAY_CLASS_E290Display 1
#define MESHCORE_COLOR_DISPLAY_CLASS_ST7735Display 1
#define MESHCORE_COLOR_DISPLAY_CLASS_ST7789Display 1
#define MESHCORE_COLOR_DISPLAY_CLASS_ST7789LCDDisplay 1
#define MESHCORE_COLOR_DISPLAY_CLASS_HeltecV4Display 1
#define MESHCORE_COLOR_DISPLAY_CLASS_NV3001BDisplay 1
#define MESHCORE_COLOR_DISPLAY_CLASS_LGFXDisplay 1
#define MESHCORE_COLOR_DISPLAY_CLASS_SCIndicatorDisplay 1
#define MESHCORE_COLOR_DISPLAY_CLASS_WioTrackerL2Display 1
#define MESHCORE_DISPLAY_TOKEN_JOIN_INNER(prefix, name) prefix##name
#define MESHCORE_DISPLAY_TOKEN_JOIN(prefix, name) \
  MESHCORE_DISPLAY_TOKEN_JOIN_INNER(prefix, name)

#if (defined(DISPLAY_CLASS) \
    && !MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_NULL_DISPLAY_CLASS_, DISPLAY_CLASS)) \
    || CARDPUTER_ADV_UI
  #define MESHCORE_HAS_REAL_DISPLAY 1
#endif

// Shared idle defaults apply to every board and role using these drivers.
#if defined(DISPLAY_CLASS) \
    && MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_EINK_DISPLAY_CLASS_, DISPLAY_CLASS)
  #define MESHCORE_HAS_EINK_DISPLAY 1
#endif

#if (defined(DISPLAY_CLASS) \
    && MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_COLOR_DISPLAY_CLASS_, DISPLAY_CLASS)) \
    || CARDPUTER_ADV_UI
  #define MESHCORE_HAS_COLOR_DISPLAY 1
#endif

// These drivers back physically compact panels where the message-channel
// selector consumes too much of the useful text area. Do not infer this from
// DisplayDriver::height(): the 160x80 ST7735 is still physically compact.
// This footer policy is separate from the runtime small-font cutoff (<160px
// on both axes); retaining the flag also retains the full message buffer.
#if defined(DISPLAY_CLASS) \
    && MESHCORE_DISPLAY_TOKEN_JOIN(MESHCORE_SMALL_DISPLAY_CLASS_, DISPLAY_CLASS)
  #define MESHCORE_HAS_SMALL_DISPLAY 1
#endif

#ifndef UI_SMALL_MESSAGE_FONT
  #if defined(MESHCORE_HAS_SMALL_DISPLAY)
    #define UI_SMALL_MESSAGE_FONT 1
  #else
    #define UI_SMALL_MESSAGE_FONT 0
  #endif
#endif

#undef MESHCORE_DISPLAY_TOKEN_JOIN
#undef MESHCORE_DISPLAY_TOKEN_JOIN_INNER
#undef MESHCORE_COLOR_DISPLAY_CLASS_WioTrackerL2Display
#undef MESHCORE_COLOR_DISPLAY_CLASS_SCIndicatorDisplay
#undef MESHCORE_COLOR_DISPLAY_CLASS_LGFXDisplay
#undef MESHCORE_COLOR_DISPLAY_CLASS_NV3001BDisplay
#undef MESHCORE_COLOR_DISPLAY_CLASS_ST7789LCDDisplay
#undef MESHCORE_COLOR_DISPLAY_CLASS_HeltecV4Display
#undef MESHCORE_COLOR_DISPLAY_CLASS_ST7789Display
#undef MESHCORE_COLOR_DISPLAY_CLASS_ST7735Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_U8g2Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_ST7735Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_SSD1306Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_HeltecV4Display
#undef MESHCORE_SMALL_DISPLAY_CLASS_SH1106Display
#undef MESHCORE_EINK_DISPLAY_CLASS_E290Display
#undef MESHCORE_EINK_DISPLAY_CLASS_E213Display
#undef MESHCORE_EINK_DISPLAY_CLASS_GxEPDDisplay
#undef MESHCORE_NULL_DISPLAY_CLASS_NullDisplayDriver
