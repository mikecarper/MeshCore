// Include the production implementation so its static validators and the full
// ClientACL load/admission/publication path execute against the SPIFFS model.
#include "../../../src/helpers/ClientACL.cpp"
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <utility>

#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); std::exit(1); \
} } while (0)

static const uint8_t KEY[PUB_KEY_SIZE] = {0x12, 0x57, 0xae, 0xe5};
static const char* PRIMARY = mesh::CLIENT_LOGIN_REPLAY_PRIMARY_PATH;
static const char* TEMP = mesh::CLIENT_LOGIN_REPLAY_TEMP_PATH;
static mesh::LocalIdentity SELF;
static const uint8_t SECOND_KEY[PUB_KEY_SIZE] = {0x12, 0x57, 0xae, 0xe5, 0x02};
static const uint8_t ARCHIVED_KEY[PUB_KEY_SIZE] = {0x77, 0x03};

static std::vector<uint8_t> replay_records(
    std::initializer_list<std::pair<const uint8_t*, uint32_t>> records) {
  std::vector<uint8_t> image;
  for (const auto& record : records) {
    image.insert(image.end(), record.first, record.first + PUB_KEY_SIZE);
    const auto* value = reinterpret_cast<const uint8_t*>(&record.second);
    image.insert(image.end(), value, value + sizeof(record.second));
  }
  const uint32_t crc = mesh::updateClientLoginReplayCRC(
      0xffffffffu, image.data(), image.size()) ^ 0xffffffffu;
  image.insert(image.end(), mesh::CLIENT_LOGIN_REPLAY_MAGIC,
               mesh::CLIENT_LOGIN_REPLAY_MAGIC + 4);
  const auto* value = reinterpret_cast<const uint8_t*>(&crc);
  image.insert(image.end(), value, value + sizeof(crc));
  return image;
}

static void check_empty_result(const ClientLoginReplayClampResult& result) {
  CHECK(result.stored_matched == 0 && result.stored_changed == 0
        && result.live_matched == 0 && result.live_changed == 0);
}

static std::vector<uint8_t> replay(uint32_t ceiling) {
  std::vector<uint8_t> image(KEY, KEY + PUB_KEY_SIZE);
  const auto* bytes = reinterpret_cast<const uint8_t*>(&ceiling);
  image.insert(image.end(), bytes, bytes + sizeof(ceiling));
  const uint32_t crc = mesh::updateClientLoginReplayCRC(0xffffffffu, image.data(), image.size()) ^ 0xffffffffu;
  image.insert(image.end(), mesh::CLIENT_LOGIN_REPLAY_MAGIC, mesh::CLIENT_LOGIN_REPLAY_MAGIC + 4);
  bytes = reinterpret_cast<const uint8_t*>(&crc);
  image.insert(image.end(), bytes, bytes + sizeof(crc));
  return image;
}

static void missing_read_is_not_empty_file() {
  FakeFilesystem fs;
  auto raw = fs.open(PRIMARY);
  CHECK(raw && raw.isDirectory() && raw.size() == 0);
  raw.close();
  auto checked = mesh::openFileRead(&fs, PRIMARY);
  CHECK(!checked && fs.missing_read_opens == 1);
  CHECK(!validateContactsFileIntegrity(&fs, "/s_contacts"));
  CHECK(!validateLoginReplayFileIntegrity(&fs, PRIMARY));
  CHECK(!mesh::openFileRead(static_cast<FakeFilesystem*>(nullptr), PRIMARY));
  CHECK(!mesh::openFileRead(&fs, nullptr));
}

static void first_admin_and_retries() {
  FakeFilesystem fs;
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(acl.authorizeLoginTimestamp(KEY, 100, 0, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(160));
  CHECK(fs.files[PRIMARY].size() == 44 && !fs.exists(TEMP));
  CHECK(fs.missing_read_opens == 0);
  const auto writes = fs.bytes_written;
  CHECK(acl.authorizeLoginTimestamp(KEY, 101, 100, PERM_ACL_ADMIN));
  CHECK(fs.bytes_written == writes);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 101, 101, PERM_ACL_ADMIN));
  CHECK(!acl.authorizeLoginTimestamp(KEY, 0, 101, PERM_ACL_ADMIN));
  CHECK(acl.authorizeLoginTimestamp(KEY, 161, 101, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(221));
}

static void reboot_preserves_ceiling() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(160);
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 160, 0, PERM_ACL_ADMIN));
  CHECK(acl.authorizeLoginTimestamp(KEY, 161, 0, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(221));
}

static void corrupt_state_is_preserved() {
  for (size_t length : {size_t(0), size_t(1), size_t(7), size_t(9), size_t(43)}) {
    FakeFilesystem fs;
    fs.files[PRIMARY] = std::vector<uint8_t>(length, 0x99);
    const auto original = fs.files[PRIMARY];
    ClientACL acl;
    acl.load(&fs, SELF);
    CHECK(!acl.authorizeLoginTimestamp(KEY, 1000, 0, PERM_ACL_ADMIN));
    CHECK(fs.files[PRIMARY] == original && fs.bytes_written == 0);
  }
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(160);
  fs.files[PRIMARY].back() ^= 1;
  const auto original = fs.files[PRIMARY];
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 1000, 0, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == original);
}

static void write_failures_keep_boundary() {
  for (int failure = 0; failure < 5; ++failure) {
    FakeFilesystem fs;
    fs.files[PRIMARY] = replay(160);
    ClientACL acl;
    acl.load(&fs, SELF);
    if (failure == 0) fs.write_budget = 12;
    if (failure == 1) fs.fail_open_write = TEMP;
    if (failure == 2) fs.fail_rename_from = TEMP;
    if (failure == 3) fs.corrupt_on_close = TEMP;
    if (failure == 4) fs.directories_on_write.insert(TEMP);
    CHECK(!acl.authorizeLoginTimestamp(KEY, 200, 0, PERM_ACL_ADMIN));
    CHECK(fs.files[PRIMARY] == replay(160));
    CHECK(!fs.exists(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH));
  }
}

static void first_write_failure_is_retriable() {
  FakeFilesystem fs;
  ClientACL acl;
  acl.load(&fs, SELF);
  fs.write_budget = 5;
  CHECK(!acl.authorizeLoginTimestamp(KEY, 100, 0, PERM_ACL_ADMIN));
  CHECK(!fs.exists(PRIMARY) && !fs.exists(TEMP));
  fs.write_budget = std::numeric_limits<size_t>::max();
  CHECK(acl.authorizeLoginTimestamp(KEY, 101, 0, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(161));
}

static void directory_reads_are_rejected() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(160);
  fs.directories_on_read.insert(PRIMARY);
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 1000, 0, PERM_ACL_ADMIN));
  CHECK(fs.directory_closes > 0 && fs.files[PRIMARY] == replay(160));
  fs.files["/s_contacts"] = {};
  fs.directories_on_read.insert("/s_contacts");
  CHECK(!validateContactsFileIntegrity(&fs, "/s_contacts"));
}

static void regular_empty_contacts_remain_valid() {
  FakeFilesystem fs;
  fs.files["/s_contacts"] = {};
  CHECK(validateContactsFileIntegrity(&fs, "/s_contacts"));
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(acl.getNumClients() == 0);
  CHECK(acl.authorizeLoginTimestamp(KEY, 100, 0, PERM_ACL_ADMIN));
}

static void contacts_save_load_uses_regular_files() {
  FakeFilesystem fs;
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(acl.applyPermissions(SELF, KEY, PUB_KEY_SIZE, PERM_ACL_ADMIN));
  CHECK(acl.save(&fs));
  CHECK(fs.files["/s_contacts"].size() == 209);
  ClientACL reloaded;
  reloaded.load(&fs, SELF);
  CHECK(reloaded.getNumClients() == 1);
  CHECK(reloaded.getClient(KEY, PUB_KEY_SIZE)->isAdmin());
  CHECK(reloaded.authorizeLoginTimestamp(KEY, 100, 0, PERM_ACL_ADMIN));
}

static void replay_source_reopen_failure_is_not_missing() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(160);
  fs.truncate_path = PRIMARY;
  fs.truncate_on_read_open = 2; // validation succeeds, source reopened truncated
  CHECK(!writeClientLoginReplayCeiling(&fs, KEY, 260,
      mesh::ClientLoginReplayReservationAction::UpdateExisting));
  CHECK(fs.bytes_written == 0 && !fs.exists(TEMP));
}

static void unreadable_primary_is_not_first_login() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(160);
  fs.unreadable.insert(PRIMARY);
  ClientACL acl;
  acl.load(&fs, SELF);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 1000, 0, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(160) && fs.bytes_written == 0);
}

static void null_load_fails_closed() {
  ClientACL acl;
  acl.load(nullptr, SELF);
  CHECK(!acl.authorizeLoginTimestamp(KEY, 100, 0, PERM_ACL_ADMIN));
}

static void clamp_exact_key_preserves_other_state() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay_records({{KEY, 900}, {SECOND_KEY, 800}});
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 1000;
  client->last_activity = 777;
  client->out_path_len = 1;
  client->out_path[0] = 0x77;
  client->alt_path_len = 1;
  client->alt_path[0] = 0x12;
  client->shared_secret[3] = 0x88;
  client->extra.room.sync_since = 600;
  ClientInfo* other = acl.putClient(mesh::Identity(SECOND_KEY), PERM_ACL_REGION_MGR);
  other->last_timestamp = 800;
  CHECK(acl.save(&fs));
  const auto saved_contacts = fs.files["/s_contacts"];
  ClientInfo expected = *client;
  expected.last_timestamp = 500;
  const ClientInfo expected_other = *other;
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
  CHECK(result.stored_matched == 1 && result.stored_changed == 1);
  CHECK(result.live_matched == 1 && result.live_changed == 1);
  CHECK(fs.files[PRIMARY] == replay_records({{KEY, 500}, {SECOND_KEY, 800}}));
  CHECK(fs.files["/s_contacts"] == saved_contacts);
  CHECK(std::memcmp(client, &expected, sizeof(expected)) == 0);
  CHECK(std::memcmp(other, &expected_other, sizeof(expected_other)) == 0);
}

static void clamp_all_keeps_duplicates_and_tombstones() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay_records(
      {{KEY, 100}, {ARCHIVED_KEY, 900}, {KEY, 700}, {SECOND_KEY, 500}});
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 1000;
  ClientInfo* other = acl.putClient(mesh::Identity(SECOND_KEY), PERM_ACL_READ_ONLY);
  other->last_timestamp = 400;
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(nullptr, 500, result));
  CHECK(result.stored_matched == 4 && result.stored_changed == 2);
  CHECK(result.live_matched == 2 && result.live_changed == 1);
  CHECK(fs.files[PRIMARY] == replay_records(
      {{KEY, 100}, {ARCHIVED_KEY, 500}, {KEY, 500}, {SECOND_KEY, 500}}));
  CHECK(acl.getNumClients() == 2 && acl.getClient(ARCHIVED_KEY, PUB_KEY_SIZE) == nullptr);
  CHECK(client->last_timestamp == 500 && other->last_timestamp == 400);
  CHECK(validateLoginReplayFileIntegrity(&fs, PRIMARY));
}

static void clamp_missing_key_or_store_never_creates() {
  FakeFilesystem fs;
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
  check_empty_result(result);
  CHECK(fs.files.empty() && fs.bytes_written == 0 && acl.getNumClients() == 0);
  fs.files[PRIMARY] = replay(900);
  const auto before = fs.files;
  CHECK(acl.clampLoginReplayTimestamps(SECOND_KEY, 500, result));
  check_empty_result(result);
  CHECK(fs.files == before && fs.bytes_written == 0 && acl.getNumClients() == 0);
  fs.files[PRIMARY] = replay_records({});
  CHECK(acl.clampLoginReplayTimestamps(nullptr, 500, result));
  check_empty_result(result);
  CHECK(fs.files[PRIMARY] == replay_records({}) && fs.bytes_written == 0);
}

static void clamp_noop_never_writes_or_raises() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay_records({{KEY, 100}, {SECOND_KEY, 500}});
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 100;
  const auto before = fs.files;
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(nullptr, 500, result));
  CHECK(result.stored_matched == 2 && result.stored_changed == 0);
  CHECK(result.live_matched == 1 && result.live_changed == 0);
  CHECK(acl.clampLoginReplayTimestamps(nullptr, UINT32_MAX, result));
  CHECK(result.stored_changed == 0 && result.live_changed == 0);
  CHECK(fs.files == before && fs.bytes_written == 0 && client->last_timestamp == 100);
}

static void clamp_live_only_does_not_create_or_raise_store() {
  for (bool has_store : {false, true}) {
    FakeFilesystem fs;
    if (has_store) fs.files[PRIMARY] = replay(100);
    ClientACL acl;
    acl.load(&fs, SELF);
    ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
    client->last_timestamp = 900;
    const auto before = fs.files;
    ClientLoginReplayClampResult result = {};
    CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
    CHECK(result.stored_matched == (has_store ? 1 : 0) && result.stored_changed == 0);
    CHECK(result.live_matched == 1 && result.live_changed == 1);
    CHECK(fs.files == before && fs.bytes_written == 0 && client->last_timestamp == 500);
  }
}

static void clamp_stored_only_does_not_raise_live() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(900);
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 100;
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
  CHECK(result.stored_changed == 1 && result.live_changed == 0);
  CHECK(fs.files[PRIMARY] == replay(500) && client->last_timestamp == 100);
  const auto writes = fs.bytes_written;
  CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
  CHECK(result.stored_changed == 0 && result.live_changed == 0);
  CHECK(fs.bytes_written == writes);
}

static void clamp_invalid_clock_or_unavailable_fails_closed() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(900);
  ClientACL acl;
  ClientLoginReplayClampResult result = {9, 9, 9, 9};
  CHECK(!acl.clampLoginReplayTimestamps(KEY, 500, result));
  check_empty_result(result);
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 900;
  CHECK(!acl.clampLoginReplayTimestamps(KEY, 0, result));
  check_empty_result(result);
  CHECK(fs.files[PRIMARY] == replay(900) && fs.bytes_written == 0);
  CHECK(client->last_timestamp == 900);
}

static void clamp_write_failures_leave_live_and_store_unchanged() {
  for (int failure = 0; failure < 6; ++failure) {
    FakeFilesystem fs;
    fs.files[PRIMARY] = replay(900);
    ClientACL acl;
    acl.load(&fs, SELF);
    ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
    client->last_timestamp = 1000;
    if (failure == 0) fs.write_budget = 12;
    if (failure == 1) fs.fail_open_write = TEMP;
    if (failure == 2) fs.fail_rename_from = TEMP;
    if (failure == 3) fs.corrupt_on_close = TEMP;
    if (failure == 4) fs.directories_on_write.insert(TEMP);
    if (failure == 5) fs.fail_rename_from = PRIMARY;
    ClientLoginReplayClampResult result = {9, 9, 9, 9};
    CHECK(!acl.clampLoginReplayTimestamps(KEY, 500, result));
    check_empty_result(result);
    CHECK(fs.files[PRIMARY] == replay(900) && client->last_timestamp == 1000);
  }
}

static void clamp_corrupt_or_unreadable_source_never_repairs() {
  for (int failure = 0; failure < 5; ++failure) {
    FakeFilesystem fs;
    fs.files[PRIMARY] = replay(900);
    ClientACL acl;
    acl.load(&fs, SELF);
    ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
    client->last_timestamp = 1000;
    if (failure == 0) fs.files[PRIMARY].back() ^= 1;
    if (failure == 1) fs.files[PRIMARY].resize(1);
    if (failure == 2) fs.unreadable.insert(PRIMARY);
    if (failure == 3) fs.directories_on_read.insert(PRIMARY);
    if (failure == 4) {
      fs.files[mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH] = replay(900);
      fs.files.erase(PRIMARY);
    }
    const auto before = fs.files;
    ClientLoginReplayClampResult result = {};
    CHECK(!acl.clampLoginReplayTimestamps(KEY, 500, result));
    check_empty_result(result);
    CHECK(fs.files == before && fs.bytes_written == 0 && client->last_timestamp == 1000);
    CHECK(!acl.authorizeLoginTimestamp(SECOND_KEY, 2000, 0, PERM_ACL_ADMIN));
  }
}

static void clamp_reopen_and_rollback_failures_block_new_admission() {
  for (bool rollback_failure : {false, true}) {
    FakeFilesystem fs;
    fs.files[PRIMARY] = replay(900);
    ClientACL acl;
    acl.load(&fs, SELF);
    ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
    client->last_timestamp = 1000;
    if (rollback_failure) {
      fs.fail_rename_from_paths.insert(TEMP);
      fs.fail_rename_from_paths.insert(mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH);
    } else {
      fs.truncate_path = PRIMARY;
      fs.truncate_on_read_open = fs.read_open_count[PRIMARY] + 2;
    }
    ClientLoginReplayClampResult result = {};
    CHECK(!acl.clampLoginReplayTimestamps(KEY, 500, result));
    check_empty_result(result);
    CHECK(client->last_timestamp == 1000);
    CHECK(!acl.authorizeLoginTimestamp(SECOND_KEY, 2000, 0, PERM_ACL_ADMIN));
    if (rollback_failure) {
      CHECK(!fs.exists(PRIMARY));
      CHECK(fs.files[mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH] == replay(900));
    }
  }
}

static void clamp_reboot_keeps_lowered_boundary() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(900);
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 1000;
  CHECK(acl.save(&fs));
  ClientLoginReplayClampResult result = {};
  CHECK(acl.clampLoginReplayTimestamps(KEY, 500, result));
  ClientACL rebooted;
  rebooted.load(&fs, SELF);
  CHECK(rebooted.getClient(KEY, PUB_KEY_SIZE)->last_timestamp == 500);
  CHECK(!rebooted.authorizeLoginTimestamp(KEY, 500, 500, PERM_ACL_ADMIN));
  CHECK(rebooted.authorizeLoginTimestamp(KEY, 501, 500, PERM_ACL_ADMIN));
  CHECK(fs.files[PRIMARY] == replay(561));
}

static void clamp_backup_cleanup_failure_does_not_mutate_live() {
  FakeFilesystem fs;
  fs.files[PRIMARY] = replay(900);
  ClientACL acl;
  acl.load(&fs, SELF);
  ClientInfo* client = acl.putClient(mesh::Identity(KEY), PERM_ACL_ADMIN);
  client->last_timestamp = 1000;
  fs.files[mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH] = replay(950);
  fs.fail_remove = mesh::CLIENT_LOGIN_REPLAY_BACKUP_PATH;
  const auto before = fs.files;
  ClientLoginReplayClampResult result = {};
  CHECK(!acl.clampLoginReplayTimestamps(KEY, 500, result));
  check_empty_result(result);
  CHECK(fs.files == before && fs.bytes_written == 0 && client->last_timestamp == 1000);
}

int main() {
  const struct { const char* name; void (*run)(); } tests[] = {
    {"missing read differs from empty file", missing_read_is_not_empty_file},
    {"first admin and monotonic retries", first_admin_and_retries},
    {"reboot preserves ceiling", reboot_preserves_ceiling},
    {"corrupt state retained", corrupt_state_is_preserved},
    {"write failures retain boundary", write_failures_keep_boundary},
    {"failed first write can retry", first_write_failure_is_retriable},
    {"truthy directories rejected", directory_reads_are_rejected},
    {"regular empty legacy ACL accepted", regular_empty_contacts_remain_valid},
    {"contacts save and reload", contacts_save_load_uses_regular_files},
    {"reopen truncation fails closed", replay_source_reopen_failure_is_not_missing},
    {"unreadable history preserved", unreadable_primary_is_not_first_login},
    {"uninitialized storage fails closed", null_load_fails_closed},
    {"clamp exact key preserves all other state", clamp_exact_key_preserves_other_state},
    {"clamp all preserves duplicate and historical records", clamp_all_keeps_duplicates_and_tombstones},
    {"clamp missing selection creates nothing", clamp_missing_key_or_store_never_creates},
    {"clamp noop never writes or raises", clamp_noop_never_writes_or_raises},
    {"clamp live only leaves storage alone", clamp_live_only_does_not_create_or_raise_store},
    {"clamp stored only never raises live", clamp_stored_only_does_not_raise_live},
    {"clamp zero clock and unavailable fail closed", clamp_invalid_clock_or_unavailable_fails_closed},
    {"clamp write errors preserve live and disk", clamp_write_failures_leave_live_and_store_unchanged},
    {"clamp corrupt source is not a repair", clamp_corrupt_or_unreadable_source_never_repairs},
    {"clamp reopen and rollback failures fail closed", clamp_reopen_and_rollback_failures_block_new_admission},
    {"clamp survives reboot", clamp_reboot_keeps_lowered_boundary},
    {"clamp backup cleanup failure preserves state", clamp_backup_cleanup_failure_does_not_mutate_live},
  };
  for (const auto& test : tests) {
    test.run();
    std::printf("PASS: %s\n", test.name);
  }
  std::puts("24 ClientACL SPIFFS checks passed");
}
