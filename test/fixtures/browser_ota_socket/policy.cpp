#include <helpers/BrowserOtaCommandPolicy.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using mesh::browser_ota::allowed;
static uint8_t target[32];

static bool local(const std::string& value) {
  std::vector<uint8_t> frame{66};
  frame.insert(frame.end(), value.begin(), value.end());
  return allowed(frame.data(), frame.size(), false, nullptr);
}
static bool remote(const std::string& value, bool guard = true) {
  std::vector<uint8_t> frame{2, 1, 0, 0, 0, 0, 0};
  frame.insert(frame.end(), target, target + 6);
  frame.insert(frame.end(), {'A', 'B', '|'});
  frame.insert(frame.end(), value.begin(), value.end());
  return allowed(frame.data(), frame.size(), guard, guard ? target : nullptr);
}
int main() {
  for (unsigned i = 0; i < 32; ++i) target[i] = i + 1;
  for (const char* text : {"get ota.direct", "get ota.direct.capabilities", "get ota.direct.radio",
      "get ota.direct.reply 0", "get ota.direct.reply 160", "get usb.raw.max", "get public.key",
      "ota config", "ota stats", "ota folder", "get radio", "get tempradio2", "get radio2.status",
      "set ota.direct.radio radio", "set ota.direct.radio radio2", "ota config hops 0",
      "ota config autofetch off", "ota config autoinstall off", "set tempradio2 off",
      "tempradio 908.5,500,5,5,120,128", "set tempradio2 908.5,500,5,5,rxtx,120,128"})
    assert(local(text));
  for (const char* text : {"get private.key", "export private", "reboot", "set wifi.enabled 0",
      "set ota.direct.radio radio;reboot", "get ota.direct.reply 161", "ota ls 129", "ota ls 0",
      "tempradio 908.5,500,5,5,120,128;reboot", "ota install", "ota cancel", "ota pull 12345678 flash"})
    assert(!local(text));
  for (const char* text : {"get public.key", "ver", "ota status", "ota self", "ota ram",
      "ota installhash", "ota ls 128", "ota pull 12345678 flash", "ota install", "ota cancel",
      "ota config hops 0", "tempradio 908.5,500,5,5,120,128",
      "tempradio.ifidle 908.5,500,5,5,120,128", "set tempradio2 908.5,500,5,5,rxtx,120,128",
      "set repeat off", "set repeat on", "set radio.flood off", "set radio.flood on"}) {
    assert(remote(text)); assert(!remote(text, false));
  }
  assert(remote("ota installhash " + std::string(64, 'a')));
  for (const char* text : {"reboot", "erase", "set password hacked", "get private.key",
      "ota pull 12345678 flash;reboot", "ota pull 1234567z flash", "ota installhash 1234",
      "ota config hops 7", "tempradio 908.5,500,5,5,120,128\nreboot"}) assert(!remote(text));
  uint8_t private_key_export[] = {23}, private_key_import[] = {24}, factory_reset[] = {51, 'r', 'e', 's', 'e', 't'};
  assert(!allowed(private_key_export, sizeof(private_key_export), true, target));
  assert(!allowed(private_key_import, sizeof(private_key_import), true, target));
  assert(!allowed(factory_reset, sizeof(factory_reset), true, target));
  uint8_t start[] = {1,3,' ',' ',' ',' ',' ',' ','m','c','c','l','i'};
  uint8_t contacts[] = {4}, clock[] = {5}, query[] = {22,4};
  for (auto pair : {std::pair<const uint8_t*, size_t>{start, sizeof(start)},
                    {contacts, sizeof(contacts)}, {clock, sizeof(clock)}, {query, sizeof(query)}})
    assert(allowed(pair.first, pair.second, false, nullptr));
  uint8_t advert_path[35] = {42, 0};
  memcpy(advert_path + 2, target, 32);
  assert(allowed(advert_path, 34, false, nullptr));
  assert(allowed(advert_path, 34, true, target));
  assert(!allowed(advert_path, 33, false, nullptr));
  assert(!allowed(advert_path, 35, false, nullptr));
  advert_path[1] = 1;
  assert(!allowed(advert_path, 34, false, nullptr));
  uint8_t guard[37] = {52,1}; memcpy(guard + 2, target, 32); guard[34] = 2;
  assert(allowed(guard, 35, false, nullptr));
  guard[1] = 4; guard[34] = 'a'; guard[35] = 'b'; guard[36] = '|';
  assert(allowed(guard, 37, true, target) && !allowed(guard, 37, false, nullptr));
  guard[2] ^= 1; assert(!allowed(guard, 37, true, target)); guard[2] ^= 1;
  uint8_t login[48] = {26}; memcpy(login + 1, target, 32);
  assert(allowed(login, 33, true, target) && !allowed(login, 33, false, nullptr));
  login[1] ^= 1; assert(!allowed(login, 33, true, target));
  uint8_t raw[] = {65,1,0x32,0,1};
  assert(allowed(raw, sizeof(raw), true, target) && !allowed(raw, sizeof(raw), false, nullptr));
  raw[2] = 0x22; assert(!allowed(raw, sizeof(raw), true, target));
  raw[2] = 0x32; raw[3] = 1; assert(!allowed(raw, sizeof(raw), true, target));
  puts("PASS browser OTA command allowlist");
}
