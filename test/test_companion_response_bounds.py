"""Check production Companion response envelope limits without radio hardware."""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <helpers/CompanionStatusResponse.h>
#define COMPANION_FEATURE_TEXT_TERMINAL 0
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RESP_SERVER_LOGIN_OK 0
@CODES@
@FRAME_LIMIT@
struct ContactInfo { struct { uint8_t pub_key[32] = {1}; } id; };
namespace mesh {
struct Packet {
  uint8_t payload[256] = {}, payload_len = 0, path_len = 0;
  float getSNR() const { return 0; }
  int getRSSI() const { return 0; }
};
}
struct Serial {
  std::vector<std::vector<uint8_t>> frames;
  bool isConnected() const { return true; }
  void writeFrame(const uint8_t* data, size_t n) {
    assert(n <= MAX_FRAME_SIZE);
    frames.emplace_back(data, data+n);
  }
  void writeFrameToRoute(Serial*, const uint8_t* data, size_t n) { writeFrame(data,n); }
};
struct MyMesh {
  uint32_t pending_login=0, pending_status=0, pending_telemetry=0, pending_req=0;
  uint8_t before[16], out_frame[MAX_FRAME_SIZE+1], after[16];
  Serial serial;
  Serial* _serial = &serial;
  unsigned clears = 0;
  bool binary_trace_pending = true;
  uint32_t binary_trace_tag = 17, binary_trace_auth = 23;
  Serial* binary_trace_reply_route = &serial;
  MyMesh() {
    memset(before, 0xA5, sizeof(before)); memset(after, 0xA5, sizeof(after));
    memset(out_frame, 0x5A, sizeof(out_frame));
  }
  void clearPendingReqs() { ++clears;pending_login=pending_status=pending_telemetry=pending_req=0; }
  void startConnection(const ContactInfo&, uint16_t) {}
  void writePendingSerialFrame(const uint8_t* p, size_t n) { serial.writeFrame(p,n); }
  void onContactResponse(const ContactInfo&, const uint8_t*, uint8_t);
  void onControlDataRecv(mesh::Packet*);
  void onRawDataRecv(mesh::Packet*);
  void onTraceRecv(mesh::Packet*,uint32_t,uint32_t,uint8_t,const uint8_t*,const uint8_t*,uint8_t);
  void clearBinaryTraceReply() { binary_trace_pending = false; }
  void checkGuards() const {
    for (uint8_t value:before) assert(value==0xA5);
    for (uint8_t value:after) assert(value==0xA5);
    assert(out_frame[MAX_FRAME_SIZE]==0x5A);
  }
};
@METHODS@
int main() {
  ContactInfo contact;
  for (unsigned kind=0; kind<3; ++kind) for (unsigned n=0; n<256; ++n) {
    MyMesh value;
    uint32_t tag=17;
    uint8_t data[256]={};memcpy(data,&tag,4);
    if (kind==0) value.pending_status=tag;
    if (kind==1) value.pending_telemetry=tag;
    if (kind==2) value.pending_req=tag;
    value.onContactResponse(contact,data,n);
    const unsigned minimum=kind==0 ? mesh::COMPANION_MIN_STATUS_RESPONSE_SIZE : 5;
    const unsigned overhead=kind==2 ? 2 : 4;
    const bool valid=n>=minimum && n+overhead<=MAX_FRAME_SIZE;
    assert(value.serial.frames.size()==(valid ? 1U : 0U));
    if (valid) assert(value.serial.frames[0].size()==n+overhead);
    value.checkGuards();
  }
  for (unsigned n=0;n<256;++n) {
    MyMesh idle;uint8_t data[256]={};
    idle.onContactResponse(contact,data,n);
    assert(idle.serial.frames.empty() && idle.clears==0);idle.checkGuards();
    for (bool control:{false,true}) {
      MyMesh value;mesh::Packet packet;packet.payload_len=n;
      if (control) value.onControlDataRecv(&packet);else value.onRawDataRecv(&packet);
      assert(value.serial.frames.size()==(n+4<=MAX_FRAME_SIZE ? 1U : 0U));
      value.checkGuards();
    }
  }
  for (bool modern:{false,true}) {
    MyMesh value;memcpy(&value.pending_login,contact.id.pub_key,4);
    uint8_t data[13]={};if (!modern) memcpy(data+4,"OK",2);
    value.onContactResponse(contact,data,modern ? 13 : 6);
    assert(value.serial.frames.size()==1 && value.clears==1);value.checkGuards();
  }
  for (unsigned n=0;n<256;++n) for (uint8_t flags=0;flags<4;++flags) {
    MyMesh value;mesh::Packet packet;uint8_t data[256]={};
    value.onTraceRecv(&packet,17,23,flags,data,data,n);
    const unsigned framed=13+n+(n>>flags);
    assert(value.serial.frames.size()==(framed<=MAX_FRAME_SIZE ? 1U : 0U));
    if (!value.serial.frames.empty()) assert(value.serial.frames[0].size()==framed);
    assert(!value.binary_trace_pending);value.checkGuards();
  }
  MyMesh empty;empty.onContactResponse(contact,nullptr,255);empty.checkGuards();
}
'''


class CompanionResponseBoundsTest(unittest.TestCase):
    def test_response_envelopes(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text(encoding='utf-8')
        codes = '\n'.join(re.findall(r'^#define PUSH_CODE_\w+\s+0x[0-9A-Fa-f]+', source, re.M))
        interface = (ROOT / 'src/helpers/BaseSerialInterface.h').read_text(encoding='utf-8')
        frame_limit = re.search(r'^#define MAX_FRAME_SIZE\s+\d+', interface, re.M).group(0)
        methods = '\n'.join(extract_braced(source, signature) for signature in (
            'void MyMesh::onContactResponse(', 'void MyMesh::onControlDataRecv(',
            'void MyMesh::onRawDataRecv(', 'void MyMesh::onTraceRecv(',
        ))
        with tempfile.TemporaryDirectory(prefix='companion-response-') as directory:
            work = Path(directory)
            cpp = work / 'test.cpp'
            cpp.write_text(HARNESS.replace('@CODES@',codes).replace('@METHODS@',methods)
                           .replace('@FRAME_LIMIT@',frame_limit), encoding='utf-8')
            flags = ['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie'] if sys.platform.startswith('linux') else []
            binary = work / 'test.exe'
            compiled = subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                *flags,f'-I{ROOT / "src"}',str(cpp),'-o',str(binary)], capture_output=True,text=True,timeout=60)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            checked = subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(checked.returncode,0,checked.stderr)


if __name__=='__main__':
    unittest.main()
