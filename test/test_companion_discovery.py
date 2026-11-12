"""Execute the merged discovery parser with truncated and unrelated radio data."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class CompanionDiscoveryTests(unittest.TestCase):
    def test_bounded_responses_and_request_lifetime(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        row_start = header.index('struct DiscoveredNode {')
        row = header[row_start:header.index('#endif', row_start)]
        parser = method(source, 'void MyMesh::checkControlDataForPendingDiscovery(')
        query = method(source, 'int MyMesh::getDiscoveredNodes(')
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
#include <initializer_list>
#define DISCOVERED_NODES_TABLE_SIZE 4
#define CTL_TYPE_NODE_DISCOVER_RESP 0x90
static uint32_t now=100;
uint32_t millis(){return now;}
struct StrHelper{static void strncpy(char* o,const char* i,size_t n){::strncpy(o,i,n-1);o[n-1]=0;}};
@DISCOVERED_NODE@
struct PreviousDiscoveredNode{uint8_t pubkey_prefix[9];float snr_in,snr_out;char name[32];uint8_t type;};
static_assert(sizeof(DiscoveredNode)==52,"Discovery fields must use natural compact alignment");
static_assert(sizeof(DiscoveredNode[4])+16==sizeof(PreviousDiscoveredNode[4]),
              "Keep all four discovery rows while freeing sixteen bytes per table");
static_assert(offsetof(DiscoveredNode,snr_in)%alignof(float)==0);
static_assert(offsetof(DiscoveredNode,snr_out)%alignof(float)==0);
struct ContactInfo{char name[32];};
struct Radio{float getLastSNR(){return 5.0f;}};
struct MyMesh {
  DiscoveredNode discovered_nodes[4]={};
  uint32_t disc_node_req_tag=123,disc_nodes_count=0,disc_node_req_at=100;
  bool disc_node_req_active=true;
  Radio radio;Radio* _radio=&radio;
  ContactInfo contact;
  MyMesh(){memset(contact.name,'A',32);}
  ContactInfo* lookupContactByPubKey(const uint8_t*,size_t){return &contact;}
  void checkControlDataForPendingDiscovery(uint8_t[],size_t);
  int getDiscoveredNodes(DiscoveredNode[],int);
};
@METHODS@
int main(){
  MyMesh mesh;
  uint8_t response[14]={0x92,20};memcpy(response+2,&mesh.disc_node_req_tag,4);
  for(size_t len=0;len<14;len++){
    uint8_t* short_frame=new uint8_t[len];memcpy(short_frame,response,len);
    mesh.checkControlDataForPendingDiscovery(short_frame,len);delete[] short_frame;
    assert(mesh.disc_nodes_count==0);
  }
  response[0]=0x82;mesh.checkControlDataForPendingDiscovery(response,14);
  assert(mesh.disc_nodes_count==0);response[0]=0x92;
  response[2]^=1;mesh.checkControlDataForPendingDiscovery(response,14);
  assert(mesh.disc_nodes_count==0);response[2]^=1;
  mesh.disc_node_req_active=false;mesh.checkControlDataForPendingDiscovery(response,14);
  assert(mesh.disc_nodes_count==0);mesh.disc_node_req_active=true;
  now=30100;mesh.checkControlDataForPendingDiscovery(response,14);assert(mesh.disc_nodes_count==0);
  mesh.disc_node_req_at=0xfffffff0;now=20;
  mesh.checkControlDataForPendingDiscovery(response,14);assert(mesh.disc_nodes_count==1);
  assert(mesh.discovered_nodes[0].name[31]==0&&mesh.discovered_nodes[0].snr_out==5.0f);
  // Existing clients must accept both key sizes with no trailer, the two-byte
  // off marker, or the full twelve-byte radio2 profile, retaining the prefix.
  for(size_t length : {14U,16U,26U,38U,40U,50U}){
    MyMesh legacy;legacy.disc_node_req_at=now;
    uint8_t extended[50];memset(extended,0xAB,sizeof(extended));
    memcpy(extended,response,14);
    size_t offset=length<=26?14:38;
    if(length>offset){extended[offset]=1;extended[offset+1]=length-offset==2?0:5;}
    legacy.checkControlDataForPendingDiscovery(extended,length);
    assert(legacy.disc_nodes_count==1);
    assert(memcmp(legacy.discovered_nodes[0].pubkey_prefix,response+6,8)==0);
    assert(legacy.discovered_nodes[0].type==2&&legacy.discovered_nodes[0].snr_out==5.0f);
  }
  for(int i=0;i<10;i++)mesh.checkControlDataForPendingDiscovery(response,14);
  assert(mesh.disc_nodes_count==4);
  DiscoveredNode result[4];assert(mesh.getDiscoveredNodes(result,100)==4);
  assert(result[0].type==2&&result[0].snr_in==5.0f&&result[0].snr_out==5.0f);
  assert(!memcmp(result[0].pubkey_prefix,response+6,8));
  assert(!strcmp(result[0].name,mesh.discovered_nodes[0].name));
  assert(mesh.getDiscoveredNodes(nullptr,4)==0&&mesh.getDiscoveredNodes(result,-1)==0);
}
'''.replace('@DISCOVERED_NODE@', row).replace('@METHODS@', parser + '\n' + query)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(code)
            subprocess.run(['g++', '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-pie', '-no-pie', str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
