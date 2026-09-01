#pragma once

#include <helpers/ui/LGFXDisplay.h>
#include "IndicatorFontClient.h"
#include "IndicatorTouch.h"
#include <driver/gpio.h>

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#include <lgfx/v1/platforms/esp32s3/Panel_RGB.hpp>
#include <lgfx/v1/platforms/esp32s3/Bus_RGB.hpp>

class LGFX : public lgfx::LGFX_Device
{
  lgfx::Panel_ST7701 _panel_instance;
  lgfx::Bus_RGB _bus_instance;
  lgfx::Light_PWM _light_instance;
  IndicatorTouch _touch_instance;

public:
  const uint16_t screenWidth = 480;
  const uint16_t screenHeight = 480;

  bool hasButton(void) { return true; }

  LGFX(void)
  {
    {
        auto cfg = _panel_instance.config();
        cfg.memory_width = 480;
        cfg.memory_height = 480;
        cfg.panel_width = screenWidth;
        cfg.panel_height = screenHeight;
        cfg.offset_x = 0;
        cfg.offset_y = 0;
        cfg.offset_rotation = 1;
        _panel_instance.config(cfg);
    }

    {
        auto cfg = _panel_instance.config_detail();
        // Chip-select is driven through the board's I2C expander below.
        cfg.pin_cs = GPIO_NUM_NC;
        cfg.pin_sclk = 41;
        cfg.pin_mosi = 48;
        cfg.use_psram = 1;
        _panel_instance.config_detail(cfg);
    }

    {
        auto cfg = _bus_instance.config();
        cfg.panel = &_panel_instance;

        cfg.freq_write = 8000000;
        cfg.pin_henable = 18;

        cfg.pin_pclk = 21;
        cfg.pclk_active_neg = 0;
        cfg.pclk_idle_high = 0;
        cfg.de_idle_high = 1;

        cfg.pin_hsync = 16;
        cfg.hsync_polarity = 0;
        cfg.hsync_front_porch = 10;
        cfg.hsync_pulse_width = 8;
        cfg.hsync_back_porch = 50;

        cfg.pin_vsync = 17;
        cfg.vsync_polarity = 0;
        cfg.vsync_front_porch = 10;
        cfg.vsync_pulse_width = 8;
        cfg.vsync_back_porch = 20;

        cfg.pin_d0 = 15;
        cfg.pin_d1 = 14;
        cfg.pin_d2 = 13;
        cfg.pin_d3 = 12;
        cfg.pin_d4 = 11;
        cfg.pin_d5 = 10;
        cfg.pin_d6 = 9;
        cfg.pin_d7 = 8;
        cfg.pin_d8 = 7;
        cfg.pin_d9 = 6;
        cfg.pin_d10 = 5;
        cfg.pin_d11 = 4;
        cfg.pin_d12 = 3;
        cfg.pin_d13 = 2;
        cfg.pin_d14 = 1;
        cfg.pin_d15 = 0;

        _bus_instance.config(cfg);
    }
    _panel_instance.setBus(&_bus_instance);

    {
        auto cfg = _light_instance.config();
        cfg.pin_bl = 45;
        _light_instance.config(cfg);
    }
    _panel_instance.light(&_light_instance);

    {
        auto cfg = _touch_instance.config();
        cfg.pin_cs = GPIO_NUM_NC;
        cfg.x_min = 0;
        cfg.x_max = 479;
        cfg.y_min = 0;
        cfg.y_max = 479;
        cfg.pin_int = GPIO_NUM_NC;
        cfg.pin_rst = GPIO_NUM_NC;
        cfg.bus_shared = false;
        cfg.offset_rotation = 2;

        cfg.i2c_port = 0;
        cfg.i2c_addr = 0x48;
        cfg.pin_sda = 39;
        cfg.pin_scl = 40;
        cfg.freq = 400000;
        _touch_instance.config(cfg);
        _panel_instance.setTouch(&_touch_instance);
    }

    setPanel(&_panel_instance);
  }
};

class SCIndicatorDisplay : public LGFXDisplay {
  LGFX disp;
  uint8_t expander_address = 0;
  uint16_t expander_output = 0;
  bool panel_chip_select_released = false;

  static constexpr gpio_num_t BACKLIGHT_PIN = GPIO_NUM_45;
  static constexpr uint8_t I2C_PORT = 0;
  static constexpr uint32_t I2C_FREQUENCY = 400000;
  static constexpr uint8_t EXPANDER_OUTPUT_REGISTER = 0x02;
  static constexpr uint8_t EXPANDER_CONFIG_REGISTER = 0x06;
  static constexpr uint16_t PANEL_CS_MASK = 1U << 4;
  static constexpr uint16_t PANEL_RESET_MASK = 1U << 5;
  static constexpr uint16_t TOUCH_INTERRUPT_MASK = 1U << 6;
  static constexpr uint16_t TOUCH_RESET_MASK = 1U << 7;

  static void setBacklight(bool enabled) {
    // Detach any stale LEDC routing before changing the level. Reasserting a
    // plain GPIO level makes wake reliable even if a previous image left the
    // backlight PWM channel stopped or assigned elsewhere.
    gpio_reset_pin(BACKLIGHT_PIN);
    gpio_set_direction(BACKLIGHT_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(BACKLIGHT_PIN, enabled ? 1 : 0);
  }

  static bool readExpanderRegister(uint8_t address, uint8_t reg,
                                   uint16_t& value) {
    uint8_t data[2];
    if (!lgfx::i2c::readRegister(I2C_PORT, address, reg, data, sizeof(data),
                                 I2C_FREQUENCY)
             .has_value()) {
      return false;
    }
    value = data[0];
    value |= static_cast<uint16_t>(data[1]) << 8;
    return true;
  }

  static bool writeExpanderRegister(uint8_t address, uint8_t reg,
                                    uint16_t value) {
    uint8_t data[] = {
        reg,
        static_cast<uint8_t>(value),
        static_cast<uint8_t>(value >> 8),
    };
    return lgfx::i2c::transactionWrite(I2C_PORT, address, data, sizeof(data),
                                       I2C_FREQUENCY)
        .has_value();
  }

  bool prepareControllersAt(uint8_t address) {
    uint16_t config;
    if (!readExpanderRegister(address, EXPANDER_OUTPUT_REGISTER,
                              expander_output)
        || !readExpanderRegister(address, EXPANDER_CONFIG_REGISTER, config)) {
      return false;
    }

    // Set safe output latches before changing pin directions. Keep LCD CS
    // inactive while both controllers are held in reset.
    expander_output |= PANEL_CS_MASK;
    expander_output &= ~(PANEL_RESET_MASK | TOUCH_RESET_MASK);
    if (!writeExpanderRegister(address, EXPANDER_OUTPUT_REGISTER,
                               expander_output)) {
      return false;
    }

    config &= ~(PANEL_CS_MASK | PANEL_RESET_MASK | TOUCH_RESET_MASK);
    config |= TOUCH_INTERRUPT_MASK;
    if (!writeExpanderRegister(address, EXPANDER_CONFIG_REGISTER, config)) {
      return false;
    }
    delay(10);

    expander_output |= PANEL_RESET_MASK | TOUCH_RESET_MASK;
    if (!writeExpanderRegister(address, EXPANDER_OUTPUT_REGISTER,
                               expander_output)) {
      return false;
    }
    delay(120);

    // Hold LCD CS active while the graphics driver emits the ST7701 init
    // sequence over the shared clock and data pins.
    expander_output &= ~PANEL_CS_MASK;
    if (!writeExpanderRegister(address, EXPANDER_OUTPUT_REGISTER,
                               expander_output)) {
      return false;
    }
    expander_address = address;
    return true;
  }

  bool prepareControllers() {
    if (!lgfx::i2c::init(I2C_PORT, PIN_BOARD_SDA, PIN_BOARD_SCL).has_value()) {
      return false;
    }
    return prepareControllersAt(0x20) || prepareControllersAt(0x39);
  }

  bool releasePanelChipSelect() {
    if (expander_address == 0) return false;
    expander_output |= PANEL_CS_MASK;
    return writeExpanderRegister(expander_address, EXPANDER_OUTPUT_REGISTER,
                                 expander_output);
  }

  bool releasePanelChipSelectWithRetry() {
    // LovyanGFX initializes the touch controller on this same I2C bus. On a
    // warm ESP32-S3 reset its final transaction can briefly leave the
    // expander unavailable even though the RGB panel and render buffer were
    // initialized successfully. Re-open the bus and retry before LoRa starts
    // using the panel's shared SCLK/MOSI pins.
    for (uint8_t attempt = 0; attempt < 10; ++attempt) {
      if (releasePanelChipSelect()) return true;
      delay(5);
      lgfx::i2c::init(I2C_PORT, PIN_BOARD_SDA, PIN_BOARD_SCL);
    }
    return false;
  }

public:
#if defined(INDICATOR_TRANSPORT_RENDER_PROFILE)
  // Full builds always use the established 160x160 logical layout. State it
  // explicitly here because PlatformIO's inherited flag ordering can leave
  // the variant's historical UI_COORD_SCALE value visible in this header.
  // The 480px canvas is allocated before radio/transport startup; begin()
  // retains its 320px emergency fallback if that allocation is unavailable.
  SCIndicatorDisplay() : LGFXDisplay(160, 160, 3, 1.0f, disp) {}
#else
  SCIndicatorDisplay() : LGFXDisplay(480, 480, disp) {}
#endif

  bool begin() {
    if (!prepareControllers()) return false;
    const bool initialized = LGFXDisplay::begin();
    if (!initialized) return false;

    // A failed CS release must not discard an otherwise valid 115 KiB render
    // buffer and leave the LCD showing its pre-reset scanout forever. Keep the
    // UI alive and retry from startFrame(); the common case succeeds here
    // before the external LoRa radio is initialized.
    panel_chip_select_released = releasePanelChipSelectWithRetry();
    setBacklight(true);
    size_t fontSize;
    uint8_t* fontData = IndicatorFontClient::load(fontSize);
    if (fontData != nullptr) {
      const bool installed = installRuntimeFont(fontData, fontSize);
#ifdef INDICATOR_WIFI_FONT_RECOVERY
      if (installed) {
        IndicatorFontClient::noteRuntimeFontInstalled();
      } else {
        IndicatorFontClient::noteRuntimeFontInvalid();
      }
#endif
    }
    return true;
  }

  void startFrame(ColorVal bkg = UIColor::window_bkg) override {
    if (!panel_chip_select_released) {
      panel_chip_select_released = releasePanelChipSelectWithRetry();
    }
    LGFXDisplay::startFrame(bkg);
  }

#ifdef INDICATOR_WIFI_FONT_RECOVERY
  void serviceFontRecovery() {
    size_t fontSize = 0;
    uint8_t* fontData = IndicatorFontClient::serviceRecovery(fontSize);
    if (fontData == nullptr) return;
    if (installRuntimeFont(fontData, fontSize)) {
      IndicatorFontClient::noteRuntimeFontInstalled();
    } else {
      IndicatorFontClient::noteRecoveredFontInvalid();
    }
  }
#endif

  void turnOn() override {
    setBacklight(true);
    _isOn = true;
  }

  void turnOff() override {
    setBacklight(false);
    _isOn = false;
  }
};
