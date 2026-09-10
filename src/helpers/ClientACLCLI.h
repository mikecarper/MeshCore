#pragma once

#include "ClientACL.h"
#include <Utils.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace cli {

// Call after the role's admin authorization check. Bare local `get acl`
// retains its streamed listing; radio requests and explicit pages use one
// bounded reply. Callers reserve three bytes for the companion CLI prefix.
inline bool handleACLGet(const ClientACL& acl, const char* command,
                         char* reply, size_t capacity, bool local) {
  static const char prefix[] = "get acl";
  const size_t prefix_len = sizeof(prefix) - 1;
  if (strncmp(command, prefix, prefix_len) != 0) return false;
  const char* arg = command + prefix_len;
  if (*arg && *arg != ' ' && *arg != '\t' && *arg != '\r' && *arg != '\n') return false;
  if (local && *arg == 0) return false;
  if (reply == nullptr || capacity == 0) return true;

  while (*arg == ' ' || *arg == '\t') ++arg;
  unsigned page = 1;
  if (*arg >= '0' && *arg <= '9') {
    page = 0;
    do {
      page = page * 10 + unsigned(*arg++ - '0');
      if (page > 65535) break;
    } while (*arg >= '0' && *arg <= '9');
  }
  while (*arg == ' ' || *arg == '\t' || *arg == '\r' || *arg == '\n') ++arg;
  if (*arg || page == 0 || page > 65535) {
    snprintf(reply, capacity, "Err - usage: get acl [page]");
    return true;
  }

  unsigned total = 0;
  for (int i = 0; i < acl.getNumClients(); ++i) {
    if (acl.getClientByIdx(i)->permissions != 0) ++total;
  }
  if (total == 0 && page == 1) {
    snprintf(reply, capacity, "ACL: empty");
    return true;
  }
  static constexpr unsigned PAGE_SIZE = 2;
  const unsigned pages = total == 0 ? 1 : (total + PAGE_SIZE - 1) / PAGE_SIZE;
  if (page > pages) {
    snprintf(reply, capacity, "Err - ACL page range: 1-%u", pages);
    return true;
  }

  const unsigned offset = (page - 1) * PAGE_SIZE;
  const unsigned rows = total - offset < PAGE_SIZE ? total - offset : PAGE_SIZE;
  const int header_len = snprintf(reply, capacity, "ACL %u/%u", page, pages);
  // Never return a truncated key if a caller supplies a smaller buffer.
  static constexpr size_t ROW_BYTES = 4 + PUB_KEY_SIZE * 2;
  if (header_len < 0 || size_t(header_len) >= capacity
      || rows > (capacity - size_t(header_len) - 1) / ROW_BYTES) {
    snprintf(reply, capacity, "Err - ACL reply too small");
    return true;
  }
  size_t used = size_t(header_len);
  unsigned active_index = 0;
  unsigned written = 0;
  for (int i = 0; i < acl.getNumClients() && written < rows; ++i) {
    const auto* client = acl.getClientByIdx(i);
    if (client->permissions == 0) continue;  // match the local listing
    if (active_index++ < offset) continue;
    char key[PUB_KEY_SIZE * 2 + 1];
    Utils::toHex(key, client->id.pub_key, PUB_KEY_SIZE);
    used += snprintf(reply + used, capacity - used, "\n%02X %s",
                     unsigned(client->permissions), key);
    ++written;
  }
  return true;
}

}  // namespace cli
}  // namespace mesh
