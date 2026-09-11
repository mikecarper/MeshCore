#include <helpers/ClientACL.cpp>
#include <helpers/ClientACLCLI.h>
#include <helpers/CLICommandUtils.h>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); std::exit(1); \
} } while (0)

class Stream {
public:
  std::string output;
  void print(char c) { output += c; }
  void println(const char* text) { output += text; output += '\n'; }
  void printf(const char* format, ...) {
    char row[256];
    va_list args;
    va_start(args, format);
    const int len = vsnprintf(row, sizeof(row), format, args);
    va_end(args);
    CHECK(len >= 0 && size_t(len) < sizeof(row));
    output += row;
  }
};

namespace mesh {
static Stream console;
Stream& usbConsolePort() { return console; }
}

#include "production.h"

static ClientInfo* add(ClientACL& acl, unsigned index, uint8_t permissions) {
  uint8_t key[PUB_KEY_SIZE];
  for (size_t i = 0; i < sizeof(key); ++i) key[i] = uint8_t(i * 7);
  // Deliberately share the first 12 hex characters between every entry.
  key[30] = uint8_t(index >> 8);
  key[31] = uint8_t(index);
  auto* client = acl.putClient(mesh::Identity(key), permissions);
  CHECK(client != nullptr);
  memset(client->shared_secret, 0xFA, sizeof(client->shared_secret));
  client->last_activity = index + 123;
  client->last_timestamp = index + 456;
  return client;
}

static std::string row(const ClientInfo* client) {
  char output[68];
  snprintf(output, sizeof(output), "%02X ", unsigned(client->permissions));
  for (size_t i = 0; i < PUB_KEY_SIZE; ++i) {
    snprintf(output + 3 + 2 * i, sizeof(output) - 3 - 2 * i,
             "%02X", unsigned(client->id.pub_key[i]));
  }
  return output;
}

static std::string query(const ClientACL& acl, const char* command, bool local = false) {
  char reply[160];
  memset(reply, '#', sizeof(reply));
  CHECK(mesh::cli::handleACLGet(acl, command, reply, 157, local));
  CHECK(reply[157] == '#' && reply[158] == '#' && reply[159] == '#');
  CHECK(strlen(reply) < 157);
  return reply;
}

static void empty_and_single_entry() {
  ClientACL acl;
  CHECK(query(acl, "get acl") == "ACL: empty");
  CHECK(query(acl, "get acl 1") == "ACL: empty");
  CHECK(query(acl, "get acl 2") == "Err - ACL page range: 1-1");
  add(acl, 0, 0);
  CHECK(query(acl, "get acl") == "ACL: empty");
  const auto* client = add(acl, 1, 0xA3);
  CHECK(query(acl, "get acl") == "ACL 1/1\n" + row(client));
}

static void skips_inactive_and_preserves_full_keys() {
  ClientACL acl;
  std::vector<std::string> active;
  for (unsigned i = 0; i < 9; ++i) {
    auto* client = add(acl, i, i % 2 == 0 ? uint8_t(i + 1) : 0);
    if (client->permissions) active.push_back(row(client));
  }
  CHECK(query(acl, "get acl") == "ACL 1/3\n" + active[0] + "\n" + active[1]);
  CHECK(query(acl, "get acl 2") == "ACL 2/3\n" + active[2] + "\n" + active[3]);
  CHECK(query(acl, "get acl 3") == "ACL 3/3\n" + active[4]);
  CHECK(query(acl, "get acl 4") == "Err - ACL page range: 1-3");
}

static void full_table_pages() {
  ClientACL acl;
  for (unsigned i = 0; i < MAX_CLIENTS; ++i) add(acl, i, PERM_ACL_ADMIN);
  const unsigned pages = (MAX_CLIENTS + 1) / 2;
  for (unsigned page = 1; page <= pages; ++page) {
    char command[32], header[32];
    snprintf(command, sizeof(command), "get acl %u", page);
    snprintf(header, sizeof(header), "ACL %u/%u", page, pages);
    std::string expected(header);
    for (unsigned i = (page - 1) * 2; i < page * 2 && i < MAX_CLIENTS; ++i) {
      expected += "\n" + row(acl.getClientByIdx(i));
    }
    CHECK(query(acl, command) == expected);
  }
}

static void rejects_bad_pages() {
  ClientACL acl;
  add(acl, 0, 3);
  for (const char* command : {
      "get acl 0", "get acl -1", "get acl +1", "get acl 1x", "get acl 1 2",
      "get acl 1.0", "get acl page 1", "get acl 65536", "get acl 4294967296",
      "get acl 9999999999999999999999999999999999", "get acl 1\nsetperm key 3"}) {
    CHECK(query(acl, command) == "Err - usage: get acl [page]");
  }
  CHECK(query(acl, "get acl 65535") == "Err - ACL page range: 1-1");
  const auto expected = query(acl, "get acl");
  for (const char* command : {"get acl ", "get acl\r\n", "get acl\t01 \r\n", "get acl 00000000000001"}) {
    CHECK(query(acl, command) == expected);
  }
}

static void matching_and_local_fallback() {
  ClientACL acl;
  char reply[157] = "unchanged";
  for (const char* command : {"", "get", "get ac", "get aclx", "get acl.other", "set acl"}) {
    CHECK(!mesh::cli::handleACLGet(acl, command, reply, sizeof(reply), false));
    CHECK(strcmp(reply, "unchanged") == 0);
  }
  CHECK(!mesh::cli::handleACLGet(acl, "get acl", reply, sizeof(reply), true));
  CHECK(strcmp(reply, "unchanged") == 0);
  CHECK(query(acl, "get acl 1", true) == "ACL: empty");
}

static void bounds_never_truncate_keys() {
  ClientACL acl;
  add(acl, 0, 3);
  add(acl, 1, 3);
  const auto expected = query(acl, "get acl");
  for (size_t capacity = 1; capacity <= 160; ++capacity) {
    char reply[161];
    memset(reply, '#', sizeof(reply));
    CHECK(mesh::cli::handleACLGet(acl, "get acl", reply, capacity, false));
    CHECK(reply[capacity] == '#');
    CHECK(strlen(reply) < capacity);
    if (capacity > expected.size()) CHECK(reply == expected);
    else CHECK(strstr(reply, "ACL 1/1") == nullptr);
  }
  char byte = '#';
  CHECK(mesh::cli::handleACLGet(acl, "get acl", &byte, 0, false));
  CHECK(byte == '#');
}

static void listing_does_not_mutate_acl() {
  FakeFilesystem fs;
  ClientACL acl;
  mesh::LocalIdentity self;
  acl.load(&fs, self);
  for (unsigned i = 0; i < 5; ++i) add(acl, i, uint8_t(i));
  std::vector<ClientInfo> before;
  for (int i = 0; i < acl.getNumClients(); ++i) before.push_back(*acl.getClientByIdx(i));
  const auto writes = fs.bytes_written;
  for (const char* command : {"get acl", "get acl 2", "get acl 3", "get acl 0"}) {
    query(acl, command);
  }
  CHECK(acl.getNumClients() == int(before.size()));
  for (int i = 0; i < acl.getNumClients(); ++i) {
    CHECK(memcmp(acl.getClientByIdx(i), &before[i], sizeof(ClientInfo)) == 0);
  }
  CHECK(fs.bytes_written == writes);
}

static void actual_role_dispatch_handles_radio_and_local() {
  ClientACL acl;
  auto* admin = add(acl, 0, PERM_ACL_ADMIN);
  add(acl, 1, PERM_ACL_READ_WRITE);
  const auto expected = query(acl, "get acl");
  for (auto handler : {repeaterCommand, roomCommand, sensorCommand}) {
    for (uint32_t timestamp : {0U, 12345U}) {
      for (const char* input : {"get acl", "get acl 1", "ab|GET acl", "ab|get acl 1"}) {
        char command[80], reply[161];
        strcpy(command, input);
        memset(reply, '#', sizeof(reply));
        mesh::console.output.clear();
        handler(acl, admin, timestamp, command, reply);
        CHECK(mesh::console.output.empty());
        CHECK(reply[160] == '#');
        CHECK(strlen(reply) < 160);
        CHECK(std::string(reply) == (input[2] == '|' ? "ab|" + expected : expected));
      }
    }
    char command[] = "get acl", reply[160] = {};
    mesh::console.output.clear();
    handler(acl, nullptr, 0, command, reply);
    CHECK(reply[0] == 0);
    CHECK(mesh::console.output.find("ACL:") == 0);
    CHECK(mesh::console.output.find(row(admin)) != std::string::npos);
    strcpy(command, "get acl");
    char paged[] = "get acl 1";
    mesh::console.output.clear();
    handler(acl, nullptr, 0, paged, reply);
    CHECK(mesh::console.output.empty());
    CHECK(reply == expected);
  }
}

static void repeater_delegation_stays_denied() {
  ClientACL acl;
  auto* sender = add(acl, 0, 3);
  for (unsigned permissions = 0; permissions <= 255; ++permissions) {
    sender->permissions = uint8_t(permissions);
    for (const char* input : {"get acl", "get acl 2", "xy|get acl", "xy|get acl 2"}) {
      char command[32], reply[160] = {};
      strcpy(command, input);
      mesh::console.output.clear();
      repeaterCommand(acl, sender, 0, command, reply);
      CHECK(mesh::console.output.empty());
      const char* body = input[2] == '|' ? reply + 3 : reply;
      if ((permissions & PERM_ACL_ROLE_MASK) != PERM_ACL_ADMIN) {
        CHECK(strcmp(body, "Err - not permitted") == 0);
      } else {
        CHECK(strstr(body, "not permitted") == nullptr);
      }
    }
  }
}

int main() {
  empty_and_single_entry();
  skips_inactive_and_preserves_full_keys();
  full_table_pages();
  rejects_bad_pages();
  matching_and_local_fallback();
  bounds_never_truncate_keys();
  listing_does_not_mutate_acl();
  actual_role_dispatch_handles_radio_and_local();
  repeater_delegation_stays_denied();
  puts("9 ACL CLI checks passed");
}
