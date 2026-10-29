#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/ConfigCapabilities.h>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/RadioProfileCommandUtils.h>
// Persistence has its own production-parser tests with an in-memory FS.
// This fixture isolates the actual CommonCLI setting branches and callbacks.
namespace mesh {
enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED };
struct Radio {
  bool isCarrierWaveActive() const { return false; }
  const RadioProfiles* profiles() const { return nullptr; }
};
inline bool morseTimingActive(const Radio*) { return false; }
struct RadioProfileCLI {
  static bool parseSuffix(const char* input, unsigned fields, char* legacy,
                          size_t size, uint16_t& preamble) {
    return cli::parseRadioPreambleSuffix(input, fields, legacy, size, preamble);
  }
  bool savePrimaryPreamble(uint16_t) { return true; }
  void appendPreamble(char*, size_t) {}
  void appendSavedPreamble(char* reply, size_t capacity, uint8_t, float) {
    const size_t used = strlen(reply);
    if (used < capacity) snprintf(reply + used, capacity - used, ",preamble=16 (auto)");
  }
  mutable unsigned warning_calls = 0;
  mutable uint8_t warning_sf = 0;
  mutable float warning_bw = 0;
  mutable uint16_t warning_preamble = 0;
  void appendPrimaryChirpWarning(char* reply, size_t capacity, uint8_t sf, float bw, uint16_t preamble) const {
    ++warning_calls; warning_sf = sf; warning_bw = bw; warning_preamble = preamble;
    const size_t used = strlen(reply);
    if (used < capacity) snprintf(reply + used, capacity - used, "; WARN recommended preamble: radio=32");
  }
  bool acceptsPrimary(float, float, uint8_t, uint8_t, uint16_t) const { return true; }
};
}
#define MIN_LORA_TX_POWER -9
#define MAX_LORA_TX_POWER 22
#include <helpers/radiolib/RadioPowerLimits.h>
#include <helpers/radiolib/LR2021SideDetectorConfig.h>

struct StrHelper {
  static const char* ftoa(float value) {
    static char buffer[24]; snprintf(buffer, sizeof(buffer), "%.3f", double(value)); return buffer;
  }
  static const char* ftoa3(float value) { return ftoa(value); }
};
struct Prefs {
  float freq=909.5f, bw=62.5f, airtime_factor=1, rx_delay_base=0;
  float tx_delay_factor=0, direct_tx_delay_factor=0;
  uint8_t sf=7, cr=5, interference_threshold=0, agc_reset_interval=0;
  uint8_t path_hash_mode=0, multi_acks=0, rx_ps_level=0, rx_ps_preamble=0;
  int8_t tx_power_dbm=10;
  uint16_t primary_radio_preamble=0;
  uint32_t rx_ps_rx_us=1000, rx_ps_sleep_us=1000;
  bool cad_enabled=false, rx_boosted_gain=false, rx_powersaving_enabled=false;
  uint8_t extra_sf[4]={};
  uint8_t flood_max=16, flood_max_advert=16, flood_max_unscoped=16;
};
struct Callbacks {
  unsigned saves=0, tx_calls=0, gain_calls=0;
  bool accept=true;
  int8_t power=0;
  bool gain=false;
  void savePrefs() { ++saves; }
  bool setTxPower(int8_t value) { ++tx_calls; if (!accept) return false; power=value; return true; }
  mesh::RadioParamApplyResult trySetTxPower(int8_t value) {
    return setTxPower(value) ? mesh::RadioParamApplyResult::APPLIED
                             : mesh::RadioParamApplyResult::FAILED;
  }
  bool isTxPowerApplied(int8_t value) const { return power == value; }
  const mesh::Radio* getProfileRadio() const { return nullptr; }
  bool setRxBoostedGain(bool value) { ++gain_calls; if (!accept) return false; gain=value; return true; }
  bool supportsRxBoostedGain() const { return true; }
  mesh::RadioParamApplyResult trySetRxBoostedGain(bool value) {
    return setRxBoostedGain(value) ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
  }
  mesh::RadioParamApplyResult trySetRxPowerSaving(bool, uint32_t, uint32_t) {
    return mesh::RadioParamApplyResult::FAILED;
  }
  mesh::RadioParamApplyResult trySetRxPowerSavingRfRxDisabled(bool) {
    return mesh::RadioParamApplyResult::FAILED;
  }
  bool configSideDetectors(const uint8_t*,uint8_t,float) { return accept; }
  mesh::RadioParamApplyResult tryConfigSideDetectors(const uint8_t* sfs,uint8_t count,float bw) {
    return configSideDetectors(sfs,count,bw) ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
  }
};
struct Board { unsigned n_cad_busy=0; };
bool isValidLoRaBandwidth(float bw) { return bw==125 || bw==62.5f; }
uint32_t millis() { return 1000; }
void recalcRxPowerSavingFromLevel(uint8_t,uint8_t,float,uint8_t,uint32_t*,uint32_t*) {}
void appendRxPowerSavingAdjustmentNote(char*,Prefs*,uint8_t,float) {}
@KEY_EQUALS@
class CLI {
public:
  Prefs prefs;
  Callbacks callbacks;
  Board board;
  mesh::RadioProfileCLI _radio_profiles;
  Prefs* _prefs=&prefs;
  Callbacks* _callbacks=&callbacks;
  Board* _board=&board;
  bool _tx_power_pending=false;
  uint32_t _tx_power_retry_at=0;
  enum RxSetting : uint8_t { RxGain=1, RxPowerSaving=2, RxSideDetectors=4, RxRfRx=8,
                            RxPendingMask=15, RxRfRxDisabled=128 };
  uint8_t _rx_settings_pending=0;
  uint32_t _rx_settings_retry_at=0;
  void savePrefs() { callbacks.savePrefs(); }
  bool applyAndSavePrimaryRadioParams(float f,float bw,uint8_t sf,uint8_t cr,uint16_t preamble,char* reply) {
    if (!savePrimaryRadioParams(f,bw,sf,cr,preamble)) return false;
    strcpy(reply,"OK - applying in 2s"); return true;
  }
  @PRIMARY_VALIDATE@
  bool trySavePrefs() { savePrefs(); return true; }
  void applyAndSaveTxPower(int8_t power, char* reply);
  void servicePendingTxPower();
  mesh::RadioParamApplyResult applyRxSetting(uint8_t);
  bool saveRxSetting(uint8_t, mesh::RadioParamApplyResult, char*);
  void restoreRxSetting(uint8_t, mesh::RadioParamApplyResult, uint8_t, uint32_t);
  bool savePrimaryRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) {
    prefs.freq=freq; prefs.bw=bw; prefs.sf=sf; prefs.cr=cr;
    prefs.primary_radio_preamble=preamble;
    prefs.tx_power_dbm=mesh::clampLoRaTxPower(prefs.tx_power_dbm,freq);
    callbacks.savePrefs(); return true;
  }
  void handleSetCmd(uint32_t, char* command, char* reply) {
    const char* config=command+4;
    @SET@
    else strcpy(reply,"unknown config");
  }
  void handleGetCmd(uint32_t, char* command, char* reply) {
    const char* config=command+4;
    @GET@
    else strcpy(reply,"unknown config");
  }
  void command(uint32_t sender_timestamp, char* command, char* reply) {
    @DISPATCH@
    else strcpy(reply,"unknown command");
  }
  void call(uint32_t sender, const char* text, char* reply) {
    char command[160]; snprintf(command,sizeof(command),"%s",text);
    this->command(sender,command,reply);
  }
};

@TX_METHODS@
@RX_METHODS@

struct Case { const char *key, *value; double expected; };
int main() {
  // Local USB and authenticated on-air calls use the same infrastructure CLI.
  for (uint32_t sender : {0u, 123456u}) {
    CLI cli; char text[160], reply[160];
    for (const Case& c : {
        Case{"freq","915.5",915.5}, {"af","2.5",2.5}, {"dutycycle","25",25},
        {"int.thresh","128",128}, {"tx","14",14}, {"rxdelay","2.5",2.5},
        {"agc.reset.interval","19",16}, {"path.hash.mode","2",2},
        {"multi.acks","1",1}, {"txdelay","1.5",1.5}, {"direct.txdelay","0.5",0.5}}) {
      unsigned saves=cli.callbacks.saves;
      snprintf(text,sizeof(text),"set %s %s",c.key,c.value);
      cli.call(sender,text,reply);
      if (strncmp(reply,"OK",2)) { fprintf(stderr,"%s -> %s\n",text,reply); return 1; }
      assert(cli.callbacks.saves==saves+1);
      snprintf(text,sizeof(text),"get %s",c.key);
      cli.call(sender,text,reply);
      double actual=0;
      assert(sscanf(reply,"> %lf",&actual)==1 && fabs(actual-c.expected)<0.001);
      assert(cli.callbacks.saves==saves+1); // reading must not persist
    }
    for (const char* key : {"cad","radio.rxgain"}) {
      for (const char* state : {"on","off"}) {
        unsigned saves=cli.callbacks.saves;
        snprintf(text,sizeof(text),"set %s %s",key,state); cli.call(sender,text,reply);
        assert(!strcmp(reply,"OK") && cli.callbacks.saves==saves+1);
        snprintf(text,sizeof(text),"get %s",key); cli.call(sender,text,reply);
        assert(!strncmp(reply+2,state,strlen(state)));
      }
    }
    // The saved byte represents four-second units. Reject malformed/out-of-
    // range input before it can wrap (1024 used to silently disable AGC).
    for (unsigned seconds=0; seconds<=1020; ++seconds) {
      const unsigned saves=cli.callbacks.saves;
      snprintf(text,sizeof(text),"set agc.reset.interval %u",seconds);
      cli.call(sender,text,reply);
      assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
      assert(cli.prefs.agc_reset_interval==seconds/4);
      cli.call(sender,"get agc.reset.interval",reply);
      unsigned actual=9999;
      assert(sscanf(reply,"> %u",&actual)==1 && actual==(seconds/4)*4);
      assert(cli.callbacks.saves==saves+1);
    }
    for (const char* value : {"1024", "1021", "1023", "-1", "-4", "",
                              "garbage", "19tail", "1.5", "0x10", "+", "--4",
                              "4294967295", "4294967296", "2147483648",
                              "99999999999999999999999999999999999999"}) {
      const unsigned saves=cli.callbacks.saves;
      const uint8_t previous=cli.prefs.agc_reset_interval;
      snprintf(text,sizeof(text),"set agc.reset.interval %s",value);
      cli.call(sender,text,reply);
      if (strncmp(reply,"Error",5))
        fprintf(stderr,"invalid AGC interval %s accepted: %s\n",value,reply);
      assert(!strncmp(reply,"Error",5));
      assert(cli.prefs.agc_reset_interval==previous && cli.callbacks.saves==saves);
    }
    for (const char* value : {"+19", " 19 ", "\t19\t", "00019"}) {
      const unsigned saves=cli.callbacks.saves;
      snprintf(text,sizeof(text),"set agc.reset.interval %s",value);
      cli.call(sender,text,reply);
      assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
      assert(cli.prefs.agc_reset_interval==4);
    }
    unsigned saves=cli.callbacks.saves;
    cli.call(sender,"set radio 916,125,8,6",reply);
    assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
    assert(strstr(reply,"WARN recommended preamble: radio=32"));
    assert(cli._radio_profiles.warning_calls==1 && cli._radio_profiles.warning_sf==8);
    assert(cli._radio_profiles.warning_bw==125 && cli._radio_profiles.warning_preamble==0);
    cli.call(sender,"get radio",reply);
    // The official app requires exactly four comma-separated radio fields.
    // Simulate the production preamble suffix above to catch regressions.
    assert(!strcmp(reply,"> 916.000,125.000,8,6"));
    float freq=0,bw=0; int sf=0,cr=0;
    assert(sscanf(reply,"> %f,%f,%d,%d",&freq,&bw,&sf,&cr)==4);
    assert(freq==916 && bw==125 && sf==8 && cr==6);
    cli.call(sender,"set radio 916,125,8,6,16",reply);
    assert(strstr(reply,"WARN recommended preamble: radio=32"));
    assert(cli._radio_profiles.warning_calls==2 && cli._radio_profiles.warning_preamble==16);
    cli.call(sender,"set radio 916,125,13,6",reply);
    assert(!strncmp(reply,"Error",5) && !strstr(reply,"WARN"));
    assert(cli._radio_profiles.warning_calls==2);
    // radio.preamble reads the saved preference; the writer remains the
    // optional fifth set radio field. Exercise that actual parser route.
    struct Preamble { const char* input; const char* reply; uint16_t value; };
    for (const Preamble& row : {Preamble{"auto", "> auto", 0},
          {"8", "> 8", 8}, {"32", "> 32", 32}, {"88", "> 88", 88},
          {"65528", "> 65528", 65528}}) {
      saves=cli.callbacks.saves;
      snprintf(text,sizeof(text),"set radio 916,125,8,6,%s",row.input);
      cli.call(sender,text,reply);
      assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
      assert(cli.prefs.primary_radio_preamble==row.value);
      cli.call(sender,"get radio.preamble",reply);
      assert(!strcmp(reply,row.reply));
      assert(cli.callbacks.saves==saves+1);
    }
    saves=cli.callbacks.saves;
    cli.call(sender,"set extra.sf 9,10",reply);
#if defined(USE_LR2021)
    assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
    assert(cli.prefs.extra_sf[0]==9 && cli.prefs.extra_sf[1]==10 && cli.prefs.extra_sf[2]==0);
    cli.call(sender,"get extra.sf",reply); assert(!strcmp(reply,"9,10"));
    saves=cli.callbacks.saves;
    cli.call(sender,"set extra.sf 8",reply);
    assert(strstr(reply,"Invalid") && cli.callbacks.saves==saves && cli.prefs.extra_sf[0]==9);
    cli.call(sender,"set extra.sf none",reply);
    assert(!strncmp(reply,"OK",2) && cli.prefs.extra_sf[0]==0);
    cli.call(sender,"get extra.sf",reply); assert(!strcmp(reply,"No extra SF configured"));
#else
    assert(strstr(reply,"requires an LR2021") && cli.callbacks.saves==saves);
    cli.call(sender,"get extra.sf",reply);
    assert(strstr(reply,"requires an LR2021") && cli.prefs.extra_sf[0]==0);
#endif
    // A failed hardware apply must not persist or advertise a changed setting.
    cli.callbacks.accept=false;
    const int8_t old_power=cli.prefs.tx_power_dbm;
    const bool old_gain=cli.prefs.rx_boosted_gain;
    saves=cli.callbacks.saves;
    cli.call(sender,"set tx 16",reply);
    assert(!strncmp(reply,"Error",5) && cli.prefs.tx_power_dbm==old_power);
    cli.call(sender,"set radio.rxgain on",reply);
    assert(!strncmp(reply,"Error",5) && cli.prefs.rx_boosted_gain==old_gain);
    assert(cli.callbacks.saves==saves);
    cli.call(sender,"set unknown.setting 1",reply);
    assert(!strcmp(reply,"unknown config") && cli.callbacks.saves==saves);
    for (const char* key : {"flood.max", "flood.max.advert", "flood.max.unscoped"}) {
      for (const char* invalid : {"", "invalid", "65", "256", "-1", "1tail",
                                 "1.5", "4294967296", "999999999999999999999"}) {
        saves=cli.callbacks.saves;
        snprintf(text,sizeof(text),"set %s %s",key,invalid); cli.call(sender,text,reply);
        assert(!strncmp(reply,"Error",5) && cli.callbacks.saves==saves);
        assert(cli.prefs.flood_max==16 && cli.prefs.flood_max_advert==16
            && cli.prefs.flood_max_unscoped==16);
      }
    }
    for (const char* key : {"flood.max", "flood.max.advert", "flood.max.unscoped"}) {
      for (unsigned value : {0u, 1u, 64u}) {
        saves=cli.callbacks.saves;
        snprintf(text,sizeof(text),"set %s %u",key,value); cli.call(sender,text,reply);
        assert(!strcmp(reply,"OK") && cli.callbacks.saves==saves+1);
        snprintf(text,sizeof(text),"get %s",key); cli.call(sender,text,reply);
        unsigned actual=1000; assert(sscanf(reply,"> %u",&actual)==1 && actual==value);
      }
    }
  }
}
