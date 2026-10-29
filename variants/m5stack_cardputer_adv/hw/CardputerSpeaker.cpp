#include "CardputerSpeaker.h"

#include "globals.h"

#include <Wire.h>
#include <driver/i2s.h> // because i2s_std.h not exists for some reason
#include <cmath>

namespace {
bool due(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}
const uint32_t AUDIO_STALL_MS = 1000;
const uint32_t AUDIO_DMA_DRAIN_MS =
    (4UL * AUDIO_BUFFER_SIZE * 1000UL + AUDIO_SAMPLE_RATE - 1) / AUDIO_SAMPLE_RATE;
}

bool CardputerSpeaker::initI2C() {
  // From M5Unified
  // Mark partial initialization too, so ready() can power it down on failure.
  i2c_initialized = true;
  if (!writeReg(0x00, 0x80) // RESET / CSM POWER ON
      || !writeReg(0x01, 0xB5) // CLOCK_MANAGER / MCLK=BCLK
      || !writeReg(0x02, 0x18) // CLOCK_MANAGER / MULT_PRE=3
      || !writeReg(0x0D, 0x01) // Power up analog circuitry
      || !writeReg(0x12, 0x00) // Power up DAC
      || !writeReg(0x13, 0x10) // Enable output to HP drive
      || !writeReg(0x32, 0xBF) // DAC volume (+/-0 dB)
      || !writeReg(0x37, 0x08)) return false; // Bypass DAC equalizer

  // Codec is not ready immediately, it need some time to stabilize
  busy_time = millis() + AUDIO_WARMUP_MS;
  return true;
}

bool CardputerSpeaker::initI2S() {
  i2s_config_t i2s_config = { .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
                              .sample_rate = AUDIO_SAMPLE_RATE,
                              .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
                              .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
                              .communication_format = I2S_COMM_FORMAT_STAND_I2S,
                              .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
                              .dma_buf_count = 4,
                              .dma_buf_len = AUDIO_BUFFER_SIZE,
                              .use_apll = false,
                              .tx_desc_auto_clear = true,
                              .fixed_mclk = 0 };

  i2s_pin_config_t pin_config = {
    .bck_io_num = PIN_AUDIO_SCLK,
    .ws_io_num = PIN_AUDIO_LRCK,
    .data_out_num = PIN_AUDIO_DSDIN,
    .data_in_num = I2S_PIN_NO_CHANGE // Mic is not needed
  };

  if (i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL) != ESP_OK) return false;
  i2s_initialized = true;
  return i2s_set_pin(I2S_NUM_0, &pin_config) == ESP_OK;
}

bool CardputerSpeaker::writeReg(uint8_t reg, uint8_t val) {
  Wire1.beginTransmission(AUDIO_I2C_ADDRESS);
  Wire1.write(reg);
  Wire1.write(val);
  return Wire1.endTransmission() == 0;
}

bool CardputerSpeaker::ready() {
  if ((!i2s_initialized && !initI2S()) || (!i2c_initialized && !initI2C())) {
    sleep();
    return false;
  }
  return true;
}

void CardputerSpeaker::begin() { (void)ready(); }

void CardputerSpeaker::playTone(int32_t hz, uint32_t duration_ms, float level) {
  queueTone(hz, duration_ms, level);
}

void CardputerSpeaker::sleep() {
  tone_pending = draining = false;
  total_bytes = bytes_sent = 0;
  if (i2c_initialized) {
    writeReg(0x0E, 0xFF); // SYSTEM
    writeReg(0x12, 0x02); // SYSTEM
    writeReg(0x0D, 0xFA); // POWER
    i2c_initialized = false;
  }

  if (i2s_initialized) {
    i2s_driver_uninstall(I2S_NUM_0);
    i2s_initialized = false;
  }
}

void CardputerSpeaker::queueTone(int32_t hz, uint32_t duration_ms, float level) {
  if (hz <= 0 || hz > AUDIO_SAMPLE_RATE / 2 || !std::isfinite(level)
      || level <= 0 || duration_ms == 0) {
    sleep();
    return;
  }
  if (!ready()) return;
  frequency_hz = hz;
  volume = level > 1.0f ? 1.0f : level;
  bytes_sent = 0;
  total_bytes = (uint64_t(AUDIO_SAMPLE_RATE) * duration_ms / 1000ULL) * sizeof(int16_t);
  tone_pending = total_bytes != 0;
  draining = false;
  // A replacement discards all unsent old samples. Already submitted DMA is
  // bounded to four chunks and can finish without stalling the mesh loop.
  last_progress = millis();
}

void CardputerSpeaker::processQueue() {
  const uint32_t now = millis();
  if (draining && due(now, drain_until)) draining = false;
  if (!tone_pending || !due(now, busy_time)) return;

  // Regenerate the unsubmitted suffix from its exact byte offset. This also
  // handles an odd-byte partial write without retaining a heap/instance buffer.
  const uint64_t first_sample = bytes_sent / sizeof(int16_t);
  const size_t byte_offset = bytes_sent % sizeof(int16_t);
  const uint64_t remaining_samples = (total_bytes / sizeof(int16_t)) - first_sample;
  const size_t count = remaining_samples < AUDIO_BUFFER_SIZE
      ? size_t(remaining_samples) : AUDIO_BUFFER_SIZE;
  int16_t audio_buffer[AUDIO_BUFFER_SIZE];
  const double increment = 2.0 * M_PI * frequency_hz / AUDIO_SAMPLE_RATE;
  double phase = std::fmod(double(first_sample) * increment, 2.0 * M_PI);
  const int16_t amplitude = static_cast<int16_t>(volume * AUDIO_MAX_SAFE_VOLUME);
  for (size_t i = 0; i < count; ++i) {
    audio_buffer[i] = static_cast<int16_t>(std::sin(phase) * amplitude);
    phase += increment;
    if (phase >= 2.0 * M_PI) phase -= 2.0 * M_PI;
  }
  const size_t requested = count * sizeof(int16_t) - byte_offset;
  size_t written = 0;
  const esp_err_t result = i2s_write(I2S_NUM_0,
      reinterpret_cast<const uint8_t*>(audio_buffer) + byte_offset, requested, &written, 0);
  if ((result != ESP_OK && result != ESP_ERR_TIMEOUT) || written > requested) {
    sleep();
    return;
  }
  if (written != 0) {
    bytes_sent += written;
    last_progress = now;
    if (bytes_sent == total_bytes) {
      tone_pending = false;
      draining = true;
      drain_until = now + AUDIO_DMA_DRAIN_MS;
    }
  } else if (static_cast<uint32_t>(now - last_progress) >= AUDIO_STALL_MS + AUDIO_WARMUP_MS) {
    // A wedged driver must not hold the node awake indefinitely.
    sleep();
  }
}
