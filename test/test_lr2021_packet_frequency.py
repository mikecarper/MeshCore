"""Exercise packet-bound LR2021 observations independently of PlatformIO."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import test_lr2021_calibration as calibration
from test_lr2021_calibration import DRIVER, RADIO, ROOT, method
from test_radio_direction_dispatcher import SOURCE as DISPATCHER_SOURCE


class LR2021PacketFrequencyTests(unittest.TestCase):
    compile_run = calibration.LR2021CalibrationTests.compile_run
    def test_dispatcher_queues_independent_samples_and_clears_reused_packets(self):
        source = DISPATCHER_SOURCE.replace('#include <cassert>', '#include <cassert>\n#include <cstring>')
        source = source.replace('unsigned rx=0,tx=0,calibrations=0,recoveries=0,loops=0;', '''
          unsigned rx=0,tx=0,calibrations=0,recoveries=0,loops=0;
          int remaining=0; mesh::lr2021::PacketFrequencyObservation latest;
        ''')
        source = source.replace('int recvRaw(uint8_t*,int) override { ++rx; return 0; }', '''
          int recvRaw(uint8_t* bytes,int) override {
            ++rx; if (!remaining) return 0;
            bytes[0]=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_TXT_MSG<<PH_TYPE_SHIFT);
            bytes[1]=0; bytes[2]=static_cast<uint8_t>(remaining);
            latest={remaining*100,910525024,100,remaining!=1}; --remaining; return 3;
          }
          bool lastPacketFrequency(mesh::lr2021::PacketFrequencyObservation& out) const override {
            out=latest; return out.valid;
          }
        ''')
        source = source.replace('unsigned inbound=0;', '''
          unsigned inbound=0; std::vector<mesh::lr2021::PacketFrequencyObservation> observations;
          int calcRxDelayForPacket(const mesh::Packet*,float,uint32_t) override {return 2000;}
        ''')
        source = source.replace('++inbound; return ACTION_RETRANSMIT_DELAYED(0,0);',
                                '++inbound; observations.push_back(packet->lr2021_frequency); return ACTION_RELEASE;')
        source = source.replace('onRecvPacket(mesh::Packet*)', 'onRecvPacket(mesh::Packet* packet)')
        source = source[:source.index('int main() {')] + '''
          int main() {
            Radio r; Clock c; StaticPoolPacketManager pool(4); Dispatcher d(r,c,pool);
            d.begin(); r.remaining=3;
            for (int i=0;i<3;++i) {c.now+=10; d.loop();}
            assert(d.inbound==0 && r.remaining==0);
            c.now+=2500;
            for (int i=0;i<10;++i) { c.now+=10; d.loop(); }
            assert(d.inbound==3 && d.observations.size()==3);
            assert(d.observations[0].valid && d.observations[0].offset_hz==300);
            assert(d.observations[1].valid && d.observations[1].offset_hz==200);
            assert(!d.observations[2].valid);
            mesh::Packet* p=pool.allocNew(); assert(p); p->lr2021_frequency.valid=true;
            pool.free(p); mesh::Packet* reused=nullptr;
            for(int i=0;i<4;++i) {auto* q=pool.allocNew(); assert(q); if(q==p) reused=q;}
            assert(reused && !reused->lr2021_frequency.valid);
            reused->lr2021_frequency.valid=true;
            assert(!reused->readFrom(nullptr,0) && !reused->lr2021_frequency.valid);
            reused->header=ROUTE_TYPE_FLOOD; reused->path_len=0;
            reused->payload_len=1; reused->payload[0]=42;
            uint8_t before[255], after[255];
            const auto length=reused->writeTo(before);
            reused->lr2021_frequency={123,910525024,500,true};
            assert(reused->writeTo(after)==length && !memcmp(before,after,length));
            assert(reused->readFrom(before,length) && !reused->lr2021_frequency.valid);
          }
        '''
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'main.cpp').write_text(source)
            (work / 'Mesh.h').write_text('#pragma once\n#include <Dispatcher.h>\n')
            binary = work / 'test'
            command = [os.environ.get('CXX', 'g++'), '-std=c++17', '-O1',
                       '-DMESH_ENABLE_LR2021_AUTO_XTRIM=1', '-DMESH_ENABLE_RADIO_DIRECTION=1',
                       '-DMESH_ENABLE_BRIDGE_INGRESS=1', '-I', str(work), '-I', str(ROOT/'test/mocks'),
                       '-I', str(ROOT/'src'), str(work/'main.cpp'), str(ROOT/'src/Dispatcher.cpp'),
                       str(ROOT/'src/Packet.cpp'), str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'),
                       '-o', str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_capture_survives_queue_delay_and_failures(self):
        source = (RADIO / "CustomLR2021.h").read_text()
        signatures = [
            "bool supportsXoscTrim() const", "bool configureBootXoscTrim(bool enabled, uint8_t a, uint8_t b)",
            "bool getXoscTrim(uint8_t& a, uint8_t& b) const", "void clearXoscTrim()",
            "int16_t applyXoscTrim(uint8_t a, uint8_t b)", "int16_t restoreXoscTrim()",
            "int16_t readPacketFrequencyErrorHz(int32_t& value)",
            "void formatFrequencyError(char* reply, size_t capacity) const",
            "bool lastPacketFrequency(mesh::lr2021::PacketFrequencyObservation& out) const",
            "int16_t readData(uint8_t* data, size_t len) override",
        ]
        methods = "\n".join(method(source, s).replace(" override", "") for s in signatures)
        driver = DRIVER.replace("#include <helpers/radiolib/LR2021CalibrationPolicy.h>",
            "#include <helpers/radiolib/LR2021CalibrationPolicy.h>\n"
            "#include <helpers/radiolib/LR2021PacketFrequency.h>")
        driver = driver.replace("bool _last_frequency_error_valid=false;",
                                "bool _last_frequency_error_valid=false; bool _packet_frequency_valid=false; "
                                "float _nominal_frequency_mhz=910.525f, _last_packet_nominal_frequency_mhz=0;")
        driver = driver.replace("uint8_t packet[4];radio.sequence.clear();", """
          uint8_t packet[4]; radio.sequence.clear();
          mesh::lr2021::PacketFrequencyObservation queued, current;
          assert(!radio.lastPacketFrequency(current) && !current.valid);
        """)
        driver = driver.replace("radio.fifo=4;\n  assert(radio.readData(packet,4)==0);",
                                "radio.fifo=4; radio._setNominalForTest(915);\n  assert(radio.readData(packet,4)==0);")
        driver = driver.replace("public:\n@METHODS@", "public:\n void _setNominalForTest(float f){_nominal_frequency_mhz=f;}\n@METHODS@")
        driver = driver.replace("assert(radio.sequence.size()==2 && radio.sequence[0]==3 && radio.sequence[1]==4);", """
          assert(radio.sequence.size()==2 && radio.sequence[0]==3 && radio.sequence[1]==4);
          assert(radio.lastPacketFrequency(queued) && queued.valid && queued.offset_hz==-1000);
          assert(queued.frequency_hz==static_cast<uint32_t>(910.525f*1000000.0f));
          assert(queued.captured_at_ms==1000);
        """)
        driver = driver.replace("assert(radio.readData(packet,4)==-7);", """
          assert(radio.readData(packet,4)==-7);
          assert(!radio.lastPacketFrequency(current) && !current.valid);
          assert(queued.valid && queued.offset_hz==-1000 && queued.captured_at_ms==1000);
        """)
        driver = driver.replace("radio.command_result=0;radio.modem=2;", """
          assert(!radio.lastPacketFrequency(current) && !current.valid);
          assert(queued.valid && queued.offset_hz==-1000);
          radio.command_result=0;radio.modem=2;
        """)
        driver = driver.replace('radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"+0 Hz")', """
          assert(radio.lastPacketFrequency(current) && current.valid && current.offset_hz==0);
          assert(current.captured_at_ms==3000 && queued.captured_at_ms==1000 && queued.offset_hz==-1000);
          radio.formatFrequencyError(result,sizeof(result));assert(strstr(result,"+0 Hz")""")
        self.compile_run(driver.replace("@METHODS@", methods),
                         ("-DMESH_ENABLE_LR2021_AUTO_XTRIM=1",))


if __name__ == "__main__":
    unittest.main()
