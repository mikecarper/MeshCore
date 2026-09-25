#include <helpers/BorrowableFrameBuffer.h>
#include <helpers/ota/OtaContext.h>
#include <helpers/CompanionMotaControl.h>
#include <helpers/ota/MotaStreamWritePolicy.h>
#include <cassert>
#include <cstring>
#include <deque>
#include <vector>
#include "../../test_ota/mota_vectors.h"

using namespace mesh::ota;

// Only the flash identity probe is replaced: the context, queue, session
// engine, source parsing, Merkle proofs and transfer bytes are production code.
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) { info = SelfFwInfo(); return false; }
} }

struct Frame { uint8_t len; uint8_t buf[176]; };
struct Queue {
  mesh::BorrowableFrameBuffer<Frame, 256, 128, OtaContext> buffer;
  int count = 129, head = 191;
  unsigned released = 0;
  static OtaContext* acquire(void* owner) {
    Queue& q = *static_cast<Queue*>(owner);
    return q.buffer.acquire(q.count, q.head);
  }
  static uint16_t drain(void* owner) {
    Queue& q = *static_cast<Queue*>(owner);
    uint16_t removed = 0;
    while (q.count > 128) {
      q.head = (q.head + 1) % q.buffer.capacity();
      --q.count;
      ++removed;
    }
    return removed;
  }
  static void release(void* owner) {
    Queue& q = *static_cast<Queue*>(owner);
    q.buffer.release(q.head);
    ++q.released;
  }
};

class Folder : public MotaSource {
public:
  uint8_t count() override { return 1; }
  bool describe(uint8_t idx, MotaDesc& d) override {
    if (idx) return false;
    MotaManifest m;
    if (!mota_parse(MOTA_VEC, MOTA_VEC_LEN, m)) return false;
    memcpy(d.mid, m.merkle_root, 4);
    d.target_id = m.target_id;
    d.fw_version = m.fw_version;
    d.codec_id = m.codec_id;
    d.flags = m.flags;
    d.block_size_log2 = m.block_size_log2;
    d.total_size = MOTA_VEC_LEN;
    d.leaves_off = m.leaves - MOTA_VEC;
    d.block_count = m.block_count;
    d.payload_off = m.payload - MOTA_VEC;
    d.payload_size = m.payload_size;
    return true;
  }
  bool read(uint8_t idx, uint32_t offset, uint8_t* out, uint32_t length) override {
    if (idx || uint64_t(offset) + length > MOTA_VEC_LEN) return false;
    memcpy(out, MOTA_VEC + offset, length);
    return true;
  }
};

// The actual BLE source controller below sees a ready/not-ready link and a
// source with real container bytes. Hardware GATT/serial framing is separate.
#if !defined(ESP32_PLATFORM)
struct Bluetooth {
  bool ready = false, active = false;
  int motaStream() { return 0; }
  bool isMotaChannelReady() const { return ready; }
  bool isMotaStreamActive() const { return active; }
  void setMotaStreamActive(bool value) { active = value; }
} bluetooth_interface;
namespace mesh {
struct Log { void println(const char*) {} };
Log& usbLoggingPort() { static Log log; return log; }
namespace ota {
class SerialMotaSource : public ::Folder {
public:
  SerialMotaSource(int, MotaStreamWritePolicy, uint32_t) {}
};
} }
#include "ble_control_under_test.h"
#endif

struct Packet { bool to_source; std::vector<uint8_t> bytes; };
static std::deque<Packet> packets;
static bool send(void* to_source, const uint8_t* bytes, uint16_t length, bool) {
  packets.push_back({to_source != nullptr, {bytes, bytes + length}});
  return true;
}

static void transfer(OtaContext& context, void* reply_route) {
  OtaManager receiver;
  OtaStoreRam<8192> destination;
  receiver.begin(EXP_TARGET_ID, send, reply_route);
  receiver.set_fetch_store(&destination);
  assert(receiver.pull(EXP_MERKLE_ROOT, EXP_TARGET_ID) == OtaManager::PULL_STARTED);
  for (uint32_t time = 100; time < 300000 && receiver.fetchState() != OtaManager::COMPLETE;
       time += 100) {
    context.manager.set_clock(time);
    receiver.set_clock(time);
    context.manager.serviceEgress();
    receiver.serviceEgress();
    if (time % 1000 == 0) { context.manager.loop(); receiver.loop(); }
    while (!packets.empty()) {
      Packet packet = packets.front();
      packets.pop_front();
      (packet.to_source ? context.manager : receiver).on_message(
          packet.bytes.data(), packet.bytes.size());
    }
  }
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  assert(destination.staged_size() == MOTA_VEC_LEN);
  assert(memcmp(destination.data(), MOTA_VEC, MOTA_VEC_LEN) == 0);
}

#if defined(ESP32_PLATFORM)
#include "test_wifi_shared_queue.h"
#endif

int main() {
  Queue q;
  unsigned first_retained = 0;
  for (int i = 0; i < q.count; ++i) {
    Frame& frame = q.buffer.at((q.head + i) % q.buffer.capacity());
    frame.len = 176;
    memset(frame.buf, i, sizeof frame.buf);
  }
  auto check_messages = [&]() {
    for (int i = 0; i < q.count; ++i) {
      const Frame& frame = q.buffer.at((q.head + i) % q.buffer.capacity());
      assert(frame.len == 176);
      for (auto byte : frame.buf) assert(byte == i + first_retained);
    }
  };
  char reply[160] = {};
  assert(!ota_acquire_context(reply, sizeof reply));
  assert(strstr(reply, "not ready"));
  ota_set_context_storage(&q, Queue::acquire, Queue::release, Queue::drain);
  uint8_t identity[4] = {1, 2, 3, 4};
  ota_begin_context(0, send, nullptr, "Heltec_t096", identity);
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  assert(ota_hop_limit() == OTA_HOP_LIMIT_DEFAULT);
  assert(!ota_acquire_context(reply, sizeof reply));
  assert(strstr(reply, "sync unread messages"));
  assert(q.head == 191 && q.count == 129 && q.buffer.capacity() == 256);
  check_messages();
  uint16_t drained = 0;
  assert(ota_acquire_context(reply, sizeof reply, true, &drained));
  assert(drained == 1 && q.count == 128 && q.head == 0);
  first_retained = 1;
  check_messages();

  for (unsigned cycle = 0; cycle < 8; ++cycle) {
    assert(ota_acquire_context(reply, sizeof reply));
    OtaContext& context = ota_ctx();
    assert(q.head == 0 && q.buffer.capacity() == 128);
    assert(ota_acquire_context(reply, sizeof reply) && &ota_ctx() == &context);
    assert(strcmp(context.hw_id, "Heltec_t096") == 0);
    check_messages();
    if (cycle) {
      assert(context.manager.max_hops() == 5);
      assert(context.manager.checkpoint_blocks() == 6);
      assert(context.manager.advert_mins() == 42);
      assert(context.allow.count() == 1);
    }
    context.manager.set_max_hops(5);
    context.manager.set_checkpoint_blocks(6);
    context.manager.set_advert_mins(42);
    uint8_t key[32] = {7};
    context.allow.add(key);

    Folder folder;
    const auto link = cycle % 2 ? OtaContext::FOLDER_LINK_BLE
                               : OtaContext::FOLDER_LINK_SERIAL;
    assert(context.attach_folder_source(&folder, link, "test", reply, sizeof reply));
    assert(context.manager.servedCount() == 1);
    ota_release_context_if_idle(false); // attached sources hold the workspace
    assert(ota_context_if_active());
    assert(!context.attach_folder_source(&folder,
        cycle % 2 ? OtaContext::FOLDER_LINK_SERIAL : OtaContext::FOLDER_LINK_BLE,
        "wrong owner", reply, sizeof reply));
    assert(context.folderLink() == link);

    transfer(context, &q);
    check_messages();
    context.detach_folder(); // same cleanup for USB/BLE stop and disconnect
    context.manager.announce(); // callers may still use the context until loop boundary
    packets.clear();
    ota_release_context_if_idle(true); // return slots even if TempRadio is still up
    assert(!ota_context_if_active() && q.buffer.capacity() == 256);
    assert(ota_hop_limit() == 5 && q.released == cycle + 1);
    check_messages();
    // Reuse every returned slot, so stale transfer state cannot survive.
    for (unsigned i = 128; i < 256; ++i)
      memset(&q.buffer.at(i), 0xCC, sizeof(Frame));
    ota_release_context_if_idle(false);
    assert(q.released == cycle + 1);
  }

  assert(ota_acquire_context(reply, sizeof reply));
  ota_release_context_if_idle(true); // discovery holds storage during TempRadio
  assert(ota_context_if_active());
  ota_release_context_if_idle(false); // expiry / normalradio releases it
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);

  assert(ota_acquire_context(reply, sizeof reply));
  assert(!ota_ctx().attach_folder_source(nullptr, OtaContext::FOLDER_LINK_SERIAL,
                                        "failed start", reply, sizeof reply));
  ota_release_context_if_idle(false);
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  check_messages();

#if defined(ESP32_PLATFORM)
  test_wifi_shared_queue(q, check_messages);
#else
  Nrf52BleMotaSourceControl ble;
  assert(!ble.start(reply, sizeof reply)); // no subscription, no storage loan
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  for (int disconnect = 0; disconnect < 2; ++disconnect) {
    bluetooth_interface.ready = true;
    assert(ble.start(reply, sizeof reply));
    assert(ble.status().attached && q.buffer.capacity() == 128);
    assert(ble.status().offered == 1 && ble.status().advertised == 1);
    if (disconnect) {
      bluetooth_interface.ready = false;
      ble.loop();
    } else {
      assert(ble.stop(reply, sizeof reply));
    }
    assert(!ble.status().attached && !bluetooth_interface.active);
    ota_release_context_if_idle(true);
    assert(!ota_context_if_active() && q.buffer.capacity() == 256);
    assert(!ble.status().attached);
    assert(ble.stop(reply, sizeof reply)); // idle stop/status/poll never acquire
    ble.loop();
    assert(!ota_context_if_active());
    check_messages();
    packets.clear();
  }

  // BLE start/stop cannot detach a USB-owned source or return its workspace.
  assert(ota_acquire_context(reply, sizeof reply));
  Folder usb;
  assert(ota_ctx().attach_folder_source(&usb, OtaContext::FOLDER_LINK_SERIAL,
                                        "USB", reply, sizeof reply));
  bluetooth_interface.ready = true;
  assert(!ble.start(reply, sizeof reply));
  assert(ble.status().another_link_active);
  assert(ble.stop(reply, sizeof reply));
  ota_release_context_if_idle(false);
  assert(ota_ctx().folder_active && q.buffer.capacity() == 128);
  ota_ctx().detach_folder();
  ota_release_context_if_idle(true);
  assert(!ota_context_if_active() && q.buffer.capacity() == 256);
  check_messages();
#endif

  Queue full;
  full.count = 256;
  for (int i = 0; i < full.count; ++i) {
    Frame& frame = full.buffer.at((full.head + i) % full.buffer.capacity());
    frame.len = 176;
    memset(frame.buf, i, sizeof frame.buf);
  }
  ota_set_context_storage(&full, Queue::acquire, Queue::release, Queue::drain);
  ota_begin_context(0, send, nullptr, "Heltec_t096", identity);
  assert(!ota_acquire_context(reply, sizeof reply));
  drained = 0;
  assert(ota_acquire_context(reply, sizeof reply, true, &drained));
  assert(drained == 128 && full.count == 128 && full.head == 0);
  for (int i = 0; i < full.count; ++i) {
    const Frame& frame = full.buffer.at(i);
    assert(frame.len == 176);
    for (auto byte : frame.buf) assert(byte == i + 128);
  }
  ota_release_context_if_idle(false);
  assert(!ota_context_if_active() && full.buffer.capacity() == 256);
}
