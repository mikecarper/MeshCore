#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/esp32/BrowserOtaSeederMailbox.h>

using mesh::ota::BrowserOtaSeederMailbox;

int main() {
  BrowserOtaSeederMailbox box;
  uint8_t request[] = {'M', 'S', 1, 1};
  uint8_t output[11] = {}, response[] = {'m', 's', 1, 0, 1, 0};
  for (size_t i = 0; i < sizeof(response) - 1; ++i) response[5] ^= response[i];
  uint32_t first = 0, repeated = 0;
  size_t length = 0;
  assert(!box.request(first, output, sizeof(output), length));
  assert(box.submit(request, sizeof(request)));
  assert(box.request(first, output, sizeof(output), length));
  assert(length == sizeof(request) && !memcmp(output, request, length));
  assert(box.request(repeated, output, sizeof(output), length) && first == repeated);
  assert(!box.response(first + 1, response, sizeof(response)));
  response[5] ^= 1;
  assert(!box.response(first, response, sizeof(response)));
  response[5] ^= 1;
  assert(box.response(first, response, sizeof(response)));
  assert(!box.response(first, response, sizeof(response)));
  assert(box.available() == sizeof(response) && box.peek() == 'm');
  for (uint8_t byte : response) assert(box.read() == byte);
  assert(box.peek() == -1 && box.read() == -1);
  assert(box.submit(request, sizeof(request)));
  assert(box.request(repeated, output, sizeof(output), length) && repeated != first);
  assert(!box.response(first, response, sizeof(response)));
  box.reset();
  assert(!box.response(repeated, response, sizeof(response)));
  assert(!box.request(repeated, output, sizeof(output), length));

  uint8_t read[] = {'M','S',3,0,0,0,0,0,192,0,0};
  for (size_t i = 2; i < sizeof(read) - 1; ++i) read[10] ^= read[i];
  assert(box.submit(read, sizeof(read)));
  assert(box.request(first, output, sizeof(output), length));
  uint8_t reply[197] = {'m', 's', 3, 0};
  for (size_t i = 0; i < sizeof(reply) - 1; ++i) reply[196] ^= reply[i];
  assert(!box.response(first, reply, sizeof(reply) - 1));
  assert(box.response(first, reply, sizeof(reply)));
  assert(box.available() == sizeof(reply));
  read[8] = 193; read[10] = 3 ^ 193;
  assert(!box.submit(read, sizeof(read)));
  request[2] = 4; request[3] = 4; // host write/storage ops are forbidden
  assert(!box.submit(request, sizeof(request)));
}
