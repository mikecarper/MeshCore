#include <cassert>
#include <initializer_list>
typedef unsigned int u32;
#define RUNNING 0x5CA90001u
#define S_CAD 1u
#define IRQ_OWN ((1u<<5)|(1u<<6)|(1u<<7)|(1u<<9)|(1u<<20)|(1u<<21))
#define IRQ_RX_DONE (1u<<18)
#define IRQ_CRC (1u<<22)
static int lab_can_pause(u32 running, u32 state, u32 owns, u32 irq, u32 pending) {
  return running == RUNNING && state == S_CAD && owns &&
      !(irq & (IRQ_OWN | IRQ_RX_DONE | IRQ_CRC)) && !(pending & ~1u);
}
int main() {
 assert(lab_can_pause(RUNNING,S_CAD,1,0,0)); assert(lab_can_pause(RUNNING,S_CAD,1,0,1));
 assert(!lab_can_pause(0,S_CAD,1,0,0)); assert(!lab_can_pause(RUNNING,2,1,0,0));
 assert(!lab_can_pause(RUNNING,3,1,0,0)); assert(!lab_can_pause(RUNNING,4,1,0,0));
 assert(!lab_can_pause(RUNNING,S_CAD,0,0,0));
 for(unsigned b: {5,6,7,9,18,20,21,22}) assert(!lab_can_pause(RUNNING,S_CAD,1,1u<<b,0));
 for(unsigned b=1;b<31;b++) assert(!lab_can_pause(RUNNING,S_CAD,1,0,1u<<b));
}
