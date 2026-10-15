// Included after the shared OTA vectors/codecs in test_ota_core.cpp.
namespace {
struct DirectManagerBus {
  struct Frame { OtaManager* destination; std::vector<uint8_t> bytes; OtaReplyRoute route; };
  struct End { DirectManagerBus* bus; OtaManager* sender; OtaManager* destination; };
  OtaManager server, client;
  OtaStoreRam<16384> store;
  End from_server{this, &server, &client}, from_client{this, &client, &server};
  std::vector<Frame> queue;
  std::vector<std::vector<uint8_t>> direct_requests;
  std::vector<Frame> requests;
  std::vector<uint32_t> request_times;
  std::vector<Frame> data;
  bool downgrade_probe = false;
  bool drop_one = false;
  bool dropped = false;
  bool lose_direct_after_first_block = false;
  unsigned drop_direct_requests_after_first_block = 0;
  unsigned reject_direct_requests_after_first_block = 0;
  unsigned dropped_direct_requests = 0;
  unsigned rejected_direct_requests = 0;
  unsigned legacy_requests = 0;
  unsigned direct_proofs = 0;
  uint32_t now = 0;

  static bool send(void* context, const uint8_t* bytes, uint16_t length, bool) {
    End& end = *static_cast<End*>(context);
    DirectManagerBus& bus = *end.bus;
    OtaReplyRoute route = end.sender->replyRoute();
    const bool direct = route.direct;
    EXPECT_LE(length, direct ? OTA_DIRECT_MESSAGE_MAX : MAX_PACKET_PAYLOAD);
    route.profile = 1;
    route.generation = 19;
    if (bytes[0] == OTA_REQ) {
      if (direct && bus.client.blocksHave() != 0 &&
          bus.reject_direct_requests_after_first_block != 0) {
        --bus.reject_direct_requests_after_first_block;
        ++bus.rejected_direct_requests;
        return false;
      }
      if (direct) bus.direct_requests.emplace_back(bytes, bytes + length);
      else ++bus.legacy_requests;
      bus.requests.push_back({end.destination, {bytes, bytes + length}, route});
      bus.request_times.push_back(bus.now);
      if (direct && bus.client.blocksHave() != 0 &&
          (bus.lose_direct_after_first_block || bus.drop_direct_requests_after_first_block)) {
        if (bus.drop_direct_requests_after_first_block) --bus.drop_direct_requests_after_first_block;
        ++bus.dropped_direct_requests;
        return true;
      }
      if (direct && bus.downgrade_probe) route.direct = false;
    }
    if (direct && bus.lose_direct_after_first_block && bus.client.blocksHave() != 0) {
      return true;
    }
    Frame frame{end.destination, {bytes, bytes + length}, route};
    if (bytes[0] == OTA_DATA) {
      bus.data.push_back(frame);
      if (bus.drop_one && direct && !bus.dropped) {
        bus.dropped = true;
        return true;
      }
    }
    if (bytes[0] == OTA_PROOF && direct) ++bus.direct_proofs;
    bus.queue.push_back(std::move(frame));
    return true;
  }

  void begin(const uint8_t* mota, uint32_t length, bool direct_client = true,
             bool direct_server = true, uint8_t hops = 0) {
    server.begin(0, send, &from_server);
    client.begin(SIM_TARGET_ID, send, &from_client);
    server.set_direct_transport_enabled(direct_server);
    client.set_direct_transport_enabled(direct_client);
    client.set_max_hops(hops);
    client.set_fetch_store(&store);
    server.set_clock(now); client.set_clock(now);
    ASSERT_TRUE(server.serve(mota, length));
  }

  void pull(const MotaManifest& manifest) {
    ASSERT_EQ(client.pull(manifest.merkle_root, manifest.target_id), OtaManager::PULL_STARTED);
  }

  void deliver() {
    unsigned bounded = 1000;
    while (!queue.empty() && bounded--) {
      Frame frame = std::move(queue.front());
      queue.erase(queue.begin());
      frame.destination->note_rx_path_hops(0);
      frame.destination->on_message(frame.bytes.data(), frame.bytes.size(), frame.route);
    }
    ASSERT_GT(bounded, 0u);
  }

  void tick() {
    server.set_clock(now); client.set_clock(now);
    server.serviceEgress(); client.serviceEgress();
    deliver();
    server.loop(); client.loop();
    deliver();
    now += 10;
  }

  void finish(const uint8_t* expected, uint32_t length) {
    for (unsigned i = 0; i < 30000 && client.fetchState() != OtaManager::COMPLETE &&
         client.fetchState() != OtaManager::FAILED; ++i) tick();
    ASSERT_EQ(client.fetchState(), OtaManager::COMPLETE);
    ASSERT_EQ(store.staged_size(), length);
    EXPECT_EQ(0, memcmp(store.data(), expected, length));
    EXPECT_EQ(client.blocksHave(), client.blocksTotal());
  }
};
}

TEST(OtaDirectManager, RoutePaddingAndLegacyPayloadCapacityAreUnchanged) {
  EXPECT_EQ(sizeof(OtaReplyRoute), 8u);
  EXPECT_EQ(MAX_PACKET_PAYLOAD, 184);
  EXPECT_EQ(OTA_MAX_BLOCK, 2048);
  EXPECT_EQ(OTA_DIRECT_FRAG_DATA, 240);
  OtaReplyRoute normal;
  OtaReplyRoute direct = normal; direct.direct = true;
  EXPECT_FALSE(normal == direct);
}

TEST(OtaDirectManager, RawOneAndTwoKiBBlocksUseFullDirectMtu) {
  for (const auto& vector : {std::make_pair(SIM_MOTA_1K, SIM_MOTA_1K_LEN),
                             std::make_pair(SIM_MOTA_2K, SIM_MOTA_2K_LEN)}) {
    DirectManagerBus bus;
    MotaManifest manifest;
    ASSERT_TRUE(mota_parse(vector.first, vector.second, manifest));
    bus.begin(vector.first, vector.second);
    bus.pull(manifest);
    bus.finish(vector.first, vector.second);
    EXPECT_TRUE(bus.client.directFetchConfirmed());
    EXPECT_FALSE(bus.direct_requests.empty());
    EXPECT_EQ(bus.direct_proofs, manifest.block_count);
    bool full = false;
    for (const auto& frame : bus.data) {
      EXPECT_TRUE(frame.route.direct);
      if (frame.bytes.size() == OTA_DIRECT_MESSAGE_MAX) full = true;
    }
    EXPECT_TRUE(full);
  }
}

TEST(OtaDirectManager, CompressionAndRawTailRetainMerkleIntegrity) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  VectorDeflateCodec codec{&manifest};
  bus.server.set_transport_deflate_encoder(vector_2k_wire_encode, &codec);
  bus.client.set_transport_deflate_decoder(ota_transport_inflate);
  bus.pull(manifest);
  bus.finish(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  EXPECT_GT(codec.calls, 0u);
  EXPECT_TRUE(bus.client.directFetchConfirmed());
}

TEST(OtaDirectManager, CompressedOneKiBRetainsDirectIdentityAndProofs) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
  bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
  TestWireCodec codec{&manifest};
  bus.server.set_transport_deflate_encoder(test_wire_encode, &codec);
  bus.client.set_transport_deflate_decoder(test_wire_decode, &codec);
  bus.pull(manifest);
  bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
  EXPECT_TRUE(bus.client.directFetchConfirmed());
  EXPECT_GT(codec.calls, 0u);
}

TEST(OtaDirectManager, OldSourceDropsProbeThenUses171ByteFallback) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN, true, false);
  bus.pull(manifest);
  bus.finish(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  EXPECT_EQ(bus.direct_requests.size(), 1u);
  EXPECT_GT(bus.legacy_requests, 0u);
  EXPECT_FALSE(bus.client.directFetchActive());
  EXPECT_EQ(bus.client.directFetchRoute().profile, 1);
  EXPECT_EQ(bus.client.directFetchRoute().generation, 19u);
  for (const auto& frame : bus.data) {
    EXPECT_FALSE(frame.route.direct);
    EXPECT_LE(frame.bytes.size(), 184u);
  }
}

TEST(OtaDirectManager, SourceVanishingAfterCommittedBlockFallsBackWithoutRewritingIt) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  bus.lose_direct_after_first_block = true;
  bus.pull(manifest);
  for (unsigned i = 0; i < 1000 && bus.client.blocksHave() == 0; ++i) bus.tick();
  ASSERT_EQ(bus.client.blocksHave(), 1u);
  const uint32_t epoch = bus.client.directTransportEpoch();
  bus.finish(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  EXPECT_FALSE(bus.client.directFetchActive());
  EXPECT_NE(bus.client.directTransportEpoch(), epoch);
  ASSERT_EQ(bus.direct_requests.size(), 3u); // first block, next flight, one same-mode sparse retry
  EXPECT_EQ(bus.dropped_direct_requests, 2u);
  ASSERT_GE(bus.requests.size(), 4u);
  EXPECT_GE(uint32_t(bus.request_times[2] - bus.request_times[1]), OTA_FETCH_RETRY_FALLBACK_MS);
  EXPECT_GE(uint32_t(bus.request_times[3] - bus.request_times[2]), OTA_FETCH_RETRY_FALLBACK_MS);
  for (size_t i = 1; i < bus.requests.size(); ++i) {
    ReqWindowMsg request{};
    ASSERT_TRUE(decode_req_window(bus.requests[i].bytes.data(), bus.requests[i].bytes.size(), request));
    for (uint8_t item = 0; item < request.n_items; ++item) EXPECT_NE(request.items[item].block_idx, 0u);
  }
  bool saw_normal = false;
  for (const auto& frame : bus.data) {
    if (!frame.route.direct) {
      saw_normal = true;
      DataMsg message{};
      ASSERT_TRUE(decode_data(frame.bytes.data(), frame.bytes.size(), message));
      EXPECT_NE(message.block_idx, 0u);
    }
  }
  EXPECT_TRUE(saw_normal);
}

TEST(OtaDirectManager, ConfirmedEmptyFlightsRetryWideAndResetGraceAfterProgress) {
  for (uint32_t started : {0u, UINT32_MAX - 2000u}) {
    DirectManagerBus bus;
    bus.now = started;
    MotaManifest manifest;
    ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
    ASSERT_EQ(manifest.block_count, 3u);
    bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    bus.drop_direct_requests_after_first_block = 1;
    bus.pull(manifest);
    bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    EXPECT_TRUE(bus.client.directFetchConfirmed());
    EXPECT_EQ(bus.legacy_requests, 0u);
    EXPECT_EQ(bus.dropped_direct_requests, 1u);
    ASSERT_EQ(bus.direct_requests.size(), 4u);
    // The lost two-block flight is repaired one slot per deadline. Progress on
    // block 1 must renew the grace for still-empty block 2, not downgrade it.
    const uint8_t expected_items[] = {1, 2, 1, 1};
    const uint16_t expected_first[] = {0, 1, 1, 2};
    for (size_t i = 0; i < bus.direct_requests.size(); ++i) {
      ReqWindowMsg request{};
      const auto& bytes = bus.direct_requests[i];
      ASSERT_TRUE(decode_req_window(bytes.data(), bytes.size(), request));
      EXPECT_EQ(request.n_items, expected_items[i]);
      EXPECT_EQ(request.items[0].block_idx, expected_first[i]);
    }
    for (const auto& frame : bus.data) EXPECT_TRUE(frame.route.direct);
    EXPECT_EQ(bus.client.directFetchRoute().profile, 1);
    EXPECT_EQ(bus.client.directFetchRoute().generation, 19u);
  }
}

TEST(OtaDirectManager, ConfirmedEmptyRetryAdmissionDoesNotConsumeGrace) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  bus.lose_direct_after_first_block = true;
  bus.pull(manifest);
  for (unsigned i = 0; i < 1000 && bus.client.blocksHave() == 0; ++i) bus.tick();
  ASSERT_EQ(bus.client.blocksHave(), 1u);
  ASSERT_TRUE(bus.client.directFetchConfirmed());
  ASSERT_EQ(bus.direct_requests.size(), 2u);
  const uint32_t epoch = bus.client.directTransportEpoch();
  bus.reject_direct_requests_after_first_block = 3;
  bus.now += bus.client.fetchRetryTimeoutMs();
  for (unsigned i = 1; i <= 3; ++i) {
    bus.tick();
    EXPECT_EQ(bus.rejected_direct_requests, i);
    EXPECT_EQ(bus.direct_requests.size(), 2u);
    EXPECT_EQ(bus.legacy_requests, 0u);
    EXPECT_TRUE(bus.client.directFetchConfirmed());
    EXPECT_EQ(bus.client.directTransportEpoch(), epoch);
  }
  bus.tick(); // one accepted wide retry; its RF loss must use a fresh deadline
  ASSERT_EQ(bus.direct_requests.size(), 3u);
  bus.tick();
  EXPECT_EQ(bus.direct_requests.size(), 3u);
  EXPECT_EQ(bus.legacy_requests, 0u);
  EXPECT_TRUE(bus.client.directFetchConfirmed());
  bus.finish(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  EXPECT_FALSE(bus.client.directFetchActive());
  EXPECT_EQ(bus.direct_requests.size(), 3u);
  EXPECT_NE(bus.client.directTransportEpoch(), epoch);
}

TEST(OtaDirectManager, ConfirmedEmptyRetryCannotOutliveItsRadioSession) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  bus.lose_direct_after_first_block = true;
  bus.pull(manifest);
  for (unsigned i = 0; i < 1000 && bus.client.blocksHave() == 0; ++i) bus.tick();
  ASSERT_EQ(bus.client.blocksHave(), 1u);
  bus.now += bus.client.fetchRetryTimeoutMs();
  bus.tick();
  ASSERT_EQ(bus.direct_requests.size(), 3u);
  ASSERT_TRUE(bus.client.directFetchConfirmed());
  const uint32_t epoch = bus.client.directTransportEpoch();
  bus.client.serviceEgress([](void*, const OtaReplyRoute&) { return false; }, nullptr);
  EXPECT_EQ(bus.client.fetchState(), OtaManager::FAILED);
  EXPECT_EQ(bus.client.fetchError(), OtaManager::FETCH_ERROR_RADIO_SESSION);
  EXPECT_NE(bus.client.directTransportEpoch(), epoch);
  bus.now += OTA_FETCH_RETRY_FALLBACK_MS;
  bus.tick();
  EXPECT_EQ(bus.direct_requests.size(), 3u);
  EXPECT_EQ(bus.legacy_requests, 0u);
}

TEST(OtaDirectManager, CanonicalNormalAnswerBeforeConfirmationDowngradesWholeFlight) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
  bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
  bus.downgrade_probe = true;
  bus.pull(manifest);
  bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
  EXPECT_EQ(bus.direct_requests.size(), 1u);
  EXPECT_FALSE(bus.client.directFetchActive());
}

TEST(OtaDirectManager, FeatureOffAndRoutedSettingsNeverProbe) {
  for (unsigned mode = 0; mode < 2; ++mode) {
    DirectManagerBus bus;
    MotaManifest manifest;
    ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
    bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN, mode != 0, true, mode ? 1 : 0);
    bus.pull(manifest);
    bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    EXPECT_TRUE(bus.direct_requests.empty());
  }
}

TEST(OtaDirectManager, LostDirectFragmentRecoversOnlyHolesAndWorksAcrossRollover) {
  DirectManagerBus bus;
  bus.now = UINT32_MAX - 200;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  bus.drop_one = true;
  bus.pull(manifest);
  bus.finish(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  ASSERT_GT(bus.direct_requests.size(), 1u);
  bool recovered_one = false;
  for (const auto& bytes : bus.direct_requests) {
    ReqWindowMsg request{};
    ASSERT_TRUE(decode_req_window(bytes.data(), bytes.size(), request));
    if (request.n_items == 1 && ota_req_v2_fragments(request.items[0].want_mask) == 1u)
      recovered_one = true;
  }
  EXPECT_TRUE(recovered_one);
  EXPECT_TRUE(bus.client.directFetchConfirmed());
}

TEST(OtaDirectManager, ConfirmedDirectRejectsNormalMetadataAndExpiredSession) {
  DirectManagerBus bus;
  MotaManifest manifest;
  ASSERT_TRUE(mota_parse(SIM_MOTA_2K, SIM_MOTA_2K_LEN, manifest));
  bus.begin(SIM_MOTA_2K, SIM_MOTA_2K_LEN);
  bus.pull(manifest);
  for (unsigned i = 0; i < 100 && bus.data.empty(); ++i) bus.tick();
  ASSERT_FALSE(bus.data.empty());
  ASSERT_TRUE(bus.client.directFetchConfirmed());
  const auto& frame = bus.data.front();
  OtaReplyRoute normal = frame.route; normal.direct = false;
  EXPECT_FALSE(bus.client.on_message(frame.bytes.data(), frame.bytes.size(), normal));
  OtaReplyRoute stale = frame.route; ++stale.generation;
  EXPECT_FALSE(bus.client.on_message(frame.bytes.data(), frame.bytes.size(), stale));
  EXPECT_FALSE(bus.client.on_message(frame.bytes.data(), frame.bytes.size() - 1, frame.route));
  auto oversized = frame.bytes; oversized.push_back(0);
  EXPECT_FALSE(bus.client.on_message(oversized.data(), oversized.size(), frame.route));
  const uint32_t epoch = bus.client.directTransportEpoch();
  bus.client.serviceEgress([](void*, const OtaReplyRoute&) { return false; }, nullptr);
  EXPECT_EQ(bus.client.fetchState(), OtaManager::FAILED);
  EXPECT_EQ(bus.client.fetchError(), OtaManager::FETCH_ERROR_RADIO_SESSION);
  EXPECT_NE(bus.client.directTransportEpoch(), epoch);
}

TEST(OtaDirectManager, EpochRetiresClearResetRecreatedContextsAndNeverUsesZero) {
  uint32_t previous = 0;
  {
    OtaManager manager;
    previous = manager.directTransportEpoch();
    EXPECT_NE(previous, 0u);
    manager.clearPendingEgress();
    EXPECT_NE(manager.directTransportEpoch(), previous);
    previous = manager.directTransportEpoch();
    manager.reset_session();
    EXPECT_NE(manager.directTransportEpoch(), previous);
    previous = manager.directTransportEpoch();
  }
  OtaManager replacement;
  EXPECT_NE(replacement.directTransportEpoch(), previous);
  EXPECT_EQ(ota_advance_direct_epoch(UINT32_MAX), 1u);
  EXPECT_EQ(ota_advance_direct_epoch(0), 1u);
}

TEST(OtaDirectManager, CompletionAndDirectPauseRetireAdmittedRawFrames) {
  for (bool pause : {false, true}) {
    DirectManagerBus bus;
    MotaManifest manifest;
    ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
    bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    bus.pull(manifest);
    for (unsigned i = 0; i < 100 && !bus.client.directFetchConfirmed(); ++i) bus.tick();
    ASSERT_TRUE(bus.client.directFetchConfirmed());
    const uint32_t epoch = bus.client.directTransportEpoch();
    if (pause) {
      ASSERT_TRUE(bus.client.pauseFetchForDisconnect());
      EXPECT_EQ(bus.client.fetchState(), OtaManager::PAUSED);
    } else {
      bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    }
    EXPECT_NE(bus.client.directTransportEpoch(), epoch);
  }
}

namespace {
class PrecompressedSource : public RamMotaSource {
public:
  explicit PrecompressedSource(const MotaManifest& manifest) : m(manifest) {
    add(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    setSourceCaps(MOTA_SOURCE_CAP_DEFLATE_BLOCK);
  }
  MotaManifest m;
  unsigned raw_reads = 0, encoded_reads = 0;
  int bad = 0;
  bool read(uint8_t idx, uint32_t off, uint8_t* out, uint32_t len) override {
    if (off >= uint32_t(m.payload - SIM_MOTA_1K) && len) ++raw_reads;
    return RamMotaSource::read(idx, off, out, len);
  }
  bool read_deflated_block(uint8_t, uint16_t block, uint8_t* out,
                           uint16_t cap, uint16_t* length) override {
    ++encoded_reads;
    if (bad) { *length = bad == 1 ? 0 : (bad == 3 ? cap + 1 : cap); return bad != 4; }
    out[0] = 0xD1; out[1] = uint8_t(block); *length = 2;
    return true;
  }
};
}

TEST(OtaDirectManager, HostPrecompressedFirstAvoidsRedundantRawHostReads) {
  for (int bad : {0, 1, 2, 3, 4}) {
    DirectManagerBus bus;
    MotaManifest manifest;
    ASSERT_TRUE(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
    bus.begin(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    bus.server.clear_primary();
    PrecompressedSource source(manifest); source.bad = bad;
    ASSERT_TRUE(bus.server.add_source(&source));
    TestWireCodec codec{&manifest};
    bus.client.set_transport_deflate_decoder(test_wire_decode, &codec);
    bus.pull(manifest);
    bus.finish(SIM_MOTA_1K, SIM_MOTA_1K_LEN);
    EXPECT_GT(source.encoded_reads, 0u);
    if (bad) EXPECT_GT(source.raw_reads, 0u);
    else EXPECT_EQ(source.raw_reads, 0u);
  }
}
