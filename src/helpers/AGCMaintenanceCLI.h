#pragma once

#include <Dispatcher.h>
#include "CLICommandUtils.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// Runtime-only maintenance controls shared with the Companion text parser.
// Primary saved interval aliases stay in CommonRadioPrefs so its normal
// dirty/save/rollback transaction remains authoritative.
inline bool handleAGCMaintenanceCommand(Radio* radio,const char* command,
    uint32_t primary_seconds,char* reply,size_t capacity) {
  if(!command || !reply || !capacity) return false;
  const size_t diagnostic_capacity=capacity<157?capacity:157;
  if(strcmp(command,"get agc.old")==0) {
    if(!radio || !radio->supportsWarmRxRefresh()) cli::formatError(reply,capacity,"unsupported");
    else snprintf(reply,capacity,"> %lu",(unsigned long)radio->getOldAGCInterval());
    return true;
  }
  if(strncmp(command,"set agc.old ",12)==0) {
    uint32_t seconds=0;
    if(!radio || !radio->supportsWarmRxRefresh()) cli::formatError(reply,capacity,"unsupported");
    else if(!cli::parseUnsignedIntegerStrict(command+12,seconds) || seconds>86400)
      cli::formatError(reply,capacity,"old AGC interval must be 0-86400 seconds");
    else if(!radio->setOldAGCInterval(seconds)) cli::formatError(reply,capacity,"failed to set old AGC interval");
    else snprintf(reply,capacity,"OK - old full-reset interval %lu seconds (RAM only; disabled after reboot)",(unsigned long)seconds);
    return true;
  }
  if(strcmp(command,"get agc.status")==0 || strcmp(command,"get agc.timing")==0
      || strcmp(command,"get agc.recovery")==0) {
    const uint8_t page=strcmp(command,"get agc.timing")==0?1:strcmp(command,"get agc.recovery")==0?2:0;
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
}

} // namespace mesh
