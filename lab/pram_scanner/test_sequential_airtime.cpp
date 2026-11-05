#include <cassert>
#include <helpers/radiolib/W12PramSequentialLab.h>
using namespace mesh::w12pram;
int main() {
  uint32_t ms=0;
  Profile p={904525000,62500,5,5,32,11};
  assert(sequentialReceiveAirtime(p,5,true,50,ms) && ms==78);
  p.sf=8; assert(sequentialReceiveAirtime(p,6,true,50,ms) && ms==501);
  p.sf=7; p.bw_hz=250000;
  assert(sequentialReceiveAirtime(p,5,false,12,ms) && ms==33);
  p.sf=12; p.bw_hz=62500; p.preamble=128;
  assert(sequentialReceiveAirtime(p,8,true,255,ms) && ms==35931);
  assert(!sequentialReceiveAirtime(p,0,true,50,ms) && ms==0);
  assert(!sequentialReceiveAirtime(p,5,true,256,ms) && ms==0);
  p.bw_hz=0; assert(!sequentialReceiveAirtime(p,5,true,50,ms) && ms==0);
}
