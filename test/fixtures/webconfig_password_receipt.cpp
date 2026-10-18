// Execute production result projection with real JSON. Auth capability gates
// are exercised separately by webconfig_recovery_auth.cpp.
#include <cassert>
#include <cstring>
#include <string>
#include <ArduinoJson.h>
#include <helpers/WebConfigBatch.h>
struct Entry { char key[16]; char reply[160]; };
struct Results {
  int _batch_count = 4;
  Entry _batch[4] = {
    {"password", "OK"},
    {"other", "> arbitrary-private-value"},
    {"other", "Error: arbitrary-private-error"},
    {"other", "OK - arbitrary-private-success"}
  };
  std::string config(bool authenticated) {
    DynamicJsonDocument doc(4096);
    JsonArray results = doc.createNestedArray("results");
@CONFIG_LOOP@
    std::string out; serializeJson(doc, out); return out;
  }
  std::string cli(bool receipt_only) {
    const int from = 0, page = _batch_count;
    DynamicJsonDocument doc(4096);
    JsonArray results = doc.createNestedArray("results");
@CLI_LOOP@
    std::string out; serializeJson(doc, out); return out;
  }
};
int main() {
  Results value;
  const auto config_receipt = value.config(false), cli_receipt = value.cli(true);
  for (const auto* receipt : {&config_receipt, &cli_receipt}) {
    assert(receipt->find("arbitrary-private") == std::string::npos);
    assert(receipt->find("OK") != std::string::npos);
    assert(receipt->find("sign in again") != std::string::npos);
  }
  assert(cli_receipt.find("Sign in again to read this result") != std::string::npos);
  // Fresh current-generation authentication permits the same batch to be
  // reread with complete details, without executing its commands again.
  assert(value.config(true).find("arbitrary-private-error") != std::string::npos);
  assert(value.cli(false).find("arbitrary-private-value") != std::string::npos);
  assert(value.cli(false).find("arbitrary-private-success") != std::string::npos);
}
