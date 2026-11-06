using mesh::AGCMaintenanceResult;
namespace radioDirection = mesh::radioDirection;

struct HostClock {
  uint32_t getMillis() const { return now_ms; }
};

struct Dispatcher {
  struct { uint8_t agc_reset_interval = 0; } _prefs;
  RadioLibWrapper* _radio;
  HostClock clock;
  HostClock* _ms = &clock;
  mesh::AGCResetScheduler agc_reset, old_agc_reset;
  @LAB_TIMER_REVISION@
  bool adaptive_mode = false, recovered_this_loop = false, radio_available = true;
  explicit Dispatcher(RadioLibWrapper& radio) : _radio(&radio) {}
  bool useAdaptiveAGC() const { return adaptive_mode; }
  @SAVED_INTERVAL@
  bool millisHasNowPassed(unsigned long timestamp) const;
  void service() {
    @CONFIGURE@
    @SERVICE@
  }
  bool oldMaintenanceWake(uint32_t& delay_millis) const {
    const uint32_t now = _ms->getMillis();
    bool found = false;
    uint32_t shortest_delay = 0;
    @OLD_WAKE@
    if (found) delay_millis = shortest_delay;
    return found;
  }
};

@CLOCK_GUARD@
