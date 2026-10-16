#define PIN_BUZZER 25
#define MESH_GPS_VOICE 1
#include <helpers/ui/buzzer.cpp>
static_assert(sizeof(unsigned long) == 4, "Use device-width unsigned long");
uint32_t now_ms = 0;
unsigned tone_calls = 0;
unsigned silence_calls = 0;
extern "C" void* memset(void* dst, int value, unsigned length) {
  unsigned char* bytes = static_cast<unsigned char*>(dst);
  for (unsigned i = 0; i < length; ++i) bytes[i] = static_cast<unsigned char>(value);
  return dst;
}
static void quit(unsigned code) {
  __asm__ volatile("int $0x80" : : "a"(1), "b"(code));
  __builtin_unreachable();
}
static void check(bool okay, unsigned line) {
  if (!okay) quit(line);
}
#define CHECK(expression) check((expression), __LINE__)
extern "C" void _start() {
  genericBuzzer buzzer;
  buzzer.begin();
  now_ms = 0xffffffffU - 100;
  CHECK(buzzer.speakGps(true));
  buzzer.loop();
  CHECK(tone_calls == 1 && buzzer.isPlaying() && !registers.ENABLE);
  now_ms = 0xffffffffU - 10;
  buzzer.loop();
  CHECK(tone_calls == 1 && buzzer.isPlaying());
  now_ms = 20;
  buzzer.loop();
  CHECK(tone_calls == 2 && buzzer.isPlaying() && !registers.ENABLE);
  now_ms += 94; buzzer.loop();
  CHECK(tone_calls == 3 && !registers.ENABLE);
  now_ms += 188; buzzer.loop();
  CHECK(rtttl::done() && !registers.ENABLE);
  buzzer.loop(); // Start the intentional speech gap.
  now_ms += 99; buzzer.loop();
  CHECK(!registers.ENABLE);
  now_ms += 1; buzzer.loop();
  CHECK(registers.ENABLE && buzzer.isPlaying() && HwPWM3.owned);
  buzzer.stop();
  CHECK(!registers.ENABLE && !buzzer.isPlaying() && !HwPWM3.owned);
  quit(0);
}
