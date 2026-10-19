
using Workspace = SourceMerkleProof<4096, OTA_LEAVES_FRAG>;
static_assert(sizeof(Workspace) <= 1536, "bounded allocation includes getter scratch");

class LeafSource : public MotaSource {
public:
  std::vector<uint8_t> bytes;
  uint16_t quantum = 128;
  unsigned calls = 0, fail_at = 0;
  uint8_t root[4];
  explicit LeafSource(uint32_t count) : bytes(17 + count * 4) {
    for (uint32_t i = 0; i < count; ++i) {
      uint8_t value[4]; wr_u32le(value, i * 8191u + count);
      merkle_leaf(bytes.data() + 17 + i * 4, value, 4);
    }
    merkle_root(root, bytes.data() + 17, count);
  }
  uint8_t count() override { return 1; }
  bool describe(uint8_t, MotaDesc&) override { return false; }
  uint16_t read_quantum() const override { return quantum; }
  bool read(uint8_t index, uint32_t offset, uint8_t* output, uint32_t length) override {
    ++calls;
    assert(length <= quantum && length <= 128);
    if (fail_at == calls) { if (length) output[0] = 0xEF; return false; }
    if (index || uint64_t(offset) + length > bytes.size()) return false;
    memcpy(output, bytes.data() + offset, length); return true;
  }
};

static std::vector<std::vector<uint8_t>> reference_levels(const uint8_t* leaves, uint32_t count) {
  std::vector<std::vector<uint8_t>> levels{{leaves, leaves + count * 4}};
  while (count > 1) {
    std::vector<uint8_t> next(((count + 1) / 2) * 4);
    const auto& previous = levels.back();
    for (uint32_t i = 0; i < count; i += 2) {
      if (i + 1 < count) merkle_combine(next.data() + (i / 2) * 4,
          previous.data() + i * 4, previous.data() + (i + 1) * 4);
      else memcpy(next.data() + (i / 2) * 4, previous.data() + i * 4, 4);
    }
    levels.push_back(std::move(next)); count = (count + 1) / 2;
  }
  return levels;
}

static void grouped_equivalence() {
  std::vector<uint32_t> counts;
  for (uint32_t count = 1; count <= 65; ++count) counts.push_back(count);
  for (uint32_t count : {127,128,129,255,256,257,511,512,513,1023,1024,1025,
                         2047,2048,2049,4095,4096}) counts.push_back(count);
  size_t checked = 0;
  for (uint32_t count : counts) {
    LeafSource source(count);
    source.quantum = count <= 65 ? uint16_t(std::array<int, 5>{1,3,17,127,128}[count % 5]) : 127;
    Workspace workspace;
    assert(workspace.bind(&source, 0, 17, count, source.root));
    const auto expected = reference_levels(source.bytes.data() + 17, count);
    for (uint32_t block = 0; block < count; ++block) {
      uint8_t output[128]; uint8_t length = 99;
      Workspace::Step step = Workspace::Step::Pending;
      for (unsigned turn = 0; step == Workspace::Step::Pending && turn < 20000; ++turn) {
        const auto before = source.calls;
        step = workspace.proof(block, output, length);
        assert(source.calls - before <= 1);
      }
      assert(step == Workspace::Step::Ready);
      std::vector<uint8_t> proof;
      uint32_t index = block;
      for (const auto& row : expected) {
        const uint32_t sibling = index ^ 1u;
        if (sibling < row.size() / 4)
          proof.insert(proof.end(), row.begin() + sibling * 4, row.begin() + sibling * 4 + 4);
        index /= 2;
      }
      assert(proof.size() == size_t(length) * 4);
      assert(proof.empty() || !memcmp(output, proof.data(), proof.size()));
      assert(merkle_verify_from_leaf(source.bytes.data() + 17 + block * 4, block,
          output, length, source.root, count));
      const auto before = source.calls;
      assert(workspace.proof(block, output, length) == Workspace::Step::Ready);
      assert(source.calls == before); // exact retry never re-reads a pinned group
      ++checked;
    }
  }
  printf("PASS: byte-identical grouped proofs (%zu blocks, max4096; workspace=%zu)\n", checked, sizeof(Workspace));
}

static void helper_faults() {
  LeafSource source(65), other(65); source.quantum = 3; other.quantum = 17;
  Workspace workspace;
  assert(!workspace.bind(nullptr, 0, 17, 65, source.root));
  assert(!workspace.bind(&source, 0, 17, 0, source.root));
  assert(!workspace.bind(&source, 0, 17, 4097, source.root));
  assert(!workspace.bind(&source, 0, UINT32_MAX, 65, source.root));
  assert(workspace.bind(&source, 0, 17, 65, source.root));
  assert(workspace.prepare() == Workspace::Step::Pending);
  const auto warmed = source.calls;
  const uint8_t* fragment = nullptr;
  while (workspace.fragment(&other, 0, 17, 101, fragment) == Workspace::Step::Pending) {}
  assert(fragment && !memcmp(fragment, other.bytes.data() + 17, 101));
  const auto read = other.calls;
  assert(workspace.fragment(&other, 0, 17, 101, fragment) == Workspace::Step::Ready);
  assert(other.calls == read && source.calls == warmed);
  workspace.releaseFragment();
  source.fail_at = source.calls + 1;
  assert(workspace.prepare() == Workspace::Step::Failed); // partial writes never authenticated
  source.fail_at = 0;
  assert(workspace.bind(&source, 0, 17, 65, source.root));
  while (workspace.prepare() == Workspace::Step::Pending) {}
  source.bytes[17] ^= 1; // source changed after the authenticated root cache was built
  uint8_t proof[128], length = 0;
  Workspace::Step step;
  do { step = workspace.proof(0, proof, length); } while (step == Workspace::Step::Pending);
  assert(step == Workspace::Step::Failed && length == 0);
  source.bytes[17] ^= 1;
  uint8_t wrong_root[4]; memcpy(wrong_root, source.root, 4); wrong_root[0] ^= 1;
  assert(workspace.bind(&source, 0, 17, 65, wrong_root));
  do { step = workspace.prepare(); } while (step == Workspace::Step::Pending);
  assert(step == Workspace::Step::Failed);
  source.quantum = 0;
  assert(workspace.bind(&source, 0, 17, 65, source.root));
  assert(workspace.prepare() == Workspace::Step::Failed);
  workspace.reset();
  assert(workspace.proof(0, proof, length) == Workspace::Step::Failed);
  puts("PASS: partial reads and source changes");
}

class TrackedSource : public Source {
public:
  using Source::Source;
  uint16_t quantum = 17;
  uint32_t leaf_bytes = 0, leaf_calls = 0;
  bool fail_leaves = false;
  uint16_t read_quantum() const override { return quantum; }
  bool read(uint8_t index, uint32_t offset, uint8_t* output, uint32_t length) override {
    const uint32_t first = 8 + MOTA_MFL, last = first + manifest.block_count * 4;
    if (offset >= first && offset < last) {
      assert(length <= quantum && length <= 128 && offset + length <= last);
      ++leaf_calls; leaf_bytes += length;
      if (fail_leaves) { if (length) output[0] = 0xEE; return false; }
    }
    return Source::read(index, offset, output, length);
  }
};

static void manager_lifetimes() {
  TrackedSource a(4096, 0x73), b(65, 0x56);
  a.host_encoding = b.host_encoding = Source::HostEncoding::Good;
  a.fail_payload = true;
  Sent sent;
  {
    OtaManager manager; manager.begin(123, send, &sent);
    assert(manager.add_source(&a) && manager.add_source(&b));
    assert(request(manager, a, 4095));
    assert(allocated == 0); // receiving a request never allocates a full leaf table
    sent.accept_data = sent.accept_proof = false;
    manager.serviceEgress(); // cache host-generated DATA
    assert(a.host_calls == 1);
    for (unsigned i = 0; i < 20; ++i) {
      const auto before = a.leaf_calls;
      manager.serviceEgress(); assert(a.leaf_calls - before <= 1);
    }
    assert(allocated == sizeof(Workspace) && peak <= 1536);
    // A different MID's manifest and getter must not restart A's warming.
    assert(manifest_request(manager, b));
    for (unsigned i = 0; i < 20 && manager.pendingManifestJobs(); ++i) {
      manager.set_clock(100000 + i * 10000); manager.serviceEgress();
    }
    assert(!manager.pendingManifestJobs());
    GetLeavesMsg get{}; memcpy(get.manifest_id, b.manifest.merkle_root, 4); get.want_mask = 1;
    uint8_t wire[MAX_PACKET_PAYLOAD];
    assert(manager.on_message(wire, encode_get_leaves(wire, sizeof(wire), get)));
    sent.accept_manifest = false;
    for (unsigned i = 0; i < 30 && manager.pendingManifestJobs(); ++i) {
      const auto before = b.leaf_calls;
      manager.set_clock(400000 + i * 10000); manager.serviceEgress();
      assert(b.leaf_calls - before <= 1);
    }
    assert(manager.pendingManifestJobs());
    const auto getter_reads = b.leaf_calls;
    for (unsigned i = 0; i < 10; ++i) manager.serviceEgress();
    assert(b.leaf_calls == getter_reads); // a rejected getter emit pins its exact fragment
    sent.accept_manifest = true;
    manager.serviceEgress();
    assert(!manager.pendingManifestJobs());
    bool getter_seen = false;
    for (const auto& packet : sent.packets) {
      LeavesMsg leaves{};
      if (decode_leaves(packet.data(), packet.size(), leaves)) {
        assert(leaves.len == OTA_LEAVES_FRAG &&
               !memcmp(leaves.bytes, b.manifest.leaves, leaves.len));
        getter_seen = true;
      }
    }
    assert(getter_seen);
    sent.accept_data = true;
    for (unsigned i = 0; i < 2000; ++i) {
      const auto before = a.leaf_calls;
      manager.set_clock(800000 + i * 1000); manager.serviceEgress();
      assert(a.leaf_calls - before <= 1);
    }
    assert(manager.pendingServeJobs() && a.host_calls == 1);
    const auto before = a.leaf_calls;
    for (unsigned i = 0; i < 10; ++i) manager.serviceEgress();
    assert(a.leaf_calls == before); // ready proof held under emit backpressure
    sent.accept_proof = true;
    manager.serviceEgress();
    assert(!manager.pendingServeJobs());
    verify_v2(sent, a, 4095, true);
    manager.set_clock(5000000); manager.loop();
    assert(allocated == 0); // idle timer releases the only workspace without an encoder scratch
    sent.accept_data = false;
    assert(request(manager, a, 4095));
    fail_next = true;
    manager.serviceEgress(); manager.serviceEgress();
    assert(!manager.pendingServeJobs() && !fail_next && allocated == 0);
    a.fail_leaves = true;
    assert(request(manager, a, 4095));
    manager.serviceEgress(); manager.serviceEgress();
    assert(!manager.pendingServeJobs());
    manager.clear_sources(); assert(allocated == 0);
    a.fail_leaves = false;
    assert(manager.add_source(&a) && request(manager, a, 4095));
    manager.serviceEgress(); manager.serviceEgress();
    assert(allocated == sizeof(Workspace));
    assert(manager.remove_source(&a)); assert(allocated == 0 && !manager.pendingServeJobs());
  }
  assert(allocated == 0);
  puts("PASS: bounded manager ownership and backpressure");
}

static void simultaneous_capture() {
  for (bool host : {false, true}) {
    TrackedSource outgoing(5, 0x40);
    Source incoming(3, 0x70);
    if (host) {
      outgoing.host_encoding = Source::HostEncoding::Good;
      outgoing.host_encoded_size = 400;
    }
    Sent sent; OtaManager manager; OtaStoreRam<16384> store;
    manager.begin(123, send, &sent); manager.set_fetch_store(&store);
    manager.set_transport_deflate_encoder(encode); manager.set_transport_deflate_decoder(decode);
    assert(manager.add_source(&outgoing) && request(manager, outgoing));
    sent.accept_data = false;
    manager.serviceEgress(); manager.serviceEgress();
    const size_t expected_allocation = sizeof(Workspace) + (host ? 0 : OTA_MAX_BLOCK);
    assert(allocated == expected_allocation);
    assert(manager.pull_archive(incoming.manifest.merkle_root, 123, false) == OtaManager::PULL_STARTED);
    for (unsigned fragment = 0; fragment < 2; ++fragment) {
      ManifestMsg mf{}; memcpy(mf.manifest_id, incoming.manifest.merkle_root, 4);
      mf.frag_idx = fragment; mf.frag_total = 2;
      mf.bytes = incoming.manifest.manifest_start + fragment * OTA_MF_FRAG;
      mf.len = fragment == 0 ? OTA_MF_FRAG : MOTA_MFL - OTA_MF_FRAG;
      uint8_t wire[MAX_PACKET_PAYLOAD];
      manager.on_message(wire, encode_manifest(wire, sizeof(wire), mf));
    }
    assert(manager.fetchState() == OtaManager::FETCHING);
    for (uint16_t block = 0; block < 3; ++block) {
      uint8_t representation[12]; memset(representation + 4, uint8_t(0x70 + block), 8);
      mh4(representation, representation + 4, 8);
      DataMsg data{}; memcpy(data.manifest_id, incoming.manifest.merkle_root, 4); data.block_idx = block;
      assert(ota_data_v2_pack_extended(0, 8, data.frag_off));
      data.data = representation; data.data_len = sizeof(representation);
      uint8_t wire[MAX_PACKET_PAYLOAD];
      assert(manager.on_message(wire, encode_data(wire, sizeof(wire), data)));
      uint8_t siblings[128]; ProofMsg proof{};
      memcpy(proof.manifest_id, incoming.manifest.merkle_root, 4); proof.block_idx = block;
      proof.n_proof = merkle_gen_proof(incoming.manifest.leaves, 3, block, nullptr, siblings);
      proof.proof = siblings;
      assert(manager.on_message(wire, encode_proof(wire, sizeof(wire), proof)));
      manager.set_clock(100000 + block * 10000); manager.loop();
      assert(allocated == expected_allocation && manager.pendingServeJobs());
    }
    assert(manager.fetchState() == OtaManager::COMPLETE);
    MotaManifest staged; assert(mota_parse(store.data(), store.staged_size(), staged));
    assert(!memcmp(staged.payload, incoming.manifest.payload, incoming.manifest.payload_size));
    sent.accept_data = true; drain(manager, 200000);
    if (host) verify_v2(sent, outgoing, 0, true, 400);
    else verify(sent, outgoing, true);
    manager.clear_sources(); assert(allocated == 0);
  }
  puts("PASS: simultaneous capture and serving");
}

int main() {
  grouped_equivalence(); helper_faults(); manager_lifetimes(); simultaneous_capture();
  assert(allocated == 0);
  return 0;
}
