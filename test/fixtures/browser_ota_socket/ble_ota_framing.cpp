#include <helpers/BleOtaFraming.h>
#include <cassert>
#include <cstring>

int main() {
  using namespace mesh::ble_ota;
  assert(isHello(MAGIC, sizeof(MAGIC)));
  Receiver rx;
  uint8_t input[MAX_FRAME]; for (size_t i = 0; i < sizeof(input); ++i) input[i] = i;
  uint8_t chunk[ATT_MIN_PAYLOAD], output[MAX_FRAME];
  for (size_t off = 0; off < sizeof(input);) {
    const size_t n = fragment(input, sizeof(input), off, chunk, sizeof(chunk));
    assert(n > HEADER && n <= ATT_MIN_PAYLOAD);
    const auto result = rx.accept(chunk, n);
    off += n - HEADER;
    assert(rx.received() == off);
    assert(result == (off == sizeof(input) ? Receiver::Result::Complete : Receiver::Result::Partial));
  }
  assert(rx.copyTo(output, sizeof(output)) == sizeof(input));
  assert(memcmp(input, output, sizeof(input)) == 0);
  uint8_t large[MAX_FRAME + HEADER];
  assert(fragment(input, sizeof(input), 0, large, sizeof(large)) == sizeof(input) + HEADER);
  assert(large[0] == FRAGMENT && large[1] == MAX_FRAME && large[2] == 0);
  assert(memcmp(large + HEADER, input, sizeof(input)) == 0);
  const uint8_t wrong_start[] = {FRAGMENT, 20, 1, 42};
  assert(rx.accept(wrong_start, sizeof(wrong_start)) == Receiver::Result::Invalid);
  const uint8_t first[] = {FRAGMENT, 20, 0, 42};
  const uint8_t changed_length[] = {FRAGMENT, 21, 1, 42};
  assert(rx.accept(first, sizeof(first)) == Receiver::Result::Partial);
  assert(rx.accept(changed_length, sizeof(changed_length)) == Receiver::Result::Invalid);
  const uint8_t overflow[] = {FRAGMENT, 1, 0, 42, 43};
  assert(rx.accept(overflow, sizeof(overflow)) == Receiver::Result::Invalid);
  assert(rx.accept(first, sizeof(first)) == Receiver::Result::Partial);
  rx.reset();
  assert(rx.accept(changed_length, sizeof(changed_length)) == Receiver::Result::Invalid);
  struct Frame { uint8_t len; uint8_t buf[176]; } queue[4] = {};
  int count = 0;
  const uint8_t log[] = {0x88, 7};
  const uint8_t response[] = {0x05, 1};
  const uint8_t push[] = {0x83, 2};
  assert(enqueue(queue, count, 4, false, log, sizeof(log)));
  // A response inserted during a fragmented best-effort head must not move it.
  assert(enqueue(queue, count, 4, true, response, sizeof(response)));
  assert(queue[0].buf[0] == 0x88 && queue[1].buf[0] == 0x05);
  assert(enqueue(queue, count, 4, true, push, sizeof(push)));
  assert(queue[0].buf[0] == 0x88 && queue[1].buf[0] == 0x05 && queue[2].buf[0] == 0x83);
  assert(enqueue(queue, count, 4, true, response, sizeof(response)));
  assert(queue[0].buf[0] == 0x88 && count == 4);
  // No best-effort tail can be evicted; the pinned best-effort head is safe.
  assert(!enqueue(queue, count, 4, true, response, sizeof(response)));
  assert(queue[0].buf[0] == 0x88 && count == 4);
}
