#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

namespace mesh { namespace w12pram {
// Diagnostic builds only instantiate this recorder. It never performs SPI or
// changes ownership, and retains the first failure through bounded recovery.
struct HandoffDiagnostics {
  struct Attempt {
    uint32_t number=0, at=0, op_irq=0, raw_irq=0;
    const char* origin="none";
    int16_t op_rc=-32768, standby_rc=-32768, fifo_rc=-32768;
    uint16_t fifo=0;
    uint8_t state=255, current=255, reason=0, detail=0;
    uint8_t restart_stage=0, major=0, minor=0, ident[4]={}, restart_hook=255;
    int16_t restart_rc=-32768;
    uint32_t dccm[5]={};int16_t dccm_rc[3]={-32768,-32768,-32768};
    int8_t guards[3]={-1,-1,-1}; // -1 means short-circuited, not observed false.
    bool software=false, dio=false, raw=false;
  } current, first_failure;
  uint32_t attempts=0, failures=0;
  void begin(const char* origin,uint32_t at) {
    current=Attempt{};current.number=++attempts;current.origin=origin;current.at=at;
  }
  void fail(uint8_t reason,uint8_t detail) {
    current.reason=reason;current.detail=detail;
    if(!failures)first_failure=current;
    ++failures;
  }
  void format(char* out,size_t cap) const {
    const auto& a=failures?first_failure:current;
    if(a.reason==2 && a.restart_stage==6) {
      snprintf(out,cap,"H n=%lu %s why=2 step=6 rc=%d v=%u/%u id=%02X%02X%02X%02X m=%08lX/%08lX/%08lX/%08lX/%08lX mr=%d/%d/%d bad=%lu",
        (unsigned long)a.number,a.origin,int(a.restart_rc),a.major,a.minor,a.ident[0],a.ident[1],a.ident[2],a.ident[3],
        (unsigned long)a.dccm[0],(unsigned long)a.dccm[1],(unsigned long)a.dccm[2],(unsigned long)a.dccm[3],(unsigned long)a.dccm[4],
        int(a.dccm_rc[0]),int(a.dccm_rc[1]),int(a.dccm_rc[2]),(unsigned long)failures);
      return;
    }
    if(a.reason==2) {
      snprintf(out,cap,"H n=%lu %s t=%lu why=2 step=%u rc=%d ver=%u/%u id=%02X%02X%02X%02X hook=%u sw=%u d=%u irq=%08lX f=%u/%d raw=%u bad=%lu",
        (unsigned long)a.number,a.origin,(unsigned long)a.at,a.restart_stage,int(a.restart_rc),a.major,a.minor,
        a.ident[0],a.ident[1],a.ident[2],a.ident[3],a.restart_hook,a.software,a.dio,
        (unsigned long)a.raw_irq,a.fifo,int(a.fifo_rc),a.raw,(unsigned long)failures);
      return;
    }
    snprintf(out,cap,"H n=%lu %s t=%lu why=%u/%u op=%d s/c=%u/%u qi=%08lX st=%d abc=%d/%d/%d sw=%u d=%u irq=%08lX f=%u/%d raw=%u bad=%lu",
      (unsigned long)a.number,a.origin,(unsigned long)a.at,a.reason,a.detail,int(a.op_rc),a.state,a.current,
      (unsigned long)a.op_irq,int(a.standby_rc),int(a.guards[0]),int(a.guards[1]),int(a.guards[2]),
      a.software,a.dio,(unsigned long)a.raw_irq,a.fifo,int(a.fifo_rc),a.raw,(unsigned long)failures);
  }
};
} }
