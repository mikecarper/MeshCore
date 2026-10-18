"""Execute the Wi-Fi migration bridge's AP credential choice on the host.

The bridge's AP gates an unauthenticated firmware uploader while the private
key is staged. Compile the actual Wi-Fi-only block of the migrator against a
fake SPIFFS and WiFi, and require the AP passphrase to be the node's saved
admin password whenever that is a usable WPA2 passphrase.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/esp32_partition_migrator/main.cpp"
DOCS = (
    ROOT / "docs/esp32_wifi_partition_migration.md",
    ROOT / "docs/heltec_v4_wifi_partition_migration.md",
    ROOT / "scripts/package_esp32_partition_migration.py",
)

HARNESS = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

typedef int esp_err_t;
constexpr esp_err_t ESP_OK = 0;
enum { WIFI_AP_STA = 3, WIFI_IF_STA = 0, WIFI_IF_AP = 1, HTTP_GET = 1 };

struct String {
  std::string s;
  String() = default;
  String(const char* value) : s(value) {}
  void reserve(size_t bytes) { s.reserve(bytes); }
  String& operator+=(const char* value) { s += value; return *this; }
  String& operator+=(const String& value) { s += value.s; return *this; }
  const char* c_str() const { return s.c_str(); }
};

struct SerialPort {
  std::string out;
  void println(const char* line) { out += line; out += '\n'; }
  __attribute__((format(printf, 2, 3))) void printf(const char* format, ...) {
    char line[512];
    va_list args;
    va_start(args, format);
    vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    out += line;
  }
} Serial;

struct IPAddress {
  uint8_t octets[4];
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) : octets{a, b, c, d} {}
  String toString() const {
    char text[16];
    snprintf(text, sizeof(text), "%u.%u.%u.%u", octets[0], octets[1], octets[2], octets[3]);
    return String(text);
  }
};

struct WiFiClass {
  std::string ssid;
  std::string password;
  bool open = false;
  int ap_calls = 0;
  bool mode(int) { return true; }
  void setAutoReconnect(bool) {}
  bool disconnect(bool, bool) { return true; }
  void setSleep(bool) {}
  bool softAPConfig(IPAddress, IPAddress, IPAddress) { return true; }
  // Arduino-ESP32 2.0.17 WiFiAP.cpp: a null or empty passphrase starts an
  // open AP; 1-7 characters fail; anything longer selects WPA2-PSK.
  bool softAP(const char* name, const char* passphrase, int) {
    ++ap_calls;
    ssid = name;
    open = passphrase == nullptr || passphrase[0] == 0;
    password = passphrase ? passphrase : "";
    return open || strlen(passphrase) >= 8;
  }
  String softAPSSID() { return String(ssid.c_str()); }
  IPAddress softAPIP() { return IPAddress(192, 168, 4, 1); }
} WiFi;

namespace mesh { namespace wifi {
constexpr int kProtocolMask = 7;
int accessPointChannel() { return 6; }
esp_err_t applyAccessPointProtocolMask() { return ESP_OK; }
} }
esp_err_t esp_wifi_set_protocol(int, int) { return ESP_OK; }
esp_err_t esp_wifi_set_max_tx_power(int) { return ESP_OK; }
struct wifi_ap_config_t { uint8_t channel; uint8_t ssid_hidden; };
struct wifi_config_t { wifi_ap_config_t ap; };
esp_err_t esp_wifi_get_config(int, wifi_config_t* config) {
  config->ap.channel = 6;
  config->ap.ssid_hidden = 0;
  return ESP_OK;
}
esp_err_t esp_wifi_get_max_tx_power(int8_t* power) { *power = 78; return ESP_OK; }
void delay(unsigned) {}

struct AsyncWebServerRequest {
  std::string body;
  void send(int, const char*, const String& text) { body = text.s; }
  void send(int, const char*, const char* text) { body = text; }
};
struct AsyncWebServer {
  std::vector<std::string> routes;
  template <class Handler> void on(const char* path, int, Handler) { routes.push_back(path); }
  void begin() {}
} server;
struct ElegantOta {
  int begins = 0;
  void begin(AsyncWebServer*, const char* = "", const char* = "") { ++begins; }
} AsyncElegantOTA;

struct File {
  std::vector<uint8_t>* data = nullptr;
  size_t position = 0;
  explicit operator bool() const { return data != nullptr; }
  size_t size() const { return data ? data->size() : 0; }
  bool seek(size_t offset);
  size_t read(uint8_t* buffer, size_t count);
  void close();
};
struct Filesystem {
  std::map<std::string, std::vector<uint8_t>> files;
  bool mountable = true, mounted = false, seek_fails = false, short_read = false;
  std::string fail_open;
  unsigned begins = 0, ends = 0, opens = 0, closes = 0;
  // Before migration this is the legacy filesystem: it must never be formatted.
  bool begin(bool format) { assert(!format); ++begins; mounted = mountable; return mounted; }
  void end() { ++ends; mounted = false; }
  bool exists(const char* path) { return mounted && files.count(path) != 0; }
  File open(const char* path, const char* mode) {
    assert(!strcmp(mode, "r"));
    File file;
    auto entry = files.find(path);
    if (mounted && fail_open != path && entry != files.end()) {
      file.data = &entry->second; ++opens;
    }
    return file;
  }
} SPIFFS;
bool File::seek(size_t offset) {
  if (SPIFFS.seek_fails || offset > size()) return false;
  position = offset;
  return true;
}
size_t File::read(uint8_t* buffer, size_t count) {
  const size_t available = position < size() ? size() - position : 0;
  size_t bytes = count < available ? count : available;
  if (SPIFFS.short_read && bytes > 10) bytes = 10;  // partial I/O after some data
  memcpy(buffer, data->data() + position, bytes);
  position += bytes;
  return bytes;
}
void File::close() { if (data) { ++SPIFFS.closes; data = nullptr; } }

char status_text[160] = "";
bool migration_started = false;
bool migration_complete = false;
bool expanded_layout_ready = false;
bool identity_ready = false;
bool reboot_requested = false;

@CONSTANTS@
@WIFI_BLOCK@
void printJoinHint() {
@JOIN_HINT@
}

const char kFallback[] = @FALLBACK@;

// A /com_prefs image: printable bytes before the admin field (so a missed seek
// would yield a plausible passphrase), the admin field at 56 and a different,
// valid guest password at 88.
std::vector<uint8_t> prefs(size_t bytes, const char* admin, size_t admin_bytes = 0) {
  std::vector<uint8_t> image(bytes < 104 ? 104 : bytes, 0);
  for (size_t i = 0; i < 56; ++i) image[i] = static_cast<uint8_t>('A' + i % 26);
  memcpy(&image[56], admin, admin_bytes ? admin_bytes : strnlen(admin, 16));
  memcpy(&image[88], "guest-password1", 15);
  image.resize(bytes);
  return image;
}

void reset(bool expanded = false) {
  SPIFFS = Filesystem();
  WiFi = WiFiClass();
  Serial.out.clear();
  AsyncElegantOTA = ElegantOta();
  expanded_layout_ready = expanded;
  identity_ready = expanded;
  strcpy(status_text, expanded ? "Expanded layout ready" : "Legacy layout verified");
}

void balancedMount() {
  assert(SPIFFS.begins == 1);
  assert(SPIFFS.ends == (SPIFFS.mountable ? 1U : 0U));
  assert(SPIFFS.opens == SPIFFS.closes);
}

void expectAdmin(const char* expected) {
  startServer();
  printJoinHint();
  assert(WiFi.ap_calls == 1 && !WiFi.open);
  assert(WiFi.password == expected);
  assert(WiFi.password != kFallback);
  assert(strcmp(status_text, "Wi-Fi AP failed; restart the board and retry") != 0);
  // The secret is never written to the serial log; the hint names it instead.
  assert(Serial.out.find(expected) == std::string::npos);
  assert(Serial.out.find("password: this node's admin password") != std::string::npos);
  assert(Serial.out.find(kFallback) == std::string::npos);
  assert(Serial.out.find("Warning") == std::string::npos);
  assert(AsyncElegantOTA.begins == 1);
  balancedMount();
}

void expectFallback() {
  startServer();
  printJoinHint();
  assert(WiFi.ap_calls == 1 && !WiFi.open);
  assert(WiFi.password == kFallback);
  assert(strcmp(status_text, "Wi-Fi AP failed; restart the board and retry") != 0);
  assert(Serial.out.find("Warning") != std::string::npos);
  assert(Serial.out.find(std::string("password: ") + kFallback) != std::string::npos);
  assert(AsyncElegantOTA.begins == 1);
  balancedMount();
}

void expectUploadDisabled(const char* expected_password) {
  startServer();
  assert(WiFi.ap_calls == 1 && !WiFi.open && WiFi.password == expected_password);
  assert(AsyncElegantOTA.begins == 0);
  AsyncWebServerRequest request;
  sendHome(&request);
  assert(request.body.find("Private identity recovery did not complete") != std::string::npos);
  assert(request.body.find("Firmware upload is disabled") != std::string::npos);
  assert(request.body.find("href='/update'") == std::string::npos);
  balancedMount();
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = atoi(argv[1]);
  if (scenario == 0) {
    reset();
    SPIFFS.files["/com_prefs"] = prefs(290, "Correct-Horse9");
    expectAdmin("Correct-Horse9");
    // After the table change the Wi-Fi bridge has restored the same file.
    reset(true);
    SPIFFS.files["/com_prefs"] = prefs(1714, "Correct-Horse9");
    expectAdmin("Correct-Horse9");
  } else if (scenario == 1) {
    reset();
    SPIFFS.files["/node_prefs"] = prefs(290, "Node-prefs-pass");
    expectAdmin("Node-prefs-pass");
    // The firmware loads /com_prefs whenever it exists.
    reset();
    SPIFFS.files["/node_prefs"] = prefs(290, "Node-prefs-pass");
    SPIFFS.files["/com_prefs"] = prefs(290, "Com-prefs-pass");
    expectAdmin("Com-prefs-pass");
  } else if (scenario == 2) {
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "Seven77"); expectFallback();
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "Eight888"); expectAdmin("Eight888");
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "Fifteen-chars15");
    expectAdmin("Fifteen-chars15");
    // Byte 15 is always the terminator, as in the firmware's own loader.
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "Sixteen-chars-16", 16);
    expectAdmin("Sixteen-chars-1");
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, ""); expectFallback();
  } else if (scenario == 3) {
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "With space 1"); expectAdmin("With space 1");
    reset(); SPIFFS.files["/com_prefs"] = prefs(290, "Tilde~~~~"); expectAdmin("Tilde~~~~");
    for (const char* unusable : {"Del\x7f-char-1", "Ctl\x1f-char-1", "Caf\xc3\xa9-pass1"}) {
      reset(); SPIFFS.files["/com_prefs"] = prefs(290, unusable); expectFallback();
    }
  } else if (scenario == 4) {
    // Stock 1.10/1.11 files end at 166/170 bytes; only the 16-byte field is needed.
    reset(); SPIFFS.files["/com_prefs"] = prefs(166, "Stock-1-10-pass"); expectAdmin("Stock-1-10-pass");
    reset(); SPIFFS.files["/com_prefs"] = prefs(72, "Just-fits-72"); expectAdmin("Just-fits-72");
    reset(); SPIFFS.files["/com_prefs"] = prefs(71, "Truncated-field"); expectFallback();
    reset(); SPIFFS.files["/com_prefs"] = prefs(60, "Truncated-field"); expectFallback();
  } else if (scenario == 5) {
    reset(); expectFallback();  // never saved: the built-in default is unknown here
    reset(); SPIFFS.mountable = false;
    SPIFFS.files["/com_prefs"] = prefs(290, "Correct-Horse9");
    expectFallback();
  } else if (scenario == 6) {
    reset(); SPIFFS.seek_fails = true;
    SPIFFS.files["/com_prefs"] = prefs(290, "Correct-Horse9");
    expectFallback();
    reset(); SPIFFS.short_read = true;
    SPIFFS.files["/com_prefs"] = prefs(290, "Correct-Horse9");
    expectFallback();
  } else if (scenario == 7) {
    reset(true); identity_ready = false;
    SPIFFS.files["/com_prefs"] = prefs(290, "Correct-Horse9");
    expectUploadDisabled("Correct-Horse9");
    reset(true); identity_ready = false;
    expectUploadDisabled(kFallback);
  } else if (scenario == 8) {
    // Existing /com_prefs wins even when it cannot be opened. Never use a
    // potentially stale /node_prefs password to bypass that file precedence.
    reset(); SPIFFS.fail_open = "/com_prefs";
    SPIFFS.files["/com_prefs"] = prefs(290, "Com-prefs-pass");
    SPIFFS.files["/node_prefs"] = prefs(290, "Node-prefs-pass");
    expectFallback();
  } else {
    return 2;
  }
  return 0;
}
'''


def wifi_block(source):
    end = source.index("#else\nbool verifiedUsbHandoff()")
    marker = "#if !defined(MESHCORE_MIGRATION_USB) || !MESHCORE_MIGRATION_USB\n"
    return source[source.rindex(marker, 0, end) + len(marker):end]


class MigrationBridgeApPasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        source = SOURCE.read_text(encoding="utf-8")
        constants = re.findall(r'^constexpr char k\w*Ap\w*\[\] = "[^"]*";$', source, re.M)
        fallback = re.search(r'^constexpr char k\w*ApPassword\[\] = ("[^"]*");$', source, re.M)
        setup = extract_braced(source, "void setup()")
        hint = re.search(r'Serial\.printf\("%s\. Join %s \(password: %s\)[\s\S]*?\);', setup)
        if not constants or fallback is None or hint is None:
            raise AssertionError("bridge AP constants or setup join hint not found")
        harness = (HARNESS.replace("@CONSTANTS@", "\n".join(constants))
                   .replace("@WIFI_BLOCK@", wifi_block(source))
                   .replace("@JOIN_HINT@", hint.group())
                   .replace("@FALLBACK@", fallback.group(1)))
        cls.directory = tempfile.TemporaryDirectory(prefix="migration-ap-password-")
        path = Path(cls.directory.name) / "test.cpp"
        cls.binary = Path(cls.directory.name) / "test"
        path.write_text(harness, encoding="utf-8")
        result = subprocess.run([
            compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            str(path), "-o", str(cls.binary),
        ], text=True, capture_output=True)
        if result.returncode:
            cls.directory.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.binary), str(scenario)], text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_saved_admin_password_protects_the_ap_before_and_after_expansion(self):
        self.run_scenario(0)

    def test_firmware_prefs_file_precedence(self):
        self.run_scenario(1)

    def test_wpa2_length_limits_and_firmware_terminator(self):
        self.run_scenario(2)

    def test_only_printable_ascii_admin_passwords_are_used(self):
        self.run_scenario(3)

    def test_older_stock_prefs_and_truncated_password_fields(self):
        self.run_scenario(4)

    def test_missing_prefs_or_filesystem_falls_back_without_formatting(self):
        self.run_scenario(5)

    def test_failed_seek_or_partial_read_falls_back(self):
        self.run_scenario(6)

    def test_failed_identity_restore_disables_upload_with_either_ap_credential(self):
        self.run_scenario(7)

    def test_unopenable_common_prefs_does_not_use_legacy_password(self):
        self.run_scenario(8)

    def test_operator_instructions_name_the_admin_password(self):
        # Extra guard only: the instructions must not present the published
        # fallback as the bridge's normal Wi-Fi password.
        for path in DOCS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("MeshCore-Migrate", text, path)
            self.assertIn("admin", text[text.index("MeshCore-Migrate"):][:200], path)
            self.assertNotRegex(text, r"\(password\s+`meshcore-migrate`\)", path)
            self.assertNotRegex(text, r"with password\s+`meshcore-migrate`", path)


if __name__ == "__main__":
    unittest.main()
