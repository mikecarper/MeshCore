#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaByteIO.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace mesh::ota;

static bool send(void*, const uint8_t*, uint16_t, bool) { return true; }

static void advert(OtaManager& manager, uint32_t source, uint8_t count = 1) {
  AdvMsg message{};
  wr_u32le(message.seeder_id, source);
  wr_u32le(message.set_digest, 123);
  message.n_motas = count;
  uint8_t wire[24];
  manager.on_message(wire, encode_adv(wire, sizeof(wire), message));
}

static void have(OtaManager& manager, uint32_t source, uint32_t mid) {
  uint8_t row[OTA_HAVE_ROW_BYTES] = {};
  wr_u32le(row, mid);
  wr_u32le(row + 4, 1234);
  row[12] = CODEC_FULL; row[13] = MFLAG_FULL;
  row[14] = 1;
  HaveMsg message{};
  wr_u32le(message.seeder_id, source);
  wr_u32le(message.set_digest, 123);
  message.frag_total = 1; message.n_rows = 1; message.rows = row;
  uint8_t wire[64];
  manager.on_message(wire, encode_have(wire, sizeof(wire), message));
}

static const OtaManager::CatRow* row(OtaManager& manager, uint32_t mid) {
  for (unsigned i = 0; i < manager.catalogCount(); ++i) {
    const auto* entry = manager.catalogRow(i);
    if (rd_u32le(entry->mid) == mid) return entry;
  }
  return nullptr;
}

using Messages = std::vector<std::vector<uint8_t>>;
static bool capture(void* context, const uint8_t* message, uint16_t length, bool) {
  static_cast<Messages*>(context)->emplace_back(message, message + length);
  return true;
}

class MixedCatalogSource : public MotaSource {
  uint8_t _count;
public:
  explicit MixedCatalogSource(uint8_t count) : _count(count) {}
  uint8_t count() override { return _count; }
  bool describe(uint8_t index, MotaDesc& out) override {
    if (index >= _count) return false;
    wr_u32le(out.mid, 100u + index);
    // Three pages: fully matching, partially matching, and empty for target 1.
    out.target_id = _count == 2 ? index + 1u
        : index < 10 ? 1u : index < 20 ? 1u + index % 2u : 2u;
    out.fw_version = 1; out.codec_id = CODEC_FULL; out.flags = MFLAG_FULL;
    out.block_size_log2 = 7; out.block_count = 1;
    out.leaves_off = 8 + MOTA_MFL; out.payload_off = out.leaves_off + 4;
    out.payload_size = 128; out.total_size = out.payload_off + out.payload_size + 5;
    return true;
  }
  bool read(uint8_t, uint32_t, uint8_t*, uint32_t) override { return false; }
};

static void filtered_catalog_remains_queryable(int scenario, bool automatic) {
  Messages from_server, from_client;
  OtaManager server, client;
  server.begin(0, capture, &from_server);
  client.begin(0, capture, &from_client);
  client.set_archive_interest(automatic);
  uint8_t sid[4] = {1, 2, 3, 4}; server.set_seeder_id(sid);
  MixedCatalogSource source(scenario == 4 ? 2 : 24);
  assert(server.add_source(&source));
  server.announce();
  assert(from_server.size() == 1);
  AdvMsg adv{};
  assert(decode_adv(from_server[0].data(), from_server[0].size(), adv));
  if (scenario != 8) client.on_message(from_server[0].data(), from_server[0].size());
  from_server.clear();
  QueryMsg query{};
  memcpy(query.seeder_id, sid, 4); memcpy(query.set_digest, adv.set_digest, 4);
  query.filter_target = scenario == 6 ? 99 : 1;
  uint8_t wire[256];
  server.on_message(wire, encode_query(wire, sizeof(wire), query));
  Messages filtered = from_server;
  if (scenario == 7) {
    // Legacy filtered servers packed only the matching rows into fewer pages.
    filtered.pop_back();
    for (auto& message : filtered) {
      HaveMsg legacy{};
      assert(decode_have(message.data(), message.size(), legacy));
      legacy.frag_total = 2;
      const uint16_t length = encode_have(wire, sizeof(wire), legacy);
      message.assign(wire, wire + length);
    }
  }
  for (const auto& message : filtered) client.on_message(message.data(), message.size());
  assert(client.catalogCount() == (scenario == 4 ? 1 : scenario == 6 ? 0 : 15));
  if (scenario == 8) {
    // Passive rows arriving before a beacon must not confer completeness on its new digest.
    client.on_message(wire, encode_adv(wire, sizeof(wire), adv));
    assert(client.catalogCount() == 0);
  }
  if (automatic) {
    client.set_clock(5000);
    client.loop();
  } else {
    client.queryAll();
  }
  assert(from_client.size() == 1);
  QueryMsg recovery{};
  assert(decode_query(from_client[0].data(), from_client[0].size(), recovery));
  assert(recovery.filter_target == 0);
  assert(recovery.want_fragments == (scenario == 4 ? 1u : scenario == 5 ? 6u : scenario == 8 ? 0u : 7u));
  from_server.clear();
  server.on_message(from_client[0].data(), from_client[0].size());
  for (const auto& message : from_server) client.on_message(message.data(), message.size());
  assert(client.catalogCount() == source.count());
  from_client.clear();
  // Late filtered packets must not downgrade already-complete pages either.
  for (const auto& message : filtered) client.on_message(message.data(), message.size());
  client.queryAll();
  client.set_clock(5000 + OTA_CATALOG_RETRY_MS + 1);
  client.loop();
  assert(from_client.empty());
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const int scenario = atoi(argv[1]);
  const bool rollover = atoi(argv[2]) != 0;
  if (scenario >= 4) {
    filtered_catalog_remains_queryable(scenario, rollover);
    return 0;
  }
  const uint32_t old = rollover ? 0xFFFF0000u : 1000u;
  const uint32_t recent = rollover ? 0u : 100000u;
  OtaManager manager;
  manager.begin(1234, send, nullptr);
  if (scenario == 0) {
    for (unsigned i = 1; i <= OTA_MAX_SOURCES; ++i) {
      manager.set_clock(old + i * 100);
      advert(manager, i); have(manager, i, i);
    }
    manager.set_clock(recent);
    advert(manager, 1); // freshest source is no longer the eviction victim
    manager.set_clock(recent + 10);
    advert(manager, OTA_MAX_SOURCES + 1);
    assert(row(manager, 1));
    assert(!row(manager, 2));
    assert(manager.sourceCount() == OTA_MAX_SOURCES);
  } else if (scenario == 1) {
    for (unsigned i = 1; i <= OTA_MAX_CATALOG; ++i) {
      manager.set_clock(old + i * 100);
      have(manager, 1, i);
    }
    manager.set_clock(recent);
    have(manager, 1, 1);
    manager.set_clock(recent + 10);
    have(manager, 1, OTA_MAX_CATALOG + 1);
    assert(row(manager, 1));
    assert(!row(manager, 2));
    assert(manager.catalogCount() == OTA_MAX_CATALOG);
  } else {
    manager.set_clock(old);
    have(manager, 1, 1);
    manager.set_clock(recent);
    have(manager, 2, 1);
    if (scenario == 2) {
      assert(row(manager, 1)->last_ms == recent);
    } else {
      manager.set_clock(recent + 10);
      have(manager, 3, 1);
      manager.set_clock(recent + 20);
      advert(manager, 3, 0); // withdrawal recomputes newest surviving source
      assert(row(manager, 1)->last_ms == recent);
      assert(row(manager, 1)->n_seeders == 2);
    }
  }
}
