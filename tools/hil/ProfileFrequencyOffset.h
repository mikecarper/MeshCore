#pragma once
#include <stdint.h>
#include <stddef.h>
// HIL-only sub-float-resolution frequency detour. Never alters caller bytes.
static uint32_t hilFrequencyOffsetSteps=0;
struct HilFrequencyOffsetScope {
  uint32_t previous;
  explicit HilFrequencyOffsetScope(uint32_t steps):previous(hilFrequencyOffsetSteps) { hilFrequencyOffsetSteps=steps; }
  ~HilFrequencyOffsetScope() { hilFrequencyOffsetSteps=previous; }
  HilFrequencyOffsetScope(const HilFrequencyOffsetScope&)=delete;
  HilFrequencyOffsetScope& operator=(const HilFrequencyOffsetScope&)=delete;
};
inline const uint8_t* hilFrequencyCommand(const uint8_t* original,size_t len,uint8_t (&shifted)[5]) {
  if(!original || len!=5 || original[0]!=0x86 || !hilFrequencyOffsetSteps) return original;
  uint32_t word=uint32_t(original[1])<<24 | uint32_t(original[2])<<16 | uint32_t(original[3])<<8 | original[4];
  if(word>UINT32_MAX-hilFrequencyOffsetSteps) return original; // per-hop verification fails closed
  word+=hilFrequencyOffsetSteps;
  shifted[0]=0x86;shifted[1]=word>>24;shifted[2]=word>>16;shifted[3]=word>>8;shifted[4]=word;
  return shifted;
}
