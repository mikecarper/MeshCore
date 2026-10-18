"""Run the production lightweight HTTP parser/upload gate with in-memory sockets."""
from pathlib import Path
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST 1
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <string>
#include <strings.h>
uint32_t clock_ms = 100;
uint32_t millis() { return clock_ms; }
void delay(unsigned time) { clock_ms += time; }
int reboots = 0;
void esp_restart() { ++reboots; }
constexpr int U_FLASH = 0;
struct WiFiClient {
  std::string input, output;
  size_t position = 0;
  bool open = true;
  explicit WiFiClient(const std::string& wire) : input(wire) {}
  bool connected() const { return open; }
  int available() const { return input.size() - position; }
  int read() { return available() ? uint8_t(input[position++]) : -1; }
  int read(uint8_t* out, size_t count) {
    count = count < size_t(available()) ? count : available();
    memcpy(out, input.data() + position, count); position += count; return count;
  }
  void print(const char* value) { output += value; }
  void printf(const char* format, ...) {
    char text[512]; va_list args; va_start(args, format);
    int count = vsnprintf(text, sizeof(text), format, args); va_end(args);
    assert(count >= 0 && size_t(count) < sizeof(text)); output.append(text, count);
  }
  size_t write(const uint8_t* value, size_t count) { output.append(reinterpret_cast<const char*>(value), count); return count; }
  void stop() { open = false; }
};
struct ESP32Board { void setInhibitSleep(bool) {} } board;
struct UpdateFake {
  int begins = 0, writes = 0, ends = 0, aborts = 0;
  bool begin(size_t, int) { ++begins; return true; }
  size_t write(uint8_t*, size_t count) { ++writes; return count; }
  void abort() { ++aborts; }
  bool end() { ++ends; return true; }
  const char* errorString() { return "fixture"; }
} Update;
class LightweightOTAServer {
public:
  bool running = true;
  ESP32Board* board = &::board;
  int pages = 0, logs = 0;
  void sendPage(WiFiClient& client) { ++pages; client.print("HTTP/1.1 200 OK"); }
  void sendLog(WiFiClient& client) { ++logs; client.print("HTTP/1.1 200 OK"); }
@METHODS@
};
int main() {
  using namespace mesh::esp32::managementPassword;
  LightweightOTAServer server;
  auto request = [&](const char* method, const char* path, const std::string& headers, const std::string& body = "") {
    WiFiClient client(std::string(method) + " " + path + " HTTP/1.1\r\n" + headers + "\r\n" + body);
    server.handleClient(client); return client.output;
  };
  assert(request("GET", "/update", "").find("503 Service Unavailable") != std::string::npos);
  assert(publish("password"));
  const std::string good = "Authorization: Basic YWRtaW46cGFzc3dvcmQ=\r\n";
  for (const char* path : {"/", "/update", "/log"}) {
    const auto absent = request("GET", path, "");
    assert(absent.find("401 Unauthorized") != std::string::npos
        && absent.find("WWW-Authenticate: Basic realm=\"MeshCore admin\", charset=\"UTF-8\"") != std::string::npos);
    assert(request("GET", path, "Authorization: Basic wrong\r\n").find("401 Unauthorized") != std::string::npos);
    assert(request("GET", path, good).find("200 OK") != std::string::npos);
  }
  assert(server.pages == 2 && server.logs == 1 && Update.begins == 0);
  assert(request("GET", "/update", "Authorization:\r\n" + good).find("401 Unauthorized") != std::string::npos);
  assert(request("GET", "/update", std::string("Authorization: ") + std::string(100, 'x') + "\r\n").find("401 Unauthorized") != std::string::npos);
  const std::string payload(8, '\0');
  assert(request("POST", "/update", "Content-Length: 8\r\n", payload).find("401 Unauthorized") != std::string::npos);
  assert(Update.begins == 0 && Update.writes == 0 && reboots == 0);
  assert(publish("different"));
  assert(request("GET", "/log", good).find("401 Unauthorized") != std::string::npos);
  const std::string updated = "Authorization: Basic YWRtaW46ZGlmZmVyZW50\r\n";
  assert(request("GET", "/log", updated).find("200 OK") != std::string::npos);
  {
    ChangeGuard changing; assert(changing.acquired());
    assert(request("POST", "/update", updated + "Content-Length: 8\r\n", payload).find("409 Conflict") != std::string::npos);
    assert(!uploadActive() && Update.begins == 0);
  }
  assert(request("POST", "/update", updated + "Content-Length: 8\r\n", payload).find("200 OK") != std::string::npos);
  assert(Update.begins == 1 && Update.writes == 1 && Update.ends == 1 && reboots == 1 && !uploadActive());
}
'''


class LightweightOtaAuthTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def test_http_routes_and_upload_admission_use_committed_admin(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "  static void sendResponse(", "  bool readLine(", "  void sendUpdateError(",
            "  void receiveUpdate(", "  void handleClient("))
        fixture = FIXTURE.replace("@METHODS@", methods)
        self.compile_and_run(fixture)
        gate = "    if (auth != mesh::esp32::managementPassword::AuthResult::Authorized) {"
        old = fixture.replace(gate, "    if (false) {")
        self.assertNotEqual(old, fixture)
        with self.assertRaises(AssertionError):
            self.compile_and_run(old)


if __name__ == "__main__":
    unittest.main()
