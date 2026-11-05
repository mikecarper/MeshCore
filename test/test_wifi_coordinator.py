import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WiFiCoordinatorTest(unittest.TestCase):
    def test_wifi_coordinator_leases_and_idle_deinit(self):
        harness = r"""
#include <assert.h>
#include <stdint.h>
#include <atomic>
#include <thread>
#include <vector>

#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_MODE_NULL 0
#define WIFI_MODE_STA 1
#define ESP_OK 0
#define ESP_ERR_WIFI_NOT_INIT 0x3001

using wifi_mode_t = int;
using esp_err_t = int;

struct FakeWiFi {
  int current_mode = WIFI_OFF;
  int current_channel = 0;
  bool auto_reconnect = true;
  int disconnects = 0;
  int stops = 0;
  int deinits = 0;
  esp_err_t sdk_get_mode_err = ESP_ERR_WIFI_NOT_INIT;
  wifi_mode_t sdk_mode = WIFI_MODE_NULL;

  int getMode() const { return current_mode; }
  int channel() const { return current_channel; }
  bool mode(int next) {
    current_mode = next;
    return true;
  }
  void setAutoReconnect(bool value) { auto_reconnect = value; }
  void disconnect(bool, bool) { ++disconnects; }
} wifi;

esp_err_t esp_wifi_get_mode(wifi_mode_t* out) {
  if (out) *out = wifi.sdk_mode;
  return wifi.sdk_get_mode_err;
}
esp_err_t esp_wifi_stop() {
  ++wifi.stops;
  return ESP_OK;
}
esp_err_t esp_wifi_deinit() {
  ++wifi.deinits;
  wifi.sdk_get_mode_err = ESP_ERR_WIFI_NOT_INIT;
  return ESP_OK;
}

#include <helpers/esp32/WiFiCoordinator.h>
#include <helpers/esp32/InfrastructureBridgeCoordinator.h>

extern mesh::wifi::Coordinator* otherCoordinator();
extern void otherSetWebHold(bool value);

int main() {
  using namespace mesh::wifi;
  Coordinator& coord = coordinator();
  coord.reset();
  assert(&coord == otherCoordinator());
  assert(!coord.hasAnyLease());
  assert(coord.canDeinitIdleDriver());

  // Cross-TU lease visibility
  otherSetWebHold(true);
  assert(coord.hasStationLease(StationOwner::WebConfig));
  assert(coord.hasAnyStationLease());
  otherSetWebHold(false);
  assert(!coord.hasAnyStationLease());

  // Station leases
  coord.setStationLease(StationOwner::MqttBridge, true);
  assert(coord.hasStationLease(StationOwner::MqttBridge));
  assert(!coord.hasStationLease(StationOwner::WebConfig));
  assert(coord.hasAnyStationLease());
  assert(!coord.canDeinitIdleDriver());

  coord.setStationLease(StationOwner::UdpBridge, true);
  coord.setStationLease(StationOwner::MqttBridge, false);
  assert(!coord.hasStationLease(StationOwner::MqttBridge));
  assert(coord.hasStationLease(StationOwner::UdpBridge));
  assert(!coord.canDeinitIdleDriver());

  coord.setStationLease(StationOwner::UdpBridge, false);
  assert(!coord.hasAnyStationLease());
  assert(coord.canDeinitIdleDriver());

  // Concurrent multi-threaded lease contention across 256 rounds
  const StationOwner owners[] = {StationOwner::Companion,
                                 StationOwner::WebConfig,
                                 StationOwner::Ota};
  for (unsigned round = 0; round < 256; ++round) {
    std::atomic<unsigned> ready{0};
    std::atomic<bool> go{false};
    std::atomic<bool> release[3] = {};
    std::atomic<bool> removed[3] = {};
    std::vector<std::thread> threads;
    for (unsigned index = 0; index < 3; ++index) {
      threads.emplace_back([&, index]() {
        ready.fetch_add(1, std::memory_order_release);
        while (!go.load(std::memory_order_acquire)) std::this_thread::yield();
        coord.setStationLease(owners[index], true);
        ready.fetch_add(1, std::memory_order_release);
        while (!release[index].load(std::memory_order_acquire)) {
          std::this_thread::yield();
        }
        coord.setStationLease(owners[index], false);
        removed[index].store(true, std::memory_order_release);
      });
    }
    while (ready.load(std::memory_order_acquire) != 3U) std::this_thread::yield();
    go.store(true, std::memory_order_release);
    while (ready.load(std::memory_order_acquire) != 6U) std::this_thread::yield();
    assert(coord.hasAnyStationLease());
    for (unsigned index = 0; index < 3; ++index) {
      release[index].store(true, std::memory_order_release);
      while (!removed[index].load(std::memory_order_acquire)) {
        std::this_thread::yield();
      }
      if (index < 2) assert(coord.hasAnyStationLease());
    }
    for (auto& thread : threads) thread.join();
    assert(!coord.hasAnyStationLease());
  }

  // AP and ESP-NOW leases
  coord.setAccessPointLease(AccessPointOwner::WebConfigSetup, true);
  assert(coord.hasAccessPointLease(AccessPointOwner::WebConfigSetup));
  assert(coord.hasAnyAccessPointLease());
  assert(!coord.canDeinitIdleDriver());
  coord.setAccessPointLease(AccessPointOwner::WebConfigSetup, false);

  coord.setEspNowLease(EspNowOwner::Bridge, true);
  assert(coord.hasEspNowLease(EspNowOwner::Bridge));
  assert(coord.hasAnyEspNowLease());
  assert(!coord.canDeinitIdleDriver());
  coord.setEspNowLease(EspNowOwner::Bridge, false);

  // Infrastructure bridge ownership arbitration
  assert(infrastructureWebConfigOwnsStation(false, false, false));
  assert(!infrastructureWebConfigOwnsStation(true, false, false));
  assert(!infrastructureWebConfigOwnsStation(false, true, false));
  assert(!infrastructureWebConfigOwnsStation(false, false, true));

  // Unconfigured setup suspension helper
  bool suspended_flag = false;
  int espnow_stops = 0;
  int mqtt_stops = 0;
  suspendUnconfiguredBridgesForSetup(
      true, suspended_flag, [&]() { ++espnow_stops; }, [&]() { ++mqtt_stops; });
  assert(!suspended_flag && espnow_stops == 0 && mqtt_stops == 0);

  suspendUnconfiguredBridgesForSetup(
      false, suspended_flag, [&]() { ++espnow_stops; }, [&]() { ++mqtt_stops; });
  assert(suspended_flag && espnow_stops == 1 && mqtt_stops == 1);

  // Idle driver deinit helper
  assert(!canDeinitInfrastructureIdleWiFi(true, false, false, false, false, false, false));
  assert(!canDeinitInfrastructureIdleWiFi(false, true, false, false, false, false, false));
  assert(canDeinitInfrastructureIdleWiFi(false, false, false, false, false, false, false));

  wifi.current_mode = WIFI_STA;
  wifi.sdk_get_mode_err = ESP_OK;
  wifi.sdk_mode = WIFI_MODE_STA;
  assert(deinitIdleWiFiDriver(
      wifi, WIFI_OFF, WIFI_STA, ESP_OK,
      esp_wifi_get_mode, esp_wifi_stop, esp_wifi_deinit));
  assert(wifi.current_mode == WIFI_OFF);
  assert(!wifi.auto_reconnect);
  assert(wifi.disconnects == 1);
  assert(wifi.stops == 1);
  assert(wifi.deinits == 1);
  return 0;
}
"""
        other_tu = r"""
#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_MODE_NULL 0
#define WIFI_MODE_STA 1
#define ESP_OK 0
#include <helpers/esp32/WiFiCoordinator.h>
mesh::wifi::Coordinator* otherCoordinator() {
  return &mesh::wifi::coordinator();
}
void otherSetWebHold(bool value) {
  mesh::wifi::coordinator().setStationLease(mesh::wifi::StationOwner::WebConfig, value);
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            main_cpp = work / "main.cpp"
            other_cpp = work / "other.cpp"
            binary = work / "wifi_coordinator"
            main_cpp.write_text(harness, encoding="ascii")
            other_cpp.write_text(other_tu, encoding="ascii")
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-pthread",
                    "-I",
                    str(ROOT / "src"),
                    str(main_cpp),
                    str(other_cpp),
                    "-o",
                    str(binary),
                ],
                text=True,
                check=True,
            )
            subprocess.run([str(binary)], check=True)

    def test_mqtt_bridge_does_not_suspend_or_turn_off_wifi_on_outage(self):
        mqtt_h = (ROOT / "src/helpers/bridges/MQTTBridge.h").read_text(encoding="utf-8")
        mqtt_cpp = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text(encoding="utf-8")
        self.assertNotIn("WiFiNetworkPowerPolicy", mqtt_h)
        self.assertNotIn("serviceNetworkPower", mqtt_h)
        self.assertNotIn("isNetworkSuspended", mqtt_h)
        self.assertNotIn("serviceNetworkPower", mqtt_cpp)
        self.assertNotIn("suspending background WiFi for 30 minutes", mqtt_cpp)


if __name__ == "__main__":
    unittest.main()
