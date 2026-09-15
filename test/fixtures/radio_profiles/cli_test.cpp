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
};
int main() {
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
    assert(restored.handle("set tx.reply radio",f.reply));
    assert(strstr(f.reply,"Error"));
    assert(f.fs.files["/radio_profiles"]==damaged);
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
    assert(!strcmp(f.cmd("get radio2.cross"),"> auto"));
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rxtx"),"WARN recommended preamble: radio2=64"));
    assert(!f.radio.p.enabled()); f.advance(2000); assert(f.radio.p.enabled());
    assert(strstr(f.cmd("get radio2"),"rxtx,64 (auto)"));
    assert(strstr(f.cmd("get radio2.scan"),"slow=radio; listen_us=9421,21847"));
    assert(strstr(f.cmd("get radio2.timing"),"chirps=4.60,85.34; need=32,64"));
    assert(strstr(f.reply,"switch=600us"));
    assert(strstr(f.reply,"loop=300us"));
    assert(strstr(f.reply,"estimate"));
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
    assert(strstr(f.cmd("set radio2 910.5,500,7,5,rx,32"),"WARN recommended preamble: radio2=64"));
    assert(strstr(f.reply,"short override: radio2"));
    f.advance(2000);
    assert(f.radio.p.secondary.params.preamble==32);
    assert(strstr(f.cmd("get radio2"),"short override: radio2"));
    f.radio.p.longest_switch_us=8428;
    assert(strstr(f.cmd("get radio2.timing"),"switch=8428us"));
    assert(strstr(f.reply,"WARN recommended preamble: radio="));
    assert(f.radio.p.secondary.params.preamble==32);
    // Primary getters use the saved primary's SF/BW instead of a temporary one.
    f.radio.p.primary.sf=10; f.radio.p.primary.bw=125;
    strcpy(f.reply,"> radio");
    f.cli.appendSavedPreamble(f.reply,sizeof(f.reply),7,62.5);
    assert(strstr(f.reply,"WARN recommended preamble: radio="));
    // Bounded appenders cannot corrupt neighboring bytes, even with tiny replies.
    for (size_t n=1;n<=160;++n) {
      char bytes[162]; memset(bytes,0x55,sizeof(bytes)); bytes[0]=0;
      mesh::RadioProfileCLI::appendChirpWarning(bytes,n,f.radio.p);
      assert(memchr(bytes,0,n)); assert(bytes[n]==0x55);
    }
    f.radio.p.primary.sf=10;f.radio.p.primary.bw=125;
    f.radio.p.secondary.params.sf=10;f.radio.p.secondary.params.bw=125;
    f.radio.p.longest_switch_us=0;
    assert(!strstr(f.cmd("get radio2.timing"),"WARN"));
    // A selected value below 32 still gets the actual recommendation, not
    // merely a warning label with no preamble to use.
    assert(strstr(f.cmd("set radio2 910.5,125,10,5,rx,16"),"WARN recommended preamble: radio2=32"));
    assert(strstr(f.reply,"short override: radio2"));
  }
  {
    Fixture f;
    f.radio.p.primary.sf=7;f.radio.p.primary.bw=500;
    assert(strstr(f.cmd("set radio2 910.5,62.5,7,5,rx"),"WARN recommended preamble: radio=64"));
    f.advance(2000);
    assert(strstr(f.cmd("get radio2.timing"),"need=64,32"));
    strcpy(f.reply,"OK - reboot to apply");
    f.cli.appendPrimaryChirpWarning(f.reply,sizeof(f.reply),7,500,32);
    assert(strstr(f.reply,"WARN recommended preamble: radio=64"));
    assert(strstr(f.reply,"short override: radio"));
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
    assert(strstr(f.cmd("set radio2 910.5,500,8,5,rx,32"),"WARN recommended preamble: radio2=88"));
    f.advance(2000);
    assert(f.radio.p.secondary.params.preamble==32);
    assert(strstr(f.cmd("get radio2.scan"),"slow=radio; listen_us=9421,21847"));
    assert(strstr(f.cmd("get radio2.timing"),"chirps=4.60,42.67; need=32,88"));
    assert(strstr(f.reply,"loop=300us"));
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
