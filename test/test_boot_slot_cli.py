"""Execute ESP32 boot selection against mocked IDF flash and OTA services."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HEADERS = {
    "MeshCore.h": r'''
#pragma once
namespace mesh {
class MainBoard {
public:
  bool ota_running = false;
  bool isOTAUpdateRunning() const { return ota_running; }
};
}
''',
    "esp_partition.h": r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
constexpr esp_err_t ESP_ERR_NOT_FOUND = 0x105;
constexpr esp_err_t ESP_ERR_INVALID_ARG = 0x102;
constexpr esp_err_t ESP_ERR_OTA_VALIDATE_FAILED = 0x1503;
constexpr esp_err_t ESP_FAIL = -1;
constexpr unsigned ESP_PARTITION_TYPE_APP = 0;
constexpr unsigned ESP_PARTITION_TYPE_DATA = 1;
constexpr unsigned ESP_PARTITION_SUBTYPE_APP_OTA_0 = 0x10;
constexpr unsigned ESP_PARTITION_SUBTYPE_APP_OTA_1 = 0x11;
using esp_partition_subtype_t = unsigned;
struct esp_partition_t {
  unsigned type, subtype;
  uint32_t address, size;
  char label[17];
};
const esp_partition_t* esp_partition_find_first(unsigned, unsigned, const char*);
''',
    "esp_err.h": '#pragma once\n#include "esp_partition.h"\n',
    "esp_ota_ops.h": r'''
#pragma once
#include "esp_partition.h"
const esp_partition_t* esp_ota_get_running_partition();
const esp_partition_t* esp_ota_get_boot_partition();
esp_err_t esp_ota_set_boot_partition(const esp_partition_t*);
enum esp_ota_img_states_t {
  ESP_OTA_IMG_NEW, ESP_OTA_IMG_PENDING_VERIFY, ESP_OTA_IMG_VALID,
  ESP_OTA_IMG_INVALID, ESP_OTA_IMG_ABORTED, ESP_OTA_IMG_UNDEFINED
};
esp_err_t esp_ota_get_state_partition(const esp_partition_t*, esp_ota_img_states_t*);
''',
    "Update.h": r'''
#pragma once
struct UpdateClass {
  bool running = false;
  bool isRunning() const { return running; }
};
extern UpdateClass Update;
''',
    "ota/OtaContext.h": r'''
#pragma once
#include <stdint.h>
namespace mesh { namespace ota {
struct OtaManager {
  enum FetchState : uint8_t {
    IDLE, WANT_MANIFEST, WANT_LEAVES, VERIFYING_STAGED, FETCHING,
    COMPLETE, FAILED, PAUSED
  };
  FetchState state = IDLE;
  uint8_t serve_jobs = 0, manifest_jobs = 0;
  FetchState fetchState() const { return state; }
  uint8_t pendingServeJobs() const { return serve_jobs; }
  uint8_t pendingManifestJobs() const { return manifest_jobs; }
};
struct Store {
  uint32_t staged = 0;
  uint32_t staged_size() const { return staged; }
};
struct OtaContext {
  OtaManager manager;
  Store fetch_store;
  bool apply_pending = false, bootloader_apply_pending = false;
  bool folder_active = false;
  unsigned serve_expected = 0;
};
extern OtaContext* active_context;
inline OtaContext* ota_context_if_active() { return active_context; }
}}
''',
}

PRELUDE = r'''
#include "MeshCore.h"
#include "esp_partition.h"
#include "esp_ota_ops.h"
#include "Update.h"
#include "BootSlot.h"
#include "esp32/ManagementPassword.h"
#if ENABLE_OTA
#include "ota/OtaContext.h"
namespace mesh { namespace ota { OtaContext* active_context = nullptr; }}
#endif
#include <cassert>
#include <cstring>
#include <initializer_list>

UpdateClass Update;
static esp_partition_t app0{ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0,
                           0x10000, 0x1f0000, "app0"};
static esp_partition_t app1{ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1,
                           0x200000, 0x1f0000, "app1"};
static const esp_partition_t* running = &app0;
static const esp_partition_t* selected = &app0;
static bool have0 = true, have1 = true, valid0 = true, valid1 = true;
static bool fail_select = false;
static esp_err_t state_error = ESP_ERR_NOT_FOUND;
static esp_ota_img_states_t image_state = ESP_OTA_IMG_UNDEFINED;
static unsigned selections = 0;
static bool upload_blocked_during_verification = false;

const esp_partition_t* esp_partition_find_first(unsigned type, unsigned subtype, const char* label) {
  for (const auto* p : {have0 ? &app0 : nullptr, have1 ? &app1 : nullptr}) {
    if (p && p->type == type && p->subtype == subtype && (!label || !strcmp(label, p->label))) return p;
  }
  return nullptr;
}
const esp_partition_t* esp_ota_get_running_partition() { return running; }
const esp_partition_t* esp_ota_get_boot_partition() { return selected; }
esp_err_t esp_ota_get_state_partition(const esp_partition_t*, esp_ota_img_states_t* out) {
  *out = image_state;
  return state_error;
}
esp_err_t esp_ota_set_boot_partition(const esp_partition_t* p) {
  ++selections;
  using namespace mesh::esp32::managementPassword;
  Snapshot snapshot_value;
  assert(snapshot(snapshot_value));
  upload_blocked_during_verification = !beginUpload(snapshot_value.generation);
  if (!upload_blocked_during_verification) endUpload();
  if (fail_select || !(p == &app0 ? valid0 : valid1)) return ESP_ERR_OTA_VALIDATE_FAILED;
  selected = p;
  return ESP_OK;
}

static mesh::MainBoard board;
static char reply[160];
static bool command(const char* text) {
  memset(reply, 0, sizeof(reply));
  return mesh::esp32::handleBootSlotCommand(text, board, reply, sizeof(reply));
}
static void succeeds(const char* text) {
  assert(command(text));
  assert(!strncmp(reply, "OK", 2));
}
static void rejects(const char* text) {
  const auto before = selected;
  assert(command(text));
  assert(!strncmp(reply, "Error", 5) || !strncmp(reply, "ERR", 3));
  assert(selected == before);
}
static void initialize() {
  assert(mesh::esp32::managementPassword::initialize("test-password"));
}
'''


class BootSlotCliTest(unittest.TestCase):
    def run_fixture(self, source, ota=False):
        with tempfile.TemporaryDirectory(prefix="mesh-boot-slot-") as temporary:
            folder = Path(temporary)
            for name, contents in HEADERS.items():
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            # Keep production code unchanged. The temporary include directory
            # substitutes IDF services and OTA storage, not the CLI behavior.
            production = folder / "BootSlot.cpp"
            production.write_bytes((ROOT / "src/helpers/BootSlot.cpp").read_bytes())
            main = folder / "test.cpp"
            main.write_text(PRELUDE + source)
            executable = folder / "test"
            flags = ["-DESP32_PLATFORM=1", "-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1"]
            if ota:
                flags += ["-DENABLE_OTA=1", "-DOTA_FLASH_STORE=1"]
            result = subprocess.run(
                [os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                 "-pthread", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                 "-fno-pie", "-no-pie", *flags, "-I", str(folder),
                 "-I", str(ROOT / "src/helpers"), "-I", str(ROOT / "src"),
                 str(production), str(main), "-o", str(executable)],
                text=True, capture_output=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readback_selection_and_no_restart_or_unrelated_flash_writes(self):
        self.run_fixture(r'''
int main() {
  initialize();
  assert(command("get boot.slot"));
  assert(strstr(reply, "running=app0") && strstr(reply, "next=app0"));
  assert(strstr(reply, "app0=") && strstr(reply, "app1="));
  assert(selections == 0);
  succeeds("set boot.slot app1");
  assert(selected == &app1 && running == &app0);
  assert(upload_blocked_during_verification);
  assert(command("get boot.slot") && strstr(reply, "next=app1"));
  succeeds("set boot.slot app0");
  assert(selected == &app0 && running == &app0);
  have1 = false;
  assert(command("get boot.slot") && strstr(reply, "app1=missing"));
  rejects("set boot.slot app1");
  // A factory-only app remains identifiable without inventing OTA slots.
  esp_partition_t factory{ESP_PARTITION_TYPE_APP, 0, 0x10000, 0x300000, "factory"};
  running = selected = &factory; have0 = false;
  assert(command("get boot.slot") && strstr(reply, "running=factory"));
  assert(strstr(reply, "app0=missing") && strstr(reply, "app1=missing"));
  rejects("set boot.slot app0");
  running = selected = nullptr;
  assert(command("get boot.slot") && strstr(reply, "running=unknown") && strstr(reply, "next=unknown"));
}
''')

    def test_parser_boundaries_bad_images_and_idf_failure_leave_selection_alone(self):
        self.run_fixture(r'''
int main() {
  initialize();
  for (const char* text : {"get boot.slot.extra", "set boot.slotx app1", "get radio"})
    assert(!command(text));
  for (const char* text : {"set boot.slot", "set boot.slot app2", "set boot.slot nvs",
       "set boot.slot app1 extra", "set boot.slot app10", "set boot.slot APP1",
       "get boot.slot app1"}) rejects(text);
  assert(selections == 0);
  valid1 = false; rejects("set boot.slot app1");
  valid1 = true; fail_select = true; rejects("set boot.slot app1");
  fail_select = false;
  // Labels alone cannot turn a data partition or wrong OTA subtype into an app.
  app1.type = ESP_PARTITION_TYPE_DATA; rejects("set boot.slot app1");
  app1.type = ESP_PARTITION_TYPE_APP;
  app1.subtype = ESP_PARTITION_SUBTYPE_APP_OTA_0; rejects("set boot.slot app1");
  app1.subtype = ESP_PARTITION_SUBTYPE_APP_OTA_1;
  strcpy(app1.label, "renamed"); rejects("set boot.slot app1");
  strcpy(app1.label, "app1"); succeeds("set boot.slot app1");
  // Null and zero-length outputs must not arm a slot, and tiny replies stay bounded.
  selected = &app0;
  mesh::esp32::handleBootSlotCommand("set boot.slot app1", board, nullptr, 0);
  assert(selected == &app0);
  char bounded[5] = {'x','x','x','x','!'};
  mesh::esp32::handleBootSlotCommand("get boot.slot", board, bounded, 4);
  assert(bounded[3] == 0 && bounded[4] == '!');
}
''')

    def test_wifi_update_and_cross_task_upload_lease_prevent_boot_mutation(self):
        self.run_fixture(r'''
int main() {
  initialize();
  board.ota_running = true; rejects("set boot.slot app1");
  assert(command("get boot.slot")); board.ota_running = false;
  Update.running = true; rejects("set boot.slot app1"); Update.running = false;
  assert(mesh::esp32::handleBootSlotCommand("set boot.slot app1", board, reply, sizeof(reply), true));
  assert(selected == &app0 && !strncmp(reply, "Error", 5));
  namespace password = mesh::esp32::managementPassword;
  password::Snapshot credentials; assert(password::snapshot(credentials));
  {
    password::UploadGuard upload(credentials.generation); assert(upload.acquired());
    rejects("set boot.slot app1");
  }
  {
    password::ChangeGuard change; assert(change.acquired());
    rejects("set boot.slot app1");
  }
  assert(selections == 0);
  succeeds("set boot.slot app1"); assert(upload_blocked_during_verification);
  assert(password::beginUpload(credentials.generation)); password::endUpload();
}
''')

    def test_lora_fetch_staging_apply_and_pending_jobs_prevent_boot_mutation(self):
        self.run_fixture(r'''
int main() {
  initialize();
  using namespace mesh::ota;
  OtaContext context; active_context = &context;
  for (auto state : {OtaManager::WANT_MANIFEST, OtaManager::WANT_LEAVES,
       OtaManager::VERIFYING_STAGED, OtaManager::FETCHING, OtaManager::PAUSED}) {
    context.manager.state = state; rejects("set boot.slot app1");
  }
  context.manager.state = OtaManager::IDLE;
  context.apply_pending = true; rejects("set boot.slot app1"); context.apply_pending = false;
  context.bootloader_apply_pending = true; rejects("set boot.slot app1"); context.bootloader_apply_pending = false;
  context.manager.serve_jobs = 1; rejects("set boot.slot app1"); context.manager.serve_jobs = 0;
  context.manager.manifest_jobs = 1; rejects("set boot.slot app1"); context.manager.manifest_jobs = 0;
  context.folder_active = true; rejects("set boot.slot app1"); context.folder_active = false;
  context.serve_expected = 123; rejects("set boot.slot app1"); context.serve_expected = 0;
  context.fetch_store.staged = 500; rejects("set boot.slot app1"); context.fetch_store.staged = 0;
  assert(selections == 0);
  for (auto state : {OtaManager::IDLE, OtaManager::COMPLETE, OtaManager::FAILED}) {
    context.manager.state = state;
    succeeds("set boot.slot app1"); succeeds("set boot.slot app0");
  }
  active_context = nullptr; succeeds("set boot.slot app1");
}
''', ota=True)

    def test_bootloader_rejected_and_unconfirmed_slots_cannot_be_rearmed(self):
        self.run_fixture(r'''
int main() {
  initialize();
  state_error = ESP_OK;
  for (auto state : {ESP_OTA_IMG_INVALID, ESP_OTA_IMG_ABORTED, ESP_OTA_IMG_PENDING_VERIFY}) {
    image_state = state; rejects("set boot.slot app1");
  }
  state_error = ESP_FAIL; rejects("set boot.slot app1");
  assert(selections == 0);
  state_error = ESP_OK;
  for (auto state : {ESP_OTA_IMG_NEW, ESP_OTA_IMG_VALID, ESP_OTA_IMG_UNDEFINED}) {
    image_state = state; succeeds("set boot.slot app1"); succeeds("set boot.slot app0");
  }
  state_error = ESP_ERR_NOT_FOUND; succeeds("set boot.slot app1");
}
''')

    def test_other_platforms_do_not_include_sdk_or_add_a_boot_selector(self):
        with tempfile.TemporaryDirectory(prefix="mesh-boot-slot-excluded-") as temporary:
            folder = Path(temporary)
            result = subprocess.run(
                [os.environ.get("CXX", "c++"), "-std=c++11", "-DNRF52_PLATFORM=1",
                 "-c", str(ROOT / "src/helpers/BootSlot.cpp"), "-o", str(folder / "slot.o")],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            symbols = subprocess.run(["nm", str(folder / "slot.o")], capture_output=True, text=True, timeout=10)
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            self.assertNotIn("handleBootSlotCommand", symbols.stdout)


if __name__ == "__main__":
    unittest.main()
