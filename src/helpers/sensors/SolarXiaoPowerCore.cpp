#include "SolarXiaoPowerCore.h"
#if defined(MESH_SOLARXIAO_POWER) && MESH_SOLARXIAO_POWER

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace mesh { namespace solar {
namespace {
constexpr uint8_t CHARGER = 0x68;
constexpr uint8_t MONITOR = 0x40;
constexpr uint8_t CONFIG = 0x14;
constexpr uint16_t SUSPEND_CHARGER = 0x20;
constexpr uint32_t REMOTE_OFF_TRIAL_MS = 45000;
constexpr uint32_t SERVICE_INTERVAL_MS = 5000;
constexpr uint16_t BATTERY_DETECTING_OR_FAULT = (1u<<12)|(1u<<11)|(1u<<1)|1u;
constexpr uint16_t DEFAULT_STOP_MV = 14000;
constexpr uint16_t DEFAULT_RESUME_MV = 13600;
enum Kind : uint8_t { Number, Signed, Boolean, Inverted, DieC, InputMa,
  InputMv, BatteryMa, InputCurrentMa, ChargeMa, VinMv, VoutMv, BatteryMv, BsrOhm, CellMv };
enum Access : uint8_t { Read, Write, Locked, Policy };
struct Field {
  const char* name;
  uint8_t reg;
  uint16_t mask;
  uint8_t shift;
  Kind kind;
  Access access;
  const char* unit;
};

// Registers and bitfields from LTC4162-F Rev A. Only FST-compatible settings
// are writable; identity and fixed-profile fields never reach Bus::write().
const Field fields[] = {
  {"alert.battery_low_mv",1,0xffff,0,BatteryMv,Write,"mV"},
  {"alert.battery_high_mv",2,0xffff,0,BatteryMv,Write,"mV"},
  {"alert.input_low_mv",3,0xffff,0,VinMv,Write,"mV"},
  {"alert.input_high_mv",4,0xffff,0,VinMv,Write,"mV"},
  {"alert.output_low_mv",5,0xffff,0,VoutMv,Write,"mV"},
  {"alert.output_high_mv",6,0xffff,0,VoutMv,Write,"mV"},
  {"alert.input_high_raw",7,0xffff,0,Signed,Write,"ADC"},
  {"alert.input_high_ma",7,0xffff,0,InputCurrentMa,Write,"mA"},
  {"alert.battery_low_ma",8,0xffff,0,BatteryMa,Write,"mA"},
  {"alert.die_high_c",9,0xffff,0,DieC,Write,"C"},
  {"alert.resistance_raw",10,0xffff,0,Number,Write,"ADC"},
  {"alert.ntc_high_raw",11,0xffff,0,Signed,Write,"ADC"},
  {"alert.ntc_low_raw",12,0xffff,0,Signed,Write,"ADC"},
  {"alert.limits_mask",13,0xcfff,0,Number,Write,"mask"},
  {"alert.states_mask",14,0x1b7f,0,Number,Write,"mask"},
  {"alert.status_mask",15,0x003f,0,Number,Write,"mask"},
  {"thermal.start_c",16,0xffff,0,DieC,Write,"C"},
  {"thermal.end_c",17,0xffff,0,DieC,Write,"C"},
  {"charge.enabled",0x14,0x20,5,Inverted,Write,""},
  {"telemetry.fast",0x14,8,3,Boolean,Write,""},
  {"telemetry.force",0x14,4,2,Boolean,Write,""},
  {"mppt.enabled",0x14,2,1,Boolean,Write,""},
  {"input.limit_ma",0x15,63,0,InputMa,Write,"mA"},
  {"input.minimum_mv",0x16,255,0,InputMv,Write,"mV"},
  {"charge.current_code",0x1a,31,0,Number,Read,"code"},
  {"charge.current_ma",0x1a,31,0,ChargeMa,Write,"mA"},
  {"charge.cell_mv",0x1b,31,0,CellMv,Write,"mV/cell"},
  {"termination.current_ma",0x1c,0xffff,0,BatteryMa,Write,"mA"},
  {"termination.cv_seconds",0x1d,0xffff,0,Number,Write,"s"},
  {"termination.total_minutes",0x1e,0xffff,0,Number,Write,"min"},
  {"jeita.t1_raw",0x1f,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.t2_raw",0x20,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.t3_raw",0x21,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.t4_raw",0x22,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.t5_raw",0x23,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.t6_raw",0x24,0xffff,0,Signed,Locked,"ADC"},
  {"jeita.voltages_65",0x25,0x03ff,0,Number,Locked,"packed"},
  {"jeita.voltages_432",0x26,0x7fff,0,Number,Locked,"packed"},
  {"jeita.currents_65",0x27,0x03ff,0,Number,Locked,"packed"},
  {"jeita.currents_432",0x28,0x7fff,0,Number,Locked,"packed"},
  {"termination.enabled",0x29,4,2,Boolean,Write,""},
  {"jeita.enabled",0x29,1,0,Boolean,Write,""},
  {"absorb.delta_code",0x2a,31,0,Number,Locked,"code"},
  {"absorb.seconds",0x2b,0xffff,0,Number,Locked,"s"},
  {"recharge.mv",0x2e,0xffff,0,BatteryMv,Locked,"mV"},
  {"timer.charge_minutes",0x30,0xffff,0,Number,Read,"min"},
  {"timer.cv_seconds",0x31,0xffff,0,Number,Read,"s"},
  {"timer.absorb_seconds",0x32,0xffff,0,Number,Read,"s"},
  {"state",0x34,0x1b7f,0,Number,Read,"mask"},
  {"status",0x35,63,0,Number,Read,"mask"},
  {"alerts.limits",0x36,0xcfff,0,Number,Read,"mask"},
  {"alerts.states",0x37,0x1b7f,0,Number,Read,"mask"},
  {"alerts.status",0x38,63,0,Number,Read,"mask"},
  {"system",0x39,0x1ff,0,Number,Read,"mask"},
  {"battery.mv",0x3a,0xffff,0,BatteryMv,Read,"mV"},
  {"input.mv",0x3b,0xffff,0,VinMv,Read,"mV"},
  {"output.mv",0x3c,0xffff,0,VoutMv,Read,"mV"},
  {"battery.ma",0x3d,0xffff,0,BatteryMa,Read,"mA"},
  {"input.current_raw",0x3e,0xffff,0,Signed,Read,"ADC"},
  {"input.ma",0x3e,0xffff,0,InputCurrentMa,Read,"mA"},
  {"die.c",0x3f,0xffff,0,DieC,Read,"C"},
  {"ntc.raw",0x40,0xffff,0,Signed,Read,"ADC"},
  {"battery.resistance_ohm",0x41,0xffff,0,BsrOhm,Read,"ohm"},
  {"jeita.region",0x42,7,0,Number,Read,"region"},
  {"chemistry",0x43,0x0f00,8,Number,Read,"code"},
  {"cells",0x43,15,0,Number,Read,"cells"},
  {"dac.charge_code",0x44,31,0,Number,Read,"code"},
  {"dac.charge_ma",0x44,31,0,ChargeMa,Read,"mA"},
  {"dac.cell_mv",0x45,31,0,CellMv,Read,"mV/cell"},
  {"dac.input_ma",0x46,63,0,InputMa,Read,"mA"},
  {"battery.filtered_mv",0x47,0xffff,0,BatteryMv,Read,"mV"},
  {"resistance.current_ma",0x48,0xffff,0,BatteryMa,Read,"mA"},
  {"telemetry.valid",0x4a,1,0,Boolean,Read,""},
  {"resistance.questionable",0x4a,2,1,Boolean,Read,""},
  {"dac.input_mv",0x4b,255,0,InputMv,Read,"mV"},
  {"charge.stop_mv",0,0,0,Number,Policy,"mV"},
  {"charge.resume_mv",0,0,0,Number,Policy,"mV"},
};
constexpr size_t FIELD_COUNT = sizeof(fields) / sizeof(fields[0]);

const Field* findField(const char* key) {
  for (const auto& field : fields) if (strcmp(key, field.name) == 0) return &field;
  return nullptr;
}
uint32_t crc(const uint8_t* data, size_t size) {
  uint32_t value = 0xffffffff;
  while (size--) {
    value ^= *data++;
    for (unsigned bit=0; bit<8; ++bit)
      value = (value >> 1) ^ (0xedb88320UL & (0U - (value & 1)));
  }
  return ~value;
}
void put16(uint8_t* p, uint16_t n) { p[0]=n; p[1]=n>>8; }
uint16_t get16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1])<<8; }
void put32(uint8_t* p, uint32_t n) { for (unsigned i=0;i<4;++i) p[i]=n>>(8*i); }
uint32_t get32(const uint8_t* p) {
  uint32_t n=0; for (unsigned i=0;i<4;++i) n|=uint32_t(p[i])<<(8*i); return n;
}
bool number(const char* text, double& value) {
  if (!text || !*text || *text==' ' || *text=='\t') return false;
  char* end=nullptr; value=strtod(text,&end);
  return end!=text && *end==0 && isfinite(value);
}
bool integer(const char* text, unsigned max, unsigned& value) {
  double parsed; if (!number(text,parsed) || parsed<0 || parsed>max || floor(parsed)!=parsed) return false;
  value=unsigned(parsed); return true;
}
bool signedKind(Kind kind) {
  return kind==Signed || kind==DieC || kind==BatteryMa || kind==InputCurrentMa || kind==VinMv
      || kind==VoutMv || kind==BatteryMv;
}
double decode(const Field& f, uint16_t word, const Settings& settings, uint8_t cells) {
  const uint16_t code=(word&f.mask)>>f.shift;
  const double n=signedKind(f.kind) ? static_cast<int16_t>(code) : code;
  switch (f.kind) {
    case DieC:return n*0.0215-264.4;
    case InputMa:return (n+1)*500.0/settings.rsnsi_mohm;
    case InputMv:return (n+1)*140.625;
    case BatteryMa:return n*1.466/settings.rsnsb_mohm;
    case InputCurrentMa:return n*1.466/settings.rsnsi_mohm;
    case ChargeMa:return (n+1)*1000.0/settings.rsnsb_mohm;
    case VinMv:return n*1.649;
    case VoutMv:return n*1.653;
    case BatteryMv:return cells ? n*cells*0.1924 : NAN;
    case BsrOhm:return cells ? n*cells*settings.rsnsb_mohm/500000.0 : NAN;
    case CellMv:return 3412.5+n*12.5;
    case Inverted:return !code;
    default:return n;
  }
}
bool encode(const Field& f, const char* text, const Settings& settings,
            uint8_t cells, uint16_t& bits) {
  double value;
  if (f.kind==Boolean || f.kind==Inverted) {
    if (!strcmp(text,"on")) value=1;
    else if (!strcmp(text,"off")) value=0;
    else return false;
    if (f.kind==Inverted) value=!value;
  } else {
    if (!number(text,value)) return false;
    switch (f.kind) {
      case DieC:
        if (value < -40 || value > 125) return false;
        value=round((value+264.4)/0.0215); break;
      case InputMa:
        if (value<=0 || value>3200) return false;
        value=floor(value*settings.rsnsi_mohm/500.0)-1; break;
      case InputMv:
        if (value<=0 || value>35000) return false;
        value=ceil(value/140.625)-1; break;
      case BatteryMa:value=round(value*settings.rsnsb_mohm/1.466); break;
      case InputCurrentMa:value=round(value*settings.rsnsi_mohm/1.466); break;
      case ChargeMa:
        if (value<=0 || value>3200) return false;
        value=floor(value*settings.rsnsb_mohm/1000.0)-1; break;
      case CellMv:
        if (value<3412.5 || value>3600) return false;
        value=floor((value-3412.5)/12.5); break;
      case VinMv:value=round(value/1.649); break;
      case VoutMv:value=round(value/1.653); break;
      case BatteryMv:
        if (!cells) return false;
        value=round(value/(cells*0.1924)); break;
      default: if (floor(value)!=value) return false; break;
    }
  }
  const double minimum=signedKind(f.kind) ? -32768 : 0;
  const double maximum=signedKind(f.kind) ? 32767 : (f.mask>>f.shift);
  if (!isfinite(value) || value<minimum || value>maximum) return false;
  const uint16_t code=static_cast<uint16_t>(static_cast<int32_t>(value));
  if ((static_cast<uint32_t>(code)<<f.shift)&~static_cast<uint32_t>(f.mask)) return false;
  bits=static_cast<uint16_t>(code<<f.shift); return true;
}
} // namespace

uint16_t Controller::writableMask(uint8_t reg) {
  uint16_t mask=0;
  for (const auto& field:fields)
    if (field.reg==reg && field.access==Write) mask|=field.mask;
  return mask;
}
bool Controller::readableRegister(uint8_t reg) {
  if (reg==0x19) return true;
  for (const auto& field:fields) if (field.reg==reg && field.access!=Policy) return true;
  return false;
}
const char* Controller::chemistryName(uint8_t value) {
  static const char* const names[]={"LAD","L42","L41","L40","FAD","FFS","FST","unknown","SST","SAD"};
  return value<10 ? names[value] : "unknown";
}
const char* Controller::stateName(uint16_t state) {
  switch(state) {
    case 4096:return "battery_detection_failed";
    case 2048:return "detecting_battery";
    case 512:return "absorb";
    case 256:return "suspended";
    case 64:return "charging";
    case 32:return "temperature_pause";
    case 16:return "timer_terminated";
    case 8:return "current_terminated";
    case 4:return "charge_time_fault";
    case 2:return "battery_missing";
    case 1:return "battery_short";
    default:return "unknown";
  }
}

void Settings::defaults() {
  memset(masks,0,sizeof(masks)); memset(values,0,sizeof(values));
  charge_stop_mv=DEFAULT_STOP_MV; charge_resume_mv=DEFAULT_RESUME_MV;
  rsnsb_mohm=rsnsi_mohm=10;
  ntc_bias_ohm=ntc_r25_ohm=10000; ntc_beta=3490; calibration_user_mask=0;
}
bool Settings::valid() const {
  if (!charge_resume_mv || charge_resume_mv>=charge_stop_mv || charge_stop_mv>14400) return false;
  const float calibration[]={rsnsb_mohm,rsnsi_mohm,ntc_bias_ohm,ntc_r25_ohm,ntc_beta};
  const float minimum[]={0.1f,0.1f,100,100,1000};
  const float maximum[]={1000,1000,1000000,1000000,10000};
  for (unsigned i=0;i<5;++i)
    if (!isfinite(calibration[i]) || calibration[i]<minimum[i] || calibration[i]>maximum[i]) return false;
  if (calibration_user_mask&~31) return false;
  for (unsigned reg=0;reg<REG_COUNT;++reg) {
    if (masks[reg]&~Controller::writableMask(reg)) return false;
    if (values[reg]&~masks[reg]) return false;
    // Full-word targets must not contain a partial mask from a damaged record.
    if (Controller::writableMask(reg)==0xffff && masks[reg] && masks[reg]!=0xffff) return false;
  }
  for (unsigned reg=16;reg<=17;++reg)
    if (masks[reg] && (values[reg]<10437 || values[reg]>18112)) return false;
  if (masks[0x1c] && static_cast<int16_t>(values[0x1c])<0) return false;
  if (masks[0x1b] && values[0x1b]>15) return false;
  if (masks[16] && masks[17] && static_cast<int16_t>(values[16])>=static_cast<int16_t>(values[17])) return false;
  return true;
}
void Settings::encode(uint8_t image[IMAGE_SIZE]) const {
  memset(image,0,IMAGE_SIZE); memcpy(image,"SXPW",4); image[4]=1; image[5]=6;
  image[6]=calibration_user_mask;
  size_t offset=8;
  for (unsigned i=0;i<REG_COUNT;++i) {
    put16(image+offset,masks[i]); put16(image+offset+2,values[i]); offset+=4;
  }
  // Register zero has no writable fields. Keep v1 for ordinary overrides and
  // reuse its four reserved bytes in v2 for custom pack thresholds.
  if (charge_stop_mv!=DEFAULT_STOP_MV || charge_resume_mv!=DEFAULT_RESUME_MV) {
    image[4]=2; put16(image+8,charge_stop_mv); put16(image+10,charge_resume_mv);
  }
  const float calibration[]={rsnsb_mohm,rsnsi_mohm,ntc_bias_ohm,ntc_r25_ohm,ntc_beta};
  for (float value:calibration) {
    static_assert(sizeof(float)==4,"Settings require IEEE binary32");
    uint32_t word; memcpy(&word,&value,4); put32(image+offset,word); offset+=4;
  }
  put32(image+IMAGE_SIZE-4,crc(image,IMAGE_SIZE-4));
}
bool Settings::decode(const uint8_t image[IMAGE_SIZE]) {
  if (memcmp(image,"SXPW",4) || (image[4]!=1 && image[4]!=2) || image[5]!=6 || image[7]
      || get32(image+IMAGE_SIZE-4)!=crc(image,IMAGE_SIZE-4)) return false;
  Settings candidate; candidate.defaults(); candidate.calibration_user_mask=image[6];
  size_t offset=8;
  for (unsigned i=0;i<REG_COUNT;++i) {
    candidate.masks[i]=get16(image+offset); candidate.values[i]=get16(image+offset+2); offset+=4;
  }
  if (image[4]==2) {
    candidate.charge_stop_mv=candidate.masks[0]; candidate.charge_resume_mv=candidate.values[0];
    candidate.masks[0]=candidate.values[0]=0;
  }
  float* calibration[]={&candidate.rsnsb_mohm,&candidate.rsnsi_mohm,&candidate.ntc_bias_ohm,
                        &candidate.ntc_r25_ohm,&candidate.ntc_beta};
  for (auto value:calibration) {uint32_t word=get32(image+offset); memcpy(value,&word,4); offset+=4;}
  if (!candidate.valid()) return false;
  *this=candidate; return true;
}

void Controller::begin() {
  if (started) return;
  started=true; serviced=false; settings.defaults(); storage_healthy=store.available();
  loaded_backup=false;
  apply_ok=false;
  if (storage_healthy && store.exists()) {
    uint8_t image[Settings::IMAGE_SIZE];
    if (store.load(image,sizeof(image)) && settings.decode(image)) return;
    loaded_backup=store.loadBackup(image,sizeof(image)) && settings.decode(image);
    storage_healthy=loaded_backup;
  }
}
bool Controller::identify(uint16_t& identity) {
  if (!bus.read(CHARGER,0x43,identity)) return false;
  const uint8_t chemistry=(identity>>8)&15;
  if (!strcmp(chemistryName(chemistry),"unknown")) return false;
  const uint8_t cells=identity&15;
  if (cells>9) return false;
  if (cells) last_cells=cells;
  return true;
}
void Controller::reload() {
  trial_off=false;
  started=false;
  begin();
  next_service=0;
}
bool Controller::verifyWritableIdentity(char* reply,size_t capacity) {
  uint16_t identity;
  if (!identify(identity)) {snprintf(reply,capacity,"Err - charger unavailable; power SOLAR/VIN"); return false;}
  if (((identity>>8)&15)!=6 || ((identity&15)!=0 && (identity&15)!=4)) {
    snprintf(reply,capacity,"Err - writes require LTC4162-FST on this 4S carrier; detected %s cells=%u",
             chemistryName((identity>>8)&15),identity&15); return false;
  }
  return true;
}
bool Controller::update(uint8_t reg,uint16_t mask,uint16_t bits) {
  if (!mask || (mask&~writableMask(reg)) || (bits&~mask)) return false;
  uint16_t old;
  if (!bus.read(CHARGER,reg,old)) return false;
  uint16_t desired=(old&~mask)|bits;
  if (reg==0x14) desired&=~0x10; // Never replay the one-shot run_bsr bit.
  if ((old&mask)==bits) return true;
  if (!bus.write(CHARGER,reg,desired)) return false;
  uint16_t checked;
  return bus.read(CHARGER,reg,checked) && (checked&mask)==bits;
}
bool Controller::save(const Settings& candidate) {
  if (!candidate.valid() || !storage_healthy || !store.available()) return false;
  uint8_t image[Settings::IMAGE_SIZE]; candidate.encode(image);
  if (!store.save(image,sizeof(image))) {storage_healthy=false; apply_ok=false; return false;}
  loaded_backup=false; settings=candidate; return true;
}

void Controller::service(uint32_t now) {
  if (!started || (serviced && static_cast<int32_t>(now-next_service)<0)) return;
  serviced=true;
  next_service=now+SERVICE_INTERVAL_MS;
  uint16_t identity;
  if (!identify(identity)) {trial_off=false; apply_ok=false; return;}
  apply_ok=true;
  if (!storage_healthy || ((identity>>8)&15)!=6 || (last_cells && last_cells!=4)) {
    trial_off=false; apply_ok=false; return;
  }
  if (trial_off) {
    // Losing solar input can reset the charger independently of the CPU.
    if (uint32_t(now-trial_last_check)>2*SERVICE_INTERVAL_MS) trial_started=now;
    trial_last_check=now;
    uint16_t live;
    if (!bus.read(CHARGER,CONFIG,live) || !(live&SUSPEND_CHARGER)) {
      trial_off=false;
    } else if (uint32_t(now-trial_started)>=REMOTE_OFF_TRIAL_MS) {
      Settings candidate=settings;
      candidate.masks[CONFIG]|=SUSPEND_CHARGER;
      candidate.values[CONFIG]|=SUSPEND_CHARGER;
      trial_off=false;
      if (!save(candidate)) return;
    }
  }
  // Suspend first. Only release after replaying the other charger settings.
  const bool manual_off=(settings.values[CONFIG]&SUSPEND_CHARGER)!=0;
  if (manual_off) voltage_hold=true;
  const bool suspend=trial_off || manual_off || holdForVoltage();
  if (suspend && !trial_off && !update(CONFIG,SUSPEND_CHARGER,SUSPEND_CHARGER)) {apply_ok=false; return;}
  uint16_t start,end;
  if (settings.masks[16] || settings.masks[17]) {
    if (!bus.read(CHARGER,16,start) || !bus.read(CHARGER,17,end)) {apply_ok=false; return;}
    const int16_t target_start=settings.masks[16] ? settings.values[16] : start;
    const int16_t target_end=settings.masks[17] ? settings.values[17] : end;
    if (target_start>=target_end) {apply_ok=false; return;}
    const bool end_first=target_start>=static_cast<int16_t>(end);
    const uint8_t order[2]={uint8_t(end_first?17:16),uint8_t(end_first?16:17)};
    for (uint8_t reg:order) if (settings.masks[reg]
        && !update(reg,settings.masks[reg],settings.values[reg])) {apply_ok=false; return;}
  }
  // JEITA must be disabled before applying manual targets. When enabled, the
  // chip continuously replaces those targets from its fixed JEITA table.
  uint16_t config;
  if (!bus.read(CHARGER,0x29,config)) {apply_ok=false; return;}
  if (settings.masks[0x29] && !update(0x29,settings.masks[0x29],settings.values[0x29])) {apply_ok=false; return;}
  const bool jeita=settings.masks[0x29]&1 ? settings.values[0x29]&1 : config&1;
  for (unsigned reg=1;reg<Settings::REG_COUNT;++reg) {
    if (reg==16 || reg==17) continue;
    if (reg==0x29 || (jeita && (reg==0x1a || reg==0x1b))) continue;
    uint16_t mask=settings.masks[reg];
    if (reg==CONFIG) mask&=~SUSPEND_CHARGER;
    if (mask && !update(reg,mask,settings.values[reg]&mask)) {apply_ok=false; return;}
  }
  if (!suspend) apply_ok=update(CONFIG,SUSPEND_CHARGER,0);
}

bool Controller::readMonitorVoltage(uint16_t& raw) {
  uint16_t manufacturer,device;
  return bus.read(MONITOR,0xfe,manufacturer) && manufacturer==0x5449
      && bus.read(MONITOR,0xff,device) && (device&0xfff0)==0x2270
      && bus.read(MONITOR,2,raw);
}

bool Controller::holdForVoltage() {
  uint16_t raw;
  if (!readMonitorVoltage(raw) || !raw) {
    voltage_hold=true;
    return true;
  }
  // INA260 bus voltage is 1.25 mV/count. Compare in quarter-millivolts.
  const uint32_t measured=uint32_t(raw)*5;
  if (measured>=uint32_t(settings.charge_stop_mv)*4) voltage_hold=true;
  else if (measured<=uint32_t(settings.charge_resume_mv)*4) voltage_hold=false;
  return voltage_hold;
}

bool Controller::readMonitor(float& volts,float& amps,float& watts) {
  uint16_t v,i,p;
  if (!readMonitorVoltage(v) || !bus.read(MONITOR,1,i) || !bus.read(MONITOR,3,p)) return false;
  volts=v*0.00125f; amps=static_cast<int16_t>(i)*0.00125f; watts=p*0.01f;
  return true;
}
bool Controller::snapshot(Snapshot& result) {
  result=Snapshot{};
  result.vin_v=result.vout_v=result.charger_vbat_v=result.ibat_a=result.iin_a=NAN;
  result.die_c=result.ntc_c=result.bsr_ohm=NAN;
  result.monitor=readMonitor(result.battery_v,result.battery_a,result.battery_w);
  uint16_t id;
  if (!identify(id)) return result.monitor;
  result.identity=id; result.chemistry=(id>>8)&15; result.cells=id&15;
  const uint8_t regs[]={0x14,0x29,0x34,0x35,0x39,0x4a,0x36,0x37,0x38,
      0x3a,0x3b,0x3c,0x3d,0x3e,0x3f,0x40,0x41};
  uint16_t* values[]={&result.config,&result.charger_config,&result.state,&result.status,
      &result.system,&result.telemetry,&result.alerts[0],&result.alerts[1],&result.alerts[2],
      &result.raw_vbat,&result.raw_vin,&result.raw_vout,&result.raw_ibat,&result.raw_iin,
      &result.raw_die,&result.raw_ntc,&result.raw_bsr};
  for (unsigned i=0;i<sizeof(regs);++i) if (!bus.read(CHARGER,regs[i],*values[i])) return result.monitor;
  result.charger=true;
  // Do not label a retained ADC bank as a live reading while its ADC is off.
  result.adc_active=(result.telemetry&1) && ((result.config&4) || (result.system&4));
  if (!result.adc_active || result.chemistry<4 || result.chemistry>6) return true;
  result.vin_v=static_cast<int16_t>(result.raw_vin)*0.001649f;
  result.vout_v=static_cast<int16_t>(result.raw_vout)*0.001653f;
  if (result.cells) {
    result.charger_vbat_v=static_cast<int16_t>(result.raw_vbat)*result.cells*0.0001924f;
    if (!(result.telemetry&2))
      result.bsr_ohm=result.raw_bsr*result.cells*settings.rsnsb_mohm/500000.0f;
  }
  result.ibat_a=static_cast<int16_t>(result.raw_ibat)*0.001466f/settings.rsnsb_mohm;
  result.iin_a=static_cast<int16_t>(result.raw_iin)*0.001466f/settings.rsnsi_mohm;
  result.die_c=static_cast<int16_t>(result.raw_die)*0.0215f-264.4f;
  const int raw=static_cast<int16_t>(result.raw_ntc);
  if (raw>0 && raw<21684) {
    const float resistance=settings.ntc_bias_ohm*raw/(21829.0f-raw);
    result.ntc_c=1.0f/(1.0f/298.15f+logf(resistance/settings.ntc_r25_ohm)/settings.ntc_beta)-273.15f;
  }
  return true;
}

bool Controller::setField(const char* key,const char* value,char* reply,size_t capacity,bool local,uint32_t now) {
  const bool stop=!strcmp(key,"charge.stop_mv");
  if (stop || !strcmp(key,"charge.resume_mv")) {
    unsigned mv;
    Settings candidate=settings;
    if (!integer(value,14400,mv) || !mv) {snprintf(reply,capacity,"Err - expected 1..14400 mV"); return true;}
    (stop ? candidate.charge_stop_mv : candidate.charge_resume_mv)=mv;
    if (!candidate.valid()) {snprintf(reply,capacity,"Err - charge.resume_mv must be below charge.stop_mv"); return true;}
    if (!verifyWritableIdentity(reply,capacity)) return true;
    if (!save(candidate)) {snprintf(reply,capacity,"Err - settings save unconfirmed; use mppt reload"); return true;}
    serviced=false;
    snprintf(reply,capacity,"OK - %s=%u mV saved",key,mv);
    return true;
  }
  const char* calibration[]={"cal.rsnsb_mohm","cal.rsnsi_mohm","cal.ntc_bias_ohm","cal.ntc_r25_ohm","cal.ntc_beta"};
  for (unsigned i=0;i<5;++i) if (!strcmp(key,calibration[i])) {
    double parsed;
    Settings candidate=settings;
    float* targets[]={&candidate.rsnsb_mohm,&candidate.rsnsi_mohm,&candidate.ntc_bias_ohm,
                      &candidate.ntc_r25_ohm,&candidate.ntc_beta};
    if (!number(value,parsed)) {snprintf(reply,capacity,"Err - finite numeric calibration required"); return true;}
    *targets[i]=parsed; candidate.calibration_user_mask|=1<<i;
    if (!candidate.valid()) snprintf(reply,capacity,"Err - calibration out of range");
    else if (!save(candidate)) snprintf(reply,capacity,"Err - settings save unconfirmed; use mppt reload");
    else snprintf(reply,capacity,"OK - %s=%.6g saved; register targets unchanged",key,parsed);
    return true;
  }
  const Field* field=findField(key);
  if (!field) {snprintf(reply,capacity,"Err - unknown setting; get mppt fields <page>"); return true;}
  if (field->access!=Write) {
    snprintf(reply,capacity,"Err - %s is %s",key,field->access==Locked ? "locked by FST charging profile" : "read-only (hardware/state)"); return true;
  }
  if (!storage_healthy || !store.available()) {snprintf(reply,capacity,"Err - settings storage unavailable; use mppt reload"); return true;}
  if (!verifyWritableIdentity(reply,capacity)) return true;
  uint16_t bits;
  // A valid cell count is needed for per-cell-scaled alert thresholds. Do not
  // substitute the last known count while the hardware reports unknown.
  uint16_t identity;
  if (!identify(identity) || !encode(*field,value,settings,identity&15,bits)) {
    snprintf(reply,capacity,"Err - invalid value/range or cell count unknown; %s (%s)",key,field->unit); return true;
  }
  uint16_t other;
  if (field->reg==0x16 && (!bus.read(CHARGER,0x14,other) || (other&2))) {
    snprintf(reply,capacity,"Err - disable mppt.enabled before setting manual input.minimum_mv"); return true;
  }
  if ((field->reg==0x1a || field->reg==0x1b)
      && (!bus.read(CHARGER,0x29,other) || (other&1))) {
    snprintf(reply,capacity,"Err - disable jeita.enabled before setting manual charge targets"); return true;
  }
  if (field->reg==16 || field->reg==17) {
    if (!bus.read(CHARGER,field->reg==16?17:16,other)
        || (field->reg==16 ? static_cast<int16_t>(bits)>=static_cast<int16_t>(other)
                          : static_cast<int16_t>(bits)<=static_cast<int16_t>(other))) {
      snprintf(reply,capacity,"Err - thermal.start_c must be below thermal.end_c"); return true;
    }
  }
  if (field->reg==0x1c && static_cast<int16_t>(bits)<0) {
    snprintf(reply,capacity,"Err - termination current must be nonnegative"); return true;
  }
  uint16_t before;
  if (!bus.read(CHARGER,field->reg,before)) {snprintf(reply,capacity,"Err - charger read failed"); return true;}
  Settings candidate=settings;
  candidate.masks[field->reg]|=field->mask;
  candidate.values[field->reg]=(candidate.values[field->reg]&~field->mask)|bits;
  if (!candidate.valid()) {snprintf(reply,capacity,"Err - setting conflicts with saved policy; use mppt forget"); return true;}
  // Save the operator's preference separately from the effective suspend bit.
  const bool charge_enable=field->reg==CONFIG && field->mask==SUSPEND_CHARGER;
  const bool auto_requested=charge_enable && !bits;
  const bool try_off=charge_enable && !auto_requested && !local && !(settings.values[CONFIG]&SUSPEND_CHARGER);
  if (try_off) {
    uint16_t state;
    if (!bus.read(CHARGER,0x34,state) || !state || (state&BATTERY_DETECTING_OR_FAULT)) {
      trial_off=false;
      snprintf(reply,capacity,"Err - charger cannot confirm battery; remote off not applied"); return true;
    }
  }
  if (charge_enable) {
    if (auto_requested && last_cells && last_cells!=4) {
      snprintf(reply,capacity,"Err - automatic charging requires a 4S carrier"); return true;
    }
    if (!auto_requested) voltage_hold=true;
    else if (holdForVoltage()) bits=SUSPEND_CHARGER;
  }
  if (!update(field->reg,field->mask,bits)) {
    if (charge_enable && bits) {
      snprintf(reply,capacity,"Err - charger suspend unconfirmed"); return true;
    }
    const bool restored=update(field->reg,field->mask,before&field->mask);
    snprintf(reply,capacity,"Err - write/readback failed; rollback=%s",restored?"verified":"unconfirmed"); return true;
  }
  if (try_off) {
    if (!trial_off || !(before&SUSPEND_CHARGER)) trial_started=trial_last_check=now;
    trial_off=true;
    snprintf(reply,capacity,"> charger held off; checking 45s before save; get mppt saved");
    return true;
  }
  if (!save(candidate)) {
    if (charge_enable && bits) {
      snprintf(reply,capacity,"Err - save unconfirmed; charger held off; use mppt reload"); return true;
    }
    const bool restored=update(field->reg,field->mask,before&field->mask);
    snprintf(reply,capacity,"Err - save unconfirmed; live rollback=%s; use mppt reload",restored?"verified":"unconfirmed"); return true;
  }
  apply_ok=true;
  if (charge_enable) trial_off=false;
  if (auto_requested) {
    snprintf(reply,capacity,"OK - automatic charging enabled; charger %s",bits?"held off":"enabled");
    return true;
  }
  snprintf(reply,capacity,"OK - %s=%.6g %s saved, readback verified",key,
           decode(*field,bits,settings,identity&15),field->unit);
  return true;
}

bool Controller::getField(const char* key,char* reply,size_t capacity) {
  const bool stop=!strcmp(key,"charge.stop_mv");
  if (stop || !strcmp(key,"charge.resume_mv")) {
    snprintf(reply,capacity,"> %s=%u mV",key,stop?settings.charge_stop_mv:settings.charge_resume_mv);
    return true;
  }
  const char* calibration[]={"cal.rsnsb_mohm","cal.rsnsi_mohm","cal.ntc_bias_ohm","cal.ntc_r25_ohm","cal.ntc_beta"};
  const float values[]={settings.rsnsb_mohm,settings.rsnsi_mohm,settings.ntc_bias_ohm,settings.ntc_r25_ohm,settings.ntc_beta};
  for (unsigned i=0;i<5;++i) if (!strcmp(key,calibration[i])) {
    snprintf(reply,capacity,"> %s=%.6g source=%s",key,double(values[i]),settings.calibration_user_mask&(1<<i)?"user":"reference"); return true;
  }
  const Field* field=findField(key);
  if (!field) return false;
  uint16_t identity,word;
  if (!identify(identity) || !bus.read(CHARGER,field->reg,word)) {
    snprintf(reply,capacity,"Err - charger unavailable; power SOLAR/VIN"); return true;
  }
  if ((field->kind==CellMv || field->kind==BatteryMv || field->kind==BsrOhm)
      && (((identity>>8)&15)<4 || ((identity>>8)&15)>6)) {
    snprintf(reply,capacity,"> %s=unsupported chemistry; raw=%04X",key,word); return true;
  }
  if (field->access==Read && ((field->reg>=0x3a && field->reg<=0x41) || field->reg==0x47 || field->reg==0x48)) {
    uint16_t cfg,system,valid;
    if (!bus.read(CHARGER,0x14,cfg) || !bus.read(CHARGER,0x39,system)
        || !bus.read(CHARGER,0x4a,valid) || !(valid&1) || (!(cfg&4) && !(system&4))) {
      snprintf(reply,capacity,"> %s=unavailable ADC inactive; raw=%04X",key,word); return true;
    }
  }
  const double value=decode(*field,word,settings,identity&15);
  if (!isfinite(value)) snprintf(reply,capacity,"> %s=unknown cells; raw=%04X",key,word);
  else if (field->kind==Boolean || field->kind==Inverted)
    snprintf(reply,capacity,"> %s=%s raw=%04X",key,value?"on":"off",word);
  else snprintf(reply,capacity,"> %s=%.7g %s raw=%04X%s",key,value,field->unit,word,
                field->access==Locked?" locked":"");
  return true;
}

bool Controller::action(const char* args,bool local,char* reply,size_t capacity) {
  if (!strcmp(args,"reset")) {
    if (!local) {snprintf(reply,capacity,"Err - reset requires local USB"); return true;}
    Settings candidate; candidate.defaults();
    storage_healthy=store.available();
    if (!save(candidate)) snprintf(reply,capacity,"Err - default policy save unconfirmed");
    else {trial_off=false; snprintf(reply,capacity,"OK - saved policy/calibration reset; live charger unchanged");}
    return true;
  }
  if (!strcmp(args,"reload")) {
    reload();
    snprintf(reply,capacity,storage_healthy?"OK - saved settings loaded; apply on service":"Err - saved settings invalid/unavailable"); return true;
  }
  if (!strcmp(args,"forget")) {
    Settings candidate=settings;
    memset(candidate.masks,0,sizeof(candidate.masks)); memset(candidate.values,0,sizeof(candidate.values));
    if (!save(candidate)) snprintf(reply,capacity,"Err - override removal save unconfirmed");
    else {trial_off=false; snprintf(reply,capacity,"OK - saved register overrides removed; live charger unchanged");}
    return true;
  }
  if (!strcmp(args,"bsr run") || !strcmp(args,"ship arm") || !strcmp(args,"ship disarm")) {
    const bool ship=!strncmp(args,"ship ",5);
    if (ship && !local) {snprintf(reply,capacity,"Err - ship mode requires local USB"); return true;}
    if (!verifyWritableIdentity(reply,capacity)) return true;
    bool ok;
    if (ship) ok=bus.write(CHARGER,0x19,!strcmp(args,"ship arm")?21325:0);
    else {
      uint16_t cfg; ok=bus.read(CHARGER,0x14,cfg) && bus.write(CHARGER,0x14,cfg|0x10);
    }
    snprintf(reply,capacity,ok?"OK - action requested; not saved":"Err - action write failed"); return true;
  }
  const char* groups[]={"limits","states","status"};
  for (unsigned i=0;i<3;++i) {
    char prefix[32]; snprintf(prefix,sizeof(prefix),"alerts clear %s ",groups[i]);
    if (strncmp(args,prefix,strlen(prefix))) continue;
    unsigned mask;
    const uint16_t allowed[]={0xcfff,0x1b7f,0x3f};
    if (!integer(args+strlen(prefix),65535,mask) || !mask || (mask&~allowed[i])) {
      snprintf(reply,capacity,"Err - invalid alert mask"); return true;
    }
    if (!verifyWritableIdentity(reply,capacity)) return true;
    // Alerts are W0C: ones preserve all unselected (including newly arrived) alerts.
    const bool ok=bus.write(CHARGER,0x36+i,static_cast<uint16_t>(~mask));
    snprintf(reply,capacity,ok?"OK - selected alerts cleared":"Err - alert clear failed"); return true;
  }
  return false;
}

bool Controller::handle(const char* command,char* reply,size_t capacity,bool local,uint32_t now) {
  if (!command || !reply || capacity<2) return false;
  const bool get=!strncmp(command,"get mppt",8) && (!command[8] || command[8]==' ');
  const bool set=!strncmp(command,"set mppt ",9);
  const bool act=!strncmp(command,"mppt ",5);
  if (!get && !set && !act) return false;
  if (set) {
    char key[48],value[48],tail;
    if (sscanf(command+9,"%47s %47s %c",key,value,&tail)!=2)
      snprintf(reply,capacity,"Err - use set mppt <field> <value>; get mppt fields <page>");
    else setField(key,value,reply,capacity,local,now);
    return true;
  }
  if (act) {
    if (!action(command+5,local,reply,capacity)) snprintf(reply,capacity,"Err - mppt reload|forget|reset|bsr run|ship arm/disarm|alerts clear <group> <mask>");
    return true;
  }
  const char* key=command+8; if (*key==' ') ++key;
  if (!strncmp(key,"fields",6) && (!key[6] || key[6]==' ')) {
    unsigned page=0;
    if (key[6] && !integer(key+7,100,page)) {snprintf(reply,capacity,"Err - fields page must be an integer"); return true;}
    const size_t start=page*3;
    if (start>=FIELD_COUNT) {snprintf(reply,capacity,"> end; fields=%u",unsigned(FIELD_COUNT)); return true;}
    size_t used=snprintf(reply,capacity,"> page=%u ",page);
    for (size_t i=start;i<FIELD_COUNT && i<start+3 && used<capacity;++i)
      used+=snprintf(reply+used,capacity-used,"%s[%s]%s",fields[i].name,
         fields[i].access==Locked?"locked":fields[i].access==Read?"ro":"rw",i+1<start+3?" ":"");
    return true;
  }
  if (!strncmp(key,"raw ",4)) {
    unsigned reg;
    if (!integer(key+4,255,reg) || !readableRegister(reg)) snprintf(reply,capacity,"Err - unknown/read-protected register");
    else {uint16_t id,value; if (!identify(id) || !bus.read(CHARGER,reg,value)) snprintf(reply,capacity,"Err - charger read failed");
      else snprintf(reply,capacity,"> addr=68 reg=%02X raw=%04X",reg,value);}
    return true;
  }
  if (!strcmp(key,"saved")) {
    unsigned count=0; for (uint16_t mask:settings.masks) if (mask) ++count;
    snprintf(reply,capacity,"> storage=%s overrides=%u apply=%s rsnsb=%.3gmOhm rsnsi=%.3gmOhm cal_user=%02X%s charge=%s",
      storage_healthy?"ok":"unavailable",count,apply_ok?"verified":"pending",double(settings.rsnsb_mohm),double(settings.rsnsi_mohm),settings.calibration_user_mask,
      storage_healthy && loaded_backup?" source=backup":"",
      trial_off?"trial":(settings.values[CONFIG]&SUSPEND_CHARGER)?"off":"auto"); return true;
  }
  if (*key && getField(key,reply,capacity)) return true;
  Snapshot s; snapshot(s);
  if (!*key || !strcmp(key,"info")) {
    if (!s.charger) snprintf(reply,capacity,"> LTC4162=unavailable INA260=%s; power SOLAR/VIN for charger access",s.monitor?"online":"unavailable");
    else snprintf(reply,capacity,"> LTC4162-%s chem=%u cells=%u last=%u MPPT=%s JEITA=%s state=%s ADC=%s saved=%s",
      chemistryName(s.chemistry),s.chemistry,s.cells,last_cells,s.config&2?"on":"off",
      s.charger_config&1?"on":"off",stateName(s.state),s.adc_active?"on":"off",storage_healthy?"ok":"bad");
  } else if (!strcmp(key,"battery")) {
    if (!s.monitor) snprintf(reply,capacity,"Err - INA260 unavailable");
    else snprintf(reply,capacity,"> INA260 %.4fV %+.5fA %.3fW; charger=%.4fV %+.5fA NTC=%.2fC model=%s",
      double(s.battery_v),double(s.battery_a),double(s.battery_w),double(s.charger_vbat_v),double(s.ibat_a),double(s.ntc_c),
      (settings.calibration_user_mask&28)==28?"user":"reference");
  } else if (!strcmp(key,"power")) {
    if (!s.charger) snprintf(reply,capacity,"Err - charger unavailable; power SOLAR/VIN");
    else snprintf(reply,capacity,"> ADC=%s VIN=%.4fV IIN=%+.5fA VOUT=%.4fV die=%.2fC NTC=%.2fC BSR=%.5fohm",
       s.adc_active?"on":"off",double(s.vin_v),double(s.iin_a),double(s.vout_v),double(s.die_c),double(s.ntc_c),double(s.bsr_ohm));
  } else if (!strcmp(key,"alerts")) {
    if (!s.charger) snprintf(reply,capacity,"Err - charger unavailable");
    else snprintf(reply,capacity,"> limits=%04X states=%04X status=%04X state=%04X/%s charge_status=%04X system=%04X telemetry=%04X",
      s.alerts[0],s.alerts[1],s.alerts[2],s.state,stateName(s.state),s.status,s.system,s.telemetry);
  } else snprintf(reply,capacity,"Err - use get mppt info|battery|power|alerts|saved|fields <page>|raw <reg>|<field>");
  return true;
}

}} // namespace mesh::solar
#endif
