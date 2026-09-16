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
        parser = method(source, 'void MyMesh::checkControlDataForPendingDiscovery(')
        query = method(source, 'int MyMesh::getDiscoveredNodes(')
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
#define DISCOVERED_NODES_TABLE_SIZE 4
#define CTL_TYPE_NODE_DISCOVER_RESP 0x90
static uint32_t now=100;
uint32_t millis(){return now;}
struct StrHelper{static void strncpy(char* o,const char* i,size_t n){::strncpy(o,i,n-1);o[n-1]=0;}};
struct DiscoveredNode{uint8_t pubkey_prefix[9];float snr_in,snr_out;char name[32];uint8_t type;};
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
  for(int i=0;i<10;i++)mesh.checkControlDataForPendingDiscovery(response,14);
  assert(mesh.disc_nodes_count==4);
  DiscoveredNode result[4];assert(mesh.getDiscoveredNodes(result,100)==4);
  assert(mesh.getDiscoveredNodes(nullptr,4)==0&&mesh.getDiscoveredNodes(result,-1)==0);
}
'''.replace('@METHODS@', parser + '\n' + query)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(code)
            subprocess.run(['g++', '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-pie', '-no-pie', str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
