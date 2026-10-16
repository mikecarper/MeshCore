#pragma once
struct Sequence { uintptr_t PTR; uint32_t CNT, REFRESH, ENDDELAY; };
struct Registers {
  uint32_t INTENCLR, SHORTS, ENABLE, MODE, COUNTERTOP, PRESCALER,
    DECODER, LOOP, EVENTS_STOPPED, EVENTS_LOOPSDONE, INTENSET;
  Sequence SEQ[2];
  uint32_t EVENTS_SEQEND[2], EVENTS_SEQSTARTED[2], TASKS_SEQSTART[2];
};
static Registers registers;
#define NRF_PWM3 (&registers)
#define PWM_MODE_UPDOWN_Up 0
#define PWM_PRESCALER_PRESCALER_DIV_1 0
#define PWM_DECODER_LOAD_Common 0
#define PWM_SHORTS_LOOPSDONE_SEQSTART0_Msk 4
#define PWM_INTENSET_SEQEND0_Msk 1
#define PWM_INTENSET_SEQEND1_Msk 2
#define PWM3_IRQn 45
inline void NVIC_DisableIRQ(int) {}
inline void NVIC_EnableIRQ(int) {}
inline void NVIC_ClearPendingIRQ(int) {}
inline void NVIC_SetPriority(int, int) {}
struct HardwarePWM {
  bool owned;
  bool takeOwnership(uint32_t) { if (owned) return false; return owned = true; }
  bool addPin(int) { return true; }
  void removePin(int) {}
  bool releaseOwnership(uint32_t) { owned = false; return true; }
};
static HardwarePWM HwPWM3;
