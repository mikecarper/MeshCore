#pragma once

#include <Dispatcher.h>
#include "CLICommandUtils.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// An explicitly compiled lab-only override. This helper performs no save,
// radio command, or scheduler attempt; the next Dispatcher loop configures a
// future fixed deadline. The separate numeric getter keeps saved status honest.
inline bool handleWarmRxLabTimerCommand(Radio* radio,const char* command,
    char* reply,size_t capacity) {
#if MESH_WARM_RX_LAB_TIMER
  if(!command || !reply || !capacity) return false;
  const bool get=strcmp(command,"get agc.test.ms")==0;
  const bool set=strncmp(command,"set agc.test.ms ",16)==0;
  if(!get && !set) return false;
  if(!radio || !radio->supportsWarmRxRefresh()) cli::formatError(reply,capacity,"unsupported");
  else if(get) {
    uint32_t interval_ms=0;
    if(radio->getWarmRxLabInterval(interval_ms)) snprintf(reply,capacity,"> %lu",(unsigned long)interval_ms);
    else snprintf(reply,capacity,"> off");
  } else {
    const char* value=command+16;
    const bool active=strcmp(value,"off")!=0;
    uint32_t interval_ms=0;
    if(active && (!cli::parseUnsignedIntegerStrict(value,interval_ms) || interval_ms>60000))
      cli::formatError(reply,capacity,"warm lab interval must be off or 0-60000 ms");
    else if(!radio->setWarmRxLabInterval(active,interval_ms)) cli::formatError(reply,capacity,"failed to set warm lab interval");
    else if(active) snprintf(reply,capacity,"OK - warm lab interval %lu ms (RAM only)",(unsigned long)interval_ms);
    else snprintf(reply,capacity,"OK - warm lab timer off (saved interval inherited)");
  }
  return true;
#else
  (void)radio; (void)command; (void)reply; (void)capacity;
  return false;
#endif
}

// Only explicitly compiled bench firmware can change the compensated sleep
// request. It does not save preferences or trigger a radio operation.
inline bool handleRxpsPulseLabCommand(Radio* radio,const char* command,char* reply,size_t capacity) {
#if MESH_RXPS_PULSE_LAB
  const bool get=strcmp(command,"get agc.test.sleep_us")==0;
  const bool set=strncmp(command,"set agc.test.sleep_us ",22)==0;
  if(!get && !set) return false;
  if(!radio || !radio->supportsWarmRxRefresh()) cli::formatError(reply,capacity,"unsupported");
  else if(get) snprintf(reply,capacity,"> %lu",(unsigned long)radio->getAGCPulseSleep());
  else {
    uint32_t us=0;
    if(!cli::parseUnsignedIntegerStrict(command+22,us) || (us!=2616 && us!=3000 && us!=3116 && us!=4000 && us!=6554))
      cli::formatError(reply,capacity,"pulse sleep must be 2616, 3000, 3116, 4000 or 6554 us");
    else if(!radio->setAGCPulseSleep(us)) cli::formatError(reply,capacity,"pulse pending or timing unsupported");
    else snprintf(reply,capacity,"OK - pulse sleep request %lu us (RAM only)",(unsigned long)us);
  }
  return true;
#else
  (void)radio;(void)command;(void)reply;(void)capacity;return false;
#endif
}

// The independent legacy timer also exists on radios without warm refresh.
// Changing its RAM-only interval does not perform a reset here.
inline bool handleOldAGCIntervalCommand(Radio* radio, const char* command,
    char* reply, size_t capacity) {
  const bool get = strcmp(command, "get agc.old") == 0;
  const bool set = strncmp(command, "set agc.old ", 12) == 0;
  if (!get && !set) return false;
  if (!radio || !radio->supportsOldAGC()) cli::formatError(reply, capacity, "unsupported");
  else if (get) snprintf(reply, capacity, "> %lu", (unsigned long)radio->getOldAGCInterval());
  else {
    uint32_t seconds = 0;
    if (!cli::parseUnsignedIntegerStrict(command + 12, seconds) || seconds > 86400)
      cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("old AGC interval must be 0-86400 seconds", "old AGC: 0-86400s"));
    else if (!radio->setOldAGCInterval(seconds)) cli::formatError(reply, capacity, MESH_CLI_REPLY_TEXT("failed to set old AGC interval", "old AGC unchanged"));
    else snprintf(reply, capacity, MESH_CLI_REPLY_TEXT("OK - old full-reset interval %lu seconds (RAM only; disabled after reboot)", "OK - old AGC %lus (RAM; reboot off)"), (unsigned long)seconds);
  }
  return true;
}

// Runtime-only maintenance controls shared with the Companion text parser.
// Primary saved interval aliases stay in CommonRadioPrefs so its normal
// dirty/save/rollback transaction remains authoritative.
inline bool handleAGCMaintenanceCommand(Radio* radio,const char* command,
    uint32_t primary_seconds,char* reply,size_t capacity) {
  if(!command || !reply || !capacity) return false;
  if (handleOldAGCIntervalCommand(radio, command, reply, capacity)) return true;
#if defined(STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC) && STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
#error "Compact STM32WL Companion AGC replies require diagnostics to be absent"
#endif
  // This radio has no warm-maintenance or diagnostic implementation. Keep
  // the same exact command matches and replies without unreachable dispatch.
  (void)radio; (void)primary_seconds;
  if(strncmp(command,"get agc.",8)==0) {
    // Share suffixes instead of retaining each old/reset diagnostic name.
    static const char names[]="status\0timing\0errors\0recovery\0pulse\0sleep\0checks\0scan\0";
    const char* key=command+8;
    bool legacy=false;
    if(strncmp(key,"old.",4)==0) { key+=4;legacy=true; }
    else if(strncmp(key,"reset.",6)==0) { key+=6;legacy=true; }
    unsigned index=0;
    for(const char* item=names; *item; item+=strlen(item)+1,++index) {
      if((legacy?index<3:index!=2) && strcmp(key,item)==0) {
        cli::formatError(reply,capacity,"unsupported"); return true;
      }
    }
  }
  return false;
#else
  if(handleWarmRxLabTimerCommand(radio,command,reply,capacity) || handleRxpsPulseLabCommand(radio,command,reply,capacity)) return true;
  const size_t diagnostic_capacity=capacity<157?capacity:157;
  if(strcmp(command,"get agc.status")==0 || strcmp(command,"get agc.timing")==0
      || strcmp(command,"get agc.recovery")==0 || strcmp(command,"get agc.pulse")==0
      || strcmp(command,"get agc.sleep")==0 || strcmp(command,"get agc.checks")==0
      || strcmp(command,"get agc.scan")==0) {
    const uint8_t page=strcmp(command,"get agc.timing")==0?1:strcmp(command,"get agc.recovery")==0?2:
        strcmp(command,"get agc.pulse")==0?3:strcmp(command,"get agc.sleep")==0?4:strcmp(command,"get agc.checks")==0?5:
        strcmp(command,"get agc.scan")==0?6:0;
    if(!radio || !radio->formatWarmRxRefresh(page,primary_seconds,reply,diagnostic_capacity))
      cli::formatError(reply,capacity,"unsupported");
    return true;
  }
  if(strcmp(command,"get agc.old.status")==0 || strcmp(command,"get agc.old.timing")==0
      || strcmp(command,"get agc.old.errors")==0 || strcmp(command,"get agc.reset.status")==0
      || strcmp(command,"get agc.reset.timing")==0 || strcmp(command,"get agc.reset.errors")==0) {
#if defined(MESH_AGC_TIMING_DIAGNOSTICS) && MESH_AGC_TIMING_DIAGNOSTICS
    const uint8_t page=(strcmp(command,"get agc.old.timing")==0 || strcmp(command,"get agc.reset.timing")==0)?1
        :(strcmp(command,"get agc.old.errors")==0 || strcmp(command,"get agc.reset.errors")==0)?2:0;
    if(!radio || !radio->formatAGCTimingDiagnostics(page,reply,diagnostic_capacity))
      cli::formatError(reply,capacity,"unsupported");
#else
    cli::formatError(reply,capacity,"unsupported");
#endif
    return true;
  }
  return false;
#endif
}

} // namespace mesh
