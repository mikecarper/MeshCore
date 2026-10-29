#pragma once

#include "../../CardputerMesh.h"
#include "../CardputerUITask.h"

class ToolsScreen : public CardputerScreen {
  enum ToolsPage { MenuPage, DiscoverPage };
  enum ToolsMenuItem {
    MeshcoreSeparator,
    AdvertZeroHop,
    AdvertFlood,
    DiscoverRepeaters,
    DeviceSeparator,
    PowerOff,
    Restart,
    Count
  };

  struct DiscoveredRepeater {
    char name[25];
    float snr;
  };

  const char *menu_item_labels[ToolsMenuItem::Count] = {
    "---- RADIO ----",
    "Advert (Zero Hop)",
    "Advert (Flood)",
    "Discover repeaters",
    "---- DEVICE ----",
    "Power off",
    "Restart",
  };

  CardputerUITask *_task;
  mesh::RTCClock *_rtc;

  ToolsPage page = ToolsPage::MenuPage;

  int menu_index = 0;
  bool power_off_pending = false;
  bool power_off_released = false;
  uint32_t power_off_deadline = 0;
  static constexpr uint32_t POWER_OFF_RELEASE_TIMEOUT_MS = 2000;

  DiscoveredRepeater discovered_repeaters[MAX_DISCOVERED_REPEATERS];
  uint16_t discovered_repeaters_count = 0;

public:
  ToolsScreen(CardputerUITask *task, mesh::RTCClock *rtc) : _task(task), _rtc(rtc) {}
  int render(CardputerDisplay &lcd) override;
  void menuItemEnter(ToolsMenuItem item);
  bool handleInput(Keyboard::Event &e) override;
  bool handlePowerOffInput(const Keyboard::Event &e);
  bool isPowerOffPending() const { return power_off_pending; }
  void poll() override;
  void discoverRecv(const mesh::Identity &id, float snr);
  void startDiscover() { menuItemEnter(ToolsMenuItem::DiscoverRepeaters); }

};
