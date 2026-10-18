#!/usr/bin/env python3
"""Execute the legacy setup portal's production HTTP gate with fake clients."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class WiFiSetupAuthTest(unittest.TestCase):
    def test_setup_http_uses_current_admin_before_reading_or_saving_form(self):
        production = (ROOT / "src/helpers/WiFiSetupPortal.cpp").read_text()
        methods = "\n".join(extract_braced(production, signature) for signature in (
            "static bool readLine(", "static void sendResponse(", "static void handleClient("))
        source = r'''
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>
#include <string>
uint32_t now = 100;
uint32_t millis() { return now; }
void delay(unsigned value) { now += value; }
struct WiFiClient {
  std::string input, output;
  size_t position = 0, bodies = 0;
  explicit WiFiClient(std::string text) : input(text) {}
  bool connected() { return true; }
  bool available() { return position < input.size(); }
  int read() { return available() ? static_cast<unsigned char>(input[position++]) : -1; }
  size_t readBytes(char* out, size_t count) {
    ++bodies; size_t size = std::min(count, input.size() - position);
    memcpy(out, input.data() + position, size); position += size; return size;
  }
  void printf(const char* format, ...) {
    char buffer[2048]; va_list args; va_start(args, format);
    int size = vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    assert(size >= 0 && size < int(sizeof(buffer))); output.append(buffer, size);
  }
  void write(const uint8_t* bytes, size_t count) { output.append(reinterpret_cast<const char*>(bytes), count); }
};
static const uint8_t MESH_HTML_WIFI_SETUP[] = {1, 2, 3};
static const size_t MESH_HTML_WIFI_SETUP_LEN = sizeof(MESH_HTML_WIFI_SETUP);
struct PortalImpl {};
int saves = 0;
void handleSave(PortalImpl*, WiFiClient&, const char* body) {
  assert(!strcmp(body, "ssid=test")); ++saves;
  // Association/persistence cannot overlap a password rotation or upload.
  using namespace mesh::esp32::managementPassword;
  Snapshot current; assert(snapshot(current));
  assert(!publish("unconfirmed"));
  UploadGuard upload(current.generation); assert(!upload.acquired());
}
@METHODS@
WiFiClient request(const std::string& text, int status) {
  WiFiClient client(text); PortalImpl portal;
  handleClient(&portal, client);
  assert(client.output.find("HTTP/1.1 " + std::to_string(status) + " ") == 0);
  assert(client.output.find("Cache-Control: no-store") != std::string::npos);
  return client;
}
std::string get(const std::string& auth) { return "GET / HTTP/1.1\r\n" + auth + "\r\n"; }
std::string post(const std::string& auth, const std::string& body = "ssid=test") {
  return "POST /save HTTP/1.1\r\nContent-Length: 9\r\n" + auth + "\r\n" + body;
}
int main() {
  using namespace mesh::esp32::managementPassword;
  const std::string old = "Authorization: Basic YWRtaW46cGFzc3dvcmQ=\r\n";
  const std::string next = "Authorization: Basic YWRtaW46bmV3LXBhc3N3b3Jk\r\n";
  request(get(old), 503); assert(publish("password"));
  auto prompt = request(get(""), 401);
  assert(prompt.output.find("WWW-Authenticate: Basic realm=\"MeshCore\"") != std::string::npos);
  assert(request(post(""), 401).bodies == 0 && saves == 0);
  assert(request(post("Authorization: Basic wrong\r\n"), 401).bodies == 0 && saves == 0);
  request(get(old), 200); request(post(old), 200); assert(saves == 1);
  assert(publish("new-password"));
  assert(request(post(old), 401).bodies == 0 && saves == 1);
  request(get(next), 200); request(post(next), 200); assert(saves == 2);
  assert(request(post(next + next), 401).bodies == 0 && saves == 2);
  request(get("Authorization: Basic " + std::string(100, 'A') + "\r\n"), 401);
  request(post(next, "short"), 408);
  {
    ChangeGuard change; assert(change.acquired());
    assert(request(post(next), 409).bodies == 0 && saves == 2);
  }
  request("POST /save HTTP/1.1\r\nContent-Length: 256\r\n" + next + "\r\n", 400);
  now += 30000;
  for (unsigned i = 0; i < 5; ++i) request(get("Authorization: Basic wrong\r\n"), 401);
  request(get(next), 429); now += 30000; request(get(next), 200);
}
'''.replace("@METHODS@", methods)
        # The production save helper writes its own success response.
        source = source.replace('assert(!strcmp(body, "ssid=test")); ++saves;',
                                'assert(!strcmp(body, "ssid=test")); ++saves; client.printf("HTTP/1.1 200 OK\\r\\nCache-Control: no-store\\r\\n\\r\\n");')
        source = source.replace('void handleSave(PortalImpl*, WiFiClient&,', 'void handleSave(PortalImpl*, WiFiClient& client,')
        with tempfile.TemporaryDirectory(prefix="wifi-setup-auth-") as temporary:
            work = Path(temporary)
            (work / "test.cpp").write_text(source)
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1", "-I", str(ROOT / "src"),
                str(work / "test.cpp"), "-o", str(work / "test")], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(work / "test")], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
