#pragma once
#include "sensor_tracker_client.h"
#define COMPANION_RAK_TRACKER 1
#define ADV_TYPE_CHAT 1

class MyMesh : public SensorMesh {
public:
  struct Store {
    MemoryFS* fs;
    bool isVolatilePrimaryFS() const { return false; }
    MemoryFS* getPrimaryFS() const { return fs; }
  } store{&fs};
  Store* _store = &store;
  struct Contact {
    mesh::Identity id;
    uint8_t type = ADV_TYPE_CHAT, secret[32] = {7};
    const uint8_t* getSharedSecret(const mesh::Identity&) { return secret; }
  } contact;
  bool added = false, _radio_available = true;
  uint32_t millis() const { return now_ms; }
  Contact* lookupPersistentContactByPubKey(const uint8_t* key, size_t len) {
    return added && len == 32 && !memcmp(contact.id.pub_key, key, len) ? &contact : nullptr;
  }
  void cancelActiveRetries(const uint8_t* hash) {
    assert(!memcmp(hash, dog_packet_hash, sizeof(dog_packet_hash))); ++cancel_active;
  }
  bool sendFloodScoped(Contact&, mesh::Packet* p) { ++flood_sends; return admit(p); }
  @DOG_FIELDS@
};

void SensorMesh::onSendFail(mesh::Packet*) { }
int main() {
  using namespace mesh::tracker;
  MyMesh tag;
  tag.beginDogTracker();
  const char* set_owner = "set tracker.owner 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
  char reply[200];
  assert(tag.dogCommand(set_owner, reply, sizeof(reply)) && !strcmp(reply, "OK"));
  assert(tag.dogCommand("set tracker.mode pet", reply, sizeof(reply)) && strstr(reply, "contact"));
  memcpy(tag.contact.id.pub_key, tag.dog.owner, 32);
  tag.added = true;
  assert(tag.dogCommand("set tracker.mode pet", reply, sizeof(reply)) && !strcmp(reply, "OK"));
  tag.loopDogTracker();
  assert(sensors.pending && !tag.created);
  sensors.fix();
  tag.loopDogTracker();
  assert(tag.created == 1 && tag.direct_sends == 1 && !tag.flood_sends);
  assert(!memcmp(tag.last_dest, tag.dog.owner, 32));
  StatusRequest req{};
  assert(parseStatusRequest(tag.last_payload.data()+4, tag.last_payload.size()-4, req));
  assert(req.flags == FreshGps && req.latitude == 471234567 && req.longitude == -1222345678);
  auto* tx = tag.pool.queued.front();
  tag.pool.queued.clear();
  tag.dogSendResult(tx, true);
  tag.releasePacket(tx);
  mesh::Packet response;
  response.header = ROUTE_TYPE_DIRECT | (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT);
  uint8_t body[ResponseLength], wrong[32] = {8};
  auto tag_id = tag.dog.last_request_tag;
  makeStatusResponse(tag_id+1, Lost, 0, body, sizeof(body));
  assert(!tag.dogResponse(&response, tag.contact.secret, body, sizeof(body)));
  makeStatusResponse(tag_id, Lost, 0, body, sizeof(body));
  assert(!tag.dogResponse(&response, wrong, body, sizeof(body)));
  assert(tag.dogResponse(&response, tag.contact.secret, body, sizeof(body)));
  assert(!tag.dogResponse(&response, tag.contact.secret, body, sizeof(body)));
  assert(tag.dog.last_lost == Lost && tag.dog_due == tag.rtc.now + tag.dog.lost_interval);
  MyMesh reboot;
  reboot.fs.files = tag.fs.files;
  reboot.beginDogTracker();
  assert(reboot.dog.enabled && reboot.dog.last_request_tag == tag_id && reboot.dog.last_lost == Lost);
  reboot.added = true;
  memcpy(reboot.contact.id.pub_key, reboot.dog.owner, 32);
  reboot.loopDogTracker();
  sensors.fix();
  reboot.fs.short_write = true;
  reboot.loopDogTracker();
  assert(reboot.dog_storage_fault && !reboot.created);
}
