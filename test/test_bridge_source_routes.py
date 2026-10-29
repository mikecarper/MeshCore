"""Exercise per-sender bridge routing with the real parser and storage writers."""
import unittest
from unittest.mock import patch

import test_bridge_ingress_settings as settings


HARNESS = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "BridgeIngressSettings.h"
#if !MESH_ENABLE_BRIDGE_INGRESS
#error "Raw bridge transport flags must activate settings before CommonCLI.h"
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include "AtomicFileWriter.h"
#endif
#include "settings_under_test.cpp"

int main() {
  using namespace mesh::bridge;
  MemoryFS fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs.rename_replaces = true;
#endif
  const char* path = "/bridge_routes";
  const std::vector<uint8_t> legacy = {9, 8, 7, 6, 5};
  fs.files["/com_prefs"] = legacy;
  loadIngressSettings(&fs);
  assert(fs.files.size() == 1);
  auto route = ingressRoute();
  assert(route.profile == IngressProfile::Radio && !route.explicit_profile);
  char reply[160];
  auto run = [&](const char* command) {
    strcpy(reply, "untouched");
    return handleIngressCommand(command, reply, sizeof(reply));
  };
  auto okay = [&](const char* command) {
    assert(run(command) && !strncmp(reply, "OK", 2));
  };
  auto rejects = [&](const char* command) {
    assert(run(command) && !strncmp(reply, "Error:", 6));
  };
  okay("set bridge.ingress off");
  assert(fs.files.at("/bridge_ingress")[3] == 2); // legacy v1 remains readable
  assert(ingressRoute().profile == IngressProfile::Off);
  assert(run("get bridge.ingress") && !strcmp(reply, "> off"));

  IngressSource media_mac, usa_mac, media_udp, usa_udp, wrong_port;
  assert(parseIngressSource("mac:02:10:20:30:40:50", media_mac));
  assert(parseIngressSource("mac:02:10:20:30:40:51", usa_mac));
  assert(parseIngressSource("udp:192.168.1.50:4404", media_udp));
  assert(parseIngressSource("udp:192.168.1.51:4404", usa_udp));
  assert(parseIngressSource("udp:192.168.1.50:4405", wrong_port));
  okay("set bridge.route.1 mac:02:10:20:30:40:50 radio2");
  okay("set bridge.route.2 mac:02:10:20:30:40:51 radio");
  okay("set bridge.route.3 udp:192.168.1.50:4404 radio2");
  okay("set bridge.route.4 udp:192.168.1.51:4404 radio");
  for (const auto* source : {&media_mac, &media_udp}) {
    route = ingressRoute(source);
    assert(route.profile == IngressProfile::Radio2 && route.explicit_profile);
  }
  for (const auto* source : {&usa_mac, &usa_udp}) {
    route = ingressRoute(source);
    assert(route.profile == IngressProfile::Radio && route.explicit_profile);
  }
  route = ingressRoute(&wrong_port);
  assert(route.profile == IngressProfile::Off && !route.explicit_profile);
  assert(run("get bridge.route") && strstr(reply, "4/8"));
  assert(run("get bridge.route.1") && !strcmp(reply, "> 1 mac:02:10:20:30:40:50 radio2"));
  assert(run("get bridge.route.3") && !strcmp(reply, "> 3 udp:192.168.1.50:4404 radio2"));
  const auto good = fs.files.at(path);
  assert(good.size() == RoutesImageSize);
  loadIngressSettings(&fs);
  assert(ingressRoute(&media_mac).profile == IngressProfile::Radio2);
  assert(ingressRoute(&usa_udp).profile == IngressProfile::Radio);
  assert(fs.files.at("/com_prefs") == legacy);

  for (const char* bad : {
      "set bridge.route.0 mac:02:10:20:30:40:50 radio", "set bridge.route.9 mac:02:10:20:30:40:50 radio",
      "set bridge.route.1 mac:02:10:20:30:40:50 both", "set bridge.route.1 mac:02:10:20:30:40:50 radio junk",
      "set bridge.route.5 mac:02:10:20:30:40:50 radio", "set bridge.route.1 mac:FF:FF:FF:FF:FF:FF radio",
      "set bridge.route.1 mac:00:00:00:00:00:00 radio", "set bridge.route.1 udp:0.0.0.0:4404 radio",
      "set bridge.route.1 udp:224.1.2.3:4404 radio", "set bridge.route.1 udp:192.168.1.50:0 radio",
      "set bridge.route.1 udp:192.168.1.50:65536 radio", "set bridge.route.1 udp:192.168.1.500:4404 radio",
      "get bridge.route.1 extra", "del bridge.route.1 extra", "set bridge.route.1"}) {
    rejects(bad);
    assert(fs.files.at(path) == good && ingressRoute(&media_mac).profile == IngressProfile::Radio2);
  }
  assert(!run("get bridge.router") && !strcmp(reply, "untouched"));
  assert(!run("set bridge.routes.1 mac:02:10:20:30:40:50 radio"));
  char bounded[] = {'x', 'x', 'x', 'z'};
  assert(handleIngressCommand("get bridge.route.1", bounded, 3));
  assert(bounded[2] == 0 && bounded[3] == 'z');

  fs.fail_write = true;
  rejects("set bridge.route.1 mac:02:10:20:30:40:50 radio");
  assert(fs.files.at(path) == good && ingressRoute(&media_mac).profile == IngressProfile::Radio2);
  fs.fail_write = false;
  fs.fail_write_after = 7;
  rejects("del bridge.route.1");
  assert(fs.files.at(path) == good && ingressRoute(&media_mac).profile == IngressProfile::Radio2);
  fs.fail_write_after = -1;

  // Every corrupt or incomplete routes image blocks matched AND unmatched inputs.
  for (size_t bit = 0; bit < good.size() * 8; ++bit) {
    fs.files[path] = good;
    fs.files[path][bit / 8] ^= uint8_t(1U << (bit % 8));
    loadIngressSettings(&fs);
    assert(ingressRoute(&media_mac).profile == IngressProfile::Blocked);
    assert(ingressRoute(&wrong_port).profile == IngressProfile::Blocked);
  }
  rejects("set bridge.route.1 mac:02:10:20:30:40:50 radio2");
  okay("clear bridge.routes");
  assert(ingressRoute(&media_mac).profile == IngressProfile::Off);
  for (size_t size = 0; size < good.size(); ++size) {
    fs.files[path] = std::vector<uint8_t>(good.begin(), good.begin() + size);
    loadIngressSettings(&fs);
    assert(ingressRoute().profile == IngressProfile::Blocked);
  }
  okay("clear bridge.routes");
  fs.files[path] = good;
  loadIngressSettings(&fs);

  // A write can publish before its readback fails. Block until storage reloads.
  fs.fail_after_publication = true;
  rejects("set bridge.route.1 mac:02:10:20:30:40:50 off");
  assert(ingressRoute(&media_mac).profile == IngressProfile::Blocked);
  fs.fail_after_publication = false;
  fs.fail_read_open = false;
  rejects("clear bridge.routes");
  loadIngressSettings(&fs);
  route = ingressRoute(&media_mac);
  assert(route.profile == IngressProfile::Off && route.explicit_profile);
  okay("del bridge.route.1");
  route = ingressRoute(&media_mac);
  assert(route.profile == IngressProfile::Off && !route.explicit_profile);
  okay("clear bridge.routes");
  okay("set bridge.ingress radio2");
  assert(fs.files.at("/bridge_ingress")[3] == 1);
  assert(ingressRoute().profile == IngressProfile::Radio2);
  okay("set bridge.ingress radio");
  fs.files.erase(path);
  loadIngressSettings(&fs);
  assert(ingressRoute().profile == IngressProfile::Radio && !ingressRoute().explicit_profile);
  assert(fs.files.at("/com_prefs") == legacy);
}
'''


class BridgeSourceRouteTests(unittest.TestCase):
    def test_saved_source_routes_and_faults_across_filesystems(self):
        # Reuse the real filesystem/writer fixture and its eight transport /
        # platform builds. Only the test main changes; production code is copied.
        with patch.object(settings, "HARNESS", HARNESS):
            settings.BridgeIngressSettingsTests().test_real_settings_and_transactions_across_filesystems()


if __name__ == "__main__":
    unittest.main()
