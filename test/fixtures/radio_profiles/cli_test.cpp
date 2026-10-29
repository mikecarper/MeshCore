#include <Arduino.h>
#include <helpers/RadioProfileCLI.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <algorithm>

struct Clock : mesh::RTCClock {
  uint32_t epoch = 1700000000;
  uint32_t getCurrentTime() override { return epoch; }
  void setCurrentTime(uint32_t time) override { epoch = time; }
};
struct Radio : mesh::Radio {
  mesh::RadioProfiles p;
  float minimum_frequency = 137;
  Radio() { p.primary.freq=909.5; p.primary.bw=62.5; p.primary.sf=7; p.primary.cr=5; }
  mesh::RadioProfiles* profiles() override { return &p; }
  const mesh::RadioProfiles* profiles() const override { return &p; }
  bool validateProfile(const mesh::RadioProfileParams& v) const override { return mesh::RadioProfiles::valid(v) && v.freq>=minimum_frequency && v.bw<=500; }
  uint16_t profilePreamble(uint8_t profile) const override { return p.preamble(profile, 32); }
  int recvRaw(uint8_t*,int) override { return 0; }
  uint32_t getEstAirtimeFor(int) override { return 10; }
  float packetScore(float,int) override { return 0; }
  bool startSendRaw(const uint8_t*,int) override { return true; }
  bool isSendComplete() override { return true; }
  void onSendFinished() override {}
  bool isInRecvMode() const override { return true; }
};
struct Fixture {
  MemoryFS fs;
  Clock clock;
  Radio radio;
  mesh::RadioProfileCLI cli;
  char reply[160];
  Fixture(bool infrastructure = false) { g_mock_millis=0; cli.begin(&fs,&radio,&clock,infrastructure); }
  const char* cmd(const char* text, bool ok=true) {
    memset(reply,0x55,sizeof(reply));
    assert(cli.handle(text,reply,sizeof(reply)));
    assert(memchr(reply,0,sizeof(reply)));
    if (strncmp(text,"get ",4) && strncmp(text,"del ",4)) {
      if ((!strncmp(reply,"OK",2))!=ok) { fprintf(stderr,"%s -> %s\n",text,reply); assert(false); }
    }
    return reply;
  }
  void advance(uint32_t ms, bool epoch=true) { g_mock_millis+=ms; if(epoch)clock.epoch+=ms/1000; cli.loop(); }
  void finishTimingTest(uint32_t switch_us=545) {
    const uint32_t start = g_mock_millis - mesh::RadioProfiles::SwitchRecommendationWindowMs;
    for (unsigned i=0;i<mesh::RadioProfiles::SwitchTestSamplesPerDirection;++i) {
      radio.p.sampleSwitch(0,1,switch_us,start+i);
      radio.p.sampleSwitch(1,0,switch_us,start+i);
    }
    radio.p.sampleSwitch(0,1,switch_us,g_mock_millis);
    assert(radio.p.switchTestReady());
    assert(radio.p.recommendationReady());
  }
};
static void permanentScheduleOrder() {
  for (bool fail_first_save : {false, true}) {
    Fixture f;
    char command[160];
    snprintf(command, sizeof(command), "set radioat2 912.5,500,8,5,rx,%lu,80",
        (unsigned long)(f.clock.epoch + 120));
    f.cmd(command);
    snprintf(command, sizeof(command), "set radioat2 910.5,500,8,5,rx,%lu,80",
        (unsigned long)(f.clock.epoch + 60));
    f.cmd(command);
    f.fs.fail_write = fail_first_save;
    f.advance(120000); // both are due after sleep or a forward clock correction
    if (fail_first_save) {
      f.fs.fail_write = false;
      f.advance(60000);
    }
    assert(f.radio.p.secondary.params.freq == 912.5f);
    Radio reboot; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &reboot, &f.clock);
    assert(reboot.p.secondary.params.freq == 912.5f);
    assert(!strcmp(f.cmd("get radioat2 all"), "> slots: "));
  }
}

static void adjacentTemporarySchedules() {
  for (bool later_first : {false, true}) {
    Fixture f;
    const uint32_t epoch = f.clock.epoch;
    for (unsigned i = 0; i < 2; ++i) {
      const bool later = later_first ? i == 0 : i == 1;
      char command[160];
      snprintf(command, sizeof(command), "set tempradioat2 %.1f,500,8,5,rxtx,%lu,%lu,80",
          later ? 912.5 : 910.5, (unsigned long)(epoch + (later ? 60 : 1)),
          (unsigned long)(epoch + (later ? 120 : 60)));
      f.cmd(command);
    }
    f.advance(1000);
    assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 910.5f);
    f.advance(59000);
    assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 912.5f);
    f.advance(59000);
    assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 912.5f);
    f.advance(1000);
    assert(!f.radio.p.enabled());
  }
}

static void scheduledLeaseStartsAtCommandTime() {
  for (bool rollover : {false, true}) {
    Fixture f;
    if (rollover) { g_mock_millis = UINT32_MAX - 30000; f.cli.loop(); }
    g_mock_millis += 10000; f.clock.epoch += 10; // command arrives between loop calls
    char command[160];
    snprintf(command, sizeof(command), "set tempradioat2 910.5,500,8,5,rx,%lu,%lu,80",
        (unsigned long)(f.clock.epoch + 1), (unsigned long)(f.clock.epoch + 60));
    f.cmd(command);
    f.advance(1000);
    assert(f.radio.p.secondary_temporary);
    f.advance(49000);
    assert(f.radio.p.secondary_temporary); // idle time before the command is not charged to it
    f.advance(9000);
    assert(f.radio.p.secondary_temporary);
    f.advance(1000);
    assert(!f.radio.p.enabled());
  }
}

static void schedulePermutations() {
  int order[] = {0, 1, 2, 3};
  do {
    for (bool temporary : {false, true}) for (bool late : {false, true}) {
      Fixture f;
      const uint32_t epoch = f.clock.epoch;
      for (int entry : order) {
        char command[160];
        if (temporary) {
          snprintf(command, sizeof(command), "set tempradioat2 %.1f,500,8,5,rx,%lu,%lu,80",
              910.5 + entry, (unsigned long)(epoch + 1 + entry * 60),
              (unsigned long)(epoch + 61 + entry * 60));
        } else {
          snprintf(command, sizeof(command), "set radioat2 %.1f,500,8,5,rx,%lu,80",
              910.5 + entry, (unsigned long)(epoch + (entry + 1) * 60));
        }
        f.cmd(command);
      }
      if (late) {
        f.advance(temporary ? 181000 : 240000);
        assert(f.radio.p.secondary.params.freq == 913.5f);
      } else {
        for (int entry = 0; entry < 4; ++entry) {
          f.advance(temporary && entry == 0 ? 1000 : 60000);
          assert(f.radio.p.secondary.params.freq == 910.5f + entry);
        }
      }
      assert(f.radio.p.secondary_temporary == temporary);
      if (temporary) { f.advance(60000); assert(!f.radio.p.enabled()); }
    }
  } while (std::next_permutation(order, order + 4));
}

static void storageBackoffDoesNotDelayTemporaryWindows() {
  Fixture f;
  f.cmd("set radio2 910.5,500,8,5,rx,80"); f.advance(2000);
  char command[160];
  snprintf(command, sizeof(command), "set radioat2 912.5,500,8,5,rx,%lu,80", (unsigned long)(f.clock.epoch + 2));
  f.cmd(command);
  snprintf(command, sizeof(command), "set tempradioat2 911.5,500,8,5,rx,%lu,%lu,80",
      (unsigned long)(f.clock.epoch + 30), (unsigned long)(f.clock.epoch + 90));
  f.cmd(command);
  f.fs.fail_write = true;
  f.advance(2000);
  f.advance(28000);
  assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 911.5f);
  f.advance(60000);
  assert(!f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 910.5f);
  f.fs.fail_write = false;
  f.advance(60000);
  assert(f.radio.p.secondary.params.freq == 912.5f);
}

static void scheduledSaveFailureKeepsOriginalTimes() {
  Fixture f;
  char command[160];
  const uint32_t start = f.clock.epoch + 60;
  snprintf(command, sizeof(command), "set radioat2 910.5,500,8,5,rx,%lu,80", (unsigned long)start);
  f.cmd(command);
  f.fs.fail_write = true;
  f.advance(60000);
  char timestamp[32]; snprintf(timestamp, sizeof(timestamp), "@%lu", (unsigned long)start);
  assert(strstr(f.cmd("get radioat2 all"), timestamp));
  f.fs.fail_write = false;
  f.advance(59000);
  assert(!f.radio.p.enabled());
  f.advance(1000);
  assert(f.radio.p.secondary.params.freq == 910.5f);
}

static void relativeSchedules() {
  Fixture f;
  f.cmd("set tempradioat2 910.5,500,8,5,rxtx,+1,+3,80");
  f.advance(59000);
  assert(!f.radio.p.enabled());
  f.advance(1000);
  assert(f.radio.p.secondary_temporary);
  f.advance(119000);
  assert(f.radio.p.secondary_temporary);
  f.advance(1000);
  assert(!f.radio.p.enabled());
  char command[160];
  snprintf(command, sizeof(command), "set tempradioat2 911.5,500,8,5,rxtx,+1,%lu,80",
      (unsigned long)(f.clock.epoch + 120));
  f.cmd(command);
  f.advance(60000);
  assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 911.5f);
  f.advance(60000);
  assert(!f.radio.p.enabled());
  f.cmd("set radioat2 912.5,500,8,5,rx,+1,80");
  f.advance(60000);
  assert(!f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq == 912.5f);
  f.cmd("set radioat2 912.5,500,8,5,rx,+0,80", false);
  f.cmd("set radioat2 912.5,500,8,5,rx,+4294967295,80", false);
  f.cmd("set tempradioat2 912.5,500,8,5,rx,+3,+1,80", false);
}

static const char* spectrum_warning = "WARN radio2 overlaps radio1 (6kHz guard)";

static void expectSpectrumWarning(const char* reply, bool expected = true) {
  if ((strstr(reply, spectrum_warning) != nullptr) != expected) {
    fprintf(stderr, "spectrum warning expected=%d: %s\n", expected, reply);
    assert(false);
  }
}

static float suggestedSpectrumFrequency(const char* reply) {
  expectSpectrumWarning(reply);
  const char* suggestion = strstr(reply, "; try ");
  float frequency = 0;
  assert(suggestion && sscanf(suggestion, "; try %fMHz", &frequency) == 1);
  assert(std::isfinite(frequency));
  return frequency;
}

static void spectrumOverlap() {
  // The warning concerns occupied RF spectrum, regardless of SF/CR or which
  // profile has the wider bandwidth. The 6 kHz guard is an edge-to-edge gap.
  struct Case { float primary_bw, secondary_bw; const char* frequency; bool warn; };
  const Case cases[] = {
      {62.5f, 62.5f, "909.500000", true},
      {62.5f, 62.5f, "909.550000", true},
      {62.5f, 62.5f, "909.450000", true},
      {125, 31.25f, "909.540000", true},  // radio2 contained in radio1
      {31.25f, 125, "909.500000", true},  // radio1 contained in radio2
      {62.5f, 62.5f, "909.562500", true}, // exact occupied-band edge touch
      {62.5f, 62.5f, "909.437500", true},
      {62.5f, 62.5f, "909.565000", true}, // no band overlap; inside guard
      {62.5f, 62.5f, "909.435000", true},
      {62.5f, 62.5f, "909.568500", true}, // configured decimal guard boundary
      {62.5f, 62.5f, "909.431500", true},
      {62.5f, 62.5f, "909.570000", false},
      {62.5f, 62.5f, "909.430000", false},
      {125, 62.5f, "909.599750", true},
      {125, 62.5f, "909.400250", true},
      {125, 62.5f, "909.601000", false},
      {125, 62.5f, "909.399000", false},
  };
  for (const auto& c : cases) {
    Fixture f;
    f.radio.p.primary.bw = c.primary_bw;
    char command[100];
    snprintf(command, sizeof(command), "set radio2 %s,%g,9,7,rx",
             c.frequency, c.secondary_bw);
    expectSpectrumWarning(f.cmd(command), c.warn);
    // A warning cannot reject the request or alter the requested modem tuple.
    f.advance(2000);
    assert(f.radio.p.secondary.mode == mesh::RadioProfileMode::Rx);
    assert(f.radio.p.secondary.params.bw == c.secondary_bw);
    assert(f.radio.p.secondary.params.sf == 9 && f.radio.p.secondary.params.cr == 7);
    expectSpectrumWarning(f.cmd("get radio2"), c.warn);
    if (c.warn) {
      const float suggestion = suggestedSpectrumFrequency(f.reply);
      const float requested = strtof(c.frequency, nullptr);
      if (requested < f.radio.p.primary.freq) assert(suggestion < f.radio.p.primary.freq);
      else assert(suggestion > f.radio.p.primary.freq); // same center chooses above
      snprintf(command, sizeof(command), "set radio2 %.3f,%g,9,7,rx", suggestion, c.secondary_bw);
      expectSpectrumWarning(f.cmd(command), false); // suggested tuple must be safe
      f.advance(2000);
      assert(f.radio.p.secondary.params.bw == c.secondary_bw);
      assert(f.radio.p.secondary.params.sf == 9 && f.radio.p.secondary.params.cr == 7);
    } else assert(!strstr(f.reply, "; try "));
  }
  // These actual float centers round to exactly 17,700 Hz of separation.
  // Together with supported 7.8/15.6 kHz bandwidths, they exercise equality
  // under the same units users set, rather than a decimal approximation.
  for (bool below : {false, true}) for (bool outside : {false, true}) {
    Fixture f;
    f.radio.p.primary.freq = 900.0f; f.radio.p.primary.bw = 7.8f;
    float frequency = below ? 899.9822998046875f : 900.0177001953125f;
    if (outside) frequency = std::nextafter(frequency, below ? -INFINITY : INFINITY);
    const auto distance = llabs(llround(double(frequency) * 1000000) - 900000000LL);
    assert(distance == (outside ? 17761 : 17700));
    char command[100];
    snprintf(command, sizeof(command), "set radio2 %.9f,15.6,9,7,rx", frequency);
    expectSpectrumWarning(f.cmd(command), !outside);
  }
  // 10.4f is slightly below decimal 10.4: truncating the computed guard
  // would lose a whole Hz and miss this inclusive 42,450 Hz boundary.
  for (bool outside : {false, true}) {
    Fixture f;
    f.radio.p.primary.freq = 150.0f; f.radio.p.primary.bw = 10.4f;
    float frequency = 150.042449951171875f;
    if (outside) frequency = std::nextafter(frequency, INFINITY);
    assert(llround(double(frequency) * 1000000) - 150000000LL
           == (outside ? 42465 : 42450));
    // The compact CLI decimal parser accumulates digits in float. These
    // short inputs produce the exact stored floats whose boundary we test.
    const char* text = outside ? "150.04247" : "150.04246";
    float parsed = 0;
    assert(mesh::cli::parseDecimalStrict(text, parsed) && parsed == frequency);
    char command[100];
    snprintf(command, sizeof(command), "set radio2 %s,62.5,8,7,rx", text);
    expectSpectrumWarning(f.cmd(command), !outside);
  }
  {
    Fixture f;
    f.radio.p.primary.freq = 910.525f;
    f.cmd("set radio2 910.525,62.5,8,7,rx");
    assert(strstr(f.reply, "; try 910.594MHz"));
    expectSpectrumWarning(f.cmd("set radio2 910.594,62.5,8,7,rx"), false);
  }
  for (const char* frequency : {"909.500000", "909.450000"}) {
    Fixture f;
    char command[100];
    snprintf(command, sizeof(command), "set radio2 %s,62.5,8,7,rx", frequency);
    f.cmd(command);
    // Nearest outward whole-kHz center, with the requested bandwidth retained.
    assert(strstr(f.reply, !strcmp(frequency, "909.500000")
                              ? "; try 909.569MHz" : "; try 909.431MHz"));
  }
  {
    Fixture f;
    f.radio.p.primary.bw = 125;
    f.cmd("set radio2 909.540,31.25,8,7,rx");
    assert(strstr(f.reply, "; try 909.585MHz"));
  }
  for (float minimum : {137.0f, 150.0f}) {
    Fixture f;
    // Cover the shared parser limit and a stricter physical-radio limit.
    f.radio.minimum_frequency = minimum;
    f.radio.p.primary.freq = minimum + 0.01f; f.radio.p.primary.bw = 31.25f;
    char command[100];
    snprintf(command, sizeof(command), "set radio2 %.3f,31.25,8,7,rx", minimum + 0.001f);
    f.cmd(command);
    const float frequency = suggestedSpectrumFrequency(f.reply);
    assert(frequency > f.radio.p.primary.freq); // lower suggestion violates radio limit
    snprintf(command, sizeof(command), "set radio2 %.3f,31.25,8,7,rx", frequency);
    expectSpectrumWarning(f.cmd(command), false);
  }
  {
    Fixture f;
    expectSpectrumWarning(f.cmd("set radio2 909.5,62.5,7,5,rxtx"));
    const char* warning = strstr(f.reply, spectrum_warning);
    const char* timing = strstr(f.reply, "timing self-test pending");
    assert(timing && warning < timing); // warning is visible before calibration
    assert(!f.radio.p.enabled());       // the normal 2-second delay is retained
    expectSpectrumWarning(f.cmd("get radio2"));
    f.advance(2000);
    f.finishTimingTest();
    expectSpectrumWarning(f.cmd("get radio2"));
    assert(!strstr(f.reply, "timing self-test pending"));
    expectSpectrumWarning(f.cmd("set radio2 off"), false);
    expectSpectrumWarning(f.cmd("get radio2"), false);
    expectSpectrumWarning(f.cmd("set radio2 909.5,63,7,5,rx", false), false);
    expectSpectrumWarning(f.cmd("set radio2 909.5,62.5,13,5,rx", false), false);
  }
  {
    Fixture f;
    f.fs.fail_write = true;
    expectSpectrumWarning(f.cmd("set radio2 909.5,62.5,7,5,rx", false), false);
    assert(!f.radio.p.enabled());
    assert(!f.cli.hasReplyMutation());
  }
  {
    Fixture f;
    f.cmd("set radio2 911.5,62.5,7,5,rx"); f.advance(2000);
    expectSpectrumWarning(f.cmd("set tempradio2 909.5,62.5,8,7,rxtx,1"));
    assert(f.radio.p.secondary.params.freq == 911.5f);
    expectSpectrumWarning(f.cmd("get tempradio2")); // pending temporary tuple
    expectSpectrumWarning(f.cmd("get radio2"), false); // saved tuple is separate
    f.advance(2000);
    expectSpectrumWarning(f.cmd("get tempradio2"));
    assert(f.radio.p.secondary_temporary);
    assert(f.radio.p.secondary.params.freq == 909.5f);
    f.advance(60000);
    expectSpectrumWarning(f.cmd("get tempradio2"), false);
    assert(f.radio.p.secondary.params.freq == 911.5f);
  }
  for (bool temporary : {false, true}) {
    Fixture f;
    const char* set = temporary ? "set tempradioat2 909.5,62.5,8,7,rx,+1,+2"
                                : "set radioat2 909.5,62.5,8,7,rx,+1";
    const char* get = temporary ? "get tempradioat2 1" : "get radioat2 1";
    expectSpectrumWarning(f.cmd(set));
    expectSpectrumWarning(f.cmd(get));
    if (temporary) {
      char reply[158]; memset(reply, 0x55, sizeof(reply));
      assert(f.cli.handle(get, reply, 157));
      assert(static_cast<unsigned char>(reply[157]) == 0x55);
      assert(memchr(reply, 0, 157));
      char timestamps[48];
      snprintf(timestamps, sizeof(timestamps), "@%lu-%lu",
               (unsigned long)(f.clock.epoch + 60),
               (unsigned long)(f.clock.epoch + 120));
      assert(strstr(reply, timestamps));
      expectSpectrumWarning(reply);
      assert(strstr(reply, "; try 909.569MHz"));
    }
    assert(!f.radio.p.enabled()); // warning does not activate a future schedule
    f.advance(60000);
    assert(f.radio.p.secondary.params.freq == 909.5f);
    assert(f.radio.p.secondary_temporary == temporary);
  }
  for (bool temporary : {false, true}) for (bool delivered : {false, true}) {
    Fixture f(true);
    f.cmd("set radio2 911.5,62.5,7,5,rx"); f.advance(2000);
    const auto saved = f.fs.files["/radio_profiles"];
    f.cli.beginReplyCommand();
    expectSpectrumWarning(f.cmd(temporary ? "set tempradio2 909.5,62.5,8,7,rxtx,1"
                                         : "set radio2 909.5,62.5,8,7,rxtx"));
    f.cli.endReplyCommand();
    assert(f.cli.hasReplyMutation());
    assert(f.fs.files["/radio_profiles"] == saved);
    f.advance(2000);
    assert(f.radio.p.secondary.params.freq == 911.5f); // old RF retained for ACK
    assert(f.cli.finishReplyMutation(delivered));
    f.advance(1);
    assert(!f.cli.hasReplyMutation());
    assert(f.radio.p.secondary.params.freq == (delivered ? 909.5f : 911.5f));
  }
#if MESH_ENABLE_FLEET_CONTROL
  for (bool temporary : {false, true}) {
    Fixture f(true);
    f.cli.beginReplyCommand();
    expectSpectrumWarning(f.cmd(temporary ? "set tempradioat2 909.5,62.5,8,7,rx,+1,+2"
                                         : "set radioat2 909.5,62.5,8,7,rx,+1"));
    f.cli.endReplyCommand();
    assert(f.cli.hasReplyMutation());
    const char* get = temporary ? "get tempradioat2 1" : "get radioat2 1";
    assert(!strcmp(f.cmd(get), "> off"));
    assert(f.cli.finishReplyMutation(true)); f.advance(1);
    expectSpectrumWarning(f.cmd(get));
    assert(!f.radio.p.enabled());
    f.advance(60000);
    assert(f.radio.p.secondary.params.freq == 909.5f);
  }
#endif
  {
    Fixture f;
    f.cmd("set radio2 909.5,62.5,8,7,rxtx");
    // The public command path must retain terminators and adjacent bytes for
    // every response budget, including the CLI's tagged 157-byte response.
    for (size_t capacity = 0; capacity <= 160; ++capacity) {
      char bytes[162]; memset(bytes, 0x55, sizeof(bytes));
      assert(f.cli.handle("get radio2", bytes, capacity));
      assert(static_cast<unsigned char>(bytes[capacity]) == 0x55);
      if (capacity) assert(memchr(bytes, 0, capacity));
      if (capacity >= 157) expectSpectrumWarning(bytes);
    }
  }
  // Reapply the literal displayed suggestion through the actual CLI parser.
  // Its float fractional accumulation can differ from a double-to-float
  // candidate, especially at high frequencies and near whole-kHz edges.
  unsigned roundtrips = 0;
  for (float base : {150.125f, 433.125f, 868.125f, 915.125f, 2400.125f}) {
    for (float bw : {7.8f, 10.4f, 15.6f, 20.8f, 31.25f,
                     41.7f, 62.5f, 125.0f, 250.0f, 500.0f}) {
      for (float offset : {0.0f, 0.000001f, 0.000499f, 0.000500f, 0.000501f,
                           0.000999f, 0.001000f, 0.001001f, 0.001499f, 0.001501f}) {
        for (bool below : {false, true}) {
          Fixture f;
          f.radio.p.primary.freq = base + offset;
          f.radio.p.primary.bw = bw;
          const float requested = f.radio.p.primary.freq + (below ? -0.001f : 0.001f);
          char command[100];
          snprintf(command, sizeof(command), "set radio2 %.6f,%g,7,5,rx", requested, bw);
          expectSpectrumWarning(f.cmd(command));
          const char* begin = strstr(f.reply, "; try ");
          assert(begin); begin += strlen("; try ");
          const char* end = strstr(begin, "MHz");
          assert(end && end - begin < 24);
          char suggestion[24];
          memcpy(suggestion, begin, end - begin); suggestion[end - begin] = 0;
          const char* decimal = strchr(suggestion, '.');
          assert(decimal && strlen(decimal + 1) == 3);
          snprintf(command, sizeof(command), "set radio2 %s,%g,7,5,rx", suggestion, bw);
          f.cmd(command);
          if (strstr(f.reply, spectrum_warning)) {
            fprintf(stderr, "Unsafe suggestion: primary=%.12f BW=%g requested=%.12f suggestion=%s\n",
                    f.radio.p.primary.freq, bw, requested, suggestion);
          }
          expectSpectrumWarning(f.reply, false);
          ++roundtrips;
        }
      }
    }
  }
  assert(roundtrips == 1000);
}

static void mediaMeshPresets() {
  for (bool infrastructure : {false, true}) {
    for (bool range : {false, true}) {
      Fixture f(infrastructure);
      const auto primary = f.radio.p.primary;
      assert(!f.radio.p.enabled() && f.radio.p.cross == mesh::RadioCrossMode::Off);
      const char* command = range ? "set radio2 mediamesh-range" : "set radio2 mediamesh";
      f.cmd(command);
      assert(!f.radio.p.enabled()); // Same delayed apply as a saved tuple.
      f.advance(2000);
      const auto config = f.radio.p.secondary;
      assert(config.mode == mesh::RadioProfileMode::RxTx);
      assert(fabsf(config.params.freq - 911.150f) < 0.0001f
          && config.params.bw == (range ? 125 : 250));
      assert(config.params.sf == 7 && config.params.cr == 5 && !config.params.preamble);
      assert(config.params.protocol == mesh::RadioProfileProtocol::MeshCore);
      assert(f.radio.p.primary == primary && f.radio.p.cross == mesh::RadioCrossMode::Off);
      assert(!f.fs.files["/radio_profiles"][14] && !f.fs.files["/radio_profiles"][15]);
      Radio reboot; mesh::RadioProfileCLI restored;
      restored.begin(&f.fs, &reboot, &f.clock, infrastructure);
      assert(reboot.p.secondary.params == config.params);
      assert(reboot.p.secondary.mode == mesh::RadioProfileMode::RxTx);
      assert(reboot.p.cross == mesh::RadioCrossMode::Off);
      // A preset replaces an old preamble override but retains operator crossover.
      f.cmd("set radio2.cross on");
      f.cmd("set radio2 912.5,500,8,5,rxtx,80"); f.advance(2000);
      f.cmd(command); f.advance(2000);
      assert(!f.radio.p.secondary.params.preamble && f.radio.p.cross == mesh::RadioCrossMode::On);
      const auto committed = f.fs.files["/radio_profiles"];
      f.fs.fail_write = true;
      f.cmd(range ? "set radio2 mediamesh" : "set radio2 mediamesh-range", false);
      assert(f.radio.p.secondary.params == config.params
          && f.fs.files["/radio_profiles"] == committed);
    }
  }
  Fixture invalid;
  for (const char* command : {"set radio2 mediamesh-extra", "set radio2 mediamesh,32",
                              "set tempradio2 mediamesh", "set radioat2 mediamesh"}) {
    invalid.cmd(command, false);
    assert(!invalid.radio.p.enabled() && invalid.fs.files.empty());
  }
  invalid.radio.minimum_frequency = 912;
  invalid.cmd("set radio2 mediamesh", false); // The hardware validator still applies.
  assert(!invalid.radio.p.enabled() && invalid.fs.files.empty());
}

#if !MESH_CLIENT_REPEATER_ONLY
static void channelPresetTransaction() {
  for (bool range : {false, true}) {
    Fixture f;
    assert(f.cli.savePrimaryPreamble(48));
    f.cli.stagePrimary(48, false);
    f.cmd("set radio2.cross on");
    f.cmd("set radio2 910.5,500,8,5,rxtx,80"); f.advance(2000);
    const auto primary = f.radio.p.primary;
    const auto previous = f.radio.p.secondary;
    const auto previous_image = f.fs.files["/radio_profiles"];
    const uint32_t intent = f.cli.secondaryIntentGeneration();
    assert(f.cli.stageChannelPreset(range ? "mediamesh-range" : "mediamesh"));
    assert(f.cli.hasReplyMutation() && f.fs.exists("/radio_profiles.reply"));
    assert(f.fs.files["/radio_profiles"] == previous_image);
    assert(f.cli.savedSecondaryConfig().params == previous.params);
    assert(f.radio.p.secondary.params == previous.params);
    f.advance(100); // A staged channel save has no permission to publish yet.
    assert(f.radio.p.secondary.params == previous.params);
    assert(f.cli.secondaryIntentGeneration() == intent);
    assert(f.cli.finishChannelPreset(true));
    assert(!f.cli.hasReplyMutation() && !f.fs.exists("/radio_profiles.reply"));
    assert(f.cli.secondaryIntentGeneration() == intent + 1);
    const auto saved = f.cli.savedSecondaryConfig();
    assert(saved.mode == mesh::RadioProfileMode::RxTx);
    assert(fabsf(saved.params.freq - 911.150f) < 0.0001f);
    assert(saved.params.bw == (range ? 125 : 250));
    assert(saved.params.sf == 7 && saved.params.cr == 5 && !saved.params.preamble);
    assert(saved.params.protocol == mesh::RadioProfileProtocol::MeshCore);
    // The pair is durable before acknowledging the channel, while the radio
    // retains the ordinary saved setter's two-second application delay.
    Radio reboot; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &reboot, &f.clock);
    assert(reboot.p.secondary.params == saved.params);
    assert(reboot.p.secondary.mode == saved.mode);
    assert(reboot.p.primary == primary && reboot.p.cross == mesh::RadioCrossMode::On);
    f.advance(1999);
    assert(f.radio.p.secondary.params == previous.params);
    f.advance(1);
    assert(f.radio.p.secondary.params == saved.params && f.radio.p.secondary.mode == saved.mode);
    assert(f.radio.p.primary == primary && f.radio.p.cross == mesh::RadioCrossMode::On);
    assert(!f.cli.finishChannelPreset(true) && !f.cli.finishChannelPreset(false));
  }
  {
    Fixture f;
    f.cmd("set radio2 off"); f.advance(2000);
    const auto before = f.fs.files;
    const uint32_t intent = f.cli.secondaryIntentGeneration();
    assert(f.cli.stageChannelPreset("mediamesh"));
    assert(f.cli.finishChannelPreset(false)); // The channel save failed.
    assert(!f.cli.hasReplyMutation() && f.fs.files == before);
    assert(f.cli.secondaryIntentGeneration() == intent);
    f.advance(2000);
    assert(!f.radio.p.enabled() && f.cli.savedSecondaryConfig().mode == mesh::RadioProfileMode::Off);
    Radio reboot; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &reboot, &f.clock);
    assert(!reboot.p.enabled());
  }
  for (int fail_rename : {1, 2}) {
    Fixture f;
    f.cmd("set radio2 910.5,500,8,5,rx,80"); f.advance(2000);
    const auto before = f.fs.files;
    const auto previous = f.radio.p.secondary;
    const uint32_t intent = f.cli.secondaryIntentGeneration();
    assert(f.cli.stageChannelPreset("mediamesh-range"));
    f.fs.fail_rename = fail_rename;
    assert(!f.cli.finishChannelPreset(true));
    assert(f.cli.hasReplyMutation());
    assert(f.radio.p.secondary.params == previous.params);
    assert(f.cli.savedSecondaryConfig().params == previous.params);
    assert(f.cli.secondaryIntentGeneration() == intent);
    assert(f.cli.finishChannelPreset(false));
    assert(f.fs.files == before && !f.cli.hasReplyMutation());
    f.advance(2000);
    assert(f.radio.p.secondary.params == previous.params);
    Radio reboot; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &reboot, &f.clock);
    assert(reboot.p.secondary.params == previous.params && reboot.p.secondary.mode == previous.mode);
  }
  {
    Fixture f;
    const char* invalid_names[] = {nullptr, "", "MediaMesh", "mediamesh-extra",
                                    "mediamesh,32", "911.150,250,7,5,rxtx"};
    for (const char* name : invalid_names) {
      assert(!f.cli.stageChannelPreset(name));
      assert(!f.cli.hasReplyMutation() && f.fs.files.empty());
    }
    f.radio.minimum_frequency = 912;
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.hasReplyMutation() && f.fs.files.empty());
    f.radio.minimum_frequency = 137;
    f.fs.fail_write = true;
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.hasReplyMutation() && !f.radio.p.enabled());
    assert(!f.fs.exists("/radio_profiles"));
  }
  {
    Fixture f;
    mesh::RadioProfileCLI unsupported;
    unsupported.begin(&f.fs, nullptr, &f.clock);
    assert(!unsupported.stageChannelPreset("mediamesh"));
    assert(!unsupported.finishChannelPreset(true) && f.fs.files.empty());
  }
  for (auto protocol : {mesh::RadioProfileProtocol::Meshtext,
                        mesh::RadioProfileProtocol::Kiss, mesh::RadioProfileProtocol::Ethernet}) {
    Fixture f;
    mesh::RadioProfileConfig foreign;
    foreign.mode = mesh::RadioProfileMode::RxTx;
    foreign.params.freq = 911.5; foreign.params.bw = 250;
    foreign.params.sf = 7; foreign.params.cr = 5; foreign.params.protocol = protocol;
    f.radio.p.setSecondary(foreign, false);
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.hasReplyMutation() && f.fs.files.empty());
    assert(f.radio.p.secondary.params == foreign.params);
  }
#if MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_HOST_KISS
  {
    Fixture f;
    mesh::RadioProfileConfig foreign;
    foreign.mode = mesh::RadioProfileMode::RxTx;
    foreign.params.freq = 911.5; foreign.params.bw = 250;
    foreign.params.sf = 7; foreign.params.cr = 5;
    foreign.params.protocol = mesh::RadioProfileProtocol::Meshtext;
    assert(f.cli.requestAutoSecondary(foreign));
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(f.cli.autoSecondaryActive() && f.fs.files.empty());
    // A separately leased MeshCore tuple must also remain protected after
    // an automatic owner observes an external generation change.
    foreign.params.protocol = mesh::RadioProfileProtocol::MeshCore;
    f.radio.p.setSecondary(foreign, false);
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.hasReplyMutation() && f.fs.files.empty());
    assert(f.radio.p.secondary.params == foreign.params);
  }
#endif
  for (unsigned busy = 0; busy < 5; ++busy) {
    Fixture f;
    if (busy == 0) f.radio.p.primary_temporary = true;
    else if (busy == 1) f.cmd("set tempradio2 910.5,500,8,5,rx,1,80");
    else if (busy == 2) { f.cmd("set tempradio2 910.5,500,8,5,rx,1,80"); f.advance(2000); }
    else if (busy == 3) f.cmd("set radioat2 910.5,500,8,5,rx,+1,80");
    else f.cmd("set radio2 910.5,500,8,5,rx,80");
    const auto before = f.fs.files;
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.hasReplyMutation() && f.fs.files == before);
  }
  {
    Fixture f;
    f.cli.beginReplyCommand();
    assert(!f.cli.stageChannelPreset("mediamesh"));
    f.cmd("set radio2 mediamesh-range");
    f.cli.endReplyCommand();
    const auto before = f.fs.files;
    const uint32_t generation = f.cli.replyMutationGeneration();
    assert(!f.cli.stageChannelPreset("mediamesh"));
    assert(!f.cli.stageChannelPreset("invalid"));
    assert(f.cli.hasReplyMutation() && f.cli.replyMutationGeneration() == generation);
    assert(f.fs.files == before); // Failed staging cannot cancel the remote ACK owner.
    assert(f.cli.finishReplyMutation(true)); f.advance(1);
    assert(!f.cli.hasReplyMutation() && f.radio.p.secondary.params.bw == 125);
  }
}

#endif

struct MediaScope {
  bool available = true, fail_save = false, enabled = false, durable = false;
  unsigned checks = 0, commits = 0;
  static bool update(void* context, bool enable, bool commit) {
    auto& self = *static_cast<MediaScope*>(context);
    if (!commit) { ++self.checks; return !enable || self.available; }
    ++self.commits;
    if (enable && !self.available) return false;
    self.enabled = enable;
    if (self.fail_save) return false;
    self.durable = enable;
    return true;
  }
};

static void mediaMeshScopeLifecycle() {
  for (const char* command : {"set radio2 mediamesh", "set radio2 mediamesh-range"}) {
    Fixture f(true);
    MediaScope scope;
    f.cli.setMediaMeshScopeHandler(MediaScope::update, &scope);
    assert(!scope.enabled && !scope.durable);
    const auto before = f.fs.files;
    const unsigned commits = scope.commits;
    scope.available = false;
    assert(strstr(f.cmd(command, false), "region unavailable or full"));
    assert(f.fs.files == before && scope.commits == commits);
    scope.available = true;
    f.fs.fail_write = true;
    f.cmd(command, false);
    assert(!scope.enabled && scope.commits == commits);
    f.fs.fail_write = false;
    scope.fail_save = true;
    assert(strstr(f.cmd(command), "scope save pending"));
    assert(scope.enabled && !scope.durable);
    assert(strstr(f.cmd("get radio2.status"), "scope save pending"));
    const unsigned pending_commits = scope.commits;
    f.advance(999);
    assert(scope.commits == pending_commits);
    scope.fail_save = false;
    f.advance(1);
    assert(scope.durable && scope.commits == pending_commits+1);
    assert(!strstr(f.cmd("get radio2.status"), "scope save pending"));

    // An interrupted region save is repaired from the committed radio2 preset
    // only after its saved region map has been supplied by infrastructure.
    Radio reboot;
    mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &reboot, &f.clock, true);
    MediaScope loaded;
    restored.setMediaMeshScopeHandler(MediaScope::update, &loaded);
    assert(loaded.enabled && loaded.durable);

    // Explicit RF tuning after a preset keeps the region's saved permission.
    const unsigned enabled_commits = scope.commits;
    f.cmd("set radio2 912.5,250,8,5,rxtx");
    assert(scope.enabled && scope.durable && scope.commits == enabled_commits);
    Radio custom_radio;
    mesh::RadioProfileCLI custom;
    custom.begin(&f.fs, &custom_radio, &f.clock, true);
    const unsigned loaded_commits = loaded.commits;
    custom.setMediaMeshScopeHandler(MediaScope::update, &loaded);
    assert(loaded.enabled && loaded.durable && loaded.commits == loaded_commits);
    f.cmd("set radio2 off");
    assert(!scope.enabled && !scope.durable);
    f.cmd(command);
    assert(scope.enabled && scope.durable);

    // Off wins after a reboot even if the old region save was interrupted.
    scope.fail_save = true;
    assert(strstr(f.cmd("set radio2 off"), "scope save pending"));
    assert(!scope.enabled && scope.durable);
    Radio off_radio;
    mesh::RadioProfileCLI off;
    off.begin(&f.fs, &off_radio, &f.clock, true);
    loaded.enabled = loaded.durable = true;
    off.setMediaMeshScopeHandler(MediaScope::update, &loaded);
    assert(!loaded.enabled && !loaded.durable);
  }
  for (bool delivered : {false, true}) {
    Fixture f(true);
    MediaScope scope;
    f.cli.setMediaMeshScopeHandler(MediaScope::update, &scope);
    const auto before = f.fs.files;
    const unsigned commits = scope.commits;
    f.cli.beginReplyCommand();
    f.cmd("set radio2 mediamesh");
    f.cli.endReplyCommand();
    assert(!scope.enabled && scope.commits == commits);
    f.advance(2000);
    assert(!scope.enabled && scope.commits == commits);
    f.cli.finishReplyMutation(delivered);
    f.advance(1);
    assert(scope.enabled == delivered && scope.durable == delivered);
    if (!delivered) assert(f.fs.files == before);
    if (delivered) {
      f.cli.beginReplyCommand(); f.cmd("set radio2 off"); f.cli.endReplyCommand();
      assert(scope.enabled);
      f.cli.finishReplyMutation(false); f.advance(1);
      assert(scope.enabled && scope.durable);
      f.cli.beginReplyCommand(); f.cmd("set radio2 off"); f.cli.endReplyCommand();
      f.cli.finishReplyMutation(true); f.advance(1);
      assert(!scope.enabled && !scope.durable);
    }
  }
  // Capacity can change while the old profile is retained for a remote reply.
  Fixture f(true);
  MediaScope scope;
  f.cli.setMediaMeshScopeHandler(MediaScope::update, &scope);
  const auto before = f.fs.files;
  f.cli.beginReplyCommand(); f.cmd("set radio2 mediamesh"); f.cli.endReplyCommand();
  scope.available = false;
  f.cli.finishReplyMutation(true); f.advance(1);
  assert(f.cli.hasReplyMutation() && !scope.enabled);
  assert(f.fs.files.find("/radio_profiles") == f.fs.files.end());
  scope.available = true;
  f.advance(1000);
  assert(!f.cli.hasReplyMutation() && scope.enabled && scope.durable);

  // Unknown radio intent must not disable an existing scope during recovery.
  const auto valid_image = f.fs.files["/radio_profiles"];
  for (bool unreadable : {false, true}) {
    f.fs.files["/radio_profiles"] = valid_image;
    if (unreadable) f.fs.fail_read_open = true;
    else f.fs.files["/radio_profiles"].resize(1);
    Radio held_radio;
    mesh::RadioProfileCLI held;
    held.begin(&f.fs, &held_radio, &f.clock, true);
    MediaScope held_scope;
    held_scope.enabled = held_scope.durable = true;
    held.setMediaMeshScopeHandler(MediaScope::update, &held_scope);
    assert(held_scope.enabled && held_scope.durable && !held_scope.commits);
    held.loop();
    assert(held_scope.enabled && !held_scope.commits);
    f.fs.fail_read_open = false;
  }
}

int main(int argc, char** argv) {
  static_assert(uint8_t(mesh::RadioCrossMode::Auto)==0 && uint8_t(mesh::RadioCrossMode::On)==1
      && uint8_t(mesh::RadioCrossMode::Off)==2, "saved crossover encoding must remain compatible");
  {
    Fixture fresh;
    assert(fresh.radio.p.cross==mesh::RadioCrossMode::Off && !fresh.radio.p.canCross());
    assert(!strcmp(fresh.cmd("get radio2.cross"),"> off") && fresh.fs.files.empty());
    for(const char* mode:{"auto","on","off"}) {
      char command[40];snprintf(command,sizeof(command),"set radio2.cross %s",mode);fresh.cmd(command);
      const auto expected=!strcmp(mode,"auto")?mesh::RadioCrossMode::Auto
          : !strcmp(mode,"on")?mesh::RadioCrossMode::On:mesh::RadioCrossMode::Off;
      assert(fresh.fs.files["/radio_profiles"][18]==uint8_t(expected));
      Radio reboot;mesh::RadioProfileCLI restored;restored.begin(&fresh.fs,&reboot,&fresh.clock);
      assert(reboot.p.cross==expected);
      restored.handle("get radio2.cross",fresh.reply);
      char reply[16];snprintf(reply,sizeof(reply),"> %s",mode);assert(!strcmp(fresh.reply,reply));
    }
    fresh.cmd("set radio2.cross on");fresh.fs.files.clear();
    fresh.cli.begin(&fresh.fs,&fresh.radio,&fresh.clock);
    assert(fresh.radio.p.cross==mesh::RadioCrossMode::Off);
    assert(!strcmp(fresh.cmd("get radio2.cross"),"> off"));
  }
  for(bool semantic:{false,true}) {
    Fixture invalid;invalid.cmd("set radio2.cross auto");
    auto& image=invalid.fs.files["/radio_profiles"];
    if(semantic) {
      image[18]=255;
      uint32_t crc=0xffffffffU;
      for(size_t i=0;i<20;++i) {crc^=image[i];for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320U:0);}
      memcpy(image.data()+20,&crc,4);
    } else image[0]^=1;
    Radio radio;radio.p.cross=mesh::RadioCrossMode::On;
    mesh::RadioProfileCLI restored;restored.begin(&invalid.fs,&radio,&invalid.clock);
    assert(radio.p.cross==mesh::RadioCrossMode::Off);
    restored.handle("get radio2.cross",invalid.reply);assert(!strcmp(invalid.reply,"> off"));
  }
  if (argc == 2) {
    if (!strcmp(argv[1], "permanent_order")) permanentScheduleOrder();
    else if (!strcmp(argv[1], "temporary_boundary")) adjacentTemporarySchedules();
    else if (!strcmp(argv[1], "lease_creation")) scheduledLeaseStartsAtCommandTime();
    else if (!strcmp(argv[1], "save_failure")) scheduledSaveFailureKeepsOriginalTimes();
    else if (!strcmp(argv[1], "schedule_permutations")) schedulePermutations();
    else if (!strcmp(argv[1], "storage_isolation")) storageBackoffDoesNotDelayTemporaryWindows();
    else if (!strcmp(argv[1], "relative_schedule")) relativeSchedules();
    else if (!strcmp(argv[1], "spectrum_overlap")) spectrumOverlap();
    else if (!strcmp(argv[1], "mediamesh")) mediaMeshPresets();
#if !MESH_CLIENT_REPEATER_ONLY
    else if (!strcmp(argv[1], "channel_preset")) channelPresetTransaction();
#endif
    else if (!strcmp(argv[1], "media_scope")) mediaMeshScopeLifecycle();
    else return 2;
    return 0;
  }
  for (bool damaged_primary : {false, true}) {
    Fixture f(true);
    f.cmd("set radio2 910.5,500,8,5,rxtx,80");
    f.cmd("set radio2.cross off");
    f.cmd("set tx.reply radio2 force");
    assert(f.cli.savePrimaryPreamble(120));
    assert(f.fs.rename("/radio_profiles", "/radio_profiles.bak"));
    const auto backup = f.fs.files["/radio_profiles.bak"];
    if (damaged_primary) {
      f.fs.files["/radio_profiles"] = {0};
      f.fs.fail_remove = true;
    } else {
      f.fs.fail_rename = 1;
    }
    Radio radio; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &radio, &f.clock, true);
    // The verified backup stays usable even when storage cannot repair it.
    assert(radio.p.enabled());
    assert(radio.p.secondary.params.freq == 910.5f);
    assert(radio.p.secondary.params.preamble == 80);
    assert(restored.primaryPreamble() == 120);
    assert(radio.p.cross == mesh::RadioCrossMode::Off);
    assert(radio.p.reply_tx == mesh::RADIO_TX_SECONDARY && radio.p.reply_force);
    f.fs.fail_remove = false;
    assert(restored.handle("set radio2 off", f.reply));
    assert(strstr(f.reply, "Error"));
    assert(f.fs.files["/radio_profiles.bak"] == backup);
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,500,8,5,rxtx,80"); f.advance(2000);
    const auto committed = f.fs.files["/radio_profiles"];
    f.fs.fail_rename_from = {"/radio_profiles.tmp", "/radio_profiles.bak"};
    f.cmd("set radio2 off", false); // both publication and rollback fail
    assert(f.radio.p.enabled());
    assert(f.fs.files["/radio_profiles.bak"] == committed);
    f.fs.fail_rename_from.clear();
    f.cmd("set radio2.cross on", false); // protect the sole committed copy
    assert(f.fs.files["/radio_profiles.bak"] == committed);
    Radio radio; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &radio, &f.clock);
    assert(radio.p.enabled() && radio.p.secondary.params.preamble == 80);
    assert(restored.handle("set radio2 off", f.reply));
    assert(!strncmp(f.reply, "OK", 2)); // reboot repairs storage and releases the hold
  }
  {
    Fixture f(true);
    f.cmd("set tx.reply off");
    // Simulate an image whose CRC is valid but whose saved mode is invalid.
    auto& image=f.fs.files["/radio_profiles"];
    image[3]=9;
    uint32_t crc=0xffffffffU;
    for (size_t i=0;i<20;++i) {
      crc ^= image[i];
      for (unsigned bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1)?0xedb88320U:0);
    }
    memcpy(image.data()+20,&crc,4);
    const auto damaged=image;
    Radio radio; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs,&radio,&f.clock,true);
    restored.loop();
    assert(radio.p.reply_tx==mesh::RADIO_TX_BOTH && !radio.p.reply_force);
    assert(!radio.p.enabled());
    assert(restored.handle("set radio2.cross on",f.reply));
    assert(!strcmp(f.reply,"OK"));
    assert(radio.p.cross==mesh::RadioCrossMode::On);
    assert(restored.handle("set tempradio2 911.3,500,8,7,rxtx,2",f.reply));
    assert(!strncmp(f.reply,"OK",2));
    g_mock_millis+=2000; restored.loop();
    assert(radio.p.secondary_temporary && radio.p.canCross());
    Radio reboot; mesh::RadioProfileCLI repaired;
    repaired.begin(&f.fs,&reboot,&f.clock,true);
    assert(reboot.p.cross==mesh::RadioCrossMode::On);
  }
  {
    Fixture companion;
    assert(companion.radio.p.reply_tx==mesh::RADIO_TX_AUTO);
    assert(!companion.cli.handle("set tx.reply both force",companion.reply));
    Fixture f(true);
    assert(strstr(f.cmd("get tx.reply"),"> both; radio2 TX unavailable"));
    f.cmd("set radio2 910.5,500,7,5,rx"); f.advance(2000);
    f.cmd("set tx.reply both force");
    assert(!strcmp(f.cmd("get tx.reply"),"> both force"));
    assert(f.radio.p.reply_tx==mesh::RADIO_TX_BOTH && f.radio.p.reply_force);
    assert(f.radio.p.secondary.mode==mesh::RadioProfileMode::Rx);
    assert(f.fs.files["/radio_profiles"].size()==24);
    // Every legal choice survives reboot, including explicit AUTO (distinct
    // from the zero reserved byte in firmware predating reply routing).
    const char* modes[]={"auto","radio","radio2","both","off","radio2 force","both force"};
    const uint8_t policies[]={0,1,2,3,4,2,3};
    for (unsigned i=0;i<7;++i) {
      char command[64]; snprintf(command,sizeof(command),"set tx.reply %s",modes[i]);
      f.cmd(command);
      Radio reboot; mesh::RadioProfileCLI restored;
      restored.begin(&f.fs,&reboot,&f.clock,true);
      assert(reboot.p.reply_tx==policies[i] && reboot.p.reply_force==(i>=5));
    }
    f.fs.fail_write=true;
    f.cmd("set tx.reply off",false);
    assert(f.radio.p.reply_tx==mesh::RADIO_TX_BOTH && f.radio.p.reply_force);
    f.fs.fail_write=false; f.fs.fail_rename=2;
    f.cmd("set tx.reply off",false);
    Radio reboot; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs,&reboot,&f.clock,true);
    assert(reboot.p.reply_tx==mesh::RADIO_TX_BOTH && reboot.p.reply_force);
    const char* invalid[]={"", "both junk", "auto force", "radio force", "off force",
      "both force extra", "force", "bothforce", "radio2345678901234567"};
    for (const char* args:invalid) {
      char command[80]; snprintf(command,sizeof(command),"set tx.reply %s",args);
      f.cmd(command,false);
      assert(f.radio.p.reply_tx==mesh::RADIO_TX_BOTH && f.radio.p.reply_force);
    }
    assert(strstr(f.cmd("get tx.reply extra"),"Error"));
    // A Companion neither activates nor erases infrastructure reply choices
    // when it saves another profile setting.
    Radio other; mesh::RadioProfileCLI other_cli;
    other_cli.begin(&f.fs,&other,&f.clock);
    assert(other.p.reply_tx==mesh::RADIO_TX_AUTO && !other.p.reply_force);
    assert(other_cli.handle("set radio2.cross on",f.reply));
    Radio back; mesh::RadioProfileCLI back_cli;
    back_cli.begin(&f.fs,&back,&f.clock,true);
    assert(back.p.reply_tx==mesh::RADIO_TX_BOTH && back.p.reply_force);
    // Old images have a zero reserved byte and adopt BOTH on infrastructure.
    Fixture legacy; legacy.cmd("set radio2 910.5,500,7,5,rxtx");
    assert(legacy.fs.files["/radio_profiles"][19]==0);
    Radio upgrade; mesh::RadioProfileCLI upgraded;
    upgraded.begin(&legacy.fs,&upgrade,&legacy.clock,true);
    assert(upgrade.p.reply_tx==mesh::RADIO_TX_BOTH && !upgrade.p.reply_force);
  }
  {
    Fixture f;
    assert(!strcmp(f.cmd("get radio2"),"> off"));
    assert(!strcmp(f.cmd("get radio2.cross"),"> off"));
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rxtx"),"timing self-test pending"));
    assert(!f.radio.p.enabled()); f.advance(2000); assert(f.radio.p.enabled());
    assert(strstr(f.cmd("get radio2.timing"),"self-test pending"));
    f.finishTimingTest();
    assert(strstr(f.cmd("get radio2"),"rxtx,64 (auto)"));
    assert(strstr(f.reply,"switch=600us"));
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rxtx"),"preamble=64;"));
    assert(strstr(f.reply,"switch=600us"));
    assert(strstr(f.cmd("get radio2.scan"),"slow=radio; listen_us=9421,22147"));
    assert(strstr(f.cmd("get radio2.timing"),"chirps=4.60,86.51; need=32,64"));
    assert(strstr(f.reply,"switch=600us"));
    assert(strstr(f.reply,"loop=0us"));
    assert(!strstr(f.reply,"estimate"));
    assert(strstr(f.reply,"WARN recommended preamble: radio2=64"));
    char timing_reply[160]; strcpy(timing_reply, f.reply);
    assert(!strcmp(f.cmd("get radio.timing"),timing_reply));
    f.cmd("set tempradio2 911.5,500,8,5,rx,2,64");
    assert(!f.radio.p.secondary_temporary); f.advance(2000);
    assert(f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq==911.5f);
    assert(!f.radio.p.canCross());
    assert(strstr(f.cmd("get tempradio2"),"0d0h2m"));
    f.cmd("set radio2.cross on"); assert(f.radio.p.canCross());
    f.cmd("set radio2.cross off"); assert(!f.radio.p.canCross());
    f.cmd("set tempradio2 off"); f.advance(2000);
    assert(!f.radio.p.secondary_temporary && f.radio.p.secondary.params.freq==910.5f);
    f.cmd("set radio2 off"); f.advance(2000); assert(!f.radio.p.enabled());
    f.cmd("get radio2.status");
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,500,7,5,rx");
    f.advance(2000);
    for (unsigned i=0;i<mesh::RadioProfiles::SwitchTestSamplesPerDirection;++i) {
      f.radio.p.sampleSwitch(0,1,545,g_mock_millis);
      f.radio.p.sampleSwitch(1,0,545,g_mock_millis);
    }
    assert(f.radio.p.switchTestReady() && !f.radio.p.recommendationReady());
    const auto wire_preamble = f.radio.p.preamble(1,32);
    assert(wire_preamble==64 && f.radio.p.switchBudgetUs()==600);
    assert(strstr(f.cmd("get radio2"),"rx,64 (auto)"));
    assert(strstr(f.reply,"wait=120s") && !strstr(f.reply,"WARN"));
    assert(strstr(f.cmd("get radio2.timing"),"provisional switch=600us"));
    assert(!strstr(f.reply,"need=") && !strstr(f.reply,"WARN"));
    f.advance(119999);
    f.radio.p.sampleSwitch(0,1,545,g_mock_millis);
    assert(!f.radio.p.recommendationReady());
    assert(strstr(f.cmd("get radio2.timing"),"wait=1s"));
    f.advance(1);
    assert(!f.radio.p.recommendationReady()); // No synthetic retune at deadline.
    f.radio.p.sampleSwitch(1,0,545,g_mock_millis);
    assert(f.radio.p.recommendationReady());
    assert(f.radio.p.preamble(1,32)==wire_preamble && f.radio.p.switchBudgetUs()==600);
    assert(strstr(f.cmd("get radio2.timing"),"need=32,64"));
    assert(strstr(f.reply,"WARN recommended preamble: radio2=64"));
    assert(!strstr(f.reply,"timing self-test pending"));
    // The UI delay never changes ongoing maximum-based timing policy.
    f.radio.p.sampleSwitch(0,1,8428,g_mock_millis);
    assert(f.radio.p.switchBudgetUs()==9271);
    assert(strstr(f.cmd("get radio2.timing"),"switch=9271us"));
    f.cmd("set radio2 910.5,500,7,5,rx");
    assert(f.radio.p.recommendationReady()); // No-op leaves the window intact.
    f.cmd("set radio2 911.5,500,7,5,rx");
    f.advance(2000);
    assert(!f.radio.p.recommendationReady());
    assert(strstr(f.cmd("get radio2.timing"),"wait=120s"));
  }
  {
    Fixture f;
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rx"),"timing self-test pending"));
    assert(!strstr(f.reply,"switch="));
    f.advance(2000);
    f.finishTimingTest(497); // 497 us observed with a 10% guard -> 547 us.
    assert(f.radio.p.switchBudgetUs()==547);
    assert(strstr(f.cmd("get radio2"),"switch=547us"));
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rx"),"switch=547us"));
    assert(strstr(f.cmd("get radio2.timing"),"switch=547us"));
    assert(!strstr(strstr(f.reply,"switch=")+1,"switch="));
    assert(strstr(f.cmd("get radio2.status"),"budget=547us"));
    assert(!strstr(f.reply,"switch=547us"));
    assert(strstr(f.cmd("set radio2 911.5,500,7,5,rx"),"timing self-test pending"));
    assert(!strstr(f.reply,"switch=547us"));
  }
  {
    Fixture f;
    const char* invalid[] = {"910.5,500,7,5", "NaN,500,7,5,rx", "910.5,0,7,5,rx",
      "910.5,500,256,5,rx", "910.5,500,7,256,rx", "910.5,500,7,5,invalid",
      "910.5,500,7,5,rx,7", "910.5,500,7,5,rx,65535", "910.5,500,7,5,rx,32,junk",
      "910.5,500,7,5,rx,32,", "910.5,500,7,5,rx,32junk"};
    char text[160];
    for (auto args : invalid) { snprintf(text,sizeof(text),"set radio2 %s",args); f.cmd(text,false); assert(!f.radio.p.enabled()); }
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,0",false);
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,10081",false);
    f.cmd("set radio2.status 1",false);
    f.cmd("set radio2.timing 1",false);
    f.cmd("set radio.timing 1",false);
    assert(strstr(f.cmd("get radio2.timing extra"),"read-only"));
    assert(!f.cli.handle("set radio2junk 910.5",f.reply));
  }
  {
    Fixture f;
    assert(!strcmp(f.cmd("get radio2.timing"),"> off"));
    // A short explicit preamble is not rewritten, and cannot hide a warning.
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rx,32"),"timing self-test pending"));
    f.advance(2000);
    f.finishTimingTest();
    assert(f.radio.p.secondary.params.preamble==32);
    assert(strstr(f.cmd("get radio2"),"short override: radio2"));
    f.radio.p.sampleSwitch(0,1,8428);
    assert(strstr(f.cmd("get radio2.timing"),"switch=9271us"));
    assert(strstr(f.reply,"WARN recommended preamble: radio2="));
    assert(f.radio.p.secondary.params.preamble==32);
    // Primary getters use the saved primary's SF/BW instead of a temporary one.
    f.radio.p.primary.sf=10; f.radio.p.primary.bw=125;
    strcpy(f.reply,"> radio");
    f.cli.appendSavedPreamble(f.reply,sizeof(f.reply),7,62.5);
    assert(strstr(f.reply,"timing self-test pending"));
    // Bounded appenders cannot corrupt neighboring bytes, even with tiny replies.
    for (size_t n=1;n<=160;++n) {
      char bytes[162]; memset(bytes,0x55,sizeof(bytes)); bytes[0]=0;
      mesh::RadioProfileCLI::appendChirpWarning(bytes,n,f.radio.p);
      assert(memchr(bytes,0,n)); assert(bytes[n]==0x55);
    }
    f.radio.p.primary.sf=10;f.radio.p.primary.bw=125;
    f.radio.p.secondary.params.sf=10;f.radio.p.secondary.params.bw=125;
    f.radio.p.resetSwitchTest(); f.finishTimingTest();
    assert(!strstr(f.cmd("get radio2.timing"),"WARN"));
    // A selected value below 32 still gets the actual recommendation, not
    // merely a warning label with no preamble to use.
    assert(strstr(f.cmd("set radio2 910.5,125,10,5,rx,16"),"timing self-test pending"));
  }
  {
    Fixture f;
    f.radio.p.primary.sf=7;f.radio.p.primary.bw=500;
    assert(strstr(f.cmd("set radio2 910.5,62.5,7,5,rx"),"timing self-test pending"));
    f.advance(2000);
    f.finishTimingTest();
    assert(strstr(f.cmd("get radio2.timing"),"need=64,32"));
    strcpy(f.reply,"OK - reboot to apply");
    f.cli.appendPrimaryChirpWarning(f.reply,sizeof(f.reply),7,500,32);
    assert(strstr(f.reply,"timing self-test pending"));
    strcpy(f.reply,"OK - reboot to apply");
    f.cli.appendPrimaryChirpWarning(f.reply,sizeof(f.reply),7,62.5,0);
    assert(!strstr(f.reply,"WARN")); // Preview requested tuple, not the live one.
    f.radio.p.primary.sf=5;f.radio.p.primary.bw=500;
    f.radio.p.secondary.params.sf=5;f.radio.p.secondary.params.bw=500;
    assert(strstr(f.cmd("get radio2.timing"),"WARN recommended preamble: radio="));
    assert(strstr(f.reply,",radio2="));
  }
  {
    Fixture f;
    // The tested pair is allocated automatically without changing explicit 32.
    assert(strstr(f.cmd("set radio2 910.5,500,8,5,rx,32"),"timing self-test pending"));
    f.advance(2000);
    f.finishTimingTest();
    assert(f.radio.p.secondary.params.preamble==32);
    assert(strstr(f.cmd("get radio2.scan"),"slow=radio; listen_us=9421,22147"));
    assert(strstr(f.cmd("get radio2.timing"),"chirps=4.60,43.26; need=32,40"));
    assert(strstr(f.reply,"loop=0us"));
  }
  {
    Fixture f;
    f.radio.p.primary.bw=500;
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rx"),"timing self-test pending"));
    f.advance(2000);
    f.finishTimingTest(8428);
    assert(f.radio.p.switchBudgetUs()==9271);
    assert(f.radio.p.preamble(0,32)==168);
    assert(!strstr(f.cmd("get radio2.timing"),"WARN need >"));
    assert(!strstr(f.cmd("get radio2.status"),"WARN need >"));
    f.finishTimingTest(40000);
    assert(f.radio.p.preamble(0,32)==512);
    assert(strstr(f.cmd("get radio2.timing"),"WARN need >512: radio"));
    assert(strstr(f.cmd("get radio2.status"),"WARN need >512: radio"));
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,500,9,5,rxtx,40"); f.advance(2000);
    f.fs.fail_write=true;
    f.cmd("set radio2 off",false); assert(f.radio.p.enabled());
    f.fs.fail_write=false;
    f.fs.fail_rename=2; // publishing temp fails after moving old image to backup
    f.cmd("set radio2 off",false); assert(f.radio.p.enabled());
    Radio reboot; mesh::RadioProfileCLI restored; restored.begin(&f.fs,&reboot,&f.clock);
    assert(reboot.p.enabled() && reboot.p.secondary.params.preamble==40);
    f.cmd("set tempradio2 911.5,500,7,5,rxtx,1"); f.advance(2000);
    Radio again; mesh::RadioProfileCLI restored_again; restored_again.begin(&f.fs,&again,&f.clock);
    assert(!again.p.secondary_temporary && again.p.secondary.params.freq==910.5f);
  }
  {
    Fixture f;
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,1"); f.advance(2000);
    f.clock.epoch-=3600; f.advance(60000,false);
    assert(!f.radio.p.enabled());
    g_mock_millis=UINT32_MAX-1000; f.cli.loop();
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,1"); f.advance(2000,false);
    assert(f.radio.p.secondary_temporary); f.advance(60000,false); assert(!f.radio.p.enabled());
  }
  {
    Fixture f;
    assert(f.cli.savePrimaryPreamble(48));
    f.radio.p.primary.preamble=96; f.radio.p.primary_temporary=true;
    strcpy(f.reply, "> 909.5,62.5,7,5");
    f.cli.appendSavedPreamble(f.reply, sizeof(f.reply), 7, 62.5);
    assert(strstr(f.reply, "preamble=48")); // saved getter cannot display temporary preamble
    assert(!f.cli.savePrimaryPreamble(7));
    f.cmd("set tempradio2 910.5,500,8,5,rx,1");
    f.advance(65000); // servicing a late start cannot extend its lease
    assert(!f.radio.p.enabled());
    f.cmd("set tempradio2 910.5,500,8,5,rx,2");
    char text[160];
    snprintf(text, sizeof(text), "set tempradioat2 910.5,500,8,5,rx,%lu,%lu",
        (unsigned long)(f.clock.epoch+60),(unsigned long)(f.clock.epoch+120));
    f.cmd(text, false); // overlaps the pending session, before it starts
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,7.8,12,8,rx,65528",false); // driver airtime overflow
    f.cmd("set radio2 910.5,500,8,5,rx,48");
    assert(f.fs.rename("/radio_profiles", "/radio_profiles.bak"));
    Radio radio; mesh::RadioProfileCLI restored;
    restored.begin(&f.fs, &radio, &f.clock);
    assert(radio.p.enabled()); // interrupted publication recovered
    f.fs.files["/radio_profiles"][2]=99;
    Radio corrupt; mesh::RadioProfileCLI invalid;
    invalid.begin(&f.fs, &corrupt, &f.clock);
    assert(!corrupt.p.enabled());
    const auto damaged=f.fs.files["/radio_profiles"];
    f.fs.fail_remove=true;
    assert(invalid.handle("set radio2.cross on", f.reply));
    assert(strstr(f.reply,"Error")); // failed cleanup never overwrites corruption
    assert(f.fs.files["/radio_profiles"]==damaged);
    f.fs.fail_remove=false;
    assert(invalid.handle("set radio2.cross on", f.reply));
    assert(!strcmp(f.reply,"OK"));
    assert(corrupt.p.cross==mesh::RadioCrossMode::On);
    assert(invalid.handle("set tempradio2 911.3,500,8,7,rxtx,2", f.reply));
    assert(!strncmp(f.reply,"OK",2));
    g_mock_millis+=2000; invalid.loop();
    assert(corrupt.p.secondary_temporary && corrupt.p.canCross());
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,500,8,5,rxtx,80");
    const auto saved=f.fs.files["/radio_profiles"];
    f.fs.fail_read_open=true;
    Radio radio; mesh::RadioProfileCLI unavailable;
    unavailable.begin(&f.fs, &radio, &f.clock);
    f.fs.fail_read_open=false;
    assert(unavailable.handle("set radio2.cross on", f.reply));
    assert(strstr(f.reply,"Error")); // an I/O fault is never treated as corruption
    assert(f.fs.files["/radio_profiles"]==saved);
  }
  {
    Fixture f;
    f.cmd("set radio2 910.5,500,8,5,rxtx,80");
    const auto backup=f.fs.files["/radio_profiles"];
    f.fs.files["/radio_profiles.bak"]=backup;
    // This versioned image has a valid CRC but an unsupported saved mode.
    // An intact backup must prevent automatic discard/recreation.
    f.fs.files["/radio_profiles"][3]=3;
    uint32_t crc=0xffffffffU;
    for (size_t i=0;i<20;++i) {
      crc ^= f.fs.files["/radio_profiles"][i];
      for (unsigned bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1)?0xedb88320U:0);
    }
    memcpy(f.fs.files["/radio_profiles"].data()+20,&crc,4);
    Radio radio; mesh::RadioProfileCLI protected_backup;
    protected_backup.begin(&f.fs, &radio, &f.clock);
    assert(protected_backup.handle("set radio2.cross on", f.reply));
    assert(strstr(f.reply,"Error"));
    assert(f.fs.files["/radio_profiles.bak"]==backup);
  }
  {
    Fixture f;
    char text[160];
    snprintf(text,sizeof(text),"set tempradioat2 910.5,500,8,5,rxtx,%lu,%lu,48",
        (unsigned long)(f.clock.epoch+60),(unsigned long)(f.clock.epoch+120));
    f.cmd(text); assert(strstr(f.cmd("get tempradioat2 1"),"48"));
    f.cmd(text,false); // overlap
    f.advance(60000); assert(f.radio.p.secondary_temporary);
    f.clock.epoch-=3600; f.advance(60000,false); assert(!f.radio.p.enabled());
    snprintf(text,sizeof(text),"set radioat2 910.5,500,9,5,rx,%lu",(unsigned long)(f.clock.epoch+60));
    f.cmd(text); f.advance(60000); assert(f.radio.p.secondary.mode==mesh::RadioProfileMode::Rx);
    f.cmd("del radioat2 all"); f.cmd("del tempradioat2 all");
  }
  puts("RadioProfileCLI: command, persistence, failure, schedule and rollover tests passed");
}
