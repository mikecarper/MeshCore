#include <cassert>
#include <helpers/radiolib/W12PramSequentialLab.h>
using namespace mesh::w12pram;
int main() {
 Profile p[2]={{910525000,62500,7,5,32,11},{911150000,250000,7,5,32,12}};
 SequentialPlan plan={}; assert(sequentialChannelsDisjoint(p[0],p[1]));
 assert(sequentialPlan(p,0,5,8,plan));
 uint8_t sfs[SequentialMaxSlots+1]={}, n=0;
 assert(sequentialTxSFs(plan,0,7,sfs,n) && n==4);
 for (int i=0;i<4;++i) assert(sfs[i]==5+i);
 assert(sequentialTxSFs(plan,0,9,sfs,n) && n==5 && sfs[4]==9);
 assert(sequentialTxSFs(plan,1,7,sfs,n) && n==1 && sfs[0]==7);
 assert(!sequentialTxSFs(plan,1,13,sfs,n));
 p[1].freq_hz=910681250; assert(!sequentialChannelsDisjoint(p[0],p[1]));
 p[1].freq_hz++; assert(sequentialChannelsDisjoint(p[0],p[1]));
 p[1].bw_hz=0; assert(!sequentialChannelsDisjoint(p[0],p[1]));
}
