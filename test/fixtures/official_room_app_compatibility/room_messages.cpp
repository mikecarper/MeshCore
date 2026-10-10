// Real room push/service methods and real Companion presentation prefix are
// supplied in generated includes. Packet encryption, transport and UI are
// boundaries; no app or firmware text-message parser is reproduced here.
#include "room_delivery_fixture.inc"
#include "helpers/CompanionFrameLimits.h"
#include "frame_codes.inc"

struct RoomContact { mesh::Identity id; };
struct CompanionPacket {
  bool flood;
  uint8_t path_len = 2;
  float getSNR() const { return 10.75f; }
  bool isRouteFlood() const { return flood; }
};
struct WirePresentation {
  int app_target_ver;
  uint8_t out_frame[MAX_FRAME_SIZE] = {};
  std::vector<uint8_t> frame;
  bool queueMessage(const RoomContact&, uint8_t, CompanionPacket*, uint32_t,
                    const uint8_t*, int, const char*, bool, uint32_t);
};
#include "presentation.inc"

static void hex(const uint8_t* bytes, size_t size) {
  for (size_t at = 0; at < size; ++at) printf("%02x", bytes[at]);
}
static void emit(MyMesh& room, const char* group, unsigned index, int version, bool flood) {
  const auto& sent = room.sent.back();
  if (strcmp(group, "ordered") != 0 || index == 2)
    assert(memcmp(sent.author, room.self_id.pub_key, 4) == 0);
  RoomContact from; from.id = room.self_id;
  CompanionPacket packet{flood};
  WirePresentation app{version};
  app.queueMessage(from, TXT_TYPE_SIGNED_PLAIN, &packet, sent.timestamp,
                   sent.author, 4, sent.text.c_str(), false, 0);
  assert(app.frame.size() == sent.text.size() + (version >= 3 ? 20 : 17));
  printf("POST:%s:%u:%d:%d:%u:", group, index, version, int(flood), sent.timestamp);
  hex(sent.author, 4); printf(":");
  hex(reinterpret_cast<const uint8_t*>(sent.text.data()), sent.text.size()); printf(":");
  hex(app.frame.data(), app.frame.size()); printf("\n");
}

int main() {
  {
    MyMesh room; room.topic("Welcome");
    auto& client = room.acl.clients[0]; client.extra.room.sync_since = 50;
    room.post(0, 100, "old-one"); room.post(1, 200, "old-two");
    room.post(2, 1100, "newer"); room.clock.now = 1200;
    room.tick(6000); assert(room.sent.back().text == "old-one");
    emit(room, "ordered", 0, 3, false); room.ack();
    room.tick(); assert(room.sent.back().text == "old-two");
    emit(room, "ordered", 1, 3, false); room.ack();
    room.tick(); assert(room.sent.back().text == "Welcome");
    emit(room, "ordered", 2, 3, false);
    assert(client.extra.room.sync_since == 200); room.ack();
    assert(client.extra.room.sync_since == 200);
    // Actual app/Companion stores the signed timestamp. Echoing it as the
    // subsequent cursor cannot omit an older unread post or this newer post.
    client.extra.room.sync_since = room.room_topic_timestamp;
    room.tick(); assert(room.sent.back().text == "newer");
    emit(room, "ordered", 3, 3, false); room.ack();
    assert(client.extra.room.sync_since == 1100);
  }
  std::string utf8;
  for (unsigned at = 0; at < 49; ++at) utf8 += "\xe2\x98\x83";
  utf8 += "test"; assert(utf8.size() == 151);
  for (int version : {2, 3}) for (bool flood : {false, true})
      for (bool multibyte : {false, true}) {
    MyMesh room;
    const std::string text = multibyte ? utf8 : std::string(151, 'x');
    room.acl.clients[0].out_path_len = flood ? OUT_PATH_UNKNOWN : 0;
    room.topic(text.c_str()); room.tick(6000);
    assert(room.sent.back().text == text);
    emit(room, multibyte ? "utf8" : "maximum", 0, version, flood); room.ack();
  }
  {
    MyMesh room;
    assert(room.pushRoomTextToClient(&room.acl.clients[0], 0xffffffffU,
                                    room.self_id, "timestamp-boundary"));
    emit(room, "timestamp", 0, 3, false);
  }
  return 0;
}
