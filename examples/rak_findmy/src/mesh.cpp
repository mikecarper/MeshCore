// A direct MeshCore collar check on the same MCU as the Find My accessory.
// Radio and GPS operations run outside Bluetooth callbacks and the USB shell.
#include <zephyr/drivers/gpio.h>
#include "radio.h"
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#if __has_include(<zephyr/random/random.h>)
#include <zephyr/random/random.h>
#else
#include <zephyr/random/rand32.h>
#endif
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>
#include <Packet.h>
#include <Utils.h>
#include <helpers/AdvertDataLayout.h>
#include <helpers/TrackerProtocol.h>
#include <ed_25519.h>
#include <stdlib.h>
#include "gnss.h"
#include "TagPackets.h"
#ifdef RAK_NATIVE_PINGS
#include "fence.h"
#endif

extern "C" int tag_battery_mv(void);

namespace {
constexpr int kReplyTimeoutMs = 20000;
using namespace mesh::tracker;
K_MUTEX_DEFINE(lock);
struct Config {
  uint32_t frequency, last_tag;
  uint8_t owner[32], sf, cr, enabled;
  uint16_t bw;
};
Config cfg{};
uint8_t pub[32], priv[64], secret[32];
int init_error = -EAGAIN, last_error;
bool have_id, gps_ready, gps_on, advertise, request;
int64_t gps_started;
int64_t due, reply_deadline;
uint32_t waiting;
uint8_t owner_status = Unknown;
#if RAK_TAG_COMPANION
constexpr uint8_t advert_type = ADV_TYPE_CHAT;
struct Watch {
  uint8_t peer[32], answer, enabled;
  uint32_t last_tag;
  StatusRequest last;
};
Watch watch{};
#ifdef RAK_NATIVE_PINGS
struct WatchAlert { uint8_t peer[32]; uint32_t utc; char text[80]; };
WatchAlert watch_alert{};
#endif
uint8_t watch_secret[32];
bool listening;
int64_t retry_listen_at;
#else
constexpr uint8_t advert_type = ADV_TYPE_SENSOR;
#endif

struct Record { void* bytes; size_t size; int result; };
int read_record(const char* suffix, size_t len, settings_read_cb read, void* arg, void* context) {
  if (suffix && *suffix) return 0;
  auto& record = *static_cast<Record*>(context);
  if (len != record.size) record.result = -EBADMSG;
  else if (read(arg, record.bytes, len) != (ssize_t)len) record.result = -EIO;
  else record.result = 0;
  return 0;
}
int load(const char* key, void* data, size_t len) {
  Record record{data, len, -ENOENT};
  int rc = settings_load_subtree_direct(key, read_record, &record);
  return rc ? rc : record.result;
}
int save(const Config& next) {
  int rc = settings_save_one("tagmesh/config1", &next, sizeof(next));
  if (!rc) cfg = next;
  return rc;
}
bool radio_valid(const Config& c) {
  return c.frequency >= 150000000 && c.frequency <= 960000000 && (c.bw == 625 || c.bw == 1250 || c.bw == 2500 || c.bw == 5000)
      && c.sf >= 7 && c.sf <= 12 && c.cr >= 5 && c.cr <= 8;
}
int identity() {
  uint8_t seed[32]{};
  int rc = load("tagmesh/seed", seed, sizeof(seed));
  if (rc == -ENOENT) {
    rc = sys_csrand_get(seed, sizeof(seed));
    if (!rc) rc = settings_save_one("tagmesh/seed", seed, sizeof(seed));
  }
  if (!rc) ed25519_create_keypair(pub, priv, seed);
  for (volatile uint8_t& value : seed) value = 0;
  return rc;
}
struct Frame { uint8_t len; uint8_t bytes[255]; };
void stop_rx() {
  tag_radio_idle();
#if RAK_TAG_COMPANION
  listening = false;
#endif
  reply_deadline = 0;
  waiting = 0;
}
int send(mesh::Packet& packet) {
  if (!radio_valid(cfg)) return -EINVAL;
  int rc = tag_radio_apply({cfg.frequency, cfg.bw, cfg.sf, cfg.cr});
  if (rc) return rc;
  uint8_t bytes[255];
  uint8_t len = packet.writeTo(bytes);
  return tag_radio_send(bytes, len);
}
int advert(uint32_t utc) {
  mesh::Packet packet;
  packet.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
  uint8_t text[] = {ADV_NAME_MASK | advert_type, 'R','A','K',' ','T','a','g'};
  uint8_t body[32 + 4 + sizeof(text)];
  memcpy(body, pub, 32);
  put32(body + 32, utc);
  memcpy(body + 36, text, sizeof(text));
  memcpy(packet.payload, body, 36);
  ed25519_sign(packet.payload + 36, body, sizeof(body), pub, priv);
  memcpy(packet.payload + 100, text, sizeof(text));
  packet.payload_len = 100 + sizeof(text);
  return send(packet);
}
int check(const TagFix& fix) {
  if (cfg.last_tag == UINT32_MAX) return -EOVERFLOW;
  Config next = cfg;
  next.last_tag = MAX(fix.utc, cfg.last_tag + 1);
  if (uint64_t(next.last_tag) > uint64_t(fix.utc) + 300) return -ERANGE;
  StatusRequest req{};
  req.flags = FreshGps;
  req.latitude = fix.lat;
  req.longitude = fix.lon;
  int mv = tag_battery_mv();
  if (mv < 0 || mv > UINT16_MAX) return -EIO;
  req.battery_mv = mv;
  uint8_t data[TagLength + RequestLength]{};
  put32(data, next.last_tag);
  if (!makeStatusRequest(req, data + TagLength, RequestLength)) return -EINVAL;
  int rc = save(next);
  if (rc) return rc;
  mesh::Packet packet;
  tag_packets::seal(packet, PAYLOAD_TYPE_REQ, pub, cfg.owner, secret, data, sizeof(data));
  rc = send(packet);
  if (!rc) rc = tag_radio_listen();
  if (!rc) {
    waiting = cfg.last_tag;
    reply_deadline = k_uptime_get() + kReplyTimeoutMs;
  }
  return rc;
}
bool response(const Frame& frame) {
  uint8_t state;
  if (!tag_packets::unpackReply(frame.bytes, frame.len, pub, cfg.owner, secret, waiting, state)) return false;
  if (state != Unknown) owner_status = state;
  return true;
}
#if RAK_TAG_COMPANION
int save_watch(const Watch& next) {
  int rc = settings_save_one("tagmesh/watch1", &next, sizeof(next));
  if (!rc) watch = next;
  return rc;
}
#ifdef RAK_NATIVE_PINGS
bool receive_alert(const Frame& frame) {
  uint8_t data[MAX_PACKET_PAYLOAD];
  int len = tag_packets::open(frame.bytes, frame.len, PAYLOAD_TYPE_TXT_MSG, pub, watch.peer, watch_secret, data);
  if (len <= 5 || (data[4] >> 2) != 0) return false;
  const auto* text = reinterpret_cast<const char*>(data + 5);
  size_t size = strnlen(text, len - 5);
  constexpr char exit_prefix[] = "Collar left the fence at ";
  constexpr char return_prefix[] = "Collar returned to the fence at ";
  if (size >= sizeof(watch_alert.text) ||
      !((size > sizeof(exit_prefix) - 1 && !memcmp(text, exit_prefix, sizeof(exit_prefix) - 1)) ||
        (size > sizeof(return_prefix) - 1 && !memcmp(text, return_prefix, sizeof(return_prefix) - 1)))) return false;
  uint32_t utc = get32(data);
  if (!utc) return false;
  if (memcmp(watch_alert.peer, watch.peer, 32) || utc > watch_alert.utc) {
    WatchAlert next{};
    memcpy(next.peer, watch.peer, 32);
    next.utc = utc;
    memcpy(next.text, text, size);
    last_error = settings_save_one("tagmesh/watchalert1", &next, sizeof(next));
    if (last_error) return true;  // The sender can retry an undelivered alert.
    watch_alert = next;
  }
  mesh::Packet ack;
  ack.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_ACK << PH_TYPE_SHIFT);
  ack.payload_len = 4;
  mesh::Utils::sha256(ack.payload, ack.payload_len, data, 5 + size, watch.peer, 32);
  stop_rx();
  last_error = send(ack);
  return true;
}
#endif
void answer(const Frame& frame) {
#ifdef RAK_NATIVE_PINGS
  if (receive_alert(frame)) return;
#endif
  TagFix own{};
  if (!tag_gps_latest(own)) return;
  uint32_t tag;
  Watch next = watch;
  if (!tag_packets::unpackCheck(frame.bytes, frame.len, pub, watch.peer, watch_secret,
                                watch.last_tag, own.utc, tag, next.last)) return;
  next.last_tag = tag;
  last_error = save_watch(next);
  if (last_error) return;
  uint8_t clear[ResponseLength];
  makeStatusResponse(tag, watch.answer, 0, clear, sizeof(clear));
  mesh::Packet reply;
  tag_packets::seal(reply, PAYLOAD_TYPE_RESPONSE, pub, watch.peer, watch_secret, clear, sizeof(clear));
  stop_rx();
  last_error = send(reply);
}
#endif
void service() {
  Frame frame;
  if (reply_deadline && k_uptime_get() >= reply_deadline) {
    stop_rx();
    last_error = -ETIMEDOUT;
  }
  bool receiving = reply_deadline != 0;
#if RAK_TAG_COMPANION
  if (watch.enabled && !receiving && !listening && k_uptime_get() >= retry_listen_at) {
    int rc = tag_radio_apply({cfg.frequency, cfg.bw, cfg.sf, cfg.cr});
    if (!rc) rc = tag_radio_listen();
    listening = !rc;
    if (rc) { last_error = rc; retry_listen_at = k_uptime_get() + 5000; }
  }
  receiving = receiving || listening;
#endif
  if (receiving) {
    int size = tag_radio_read(frame.bytes, sizeof(frame.bytes));
    if (size > 0) frame.len = size;
    if (size > 0 && waiting && response(frame)) {
      stop_rx();
      last_error = 0;
      due = k_uptime_get() + (owner_status == Lost ? 60000 : 900000);
    }
#ifdef RAK_NATIVE_PINGS
    else if (size > 0 && reply_deadline && !waiting && collar_fence::acknowledge(frame.bytes, frame.len)) {
      stop_rx();
      last_error = 0;
    }
#endif
#if RAK_TAG_COMPANION
    else if (size > 0 && !reply_deadline && watch.enabled) answer(frame);
#endif
  }
  if (reply_deadline) return;
#ifdef RAK_NATIVE_PINGS
  if (collar_fence::alert_due(k_uptime_get())) {
    stop_rx();
    mesh::Packet packet;
    collar_fence::make_alert(packet, pub, secret, k_uptime_get());
    last_error = send(packet);
    if (!last_error) last_error = tag_radio_listen();
    if (!last_error) reply_deadline = k_uptime_get() + kReplyTimeoutMs;
    return;
  }
#endif
  if (!advertise && !request && (!cfg.enabled || k_uptime_get() < due)) return;
  TagFix fix{};
  if (!tag_gps_latest(fix)) return;
  stop_rx();
  if (advertise) {
    advertise = false;
    last_error = advert(fix.utc);
  } else {
    request = false;
    due = k_uptime_get() + (owner_status == Lost ? 60000 : 900000);
    last_error = check(fix);
  }
}
bool number(const char* s, unsigned long& value) {
  if (!s || *s < '0' || *s > '9') return false;
  char* end;
  value = strtoul(s, &end, 10);
  return *end == 0;
}
int command(const shell* sh, size_t argc, char** argv) {
  k_mutex_lock(&lock, K_FOREVER);
  int rc = init_error;
  Config next = cfg;
  const char* op = argc > 1 ? argv[1] : "status";
  if (!strcmp(op, "status") && argc <= 2) {
    char key[65];
    mesh::Utils::toHex(key, pub, sizeof(pub));
    TagFix fix{};
#if RAK_TAG_COMPANION
    shell_print(sh, "role=tracker-companion");
#else
    shell_print(sh, "role=tracker-sensor");
#endif
    shell_print(sh, "id=%s radio=%s frequency_Hz=%lu GPS=%s fix=%d mode=%s status=%u pending=%lu error=%d",
                have_id ? key : "unavailable", tag_radio_available() ? "ready" : "unavailable",
                (unsigned long)cfg.frequency, gps_on ? "on" : gps_ready ? "idle" : "unavailable",
                tag_gps_latest(fix), cfg.enabled ? "pet" : "off", owner_status,
                (unsigned long)waiting, rc ? rc : last_error);
  } else if (!rc) {
#if RAK_TAG_COMPANION
    if (!strcmp(op, "watch") && argc == 3) {
      Watch candidate{};
      uint8_t derived[32]{};
      if (!strcmp(argv[2], "off")) {
        rc = save_watch(candidate);
      } else if (!radio_valid(cfg) || !mesh::Utils::fromHex(candidate.peer, 32, argv[2])
                 || !memcmp(pub, candidate.peer, 32)) rc = -EINVAL;
      else {
        ed25519_key_exchange(derived, candidate.peer, priv);
        if (mesh::Utils::isZeroes(derived, 32)) rc = -EINVAL;
        else {
          if (!memcmp(candidate.peer, watch.peer, 32)) candidate = watch;
          candidate.enabled = 1;
          rc = save_watch(candidate);
        }
      }
      if (!rc) { memcpy(watch_secret, derived, 32); stop_rx(); }
    } else if (!strcmp(op, "lost") && argc == 3) {
      Watch candidate = watch;
      if (!strcmp(argv[2], "yes")) candidate.answer = Lost;
      else if (!strcmp(argv[2], "no")) candidate.answer = Safe;
      else if (!strcmp(argv[2], "off")) candidate.answer = Unknown;
      else rc = -EINVAL;
      if (!rc && !watch.enabled) rc = -ENOENT;
      if (!rc) rc = save_watch(candidate);
    } else if (!strcmp(op, "last") && argc == 2) {
      shell_print(sh, "watch=%u tag=%lu gps_flags=%u latitude_e7=%ld longitude_e7=%ld battery_mV=%u",
                  watch.enabled, (unsigned long)watch.last_tag, watch.last.flags,
                  (long)watch.last.latitude, (long)watch.last.longitude, watch.last.battery_mv);
#ifdef RAK_NATIVE_PINGS
      if (watch.enabled && watch_alert.utc && !memcmp(watch_alert.peer, watch.peer, 32))
        shell_print(sh, "alert_utc=%lu %s", (unsigned long)watch_alert.utc, watch_alert.text);
#endif
    } else
#endif
#ifdef RAK_NATIVE_PINGS
    if (!strcmp(op, "fence") || !strcmp(op, "motion")) {
      rc = collar_fence::command(sh, argc, argv, cfg.owner, radio_valid(cfg) && !mesh::Utils::isZeroes(secret, 32));
    } else
#endif
    if (!strcmp(op, "radio") && argc == 6) {
      unsigned long hz, sf, cr;
      uint16_t bw = !strcmp(argv[3], "62.5") ? 625 : !strcmp(argv[3], "125") ? 1250
          : !strcmp(argv[3], "250") ? 2500 : !strcmp(argv[3], "500") ? 5000 : 0;
      if (reply_deadline || cfg.enabled
#if RAK_TAG_COMPANION
          || watch.enabled
#endif
      ) rc = -EBUSY;
      else if (!number(argv[2], hz) || !bw || !number(argv[4], sf)
          || !number(argv[5], cr) || hz < 150000000 || hz > 960000000
          || sf < 7 || sf > 12 || cr < 5 || cr > 8) rc = -EINVAL;
      else {
        next.frequency = hz;
        next.bw = bw;
        next.sf = sf;
        next.cr = cr;
        rc = save(next);
      }
    } else if (!strcmp(op, "owner") && argc == 3) {
      uint8_t new_secret[32]{};
      if (cfg.enabled || reply_deadline) rc = -EBUSY;
      else if (!mesh::Utils::fromHex(next.owner, sizeof(next.owner), argv[2])
               || !memcmp(pub, next.owner, sizeof(pub))) rc = -EINVAL;
      else {
        ed25519_key_exchange(new_secret, next.owner, priv);
        if (mesh::Utils::isZeroes(new_secret, sizeof(new_secret))) rc = -EINVAL;
        else if (!(rc = save(next))) {
          memcpy(secret, new_secret, sizeof(secret));
          owner_status = Unknown;
        }
      }
    } else if (!strcmp(op, "off") && argc == 2) {
      next.enabled = false;
      rc = save(next);
      if (!rc) { stop_rx(); request = advertise = false; }
    } else if ((!strcmp(op, "on") || !strcmp(op, "check") || !strcmp(op, "advert")) && argc == 2) {
      if (!gps_ready || !tag_radio_available() || !radio_valid(cfg)) rc = -ENODEV;
      else if (!strcmp(op, "advert")) advertise = true;
      else if (mesh::Utils::isZeroes(secret, sizeof(secret))) rc = -ENOENT;
      else if (!strcmp(op, "check")) request = true;
      else {
        next.enabled = true;
        rc = save(next);
        if (!rc) due = 0;
      }
    } else rc = -EINVAL;
    if (!rc) shell_print(sh, "OK");
  }
  if (rc) shell_error(sh, "Mesh unavailable (%d)", rc);
  k_mutex_unlock(&lock);
  return rc;
}
#ifdef RAK_NATIVE_PINGS
#define FENCE_HELP " | motion [on | off] | fence [off | lat lon radius_m | lat lon lat lon ...]"
#else
#define FENCE_HELP ""
#endif
#if RAK_TAG_COMPANION
SHELL_CMD_REGISTER(pet, NULL, "status | radio Hz BW_kHz SF CR | owner key | on | off | advert | check | watch key/off | lost yes/no/off | last" FENCE_HELP, command);
#else
SHELL_CMD_REGISTER(pet, NULL, "status | radio Hz BW_kHz SF CR | owner key | on | off | advert | check" FENCE_HELP, command);
#endif

void worker(void*, void*, void*) {
  k_mutex_lock(&lock, K_FOREVER);
  int rc = settings_subsys_init();
  if (!rc) rc = identity();
  have_id = !rc;
  if (!rc) {
    rc = load("tagmesh/config1", &cfg, sizeof(cfg));
    if (rc == -ENOENT) rc = 0;
    if (!rc && cfg.frequency && !radio_valid(cfg)) rc = -EBADMSG;
    if (!rc && !mesh::Utils::isZeroes(cfg.owner, sizeof(cfg.owner)))
      ed25519_key_exchange(secret, cfg.owner, priv);
    if (!rc && cfg.enabled && (!radio_valid(cfg) || mesh::Utils::isZeroes(secret, sizeof(secret)))) rc = -EBADMSG;
  }
#if RAK_TAG_COMPANION
  if (!rc) {
    rc = load("tagmesh/watch1", &watch, sizeof(watch));
    if (rc == -ENOENT) rc = 0;
    if (!rc && watch.enabled) {
      ed25519_key_exchange(watch_secret, watch.peer, priv);
      if (!radio_valid(cfg) || watch.answer > Lost || mesh::Utils::isZeroes(watch_secret, 32)) rc = -EBADMSG;
    }
  }
#endif
#ifdef RAK_NATIVE_PINGS
  if (!rc) {
    collar_fence::after_load(load(collar_fence::settings_key, &collar_fence::record, sizeof(collar_fence::record)));
#if RAK_TAG_COMPANION
    if (load("tagmesh/watchalert1", &watch_alert, sizeof(watch_alert)) ||
        !memchr(watch_alert.text, 0, sizeof(watch_alert.text))) watch_alert = {};
#endif
  }
#endif
  gps_ready = tag_gps_available();
  init_error = rc;
  k_mutex_unlock(&lock);
  while (true) {
    k_mutex_lock(&lock, K_FOREVER);
    bool check_needs_gps = !init_error && (advertise || request
        || (cfg.enabled && k_uptime_get() >= due));
    bool need_gps = check_needs_gps;
#ifdef RAK_NATIVE_PINGS
    if (!init_error && collar_fence::needs_gps(cfg.enabled, cfg.owner, k_uptime_get())) need_gps = true;
#endif
#if RAK_TAG_COMPANION
    need_gps = need_gps || (!init_error && watch.enabled);
#endif
    if (need_gps && !gps_on) {
      gps_on = tag_gps_begin();
      gps_started = k_uptime_get();
    } else if (!need_gps && gps_on) {
      tag_gps_end();
      gps_on = false;
    }
    if (gps_on) tag_gps_process();
    TagFix fix{};
#ifdef RAK_NATIVE_PINGS
    if (gps_on && tag_gps_latest(fix)) collar_fence::observe(fix, k_uptime_get());
#endif
    if (!init_error) service();
    if (check_needs_gps && gps_on && k_uptime_get() - gps_started > 120000 && !tag_gps_latest(fix)
#if RAK_TAG_COMPANION
        && !watch.enabled
#endif
    ) {
      last_error = -ETIMEDOUT;
      advertise = request = false;
      due = k_uptime_get() + (owner_status == Lost ? 60000 : 900000);
    }
    k_mutex_unlock(&lock);
    k_sleep(K_MSEC(25));
  }
}
K_THREAD_STACK_DEFINE(worker_stack, 12288);
struct k_thread worker_thread;
}

extern "C" void tag_mesh_start() {
  k_thread_create(&worker_thread, worker_stack, K_THREAD_STACK_SIZEOF(worker_stack),
                  worker, nullptr, nullptr, nullptr, 7, 0, K_NO_WAIT);
}
