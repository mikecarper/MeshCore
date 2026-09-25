// A self-hosted partition bridge. Reuse the tested Wi-Fi migration engine so
// both legacy OTA placements preserve the running image and node configuration.
// After its restart into expanded app0, this role also receives the final
// Full application over LoRa; the old application need not remain bootable.
#define setup partitionMigrationSetup
#define loop partitionMigrationLoop
#include "../esp32_partition_migrator/main.cpp"
#undef setup
#undef loop

#include <Mesh.h>
#include <target.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/ESP32TrueRandom.h>
#include <helpers/CommonCLI.h>
#include <helpers/IdentityStore.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/ota/OtaContext.h>
#include <helpers/ota/OtaApply.h>

#ifndef MOTA_MIGRATION_TARGET_ID
#error "Partition Expander must pin the exact successor Full target ID"
#endif

#ifndef LORA_FREQ
#define LORA_FREQ 915.0
#endif
#ifndef LORA_BW
#define LORA_BW 250.0
#endif
#ifndef LORA_SF
#define LORA_SF 10
#endif
#ifndef LORA_CR
#define LORA_CR 5
#endif

namespace {

class ExpanderMesh final : public mesh::Mesh {
 public:
  ExpanderMesh(mesh::Radio& radio, mesh::MillisecondClock& millis_clock,
               mesh::RNG& rng, mesh::RTCClock& rtc,
               mesh::PacketManager& packets, mesh::MeshTables& tables)
      : mesh::Mesh(radio, millis_clock, rng, rtc, packets, tables) {}

  bool isTempRadioActive() const override { return true; }
};

ArduinoMillis uptime_clock;
StdRNG fast_rng;
StaticPoolPacketManager packets(32);
SimpleMeshTables tables;
ExpanderMesh the_mesh(radio_driver, uptime_clock, fast_rng, rtc_clock, packets, tables);
bool lora_ready = false;
bool install_attempted = false;
mesh::RadioProfileParams active_profile;

// /com_prefs stores these fields at fixed offsets in both stock 1.17.1 and
// keymindCascade. Keep the migration receiver on the user's saved radio1
// channel so an existing LoRa seeder can reach it without a USB-only retune.
bool loadPrimaryRadio(mesh::RadioProfileParams& profile) {
  bool ok = false;
  if (SPIFFS.exists("/com_prefs") || SPIFFS.exists("/node_prefs")) {
    File prefs = SPIFFS.open(SPIFFS.exists("/com_prefs")
        ? "/com_prefs" : "/node_prefs", "r");
    if (prefs && prefs.size() >= 290) {
      ok = prefs.seek(72) && prefs.readBytes(reinterpret_cast<char*>(&profile.freq), 4) == 4;
      ok = ok && prefs.seek(112) && prefs.readBytes(reinterpret_cast<char*>(&profile.sf), 1) == 1;
      ok = ok && prefs.readBytes(reinterpret_cast<char*>(&profile.cr), 1) == 1;
      ok = ok && prefs.seek(116) && prefs.readBytes(reinterpret_cast<char*>(&profile.bw), 4) == 4;
    }
    if (prefs) prefs.close();
  } else if (SPIFFS.exists("/prefs.json")) {
    File prefs = SPIFFS.open("/prefs.json", "r");
    NodePrefs legacy;
    ok = prefs && legacy.loadSerial(prefs);
    if (prefs) prefs.close();
    if (ok) {
      profile.freq = legacy.freq;
      profile.bw = legacy.bw;
      profile.sf = legacy.sf;
      profile.cr = legacy.cr;
    }
  }
  return ok && profile.freq >= 100.0f && profile.freq <= 2500.0f
      && profile.bw > 0.0f && profile.bw <= 500.0f
      && profile.sf >= 5 && profile.sf <= 12
      && profile.cr >= 5 && profile.cr <= 8;
}

void startLoRaReceiver() {
  board.begin();
  if (!radio_init()) {
    Serial.println("Partition Expander: radio init failed; Wi-Fi recovery remains available");
    return;
  }
  fast_rng.begin(radio_driver.getRngSeed());

  if (!SPIFFS.begin(false)) {
    Serial.println("Partition Expander: identity filesystem unavailable");
    return;
  }
  IdentityStore identity(SPIFFS, "/identity");
  if (identity.loadResult("_main", the_mesh.self_id) != IdentityLoadResult::Loaded) {
    Serial.println("Partition Expander: private identity unavailable; LoRa disabled");
    return;
  }

  mesh::RadioProfileParams profile;
  profile.freq = LORA_FREQ;
  profile.bw = LORA_BW;
  profile.sf = LORA_SF;
  profile.cr = LORA_CR;
  // HIL recipe: save 909.5 MHz / 500 kHz / SF5 / CR5 as radio1 on
  // both bench nodes before migration. Production recovery must still use
  // the node's own saved radio1, never a hard-coded lab frequency.
  if (!loadPrimaryRadio(profile)) {
    Serial.println("Partition Expander: saved radio1 unavailable; using build preset");
  }
  if (radio_driver.trySetPrimaryParams(profile, true)
      != mesh::RadioParamApplyResult::APPLIED) {
    Serial.println("Partition Expander: OTA radio profile refused");
    return;
  }
  active_profile = profile;

  the_mesh.begin();
  auto& ota = mesh::ota::ota_ctx();
  ota.manager.set_auto_migration_target((uint32_t)MOTA_MIGRATION_TARGET_ID);
  ota.manager.set_auto_version_floor(0, false);
  ota.manager.set_autofetch(mesh::ota::OtaManager::AUTOFETCH_ANY);
  lora_ready = true;
  Serial.println("Partition Expander: waiting for exact Full target over LoRa");
}

}  // namespace

String partitionExpanderStatus() {
  if (reboot_at) return "Returning to the verified Full image";
  if (!lora_ready) return "LoRa receiver unavailable; use Wi-Fi recovery";
  const auto& manager = mesh::ota::ota_ctx().manager;
  char detail[160];
  snprintf(detail, sizeof(detail),
           "LoRa receiver %.3f MHz / %.1f kHz / SF%u / CR%u; "
           "OTA state %u; blocks %lu/%lu; request window %u/%u",
           active_profile.freq, active_profile.bw,
           (unsigned)active_profile.sf, (unsigned)active_profile.cr,
           (unsigned)manager.fetchState(),
           (unsigned long)manager.blocksHave(),
           (unsigned long)manager.blocksTotal(),
           (unsigned)manager.fetchPipelineWidth(),
           (unsigned)manager.fetchPipelineCapacity());
  return detail;
}

void setup() {
  partitionMigrationSetup();
  // Never start a LoRa fetch against the 1.25 MiB layout or before the key is
  // restored. The migration engine restarts after verifying the new table.
  if (!expanded_layout_ready || !identity_ready) return;
  // On an already-expanded board the temporary role may have landed next to
  // a valid Full image. Return to it instead of needlessly replacing it.
  if (returnToVerifiedFull()) return;
  if (!hasExpanderHandoff()) {
    strcpy(status_text,
           "Already expanded; no migration handoff. Wi-Fi recovery available");
    return;
  }
  if (!hasVerifiedConfigHandoff()) {
    strcpy(status_text,
           "Refused: saved configuration was not verified; Wi-Fi recovery available");
    return;
  }
  startLoRaReceiver();
  if (lora_ready) strcpy(status_text, "Expanded layout ready; LoRa receiver active");
}

void loop() {
  partitionMigrationLoop();
  if (!lora_ready) return;
  the_mesh.loop();
  auto& ota = mesh::ota::ota_ctx();
  if (!install_attempted
      && ota.manager.fetchState() == mesh::ota::OtaManager::COMPLETE) {
    install_attempted = true;
    uint8_t manifest_bytes[mesh::ota::MOTA_MFL];
    mesh::ota::MotaManifest manifest;
    if (!ota.fetch_store.read(8, manifest_bytes, sizeof(manifest_bytes))
        || !mesh::ota::mota_parse_manifest(manifest_bytes,
                                            sizeof(manifest_bytes), manifest)
        || !manifest.is_full()
        || manifest.target_id != (uint32_t)MOTA_MIGRATION_TARGET_ID) {
      Serial.println("Partition Expander: staged image is not the pinned Full target");
      return;
    }
    char message[100] = {};
    const bool installed = ota.apply_fetched(message);
    Serial.println(message);
    if (installed) {
      // This role has no CLI reply queue or role-specific reboot scheduler.
      // apply_fetched() only arms the ESP32 OTA slot; it does not restart.
      Serial.flush();
      delay(200);
      mesh::ota::ota_reboot_to_apply();
    } else {
      Serial.println("Partition Expander: Full install refused; restart to retry");
    }
  }
}
