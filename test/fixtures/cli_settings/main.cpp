#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/RadioProfileCommandUtils.h>
// Persistence has its own production-parser tests with an in-memory FS.
// This fixture isolates the actual CommonCLI setting branches and callbacks.
namespace mesh {
struct RadioProfileCLI {
  static bool parseSuffix(const char* input, unsigned fields, char* legacy,
                          size_t size, uint16_t& preamble) {
    return cli::parseRadioPreambleSuffix(input, fields, legacy, size, preamble);
  }
  bool savePrimaryPreamble(uint16_t) { return true; }
  void appendPreamble(char*, size_t) {}
  void appendSavedPreamble(char*, size_t, uint8_t, float) {}
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
  uint32_t rx_ps_rx_us=1000, rx_ps_sleep_us=1000;
  bool cad_enabled=false, rx_boosted_gain=false;
  uint8_t extra_sf[4]={};
};
struct Callbacks {
  unsigned saves=0, tx_calls=0, gain_calls=0;
  bool accept=true;
  int8_t power=0;
  bool gain=false;
  void savePrefs() { ++saves; }
  bool setTxPower(int8_t value) { ++tx_calls; if (!accept) return false; power=value; return true; }
  bool setRxBoostedGain(bool value) { ++gain_calls; if (!accept) return false; gain=value; return true; }
  bool configSideDetectors(const uint8_t*,uint8_t,float) { return accept; }
};
struct Board { unsigned n_cad_busy=0; };
bool isValidLoRaBandwidth(float bw) { return bw==125 || bw==62.5f; }
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
  void savePrefs() { callbacks.savePrefs(); }
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
    unsigned saves=cli.callbacks.saves;
    cli.call(sender,"set radio 916,125,8,6",reply);
    assert(!strncmp(reply,"OK",2) && cli.callbacks.saves==saves+1);
    cli.call(sender,"get radio",reply);
    float freq=0,bw=0; int sf=0,cr=0;
    assert(sscanf(reply,"> %f,%f,%d,%d",&freq,&bw,&sf,&cr)==4);
    assert(freq==916 && bw==125 && sf==8 && cr==6);
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
  }
}
