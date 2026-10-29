#pragma once

#include <stdint.h>

/** ES8311 tone driver serviced in bounded foreground chunks. */
class CardputerSpeaker {
private:
  bool i2c_initialized = false;
  bool i2s_initialized = false;
  bool tone_pending = false;
  bool draining = false;
  uint32_t busy_time = 0;
  uint32_t last_progress = 0;
  uint32_t drain_until = 0;
  uint64_t bytes_sent = 0;
  uint64_t total_bytes = 0;
  int32_t frequency_hz = 0;
  float volume = 0;

  bool initI2C();
  bool initI2S();
  bool ready();
  bool writeReg(uint8_t reg, uint8_t val);

public:
  CardputerSpeaker() {}

  void begin();
  // Both entry points queue playback; neither waits for audio to finish.
  void playTone(int32_t frequency_hz, uint32_t duration_ms, float volume = 0.5f);
  void queueTone(int32_t frequency_hz, uint32_t duration_ms, float volume = 0.5f);
  // Cancel pending playback, stop DMA and power down the codec.
  void sleep();
  // At most one AUDIO_BUFFER_SIZE chunk and one zero-timeout write per call.
  void processQueue();
  bool isBusy() const { return tone_pending || draining; }
};
