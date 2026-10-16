#include <NonBlockingRtttl.h>
static_assert(sizeof(unsigned long) == 4, "Use device-width unsigned long");
uint32_t now_ms = 0;
unsigned tone_calls = 0;
unsigned silence_calls = 0;
extern unsigned long noteDelay;
static const char rising[] = "Startup:d=4,o=5,b=160:16c6,16e6,8g6";
static const char empty[] = "Empty:d=4,o=5,b=160:";
static void quit(unsigned code) {
  __asm__ volatile("int $0x80" : : "a"(1), "b"(code));
  __builtin_unreachable();
}
static void check(bool okay, unsigned line) {
  if (!okay) quit(line);
}
#define CHECK(expression) check((expression), __LINE__)

static void advance(uint32_t amount) {
  now_ms += amount;
  rtttl::play();
}
static void checkStartup(uint32_t origin) {
  now_ms = origin;
  tone_calls = silence_calls = 0;
  rtttl::begin(25, rising);
  CHECK(rtttl::isPlaying() && tone_calls == 0);
  rtttl::play();
  CHECK(tone_calls == 1 && rtttl::isPlaying());
  for (unsigned i = 0; i < 4; ++i) rtttl::play();
  CHECK(tone_calls == 1 && rtttl::isPlaying());
  advance(93);
  CHECK(tone_calls == 1 && rtttl::isPlaying());
  advance(1);
  CHECK(tone_calls == 2 && rtttl::isPlaying());
  advance(93);
  CHECK(tone_calls == 2 && rtttl::isPlaying());
  advance(1);
  CHECK(tone_calls == 3 && rtttl::isPlaying());
  advance(187);
  CHECK(tone_calls == 3 && rtttl::isPlaying());
  advance(1);
  CHECK(tone_calls == 3 && rtttl::done());
}
extern "C" void _start() {
  // Identical note timing, including first note in the upper counter half.
  for (unsigned i = 0; i < 5; ++i) {
    const uint32_t origins[] = {0, 1000, 0x80000000U, 0xfffffff0U, 0xffffffffU - 100};
    checkStartup(origins[i]);
  }
  // A foreground loop which crosses an expired pre-wrap deadline must
  // continue the melody and allow the pending control clip to start.
  now_ms = 0xffffffffU - 100;
  tone_calls = 0;
  rtttl::begin(25, rising);
  rtttl::play();
  CHECK(tone_calls == 1);
  advance(90);
  CHECK(tone_calls == 1);
  now_ms = 20;
  rtttl::play();
  CHECK(tone_calls == 2 && rtttl::isPlaying());
  advance(94);
  CHECK(tone_calls == 3);
  advance(188);
  CHECK(rtttl::done());

#ifndef RTTTL_NO_LOOPS
  // Repeating gap also wraps safely; no new note during the gap.
  now_ms = 0xffffffffU - 500;
  tone_calls = 0;
  rtttl::begin(25, rising, 2, 1000);
  rtttl::play(); advance(94); advance(94); advance(188);
  CHECK(rtttl::isPlaying() && tone_calls == 3);
  advance(999);
  CHECK(tone_calls == 3);
  advance(1);
  CHECK(tone_calls == 4);
  advance(94); advance(94); advance(188);
  CHECK(rtttl::done() && tone_calls == 6);
#endif

  // A rest uses the duration directly, without the audible note's extra ms.
  now_ms = 0xfffffff0U;
  tone_calls = 0;
  rtttl::begin(25, "Rest:d=4,o=5,b=160:16p,16c6");
  rtttl::play();
  CHECK(tone_calls == 0 && rtttl::isPlaying());
  advance(92);
  CHECK(tone_calls == 0 && rtttl::isPlaying());
  advance(1);
  CHECK(tone_calls == 1 && rtttl::isPlaying());
  advance(93);
  CHECK(rtttl::isPlaying());
  advance(1);
  CHECK(rtttl::done());

  // Empty melody, stop, and replacement do not remain artificially busy.
  now_ms = 0xfffffff0U;
  tone_calls = 0;
  rtttl::begin(25, empty);
  rtttl::play(); CHECK(rtttl::done() && tone_calls == 0);
  rtttl::begin(25, rising); rtttl::play();
  CHECK(tone_calls == 1);
  rtttl::stop(); CHECK(rtttl::done());
  advance(500); CHECK(tone_calls == 1);
  rtttl::begin(25, rising); rtttl::play();
  CHECK(tone_calls == 2 && rtttl::isPlaying());
  rtttl::stop();
  quit(0);
}
