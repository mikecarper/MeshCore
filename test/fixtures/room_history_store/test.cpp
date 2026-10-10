#include "filesystem.h"
#include <helpers/RoomHistoryStore.h>
#include "vectors.inc"

using Record = mesh::RoomHistoryRecord;
using State = mesh::RoomHistoryState;
static const char* P = mesh::ROOM_HISTORY_PATH;
static const char* T = mesh::ROOM_HISTORY_TEMP_PATH;
static const char* B = mesh::ROOM_HISTORY_BACKUP_PATH;
static const char* C = mesh::ROOM_HISTORY_CONFIG_PATH;
static const char* CT = mesh::ROOM_HISTORY_CONFIG_TEMP_PATH;
static const char* CB = mesh::ROOM_HISTORY_CONFIG_BACKUP_PATH;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
static const char* WRITE_MODE = "a";
#else
static const char* WRITE_MODE = "w";
#endif
static unsigned scenarios = 0;

static Record record(unsigned number) {
  Record result = {};
  result.author[0] = 1; result.author[31] = uint8_t(number);
  result.timestamp = 1000 + number;
  snprintf(result.text, sizeof(result.text), "post %u", number);
  return result;
}
static bool same(const Record& a, const Record& b) {
  return a.timestamp == b.timestamp && strcmp(a.text, b.text) == 0
      && memcmp(a.author, b.author, sizeof(a.author)) == 0;
}
static bool sameState(const State& a, const State& b) {
  return a.count == b.count && a.total_records == b.total_records
      && a.needs_repair == b.needs_repair && a.present == b.present;
}
static bool snapshot(MemoryFS& fs, State& state, const std::vector<Record>& records) {
  metadata_filesystem = &fs;
  return mesh::saveRoomHistorySnapshot(&fs, state, uint8_t(records.size()),
      [&](uint8_t index, Record& output) {
        if (index >= records.size()) return false;
        output = records[index]; return true;
      });
}
static bool append(MemoryFS& fs, State& state, const Record& input) {
  metadata_filesystem = &fs;
  return mesh::appendRoomHistory(&fs, state, input.author, input.timestamp, input.text);
}
static bool load(MemoryFS& fs, State& state, std::vector<Record>& records) {
  metadata_filesystem = &fs; records.clear();
  return mesh::loadRoomHistory(&fs, state,
      [&](uint8_t index, const Record& input) {
        assert(index == records.size()); records.push_back(input); return true;
      });
}
static bool config(MemoryFS& fs, bool enabled) {
  metadata_filesystem = &fs; return mesh::saveRoomHistoryConfig(&fs, enabled);
}
static bool loadConfig(MemoryFS& fs, bool& enabled) {
  metadata_filesystem = &fs; return mesh::loadRoomHistoryConfig(&fs, enabled);
}
static void expect(MemoryFS& fs, const std::vector<Record>& expected, bool repair = false) {
  State state; std::vector<Record> actual;
  assert(load(fs, state, actual) && actual.size() == expected.size());
  for (size_t index = 0; index < expected.size(); ++index) assert(same(actual[index], expected[index]));
  assert(state.count == expected.size() && state.needs_repair == repair);
}
static std::vector<uint8_t> rendered(const Record& input) {
  uint8_t image[mesh::ROOM_HISTORY_RECORD_SIZE];
  assert(mesh::room_history_detail::encodeRecord(input, image));
  return {image, image + sizeof(image)};
}
static void checkFailedSnapshot(MemoryFS& fs, State& state,
                                 const std::vector<Record>& old) {
  const State previous = state;
  assert(!snapshot(fs, state, {record(10), record(11)}));
  assert(sameState(state, previous)); fs.reset(); expect(fs, old);
  assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
}

int main() {
  const std::vector<Record> old = {record(1), record(2)};
  {
    MemoryFS fs; bool enabled = true; assert(loadConfig(fs, enabled) && !enabled);
    assert(config(fs, true) && fs.get(C) == CONFIG_ON);
    assert(loadConfig(fs, enabled) && enabled);
    assert(config(fs, false) && fs.get(C) == CONFIG_OFF);
    assert(loadConfig(fs, enabled) && !enabled); ++scenarios;
  }
  {
    MemoryFS fs; State state; std::vector<Record> rows;
    assert(load(fs, state, rows) && !state.present && rows.empty());
    assert(append(fs, state, record(1)) && state.present);
    const auto wire = fs.get(P);
    assert(wire.size() == HEADER.size() + RECORD_ONE.size());
    assert(std::equal(HEADER.begin(), HEADER.end(), wire.begin()));
    assert(std::equal(RECORD_ONE.begin(), RECORD_ONE.end(), wire.begin() + HEADER.size()));
    expect(fs, {record(1)}); ++scenarios;
  }
  {
    MemoryFS fs; State loaded; assert(snapshot(fs, loaded, old));
    const auto previous = fs.get(P); State uninitialized; fs.reset();
    assert(!append(fs, uninitialized, record(3)) && uninitialized.needs_repair);
    assert(fs.get(P) == previous && fs.write_opens == 0);
    fs.files.erase(P); fs.put(B, previous); uninitialized = State{}; fs.reset();
    assert(!append(fs, uninitialized, record(3)) && uninitialized.needs_repair);
    assert(fs.get(P) == previous && fs.write_opens == 0); ++scenarios;
  }
  {
    MemoryFS fs; State state; std::vector<Record> retained;
    assert(snapshot(fs, state, {}));
    for (unsigned number = 1; number <= 64; ++number) assert(append(fs, state, record(number)));
    assert(state.count == 32 && state.total_records == 64);
    assert(fs.get(P).size() == mesh::ROOM_HISTORY_MAX_FILE_SIZE);
    for (unsigned number = 33; number <= 64; ++number) retained.push_back(record(number));
    expect(fs, retained); const auto full = fs.get(P); fs.reset();
    assert(!append(fs, state, record(65)) && fs.trace.empty() && fs.get(P) == full);
    assert(snapshot(fs, state, retained) && state.total_records == 32);
    assert(fs.get(P).size() == HEADER.size() + 32 * RECORD_ONE.size());
    assert(append(fs, state, record(65))); retained.erase(retained.begin()); retained.push_back(record(65));
    expect(fs, retained); metadata_filesystem = &fs;
    assert(mesh::clearRoomHistory(&fs, state) && fs.get(P) == HEADER && state.count == 0);
    expect(fs, {}); assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); const auto previous = fs.get(P);
    Record maximum = record(3); memset(maximum.text, 'x', 151); maximum.text[151] = 0;
    assert(append(fs, state, maximum)); expect(fs, {record(1), record(2), maximum});
    fs.reset(); const auto accepted = fs.get(P);
    Record bad = maximum; memset(bad.text, 'x', sizeof(bad.text));
    assert(!append(fs, state, bad) && fs.trace.empty());
    bad = record(4); bad.timestamp = 0; assert(!append(fs, state, bad) && fs.trace.empty());
    bad = record(4); memset(bad.author, 0, sizeof(bad.author));
    assert(!append(fs, state, bad) && fs.trace.empty() && fs.get(P) == accepted);
    assert(previous.size() < accepted.size()); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); const auto previous = fs.get(P);
    metadata_filesystem = &fs;
    assert(!mesh::saveRoomHistorySnapshot(&fs, state, 33, mesh::room_history_detail::EmptyProvider{}));
    assert(fs.get(P) == previous);
    unsigned calls = 0;
    assert(!mesh::saveRoomHistorySnapshot(&fs, state, 2, [&](uint8_t, Record& out) {
      out = record(10); return ++calls == 1;
    })); expect(fs, old);
    calls = 0;
    assert(!mesh::saveRoomHistorySnapshot(&fs, state, 2, [&](uint8_t index, Record& out) {
      out = record(index + 10); return ++calls <= 2; // writer succeeds, verification provider fails
    })); expect(fs, old); ++scenarios;
  }
  for (const std::string& fault : {std::string("open:") + WRITE_MODE + ":" + T,
       std::string("write:") + T, std::string("flush:") + T,
       std::string("close:w:") + T, std::string("open:r:") + T,
       std::string("read:") + T, std::string("size:") + T,
       std::string("directory:") + T, std::string("rename:") + P + ":" + B,
       std::string("rename:") + T + ":" + P}) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset();
    fs.faults.insert(fault); checkFailedSnapshot(fs, state, old);
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    fs.capacity = fs.get(P).size() + 100; checkFailedSnapshot(fs, state, old);
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    fs.replacement_path = T; fs.replacement_on_close = fs.get(P);
    checkFailedSnapshot(fs, state, old); // valid CRC stale image is not intended contents
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    fs.faults.insert(std::string("rename:") + T + ":" + P);
    fs.faults.insert(std::string("rename:") + B + ":" + P);
    fs.faults.insert(std::string("remove:") + T);
    assert(!snapshot(fs, state, {record(10)})); assert(!fs.files.count(P) && fs.files.count(B));
    fs.reset(); expect(fs, old); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    fs.faults.insert(std::string("remove:") + B);
    assert(snapshot(fs, state, {record(10)}) && fs.files.count(B));
    const unsigned writes = fs.write_opens;
    assert(!snapshot(fs, state, old) && fs.write_opens == writes);
    assert(!append(fs, state, record(11)) && fs.write_opens == writes && state.needs_repair);
    fs.reset(); expect(fs, {record(10)}); assert(snapshot(fs, state, {record(10)}));
    assert(append(fs, state, record(11))); ++scenarios;
  }
  for (const std::string& fault : {std::string("open:a:") + P, std::string("write:") + P,
       std::string("flush:") + P, std::string("close:w:") + P,
       std::string("seek:") + P}) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset();
    fs.faults.insert(fault); assert(!append(fs, state, record(3)) && state.needs_repair);
    assert(state.count == 2 && state.total_records == 2);
    const unsigned writes = fs.write_opens;
    assert(!append(fs, state, record(3)) && fs.write_opens == writes);
    fs.reset(); assert(snapshot(fs, state, old) && append(fs, state, record(3)));
    expect(fs, {record(1), record(2), record(3)}); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset(); fs.short_write = true;
    assert(!append(fs, state, record(3)) && state.needs_repair); fs.reset(); expect(fs, old, true);
    assert(snapshot(fs, state, old) && append(fs, state, record(3))); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset();
    fs.capacity = fs.get(P).size() + 100;
    assert(!append(fs, state, record(3)) && state.needs_repair); fs.reset(); expect(fs, old, true); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset();
    // Three old scan reads; the fourth, post-write verification header, fails.
    fs.fail_read_at = 4;
    assert(!append(fs, state, record(3)) && state.needs_repair && state.count == 2);
    fs.reset(); expect(fs, {record(1), record(2), record(3)});
    // Runtime repair uses unchanged trusted RAM, discarding the unACKed append.
    assert(snapshot(fs, state, old) && append(fs, state, record(3)));
    expect(fs, {record(1), record(2), record(3)}); ++scenarios;
  }
  for (size_t tail_length = 1; tail_length < RECORD_ONE.size(); ++tail_length) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); auto bytes = fs.get(P);
    const auto tail = rendered(record(3)); bytes.insert(bytes.end(), tail.begin(), tail.begin() + tail_length);
    fs.put(P, bytes); expect(fs, old, true); std::vector<Record> rows;
    assert(load(fs, state, rows) && state.needs_repair);
    fs.reset(); assert(!append(fs, state, record(3)) && fs.trace.empty() && fs.get(P) == bytes);
    assert(snapshot(fs, state, old) && append(fs, state, record(3))); ++scenarios;
  }
  for (size_t position = 0; position < RECORD_ONE.size(); ++position) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); assert(append(fs, state, record(3)));
    auto bytes = fs.get(P); bytes[HEADER.size() + 2 * RECORD_ONE.size() + position] ^= 1;
    fs.put(P, bytes); expect(fs, old, true); assert(fs.get(P) == bytes); ++scenarios;
  }
  // Corruption before another complete accepted record is never a torn
  // append. Do not expose a shortened RAM ring that could replace the intact
  // later records on the next post, clear, or snapshot attempt.
  for (size_t damaged_record = 0; damaged_record < 2; ++damaged_record) {
    for (size_t position = 0; position < RECORD_ONE.size(); ++position) {
      MemoryFS fs; State state;
      assert(snapshot(fs, state, {record(1), record(2), record(3)}));
      auto bytes = fs.get(P);
      bytes[HEADER.size() + damaged_record * RECORD_ONE.size() + position] ^= 1;
      fs.put(P, bytes); fs.reset();
      const State previous = state;
      std::vector<Record> rows;
      assert(!load(fs, state, rows) && sameState(state, previous) && rows.empty());
      assert(fs.get(P) == bytes && fs.write_opens == 0);
      // Both a truncated boot destination and the old trusted live ring must
      // fail closed; administrative clear cannot discard the evidence either.
      assert(!snapshot(fs, state, {record(1)}));
      assert(!snapshot(fs, state, {record(1), record(2), record(3)}));
      assert(!mesh::clearRoomHistory(&fs, state));
      assert(fs.get(P) == bytes && fs.write_opens == 0 && sameState(state, previous));
      assert(!append(fs, state, record(4)) && state.needs_repair);
      assert(fs.get(P) == bytes && fs.write_opens == 0);
      assert(!fs.files.count(T) && !fs.files.count(B)); ++scenarios;
    }
  }
  for (int malformed = 0; malformed < 4; ++malformed) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    auto bytes = fs.get(P); auto tail = rendered(record(3));
    if (malformed == 0) std::fill(tail.begin(), tail.begin() + 32, 0);
    if (malformed == 1) std::fill(tail.begin() + 32, tail.begin() + 36, 0);
    if (malformed == 2) std::fill(tail.begin() + 36, tail.begin() + 188, 'x');
    if (malformed == 3) tail[150] = 'x';
    mesh::room_history_detail::put32(tail.data() + 188,
        mesh::room_history_detail::crcBytes(tail.data(), 188));
    bytes.insert(bytes.end(), tail.begin(), tail.end()); fs.put(P, bytes);
    expect(fs, old, true); assert(fs.get(P) == bytes); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); fs.reset(); fs.fail_read_at = 2;
    const State before = state; std::vector<Record> rows;
    assert(!load(fs, state, rows) && sameState(state, before) && rows.empty());
    fs.reset(); fs.fail_read_at = 6;
    assert(!load(fs, state, rows) && sameState(state, before) && rows.size() == 1);
    rows.clear(); fs.reset(); expect(fs, old); ++scenarios;
  }
  std::vector<std::vector<uint8_t>> invalid_headers = {{}, {1}, std::vector<uint8_t>(11), HEADER,
      std::vector<uint8_t>(mesh::ROOM_HISTORY_MAX_FILE_SIZE + 1)};
  invalid_headers[3][3] = 2;
  mesh::room_history_detail::put32(invalid_headers[3].data() + 8,
      mesh::room_history_detail::crcBytes(invalid_headers[3].data(), 8));
  for (const auto& bytes : invalid_headers) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); const auto good = fs.get(P);
    fs.put(P, bytes); fs.put(B, good); fs.put(T, good);
    State before = state; std::vector<Record> rows;
    assert(!load(fs, state, rows) && sameState(state, before));
    assert(!snapshot(fs, state, {}) && fs.get(P) == bytes && fs.get(B) == good && fs.get(T) == good);
    MemoryFS backup; backup.put(B, bytes); backup.put(T, good);
    assert(!load(backup, state, rows) && backup.get(B) == bytes && backup.get(T) == good); ++scenarios;
  }
  for (size_t position = 0; position < HEADER.size(); ++position) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); auto bytes = fs.get(P);
    bytes[position] ^= 1; fs.put(P, bytes); std::vector<Record> rows;
    assert(!load(fs, state, rows) && !snapshot(fs, state, {}));
    assert(fs.get(P) == bytes); ++scenarios;
  }
  for (size_t position : {size_t(4), size_t(6), size_t(7)}) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); auto bytes = fs.get(P);
    ++bytes[position]; mesh::room_history_detail::put32(bytes.data() + 8,
        mesh::room_history_detail::crcBytes(bytes.data(), 8)); fs.put(P, bytes);
    std::vector<Record> rows; assert(!load(fs, state, rows) && !snapshot(fs, state, {}));
    assert(fs.get(P) == bytes); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old)); const auto bytes = fs.get(P);
    fs.files.erase(P); fs.put(B, bytes); fs.put(T, HEADER); expect(fs, old);
    assert(fs.get(P) == bytes && !fs.files.count(T) && !fs.files.count(B));
    MemoryFS temp; temp.put(T, bytes); expect(temp, {}); assert(!temp.files.count(P)); ++scenarios;
  }
  {
    MemoryFS fs; State state; assert(snapshot(fs, state, old));
    fs.directories.insert(P); std::vector<Record> rows;
    assert(!load(fs, state, rows) && !snapshot(fs, state, {}) && fs.files.count(P)); ++scenarios;
  }
  for (size_t position = 0; position < CONFIG_ON.size(); ++position) {
    MemoryFS fs; auto image = CONFIG_ON; image[position] ^= 1;
    fs.put(C, image); fs.put(CB, CONFIG_OFF); fs.put(CT, CONFIG_OFF);
    bool enabled = false;
    assert(!loadConfig(fs, enabled) && !enabled && !config(fs, false));
    assert(fs.get(C) == image && fs.get(CB) == CONFIG_OFF && fs.get(CT) == CONFIG_OFF); ++scenarios;
  }
  {
    MemoryFS fs; auto image = CONFIG_ON; image[3] = 2;
    mesh::room_history_detail::put32(image.data() + 12, mesh::room_history_detail::crcBytes(image.data(), 12));
    fs.put(C, image); bool enabled = true;
    assert(!loadConfig(fs, enabled) && enabled && !config(fs, false) && fs.get(C) == image);
    MemoryFS backup; backup.put(CB, CONFIG_ON); backup.put(CT, CONFIG_OFF);
    assert(loadConfig(backup, enabled) && enabled && backup.get(C) == CONFIG_ON);
    MemoryFS temp; temp.put(CT, CONFIG_ON); assert(loadConfig(temp, enabled) && !enabled && !temp.files.count(C)); ++scenarios;
  }
  for (size_t position : {size_t(4), size_t(5)}) {
    MemoryFS fs; auto image = CONFIG_ON; image[position] = 2;
    mesh::room_history_detail::put32(image.data() + 12,
        mesh::room_history_detail::crcBytes(image.data(), 12)); fs.put(C, image);
    bool enabled = false; assert(!loadConfig(fs, enabled) && !config(fs, false));
    assert(fs.get(C) == image); ++scenarios;
  }
  for (const std::string& fault : {std::string("open:") + WRITE_MODE + ":" + CT,
       std::string("write:") + CT, std::string("flush:") + CT,
       std::string("close:w:") + CT, std::string("open:r:") + CT,
       std::string("read:") + CT, std::string("size:") + CT,
       std::string("directory:") + CT, std::string("rename:") + C + ":" + CB,
       std::string("rename:") + CT + ":" + C}) {
    MemoryFS fs; assert(config(fs, false)); fs.reset(); fs.faults.insert(fault);
    assert(!config(fs, true)); fs.reset(); bool enabled = true;
    assert(loadConfig(fs, enabled) && !enabled); ++scenarios;
  }
#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  for (const char* path : {P, C}) {
    MemoryFS fs; State state; assert(snapshot(fs, state, old) && config(fs, false)); fs.reset();
    fs.faults.insert(std::string("stat:") + path); bool enabled = false; std::vector<Record> rows;
    if (path == P) assert(!load(fs, state, rows) && !snapshot(fs, state, {}));
    else assert(!loadConfig(fs, enabled) && !config(fs, true));
    assert(fs.write_opens == 0); ++scenarios;
  }
#endif
  // Simulate interruption before/after every filesystem operation, including
  // verification and publication. Only temp->primary publication changes the
  // selected snapshot/config; journal appends may recover a complete new row.
  for (int action = 0; action < 3; ++action) {
    MemoryFS baseline; State state; assert(snapshot(baseline, state, old) && config(baseline, false));
    baseline.reset();
    if (action == 0) assert(snapshot(baseline, state, {record(10), record(11)}));
    if (action == 1) assert(append(baseline, state, record(3)));
    if (action == 2) assert(config(baseline, true));
    const size_t boundaries = baseline.boundary;
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs; State current; assert(snapshot(fs, current, old) && config(fs, false));
      fs.reset(); fs.cut_at = cut;
      try {
        if (action == 0) snapshot(fs, current, {record(10), record(11)});
        if (action == 1) append(fs, current, record(3));
        if (action == 2) config(fs, true);
      } catch (const PowerCut&) {}
      const bool new_snapshot = fs.files.count(P) && fs.get(P).size() > HEADER.size()
          && fs.get(P)[HEADER.size() + 31] == 10;
      const bool appended = action == 1 && fs.files.count(P)
          && fs.get(P).size() == HEADER.size() + 3 * RECORD_ONE.size();
      const bool new_config = fs.files.count(C) && fs.get(C) == CONFIG_ON;
      fs.reset();
      if (action == 0) expect(fs, new_snapshot ? std::vector<Record>{record(10), record(11)} : old);
      if (action == 1) expect(fs, appended ? std::vector<Record>{record(1), record(2), record(3)} : old);
      if (action == 2) { bool enabled; assert(loadConfig(fs, enabled) && enabled == new_config); expect(fs, old); }
      assert(!fs.files.count(T) && !fs.files.count(B) && !fs.files.count(CT) && !fs.files.count(CB)); ++scenarios;
    }
  }
  printf("PASS: %u bounded room history fault and recovery scenarios\n", scenarios);
}
