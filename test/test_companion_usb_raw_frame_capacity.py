#!/usr/bin/env python3
"""Exercise the production USB parser with full-size OTA/raw command frames."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <cassert>
#include <deque>
#include <vector>
#include <cstdio>
struct Port : Stream {
  std::deque<uint8_t> bytes;
  int available() override { return bytes.size(); }
  int read() override { if(bytes.empty())return -1;int b=bytes.front();bytes.pop_front();return b; }
  int peek() override { return bytes.empty()?-1:bytes.front(); }
  size_t write(const uint8_t*,size_t n) override { return n; }
  int availableForWrite() override { return 4096; }
  void push(const std::vector<uint8_t>& b,size_t start,size_t end){for(size_t i=start;i<end;i++)bytes.push_back(b[i]);}
};
std::vector<uint8_t> wire(size_t length,uint8_t command=65){
  std::vector<uint8_t> b(length+3,0x5a);b[0]='<';b[1]=length&255;b[2]=length>>8;
  if(length)b[3]=command;
  if(length>1)b[4]=1;
  return b;
}
void initializeUsb(ArduinoSerialInterface& usb_serial_interface) {
  @USB_OPT_IN@
}
bool capacityQuery(BaseSerialInterface* _serial, const char* command,
                   uint32_t sender_timestamp, char* reply) {
  const size_t reply_capacity=160;
  @CAPACITY_QUERY@
  return false;
}
int main(){
  static_assert(MAX_FRAME_SIZE==176,"normal frame/offline queues must remain unchanged");
  static_assert(BaseSerialInterface::MAX_USB_RAW_FRAME_SIZE==188,"raw USB maximum");
  for(size_t length:{size_t(177),size_t(187),size_t(188)}){
    auto b=wire(length);
    for(size_t split=0;split<=b.size();split++){
      Port port;ArduinoSerialInterface usb;usb.begin(port);usb.enable();usb.setUsbRawPacketReceive(true);
      uint8_t out[190];memset(out,0xa5,sizeof(out));
      port.push(b,0,split);size_t n=usb.checkRecvFrame(out+1,188);
      if(split<b.size())assert(n==0);
      port.push(b,split,b.size());if(!n)n=usb.checkRecvFrame(out+1,188);
      assert(n==length && !memcmp(out+1,b.data()+3,length));
      assert(out[0]==0xa5 && out[189]==0xa5);
      assert(usb.getCompletedFrameCount()==1);
    }
  }
  for(bool opted:{false,true}) for(size_t capacity:{size_t(176),size_t(188)})
    for(size_t length:{size_t(177),size_t(187),size_t(188),size_t(189),size_t(300)})
      for(uint8_t command:{uint8_t(2),uint8_t(66),uint8_t(65)}){
        Port port;ArduinoSerialInterface usb;usb.begin(port);usb.enable();usb.setUsbRawPacketReceive(opted);
        auto b=wire(length,command);port.push(b,0,b.size());
        uint8_t out[190];memset(out,0xa5,sizeof(out));
        size_t n=usb.checkRecvFrame(out+1,capacity);
        const bool accepted=opted&&capacity>=length&&length<=188&&command==65;
        assert(n==(accepted?length:0));assert(usb.hasReceivedFrame()==accepted);
        assert(out[0]==0xa5&&out[189]==0xa5);
        auto next=wire(176,66);port.push(next,0,next.size());
        assert(usb.checkRecvFrame(out+1,capacity)==176);
        assert(!memcmp(out+1,next.data()+3,176));
      }
  // The old pointer-only API cannot infer a larger destination allocation.
  {Port p;ArduinoSerialInterface usb;usb.begin(p);usb.enable();usb.setUsbRawPacketReceive(true);
   auto b=wire(188);p.push(b,0,b.size());uint8_t out[176];assert(usb.checkRecvFrame(out)==0);}
  // Aggregated reads preserve the larger capacity and the physical USB route.
  {Port p;ArduinoSerialInterface usb;usb.begin(p);usb.setUsbRawPacketReceive(true);
   MultiSerialInterface manager;assert(manager.addInterface(InterfaceType::USB,&usb));manager.enable();
   uint8_t out[188];auto b=wire(188);p.push(b,0,b.size());
   assert(manager.checkRecvFrame(out,sizeof(out))==188);assert(manager.captureReplyRoute()==&usb);
   usb.setUsbRawPacketReceive(false);p.push(b,0,b.size());assert(manager.checkRecvFrame(out,sizeof(out))==0);}
  // RX extension does not grow ordinary TX response frames.
  {Port p;ArduinoSerialInterface usb;usb.begin(p);usb.enable();usb.setUsbRawPacketReceive(true);
   uint8_t out[188]={};assert(usb.writeFrame(out,176)==176);assert(usb.writeFrame(out,177)==0);}
  // The exact production USB initialization and common CLI query work with
  // optional guarded-OTA features both enabled and compiled out.
  {Port p;ArduinoSerialInterface usb,auxiliary;usb.begin(p);initializeUsb(usb);
   assert(usb.usbRawPacketReceiveCapacity()==188);
   assert(auxiliary.usbRawPacketReceiveCapacity()==176);
   MultiSerialInterface manager;assert(manager.addInterface(InterfaceType::USB,&usb));manager.enable();
   auto b=wire(188);p.push(b,0,b.size());uint8_t out[188];assert(manager.checkRecvFrame(out,sizeof(out))==188);
   char reply[160]={};
   assert(capacityQuery(&manager,"get usb.raw.max",0,reply)&&!strcmp(reply,"> 188"));
   assert(capacityQuery(&auxiliary,"get usb.raw.max",0,reply)&&!strcmp(reply,"> 176"));
   assert(capacityQuery(nullptr,"get usb.raw.max",0,reply)&&!strcmp(reply,"> 176"));
   assert(capacityQuery(&manager,"get usb.raw.max",1,reply)&&strstr(reply,"ERR"));
   assert(!capacityQuery(&manager,"other",0,reply));}
  std::printf("PASS: USB raw188 framing, all splits, limits, routes; serial_size=%zu\n",sizeof(ArduinoSerialInterface));
}
'''

class CompanionUsbRawFrameCapacityTests(unittest.TestCase):
    def test_production_usb_raw_receive_capacity(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('a host C++17 compiler is required')
        with tempfile.TemporaryDirectory(prefix='meshcore-usb-raw188-') as directory:
            work = Path(directory)
            source = work / 'test.cpp'
            main = (ROOT/'examples/companion_radio/main.cpp').read_text()
            mesh = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
            opt_in = 'usb_serial_interface.setUsbRawPacketReceive(true);'
            self.assertEqual(main.count(opt_in),1)
            start = main.index('usb_serial_interface.begin(mesh::usbCompanionPort()')
            end = main.index('usb_serial_interface.enableFlowControl(true);',start)
            self.assertIn(opt_in,main[start:end])
            self.assertNotIn('COMPANION_FEATURE_DIRECT_OTA_ADMIN',main[start:end])
            common = extract_braced(mesh,'bool MyMesh::handleCommand(')
            query = extract_braced(common,'if (!strcmp(command, "get usb.raw.max"))')
            self.assertLess(common.index('get usb.raw.max'),common.index('#if COMPANION_FEATURE_DIRECT_OTA_ADMIN'))
            source.write_text(HARNESS.replace('@USB_OPT_IN@',opt_in).replace('@CAPACITY_QUERY@',query))
            binary = work / 'usb-raw188'
            for full in (0,1):
                with self.subTest(guarded_features=full):
                    built = subprocess.run([compiler,'-std=c++17','-fsanitize=address,undefined',
                        '-fno-sanitize-recover=all','-fno-pie','-no-pie',
                        '-DCOMPANION_FEATURE_DIRECT_OTA_ADMIN='+str(full),
                        '-I'+str(ROOT/'test/mocks'),'-I'+str(ROOT/'src'),str(source),
                        str(ROOT/'src/helpers/ArduinoSerialInterface.cpp'),'-o',str(binary)],
                        capture_output=True,text=True,timeout=60)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    checked = subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
                    self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
                    self.assertIn('PASS: USB raw188 framing',checked.stdout)

if __name__=='__main__':
    unittest.main()
