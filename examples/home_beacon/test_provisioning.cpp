#include <cassert>
#include <cstring>
#include "Provisioning.h"

using mesh::home_beacon::Provisioning;
using mesh::home_beacon::Settings;
using mesh::home_beacon::validSettings;
using Result = Provisioning::Result;
using Stage = Provisioning::Stage;

int main() {
  Provisioning p;
  assert(p.line("ignored", 7) == Result::Invalid);
  p.begin();
  assert(p.line("", 0) == Result::Invalid && p.stage() == Stage::Ssid);
  assert(p.line("home", 4) == Result::Accepted);
  assert(p.line("short", 5) == Result::Invalid && p.stage() == Stage::Password);
  assert(p.line("validpass", 9) == Result::Accepted);
  assert(p.line("1234q678", 8) == Result::Invalid && p.stage() == Stage::Id);
  assert(p.line("01020304", 8) == Result::Accepted);
  assert(p.line("00000000000000000000000000000000", 32) == Result::Invalid);
  assert(p.line("0102030405060708090a0b0c0d0e0f10", 32) == Result::Complete);
  Settings s = p.settings();
  assert(validSettings(s) && s.id[0] == 1 && s.key_len == 16 && s.key[15] == 16);
  assert(std::strcmp(s.ssid, "home") == 0 && std::strcmp(s.password, "validpass") == 0);
  p.clear();
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&p.settings());
  for (size_t i = 0; i < sizeof(Settings); ++i) assert(bytes[i] == 0);
  s.magic = 0; assert(!validSettings(s));
  p.begin();
  assert(p.line("home2", 5) == Result::Accepted);
  assert(p.line("", 0) == Result::Invalid);
  assert(p.line("password", 8) == Result::Accepted);
  assert(p.line("deadbeef", 8) == Result::Accepted);
  const char* key = "0102030405060708090a0b0c0d0e0f100102030405060708090a0b0c0d0e0f10";
  assert(p.line(key, strlen(key)) == Result::Complete);
  assert(p.settings().key_len == 32 && validSettings(p.settings()));
}
