#include <helpers/MorseLoopPolicy.h>
#include <helpers/RadioPresetConfig.h>
static uint32_t now_ms = 100;
uint32_t millis() { return now_ms; }
#define PUB_KEY_SIZE 32
struct ClientInfo {
  struct { uint8_t pub_key[32] = {}; } id;
  bool admin = true;
  bool isAdmin() const { return admin; }
};
struct ACL {
  std::vector<ClientInfo> clients;
  size_t requested_key_size = 0;
  const ClientInfo* getClient(const uint8_t* key, size_t size) {
    requested_key_size = size;
    assert(size == 32);
    for (const auto& client : clients) if (!memcmp(client.id.pub_key, key, size)) return &client;
    return nullptr;
  }
};
struct Callbacks { TestRadio* radio; mesh::Radio* getProfileRadio() { return radio; } };
struct CommonCLI {
  mesh::MorseReplyHandoff _morse_reply;
  ACL* _acl;
  Callbacks* _callbacks;
  struct Profiles { unsigned loops = 0; void loop() { ++loops; } } _radio_profiles;
  struct Presets { unsigned loops = 0; void loop(unsigned long) { ++loops; } } _radio_presets;
  unsigned management_loops = 0, tx_services = 0;
  CommonCLI(ACL& acl, Callbacks& callbacks) : _acl(&acl), _callbacks(&callbacks) {}
  void loopBatteryForwarding(bool = false) {}
  void recoverMediaMeshRegions() {}
  void loopManagement() { ++management_loops; }
  void servicePendingTxPower() { ++tx_services; }
  void servicePendingRxSettings() {}
  void loop();
};
@LOOP@

int main() {
  const uint8_t key[32] = {1, 2, 3, 4};
  for (int scenario = 0; scenario < 5; ++scenario) {
    TestRadio radio; radio.rx_supported = true;
    ACL acl; ClientInfo actor; memcpy(actor.id.pub_key, key, 32); acl.clients.push_back(actor);
    Callbacks callbacks{&radio}; CommonCLI cli(acl, callbacks);
    int packet;
    cli._morse_reply.beginCommand(key);
    assert(cli._morse_reply.stage(902100, 25, 60000, now_ms));
    cli._morse_reply.endCommand(); cli._morse_reply.prepareReply(&packet);
    cli._morse_reply.armReply(&packet, true);
    cli.loop(); assert(radio.rx_starts == 0); // shared loop must still wait for PHY completion
    cli._morse_reply.complete(&packet);
    if (scenario == 0) acl.clients[0].admin = false;
    if (scenario == 1) { acl.clients[0].id.pub_key[31] = 1; } // reused slot, same short key
    if (scenario == 2) {
      acl.clients[0].id.pub_key[31] = 1; acl.clients.push_back(actor); // original full identity moved
    }
    if (scenario == 3) mesh::usable = false;
    if (scenario == 4) mesh::current.frequency_khz = 902101;
    cli.loop();
    const bool allowed = scenario == 2;
    assert(radio.rx_starts == unsigned(allowed) && !cli._morse_reply.pending());
    assert(acl.requested_key_size == 32 && cli._radio_profiles.loops == unsigned(allowed ? 1 : 2));
    assert(cli.management_loops == unsigned(allowed ? 1 : 2));
    assert(cli.tx_services == cli.management_loops);
    assert(cli._radio_presets.loops == cli._radio_profiles.loops);
    mesh::usable = true; mesh::current = mesh::MorseSettings();
  }
}
