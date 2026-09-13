#include <Arduino.h>
#include <helpers/RadioProfileCLI.h>
#include <cassert>
#include <cstdio>
#include <cstring>

struct Clock : mesh::RTCClock {
  uint32_t epoch = 1700000000;
  uint32_t getCurrentTime() override { return epoch; }
  void setCurrentTime(uint32_t time) override { epoch = time; }
};
struct Radio : mesh::Radio {
  mesh::RadioProfiles p;
  Radio() { p.primary.freq=909.5; p.primary.bw=62.5; p.primary.sf=7; p.primary.cr=5; }
  mesh::RadioProfiles* profiles() override { return &p; }
  const mesh::RadioProfiles* profiles() const override { return &p; }
  bool validateProfile(const mesh::RadioProfileParams& v) const override { return mesh::RadioProfiles::valid(v) && v.bw<=500; }
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
  Fixture() { g_mock_millis=0; cli.begin(&fs,&radio,&clock); }
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
};
int main() {
  {
    Fixture f;
    assert(!strcmp(f.cmd("get radio2"),"> off"));
    assert(!strcmp(f.cmd("get radio2.cross"),"> auto"));
    f.cmd("set radio2 910.5,500,7,5,rxtx");
    assert(!f.radio.p.enabled()); f.advance(2000); assert(f.radio.p.enabled());
    assert(strstr(f.cmd("get radio2"),"rxtx,120 (auto)"));
    assert(strstr(f.cmd("get radio2.scan"),"slow=radio; listen_us=9831,6937"));
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
    const char* invalid[] = {"910.5,500,7,5", "NaN,500,7,5,rx", "910.5,0,7,5,rx",
      "910.5,500,256,5,rx", "910.5,500,7,256,rx", "910.5,500,7,5,invalid",
      "910.5,500,7,5,rx,7", "910.5,500,7,5,rx,65535", "910.5,500,7,5,rx,32,junk",
      "910.5,500,7,5,rx,32,", "910.5,500,7,5,rx,32junk"};
    char text[160];
    for (auto args : invalid) { snprintf(text,sizeof(text),"set radio2 %s",args); f.cmd(text,false); assert(!f.radio.p.enabled()); }
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,0",false);
    f.cmd("set tempradio2 910.5,500,7,5,rxtx,10081",false);
    f.cmd("set radio2.status 1",false);
    assert(!f.cli.handle("set radio2junk 910.5",f.reply));
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
    assert(invalid.handle("set radio2 off", f.reply));
    assert(strstr(f.reply,"Error")); // newer/corrupt image is not overwritten
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
