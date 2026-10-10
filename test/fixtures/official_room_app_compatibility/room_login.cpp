// Reuse actual Companion request/response and route-admission methods from
// the existing delayed-reply fixture, with only radio login envelopes mocked.
#include "companion_login_fixture.inc"

static Bytes fromHex(const char* text) {
  Bytes bytes;
  for (size_t at = 0; text[at]; at += 2) {
    unsigned byte; assert(text[at + 1]); assert(sscanf(text + at, "%2x", &byte) == 1);
    bytes.push_back(uint8_t(byte));
  }
  return bytes;
}
static void hex(const Bytes& bytes) {
  for (uint8_t byte : bytes) printf("%02x", byte);
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const Bytes command = fromHex(argv[1]);
  const unsigned format = unsigned(atoi(argv[2]));
  assert(command.size() >= 33 && command.size() < MAX_FRAME_SIZE && format < 3);
  assert(command[0] == CMD_SEND_LOGIN);
  Fixture fixture;
  memcpy(fixture.mesh.recipient.id.pub_key, command.data() + 1, 32);
  memcpy(fixture.mesh.cmd_frame, command.data(), command.size());
  fixture.mesh.handleRequestFrame(command.size());
  assert(slot(fixture, Tracker::Login).phase == Tracker::AwaitRadio);
  const uint32_t request_tag = slot(fixture, Tracker::Login).tag;
  uint8_t data[13] = {};
  const uint32_t timestamp = 0x76543210;
  memcpy(data, &timestamp, 4);
  if (format == 2) { data[4] = 'O'; data[5] = 'K'; }
  else {
    data[4] = RESP_SERVER_LOGIN_OK; data[5] = 1;
    data[6] = uint8_t(format); data[7] = format ? 3 : 1; data[12] = 13;
  }
  fixture.mesh.onContactResponse(fixture.mesh.recipient, data, format == 2 ? 6 : 13);
  fixture.drain();
  assertSentBeforeFinal(fixture.wire(), Tracker::Login, request_tag);
  assert(count(fixture.wire(), PUSH_CODE_LOGIN_SUCCESS) == 1);
  printf("LOGIN:"); hex(frameWith(fixture.wire(), RESP_CODE_SENT)); printf(":");
  hex(frameWith(fixture.wire(), PUSH_CODE_LOGIN_SUCCESS)); printf("\n");
  return 0;
}
