#include <helpers/ota/FolderMotaStore.h>
#include <helpers/ota/OtaByteIO.h>
#include <helpers/ota/OtaManager.h>
#include <helpers/ota/MotaSeederProto.h>
#include <Utils.h>
#include "test_ota/mota_vectors.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <vector>

using namespace mesh::ota;

namespace mesh {
namespace ota {
struct OtaContext {
  OtaManager manager;
  OtaStoreRam<32768> fetch_store;
  FolderMotaStore* folder_dest = nullptr;
  const char* folder_dest_info = "test host";
  bool apply_pending = false, fetch_to_folder = false;
};

// @CLI_HELPERS@

static bool pull_command(const char* a, char* reply, OtaContext& c) {
  const char* rest = nullptr;
  if (false) {
// @CLI_PULL@
  } else {
    return false;
  }
  return true;
}
}  // namespace ota
}  // namespace mesh

struct HostStream : Stream {
  enum Failure { None, Timeout, Truncated, BadChecksum, Error, ShortWrite, WrongOp };
  std::vector<uint8_t> bytes{SIM_MOTA_1K, SIM_MOTA_1K + SIM_MOTA_1K_LEN};
  std::vector<uint8_t> response;
  std::vector<uint8_t> operations;
  size_t position = 0;
  bool present = true;
  uint8_t fail_op = MS_OP_SREAD;
  uint32_t fail_offset = 0;
  Failure failure = None;
  unsigned failures_left = 0, begins = 0, writes = 0;

  int available() override { return response.size() - position; }
  int read() override { return position < response.size() ? response[position++] : -1; }
  int peek() override { return -1; }
  void flush() override { assert(false); }
  size_t write(uint8_t) override { assert(false); return 0; }
  size_t write(const uint8_t* request, size_t length) override {
    assert(length >= 4 && request[0] == MOTA_SEEDER_REQ_MAGIC0
           && request[1] == MOTA_SEEDER_REQ_MAGIC1);
    uint8_t checksum = 0;
    for (size_t i = 2; i < length - 1; ++i) checksum ^= request[i];
    assert(checksum == request[length - 1]);
    const uint8_t op = request[2];
    operations.push_back(op);
    const bool fail = failures_left && op == fail_op
        && (op != MS_OP_SREAD || rd_u32le(request + 7) == fail_offset);
    if (fail) --failures_left;
    response.clear();
    position = 0;
    if (fail && failure == ShortWrite) return length - 1;
    if (fail && failure == Timeout) return length;
    response = {MOTA_SEEDER_RSP_MAGIC0, MOTA_SEEDER_RSP_MAGIC1,
                uint8_t(fail && failure == WrongOp ? op + 1 : op),
                uint8_t(fail && failure == Error ? MS_STATUS_ERR : MS_STATUS_OK)};
    if (!(fail && failure == Error)) {
      if (op == MS_OP_STAT) {
        response.push_back(present ? 1 : 0);
        uint8_t size[4]; wr_u32le(size, bytes.size());
        response.insert(response.end(), size, size + sizeof(size));
      } else if (op == MS_OP_SREAD) {
        const uint32_t offset = rd_u32le(request + 7);
        const uint16_t count = request[11] | (uint16_t(request[12]) << 8);
        assert(present && offset <= bytes.size() && count <= bytes.size() - offset);
        response.insert(response.end(), bytes.begin() + offset, bytes.begin() + offset + count);
      } else if (op == MS_OP_BEGIN) {
        ++begins;
        present = true;
        bytes.assign(rd_u32le(request + 7), 0xFF);
      } else if (op == MS_OP_WRITE) {
        ++writes;
        const uint32_t offset = rd_u32le(request + 7);
        const uint16_t count = request[11] | (uint16_t(request[12]) << 8);
        assert(present && offset <= bytes.size() && count <= bytes.size() - offset);
        std::copy(request + 13, request + 13 + count, bytes.begin() + offset);
      } else {
        assert(op == MS_OP_FIN);
      }
    }
    checksum = 0;
    for (uint8_t value : response) checksum ^= value;
    response.push_back(fail && failure == BadChecksum ? uint8_t(checksum ^ 1) : checksum);
    if (fail && failure == Truncated) response.resize(4);
    return length;
  }
};

struct Flight {
  unsigned manifests = 0;
  std::vector<uint32_t> requested_blocks;
  static bool send(void* context, const uint8_t* bytes, uint16_t length, bool) {
    auto& flight = *static_cast<Flight*>(context);
    GetManifestMsg manifest{};
    if (decode_get_manifest(bytes, length, manifest)) ++flight.manifests;
    ReqWindowMsg request{};
    if (decode_req_window(bytes, length, request)) {
      for (uint8_t i = 0; i < request.n_items; ++i)
        flight.requested_blocks.push_back(request.items[i].block_idx);
    }
    return true;
  }
};

struct Fixture {
  HostStream host;
  FolderMotaStore store{host, MotaStreamWritePolicy::NoFlush, 4};
  OtaContext context;
  Flight flight;
  MotaManifest manifest;

  Fixture() {
    assert(mota_parse(SIM_MOTA_1K, SIM_MOTA_1K_LEN, manifest));
    context.folder_dest = &store;
    context.manager.begin(manifest.target_id, Flight::send, &flight);
    context.manager.set_wire_v2_enabled(false);
    uint8_t row[OTA_HAVE_ROW_BYTES] = {};
    memcpy(row, manifest.merkle_root, 4);
    wr_u32le(row + 4, manifest.target_id);
    wr_u32le(row + 8, manifest.fw_version);
    row[12] = manifest.codec_id;
    row[13] = manifest.flags;
    HaveMsg have{};
    have.seeder_id[0] = 1;
    have.frag_total = have.n_rows = 1;
    have.rows = row;
    uint8_t wire[256];
    context.manager.on_message(wire, encode_have(wire, sizeof(wire), have));
    assert(context.manager.catalogCount() == 1);
  }

  void pull(bool validate = false) {
    char reply[160];
    assert(pull_command(validate ? "pull 1 folder validate" : "pull 1 folder", reply, context));
    assert(strncmp(reply, "OK ", 3) == 0);
    if (context.manager.fetchState() == OtaManager::PAUSED) {
      assert(strstr(reply, "OK paused (folder I/O error; reconnect to resume)") == reply);
      assert(context.fetch_to_folder);
    }
  }

  void deliver_manifest() {
    for (uint32_t offset = 0; offset < MOTA_MFL; offset += OTA_MF_FRAG) {
      ManifestMsg message{};
      memcpy(message.manifest_id, manifest.merkle_root, 4);
      message.frag_idx = offset / OTA_MF_FRAG;
      message.frag_total = (MOTA_MFL + OTA_MF_FRAG - 1) / OTA_MF_FRAG;
      message.bytes = manifest.manifest_start + offset;
      message.len = std::min(uint32_t(OTA_MF_FRAG), MOTA_MFL - offset);
      uint8_t wire[256];
      context.manager.on_message(wire, encode_manifest(wire, sizeof(wire), message));
    }
  }

  void finish_verification() {
    for (unsigned guard = 0; guard < 100 && context.manager.fetchState() == OtaManager::VERIFYING_STAGED; ++guard)
      context.manager.loop();
    assert(context.manager.fetchState() != OtaManager::VERIFYING_STAGED);
  }

  void deliver_block(uint32_t block) {
    const uint32_t block_size = manifest.block_size();
    const uint32_t length = std::min(block_size, manifest.payload_size - block * block_size);
    uint8_t wire[256];
    for (uint32_t offset = 0; offset < length; offset += OTA_FRAG_DATA) {
      DataMsg message{};
      memcpy(message.manifest_id, manifest.merkle_root, 4);
      message.block_idx = block;
      message.frag_off = offset;
      message.data = manifest.payload + block * block_size + offset;
      message.data_len = std::min(uint32_t(OTA_FRAG_DATA), length - offset);
      assert(context.manager.on_message(wire, encode_data(wire, sizeof(wire), message)));
    }
    std::vector<uint8_t> scratch(manifest.block_count * 4);
    uint8_t siblings[128];
    ProofMsg proof{};
    memcpy(proof.manifest_id, manifest.merkle_root, 4);
    proof.block_idx = block;
    proof.n_proof = merkle_gen_proof(manifest.leaves, manifest.block_count, block,
                                    scratch.data(), siblings);
    proof.proof = siblings;
    assert(context.manager.on_message(wire, encode_proof(wire, sizeof(wire), proof)));
  }
};

static void transient_resume_failure_preserves_bytes(HostStream::Failure failure,
                                                     uint8_t op, uint32_t offset,
                                                     bool retry_by_pull) {
  Fixture f;
  f.host.failure = failure;
  f.host.fail_op = op;
  f.host.fail_offset = offset;
  f.host.failures_left = 2;
  const auto saved = f.host.bytes;
  f.pull();
  f.deliver_manifest();  // Even a late peer response cannot initialize a paused slot.
  if (f.host.bytes != saved)
    fputs("FAIL: resume I/O fallback issued BEGIN and erased staging\n", stderr);
  assert(f.host.bytes == saved && f.host.begins == 0 && f.host.writes == 0);
  assert(f.context.manager.fetchState() == OtaManager::PAUSED);
  assert(f.context.manager.fetchError() == OtaManager::FETCH_ERROR_STORAGE);
  assert(memcmp(f.context.manager.fetchManifestId(), f.manifest.merkle_root, 4) == 0);
  assert(f.flight.manifests == 0 && f.host.begins == 0 && f.host.writes == 0);
  f.context.manager.set_clock(1000000);
  f.context.manager.loop();
  assert(f.host.bytes == saved && f.flight.manifests == 0);
  char reply[160];
  assert(pull_command("pull 1 folder", reply, f.context));
  assert(strncmp(reply, "ERR OTA slot is paused", 22) == 0);
  assert(!f.context.manager.resumeFetchAfterReconnect());
  assert(f.context.manager.fetchState() == OtaManager::PAUSED);
  assert(f.host.bytes == saved && f.host.begins == 0 && f.flight.manifests == 0);
  if (retry_by_pull) {
    f.context.manager.reset_session(); // The manager action performed by explicit cancellation.
    f.pull();
  } else {
    assert(f.context.manager.resumeFetchAfterReconnect());
  }
  assert(f.context.manager.fetchState() == OtaManager::VERIFYING_STAGED);
  f.finish_verification();
  assert(f.context.manager.fetchState() == OtaManager::COMPLETE);
  assert(f.context.manager.blocksHave() == f.manifest.block_count);
  assert(f.context.manager.fetchError() == OtaManager::FETCH_ERROR_NONE);
  assert(f.host.bytes == saved && f.host.begins == 0 && f.host.writes == 0);
}

static void known_absent_or_invalid_staging_starts_fresh(unsigned corruption,
                                                        bool after_io_failure) {
  Fixture f;
  if (after_io_failure) {
    f.host.failure = HostStream::Timeout;
    f.host.fail_op = MS_OP_STAT;
    f.host.failures_left = 1;
    f.pull();
    assert(f.context.manager.fetchState() == OtaManager::PAUSED);
  }
  if (corruption == 0) f.host.present = false;
  else if (corruption == 1) f.host.bytes[0] ^= 1;
  else if (corruption == 2) wr_u32le(f.host.bytes.data() + 4, f.host.bytes.size() + 1);
  else if (corruption == 3) f.host.bytes[8] = 0xFF; // Invalid manifest format version.
  else if (corruption == 4) f.host.bytes.back() ^= 1;
  else if (corruption == 5) f.host.bytes.resize(12);
  else if (corruption == 6) f.host.bytes[8 + 20] ^= 1; // Another MID, read successfully.
  else wr_u32le(f.host.bytes.data() + 8 + 3, f.manifest.target_id ^ 1u);
  if (after_io_failure) assert(f.context.manager.resumeFetchAfterReconnect());
  else f.pull();
  assert(f.context.manager.fetchState() == OtaManager::WANT_MANIFEST);
  assert(f.context.manager.fetchError() == OtaManager::FETCH_ERROR_NONE);
  assert(!f.store.reopenHadIoError());
  assert(f.flight.manifests == 1 && f.host.begins == 0);
  f.deliver_manifest();
  assert(f.context.manager.fetchState() == OtaManager::FETCHING);
  assert(f.host.begins == 1);
}

static void explicit_validate_requests_fresh_initialization() {
  Fixture f;
  f.host.failure = HostStream::Timeout;
  f.host.fail_op = MS_OP_STAT;
  f.host.failures_left = 1;
  f.pull(true);
  assert(f.context.manager.fetchState() == OtaManager::WANT_MANIFEST);
  assert(f.host.operations.empty() && f.flight.manifests == 1);
  f.deliver_manifest();
  assert(f.host.begins == 1 && f.host.failures_left == 1);
}

static void transient_probe_failure_resumes_only_missing_blocks() {
  Fixture f;
  const auto complete = f.host.bytes;
  const uint32_t missing = 1;
  const uint32_t leaf = f.manifest.leaves - SIM_MOTA_1K + missing * 4;
  const uint32_t payload = f.manifest.payload - SIM_MOTA_1K + missing * f.manifest.block_size();
  std::fill(f.host.bytes.begin() + leaf, f.host.bytes.begin() + leaf + 4, 0xFF);
  std::fill(f.host.bytes.begin() + payload,
            f.host.bytes.begin() + payload + f.manifest.block_size(), 0xFF);
  const auto partial = f.host.bytes;
  f.host.failure = HostStream::Timeout;
  f.host.fail_offset = 8;
  f.host.failures_left = 1;
  f.pull();
  assert(f.context.manager.fetchState() == OtaManager::PAUSED);
  assert(f.context.manager.resumeFetchAfterReconnect());
  f.finish_verification();
  assert(f.context.manager.fetchState() == OtaManager::FETCHING);
  assert(f.context.manager.blocksHave() == f.manifest.block_count - 1);
  assert(f.host.bytes == partial && f.host.begins == 0 && f.host.writes == 0);
  assert(!f.flight.requested_blocks.empty());
  for (uint32_t block : f.flight.requested_blocks) assert(block == missing);
  f.deliver_block(missing);
  assert(f.context.manager.fetchState() == OtaManager::COMPLETE);
  assert(f.host.bytes == complete && f.host.begins == 0);
}

static void established_capture_still_refuses_replacement_file() {
  Fixture f;
  f.pull();
  assert(f.context.manager.fetchState() == OtaManager::VERIFYING_STAGED);
  assert(f.context.manager.pauseFetchForDisconnect());
  f.host.bytes[0] ^= 1;
  assert(!f.context.manager.resumeFetchAfterReconnect());
  assert(f.context.manager.fetchState() == OtaManager::PAUSED);
  assert(f.host.begins == 0 && f.flight.manifests == 0);
}

int main() {
  for (auto failure : {HostStream::Timeout, HostStream::Truncated, HostStream::BadChecksum,
                       HostStream::Error, HostStream::ShortWrite, HostStream::WrongOp}) {
    for (bool retry_by_pull : {false, true}) {
      transient_resume_failure_preserves_bytes(failure, MS_OP_STAT, 0, retry_by_pull);
      for (uint32_t offset : {0u, 8u, SIM_MOTA_1K_LEN - 5u})
        transient_resume_failure_preserves_bytes(failure, MS_OP_SREAD, offset, retry_by_pull);
    }
  }
  for (unsigned corruption = 0; corruption < 8; ++corruption)
    for (bool after_io_failure : {false, true})
      known_absent_or_invalid_staging_starts_fresh(corruption, after_io_failure);
  explicit_validate_requests_fresh_initialization();
  transient_probe_failure_resumes_only_missing_blocks();
  established_capture_still_refuses_replacement_file();
  puts("PASS: folder resume I/O failures preserve staging and recover safely");
}
