// Reuse the memory filesystem and radio model, not the CLI test entry point.
#define main existing_cli_main
#include "cli_test.cpp"
#undef main
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/TempRadioReplyBarrier.h>
#include <vector>

class Millis : public mesh::MillisecondClock {
 public:
  unsigned long getMillis() override { return g_mock_millis; }
};
class Inspector : public mesh::Dispatcher {
 public:
  Inspector(mesh::Radio& r, mesh::MillisecondClock& c, mesh::PacketManager& m) : Dispatcher(r,c,m) {}
  mesh::DispatcherAction onRecvPacket(mesh::Packet*) override { return ACTION_RELEASE; }
  using Dispatcher::isPacketRadioCurrent;
  using Dispatcher::getNextQueueWakeDelay;
  uint8_t getDefaultTxCodingRate() const override { return 5; }
};

static void secondaryReplies() {
  unsigned checks = 0;
  for (const char* command : {"set tempradio2 911.5,500,8,5,rxtx,1",
                             "set radio2 912.5,500,8,5,rxtx", "set radio2 off",
                             "set tempradio2 off", "del tempradioat2 all"}) {
    for (bool success : {false, true}) {
      Fixture f(true);
      f.cmd("set radio2 910.5,500,8,5,rxtx"); f.advance(2000);
      const auto saved = f.fs.files["/radio_profiles"];
      const uint32_t before = f.radio.p.generation[1];
      Millis ms;
      StaticPoolPacketManager manager(8);
      Inspector inspector(f.radio, ms, manager);
      f.cli.beginReplyCommand(); f.cmd(command); f.cli.endReplyCommand();
      assert(f.cli.hasReplyMutation());
      const uint32_t mutation = f.cli.replyMutationGeneration();
      assert(strstr(f.cmd("get radio2.status"), "reply transmission"));
      assert(mutation == f.cli.replyMutationGeneration()); // getters cannot steal ownership
      assert(saved == f.fs.files["/radio_profiles"]); // power loss before ACK is safe
      Radio reboot; mesh::RadioProfileCLI restored;
      restored.begin(&f.fs, &reboot, &f.clock);
      assert(reboot.p.secondary.params.freq == 910.5f);
      mesh::Packet reply;
      reply.radio_bound = reply.radio_reply = true;
      reply.tx_radio = mesh::RADIO_TX_BOTH;
      reply.radio_origin = 1; reply.radio_origin_generation = before;
      reply.radio_profile = 0; reply.radio_generation = f.radio.p.generation[0];
      assert(inspector.isPacketRadioCurrent(&reply));
      f.advance(10000);
      assert(before == f.radio.p.generation[1]);
      assert(inspector.isPacketRadioCurrent(&reply)); // origin preserved even for primary TX
      assert(f.cli.finishReplyMutation(success));
      assert(before == f.radio.p.generation[1]); // callback doesn't change radio under Dispatcher
      f.advance(1);
      assert(!f.cli.hasReplyMutation());
      if (!success) {
        assert(before == f.radio.p.generation[1]);
        assert(saved == f.fs.files["/radio_profiles"]);
      } else if (strstr(command, "911.5") || strstr(command, "912.5") || !strcmp(command,"set radio2 off")) {
        assert(before != f.radio.p.generation[1]);
        assert(!inspector.isPacketRadioCurrent(&reply));
      }
      ++checks;
    }
  }
  for (unsigned failure = 1; failure <= 2; ++failure) {
    Fixture f(true);
    f.cmd("set radio2 910.5,500,8,5,rxtx"); f.advance(2000);
    const auto saved = f.fs.files["/radio_profiles"];
    f.cli.beginReplyCommand(); f.cmd("set radio2 912.5,500,8,5,rxtx"); f.cli.endReplyCommand();
    f.fs.fail_rename = failure;
    f.cli.finishReplyMutation(true); f.advance(1);
    assert(f.cli.hasReplyMutation());
    assert(f.radio.p.secondary.params.freq == 910.5f);
    assert(saved == f.fs.files["/radio_profiles"]);
    assert(strstr(f.cmd("get radio2.status"), "storage commit (retrying)"));
    f.advance(999); assert(f.cli.hasReplyMutation());
    f.advance(1); assert(!f.cli.hasReplyMutation());
    assert(f.radio.p.secondary.params.freq == 912.5f);
    ++checks;
  }
  {
    Fixture f(true);
    f.fs.fail_write = true;
    f.cli.beginReplyCommand(); f.cmd("set radio2 912.5,500,8,5,rxtx", false); f.cli.endReplyCommand();
    assert(!f.cli.hasReplyMutation()); // failure reported before a success is queued
    ++checks;
  }
  for (bool rollover : {false, true}) {
    Fixture f(true);
    if (rollover) { g_mock_millis = UINT32_MAX - 1000; f.cli.loop(); }
    f.cli.beginReplyCommand(); f.cmd("set tempradio2 911.5,500,8,5,rxtx,1"); f.cli.endReplyCommand();
    f.advance(65000, false); f.cli.finishReplyMutation(true); f.advance(1, false);
    assert(!f.radio.p.enabled()); // no late start or lease extension
    ++checks;
  }
  for (const char* cmd : {"set radio2 off", "set tempradio2 off", "tempradio2 911.5,500,8,5,rxtx,1",
                          "set radio2 911.5,500,8,5,rxtx", "del tempradioat2 all"}) {
    Fixture f(true);
    assert(f.cli.handle(cmd, f.reply, sizeof(f.reply), true));
    assert(strstr(f.reply, "requires local USB"));
    assert(!f.cli.hasReplyMutation()); assert(!f.radio.p.enabled());
    assert(f.cli.handle("get radio2", f.reply, sizeof(f.reply), true));
    assert(!strcmp(f.reply, "> off"));
    ++checks;
  }
  std::printf("Secondary reply transaction: %u scenarios passed\n", checks);
}

struct FaultRadio : Radio {
  uint8_t physical_cr = 5;
  bool sending = false, hold_restore = false, busy_restore = false;
  unsigned fail_restores = 0, restore_calls = 0, recoveries = 0;
  std::vector<uint8_t> sent_cr;
  bool setCodingRate(uint8_t cr) override { physical_cr = cr; return true; }
  mesh::RadioParamApplyResult tryRestoreCodingRate(uint8_t cr) override {
    ++restore_calls;
    if (busy_restore) return mesh::RadioParamApplyResult::BUSY;
    if (hold_restore || fail_restores) {
      if (fail_restores) --fail_restores;
      return mesh::RadioParamApplyResult::FAILED;
    }
    physical_cr = cr; return mesh::RadioParamApplyResult::APPLIED;
  }
  bool startSendRaw(const uint8_t*, int) override { sending = true; sent_cr.push_back(physical_cr); return true; }
  bool isSendComplete() override { return sending; }
  void onSendFinished() override { sending = false; }
  bool recoverRadio(bool) override { ++recoveries; return true; }
};

static void codingRateRestore() {
  unsigned checks = 0;
  for (unsigned mode = 0; mode < 5; ++mode) {
    g_mock_millis = mode == 4 ? UINT32_MAX - 20 : 1000;
    FaultRadio radio;
    Millis ms; StaticPoolPacketManager manager(8); Inspector d(radio, ms, manager);
    d.begin();
    auto queue = [&](uint8_t cr) {
      auto* pkt = d.obtainNewPacket(); assert(pkt);
      pkt->header = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT);
      pkt->path_len = 0; pkt->payload_len = 1; pkt->payload[0] = 42; pkt->tx_cr = cr;
      assert(d.sendPacket(pkt, 0));
    };
    auto tick = [&](uint32_t delta) { g_mock_millis += delta; d.loop(); };
    radio.fail_restores = 1;
    radio.hold_restore = mode == 1;
    radio.busy_restore = mode == 2;
    queue(7); tick(1); tick(1);
    assert(radio.sent_cr.size() == 1 && radio.sent_cr[0] == 7);
    uint32_t wake = 0;
    assert(d.getNextQueueWakeDelay(wake) && wake <= 250); // recovery survives idle/sleep
    const auto calls = radio.restore_calls;
    tick(100); assert(radio.restore_calls == calls); // no hot retry
    queue(0); tick(1);
    assert(radio.sent_cr.size() == 1); // ordinary traffic never inherits CR7
    if (mode == 1 || mode == 2) {
      for (unsigned i=0; i<40; ++i) tick(250);
      assert(radio.sent_cr.size() == 1 && manager.getOutboundTotal() == 1);
      if (mode == 1) assert(radio.recoveries > 0);
      else assert(radio.recoveries == 0); // legitimate RX/BUSY never reset
    }
    radio.hold_restore = radio.busy_restore = false; radio.fail_restores = 0;
    if (mode == 3) radio.p.primary.cr = 6; // settings changed during recovery
    tick(250); tick(1);
    assert(radio.sent_cr.size() == 2 && radio.sent_cr[1] == (mode == 3 ? 6 : 5));
    assert(manager.getFreeCount() == 8);
    ++checks;
  }
  std::printf("Coding-rate restoration: %u scenarios passed\n", checks);
}

int main() { secondaryReplies(); codingRateRestore(); }
