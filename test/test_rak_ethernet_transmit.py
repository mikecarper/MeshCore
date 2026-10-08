"""Run the bounded TCP sender against a register-level W5100S simulator."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = (ROOT / ".pio/libdeps/RAK_4631_repeater_unified_lora_ota"
          / "RAK13800-W5100S/src/w5100.h")

ARDUINO = r'''
#pragma once
#include <cstdint>
constexpr unsigned OUTPUT = 1, LOW = 0, HIGH = 1;
inline void pinMode(unsigned, unsigned) {}
inline void digitalWrite(unsigned, unsigned) {}
'''
SPI = r'''
#pragma once
#include <cassert>
constexpr unsigned MSBFIRST = 1, SPI_MODE0 = 0;
struct SPISettings { SPISettings(unsigned, unsigned, unsigned) {} };
struct SPIClass {
  bool active = false;
  unsigned begins = 0, ends = 0;
  void beginTransaction(SPISettings) { assert(!active); active = true; ++begins; }
  void endTransaction() { assert(active); active = false; ++ends; }
};
'''
RAK_HEADER = r'''
#pragma once
#define _RAK13800_W5100S_H_
#define MAX_SOCK_NUM 8
#include <Arduino.h>
#include <SPI.h>
'''
# Keep the production register interface available in native CI without
# downloading board libraries. A second test uses the actual installed vendor
# header when available, and the firmware build always checks that interface.
PORTABLE_DRIVER = r'''
#pragma once
#include <Arduino.h>
#include <SPI.h>
#define SPI_ETHERNET_SETTINGS SPISettings(14000000, MSBFIRST, SPI_MODE0)
struct SnSR { static constexpr uint8_t CLOSED=0, ESTABLISHED=0x17, CLOSE_WAIT=0x1c; };
struct SnIR { static constexpr uint8_t SEND_OK=0x10, TIMEOUT=0x08; };
enum SockCMD { Sock_CLOSE=0x10, Sock_SEND=0x20, Sock_RECV=0x40 };
class W5100Class {
  static uint8_t chip, CH_BASE_MSB, ss_pin;
  static SPIClass* spi;
public:
  static constexpr uint16_t SSIZE=2048, SMASK=2047;
  static uint8_t init();
  static uint8_t getChip() { return chip; }
  static SPIClass* getSPI() { return spi; }
  static void setSPI(SPIClass& value) { spi=&value; }
  static uint16_t SBASE(uint8_t socket) { return 0x4000 + socket*SSIZE; }
  static uint16_t RBASE(uint8_t socket) { return 0x6000 + socket*SSIZE; }
  static uint16_t read(uint16_t, uint8_t*, uint16_t);
  static uint16_t write(uint16_t, const uint8_t*, uint16_t);
  static uint8_t read(uint16_t address) { uint8_t value; read(address,&value,1); return value; }
  static uint8_t write(uint16_t address,uint8_t value) { return write(address,&value,1); }
  static uint16_t reg(uint8_t socket,uint16_t offset) { return (CH_BASE_MSB<<8)+socket*256+offset; }
  static uint8_t readSnSR(uint8_t socket) { return read(reg(socket,3)); }
  static uint8_t readSnIR(uint8_t socket) { return read(reg(socket,2)); }
  static uint8_t readSnCR(uint8_t socket) { return read(reg(socket,1)); }
  static void writeSnIR(uint8_t socket,uint8_t value) { write(reg(socket,2),value); }
  static void writeSnCR(uint8_t socket,uint8_t value) { write(reg(socket,1),value); }
  static uint16_t readSnTX_WR(uint8_t socket) {
    uint8_t bytes[2]; read(reg(socket,0x24),bytes,2); return (bytes[0]<<8)|bytes[1];
  }
  static uint16_t readSnTX_FSR(uint8_t socket) {
    uint8_t bytes[2]; read(reg(socket,0x20),bytes,2); return (bytes[0]<<8)|bytes[1];
  }
  static uint16_t readSnRX_RSR(uint8_t socket) {
    uint8_t bytes[2]; read(reg(socket,0x26),bytes,2); return (bytes[0]<<8)|bytes[1];
  }
  static uint16_t readSnRX_RD(uint8_t socket) {
    uint8_t bytes[2]; read(reg(socket,0x28),bytes,2); return (bytes[0]<<8)|bytes[1];
  }
  static void writeSnRX_RD(uint8_t socket,uint16_t value) {
    uint8_t bytes[2]={uint8_t(value>>8),uint8_t(value)}; write(reg(socket,0x28),bytes,2);
  }
  static void writeSnTX_WR(uint8_t socket,uint16_t value) {
    uint8_t bytes[2]={uint8_t(value>>8),uint8_t(value)}; write(reg(socket,0x24),bytes,2);
  }
  static void execCmdSn(uint8_t, SockCMD); // deliberately undefined: a blocking call fails linking
};
extern W5100Class W5100;
'''

RUNNER = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "src/helpers/nrf52/EthernetCliTransmit.h"
using mesh::nrf52::EthernetCliTransmit;
static_assert(Sock_SEND == 0x20 && Sock_CLOSE == 0x10 && Sock_RECV == 0x40);
static_assert(SnIR::SEND_OK == 0x10 && SnIR::TIMEOUT == 0x08);
static_assert(SnSR::ESTABLISHED == 0x17 && SnSR::CLOSE_WAIT == 0x1c);
static SPIClass spi;
uint8_t W5100Class::chip = 51, W5100Class::CH_BASE_MSB = 4, W5100Class::ss_pin = 26;
SPIClass* W5100Class::spi = &::spi;
W5100Class W5100;
static uint8_t memory[65536] = {};
static uint8_t next_chip = 51;
static unsigned reads = 0, writes = 0, free_reads = 0, data_writes = 0, commands = 0;
static bool short_write = false;
static std::vector<uint16_t> changing_free;
static std::vector<uint16_t> changing_receive;
uint8_t W5100Class::init() { chip = next_chip; spi = &::spi; return chip; }
uint16_t W5100Class::read(uint16_t address, uint8_t* buffer, uint16_t length) {
  assert(::spi.active); ++reads;
  if (address >= 0x400 && address < 0x800 && (address&255) == 0x20 && length == 2) {
    ++free_reads;
    if (!changing_free.empty()) {
      const uint16_t value = changing_free.front(); changing_free.erase(changing_free.begin());
      buffer[0] = value>>8; buffer[1] = value; return length;
    }
  }
  if (address >= 0x400 && address < 0x800 && (address&255) == 0x26 && length == 2 &&
      !changing_receive.empty()) {
    const uint16_t value=changing_receive.front(); changing_receive.erase(changing_receive.begin());
    buffer[0]=value>>8; buffer[1]=value; return length;
  }
  memcpy(buffer,memory+address,length);
  return length;
}
uint16_t W5100Class::write(uint16_t address,const uint8_t* buffer,uint16_t length) {
  assert(::spi.active); ++writes;
  if (address >= 0x4000) {
    ++data_writes;
    if (short_write) return 0;
  }
  if (address >= 0x400 && address < 0x800 && length == 1 && (address&255) == 2) {
    memory[address] &= ~buffer[0];
  } else {
    if (address >= 0x400 && address < 0x800 && length == 1 && (address&255) == 1) ++commands;
    memcpy(memory+address,buffer,length);
  }
  return length;
}
static uint16_t reg(unsigned offset) { return 0x400+offset; }
static void put16(unsigned offset,uint16_t value) {
  memory[reg(offset)] = value>>8; memory[reg(offset)+1] = value;
}
static uint16_t get16(unsigned offset) { return (memory[reg(offset)]<<8)|memory[reg(offset)+1]; }
static void reset() {
  memset(memory,0,sizeof(memory));
  memory[reg(3)] = SnSR::ESTABLISHED;
  put16(0x20,2048);
  reads = writes = free_reads = data_writes = commands = 0;
  changing_free.clear(); changing_receive.clear(); short_write = false; next_chip = 51;
  W5100.init();
  assert(!::spi.active && ::spi.begins == ::spi.ends);
}
int main() {
  uint8_t payload[512];
  for (unsigned i=0;i<sizeof(payload);++i) payload[i]=i;
  reset();
  EthernetCliTransmit sender;
  memory[reg(2)] = SnIR::SEND_OK; // stale ACK must be revoked
  assert(sender.write(0,payload,4,10) == 4 && sender.pending() && !sender.failed());
  assert(memcmp(memory+0x4000,payload,4)==0 && get16(0x24)==4);
  assert(memory[reg(2)]==0 && memory[reg(1)]==Sock_SEND && commands==1);
  assert(sender.write(0,payload,4,11)==0 && commands==1);
  const unsigned before=reads;
  sender.poll(12); // command/ACK remain stuck: one finite poll
  assert(sender.pending() && reads-before<=3);
  memory[reg(1)]=0; memory[reg(2)]=SnIR::SEND_OK;
  sender.poll(20);
  assert(!sender.pending() && !sender.failed() && memory[reg(2)]==0);
  assert(sender.write(0,payload,4,21)==4 && get16(0x24)==8);
  reset(); sender.reset();
  put16(0x24,0xfffe); // circular SRAM and 16-bit counter both wrap
  assert(sender.write(0,payload,4,1)==4);
  assert(memory[0x47fe]==0 && memory[0x47ff]==1 && memory[0x4000]==2 && memory[0x4001]==3);
  assert(get16(0x24)==2 && data_writes==2);
  reset(); sender.reset(); put16(0x20,32);
  assert(sender.write(0,payload,sizeof(payload),1)==32 && get16(0x24)==32);
  reset(); sender.reset(); changing_free={100,101,100,101};
  assert(sender.write(0,payload,4,1)==0 && free_reads==4 && !sender.failed());
  assert(!sender.pending() && !data_writes && !commands);
  assert(sender.write(0,payload,4,2)==4); // instability yields and retries
  reset(); sender.reset(); put16(0x20,0);
  assert(sender.write(0,payload,4,1)==0 && !sender.failed());
  assert(sender.write(0,payload,4,10000)==0 && !sender.failed()); // backpressure is not an ACK timeout
  reset(); sender.reset(); short_write=true;
  assert(sender.write(0,payload,4,1)==0 && sender.failed());
  assert(get16(0x24)==0 && commands==0);
  reset(); sender.reset();
  assert(sender.write(0,payload,4,0xfffffff0u)==4);
  memory[reg(1)]=0; // ACK never arrives; register polling stays finite
  for (unsigned elapsed=1;elapsed<250;++elapsed) {
    const unsigned first=reads;
    sender.poll(0xfffffff0u+elapsed);
    assert(sender.pending() && !sender.failed() && reads-first<=3);
  }
  sender.poll(0xfffffff0u+250);
  assert(sender.failed() && !sender.pending());
  assert(EthernetCliTransmit::close(0));
  assert(memory[reg(1)]==Sock_CLOSE);
  reset(); sender.reset(); memory[reg(1)]=Sock_SEND;
  assert(sender.write(0,payload,4,10)==0 && sender.pending() && !sender.failed());
  sender.poll(259); assert(!sender.failed());
  sender.poll(260); assert(sender.failed());
  const unsigned close_reads=reads;
  assert(!EthernetCliTransmit::close(0) && reads-close_reads==1); // stuck CR never spins
  reset(); sender.reset(); assert(sender.write(0,payload,4,1)==4);
  memory[reg(1)]=0; memory[reg(2)]=SnIR::SEND_OK|SnIR::TIMEOUT;
  sender.poll(2); assert(sender.failed() && memory[reg(2)]==0);
  reset(); sender.reset(); assert(sender.write(0,payload,4,1)==4);
  memory[reg(3)]=SnSR::CLOSED;
  sender.poll(2); assert(sender.failed());
  reset(); sender.reset(); put16(0x20,0xffff);
  assert(sender.write(0,payload,4,1)==0 && sender.failed() && commands==0);
  reset(); sender.reset();
  assert(sender.write(4,payload,4,1)==0 && sender.failed() && reads==0);
  assert(!EthernetCliTransmit::close(4));
  reset(); sender.reset(); next_chip=55; W5100.init();
  assert(sender.write(0,payload,4,1)==0 && sender.failed() && reads==0);
  reset(); sender.reset(); assert(sender.write(0,payload,4,1)==4);
  const unsigned no_io_reads=reads, no_io_writes=writes;
  sender.reset(); assert(!sender.pending() && !sender.failed());
  assert(reads==no_io_reads && writes==no_io_writes);
  reset(); sender.reset();
  assert(sender.write(0,nullptr,4,1)==0 && sender.write(0,payload,0,1)==0 && !sender.failed());
  reset();
  assert(EthernetCliTransmit::commandReady(0) && EthernetCliTransmit::connected(0));
  put16(0x26,2); put16(0x28,0xffff);
  memory[0x67ff]='X'; memory[0x6000]='Y';
  assert(EthernetCliTransmit::available(0)==2);
  assert(EthernetCliTransmit::peek(0)=='X' && get16(0x28)==0xffff && commands==0);
  assert(EthernetCliTransmit::read(0)=='X');
  assert(get16(0x28)==0 && memory[reg(1)]==Sock_RECV && commands==1);
  assert(!EthernetCliTransmit::commandReady(0)); // newly stuck RECV never spins
  const unsigned first_rx_read=reads;
  assert(EthernetCliTransmit::available(0)==0 && EthernetCliTransmit::read(0)==-1);
  assert(reads-first_rx_read<=4 && commands==1);
  memory[reg(1)]=0; put16(0x26,1);
  assert(EthernetCliTransmit::read(0)=='Y' && get16(0x28)==1);
  reset(); changing_receive={1,2,1,2};
  const unsigned before_rx_reads=reads;
  assert(EthernetCliTransmit::available(0)==0 && reads-before_rx_reads==6);
  assert(commands==0); // unstable register values yield after four reads
  memory[reg(3)]=SnSR::CLOSE_WAIT; changing_receive={1,2,1,2};
  assert(EthernetCliTransmit::connected(0)); // uncertainty must not discard final bytes
  put16(0x26,1); assert(EthernetCliTransmit::connected(0));
  put16(0x26,0); assert(!EthernetCliTransmit::connected(0));
  memory[reg(3)]=SnSR::CLOSED;
  assert(EthernetCliTransmit::available(0)==0 && EthernetCliTransmit::read(0)==-1);
  assert(!EthernetCliTransmit::connected(0));
  assert(EthernetCliTransmit::read(4)==-1 && EthernetCliTransmit::available(4)==0);
  assert(!EthernetCliTransmit::commandReady(4) && !EthernetCliTransmit::connected(4));
  assert(!::spi.active && ::spi.begins==::spi.ends);
}
'''


class EthernetTransmitTest(unittest.TestCase):
    def compile_and_run(self, driver):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name, content in (("Arduino.h", ARDUINO), ("SPI.h", SPI),
                                  ("RAK13800_W5100S.h", RAK_HEADER), ("w5100.h", driver)):
                (path / name).write_text(content)
            (path / "test.cpp").write_text(RUNNER)
            exe = path / "test"
            subprocess.run([
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-I", str(path), "-I", str(ROOT), str(path / "test.cpp"), "-o", str(exe),
            ], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_nonblocking_send_ring_wrap_deadlines_and_faults(self):
        self.compile_and_run(PORTABLE_DRIVER)

    @unittest.skipUnless(DRIVER.is_file(), "RAK13800 board library is not installed in this native checkout")
    def test_actual_vendor_register_header_compatibility(self):
        self.compile_and_run(DRIVER.read_text())


if __name__ == "__main__":
    unittest.main()
